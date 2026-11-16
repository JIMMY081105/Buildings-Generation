#!/usr/bin/env python3
"""Create a single immutable scene root for complete geometry validation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["selected"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-selection", type=Path, required=True)
    parser.add_argument("--new-selection", type=Path, required=True)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--new-root", type=Path, required=True)
    parser.add_argument("--old-source-class", default="complete_official_package")
    parser.add_argument("--new-source-class", default="exact_scene_layers_without_textures")
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.out_root.exists() or args.report.exists():
        raise FileExistsError("refusing to overwrite geometry-validation scene union")
    old_rows, new_rows = load_rows(args.old_selection), load_rows(args.new_selection)
    old_names = {row["scene"] for row in old_rows}
    new_names = {row["scene"] for row in new_rows}
    if old_names.intersection(new_names):
        raise RuntimeError("old/new union selections must be scene-disjoint")
    args.out_root.mkdir(parents=True)
    links = []
    for source_class, root, rows in (
        (args.old_source_class, args.old_root.resolve(), old_rows),
        (args.new_source_class, args.new_root.resolve(), new_rows),
    ):
        for row in rows:
            for variant in (row["scene"], row["scene"].removesuffix("_ceiling")):
                source = root / variant
                required = [
                    source / "scene.usda", source / "scene_metadata.json",
                    source / "Payload" / "Geometry.usda",
                    source / "Payload" / "GeometryLibrary.usdc",
                ]
                if not all(path.is_file() and path.stat().st_size > 0 for path in required):
                    raise FileNotFoundError(f"source scene variant incomplete: {source}")
                target = args.out_root / variant
                if target.exists() or target.is_symlink():
                    raise RuntimeError(f"duplicate union target: {target}")
                target.symlink_to(source, target_is_directory=True)
                if not (target / "scene.usda").is_file():
                    raise RuntimeError(f"union scene link unresolved: {target}")
                links.append({
                    "scene": row["scene"], "variant": variant,
                    "source_class": source_class,
                    "source": str(source), "target": str(target),
                    "scene_usda_sha256": sha256(source / "scene.usda"),
                })
    output = {
        "schema": "Factory10GeometryValidationSceneUnionV1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "role": "complete_geometry_dependency_doorway_A3_A4_validation_only",
        "final_build_requires_complete_official_packages": True,
        "old_selection": str(args.old_selection.resolve()),
        "old_selection_sha256": sha256(args.old_selection),
        "new_selection": str(args.new_selection.resolve()),
        "new_selection_sha256": sha256(args.new_selection),
        "selected_scene_count": len(old_rows) + len(new_rows),
        "linked_variant_count": len(links),
        "out_root": str(args.out_root.resolve()),
        "links": links,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "selected_scene_count": output["selected_scene_count"],
        "linked_variant_count": len(links),
    }, indent=2))


if __name__ == "__main__":
    main()
