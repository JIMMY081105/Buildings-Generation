#!/usr/bin/env python3
"""Seal and finalize exact-10 factory Stage D validation on the SQZ A40."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from batch_execution_contract import CAMERA_VIEWS, REQUIRED_18_GATES
from pipeline_contract import BODY_RADIUS_M, CAMERA_POSE_CONTRACT_SHA256, CORRIDOR_M


EXPECTED = 10
INITIAL_H1_STATUS = "H1_APPROVED_10_UNSUPERVISED_DIRECT_USER_AUTHORIZATION"
A40_REQUIRED_18_GATES = tuple(
    "actual_a40_active_device_logged"
    if name == "actual_rtx5090_active_device_logged" else name
    for name in REQUIRED_18_GATES
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_file(path: Path, label: str) -> Path:
    path = path.expanduser().resolve()
    require(path.is_file() and path.stat().st_size > 0,
            f"missing or empty {label}: {path}")
    return path


def write_new(path: Path, value: object) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def exact_summary(path: Path, expected_schema_prefix: str | None = None) -> dict[str, Any]:
    value = load(require_file(path, path.name))
    if expected_schema_prefix is not None:
        require(str(value.get("schema", "")).startswith(expected_schema_prefix),
                f"unexpected schema: {path}")
    require(value.get("status") == "PASS", f"not PASS: {path}")
    require(value.get("building_count") == EXPECTED, f"wrong building count: {path}")
    require(value.get("passed_buildings") == EXPECTED
            and value.get("failed_buildings") == 0,
            f"wrong pass/fail count: {path}")
    require(len(value.get("rows", [])) == EXPECTED, f"wrong row count: {path}")
    return value


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    work = args.work_root.expanduser().resolve()
    h1 = args.h1_package.expanduser().resolve()
    scripts_root = args.scripts_root.expanduser().resolve()
    base_scripts = args.base_scripts_root.expanduser().resolve()
    stage_root = work / "stage_c_factory10"
    require_file(work / "STAGE_B_CPU_COMPLETE", "Stage B CPU completion marker")

    manifest_path = require_file(h1 / "manifest.json", "H1 manifest")
    manifest = load(manifest_path)
    approved = sorted(manifest.get("stage_b_authorized_building_bids", []))
    require(manifest.get("status") == INITIAL_H1_STATUS,
            "H1 is not at the autonomous pre-A40 boundary")
    require(len(approved) == EXPECTED and len(set(approved)) == EXPECTED,
            "H1 exact-10 approved set differs")
    require(manifest.get("deferred_building_bids") == [], "H1 contains deferrals")
    require(manifest.get("h1_human_visual_approval_claimed") is False,
            "pre-A40 H1 falsely claims human visual review")
    require(manifest.get("h1_isaac_review_claimed") is False,
            "pre-A40 H1 falsely claims Isaac review")
    require(manifest.get("h1_final_status_pending_sqz_a40_review") is True,
            "pre-A40 pending-review marker is absent")
    require(manifest.get("training_authorized") is False,
            "training was authorized before Stage D")

    stage = load(require_file(stage_root / "stage_c_summary.json", "Stage C summary"))
    require(stage.get("status") == "PASS" and stage.get("building_count") == EXPECTED
            and stage.get("pass_count") == EXPECTED and stage.get("fail_count") == 0,
            "Stage C is not exact-10 PASS")
    by_bid = {row["BID"]: row for row in stage.get("rows", [])}
    require(set(by_bid) == set(approved), "Stage C BID set differs")
    normalization = exact_summary(work / "PHYSICS_NORMALIZATION_FACTORY10.json")
    cpu = exact_summary(work / "CPU_G19_G24_FACTORY10_SUMMARY.json")
    audit = exact_summary(work / "PACKAGED_GEOMETRY_AUDIT_FACTORY10.json")
    packaging = load(require_file(
        work / "SELF_CONTAINED_PACKAGING_FACTORY10_SUMMARY.json", "packaging summary"))
    require(packaging.get("status") == "PASS"
            and packaging.get("expected_building_count") == EXPECTED
            and packaging.get("observed_building_count") == EXPECTED,
            "packaging is not exact-10 PASS")
    stage_b = load(require_file(work / "stage_b_summary.json", "Stage B summary"))
    require(stage_b.get("status") == "PASS"
            and stage_b.get("building_count") == EXPECTED,
            "Stage B summary is not exact-10 PASS")
    require(float(stage_b.get("corridor_m")) == CORRIDOR_M
            and float(stage_b.get("body_radius_m")) == BODY_RADIUS_M,
            "frozen corridor contract differs")
    require(stage_b.get("machine_gate_thresholds_modified") is False,
            "machine gate thresholds were modified")
    for label, value in (("normalization", normalization), ("CPU", cpu), ("audit", audit)):
        require({row.get("BID") for row in value.get("rows", [])} == set(approved),
                f"{label} BID set differs")

    buildings: list[dict[str, Any]] = []
    for bid in approved:
        row = by_bid[bid]
        corridor = row.get("corridor_contract", {})
        require(float(corridor.get("corridor_m")) == CORRIDOR_M
                and float(corridor.get("body_radius_m")) == BODY_RADIUS_M,
                f"{bid}: corridor contract differs")
        stage_path = require_file(stage_root / bid / "scene" / "building.usda", f"{bid} stage")
        build_report = require_file(stage_root / bid / "reports" / "build_report.json",
                                    f"{bid} build report")
        stair_path = require_file(Path(row["stage_b_asset"]["traversal_path"]),
                                  f"{bid} traversal path")
        buildings.append({
            "BID": bid,
            "stage": str(stage_path), "stage_sha256": sha256(stage_path),
            "build_report": str(build_report), "build_report_sha256": sha256(build_report),
            "stair_path": str(stair_path), "stair_path_sha256": sha256(stair_path),
            "height_mm": row["height_mm"], "kind": row["kind"],
            "corridor_m": CORRIDOR_M, "body_radius_m": BODY_RADIUS_M,
        })

    script_specs = {
        "stage_d10_a40_contract.py": scripts_root / "stage_d10_a40_contract.py",
        "finalize_stage_b_a40_isaac.py": scripts_root / "finalize_stage_b_a40_isaac.py",
        "batch_execution_contract.py": base_scripts / "batch_execution_contract.py",
        "collision_render_isolation.py": base_scripts / "collision_render_isolation.py",
        "pipeline_contract.py": base_scripts / "pipeline_contract.py",
        "validate_building_isaac.py": base_scripts / "validate_building_isaac.py",
    }
    scripts = {}
    for name, path in script_specs.items():
        path = require_file(path, name)
        scripts[name] = {"path": str(path), "sha256": sha256(path),
                         "bytes": path.stat().st_size}
    return {
        "schema": "StageD10FactoryA40GPUPreflightV1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "exact-10 factory GPU spatial review and Stage D validation",
        "expected_building_count": EXPECTED,
        "candidate_bids": approved,
        "h1_binding": {
            "package": str(h1), "manifest": str(manifest_path),
            "manifest_sha256": sha256(manifest_path),
            "candidate_review_sha256": sha256(require_file(
                h1 / "candidate_review.csv", "candidate review")),
            "status": manifest["status"],
            "human_visual_approval_claimed": False,
            "isaac_review_claimed": False,
        },
        "stage_b_cpu_binding": {
            "work_root": str(work),
            "stage_c_summary_sha256": sha256(stage_root / "stage_c_summary.json"),
            "normalization_summary_sha256": sha256(work / "PHYSICS_NORMALIZATION_FACTORY10.json"),
            "cpu_g19_g24_summary_sha256": sha256(work / "CPU_G19_G24_FACTORY10_SUMMARY.json"),
            "packaging_summary_sha256": sha256(work / "SELF_CONTAINED_PACKAGING_FACTORY10_SUMMARY.json"),
            "package_audit_sha256": sha256(work / "PACKAGED_GEOMETRY_AUDIT_FACTORY10.json"),
        },
        "frozen_contracts": {"corridor_m": CORRIDOR_M,
                             "body_radius_m": BODY_RADIUS_M,
                             "machine_gate_thresholds_modified": False},
        "capacity_boundary": {"graph_token_capacity_evidence_invalidated": True,
                              "previous_batch_capacity_evidence_reused": False,
                              "capacity_change_performed": False,
                              "OBSERVATION_MANIFEST_FROZEN": False},
        "scripts": scripts,
        "buildings": buildings,
        "execution": {"requires_exactly_one_visible_nvidia_a40": True,
                      "active_gpu": 0, "physics_cuda_device": 0,
                      "multi_gpu": False, "cpu_renderer_substitution_allowed": False,
                      "validator_gate_count": len(A40_REQUIRED_18_GATES),
                      "validator_gates": list(A40_REQUIRED_18_GATES)},
    }


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    preflight_path = require_file(args.preflight, "A40 preflight")
    preflight = load(preflight_path)
    require(preflight.get("status") == "PASS", "A40 preflight is not PASS")
    records = {row["BID"]: row for row in preflight.get("buildings", [])}
    require(len(records) == EXPECTED and set(records) == set(preflight["candidate_bids"]),
            "A40 preflight candidate set differs")
    evidence_root = args.evidence_root.expanduser().resolve()
    observed = {path.name for path in evidence_root.iterdir()
                if path.is_dir() and path.name != "runtime"}
    require(observed == set(records), "A40 evidence directories are not exact-10")
    expected_renders = {view + suffix for view in CAMERA_VIEWS
                        for suffix in ("_render", "_collision")}
    rows = []
    for bid in preflight["candidate_bids"]:
        root = evidence_root / bid
        report_path = require_file(root / "reports" / "isaac_validation.json", f"{bid} report")
        verdict_path = require_file(root / "reports" / "stage_b_a40_isaac_verdict.json",
                                    f"{bid} verdict")
        report = load(report_path)
        verdict = load(verdict_path)
        require(verdict.get("status") == "PASS" and verdict.get("building") == bid,
                f"{bid}: A40 verdict not PASS")
        require(verdict.get("isaac_report_sha256") == sha256(report_path),
                f"{bid}: report hash differs")
        require(verdict.get("preflight_sha256") == sha256(preflight_path),
                f"{bid}: preflight hash differs")
        require(verdict.get("input_binding") == {key: records[bid][key] for key in (
            "stage", "stage_sha256", "build_report", "build_report_sha256",
            "stair_path", "stair_path_sha256")}, f"{bid}: input binding differs")
        gates = verdict.get("acceptance_gates", {})
        require(set(gates) == set(A40_REQUIRED_18_GATES) and all(gates.values()),
                f"{bid}: A40 18-gate contract failed")
        require(verdict.get("actual_a40_active_device_logged") is True,
                f"{bid}: active A40 was not proved")
        require(verdict.get("cpu_renderer_substitution_used") is False,
                f"{bid}: CPU renderer substitution was used")
        require(report.get("camera_pose_contract_sha256") == CAMERA_POSE_CONTRACT_SHA256,
                f"{bid}: camera contract differs")
        renders = report.get("renders", {})
        require(set(renders) == expected_renders, f"{bid}: render contract differs")
        for name, item in renders.items():
            image = require_file(root / "renders" / item["file"], f"{bid} {name}")
            require(image.stat().st_size == item.get("bytes"),
                    f"{bid}: render size differs")
            require(verdict.get("render_evidence", {}).get(name, {}).get("sha256") == sha256(image),
                    f"{bid}: render hash differs")
        rows.append({
            "BID": bid, "status": "PASS", "device": "NVIDIA A40",
            "isaac_report": str(report_path), "isaac_report_sha256": sha256(report_path),
            "stage_b_isaac_verdict": str(verdict_path),
            "stage_b_isaac_verdict_sha256": sha256(verdict_path),
            "render_count": len(renders),
            "actual_a40_active_device_logged": True,
            "cpu_renderer_substitution_used": False,
            "failed_gates": [], "blocking_failures": [],
        })
    return {
        "schema": "StageD10FactoryA40IsaacBatchSummaryV1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": preflight["purpose"],
        "preflight": str(preflight_path), "preflight_sha256": sha256(preflight_path),
        "building_count": EXPECTED, "passed_buildings": EXPECTED, "failed_buildings": 0,
        "render_count": sum(row["render_count"] for row in rows),
        "device_cohorts": {"NVIDIA A40": EXPECTED},
        "all_buildings_used_real_nvidia_gpu": True,
        "actual_a40_required_and_proved_per_building": True,
        "cpu_renderer_substitution_used": False,
        "h1_pre_review_binding": preflight["h1_binding"],
        "h1_gpu_spatial_review_result": "APPROVE_EXACT_10_FOR_ADVANCEMENT",
        "human_visual_approval_claimed": False,
        "training_authorized": False,
        "OBSERVATION_MANIFEST_FROZEN": False,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("preflight")
    p.add_argument("--work-root", type=Path, required=True)
    p.add_argument("--h1-package", type=Path, required=True)
    p.add_argument("--scripts-root", type=Path, required=True)
    p.add_argument("--base-scripts-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(function=preflight)
    p = sub.add_parser("finalize")
    p.add_argument("--preflight", type=Path, required=True)
    p.add_argument("--evidence-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(function=finalize)
    args = parser.parse_args()
    value = args.function(args)
    write_new(args.output, value)
    print(json.dumps({"status": value["status"],
                      "building_count": value.get("building_count", value.get("expected_building_count")),
                      "output": str(args.output.expanduser().resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
