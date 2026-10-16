#!/usr/bin/env python3
"""Run unchanged G1--G10 graph gates on the exact approved 50-building batch."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import numpy as np

import gate7_global_graph48 as impl


EXPECTED = 50


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h1-package", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    parser.add_argument("--stage-d-summary", type=Path, required=True)
    parser.add_argument("--g25-root", type=Path, required=True)
    parser.add_argument("--g25-summary", type=Path, required=True)
    parser.add_argument("--graphs-root", type=Path, required=True)
    parser.add_argument("--build-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    h1_root = args.h1_package.expanduser().resolve()
    delivery_root = args.delivery_root.expanduser().resolve()
    g25_root = args.g25_root.expanduser().resolve()
    graph_root = args.graphs_root.expanduser().resolve()
    h1_manifest = impl.require_file(h1_root / "manifest.json", "H1 manifest")
    candidate_review = impl.require_file(h1_root / "candidate_review.csv", "candidate review")
    manifest = impl.load_json(h1_manifest)
    decisions = impl.read_decisions(candidate_review)
    approved = sorted(manifest.get("approved_building_bids", []))
    impl.require(manifest.get("status") == "H1_APPROVED_50_AFTER_SQZ_A40_REVIEW",
                 "H1 manifest status differs")
    impl.require(len(approved) == EXPECTED and len(set(approved)) == EXPECTED,
                 "H1 approved set is not 50 unique BIDs")
    impl.require(manifest.get("deferred_building_count") == 0
                 and manifest.get("deferred_building_bids") == [],
                 "H1 still contains deferred buildings")
    impl.require(set(decisions) == set(approved)
                 and all(decisions[bid] == "approve" for bid in approved),
                 "candidate decisions are not exact 50 approvals")
    impl.require(manifest.get("graph_token_capacity_evidence_invalidated") is True,
                 "capacity evidence is not invalidated")
    impl.require(manifest.get("graph_token_capacity_change_authorized_now") is False,
                 "capacity changed before remeasurement")
    impl.require(manifest.get("OBSERVATION_MANIFEST_FROZEN") is False, "T7 is frozen")

    stage_d_path = impl.require_file(args.stage_d_summary, "Stage D summary")
    stage_d = impl.load_json(stage_d_path)
    impl.require(stage_d.get("status") == "PASS"
                 and stage_d.get("passed_buildings") == EXPECTED
                 and stage_d.get("failed_buildings") == 0,
                 "Stage D is not exact 50/50 PASS")
    stage_d_rows = {row["BID"]: row for row in stage_d.get("rows", [])}
    impl.require(set(stage_d_rows) == set(approved), "Stage D set differs")
    g25_summary_path = impl.require_file(args.g25_summary, "G25 summary")
    g25_summary = impl.load_json(g25_summary_path)
    impl.require(g25_summary.get("status") in {"PASS", "FAIL"}
                 and g25_summary.get("passed_buildings", 0)
                 + g25_summary.get("failed_buildings", 0) == EXPECTED,
                 "G25 terminal coverage differs")
    impl.require({row["BID"] for row in g25_summary.get("rows", [])} == set(approved),
                 "G25 set differs")
    build_summary_path = impl.require_file(args.build_summary, "graph build summary")
    build_summary = impl.load_json(build_summary_path)
    impl.require(build_summary.get("status") in {"PASS", "FAIL"}
                 and build_summary.get("coverage_status") == "PASS"
                 and build_summary.get("terminal_building_count") == EXPECTED
                 and build_summary.get("built_building_count") == EXPECTED
                 and build_summary.get("capacity_measured_building_count") == EXPECTED,
                 "graph build lacks exact-50 terminal/capacity coverage")
    impl.require({row["BID"] for row in build_summary.get("rows", [])} == set(approved),
                 "graph build set differs")

    blocking: list[dict[str, Any]] = []
    table: dict[str, Any] = {}
    artifacts_found = 0
    for bid in approved:
        path = graph_root / f"{bid}.multistorey_topology_v1.npz"
        row: dict[str, Any] = {"details": {}, "measured": {}}
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
        gate_names = tuple(f"G{index}" for index in range(1, 10))
        for name in gate_names:
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
                    result = impl.gate4(
                        bid, arrays,
                        usdz=impl.require_file(delivery_root / bid / "scene" / "building.usdz", f"{bid} USDZ"),
                        h1_manifest=h1_manifest, candidate_review=candidate_review,
                        decision=decisions[bid],
                        g25_path=impl.require_file(g25_path, f"{bid} G25 evidence"),
                        stage_d_row=stage_d_rows[bid])
                elif name == "G5":
                    result = impl.gate5(arrays, g25)
                elif name == "G6":
                    result = impl._g6(arrays)
                elif name == "G7":
                    result = impl.gate7(arrays, g25)
                elif name == "G8":
                    result = impl.gate8(arrays, g25, stage_d_rows[bid])
                else:
                    result = impl._g9(arrays)
                    measures = impl.graph_path_measures(arrays)
                    result["actual"].update(measures)
                    result["expected"].update({
                        "goal_next_hop_delta_m": 0.0,
                        "geodesic_not_shorter_than_euclidean": True,
                        "all_next_hops_reach_goal_monotonically": True,
                        "maximum_next_hop_steps": f"<= {len(arrays['node_id'])}",
                    })
                    result["status"] = "PASS" if (
                        result["status"] == "PASS"
                        and measures["goal_next_hop_delta_m"] == 0.0
                        and measures["geodesic_not_shorter_than_euclidean"]
                        and measures["all_next_hops_reach_goal_monotonically"]
                        and measures["maximum_next_hop_steps"] <= len(arrays["node_id"])) else "FAIL"
            except Exception as error:
                result = impl._gate_result(
                    actual={"exception": f"{type(error).__name__}: {error}"},
                    expected="explicit PASS with complete evidence", passed=False,
                    detail="exception is a fail-closed gate failure")
            row[name] = result["status"]
            row["details"][name] = result
            if result["status"] != "PASS":
                blocking.append({"building": bid, "gate": name,
                                 "expected": result["expected"], "actual": result["actual"],
                                 "detail": result["detail"]})
        row["FINAL"] = "PASS" if all(row[name] == "PASS" for name in gate_names) else "FAIL"
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
                    len(arrays["node_id"]), arrays["edge_node_ids"][~stair_mask]),
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
        actual={"artifact_building_count": artifacts_found,
                "G1_through_G9_pass_building_count": passed},
        expected={"artifact_building_count": EXPECTED,
                  "G1_through_G9_pass_building_count": EXPECTED},
        passed=g10_pass, detail="all 50 approved buildings must exist and pass G1--G9")
    if not g10_pass:
        blocking.append({"building": "ALL", "gate": "G10",
                         "expected": g10["expected"], "actual": g10["actual"],
                         "detail": g10["detail"]})
    for row in table.values():
        row["G10"] = g10["status"]
    payload = {
        "schema": "GraphArtifactAcceptanceGate50V1",
        "GRAPH_ARTIFACT_AUTHORIZED": not blocking and g10_pass,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_building_count": EXPECTED, "gate_revision": impl.GATE_REVISION,
        "gates_run": [f"G{index}" for index in range(1, 11)],
        "blocking_failures": blocking, "G10": g10, "table": table,
        "artifact_provenance": {
            "architecture_reversal": impl.ARCHITECTURE_REVERSAL,
            "h1_manifest": str(h1_manifest), "h1_manifest_sha256": impl._sha256_file(h1_manifest),
            "candidate_review": str(candidate_review), "candidate_review_sha256": impl._sha256_file(candidate_review),
            "stage_d_summary": str(stage_d_path), "stage_d_summary_sha256": impl._sha256_file(stage_d_path),
            "g25_summary": str(g25_summary_path), "g25_summary_sha256": impl._sha256_file(g25_summary_path),
            "graph_build_summary": str(build_summary_path),
            "graph_build_summary_sha256": impl._sha256_file(build_summary_path),
        },
        "approved_building_bids": approved, "deferred_building_bids": [],
        "graph_token_capacity_evidence_invalidated": True,
        "capacity_change_performed": False, "OBSERVATION_MANIFEST_FROZEN": False,
    }
    impl.exclusive_json(args.output, payload)
    print(json.dumps({"GRAPH_ARTIFACT_AUTHORIZED": payload["GRAPH_ARTIFACT_AUTHORIZED"],
                      "artifact_building_count": artifacts_found,
                      "passed_building_count": passed,
                      "blocking_failure_count": len(blocking),
                      "output": str(args.output.expanduser().resolve())}, indent=2))
    return 0 if payload["GRAPH_ARTIFACT_AUTHORIZED"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
