#!/usr/bin/env python3
"""Factory-specific type adapter for the frozen A5 compiler contracts."""

from __future__ import annotations

import json


MANIFEST_FIELDS = (
    "BID", "floor1", "floor2", "kind", "height_m",
    "x0", "y0", "x1", "y1", "rotation_deg", "room", "scene_type",
)


def scene_token(name: str) -> str:
    if name.endswith("_ceiling"):
        name = name[:-len("_ceiling")]
    return name.replace("_", "")


def bid_for(candidate: dict) -> str:
    height = int(round(float(candidate["floor1_f2f"]) * 100))
    kind = candidate["kind"].replace("_", "")
    return "{}_{}_{}_{}_h{}".format(
        candidate["scene_type"], scene_token(candidate["floor1"]),
        scene_token(candidate["floor2"]), kind, height,
    )


def evidence_text(record: dict) -> str:
    evidence = record.get("factory_evidence", {})
    return json.dumps({
        "type": record.get("type"),
        "strict_factory_source_review_pass": record.get(
            "strict_factory_source_review_pass"),
        "factory_core_room_pass": record.get("factory_core_room_pass"),
        "factory_core_rooms": record.get("factory_core_rooms", []),
        "score": evidence.get("score"),
        "occurrences": evidence.get("occurrences", {}),
        "unique_assets": evidence.get("unique_assets", {}),
    }, sort_keys=True, separators=(",", ":"))


def validate(candidate: dict, types: dict) -> None:
    floor1 = types[candidate["floor1"]]
    floor2 = types[candidate["floor2"]]
    if floor1.get("type") != "factory" or floor2.get("type") != "factory":
        raise ValueError("strict factory type gate failed for {} + {}".format(
            candidate["floor1"], candidate["floor2"]))
    if any(record.get("strict_factory_source_review_pass") is not True
           for record in (floor1, floor2)):
        raise ValueError("strict factory source review is not bound to both floors")
    if any(record.get("factory_core_room_pass") is not True
           for record in (floor1, floor2)):
        raise ValueError("factory core-room gate failed on one or both floors")
    if candidate.get("scene_type") != "factory":
        raise ValueError("placement type evidence is not factory")
    if not (candidate.get("floor1_cross_room_vote_pass")
            and candidate.get("floor2_cross_room_vote_pass")):
        raise ValueError("factory evidence is absent on one or both floors")
    if float(candidate["height_error_m"]) > 0.001:
        raise ValueError("height error exceeds 1 mm")
    if float(candidate.get("floor2_overhang_ratio", 1.0)) > 0.12:
        raise ValueError("floor2 overhang exceeds 12%")
    if not candidate.get("approach_clear") or not candidate.get("egress_clear"):
        raise ValueError("approach/egress corridor check failed")
    if not candidate.get("precision_clip_gate_pass"):
        raise ValueError("A4 precision stair-vs-scene clipping gate failed")
    if candidate.get("placement_contract_version") != 3:
        raise ValueError("placement was not audited with corridor contract v3")
    if candidate.get("doorway_clearance_contract_version") != 2:
        raise ValueError("placement lacks doorway-clearance contract v2")
    if candidate.get("doorway_clearance_gate_pass") is not True:
        raise ValueError("rasterized collision occupancy narrows a doorway below 0.60 m")
    if any(candidate.get(floor + "_doorway_clearance", {}).get("pass") is not True
           for floor in ("floor1", "floor2")):
        raise ValueError("a selected factory storey fails doorway clearance")
    if candidate.get("corridor_obstacle_source") != "all_obstacle_boxes_walk_prism":
        raise ValueError("corridor audit omitted visual-only furniture AABBs")
    if int(candidate["rotation_deg"]) not in (0, 90, 180, 270):
        raise ValueError("unsupported rotation")
