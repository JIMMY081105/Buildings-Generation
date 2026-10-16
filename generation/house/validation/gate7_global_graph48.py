#!/usr/bin/env python3
"""Fail-closed G1--G10 acceptance for the exact 48 approved graph artifacts."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ProtoMotions-main"))

from gate7_global_graph import (  # noqa: E402
    GATE_REVISION,
    _connected_component_count,
    _g1,
    _g2,
    _g3,
    _g6,
    _g9,
    _gate_result,
    _sha256_file,
)
from protomotions.simulator.base_simulator.multistorey_topology import (  # noqa: E402
    ARCHITECTURE_REVERSAL,
    embedded_topology_arrays,
    load_multistorey_artifact,
    manifest_from_artifact,
)
from protomotions.simulator.base_simulator.scene_geometry_cache import (  # noqa: E402
    FINE_VOXEL_SIZE_M,
)


EXPECTED = 48
DEFERRED = {
    "home_train123_train142_straight_v001_h320",
    "home_train84_train64_straight_v001_h270",
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_file(path: Path, role: str) -> Path:
    resolved = path.expanduser().resolve()
    require(resolved.is_file() and resolved.stat().st_size > 0,
            f"missing or empty {role}: {resolved}")
    return resolved


def exclusive_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")


def read_decisions(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    require(len(rows) == 50, "candidate review must have 50 rows")
    result: dict[str, str] = {}
    for row in rows:
        bid = row.get("BID") or row.get("building") or row.get("candidate")
        require(isinstance(bid, str) and bid, "candidate row lacks BID")
        require(bid not in result, f"duplicate candidate decision: {bid}")
        result[bid] = str(row.get("human_decision", ""))
    return result


def gate4(
    bid: str,
    arrays: dict[str, np.ndarray],
    *,
    usdz: Path,
    h1_manifest: Path,
    candidate_review: Path,
    decision: str,
    g25_path: Path,
    stage_d_row: dict[str, Any],
) -> dict[str, Any]:
    manifest = manifest_from_artifact(arrays)
    live_stage_d_report = require_file(
        Path(stage_d_row["isaac_report"]), f"{bid} live Stage D report")
    live_stage_d_verdict = require_file(
        Path(stage_d_row["stage_b_isaac_verdict"]),
        f"{bid} live Stage D verdict")
    actual = {
        "source_usdz_sha256": _sha256_file(usdz),
        "manifest_source_usdz_sha256": manifest.get("source_usdz_sha256"),
        "candidate_review_sha256": _sha256_file(candidate_review),
        "manifest_candidate_review_sha256": manifest.get(
            "candidate_review_csv_sha256"),
        "manifest_decision_lineage_sha256": manifest.get(
            "decision_json_sha256"),
        "manifest_decision_lineage_path": manifest.get("decision_json"),
        "human_decision": decision,
        "human_approved": manifest.get("human_approved"),
        "usage": manifest.get("usage"),
        "h1_manifest_sha256": _sha256_file(h1_manifest),
        "manifest_h1_manifest_sha256": manifest.get("h1_manifest_sha256"),
        "manifest_h1_manifest_path": manifest.get("h1_manifest"),
        "g25_evidence_sha256": _sha256_file(g25_path),
        "manifest_g25_evidence_sha256": manifest.get("g25_evidence_sha256"),
        "stage_d_isaac_report_sha256": stage_d_row["isaac_report_sha256"],
        "live_stage_d_isaac_report_sha256": _sha256_file(
            live_stage_d_report),
        "manifest_stage_d_isaac_report_sha256": manifest.get(
            "stage_d_isaac_report_sha256"),
        "stage_d_isaac_verdict_sha256": stage_d_row[
            "stage_b_isaac_verdict_sha256"],
        "live_stage_d_isaac_verdict_sha256": _sha256_file(
            live_stage_d_verdict),
        "manifest_stage_d_isaac_verdict_sha256": manifest.get(
            "stage_d_isaac_verdict_sha256"),
        "architecture_reversal": manifest.get("architecture_reversal"),
        "capacity_evidence_invalidated": manifest.get(
            "graph_token_capacity_evidence_invalidated"),
        "OBSERVATION_MANIFEST_FROZEN": manifest.get(
            "OBSERVATION_MANIFEST_FROZEN"),
    }
    expected = {
        "source_usdz_sha256": actual["source_usdz_sha256"],
        "manifest_source_usdz_sha256": actual["source_usdz_sha256"],
        "candidate_review_sha256": actual["candidate_review_sha256"],
        "manifest_candidate_review_sha256": actual["candidate_review_sha256"],
        "manifest_decision_lineage_sha256": actual["candidate_review_sha256"],
        "manifest_decision_lineage_path": str(candidate_review),
        "human_decision": "approve",
        "human_approved": True,
        "usage": "training_candidate_pre_freeze",
        "h1_manifest_sha256": actual["h1_manifest_sha256"],
        "manifest_h1_manifest_sha256": actual["h1_manifest_sha256"],
        "manifest_h1_manifest_path": str(h1_manifest),
        "g25_evidence_sha256": actual["g25_evidence_sha256"],
        "manifest_g25_evidence_sha256": actual["g25_evidence_sha256"],
        "stage_d_isaac_report_sha256": actual[
            "stage_d_isaac_report_sha256"],
        "live_stage_d_isaac_report_sha256": actual[
            "stage_d_isaac_report_sha256"],
        "manifest_stage_d_isaac_report_sha256": actual[
            "stage_d_isaac_report_sha256"],
        "stage_d_isaac_verdict_sha256": actual[
            "stage_d_isaac_verdict_sha256"],
        "live_stage_d_isaac_verdict_sha256": actual[
            "stage_d_isaac_verdict_sha256"],
        "manifest_stage_d_isaac_verdict_sha256": actual[
            "stage_d_isaac_verdict_sha256"],
        "architecture_reversal": ARCHITECTURE_REVERSAL,
        "capacity_evidence_invalidated": True,
        "OBSERVATION_MANIFEST_FROZEN": False,
    }
    return _gate_result(
        actual=actual,
        expected=expected,
        passed=actual == expected,
        detail=(
            "source, H1 approval, Stage D, G25, architecture reversal, and "
            "pre-freeze capacity lineage compared exactly"
        ),
    )


def gate5(arrays: dict[str, np.ndarray], g25: dict[str, Any]) -> dict[str, Any]:
    manifest = manifest_from_artifact(arrays)
    modelling = g25["storeys"]["Floor1"]["modelling_decision"]
    actual = {
        "floor1_injected_cells": manifest["storey_band_parameters"][
            "floor1_injected_cells"],
        "floor1_support_top_z_m": manifest["storey_band_parameters"][
            "floor1_support_top_z_m"],
    }
    expected = {
        "floor1_injected_cells": modelling["new_ground_cells_injected"],
        "floor1_support_top_z_m": modelling["rasterized_support_top_z_m"],
    }
    passed = (
        int(actual["floor1_injected_cells"])
        == int(expected["floor1_injected_cells"])
        and abs(float(actual["floor1_support_top_z_m"])
                - float(expected["floor1_support_top_z_m"]))
        <= 0.0001 + 1.0e-12
    )
    return _gate_result(
        actual=actual,
        expected=expected | {"support_top_tolerance_m": 0.0001},
        passed=passed,
        detail="G25-bound injected-cell and support-top regression",
    )


def gate7(arrays: dict[str, np.ndarray], g25: dict[str, Any]) -> dict[str, Any]:
    actual: dict[str, float] = {}
    expected: dict[str, float] = {}
    passed = True
    for storey, prefix in (("Floor1", "floor1"), ("Floor2", "floor2")):
        support = embedded_topology_arrays(arrays, prefix)["support_z_index"]
        actual[storey] = round(
            int(np.count_nonzero(support >= 0)) * FINE_VOXEL_SIZE_M**2, 4)
        expected[storey] = float(g25["storeys"][storey]["walkable_area_m2"])
        passed &= abs(actual[storey] - expected[storey]) <= 0.01 + 1.0e-12
    return _gate_result(
        actual=actual,
        expected=expected | {"absolute_tolerance_m2": 0.01},
        passed=passed,
        detail="walkable support areas compared to same-release G25 evidence",
    )


def gate8(
    arrays: dict[str, np.ndarray],
    g25: dict[str, Any],
    stage_d_row: dict[str, Any],
) -> dict[str, Any]:
    pairs = np.asarray(arrays["edge_node_ids"])
    storeys = np.asarray(arrays["node_storey_id"])
    stair_mask = np.asarray(arrays["edge_is_stair"], dtype=np.bool_)
    manifest = manifest_from_artifact(arrays)
    stair = manifest["stair_edge"]
    centers = np.asarray(arrays["node_center_m"], dtype=np.float64)
    components = _connected_component_count(len(storeys), pairs)
    components_without_stair = _connected_component_count(
        len(storeys), pairs[~stair_mask])
    endpoint_errors: dict[str, float] = {}
    for storey, node_key in (
        ("Floor1", "floor1_node_id"),
        ("Floor2", "floor2_node_id"),
    ):
        expected_xy = np.asarray(
            g25["storeys"][storey]["stair_arrival_xyz_m"][:2],
            dtype=np.float64,
        )
        endpoint_errors[storey] = float(np.linalg.norm(
            centers[int(stair[node_key]), :2] - expected_xy))
    rise = (
        float(g25["storeys"]["Floor2"]["stair_arrival_xyz_m"][2])
        - float(g25["storeys"]["Floor1"]["stair_arrival_xyz_m"][2])
    )
    stair_length = float(stair["length_m"])
    actual = {
        "merged_component_count": components,
        "components_after_stair_edge_removal": components_without_stair,
        "storeys_present": sorted(int(value) for value in np.unique(storeys)),
        "stair_edge_count": int(np.count_nonzero(stair_mask)),
        "stair_endpoint_error_m": endpoint_errors,
        "floor_to_floor_rise_m": rise,
        "stair_edge_length_m": stair_length,
        "stair_length_in_rise_to_4x_rise": rise <= stair_length <= 4.0 * rise,
        "edge_node_relation_delta": abs(len(pairs) - (len(storeys) - 1)),
        "stage_d_status": stage_d_row.get("status"),
        "g25_status": g25.get("status"),
    }
    passed = (
        components == 1
        and components_without_stair == 2
        and actual["storeys_present"] == [0, 1]
        and actual["stair_edge_count"] == 1
        and all(error <= 0.50 + 1.0e-12
                for error in endpoint_errors.values())
        and actual["stair_length_in_rise_to_4x_rise"]
        and actual["edge_node_relation_delta"] <= 2
        and actual["stage_d_status"] == "PASS"
        and actual["g25_status"] == "PASS"
    )
    return _gate_result(
        actual=actual,
        expected={
            "merged_component_count": 1,
            "components_after_stair_edge_removal": 2,
            "storeys_present": [0, 1],
            "stair_edge_count": 1,
            "maximum_stair_endpoint_error_m": 0.50,
            "stair_edge_length_range": "[rise, 4*rise]",
            "maximum_edge_node_relation_delta": 2,
            "stage_d_status": "PASS",
            "g25_status": "PASS",
        },
        passed=passed,
        detail=(
            "cross-storey connectivity plus a stair-edge-removal negative "
            "control that must split the graph into exactly two components"
        ),
    )


def graph_path_measures(arrays: dict[str, np.ndarray]) -> dict[str, Any]:
    centers = np.asarray(arrays["node_center_m"], dtype=np.float64)
    distances = np.asarray(
        arrays["node_geodesic_to_goal_m"], dtype=np.float64)
    next_xy = np.asarray(arrays["node_next_hop_xy"], dtype=np.float64)
    pairs = np.asarray(arrays["edge_node_ids"], dtype=np.int64)
    goal = int(np.asarray(arrays["goal_node_id"]).item())
    euclidean = np.linalg.norm(centers - centers[goal], axis=1)
    adjacency: list[list[int]] = [[] for _ in range(len(centers))]
    for first, second in pairs:
        adjacency[int(first)].append(int(second))
        adjacency[int(second)].append(int(first))
    valid = True
    maximum_steps = 0
    for start in range(len(centers)):
        current = start
        visited = {current}
        steps = 0
        while current != goal and steps <= len(centers):
            matches = [
                neighbor for neighbor in adjacency[current]
                if np.array_equal(centers[neighbor, :2], next_xy[current])
                and distances[neighbor] < distances[current]
            ]
            if not matches:
                valid = False
                break
            current = min(matches, key=lambda node: (distances[node], node))
            if current in visited:
                valid = False
                break
            visited.add(current)
            steps += 1
        if current != goal or steps > len(centers):
            valid = False
        maximum_steps = max(maximum_steps, steps)
    return {
        "goal_next_hop_delta_m": float(np.linalg.norm(
            next_xy[goal] - centers[goal, :2])),
        "geodesic_not_shorter_than_euclidean": bool(
            np.all(distances >= euclidean)),
        "minimum_geodesic_minus_euclidean_m": float(
            np.min(distances - euclidean)),
        "all_next_hops_reach_goal_monotonically": valid,
        "maximum_next_hop_steps": maximum_steps,
    }


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
    h1_manifest = require_file(h1_root / "manifest.json", "H1 manifest")
    candidate_review = require_file(
        h1_root / "candidate_review.csv", "candidate review")
    manifest = load_json(h1_manifest)
    decisions = read_decisions(candidate_review)
    approved = sorted(manifest.get("approved_building_bids", []))
    require(len(approved) == EXPECTED and len(set(approved)) == EXPECTED,
            "H1 approved set is not 48 unique BIDs")
    require(set(manifest.get("deferred_building_bids", [])) == DEFERRED,
            "H1 deferred set differs")
    require(set(decisions) == set(approved) | DEFERRED,
            "candidate decision set differs")
    require(manifest.get("graph_token_capacity_evidence_invalidated") is True,
            "capacity evidence is not invalidated")
    require(manifest.get("graph_token_capacity_change_authorized_now") is False,
            "capacity changed before remeasurement")
    require(manifest.get("OBSERVATION_MANIFEST_FROZEN") is False,
            "T7 is frozen")

    stage_d_path = require_file(args.stage_d_summary, "Stage D summary")
    stage_d = load_json(stage_d_path)
    require(stage_d.get("status") == "PASS"
            and stage_d.get("passed_buildings") == EXPECTED
            and stage_d.get("failed_buildings") == 0,
            "Stage D exact-batch status differs")
    stage_d_rows = {row["BID"]: row for row in stage_d.get("rows", [])}
    require(set(stage_d_rows) == set(approved), "Stage D set differs")
    g25_summary_path = require_file(args.g25_summary, "G25 summary")
    g25_summary = load_json(g25_summary_path)
    require(g25_summary.get("status") == "PASS"
            and g25_summary.get("passed_buildings") == EXPECTED
            and g25_summary.get("failed_buildings") == 0,
            "G25 exact-batch status differs")
    require({row["BID"] for row in g25_summary.get("rows", [])} == set(approved),
            "G25 set differs")
    build_summary_path = require_file(args.build_summary, "graph build summary")
    build_summary = load_json(build_summary_path)
    require(build_summary.get("status") == "PASS"
            and build_summary.get("built_building_count") == EXPECTED,
            "graph build summary is not exact PASS")
    require({row["BID"] for row in build_summary.get("rows", [])} == set(approved),
            "graph build set differs")

    blocking: list[dict[str, Any]] = []
    table: dict[str, Any] = {}
    artifacts_found = 0
    for bid in approved:
        path = graph_root / f"{bid}.multistorey_topology_v1.npz"
        row: dict[str, Any] = {"details": {}, "measured": {}}
        arrays: dict[str, np.ndarray] | None = None
        if path.is_file():
            artifacts_found += 1
            try:
                arrays = load_multistorey_artifact(path)
            except Exception as error:
                load_error: Exception = error
        else:
            load_error = FileNotFoundError(f"artifact missing: {path}")
        g25_path = g25_root / f"{bid}.json"
        try:
            g25 = load_json(require_file(g25_path, f"{bid} G25 evidence"))
        except Exception as error:
            g25 = {"_load_error": f"{type(error).__name__}: {error}"}
        gate_functions = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9")
        for name in gate_functions:
            try:
                if arrays is None:
                    raise load_error
                if name == "G1":
                    result = _g1(arrays)
                elif name == "G2":
                    result = _g2(arrays)
                elif name == "G3":
                    result = _g3(path, arrays)
                elif name == "G4":
                    result = gate4(
                        bid, arrays,
                        usdz=require_file(
                            delivery_root / bid / "scene" / "building.usdz",
                            f"{bid} USDZ"),
                        h1_manifest=h1_manifest,
                        candidate_review=candidate_review,
                        decision=decisions[bid],
                        g25_path=require_file(g25_path, f"{bid} G25 evidence"),
                        stage_d_row=stage_d_rows[bid],
                    )
                elif name == "G5":
                    result = gate5(arrays, g25)
                elif name == "G6":
                    result = _g6(arrays)
                elif name == "G7":
                    result = gate7(arrays, g25)
                elif name == "G8":
                    result = gate8(arrays, g25, stage_d_rows[bid])
                else:
                    result = _g9(arrays)
                    path_measures = graph_path_measures(arrays)
                    result["actual"].update(path_measures)
                    result["expected"].update({
                        "goal_next_hop_delta_m": 0.0,
                        "geodesic_not_shorter_than_euclidean": True,
                        "all_next_hops_reach_goal_monotonically": True,
                        "maximum_next_hop_steps": f"<= {len(arrays['node_id'])}",
                    })
                    result["status"] = "PASS" if (
                        result["status"] == "PASS"
                        and path_measures["goal_next_hop_delta_m"] == 0.0
                        and path_measures[
                            "geodesic_not_shorter_than_euclidean"]
                        and path_measures[
                            "all_next_hops_reach_goal_monotonically"]
                        and path_measures["maximum_next_hop_steps"]
                        <= len(arrays["node_id"])
                    ) else "FAIL"
            except Exception as error:
                result = _gate_result(
                    actual={"exception": f"{type(error).__name__}: {error}"},
                    expected="explicit PASS with complete evidence",
                    passed=False,
                    detail="exception is a fail-closed gate failure",
                )
            row[name] = result["status"]
            row["details"][name] = result
            if result["status"] != "PASS":
                blocking.append({
                    "building": bid,
                    "gate": name,
                    "expected": result["expected"],
                    "actual": result["actual"],
                    "detail": result["detail"],
                })
        row["FINAL"] = "PASS" if all(
            row[name] == "PASS" for name in gate_functions) else "FAIL"
        if arrays is not None:
            graph_manifest = manifest_from_artifact(arrays)
            stair = graph_manifest["stair_edge"]
            centers = np.asarray(arrays["node_center_m"])
            stair_mask = np.asarray(arrays["edge_is_stair"], dtype=np.bool_)
            row["measured"] = {
                "floor1_injected_cells": graph_manifest[
                    "storey_band_parameters"]["floor1_injected_cells"],
                "floor1_walkable_area_m2": graph_manifest["storeys"][
                    "Floor1"]["walkable_area_m2"],
                "floor2_walkable_area_m2": graph_manifest["storeys"][
                    "Floor2"]["walkable_area_m2"],
                "nodes": len(arrays["node_id"]),
                "nodes_floor1": int(np.count_nonzero(
                    arrays["node_storey_id"] == 0)),
                "nodes_floor2": int(np.count_nonzero(
                    arrays["node_storey_id"] == 1)),
                "edges": len(arrays["edge_id"]),
                "merged_components": _connected_component_count(
                    len(arrays["node_id"]), arrays["edge_node_ids"]),
                "components_after_stair_edge_removal": (
                    _connected_component_count(
                        len(arrays["node_id"]),
                        arrays["edge_node_ids"][~stair_mask])),
                "stair_edge_count": int(np.count_nonzero(stair_mask)),
                "stair_endpoint_xy_m": {
                    "Floor1": centers[int(stair["floor1_node_id"]), :2].tolist(),
                    "Floor2": centers[int(stair["floor2_node_id"]), :2].tolist(),
                },
                "artifact_sha256": _sha256_file(path),
                **graph_path_measures(arrays),
            }
        table[bid] = row

    passed = sum(row["FINAL"] == "PASS" for row in table.values())
    g10_pass = artifacts_found == passed == EXPECTED
    g10 = _gate_result(
        actual={
            "artifact_building_count": artifacts_found,
            "G1_through_G9_pass_building_count": passed,
        },
        expected={
            "artifact_building_count": EXPECTED,
            "G1_through_G9_pass_building_count": EXPECTED,
        },
        passed=g10_pass,
        detail="all 48 approved buildings must exist and pass G1--G9",
    )
    if not g10_pass:
        blocking.append({
            "building": "ALL",
            "gate": "G10",
            "expected": g10["expected"],
            "actual": g10["actual"],
            "detail": g10["detail"],
        })
    for row in table.values():
        row["G10"] = g10["status"]
    payload = {
        "schema": "GraphArtifactAcceptanceGate48V1",
        "GRAPH_ARTIFACT_AUTHORIZED": not blocking and g10_pass,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_building_count": EXPECTED,
        "gate_revision": GATE_REVISION,
        "gates_run": [f"G{index}" for index in range(1, 11)],
        "blocking_failures": blocking,
        "G10": g10,
        "table": table,
        "artifact_provenance": {
            "architecture_reversal": ARCHITECTURE_REVERSAL,
            "h1_manifest": str(h1_manifest),
            "h1_manifest_sha256": _sha256_file(h1_manifest),
            "candidate_review": str(candidate_review),
            "candidate_review_sha256": _sha256_file(candidate_review),
            "stage_d_summary": str(stage_d_path),
            "stage_d_summary_sha256": _sha256_file(stage_d_path),
            "g25_summary": str(g25_summary_path),
            "g25_summary_sha256": _sha256_file(g25_summary_path),
            "graph_build_summary": str(build_summary_path),
            "graph_build_summary_sha256": _sha256_file(build_summary_path),
        },
        "approved_building_bids": approved,
        "deferred_building_bids": sorted(DEFERRED),
        "deferred_buildings_are_failures": False,
        "graph_token_capacity_evidence_invalidated": True,
        "capacity_change_performed": False,
        "OBSERVATION_MANIFEST_FROZEN": False,
    }
    exclusive_json(args.output, payload)
    print(json.dumps({
        "GRAPH_ARTIFACT_AUTHORIZED": payload["GRAPH_ARTIFACT_AUTHORIZED"],
        "artifact_building_count": artifacts_found,
        "passed_building_count": passed,
        "blocking_failure_count": len(blocking),
        "output": str(args.output.expanduser().resolve()),
    }, indent=2))
    return 0 if payload["GRAPH_ARTIFACT_AUTHORIZED"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
