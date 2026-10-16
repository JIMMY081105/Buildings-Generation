#!/usr/bin/env python3
"""Finalize the collision-raster doorway contract and held-H1 evidence."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from doorway_clearance import evaluate_rasterized_centerline


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--old-controls", type=Path, required=True)
    parser.add_argument("--raster-dir", type=Path, required=True)
    parser.add_argument("--h1-summary", type=Path, required=True)
    parser.add_argument("--rule-source", type=Path, required=True)
    parser.add_argument("--fixture-source", type=Path, required=True)
    parser.add_argument("--counter-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite evidence: {args.output}")

    doorway = {
        "prim": "/World/Geometry/doorway_fixture/Geometry/Doorway_1",
        "frame_bounds_m": [0.0, -0.05, 0.0, 1.0, 0.05, 2.1],
        "width_axis": 0,
        "centre_depth_m": 0.0,
        "aperture_interval_m": [0.0, 1.0],
        "aperture_source": "fixture_1m_aperture",
    }
    leaf = "/World/Geometry/doorway_fixture/Geometry/Doorway_1_door_1"
    occupied = np.zeros(20, dtype=np.bool_)
    occupied[:18] = True
    closed = evaluate_rasterized_centerline(
        doorway, occupied,
        [{leaf} if index < 18 else set() for index in range(20)])
    opened = evaluate_rasterized_centerline(
        doorway, np.zeros(20, dtype=np.bool_), [set() for _ in range(20)])
    require(closed["pass"] is False and closed["minimum_clear_opening_m"] == 0.1,
            "hung closed door negative fixture did not go RED")
    require(opened["pass"] is True and opened["minimum_clear_opening_m"] == 1.0,
            "open-door positive control did not pass")

    old = load(args.old_controls)["stages"]
    old_counts = [(row["full_door_panel_collider_count"],
                   row["full_door_panel_collision_enabled_count"],
                   row["full_door_panel_tall_thin_closed_leaf_count"])
                  for row in old]
    require(old_counts == [(279, 279, 24), (170, 170, 31)],
            "door-panel selector does not reproduce provided controls")

    source_rows = load(args.source_inventory)["stages"]
    source_by_scene = {
        str(row["stage"]).replace("\\", "/").split("/")[-2].replace(
            "_ceiling", ""): row
        for row in source_rows
    }
    raster_by_scene = {}
    for path in sorted(args.raster_dir.glob("train*_doorway_raster_v2.json")):
        scene = path.stem.replace("_doorway_raster_v2", "")
        if scene.startswith("train") and not scene.startswith("train_"):
            scene = "train_" + scene[len("train"):]
        row = load(path)
        require(row["contract_version"] == 2, f"stale raster contract: {scene}")
        require(row["pass"] is False and row["failed_doorway_count"] >= 1,
                f"closed-door source scene unexpectedly passes: {scene}")
        raster_by_scene[scene] = row

    with args.h1_summary.open("r", encoding="utf-8-sig", newline="") as stream:
        h1_rows = list(csv.DictReader(stream))
    pending = [row for row in h1_rows if row["machine_h1_state"] == "PENDING"]
    require(len(pending) == 5, "H1 pending candidate count differs")
    candidates = []
    for row in pending:
        floor1 = row["floor1"].replace("_ceiling", "")
        floor2 = row["floor2"].replace("_ceiling", "")
        source1, source2 = source_by_scene[floor1], source_by_scene[floor2]
        raster1, raster2 = raster_by_scene[floor1], raster_by_scene[floor2]
        candidate = {
            "BID": row["BID"],
            "floor1": floor1,
            "floor2": floor2,
            "door_panel_collider_count": (
                source1["full_door_panel_collider_count"]
                + source2["full_door_panel_collider_count"]),
            "door_panel_collision_enabled_count": (
                source1["full_door_panel_collision_enabled_count"]
                + source2["full_door_panel_collision_enabled_count"]),
            "tall_thin_closed_leaf_count": (
                source1["full_door_panel_tall_thin_closed_leaf_count"]
                + source2["full_door_panel_tall_thin_closed_leaf_count"]),
            "floor1_collision_raster_pass": raster1["pass"],
            "floor2_collision_raster_pass": raster2["pass"],
            "collision_raster_gate_pass": raster1["pass"] and raster2["pass"],
            "minimum_rasterized_doorway_gap_m": min(
                min(item["minimum_clear_opening_m"] for item in raster1["doorways"]),
                min(item["minimum_clear_opening_m"] for item in raster2["doorways"]),
            ),
        }
        require(candidate["door_panel_collider_count"] ==
                candidate["door_panel_collision_enabled_count"],
                "candidate contains a mixed door collision state")
        require(candidate["collision_raster_gate_pass"] is False,
                "pending candidate survived the corrected doorway gate")
        candidates.append(candidate)
    expected = {
        "home_train96_train99_straight_h270": (84, 84, 21),
        "home_train249_train74_ushaped_h360": (203, 203, 22),
        "home_train74_train157_straight_h300": (218, 218, 23),
        "home_train249_train74_lshaped_h360": (203, 203, 22),
        "home_train178_train132_straight_h270": (332, 332, 32),
    }
    require({row["BID"]: (row["door_panel_collider_count"],
                           row["door_panel_collision_enabled_count"],
                           row["tall_thin_closed_leaf_count"])
             for row in candidates} == expected,
            "pending candidate door census differs")

    payload = {
        "status": "PASS_RULE_PROOF_H1_CANDIDATES_RED",
        "device": "CPU",
        "contract_version": 2,
        "rule": (
            "maximum unoccupied lateral run in the 5 cm rasterized enabled-"
            "collider occupancy at every doorway centre line >= 0.60 m; all "
            "colliders in the aperture, including door leaves and frames, count"),
        "threshold_m": 0.60,
        "hung_closed_door_negative_fixture": {
            "nominal_aperture_width_m": 1.0,
            "obsolete_nominal_aperture_rule_pass": True,
            "closed_leaf_width_m": 0.90,
            "rasterized_clear_gap_m": closed["minimum_clear_opening_m"],
            "corrected_gate_result": "RED",
            "collision_blockers": closed["collision_blockers"],
        },
        "open_door_positive_control": {
            "rasterized_clear_gap_m": opened["minimum_clear_opening_m"],
            "corrected_gate_result": "PASS",
        },
        "selector_control_counts": old_counts,
        "h1_status": "HOLD_DO_NOT_APPROVE",
        "pending_candidates_tested": len(candidates),
        "pending_candidates_passing_corrected_gate": 0,
        "pending_candidates": candidates,
        "source_hashes": {
            "doorway_clearance.py": sha256(args.rule_source),
            "test_doorway_clearance.py": sha256(args.fixture_source),
            "inspect_door_panel_colliders.py": sha256(args.counter_source),
            "source_inventory": sha256(args.source_inventory),
            "old_controls": sha256(args.old_controls),
            "h1_summary": sha256(args.h1_summary),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "candidates": [(row["BID"], row["door_panel_collider_count"],
                        row["tall_thin_closed_leaf_count"],
                        row["minimum_rasterized_doorway_gap_m"])
                       for row in candidates],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
