#!/usr/bin/env python3
"""Build deterministic T0--T3 graphs for the exact approved 10-building batch."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from build_multistorey_graphs48 import (
    ARCHITECTURE_REVERSAL,
    build_one,
    exclusive_json,
    file_sha256,
    load_json,
    require,
    require_file,
)
from protomotions.simulator.base_simulator.free_space_topology import (
    exclusive_publish_bytes,
)
from protomotions.simulator.base_simulator.multistorey_topology import (
    _geodesics,
    canonical_json_bytes,
    deterministic_multistorey_npz_bytes,
    load_multistorey_artifact,
    manifest_from_artifact,
    validate_multistorey_artifact_arrays,
)


EXPECTED = 10


def prune_cycles_to_goal_shortest_path_tree(path: Path) -> dict[str, Any]:
    """Remove redundant cycles without changing any node-to-goal distance.

    The protected G8 contract allows at most two edges beyond a tree.  Large,
    open factory floors can contain more medial-axis cycles than that.  Keep
    one deterministic shortest-path predecessor edge for every non-goal node;
    this retains every node, the sole inter-storey stair edge, embedded floor
    topology, and all shortest-path distances while yielding exactly N-1
    wrapper edges.
    """
    arrays = load_multistorey_artifact(path)
    pairs = np.asarray(arrays["edge_node_ids"], dtype=np.int32)
    lengths = np.asarray(arrays["edge_path_length_m"], dtype=np.float64)
    distances = np.asarray(
        arrays["node_geodesic_to_goal_m"], dtype=np.float64)
    node_count = len(np.asarray(arrays["node_id"]))
    goal = int(np.asarray(arrays["goal_node_id"]).item())
    require(len(pairs) >= node_count - 1, "graph has fewer edges than a tree")

    incident: list[list[tuple[int, int]]] = [[] for _ in range(node_count)]
    for edge_id, pair in enumerate(pairs):
        first, second = int(pair[0]), int(pair[1])
        incident[first].append((edge_id, second))
        incident[second].append((edge_id, first))

    selected: set[int] = set()
    tolerance = 1.0e-9
    for node in range(node_count):
        if node == goal:
            continue
        candidates: list[tuple[float, int, int]] = []
        for edge_id, neighbor in incident[node]:
            residual = abs(
                distances[node] - (lengths[edge_id] + distances[neighbor]))
            if residual <= tolerance:
                candidates.append((residual, neighbor, edge_id))
        require(candidates, f"node {node} lacks a shortest-path predecessor")
        selected.add(min(candidates)[2])
    require(len(selected) == node_count - 1,
            "shortest-path predecessors do not form a tree")
    keep = np.asarray(sorted(selected), dtype=np.int64)

    old_stair = np.flatnonzero(
        np.asarray(arrays["edge_is_stair"], dtype=np.bool_))
    require(len(old_stair) == 1 and int(old_stair[0]) in selected,
            "shortest-path tree does not retain exactly one stair edge")
    edge_id_remap = {int(old): new for new, old in enumerate(keep)}

    old_offsets = np.asarray(arrays["edge_path_offsets"], dtype=np.int64)
    old_paths = np.asarray(arrays["edge_path_xyz_m"], dtype=np.float64)
    kept_paths = [
        np.ascontiguousarray(old_paths[old_offsets[old]:old_offsets[old + 1]])
        for old in keep
    ]
    offsets = np.zeros(len(kept_paths) + 1, dtype=np.int64)
    for index, edge_path in enumerate(kept_paths):
        offsets[index + 1] = offsets[index] + len(edge_path)

    pruned = dict(arrays)
    pruned["edge_id"] = np.arange(len(keep), dtype="<i4")
    pruned["edge_node_ids"] = np.ascontiguousarray(pairs[keep], dtype="<i4")
    pruned["edge_path_offsets"] = np.ascontiguousarray(offsets, dtype="<i8")
    pruned["edge_path_xyz_m"] = np.ascontiguousarray(
        np.concatenate(kept_paths, axis=0), dtype="<f8")
    pruned["edge_path_length_m"] = np.ascontiguousarray(
        lengths[keep], dtype="<f8")
    pruned["edge_is_stair"] = np.ascontiguousarray(
        np.asarray(arrays["edge_is_stair"], dtype=np.bool_)[keep])

    new_distances, new_next_hop = _geodesics(
        node_count,
        pruned["edge_node_ids"],
        pruned["edge_path_length_m"],
        np.asarray(pruned["node_center_m"], dtype=np.float64),
        goal,
    )
    require(np.allclose(new_distances, distances, rtol=0.0, atol=tolerance),
            "cycle pruning changed a node-to-goal geodesic")
    pruned["node_geodesic_to_goal_m"] = np.ascontiguousarray(
        new_distances, dtype="<f8")
    pruned["node_next_hop_xy"] = np.ascontiguousarray(
        new_next_hop, dtype="<f8")

    manifest = manifest_from_artifact(pruned)
    manifest["stair_edge"]["edge_id"] = edge_id_remap[int(old_stair[0])]
    manifest["graph_sparsification"] = {
        "method": "deterministic_goal_shortest_path_tree",
        "reason": "satisfy protected G8 edge-node relation without weakening it",
        "nodes_retained": node_count,
        "edges_before": len(pairs),
        "edges_after": len(keep),
        "removed_cycle_edges": len(pairs) - len(keep),
        "all_node_to_goal_geodesics_unchanged_atol_m": tolerance,
        "embedded_floor_topologies_modified": False,
    }
    pruned["manifest_json_utf8"] = np.frombuffer(
        canonical_json_bytes(manifest), dtype=np.uint8).copy()
    pruned = {key: np.ascontiguousarray(value) for key, value in pruned.items()}
    validate_multistorey_artifact_arrays(pruned)

    encoded = deterministic_multistorey_npz_bytes(pruned)
    temporary = path.with_name(path.name + ".cycle_pruned.tmp")
    require(not temporary.exists(), f"temporary graph already exists: {temporary}")
    exclusive_publish_bytes(temporary, encoded)
    os.replace(temporary, path)
    return {
        "nodes": node_count,
        "edges_before": len(pairs),
        "edges_after": len(keep),
        "removed_cycle_edges": len(pairs) - len(keep),
        "artifact_sha256": file_sha256(path),
    }


def read_decisions(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    require(len(rows) == EXPECTED, "candidate review must have 10 rows")
    result: dict[str, str] = {}
    for row in rows:
        bid = row.get("BID") or row.get("building") or row.get("candidate")
        require(isinstance(bid, str) and bid, "candidate row lacks BID")
        require(bid not in result, f"duplicate candidate decision: {bid}")
        result[bid] = str(row.get("human_decision", ""))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h1-package", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    parser.add_argument("--stage-d-summary", type=Path, required=True)
    parser.add_argument("--g25-root", type=Path, required=True)
    parser.add_argument("--g25-summary", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    h1_root = args.h1_package.expanduser().resolve()
    delivery_root = args.delivery_root.expanduser().resolve()
    g25_root = args.g25_root.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    summary_path = args.summary.expanduser().resolve()
    require(not output_root.exists(), f"output root already exists: {output_root}")
    require(not summary_path.exists(), f"summary already exists: {summary_path}")
    output_root.mkdir(parents=True)

    h1_manifest = require_file(h1_root / "manifest.json", "H1 manifest")
    candidate_review = require_file(h1_root / "candidate_review.csv", "candidate review")
    manifest = load_json(h1_manifest)
    decisions = read_decisions(candidate_review)
    approved = sorted(manifest.get("approved_building_bids", []))
    require(manifest.get("status") == "H1_APPROVED_10_AFTER_SQZ_A40_REVIEW",
            "H1 manifest status differs")
    require(len(approved) == EXPECTED and len(set(approved)) == EXPECTED,
            "H1 approved set is not 10 unique BIDs")
    require(manifest.get("deferred_building_count") == 0
            and manifest.get("deferred_building_bids") == [],
            "H1 still contains deferred buildings")
    require(set(decisions) == set(approved)
            and all(decisions[bid] == "approve" for bid in approved),
            "candidate decisions are not exact 10 approvals")
    require(manifest.get("graph_token_capacity_evidence_invalidated") is True,
            "capacity invalidation flag is absent")
    require(manifest.get("graph_token_capacity_change_authorized_now") is False,
            "capacity changed before remeasurement")
    require(manifest.get("OBSERVATION_MANIFEST_FROZEN") is False,
            "T7 is already frozen")

    stage_d_path = require_file(args.stage_d_summary, "Stage D summary")
    stage_d = load_json(stage_d_path)
    require(stage_d.get("status") == "PASS"
            and stage_d.get("building_count") == EXPECTED
            and stage_d.get("passed_buildings") == EXPECTED
            and stage_d.get("failed_buildings") == 0,
            "Stage D is not exact 10/10 PASS")
    stage_d_rows = {row["BID"]: row for row in stage_d.get("rows", [])}
    require(set(stage_d_rows) == set(approved), "Stage D set differs")

    g25_summary_path = require_file(args.g25_summary, "G25 summary")
    g25_summary = load_json(g25_summary_path)
    require(g25_summary.get("status") in {"PASS", "FAIL"}
            and g25_summary.get("building_count") == EXPECTED
            and isinstance(g25_summary.get("passed_buildings"), int)
            and isinstance(g25_summary.get("failed_buildings"), int)
            and g25_summary["passed_buildings"] + g25_summary["failed_buildings"] == EXPECTED,
            "G25 terminal coverage differs")
    require({row["BID"] for row in g25_summary.get("rows", [])} == set(approved),
            "G25 set differs")

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    scratch_root = args.scratch_root.expanduser().resolve() if args.scratch_root else None
    for index, bid in enumerate(approved, 1):
        try:
            row = build_one(
                bid, delivery_root=delivery_root, g25_root=g25_root,
                output_root=output_root, scratch_root=scratch_root,
                h1_manifest=h1_manifest, candidate_review=candidate_review,
                stage_d_row=stage_d_rows[bid],
            )
            pruning = prune_cycles_to_goal_shortest_path_tree(
                Path(row["output"]))
            row.update({
                "artifact_sha256": pruning["artifact_sha256"],
                "edges": pruning["edges_after"],
                "graph_sparsification": pruning,
            })
            rows.append(row)
            if row["status"] != "PASS":
                failures.append({"BID": bid, "gate": "G9_CORRIDOR",
                                 "error": "frozen G9 corridor gate failed",
                                 "measured": row["corridor_cross_check"]})
            print(f"graph {index}/{EXPECTED} {bid} {row['status']} nodes={row['nodes']}", flush=True)
        except Exception as error:
            failures.append({"BID": bid, "gate": "BUILD_OR_BINDING",
                             "error": f"{type(error).__name__}: {error}"})
            print(f"graph {index}/{EXPECTED} {bid} FAIL: {error}", flush=True)
    passed = sum(row["status"] == "PASS" for row in rows)
    terminal = len(rows) + sum(failure["gate"] == "BUILD_OR_BINDING" for failure in failures)
    coverage_complete = terminal == EXPECTED
    capacity_complete = len(rows) == EXPECTED
    payload = {
        "schema": "MultistoreyGraphBuild10V1",
        "status": "PASS" if len(rows) == EXPECTED and passed == EXPECTED else "FAIL",
        "coverage_status": "PASS" if coverage_complete else "FAIL",
        "expected_building_count": EXPECTED,
        "built_building_count": len(rows), "terminal_building_count": terminal,
        "passed_building_count": passed,
        "failed_building_count": EXPECTED - passed if coverage_complete else len(failures),
        "capacity_measured_building_count": len(rows),
        "approved_building_bids": approved, "deferred_building_bids": [],
        "h1_manifest_sha256": file_sha256(h1_manifest),
        "candidate_review_sha256": file_sha256(candidate_review),
        "stage_d_summary": str(stage_d_path), "stage_d_summary_sha256": file_sha256(stage_d_path),
        "g25_summary": str(g25_summary_path), "g25_summary_sha256": file_sha256(g25_summary_path),
        "architecture_reversal": ARCHITECTURE_REVERSAL,
        "graph_token_capacity_evidence_invalidated": True,
        "capacity_change_performed": False, "OBSERVATION_MANIFEST_FROZEN": False,
        "rows": rows, "blocking_failures": failures,
    }
    exclusive_json(summary_path, payload)
    print(json.dumps({"status": payload["status"], "built_building_count": len(rows),
                      "blocking_failure_count": len(failures),
                      "summary": str(summary_path)}, indent=2))
    return 0 if coverage_complete and capacity_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
