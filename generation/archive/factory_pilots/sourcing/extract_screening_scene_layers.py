#!/usr/bin/env python3
"""Extract exact scene USD layers while omitting texture payloads.

This artifact is only for the pre-dependency semantic/structural review.  A
scene that survives that review must still be extracted from its complete,
hash-verified official package before dependency, doorway, A4, or build gates.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


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

    if args.out_root.exists():
        raise FileExistsError(f"refusing to reuse screening root: {args.out_root}")
    if args.manifest.exists():
        raise FileExistsError(f"refusing to overwrite manifest: {args.manifest}")
    selected = json.loads(args.selection.read_text(encoding="utf-8"))["selected"]
    records = {}
    for line in args.download_log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if record.get("status") == "PASS":
                records[record["package"]] = record
    if len(selected) != len({row["scene"] for row in selected}):
        raise RuntimeError("selection has duplicate scenes")

    args.out_root.mkdir(parents=True)
    packages = []
    for index, row in enumerate(selected, 1):
        package = args.package_root / row["source"] / row["package"]
        record = records.get(row["package"])
        if record is None:
            raise RuntimeError(f"package lacks PASS download record: {row['package']}")
        expected = (int(record["size"]), str(record["sha256"]).lower())
        actual = (package.stat().st_size, sha256(package))
        if actual != expected:
            raise RuntimeError(
                f"official package verification failed: {package}: {actual} != {expected}"
            )
        subprocess.run(
            [
                "tar", "-xf", str(package), "-C", str(args.out_root),
                "--exclude=*/Payload/Textures/*",
            ],
            check=True,
        )
        selected_dir = args.out_root / row["scene"]
        base_dir = args.out_root / row["scene"].removesuffix("_ceiling")
        required = [
            selected_dir / "scene.usda", selected_dir / "scene_metadata.json",
            selected_dir / "Payload" / "Geometry.usda",
            selected_dir / "Payload" / "GeometryLibrary.usdc",
            base_dir / "scene.usda", base_dir / "scene_metadata.json",
            base_dir / "Payload" / "Geometry.usda",
            base_dir / "Payload" / "GeometryLibrary.usdc",
        ]
        if not all(path.is_file() and path.stat().st_size > 0 for path in required):
            raise RuntimeError(f"screening layer extraction incomplete: {row['scene']}")
        packages.append({
            "scene": row["scene"], "source": row["source"],
            "official_package": row["package"],
            "official_package_size": actual[0],
            "official_package_sha256": actual[1],
        })
        print(f"extracted {index}/{len(selected)} {row['scene']}", flush=True)

    files = sorted(path for path in args.out_root.rglob("*") if path.is_file())
    texture_files = [path for path in files if "Textures" in path.parts]
    if texture_files:
        raise RuntimeError(f"texture exclusion failed: {texture_files[:5]}")
    file_rows = [
        {
            "path": path.relative_to(args.out_root).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in files
    ]
    tree_digest = hashlib.sha256()
    for row in file_rows:
        tree_digest.update(
            f"{row['path']}\0{row['size']}\0{row['sha256']}\n".encode("utf-8")
        )
    output = {
        "schema": "Factory10ExactSceneLayersWithoutTexturesV1",
        "status": "PASS",
        "role": "pre_dependency_semantic_and_structural_screen_only",
        "prohibited_uses": ["doorway_gate", "A4", "building_generation", "release"],
        "full_official_package_required_after_prefilter": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scene_count": len(packages),
        "official_package_count": len(packages),
        "official_package_total_bytes": sum(row["official_package_size"] for row in packages),
        "extracted_file_count": len(file_rows),
        "extracted_total_bytes": sum(row["size"] for row in file_rows),
        "textures_omitted": True,
        "tree_sha256": tree_digest.hexdigest(),
        "packages": packages,
        "files": file_rows,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "scene_count": len(packages),
        "file_count": len(file_rows),
        "bytes": output["extracted_total_bytes"],
        "tree_sha256": output["tree_sha256"],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
