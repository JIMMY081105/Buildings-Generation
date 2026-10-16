#!/usr/bin/env python3
"""Run unchanged G1--G9 implementations and exact-54 G10 coverage."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import numpy as np

import gate7_global_graph48 as impl


EXPECTED = 54


def gate4_exact54(
    arrays: dict[str, np.ndarray], *, usdz: Path, input_manifest: Path,
    door_report: Path, collision_audit: Path, g25_path: Path
) -> dict[str, Any]:
    manifest = impl.manifest_from_artifact(arrays)
    live = {
        "source_usdz_sha256": impl._sha256_file(usdz),
        "input_manifest_sha256": impl._sha256_file(input_manifest),
        "door_opening_report_sha256": impl._sha256_file(door_report),
        "collision_audit_sha256": impl._sha256_file(collision_audit),
        "g25_evidence_sha256": impl._sha256_file(g25_path),
    }
    actual = live | {
        "manifest_source_usdz_sha256": manifest.get("source_usdz_sha256"),
        "manifest_decision_lineage_path": manifest.get("decision_json"),
        "manifest_decision_lineage_sha256": manifest.get("decision_json_sha256"),
        "manifest_input_manifest_path": manifest.get("exact54_input_manifest"),
        "manifest_input_manifest_sha256": manifest.get("exact54_input_manifest_sha256"),
        "manifest_door_opening_report_path": manifest.get("door_opening_report"),
        "manifest_door_opening_report_sha256": manifest.get("door_opening_report_sha256"),
        "manifest_collision_audit_path": manifest.get("collision_audit"),
        "manifest_collision_audit_sha256": manifest.get("collision_audit_sha256"),
        "manifest_g25_evidence_path": manifest.get("g25_evidence"),
        "manifest_g25_evidence_sha256": manifest.get("g25_evidence_sha256"),
        "human_approved": manifest.get("human_approved"),
        "usage": manifest.get("usage"),
        "architecture_reversal": manifest.get("architecture_reversal"),
        "capacity_evidence_invalidated": manifest.get("graph_token_capacity_evidence_invalidated"),
        "OBSERVATION_MANIFEST_FROZEN": manifest.get("OBSERVATION_MANIFEST_FROZEN"),
    }
    expected = live | {
        "manifest_source_usdz_sha256": live["source_usdz_sha256"],
        "manifest_decision_lineage_path": str(input_manifest),
        "manifest_decision_lineage_sha256": live["input_manifest_sha256"],
        "manifest_input_manifest_path": str(input_manifest),
        "manifest_input_manifest_sha256": live["input_manifest_sha256"],
        "manifest_door_opening_report_path": str(door_report),
        "manifest_door_opening_report_sha256": live["door_opening_report_sha256"],
        "manifest_collision_audit_path": str(collision_audit),
        "manifest_collision_audit_sha256": live["collision_audit_sha256"],
        "manifest_g25_evidence_path": str(g25_path),
        "manifest_g25_evidence_sha256": live["g25_evidence_sha256"],
        "human_approved": True,
        "usage": "training_candidate_pre_freeze",
        "architecture_reversal": impl.ARCHITECTURE_REVERSAL,
        "capacity_evidence_invalidated": True,
        "OBSERVATION_MANIFEST_FROZEN": False,
    }
    return impl._gate_result(
        actual=actual,
        expected=expected,
        passed=actual == expected,
        detail="source, exact-54 approval/input, door, audit, G25 and pre-freeze lineage compared exactly",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--g25-root", required=True, type=Path)
    parser.add_argument("--g25-summary", required=True, type=Path)
    parser.add_argument("--graphs-root", required=True, type=Path)
    parser.add_argument("--build-summary", required=True, type=Path)
    parser.add_argument("--collision-audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    input_root = args.input_root.resolve()
    input_manifest = input_root / "G25_INPUT_MANIFEST.json"
    manifest = impl.load_json(input_manifest)
    bids = sorted(manifest.get("building_bids", []))
    impl.require(
        manifest.get("schema") == "G25InputView54V1"
        and manifest.get("status") == "PASS"
        and manifest.get("building_count") == EXPECTED
        and len(bids) == len(set(bids)) == EXPECTED,
        "input manifest is not exact-54 PASS",
    )
    input_rows = {row["BID"]: row for row in manifest.get("rows", [])}
    impl.require(set(input_rows) == set(bids), "input row set differs")
    g25_summary = impl.load_json(args.g25_summary.resolve())
    impl.require(
        g25_summary.get("building_count") == EXPECTED
        and g25_summary.get("terminal_coverage_complete") is True
        and g25_summary.get("passed_buildings", 0) + g25_summary.get("failed_buildings", 0) == EXPECTED
        and {row["BID"] for row in g25_summary.get("rows", [])} == set(bids),
        "G25 terminal coverage differs",
    )
    build_summary = impl.load_json(args.build_summary.resolve())
    impl.require(
        build_summary.get("coverage_status") == "PASS"
        and build_summary.get("terminal_building_count") == EXPECTED
        and build_summary.get("built_building_count") == EXPECTED
        and build_summary.get("capacity_measured_building_count") == EXPECTED
        and {row["BID"] for row in build_summary.get("rows", [])} == set(bids),
        "graph build lacks exact-54 terminal/capacity coverage",
    )
    collision_audit = args.collision_audit.resolve()
    collision = impl.load_json(collision_audit)
    impl.require(
        collision.get("status") == "PASS"
        and collision.get("scope", {}).get("observed_buildings") == EXPECTED,
        "collision audit is not exact-54 PASS",
    )
    graph_root = args.graphs_root.resolve()
    g25_root = args.g25_root.resolve()
    blocking = []
    table = {}
    artifacts_found = 0
    for bid in bids:
        path = graph_root / f"{bid}.multistorey_topology_v1.npz"
        row = {"details": {}, "measured": {}}
        arrays = None
        load_error: Exception = FileNotFoundError(f"artifact missing: {path}")
        if path.is_file():
            artifacts_found += 1
            try:
                arrays = impl.load_multistorey_artifact(path)
            except Exception as error:
                load_error = error
        g25_path = g25_root / f"{bid}.json"
        try:
            g25 = impl.load_json(impl.require_file(g25_path, f"{bid} G25 evidence"))
        except Exception as error:
            g25 = {"_load_error": f"{type(error).__name__}: {error}"}
        input_row = input_rows[bid]
        door_report = Path(input_row["door_opening_report"])
        for name in tuple(f"G{index}" for index in range(1, 10)):
            try:
                if arrays is None:
                    raise load_error
                if name == "G1":
                    result = impl._g1(arrays)
                elif name == "G2":
                    result = impl._g2(arrays)
                elif name == "G3":
                    result = impl._g3(path, arrays)
                elif name == "G4":
                    result = gate4_exact54(
                        arrays,
                        usdz=Path(input_row["geometry_link"]),
                        input_manifest=input_manifest,
                        door_report=door_report,
                        collision_audit=collision_audit,
                        g25_path=g25_path,
                    )
                elif name == "G5":
                    result = impl.gate5(arrays, g25)
                elif name == "G6":
                    result = impl._g6(arrays)
                elif name == "G7":
                    result = impl.gate7(arrays, g25)
                elif name == "G8":
                    result = impl.gate8(
                        arrays,
                        g25,
                        {"status": impl.load_json(door_report).get("status")},
                    )
                    result["detail"] += "; exact-54 terminal status input is the bound door-opening report"
                else:
                    result = impl._g9(arrays)
                    measures = impl.graph_path_measures(arrays)
                    result["actual"].update(measures)
                    result["expected"].update(
                        {
                            "goal_next_hop_delta_m": 0.0,
                            "geodesic_not_shorter_than_euclidean": True,
                            "all_next_hops_reach_goal_monotonically": True,
                            "maximum_next_hop_steps": f"<= {len(arrays['node_id'])}",
                        }
                    )
                    result["status"] = "PASS" if (
                        result["status"] == "PASS"
                        and measures["goal_next_hop_delta_m"] == 0.0
                        and measures["geodesic_not_shorter_than_euclidean"]
                        and measures["all_next_hops_reach_goal_monotonically"]
                        and measures["maximum_next_hop_steps"] <= len(arrays["node_id"])
                    ) else "FAIL"
            except Exception as error:
                result = impl._gate_result(
                    actual={"exception": f"{type(error).__name__}: {error}"},
                    expected="explicit PASS with complete evidence",
                    passed=False,
                    detail="exception is a fail-closed gate failure",
                )
            row[name] = result["status"]
            row["details"][name] = result
            if result["status"] != "PASS":
                blocking.append(
                    {
                        "building": bid,
                        "gate": name,
                        "expected": result["expected"],
                        "actual": result["actual"],
                        "detail": result["detail"],
                    }
                )
        row["FINAL"] = "PASS" if all(row[f"G{i}"] == "PASS" for i in range(1, 10)) else "FAIL"
        if arrays is not None:
            graph_manifest = impl.manifest_from_artifact(arrays)
            stair = graph_manifest["stair_edge"]
            centers = np.asarray(arrays["node_center_m"])
            stair_mask = np.asarray(arrays["edge_is_stair"], dtype=np.bool_)
            row["measured"] = {
                "floor1_injected_cells": graph_manifest["storey_band_parameters"]["floor1_injected_cells"],
                "floor1_walkable_area_m2": graph_manifest["storeys"]["Floor1"]["walkable_area_m2"],
                "floor2_walkable_area_m2": graph_manifest["storeys"]["Floor2"]["walkable_area_m2"],
                "nodes": len(arrays["node_id"]),
                "nodes_floor1": int(np.count_nonzero(arrays["node_storey_id"] == 0)),
                "nodes_floor2": int(np.count_nonzero(arrays["node_storey_id"] == 1)),
                "edges": len(arrays["edge_id"]),
                "merged_components": impl._connected_component_count(len(arrays["node_id"]), arrays["edge_node_ids"]),
                "components_after_stair_edge_removal": impl._connected_component_count(
                    len(arrays["node_id"]), arrays["edge_node_ids"][~stair_mask]
                ),
                "stair_edge_count": int(np.count_nonzero(stair_mask)),
                "stair_endpoint_xy_m": {
                    "Floor1": centers[int(stair["floor1_node_id"]), :2].tolist(),
                    "Floor2": centers[int(stair["floor2_node_id"]), :2].tolist(),
                },
                "artifact_sha256": impl._sha256_file(path),
                **impl.graph_path_measures(arrays),
            }
        table[bid] = row
    passed = sum(row["FINAL"] == "PASS" for row in table.values())
    g10_pass = artifacts_found == passed == EXPECTED
    g10 = impl._gate_result(
        actual={"artifact_building_count": artifacts_found, "G1_through_G9_pass_building_count": passed},
        expected={"artifact_building_count": EXPECTED, "G1_through_G9_pass_building_count": EXPECTED},
        passed=g10_pass,
        detail="all 54 buildings must exist and pass unchanged G1--G9",
    )
    if not g10_pass:
        blocking.append(
            {"building": "ALL", "gate": "G10", "expected": g10["expected"], "actual": g10["actual"], "detail": g10["detail"]}
        )
    for row in table.values():
        row["G10"] = g10["status"]
    gate_pass_counts = {
        f"G{i}": sum(row[f"G{i}"] == "PASS" for row in table.values())
        for i in range(1, 11)
    }
    payload = {
        "schema": "GraphArtifactAcceptanceGate54V1",
        "GRAPH_ARTIFACT_AUTHORIZED": not blocking and g10_pass,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_building_count": EXPECTED,
        "gate_revision": impl.GATE_REVISION,
        "gates_run": [f"G{i}" for i in range(1, 11)],
        "gate_pass_counts": gate_pass_counts,
        "blocking_failures": blocking,
        "G10": g10,
        "table": table,
        "artifact_provenance": {
            "architecture_reversal": impl.ARCHITECTURE_REVERSAL,
            "input_manifest": str(input_manifest),
            "input_manifest_sha256": impl._sha256_file(input_manifest),
            "collision_audit": str(collision_audit),
            "collision_audit_sha256": impl._sha256_file(collision_audit),
            "g25_summary": str(args.g25_summary.resolve()),
            "g25_summary_sha256": impl._sha256_file(args.g25_summary.resolve()),
            "graph_build_summary": str(args.build_summary.resolve()),
            "graph_build_summary_sha256": impl._sha256_file(args.build_summary.resolve()),
        },
        "building_bids": bids,
        "graph_token_capacity_evidence_invalidated": True,
        "capacity_change_performed": False,
        "OBSERVATION_MANIFEST_FROZEN": False,
    }
    impl.exclusive_json(args.output, payload)
    print(
        json.dumps(
            {
                "GRAPH_ARTIFACT_AUTHORIZED": payload["GRAPH_ARTIFACT_AUTHORIZED"],
                "artifact_building_count": artifacts_found,
                "passed_building_count": passed,
                "gate_pass_counts": gate_pass_counts,
                "blocking_failure_count": len(blocking),
                "output": str(args.output.resolve()),
            },
            indent=2,
        )
    )
    return 0 if payload["GRAPH_ARTIFACT_AUTHORIZED"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
