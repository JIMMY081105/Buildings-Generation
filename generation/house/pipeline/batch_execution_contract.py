#!/usr/bin/env python3
"""Fail-closed contracts for the post-H1 building batch workflow.

This module performs no GPU submission. It validates the human authorization
boundary and routes CPU build/pre-flight evidence to the next legal stage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from pipeline_contract import (
    CAMERA_POSE_CONTRACT,
    CAMERA_POSE_CONTRACT_SHA256,
)


CPLUS_GATES = (
    "G19_zero_rigid_body_api",
    "G20_all_approximation_none",
    "G21_per_floor_parity",
    "G22_collider_bounds_match_vertex_union",
    "G23_voxel_bake_clean",
    "G24_stair_reachability",
)
REQUIRED_18_GATES = (
    "C3_hole_open", "C3_four_sides_blocked", "C3_down_lands_on_stair",
    "traversable", "floor2_final_landing_support",
    "build_no_clipping_walls", "build_no_clipping_furniture",
    "build_upper_floor_is_final_landing",
    "build_opening_exit_aligned_to_final_riser",
    "build_final_landing_is_on_floor_side",
    "build_approach_corridor_clear", "build_egress_corridor_clear",
    "sources_unmodified", "render_pairs_complete", "render_nonblack_warm",
    "collision_frames_gray", "actual_rtx5090_active_device_logged",
    "validation_process_exit_zero",
)
CAMERA_VIEWS = tuple(CAMERA_POSE_CONTRACT["views"])
CONDITIONAL_STAGE_B_CONTRACT = (
    "STAGE_B_REGENERATE_STRICT_COLLIDER_OR_RETURN_A4"
)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify_h1_authorization(h1_root: Path, authorization_path: Path) -> dict[str, Any]:
    """Return the approved manifest subset or fail before any stage-B action."""
    h1_root = h1_root.resolve()
    manifest_path = h1_root / "manifest.json"
    manifest = load(manifest_path)
    authorization = load(authorization_path.resolve())
    require(manifest.get("status") == "H1_PENDING", "unexpected H1 manifest status")
    require(manifest.get("batch_generation_authorized") is False,
            "H1 package must preserve batch_generation_authorized=false")
    require(manifest.get("automatic_batch_generation_authorized") is False,
            "H1 package must preserve automatic_batch_generation_authorized=false")
    contract_version = authorization.get("authorization_contract_version")
    require(contract_version in (1, 2),
            "unsupported H1 authorization contract")
    require(authorization.get("gate") == "H1_A5_PLACEMENT",
            "authorization is not an H1 placement decision")
    require(authorization.get("status") == "APPROVED",
            "H1 has not been explicitly approved")
    require(authorization.get("authorization_source") == "explicit_user_message",
            "authorization must cite an explicit user message")
    if contract_version == 2:
        source_artifact = Path(str(
            authorization.get("authorization_source_artifact", "")))
        require(source_artifact.is_file(),
                "authorization source message artifact is absent")
        require(authorization.get("authorization_source_sha256") ==
                digest(source_artifact),
                "authorization source message hash mismatch")
    require(authorization.get("batch_generation_authorized") is True,
            "batch_generation_authorized must be explicitly true")
    require(authorization.get("automatic_batch_generation_authorized") is True,
            "automatic_batch_generation_authorized must be explicitly true")
    require(authorization.get("h1_manifest_sha256") == digest(manifest_path),
            "authorization does not bind the current H1 manifest")
    require(
        authorization.get("delivery_location_confirmed")
        == manifest.get("final_delivery_location_proposed"),
        "delivery location was not explicitly confirmed",
    )

    buildings = manifest.get("buildings", [])
    by_bid = {item["BID"]: item for item in buildings}
    require(len(by_bid) == len(buildings) == 10,
            "H1 manifest must contain ten unique candidates")
    decisions = authorization.get("decisions")
    require(isinstance(decisions, list), "authorization decisions must be a list")
    decision_by_bid = {}
    for item in decisions:
        bid = item.get("BID")
        require(bid in by_bid, "authorization contains unknown BID: " + str(bid))
        require(bid not in decision_by_bid, "duplicate H1 decision: " + str(bid))
        decision = item.get("decision")
        require(decision in (
            "approve", "reject", "approved", "rejected",
            "approved_conditional",
        ), "H1 decision is unsupported: " + str(bid))
        if decision == "approved_conditional":
            require(by_bid[bid].get("post_A5_build_blocked") is True,
                    "conditional approval requires a Stage-B-blocked candidate")
            require(item.get("condition_contract_id") ==
                    CONDITIONAL_STAGE_B_CONTRACT,
                    "conditional approval lacks the binding Stage-B contract")
            conditions = item.get("binding_conditions")
            require(isinstance(conditions, list) and len(conditions) >= 4,
                    "conditional approval must record all binding conditions")
        elif decision in ("approve", "approved"):
            require(by_bid[bid].get("post_A5_build_blocked") is not True,
                    "Stage-B-blocked candidate cannot be approved unconditionally")
        decision_by_bid[bid] = item
    require(set(decision_by_bid) == set(by_bid),
            "every H1 candidate needs exactly one human decision")

    approved_values = {"approve", "approved", "approved_conditional"}
    approved = [by_bid[bid] for bid, item in decision_by_bid.items()
                if item["decision"] in approved_values]
    rejected = [by_bid[bid] for bid, item in decision_by_bid.items()
                if item["decision"] in {"reject", "rejected"}]
    require(8 <= len(approved) <= 10,
            "the authorized batch must retain 8-10 approved buildings")
    return {
        "status": "AUTHORIZED",
        "h1_manifest": str(manifest_path),
        "h1_manifest_sha256": digest(manifest_path),
        "authorization": str(authorization_path.resolve()),
        "approved_buildings": approved,
        "rejected_buildings": rejected,
        "decision_by_bid": decision_by_bid,
        "delivery_location": manifest["final_delivery_location_proposed"],
        "batch_generation_authorized": True,
        "automatic_batch_generation_authorized": True,
    }


def draft_execution_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    """Describe post-H1 tasks without granting permission to execute them."""
    buildings = manifest.get("buildings", [])
    asset_cells = sorted({
        (int(round(float(item["height_m"]) * 100)), item["kind"])
        for item in buildings
    })
    return {
        "status": "NOT_EXECUTABLE_PENDING_H1",
        "candidate_count": len(buildings),
        # Shape is part of the cache key: two shapes at one height are distinct
        # staircase assets, while repeated buildings share that shape/height.
        "stage_B_dedup_key": ["height_mm", "kind"],
        "stage_B_assets": [
            {"height_mm": height, "height_m": height / 100.0, "kind": kind}
            for height, kind in asset_cells
        ],
        "stage_C_buildings": [item["BID"] for item in buildings],
        "stage_C2_rule": (
            "wall or furniture clipping => RETURN_TO_A4; never advance to D"
        ),
        "stage_Cplus_required_gates": list(CPLUS_GATES),
        "stage_D_camera_pose_contract_sha256": CAMERA_POSE_CONTRACT_SHA256,
        "stage_D_camera_views": list(CAMERA_VIEWS),
        "stage_D_concurrency": 6,
        "batch_generation_authorized": False,
        "automatic_batch_generation_authorized": False,
    }


def route_c2(build_report: dict[str, Any]) -> dict[str, Any]:
    wall = build_report.get("QA_no_clipping_walls")
    furniture = build_report.get("QA_no_clipping_furniture")
    if wall is False or furniture is False:
        reasons = []
        if wall is False:
            reasons.append("QA_no_clipping_walls=false")
        if furniture is False:
            reasons.append("QA_no_clipping_furniture=false")
        return {
            "route": "RETURN_TO_A4",
            "may_advance_to_D": False,
            "reasons": reasons,
        }
    if wall is not True or furniture is not True:
        return {
            "route": "FAIL_CLOSED_MISSING_C2_EVIDENCE",
            "may_advance_to_D": False,
            "reasons": ["both C2 clipping gates must be explicit booleans"],
        }
    gates = build_report.get("build_acceptance_gates")
    if build_report.get("status") != "pass" or not isinstance(gates, dict) or not gates or not all(gates.values()):
        return {
            "route": "REJECT_CPU_BUILD",
            "may_advance_to_D": False,
            "reasons": ["CPU build or a non-clipping build gate failed"],
        }
    return {"route": "ADVANCE_TO_CPLUS", "may_advance_to_D": False, "reasons": []}


def route_cplus(gate_report: dict[str, Any]) -> dict[str, Any]:
    gates = gate_report.get("gates", {})
    missing = [name for name in CPLUS_GATES if name not in gates]
    failed = [name for name in CPLUS_GATES
              if name in gates and gates[name].get("status") != "PASS"]
    passed = not missing and not failed
    return {
        "route": "ADVANCE_TO_D" if passed else "REJECT_CPLUS",
        "may_advance_to_D": passed,
        "missing_gates": missing,
        "failed_gates": failed,
    }


def verify_camera_batch(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    require(reports, "camera batch is empty")
    failures = {}
    hashes = set()
    for bid, report in reports.items():
        reasons = []
        contract_hash = report.get("camera_pose_contract_sha256")
        hashes.add(contract_hash)
        if contract_hash != CAMERA_POSE_CONTRACT_SHA256:
            reasons.append("camera contract hash mismatch")
        if report.get("camera_pose_contract") != CAMERA_POSE_CONTRACT:
            reasons.append("camera contract document mismatch")
        if report.get("camera_pair_pose_consistency") is not True:
            reasons.append("render/collision camera pair drift")
        poses = report.get("camera_poses", {})
        if set(poses) != set(CAMERA_VIEWS):
            reasons.append("camera pose set is not the five-view contract")
        if reasons:
            failures[bid] = reasons
    passed = not failures and hashes == {CAMERA_POSE_CONTRACT_SHA256}
    return {
        "status": "PASS" if passed else "FAIL",
        "building_count": len(reports),
        "expected_contract_sha256": CAMERA_POSE_CONTRACT_SHA256,
        "observed_contract_sha256": sorted(str(value) for value in hashes),
        "failures": failures,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("draft-plan")
    plan.add_argument("--h1-root", required=True, type=Path)
    auth = sub.add_parser("verify-authorization")
    auth.add_argument("--h1-root", required=True, type=Path)
    auth.add_argument("--authorization", required=True, type=Path)
    c2 = sub.add_parser("route-c2")
    c2.add_argument("--build-report", required=True, type=Path)
    cp = sub.add_parser("route-cplus")
    cp.add_argument("--gate-report", required=True, type=Path)
    camera = sub.add_parser("verify-camera-batch")
    camera.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args(argv)

    if args.command == "draft-plan":
        result = draft_execution_plan(load(args.h1_root / "manifest.json"))
    elif args.command == "verify-authorization":
        result = verify_h1_authorization(args.h1_root, args.authorization)
    elif args.command == "route-c2":
        result = route_c2(load(args.build_report))
    elif args.command == "route-cplus":
        result = route_cplus(load(args.gate_report))
    else:
        reports = {path.parent.parent.name: load(path) for path in args.reports}
        result = verify_camera_batch(reports)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
