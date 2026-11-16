#!/usr/bin/env python3
"""Apply dependency-independent necessary A3 conditions before full transfer.

This is a transfer optimization only.  It uses the same footprint IoU and
upper-overhang thresholds as A3, plus the exact available prefab heights.  All
survivors must still pass the ordinary complete-dependency A3/A4 pipeline.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from itertools import permutations
import json
from pathlib import Path

from shapely.geometry import box
from shapely.ops import unary_union


MIN_IOU = 0.60
MAX_FLOOR2_OVERHANG = 0.12
HEIGHT_TOLERANCE_M = 0.001


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def room_union(record: dict):
    return unary_union([box(*room["rect"]) for room in record["geometry"]["rooms"]])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-cache", type=Path, required=True)
    parser.add_argument("--old-strict-review", type=Path, required=True)
    parser.add_argument("--new-cache", type=Path, required=True)
    parser.add_argument("--new-prefilter", type=Path, required=True)
    parser.add_argument("--stair-audit", type=Path, required=True)
    parser.add_argument("--old-selection", type=Path, required=True)
    parser.add_argument("--new-selection", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--selection-out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists() or args.selection_out.exists():
        raise FileExistsError("refusing to overwrite A3 prefilter evidence")
    old_cache = json.loads(args.old_cache.read_text(encoding="utf-8"))
    old_review = json.loads(args.old_strict_review.read_text(encoding="utf-8"))
    new_cache = json.loads(args.new_cache.read_text(encoding="utf-8"))
    new_prefilter = json.loads(args.new_prefilter.read_text(encoding="utf-8"))
    stair_audit = json.loads(args.stair_audit.read_text(encoding="utf-8"))
    old_selection = json.loads(args.old_selection.read_text(encoding="utf-8"))
    selection = json.loads(args.new_selection.read_text(encoding="utf-8"))
    if stair_audit.get("status") != "PASS":
        raise ValueError("stair audit is not PASS")
    heights = sorted({int(row["height_mm"]) for row in stair_audit["rows"]})
    old_names = set(old_review["accepted_factory_floors"])
    new_names = set(new_prefilter["potential_scenes"])
    overlap = sorted(old_names.intersection(new_names))
    old_packages = {row["scene"]: row for row in old_selection["selected"]}
    new_packages = {row["scene"]: row for row in selection["selected"]}
    locator_fields = ("source", "version", "shard_id", "offset", "size", "package")
    for name in overlap:
        if name not in old_packages or name not in new_packages:
            raise RuntimeError(f"overlap lacks package evidence: {name}")
        if any(old_packages[name].get(key) != new_packages[name].get(key)
               for key in locator_fields):
            raise RuntimeError(f"overlap points to conflicting official package: {name}")
    if not old_names.issubset(old_cache["scenes"]):
        raise RuntimeError("old accepted scene is absent from old cache")
    if not new_names.issubset(new_cache["scenes"]):
        raise RuntimeError("new potential scene is absent from prefilter cache")
    records = {
        **{name: new_cache["scenes"][name] for name in new_names},
        **{name: old_cache["scenes"][name] for name in old_names},
    }
    unions = {name: room_union(record) for name, record in records.items()}
    viable_pairs = []
    for lower, upper in permutations(sorted(records), 2):
        f2f = float(records[lower]["geometry"]["floor_to_floor"])
        matched_heights = [height for height in heights
                           if abs(f2f - height / 100.0) <= HEIGHT_TOLERANCE_M]
        if not matched_heights:
            continue
        lo, up = unions[lower], unions[upper]
        union_area = float(lo.union(up).area)
        iou = float(lo.intersection(up).area) / union_area if union_area else 0.0
        if iou < MIN_IOU:
            continue
        overhang_area = float(up.difference(lo).area)
        overhang_ratio = overhang_area / float(up.area) if up.area else 1.0
        if overhang_ratio > MAX_FLOOR2_OVERHANG:
            continue
        viable_pairs.append({
            "floor1": lower,
            "floor2": upper,
            "floor1_height_m": f2f,
            "matched_genuine_stair_heights_mm": matched_heights,
            "footprint_iou": round(iou, 8),
            "floor2_overhang_area_m2": round(overhang_area, 8),
            "floor2_overhang_ratio": round(overhang_ratio, 8),
        })
    participants = {name for row in viable_pairs for name in (row["floor1"], row["floor2"])}
    new_needed = sorted(new_names.difference(old_names).intersection(participants))
    selection_rows = [row for row in selection["selected"] if row["scene"] in new_needed]
    if {row["scene"] for row in selection_rows} != set(new_needed):
        raise RuntimeError("necessary-pair scenes do not bind exactly to package selection")
    payload = {
        "schema": "Factory10A3NecessaryConditionTransferPrefilterV1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "role": "transfer_optimization_only_not_A3_acceptance",
        "criteria_unchanged": True,
        "full_A3_and_A4_still_required": True,
        "rules": {
            "minimum_footprint_iou": MIN_IOU,
            "maximum_floor2_overhang_ratio": MAX_FLOOR2_OVERHANG,
            "height_tolerance_m": HEIGHT_TOLERANCE_M,
            "available_genuine_stair_heights_mm": heights,
        },
        "old_strict_scene_count": len(old_names),
        "new_strict_non_dependency_potential_count": len(new_names),
        "round_overlap_count": len(overlap),
        "round_overlap_reused_from_complete_round1": overlap,
        "round_overlap_official_locator_match": True,
        "combined_scene_count": len(records),
        "viable_directed_pair_count": len(viable_pairs),
        "participant_count": len(participants),
        "new_full_package_required_count": len(new_needed),
        "new_full_package_required_scenes": new_needed,
        "viable_pairs": viable_pairs,
        "inputs": {
            str(path.resolve()): sha256(path) for path in (
                args.old_cache, args.old_strict_review, args.new_cache,
                args.new_prefilter, args.stair_audit, args.old_selection,
                args.new_selection,
            )
        },
    }
    selection_output = {
        **{key: value for key, value in selection.items()
           if key not in ("selected", "selected_count", "selected_total_bytes")},
        "schema": "Factory10A3NecessaryFullPackageSelectionV1",
        "generated_at_utc": payload["generated_at_utc"],
        "a3_necessary_prefilter": str(args.out.resolve()),
        "selected_count": len(selection_rows),
        "selected_total_bytes": sum(int(row["size"]) for row in selection_rows),
        "selected": selection_rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    args.selection_out.write_text(json.dumps(selection_output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "viable_directed_pair_count": len(viable_pairs),
        "new_full_package_required_count": len(new_needed),
        "selected_total_bytes": selection_output["selected_total_bytes"],
    }, indent=2))


if __name__ == "__main__":
    main()
