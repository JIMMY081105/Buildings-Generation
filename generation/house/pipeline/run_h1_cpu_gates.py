#!/usr/bin/env python3
"""Run and retain G19-G24 for all ten remediated H1 candidates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import traceback

from acceptance_gates import audit
from reachability_gate import audit_reachability


GATES = (
    "G19_zero_rigid_body_api",
    "G20_all_approximation_none",
    "G21_per_floor_parity",
    "G22_collider_bounds_match_vertex_union",
    "G23_voxel_bake_clean",
    "G24_stair_reachability",
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-c-root", required=True, type=Path)
    parser.add_argument("--normalization-summary", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--baker", required=True, type=Path)
    parser.add_argument("--voxel-bake-root", required=True, type=Path)
    parser.add_argument("--control-building", required=True, type=Path)
    parser.add_argument("--control-cache", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--resume", action="store_true",
        help="Reuse only completed per-building g19_g24.json evidence.")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite CPU gate summary")
    if args.evidence_root.exists() and not args.resume:
        raise FileExistsError("refusing to overwrite CPU gate evidence")
    normalization = load(args.normalization_summary)
    if normalization.get("status") != "PASS" or normalization.get(
            "passed_buildings") != 10:
        raise ValueError("physics normalization batch is not 10/10 PASS")
    stage_summary = load(args.stage_c_root / "stage_c_summary.json")
    args.evidence_root.mkdir(parents=True, exist_ok=args.resume)
    rows = []
    for index, source in enumerate(stage_summary["rows"], 1):
        bid = source["BID"]
        building = args.stage_c_root / bid
        evidence = args.evidence_root / bid
        result_path = evidence / "g19_g24.json"
        if args.resume and result_path.is_file():
            result = load(result_path)
            rows.append({
                "BID": bid,
                "status": result["status"],
                "gate_statuses": result.get("gate_statuses", {}),
                "error": result.get("error"),
            })
            print(f"REUSED {index}/10 {bid} {result['status']}", flush=True)
            continue
        evidence.mkdir(exist_ok=args.resume)
        stage = building / "scene" / "building.usda"
        build_report = building / "reports" / "build_report.json"
        cache = evidence / (bid + ".scene_geometry_v1")
        bake_report = evidence / "voxel_bake_report.json"
        result = {"BID": bid, "status": "FAIL", "gates": {}}
        try:
            completed = subprocess.run([
                sys.executable, str(args.baker),
                "--scene", str(stage), "--cache", str(cache),
                "--report", str(bake_report),
            ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (evidence / "voxel_bake.log").write_text(
                completed.stdout, encoding="utf-8")
            if completed.returncode:
                raise RuntimeError(f"voxel bake rc={completed.returncode}")
            structural = audit(
                stage, bake_report, voxel_bake_root=args.voxel_bake_root)
            g24 = audit_reachability(
                stage, build_report, cache,
                control_stage_path=(
                    args.control_building / "scene" / "building.usda"),
                control_build_report_path=(
                    args.control_building / "reports" / "build.json"),
                control_cache_path=args.control_cache,
            )
            gates = structural["gates"] | {"G24_stair_reachability": g24}
            statuses = {name: gates[name]["status"] for name in GATES}
            result.update({
                "status": "PASS" if all(value == "PASS"
                                        for value in statuses.values()) else "FAIL",
                "gate_statuses": statuses,
                "gates": gates,
                "G22b": structural["informational_checks"][
                    "G22b_mesh_extent_matches_points"],
            })
        except Exception as error:
            result["error"] = str(error)
            result["traceback"] = traceback.format_exc()
        result_path.write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8")
        rows.append({
            "BID": bid,
            "status": result["status"],
            "gate_statuses": result.get("gate_statuses", {}),
            "error": result.get("error"),
        })
        print(f"DONE {index}/10 {bid} {result['status']}", flush=True)
    passed = sum(row["status"] == "PASS" for row in rows)
    payload = {
        "schema": "H1MachineG19G24V1",
        "status": "PASS" if passed == 10 else "FAIL",
        "required_gates": list(GATES),
        "building_count": len(rows),
        "passed_buildings": passed,
        "failed_buildings": len(rows) - passed,
        "G22_definition": (
            "actual baker _collider_bounds_m contains the actual rasterized "
            "vertex union, including purpose=guide"),
        "G22b_scope": (
            "informational only; stale per-mesh extent was never observed in "
            "this project"),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "status", "building_count", "passed_buildings", "failed_buildings")},
        indent=2))
    return 0 if passed == 10 else 1


if __name__ == "__main__":
    raise SystemExit(main())
