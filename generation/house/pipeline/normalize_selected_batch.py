#!/usr/bin/env python3
"""Normalize selected buildings while preserving the non-blocking door policy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pxr import Usd

from door_collision_policy import inventory
from doorway_clearance import scan_stage
from normalize_building_physics import repair


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-c-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    stage_summary = load(args.stage_c_root / "stage_c_summary.json")
    rows = []
    for index, source_row in enumerate(stage_summary["rows"], 1):
        bid = source_row["BID"]
        building = args.stage_c_root / bid
        stage_path = building / "scene" / "building.usda"
        report_path = building / "reports" / "physics_normalization.json"
        report = repair(stage_path, report_path)
        stage = Usd.Stage.Open(str(stage_path), Usd.Stage.LoadAll)
        doors = inventory(stage)
        doorway = scan_stage(stage)
        checks = {
            "normalization_pass": report["status"] == "PASS",
            "swinging_door_collision_disabled": (
                doors["swinging_assembly_collider_count"] > 0
                and doors["swinging_assembly_collision_enabled_count"] == 0),
            "frames_and_jambs_collidable": (
                doors["frame_or_jamb_collider_count"] > 0
                and doors["frame_or_jamb_collision_enabled_count"]
                == doors["frame_or_jamb_collider_count"]),
            "A4_v2_doorway_clearance": doorway["pass"] is True,
        }
        row = {
            "BID": bid,
            "status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks,
            "before_after_enabled_colliders_per_floor": report[
                "before_after_enabled_colliders_per_floor"],
            "stagewide_enabled_mesh_collider_count": report[
                "stagewide_enabled_mesh_collider_count"],
            "door_inventory": {
                key: doors[key] for key in (
                    "door_object_root_count",
                    "swinging_assembly_collider_count",
                    "swinging_assembly_collision_enabled_count",
                    "frame_or_jamb_collider_count",
                    "frame_or_jamb_collision_enabled_count",
                    "unrelated_door_named_collider_count",
                )
            },
            "A4_v2_minimum_clear_opening_m": min(
                float(item["minimum_clear_opening_m"])
                for item in doorway["doorways"]),
        }
        rows.append(row)
        if row["status"] != "PASS":
            raise RuntimeError("post-normalization policy failure: " + bid)
        print(f"DONE {index}/10 {bid}", flush=True)
    payload = {
        "schema": "SelectedBatchPhysicsNormalizationV1",
        "status": "PASS",
        "building_count": len(rows),
        "passed_buildings": sum(row["status"] == "PASS" for row in rows),
        "failed_buildings": sum(row["status"] != "PASS" for row in rows),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "status", "building_count", "passed_buildings", "failed_buildings")},
        indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
