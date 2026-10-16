#!/usr/bin/env python3
"""Independently verify selected official scene packages and extractions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_index(rows: list[dict], key: str, label: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in rows:
        value = str(row[key])
        if value in result:
            raise RuntimeError(f"duplicate {label} {key}: {value}")
        result[value] = row
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--transfer-jsonl", type=Path, required=True)
    parser.add_argument("--extract-jsonl", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--extract-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite extraction audit: {args.out}")

    selected = load_json(args.selection)["selected"]
    selected_by_scene = unique_index(selected, "scene", "selection")
    transfer_by_scene = unique_index(load_jsonl(args.transfer_jsonl), "scene", "transfer")
    extract_by_scene = unique_index(load_jsonl(args.extract_jsonl), "scene", "extraction")
    names = set(selected_by_scene)
    errors: list[str] = []
    if set(transfer_by_scene) != names:
        errors.append("transfer scene set differs from selection")
    if set(extract_by_scene) != names:
        errors.append("extraction scene set differs from selection")

    package_bytes = 0
    package_hashes_checked = 0
    required_files_checked = 0
    for scene in sorted(names):
        selected_row = selected_by_scene[scene]
        transfer = transfer_by_scene.get(scene, {})
        extraction = extract_by_scene.get(scene, {})
        for label, row in (("transfer", transfer), ("extraction", extraction)):
            if row.get("status") != "PASS":
                errors.append(f"{scene}: {label} status is not PASS")
            if row.get("package") != selected_row["package"]:
                errors.append(f"{scene}: {label} package mismatch")
        package = args.package_root / selected_row["source"] / selected_row["package"]
        expected_size = int(selected_row["size"])
        if not package.is_file() or package.stat().st_size != expected_size:
            errors.append(f"{scene}: package missing or size mismatch")
        else:
            package_bytes += package.stat().st_size
            digest = sha256(package)
            package_hashes_checked += 1
            if transfer.get("sha256") != digest or extraction.get("sha256") != digest:
                errors.append(f"{scene}: package SHA256 mismatch")
        for variant in (scene, scene.removesuffix("_ceiling")):
            root = args.extract_root / variant
            required = (
                root / "scene.usda",
                root / "scene_metadata.json",
                root / "Payload" / "Geometry.usda",
                root / "Payload" / "GeometryLibrary.usdc",
            )
            for path in required:
                required_files_checked += 1
                if not path.is_file() or path.stat().st_size <= 0:
                    errors.append(f"{scene}: missing/nonpositive required file {path}")

    report = {
        "schema": "Factory10FullSceneExtractionAuditV1",
        "status": "PASS" if not errors else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection": str(args.selection.resolve()),
        "selection_sha256": sha256(args.selection),
        "transfer_jsonl": str(args.transfer_jsonl.resolve()),
        "transfer_jsonl_sha256": sha256(args.transfer_jsonl),
        "extract_jsonl": str(args.extract_jsonl.resolve()),
        "extract_jsonl_sha256": sha256(args.extract_jsonl),
        "selected_scene_count": len(names),
        "transfer_scene_count": len(transfer_by_scene),
        "extraction_scene_count": len(extract_by_scene),
        "package_hashes_checked": package_hashes_checked,
        "package_bytes_checked": package_bytes,
        "required_files_checked": required_files_checked,
        "errors": errors,
        "dependency_scope_note": (
            "This audit proves package and extraction integrity only; USD dependency "
            "closure is separately hard-gated by source_dependency_audit."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "status", "selected_scene_count", "package_hashes_checked",
        "package_bytes_checked", "required_files_checked", "errors",
    )}, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
