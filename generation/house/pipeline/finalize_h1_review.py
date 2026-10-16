#!/usr/bin/env python3
"""Finalize and fail-capably audit the new-batch H1 review package."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--scene-install", type=Path, required=True)
    parser.add_argument("--a1-report", type=Path, required=True)
    parser.add_argument("--a4-report", type=Path, required=True)
    parser.add_argument("--capacity-sampler", type=Path, required=True)
    args = parser.parse_args()

    package = args.package
    manifest_path = package / "manifest.json"
    manifest = load(manifest_path)
    authorization = load(args.authorization)
    policy = load(args.policy)
    fixture = load(args.fixture)
    scene_install = load(args.scene_install)
    a1 = load(args.a1_report)
    a4 = load(args.a4_report)
    require(manifest.get("status") == "AWAITING_HUMAN_APPROVAL",
            "H1 manifest is not awaiting human approval")
    require(manifest.get("batch_generation_authorized") is True
            and manifest.get("automatic_batch_generation_authorized") is True,
            "fresh generation authorization is absent")
    require(manifest.get("training_authorized") is False,
            "training authorization changed")
    require(manifest.get("h1_placement_approved") is False
            and manifest.get("stage_b_blocked_pending_h1") is True,
            "H1 freeze boundary is not active")
    buildings = manifest.get("buildings", [])
    require(len(buildings) == 10, "H1 review set is not exactly ten")
    require(manifest.get("dependency_eligible_candidate_count") == 10
            and manifest.get("dependency_failed_candidate_count") == 0,
            "selected H1 dependency split differs from 10 PASS / 0 FAIL")
    require(manifest.get("all_a4_pair_outcomes_retained") is True,
            "A4 pair outcomes are incomplete")
    require(manifest.get("a4_missing_stair_asset_rejection_count") == 1203,
            "missing-stair candidate count differs")
    require(manifest.get("a4_doorway_clearance_rejection_count") == 5070,
            "doorway rejection count differs")
    require(manifest.get("a4_strict_placement_rejection_count") == 1426,
            "strict placement rejection count differs")
    require(manifest.get("full_strict_candidate_inventory_count") == 17,
            "strict candidate inventory differs")
    require(manifest.get("excluded_prior_batch_candidate_count") == 1,
            "old-batch exclusion count differs")
    require(manifest.get("strict_candidates_not_selected_count") == 6,
            "unselected non-failure count differs")
    require(fixture.get("status") == "PASS"
            and fixture.get("negative_fixture", {}).get(
                "new_A4_doorway_gate_pass") is False,
            "doorway negative fixture is not fail-capable")
    require(scene_install.get("rejected_archive_count") == 4
            and a1.get("retained_source_rejection_count") == 4,
            "A1 source rejection count differs")
    require(a4.get("status") == "PASS" and len(a4.get("searches", [])) == 15,
            "A4 run report is incomplete")

    selected_bids = {row["BID"] for row in buildings}
    excluded_bids = set(manifest.get("excluded_prior_batch_candidates", []))
    require(not selected_bids.intersection(excluded_bids),
            "old-batch BID entered the H1 review set")
    stair_asset_keys = sorted({(float(row["height_m"]), row["kind"])
                               for row in buildings})
    for row in buildings:
        require(row.get("doorway_clearance_gate_pass") is True,
                "selected candidate fails doorway clearance")
        require(row.get("dependency_gate_pass") is True,
                "selected candidate fails dependency gate")
        bid = row["BID"]
        placement = package / bid / "placement"
        record = load(placement / "placement.json")
        require(record.get("placement_contract_version") == 3
                and record.get("doorway_clearance_contract_version") == 2,
                "selected floorplan evidence uses a stale contract")
        for name in ("floorplan_floor1.png", "floorplan_floor2.png"):
            with Image.open(placement / name) as image:
                require(image.size == (1200, 900), "floorplan dimensions differ")

    forbidden = [path for path in package.rglob("decision.json")]
    require(not forbidden, "decision.json exists before H1 approval")
    decisions = load(package / "candidate_decisions.pending.json")
    decision_rows = decisions.get("candidates", [])
    require(len(decision_rows) == len(buildings),
            "candidate decision row count differs from H1 candidate count")
    decision_by_bid = {row.get("BID"): row for row in decision_rows}
    require(set(decision_by_bid) == selected_bids,
            "candidate decision BIDs differ from the H1 review set")
    for record in buildings:
        expected = ("REJECT_REQUIRED" if
                    (not record.get("dependency_gate_pass") or
                     record.get("post_A5_build_blocked")) else "PENDING")
        require(decision_by_bid[record["BID"]].get("decision") == expected,
                "candidate machine decision does not match its gates: " +
                record["BID"])
    reject_required_count = sum(
        row.get("decision") == "REJECT_REQUIRED" for row in decision_rows)
    pending_count = sum(row.get("decision") == "PENDING"
                        for row in decision_rows)
    require(reject_required_count + pending_count == len(buildings),
            "candidate template contains an unauthorized decision state")

    provenance = package / "provenance"
    provenance.mkdir(exist_ok=True)
    for source, name in (
        (args.authorization, "AUTHORIZATION.json"),
        (args.policy, "BATCH_POLICY.json"),
        (args.fixture, "DOORWAY_RULE_FIXTURE_RESULT.json"),
        (args.scene_install, "scene_install_256.json"),
        (args.a1_report, "A1_A3_RUN_REPORT.json"),
        (args.a4_report, "A4_RUN_REPORT.json"),
    ):
        shutil.copy2(source, provenance / name)

    summary_fields = (
        "BID", "floor1", "floor2", "height_m", "kind", "rotation_deg",
        "floor1_min_doorway_clearance_m", "floor2_min_doorway_clearance_m",
        "dependency_gate_pass", "strict_collider_normalization_pass",
        "post_A5_build_blocked", "strict_collider_failed_gates",
        "machine_h1_state", "human_decision",
    )
    with (package / "summary.csv").open(
            "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=summary_fields,
                                lineterminator="\n")
        writer.writeheader()
        for row in buildings:
            output = {field: row.get(field) for field in summary_fields}
            output["strict_collider_failed_gates"] = json.dumps(
                row.get("strict_collider_failed_gates", []), separators=(",", ":"))
            output["machine_h1_state"] = decision_by_bid[row["BID"]]["decision"]
            output["human_decision"] = "PENDING"
            writer.writerow(output)

    sampler_text = args.capacity_sampler.read_text(encoding="utf-8")
    require('ComputePurpose()) != "default"' in sampler_text,
            "capacity sampler no longer selects purpose=default")
    require("load_scene_geometry_cache" in sampler_text,
            "capacity sampler no longer requires the baked geometry cache")
    capacity = {
        "status": "NOT_SCORABLE_BEFORE_H1",
        "new_batch_maximum_face_count": None,
        "new_batch_maximum_boundary_segments": None,
        "reason": (
            "The authoritative capacity extractor needs each composed "
            "building.usda plus its baked scene_geometry_v1 cache. H1 has not "
            "approved a placement, so those artifacts do not exist and Stage B "
            "remains blocked. Creating them would advance past the requested "
            "H1 stop."
        ),
        "sampled_geometry": "renderable meshes with purpose=default, not colliders",
        "collider_repair_effect": "none",
        "configured_face_capacity": policy["scene_input_capacity"]["face_capacity"],
        "configured_boundary_segment_capacity": policy[
            "scene_input_capacity"]["boundary_segment_capacity"],
        "capacity_change": "NONE_PROVISIONAL_PENDING_POST_H1_MEASUREMENT",
        "overflow_policy": "error",
        "capacity_sampler": str(args.capacity_sampler),
        "capacity_sampler_sha256": sha256(args.capacity_sampler),
    }
    (package / "CAPACITY_RECOMPUTE_STATUS.json").write_text(
        json.dumps(capacity, indent=2) + "\n", encoding="utf-8")

    audit = {
        "status": (
            "REVIEW_READY_WITH_MACHINE_REJECTS_AND_EXPLICIT_CAPACITY_BLOCKER"
            if reject_required_count else
            "REVIEW_READY_WITH_EXPLICIT_CAPACITY_BLOCKER"),
        "candidate_count": len(buildings),
        "candidate_machine_pending_count": pending_count,
        "candidate_machine_reject_required_count": reject_required_count,
        "candidate_bids": [row["BID"] for row in buildings],
        "strict_candidate_inventory_count": 17,
        "old_batch_candidates_excluded": 1,
        "strict_candidates_not_selected_not_failures": 6,
        "retained_failures": {
            "A1_incomplete_source_archives": 4,
            "A4_no_exact_stair_asset": 1203,
            "A4_doorway_clearance": 5070,
            "A4_no_strict_placement": 1426,
            "strict_collider_normalization": manifest.get(
                "strict_collider_blocked_candidate_count"),
        },
        "doorway_fixture_status": "PASS",
        "stair_asset_dedup_keys": [list(key) for key in stair_asset_keys],
        "stair_asset_unique_count": len(stair_asset_keys),
        "capacity_status": capacity["status"],
        "gpu_used": False,
        "decision_json_written": False,
        "training_started": False,
        "h1_placement_approved": False,
        "stage_b_blocked_pending_h1": True,
        "manifest_sha256": sha256(manifest_path),
        "summary_sha256": sha256(package / "summary.csv"),
    }
    (package / "H1_PACKAGE_AUDIT.json").write_text(
        json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
