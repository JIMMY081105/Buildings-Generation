#!/usr/bin/env python3
"""Build a task-scoped object overlay from existing and exact downloaded USD assets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_members(package: Path) -> list[str]:
    result = subprocess.run(
        ["tar", "--use-compress-program=unzstd", "-tf", str(package)],
        check=True, capture_output=True, text=True,
    )
    members = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not members:
        raise RuntimeError(f"empty object package: {package}")
    for member in members:
        path = PurePosixPath(member)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"unsafe member in {package}: {member}")
    return members


def load_download_records(path: Path) -> dict[str, dict]:
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("status") == "PASS":
                records[row["directory"]] = row
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--download-jsonl", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--existing-root", type=Path, required=True)
    parser.add_argument("--overlay-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    dependencies = json.loads(args.dependencies.read_text(encoding="utf-8"))["dependencies"]
    downloads = load_download_records(args.download_jsonl)
    existing = args.existing_root.expanduser().resolve()
    overlay = args.overlay_root.expanduser().absolute()
    if not (existing / "objaverse").is_dir() or not (existing / "thor").is_dir():
        raise RuntimeError(f"existing official object root is incomplete: {existing}")

    if overlay.is_symlink():
        if overlay.resolve() != existing:
            raise RuntimeError(f"unexpected pre-existing overlay symlink: {overlay}")
        overlay.unlink()
    overlay.mkdir(parents=True, exist_ok=True)
    objaverse_root = overlay / "objaverse"
    objaverse_root.mkdir(exist_ok=True)
    thor_link = overlay / "thor"
    if not thor_link.exists() and not thor_link.is_symlink():
        thor_link.symlink_to(existing / "thor", target_is_directory=True)
    if not thor_link.is_symlink() or thor_link.resolve() != (existing / "thor").resolve():
        raise RuntimeError("task THOR overlay does not resolve to the existing official root")

    linked = 0
    extracted = 0
    reused = 0
    package_rows = []
    for index, dependency in enumerate(dependencies, 1):
        source = dependency["source"]
        directory = dependency["directory"]
        expected_name = f"{directory}.usda"
        if source == "thor":
            expected = thor_link / directory / expected_name
            if not expected.is_file():
                raise FileNotFoundError(f"existing THOR dependency disappeared: {expected}")
            continue
        if source != "objaverse":
            raise RuntimeError(f"unsupported object source: {source}")
        target = objaverse_root / directory
        expected = target / expected_name
        if expected.is_file():
            reused += 1
            continue
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"incomplete overlay target requires audit: {target}")
        installed = existing / "objaverse" / directory
        installed_usda = installed / expected_name
        if installed_usda.is_file():
            target.symlink_to(installed, target_is_directory=True)
            if not expected.is_file():
                raise RuntimeError(f"new overlay link is unresolved: {target}")
            linked += 1
            continue

        record = downloads.get(directory)
        if record is None:
            raise FileNotFoundError(f"no verified download record for {directory}")
        package = args.package_root / record["bucket"] / record["package"]
        if not package.is_file() or package.stat().st_size != int(record["size"]):
            raise RuntimeError(f"downloaded object package absent or wrong size: {package}")
        package_hash = sha256(package)
        if package_hash != record["sha256"]:
            raise RuntimeError(f"downloaded object package hash differs: {package}")
        members = package_members(package)
        top_levels = {PurePosixPath(member).parts[0] for member in members}
        if top_levels != {directory}:
            raise RuntimeError(f"object package top-level mismatch for {directory}: {top_levels}")
        with tempfile.TemporaryDirectory(prefix="object_extract_", dir=objaverse_root) as temp_name:
            temp = Path(temp_name)
            subprocess.run([
                "tar", "--use-compress-program=unzstd", "-xf", str(package), "-C", str(temp)
            ], check=True)
            staged = temp / directory
            staged_usda = staged / expected_name
            if not staged_usda.is_file() or staged_usda.stat().st_size == 0:
                raise RuntimeError(f"extracted USD is absent: {staged_usda}")
            staged.rename(target)
        extracted += 1
        package_rows.append({
            "directory": directory, "package": str(package),
            "package_sha256": package_hash, "member_count": len(members),
        })
        if index % 100 == 0:
            print(json.dumps({"done": index, "total": len(dependencies),
                              "linked": linked, "extracted": extracted, "reused": reused}),
                  flush=True)

    missing = []
    for dependency in dependencies:
        expected = overlay / dependency["source"] / dependency["directory"] / (
            dependency["directory"] + ".usda"
        )
        if not expected.is_file() or expected.stat().st_size == 0:
            missing.append(str(expected))
    payload = {
        "schema": "Factory10OfficialObjectOverlayV1",
        "status": "PASS" if not missing else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dependencies": str(args.dependencies.resolve()),
        "dependencies_sha256": sha256(args.dependencies),
        "download_jsonl": str(args.download_jsonl.resolve()),
        "download_jsonl_sha256": sha256(args.download_jsonl),
        "existing_root": str(existing),
        "overlay_root": str(overlay),
        "dependency_count": len(dependencies),
        "linked_from_existing_count": linked,
        "downloaded_extracted_count": extracted,
        "reused_overlay_count": reused,
        "missing_count": len(missing),
        "missing": missing,
        "downloaded_package_evidence": package_rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite overlay report: {args.out}")
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "status", "dependency_count", "linked_from_existing_count",
        "downloaded_extracted_count", "reused_overlay_count", "missing_count")}, indent=2))
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
