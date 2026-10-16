#!/usr/bin/env python3
"""Cache A1 geometry and §7.15 dependency evidence once per A5 scene."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from make_floorplans import source_dependency_audit
from pair_scenes import SCENES, scan


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def serializable_geometry(scene: dict) -> dict:
    return {
        "scene": scene["scene"],
        "floor_to_floor": scene["floor_to_floor"],
        "rooms": scene["rooms"],
        "walls": [list(rect) for rect in scene["walls"]],
        "obstacles": [list(rect) for rect in scene["obstacles"]],
        "wall_boxes": scene["wall_boxes"],
        "obstacle_boxes": scene["obstacle_boxes"],
        "doorway_clearance": scene["doorway_clearance"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--types", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit-scenes", type=int, default=0,
                        help="test-only prefix limit; zero caches every pair scene")
    args = parser.parse_args()
    pair_doc = json.loads(args.pairs.read_text(encoding="utf-8-sig"))
    type_doc = json.loads(args.types.read_text(encoding="utf-8-sig"))
    names = sorted({record[floor] for record in pair_doc["results"]
                    for floor in ("floor1", "floor2")})
    if args.limit_scenes:
        if args.limit_scenes < 1:
            raise SystemExit("--limit-scenes must be positive")
        names = names[:args.limit_scenes]
    scenes = {}
    for index, name in enumerate(names, 1):
        geometry = scan(name)
        if geometry is None:
            raise RuntimeError("could not scan A5 scene: " + name)
        dependencies = source_dependency_audit(name)
        if name not in type_doc.get("scenes", {}):
            raise ValueError("scene missing from authoritative type table: " + name)
        scenes[name] = {
            "geometry": serializable_geometry(geometry),
            "dependencies": dependencies,
            "type": type_doc["scenes"][name],
        }
        print("cached {}/{} {}".format(index, len(names), name))
    output = {
        "cache_contract_version": 2,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scene_root": str(SCENES.resolve()),
        "pair_cache_path": str(args.pairs),
        "pair_cache_sha256": digest(args.pairs),
        "type_cache_path": str(args.types),
        "type_cache_sha256": digest(args.types),
        "scene_count": len(scenes),
        "complete_for_pair_cache": not bool(args.limit_scenes),
        "scenes": scenes,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise FileExistsError("refusing to overwrite scene cache: " + str(args.out))
    args.out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({k: output[k] for k in (
        "cache_contract_version", "scene_count", "complete_for_pair_cache")}, indent=2))


if __name__ == "__main__":
    main()
