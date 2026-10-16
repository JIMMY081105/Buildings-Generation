#!/usr/bin/env python3
"""Extract exact object USD geometry/physics layers while omitting textures."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


ASSET_REF = re.compile(rb"@([^@]+)@")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--download-log", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.out_root.exists() or args.manifest.exists():
        raise FileExistsError("refusing to overwrite geometry-only object evidence")
    selection = json.loads(args.selection.read_text(encoding="utf-8"))["selected"]
    latest = {}
    for line in args.download_log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("status") == "PASS":
                latest[row["directory"]] = row
    args.out_root.mkdir(parents=True)
    package_rows = []
    omitted_references = []
    unexpected_missing_references = []
    for index, selected in enumerate(selection, 1):
        directory = selected["directory"]
        record = latest.get(directory)
        if record is None:
            raise RuntimeError(f"object lacks PASS download record: {directory}")
        package = args.package_root / record["bucket"] / record["package"]
        actual = (package.stat().st_size, sha256(package))
        expected = (int(record["size"]), str(record["sha256"]).lower())
        if actual != expected:
            raise RuntimeError(f"official object package changed: {package}")
        subprocess.run([
            "tar", "-xf", str(package), "-C", str(args.out_root),
            "--exclude=*/Textures/*",
        ], check=True)
        root = args.out_root / directory
        primary = root / f"{directory}.usda"
        if not primary.is_file() or primary.stat().st_size == 0:
            raise RuntimeError(f"object USDA missing after extraction: {primary}")
        for layer in root.rglob("*.usd*"):
            for raw in ASSET_REF.findall(layer.read_bytes()):
                reference = raw.decode("utf-8", errors="replace")
                if reference.startswith("anon:"):
                    continue
                resolved = layer.parent / Path(reference.replace("/", "\\"))
                if resolved.is_file():
                    continue
                item = {
                    "directory": directory,
                    "layer": layer.relative_to(args.out_root).as_posix(),
                    "reference": reference,
                }
                if "Textures/" in reference or "Textures\\" in reference:
                    omitted_references.append(item)
                else:
                    unexpected_missing_references.append(item)
        package_rows.append({
            "directory": directory,
            "official_package": record["package"],
            "official_package_size": actual[0],
            "official_package_sha256": actual[1],
        })
        if index % 100 == 0 or index == len(selection):
            print(f"extracted {index}/{len(selection)}", flush=True)
    if unexpected_missing_references:
        raise RuntimeError(
            f"non-texture asset references are missing: {unexpected_missing_references[:5]}"
        )
    files = sorted(path for path in args.out_root.rglob("*") if path.is_file())
    textures = [path for path in files if "Textures" in path.parts]
    if textures:
        raise RuntimeError(f"texture exclusion failed: {textures[:5]}")
    file_rows = [{
        "path": path.relative_to(args.out_root).as_posix(),
        "size": path.stat().st_size,
        "sha256": sha256(path),
    } for path in files]
    tree_digest = hashlib.sha256()
    for row in file_rows:
        tree_digest.update(
            f"{row['path']}\0{row['size']}\0{row['sha256']}\n".encode("utf-8")
        )
    output = {
        "schema": "Factory10ExactObjectGeometryWithoutTexturesV1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "role": "dependency_doorway_and_A4_geometry_validation_only",
        "allowed_uses": ["source_dependency_audit", "geometry_cache", "doorway_gate", "A3", "A4"],
        "prohibited_uses": ["visual_release", "final_build", "final_USDZ_packaging"],
        "complete_official_packages_required_for_final_selected_scene_dependencies": True,
        "official_package_count": len(package_rows),
        "official_package_total_bytes": sum(row["official_package_size"] for row in package_rows),
        "extracted_file_count": len(file_rows),
        "extracted_total_bytes": sum(row["size"] for row in file_rows),
        "textures_omitted": True,
        "omitted_texture_reference_count": len(omitted_references),
        "unexpected_missing_reference_count": 0,
        "tree_sha256": tree_digest.hexdigest(),
        "packages": package_rows,
        "omitted_texture_references": omitted_references,
        "files": file_rows,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "package_count": len(package_rows),
        "file_count": len(file_rows), "bytes": output["extracted_total_bytes"],
        "omitted_texture_reference_count": len(omitted_references),
        "tree_sha256": output["tree_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
