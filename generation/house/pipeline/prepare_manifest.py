#!/usr/bin/env python3
"""Freeze audited A4 placements into an A5 manifest and human decision table."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


MANIFEST_FIELDS = (
    "BID", "floor1", "floor2", "kind", "height_m",
    "x0", "y0", "x1", "y1", "rotation_deg", "room", "scene_type",
)


def scene_token(name: str) -> str:
    if name.endswith("_ceiling"):
        name = name[:-len("_ceiling")]
    return name.replace("_", "")


def bid_for(candidate: dict) -> str:
    h = int(round(float(candidate["floor1_f2f"]) * 100))
    kind = candidate["kind"].replace("_", "")
    return "{}_{}_{}_{}_h{}".format(
        candidate["scene_type"], scene_token(candidate["floor1"]),
        scene_token(candidate["floor2"]), kind, h)


def evidence_text(record: dict) -> str:
    chunks = []
    for label in ("home", "school"):
        hits = record["evidence"][label]
        assets = ", ".join(
            "{}x{}".format(hit["asset_id"], hit["count"]) for hit in hits)
        chunks.append("{}={} [{}]".format(label, record[label + "_votes"], assets))
    return "; ".join(chunks)


def validate(candidate: dict, types: dict) -> None:
    f1 = types[candidate["floor1"]]
    f2 = types[candidate["floor2"]]
    if f1["type"] not in ("home", "school") or f1["type"] != f2["type"]:
        raise ValueError("type gate failed for {} + {}".format(
            candidate["floor1"], candidate["floor2"]))
    if candidate["scene_type"] != f1["type"]:
        raise ValueError("placement type evidence does not match scene_types.json")
    if not (candidate.get("floor1_cross_room_vote_pass")
            and candidate.get("floor2_cross_room_vote_pass")):
        raise ValueError("type-bearing votes do not span rooms on both floors")
    if float(candidate["height_error_m"]) > 0.001:
        raise ValueError("height error exceeds 1 mm")
    if float(candidate.get("floor2_overhang_ratio", 1.0)) > 0.12:
        raise ValueError("floor2 overhang exceeds 12%")
    if not candidate.get("approach_clear") or not candidate.get("egress_clear"):
        raise ValueError("approach/egress corridor check failed")
    if not candidate.get("precision_clip_gate_pass"):
        raise ValueError("A4 precision stair-vs-scene clipping gate failed")
    if candidate.get("placement_contract_version") != 3:
        raise ValueError("placement was not audited with the current corridor contract")
    if candidate.get("doorway_clearance_contract_version") != 2:
        raise ValueError("placement lacks the A4 doorway-clearance contract")
    if candidate.get("doorway_clearance_gate_pass") is not True:
        raise ValueError("rasterized collision occupancy narrows a doorway below 0.60 m")
    if any(candidate.get(floor + "_doorway_clearance", {}).get("pass") is not True
           for floor in ("floor1", "floor2")):
        raise ValueError("a selected storey fails doorway clearance")
    if candidate.get("corridor_obstacle_source") != "all_obstacle_boxes_walk_prism":
        raise ValueError("corridor audit did not include visual-only furniture AABBs")
    if int(candidate["rotation_deg"]) not in (0, 90, 180, 270):
        raise ValueError("unsupported rotation")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--placements", type=Path, required=True)
    ap.add_argument("--types", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=2)
    ap.add_argument("--require-count", type=int, default=0)
    args = ap.parse_args()

    placement_doc = json.loads(args.placements.read_text(encoding="utf-8"))
    type_doc = json.loads(args.types.read_text(encoding="utf-8"))
    candidates = placement_doc["candidates"][:args.limit]
    if args.require_count and len(candidates) != args.require_count:
        raise SystemExit("required {} candidates, found {}".format(
            args.require_count, len(candidates)))
    types = type_doc["scenes"]
    for candidate in candidates:
        validate(candidate, types)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    seen = set()
    for candidate in candidates:
        bid = bid_for(candidate)
        if bid in seen:
            raise ValueError("duplicate BID: " + bid)
        seen.add(bid)
        x0, y0, x1, y1 = candidate["rect"]
        manifest = {
            "BID": bid,
            "floor1": candidate["floor1"],
            "floor2": candidate["floor2"],
            "kind": candidate["kind"],
            "height_m": candidate["floor1_f2f"],
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "rotation_deg": candidate["rotation_deg"],
            "room": candidate["room"],
            "scene_type": candidate["scene_type"],
        }
        records.append({"manifest": manifest, "placement": candidate})

    with (args.out_dir / "manifest.tsv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, dialect="excel-tab", lineterminator="\n")
        for record in records:
            writer.writerow([record["manifest"][field] for field in MANIFEST_FIELDS])

    manifest_status = ("AWAITING_HUMAN_APPROVAL" if records else
                       "NO_AUTHORIZED_BUILDINGS_RETURN_TO_A4")
    manifest_doc = {
        "status": manifest_status,
        "manifest_tsv_fields": list(MANIFEST_FIELDS),
        "corridor_m": placement_doc["corridor_m"],
        "corridor_body_radius_m": placement_doc["corridor_body_radius_m"],
        "corridor_reserved_length_m": placement_doc["corridor_reserved_length_m"],
        "grid_step_m": placement_doc["grid_step_m"],
        "height_tolerance_m": placement_doc["height_tolerance_m"],
        "rotations_tested_deg": placement_doc["rotations_tested_deg"],
        "buildings": records,
    }
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest_doc, indent=2), encoding="utf-8")

    review_fields = (
        "BID", "floor1", "floor2", "scene_type", "floor1_type_evidence",
        "floor2_type_evidence", "footprint_IoU", "height_m", "height_error_m",
        "floor2_overhang_ratio", "floor2_overhang_pass",
        "floor1_type_bearing_rooms", "floor2_type_bearing_rooms",
        "cross_room_vote_pass",
        "stair_kind", "room", "placement_rect", "rotation_deg",
        "approach_corridor_clear", "egress_corridor_clear",
        "precision_clip_gate_pass", "pipeline_status",
        "human_decision", "review_notes",
    )
    with (args.out_dir / "candidate_review.csv").open(
            "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=review_fields, lineterminator="\n")
        writer.writeheader()
        for record in records:
            candidate = record["placement"]
            writer.writerow({
                "BID": record["manifest"]["BID"],
                "floor1": candidate["floor1"],
                "floor2": candidate["floor2"],
                "scene_type": candidate["scene_type"],
                "floor1_type_evidence": evidence_text(types[candidate["floor1"]]),
                "floor2_type_evidence": evidence_text(types[candidate["floor2"]]),
                "footprint_IoU": candidate["footprint_iou"],
                "height_m": candidate["floor1_f2f"],
                "height_error_m": candidate["height_error_m"],
                "floor2_overhang_ratio": candidate["floor2_overhang_ratio"],
                "floor2_overhang_pass": candidate["floor2_overhang_pass"],
                "floor1_type_bearing_rooms": json.dumps(
                    candidate["floor1_type_bearing_rooms"]),
                "floor2_type_bearing_rooms": json.dumps(
                    candidate["floor2_type_bearing_rooms"]),
                "cross_room_vote_pass": bool(
                    candidate["floor1_cross_room_vote_pass"]
                    and candidate["floor2_cross_room_vote_pass"]),
                "stair_kind": candidate["kind"],
                "room": candidate["room"],
                "placement_rect": json.dumps(candidate["rect"]),
                "rotation_deg": candidate["rotation_deg"],
                "approach_corridor_clear": candidate["approach_clear"],
                "egress_corridor_clear": candidate["egress_clear"],
                "precision_clip_gate_pass": candidate["precision_clip_gate_pass"],
                "pipeline_status": "AWAITING_HUMAN_APPROVAL",
                "human_decision": "",
                "review_notes": "",
            })
    print(json.dumps({
        "status": manifest_status,
        "buildings": [r["manifest"]["BID"] for r in records],
        "out_dir": str(args.out_dir),
    }, indent=2))


if __name__ == "__main__":
    main()
