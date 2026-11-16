#!/usr/bin/env python3
"""Bind strict factory types and canonical geometry to the A5 compiler inputs."""

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


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_json(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite compiler binding: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--broad-types", type=Path, required=True)
    parser.add_argument("--strict-review", type=Path, required=True)
    parser.add_argument("--canonical-cache", type=Path, required=True)
    parser.add_argument("--doorway-cache", type=Path, required=True)
    parser.add_argument("--out-types", type=Path, required=True)
    parser.add_argument("--out-cache", type=Path, required=True)
    args = parser.parse_args()
    pairs, broad = load(args.pairs), load(args.broad_types)
    review, canonical, doorway = (load(args.strict_review), load(args.canonical_cache),
                                  load(args.doorway_cache))
    if review.get("status") != "PASS":
        raise ValueError("strict source review is not PASS")
    accepted = set(review["accepted_factory_floors"])
    pair_names = {str(row[key]) for row in pairs["results"]
                  if row.get("usable") is True for key in ("floor1", "floor2")}
    if not pair_names.issubset(accepted):
        raise ValueError("strict pair cache contains a source rejected by strict review")
    if any(canonical["scenes"][name]["dependencies"].get("dependency_gate_pass") is not True
           for name in pair_names):
        raise ValueError("a strict factory scene fails canonical dependencies")
    door_names = set(doorway["scenes"])
    if not door_names.issubset(pair_names):
        raise ValueError("doorway cache contains a non-strict scene")
    searched_height_names = {
        str(row[key]) for row in pairs["results"]
        if row.get("usable") is True
        and round(float(row["floor1_f2f"]), 2) in {3.20, 3.50, 3.60}
        for key in ("floor1", "floor2")
    }
    if door_names != searched_height_names:
        raise ValueError("doorway cache does not exactly cover genuine-stair search cells")

    strict_types = {
        "contract": "factory10_strict_scene_types_compiler_binding_v1",
        "scene_root": pairs["scene_root"],
        "semantic_policy": "strict factory review; office and school classified separately",
        "strict_source_review": str(args.strict_review.resolve()),
        "strict_source_review_sha256": sha256(args.strict_review),
        "counts": {"factory": len(pair_names)},
        "strict_review_accepted_source_count": len(accepted),
        "strict_pair_participant_count": len(pair_names),
        "scenes": {},
    }
    for name in sorted(pair_names):
        record = dict(broad["scenes"][name])
        if record.get("type") != "factory" or record.get("factory_core_room_pass") is not True:
            raise ValueError(f"broad type record is not factory-safe: {name}")
        record["strict_factory_source_review_pass"] = True
        record["strict_source_review_sha256"] = sha256(args.strict_review)
        strict_types["scenes"][name] = record
    atomic_json(args.out_types, strict_types)

    scenes = {}
    for name in sorted(pair_names):
        record = canonical["scenes"][name]
        if name in doorway["scenes"]:
            door_record = doorway["scenes"][name]
            if door_record["dependencies"] != record["dependencies"]:
                raise ValueError(f"doorway/canonical dependency mismatch: {name}")
            geometry = dict(record["geometry"])
            geometry["doorway_clearance"] = door_record["geometry"]["doorway_clearance"]
            record = {**record, "geometry": geometry}
        scenes[name] = record
    bound_cache = {
        "cache_contract_version": 3,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scene_root": pairs["scene_root"],
        "pair_cache_path": str(args.pairs.resolve()),
        "pair_cache_sha256": sha256(args.pairs),
        "type_cache_path": str(args.out_types.resolve()),
        "type_cache_sha256": sha256(args.out_types),
        "scene_count": len(scenes),
        "complete_for_pair_cache": True,
        "canonical_geometry_dependency_cache": str(args.canonical_cache.resolve()),
        "canonical_geometry_dependency_cache_sha256": sha256(args.canonical_cache),
        "doorway_cache": str(args.doorway_cache.resolve()),
        "doorway_cache_sha256": sha256(args.doorway_cache),
        "strict_source_review": str(args.strict_review.resolve()),
        "strict_source_review_sha256": sha256(args.strict_review),
        "doorway_clearance_contract_version": 2,
        "doorway_scene_count": len(door_names),
        "doorway_complete_for_genuine_stair_search_cells": True,
        "unsupported_height_scenes_may_lack_doorway_evidence": True,
        "scenes": scenes,
    }
    atomic_json(args.out_cache, bound_cache)
    print(json.dumps({
        "status": "PASS", "strict_review_accepted_source_count": len(accepted),
        "strict_pair_participant_count": len(pair_names),
        "doorway_scene_count": len(door_names),
        "all_dependencies_pass": True,
        "pair_sha256": bound_cache["pair_cache_sha256"],
        "type_sha256": bound_cache["type_cache_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
