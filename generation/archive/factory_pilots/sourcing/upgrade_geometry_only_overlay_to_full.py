#!/usr/bin/env python3
"""Atomically replace task geometry-only object links with full official objects."""

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


def safe_members(package: Path) -> list[str]:
    result = subprocess.run(
        ["tar", "--use-compress-program=unzstd", "-tf", str(package)],
        check=True, capture_output=True, text=True,
    )
    members = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not members:
        raise RuntimeError(f"empty package: {package}")
    for member in members:
        path = PurePosixPath(member)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"unsafe package member: {member}")
    return members


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-manifest", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--full-object-root", type=Path, required=True)
    parser.add_argument("--overlay-root", type=Path, required=True)
    parser.add_argument("--geometry-overlay-report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.full_object_root.exists() or args.out.exists():
        raise FileExistsError("refusing to overwrite full-object upgrade outputs")
    manifest = json.loads(args.bundle_manifest.read_text(encoding="utf-8"))
    geometry_report = json.loads(args.geometry_overlay_report.read_text(encoding="utf-8"))
    if manifest.get("status") != "PASS" or geometry_report.get("status") != "PASS":
        raise ValueError("bundle or geometry overlay evidence is not PASS")
    linked = set(geometry_report["linked_geometry_only"])
    expected = {row["directory"] for row in manifest["packages"]}
    if linked != expected:
        raise RuntimeError("full bundle set differs from geometry-only overlay link set")
    geometry_root = Path(geometry_report["geometry_root"]).resolve()
    overlay_objaverse = args.overlay_root.resolve() / "objaverse"
    args.full_object_root.mkdir(parents=True)
    rows = []
    for index, record in enumerate(manifest["packages"], 1):
        directory = record["directory"]
        package = args.package_root / record["relative_path"]
        actual = (package.stat().st_size, sha256(package))
        wanted = (int(record["size"]), str(record["sha256"]).lower())
        if actual != wanted:
            raise RuntimeError(f"transferred package mismatch: {package}")
        members = safe_members(package)
        top_levels = {PurePosixPath(member).parts[0] for member in members}
        if top_levels != {directory}:
            raise RuntimeError(f"package top-level mismatch for {directory}: {top_levels}")
        with tempfile.TemporaryDirectory(
            prefix="full_object_extract_", dir=args.full_object_root
        ) as temporary:
            temp = Path(temporary)
            subprocess.run([
                "tar", "--use-compress-program=unzstd", "-xf", str(package),
                "-C", str(temp),
            ], check=True)
            staged = temp / directory
            required = staged / f"{directory}.usda"
            if not required.is_file() or required.stat().st_size == 0:
                raise RuntimeError(f"full package USDA absent: {required}")
            full_target = args.full_object_root / directory
            staged.rename(full_target)
        overlay_link = overlay_objaverse / directory
        if not overlay_link.is_symlink():
            raise RuntimeError(f"expected geometry-only overlay symlink: {overlay_link}")
        old_resolved = overlay_link.resolve()
        if old_resolved != geometry_root / directory:
            raise RuntimeError(f"geometry-only overlay target changed: {overlay_link}")
        incoming = overlay_link.with_name(overlay_link.name + ".full_upgrade_incoming")
        if incoming.exists() or incoming.is_symlink():
            raise RuntimeError(f"stale incoming full-object link: {incoming}")
        incoming.symlink_to(full_target.resolve(), target_is_directory=True)
        if not (incoming / f"{directory}.usda").is_file():
            raise RuntimeError(f"incoming full-object link unresolved: {incoming}")
        os.replace(incoming, overlay_link)
        if overlay_link.resolve() != full_target.resolve():
            raise RuntimeError(f"atomic full-object link promotion failed: {overlay_link}")
        rows.append({
            "directory": directory,
            "package": str(package.resolve()),
            "package_size": actual[0],
            "package_sha256": actual[1],
            "member_count": len(members),
            "old_geometry_only_target": str(old_resolved),
            "new_full_target": str(full_target.resolve()),
            "overlay_link": str(overlay_link),
        })
        if index % 100 == 0 or index == len(expected):
            print(f"upgraded {index}/{len(expected)}", flush=True)
    output = {
        "schema": "Factory10FullOfficialObjectOverlayUpgradeV1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "bundle_manifest": str(args.bundle_manifest.resolve()),
        "bundle_manifest_sha256": sha256(args.bundle_manifest),
        "geometry_overlay_report": str(args.geometry_overlay_report.resolve()),
        "geometry_overlay_report_sha256": sha256(args.geometry_overlay_report),
        "object_count": len(rows),
        "all_geometry_only_links_replaced_atomically": len(rows) == len(expected),
        "full_object_root": str(args.full_object_root.resolve()),
        "overlay_root": str(args.overlay_root.resolve()),
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "object_count": len(rows),
        "all_geometry_only_links_replaced_atomically": True,
    }, indent=2))


if __name__ == "__main__":
    main()
