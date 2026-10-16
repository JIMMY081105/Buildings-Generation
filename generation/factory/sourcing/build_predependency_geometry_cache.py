#!/usr/bin/env python3
"""Cache only source-local geometry for the pre-dependency factory prefilter.

The output is deliberately not a release/A4 geometry-cache contract.  Missing
external objects can affect obstacle geometry, so those fields are retained for
diagnostics but prohibited from A3/A4 use.  The strict prefilter consumes only
floor-to-floor and room rectangles, which are authored in the scene package.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from pair_scenes import SCENES, scan


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--types", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite cache: {args.out}")
    pair_doc = json.loads(args.pairs.read_text(encoding="utf-8-sig"))
    type_doc = json.loads(args.types.read_text(encoding="utf-8-sig"))
    names = sorted({
        row[floor] for row in pair_doc["results"] for floor in ("floor1", "floor2")
    })
    if set(names) != set(type_doc["scenes"]):
        raise RuntimeError("pair-carrier and type scene sets differ")
    scenes = {}
    for index, name in enumerate(names, 1):
        geometry = scan(name)
        if geometry is None:
            raise RuntimeError(f"could not scan source-local geometry: {name}")
        stage = SCENES / name / "scene.usda"
        scenes[name] = {
            "geometry": {
                "scene": geometry["scene"],
                "floor_to_floor": geometry["floor_to_floor"],
                "rooms": geometry["rooms"],
                "walls": [list(rect) for rect in geometry["walls"]],
                "obstacles": [list(rect) for rect in geometry["obstacles"]],
                "wall_boxes": geometry["wall_boxes"],
                "obstacle_boxes": geometry["obstacle_boxes"],
            },
            "dependencies": {
                "dependency_gate_pass": False,
                "status": "DEFERRED_NOT_EVALUATED_PREFILTER_ONLY",
            },
            "type": type_doc["scenes"][name],
            "source_stage": str(stage.resolve()),
            "source_stage_sha256": sha256(stage),
        }
        print(f"cached {index}/{len(names)} {name}", flush=True)
    output = {
        "schema": "Factory10PredependencyGeometryCacheV1",
        "cache_contract_version": "PREFILTER_ONLY_NOT_A1_A4",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scene_root": str(SCENES.resolve()),
        "pair_cache_path": str(args.pairs.resolve()),
        "pair_cache_sha256": sha256(args.pairs),
        "type_cache_path": str(args.types.resolve()),
        "type_cache_sha256": sha256(args.types),
        "scene_count": len(scenes),
        "complete_for_pair_cache": False,
        "external_dependencies_evaluated": False,
        "allowed_metrics": ["floor_to_floor", "rooms"],
        "prohibited_uses": ["A3", "doorway_gate", "A4", "build", "release"],
        "scenes": scenes,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "scene_count": len(scenes)}, indent=2))


if __name__ == "__main__":
    main()
