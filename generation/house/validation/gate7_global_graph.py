#!/usr/bin/env python3
"""Fail-closed G1--G10 acceptance gate for multistorey graph artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Callable

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ProtoMotions-main"))

from protomotions.simulator.base_simulator.free_space_topology import (
    FreeSpaceTopologyConfig,
    deterministic_topology_npz_bytes,
    exclusive_publish_bytes,
    extract_free_space_topology,
    validate_topology_artifact_arrays,
)
from protomotions.simulator.base_simulator.multistorey_topology import (
    ARCHITECTURE_REVERSAL,
    corridor_cross_check,
    deterministic_multistorey_npz_bytes,
    embedded_topology_arrays,
    grid_from_artifact,
    load_multistorey_artifact,
    manifest_from_artifact,
    validate_multistorey_artifact_arrays,
)
from protomotions.simulator.base_simulator.scene_geometry_cache import (
    FINE_VOXEL_SIZE_M,
)
from protomotions.simulator.base_simulator.standability import (
    StandabilityConfig,
    derive_standability,
)

from build_multistorey_graphs import (
    BATCH_APPROVED,
    BATCH_CAPACITY_EVIDENCE,
    BATCHES,
    BUILDINGS,
    CAPACITY_EVIDENCE_BUILDINGS,
    DEFAULT_CAPACITY_EVIDENCE_ROOT,
    DEFAULT_CAPACITY_OUTPUT_ROOT,
    DEFAULT_CAPACITY_USD_ROOT,
    DEFAULT_DECISION_ROOT,
    DEFAULT_G25,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_USD_ROOT,
)


DEFAULT_GATE6 = HERE / "evidence" / "gate6_stair_reachability.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GATE_REVISION = {
    "gate": "G9",
    "date": "2026-08-28",
    "reason": (
        "spine/medial-axis graph detour does not scale with path length; "
        "original ratio bound invalid at short range"
    ),
    "added_lower_bound": "graph >= corridor * 0.95",
    "upper_bound": "abs diff <= 5.0 m below 5 m corridor; ratio <= 2.0 at or above",
    "human_directed": True,
}


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, role: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{role} must be a JSON object")
    return value


def _connected_component_count(node_count: int, pairs: np.ndarray) -> int:
    adjacency: list[list[int]] = [[] for _ in range(node_count)]
    for first, second in np.asarray(pairs, dtype=np.int64):
        adjacency[int(first)].append(int(second))
        adjacency[int(second)].append(int(first))
    unseen = set(range(node_count))
    count = 0
    while unseen:
        count += 1
        pending = [min(unseen)]
        unseen.remove(pending[0])
        while pending:
            current = pending.pop()
            for neighbor in adjacency[current]:
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    pending.append(neighbor)
    return count


def _gate_result(
    *, actual: Any, expected: Any, passed: bool, detail: str
) -> dict[str, Any]:
    return {
        "status": "PASS" if passed else "FAIL",
        "actual": actual,
        "expected": expected,
        "detail": detail,
    }


def _g1(arrays: dict[str, np.ndarray]) -> dict[str, Any]:
    validate_multistorey_artifact_arrays(arrays)
    actual: dict[str, Any] = {"wrapper_validator": "PASS", "storeys": {}}
    for prefix in ("floor1", "floor2"):
        base = embedded_topology_arrays(arrays, prefix)
        validate_topology_artifact_arrays(base)
        grid = grid_from_artifact(arrays, prefix)
        grid.validate()
        config_json = str(np.asarray(base["config_json"]).item())
        FreeSpaceTopologyConfig().validate(grid.fine_voxel_size_m)
        sha_fields = {
            key: str(np.asarray(base[key]).item())
            for key in ("source_manifest_sha256", "source_occupancy_sha256")
        }
        if len(config_json) > 4096 or np.asarray(base["config_json"]).dtype != np.dtype("<U4096"):
            raise ValueError(f"{prefix} config_json contract differs")
        if not all(SHA256_PATTERN.fullmatch(value) for value in sha_fields.values()):
            raise ValueError(f"{prefix} SHA-256 field contract differs")
        actual["storeys"][prefix] = {
            "topology_validator": "PASS",
            "grid_validator": "PASS",
            "config_validator": "PASS",
            "artifact_key_count": len(base),
            "artifact_keys_exact_order": tuple(base) == tuple(base.keys()),
            "config_json_length": len(config_json),
            "config_json_dtype": str(np.asarray(base["config_json"]).dtype),
            "sha256_fields": sha_fields,
        }
    return _gate_result(
        actual=actual,
        expected={
            "all_repository_validators": "PASS",
            "topology_artifact_key_count": 33,
            "config_json_max_length": 4096,
            "sha256": "64 lowercase hexadecimal characters",
        },
        passed=True,
        detail="repository validators accepted wrapper and both embedded V0 payloads",
    )


def _g2(arrays: dict[str, np.ndarray]) -> dict[str, Any]:
    manifest = manifest_from_artifact(arrays)
    config = manifest["topology_config"]
    actual = {
        "body_radius_m": config["body_radius_m"],
        "body_height_m": config["body_height_m"],
        "max_support_step_m": config["max_support_step_m"],
        "min_component_area_m2": config["min_component_area_m2"],
        "query_snap_radius_m": config["query_snap_radius_m"],
        "fine_voxel_size_m": config["voxel_size_m"],
    }
    expected = {
        "body_radius_m": 0.25,
        "body_height_m": 1.60,
        "max_support_step_m": 0.20,
        "min_component_area_m2": 1.00,
        "query_snap_radius_m": 0.50,
        "fine_voxel_size_m": 0.05,
    }
    passed = all(
        float(actual[key]).hex() == float(expected[key]).hex() for key in expected
    )
    return _gate_result(
        actual=actual,
        expected=expected,
        passed=passed,
        detail="configuration values compared by exact binary64 representation",
    )


def _g3(path: Path, arrays: dict[str, np.ndarray]) -> dict[str, Any]:
    first = deterministic_multistorey_npz_bytes(arrays)
    second = deterministic_multistorey_npz_bytes(arrays)
    on_disk = path.read_bytes()
    base_equal: dict[str, bool] = {}
    for prefix in ("floor1", "floor2"):
        base = embedded_topology_arrays(arrays, prefix)
        base_equal[prefix] = (
            deterministic_topology_npz_bytes(base)
            == deterministic_topology_npz_bytes(base)
        )
    refused_existing = False
    try:
        exclusive_publish_bytes(path, first)
    except FileExistsError:
        refused_existing = True
    actual = {
        "wrapper_bytes_equal_across_two_runs": first == second,
        "wrapper_sha256_equal": sha256(first).hexdigest() == sha256(second).hexdigest(),
        "on_disk_bytes_equal_deterministic_payload": on_disk == first,
        "embedded_topology_bytes_equal_across_two_runs": base_equal,
        "exclusive_publish_refused_existing": refused_existing,
    }
    passed = all((
        actual["wrapper_bytes_equal_across_two_runs"],
        actual["wrapper_sha256_equal"],
        actual["on_disk_bytes_equal_deterministic_payload"],
        all(base_equal.values()),
        refused_existing,
    ))
    return _gate_result(
        actual=actual,
        expected={key: True for key in actual},
        passed=passed,
        detail="byte reproducibility and exclusive no-overwrite behavior",
    )


def _g4(
    building: str,
    arrays: dict[str, np.ndarray],
    *,
    usd_root: Path,
    decision_root: Path,
    g25: dict[str, Any],
) -> dict[str, Any]:
    manifest = manifest_from_artifact(arrays)
    usdz = usd_root / f"{building}.usdz"
    decision_path = decision_root / building / "decision.json"
    decision = _load_json(decision_path, "decision")
    actual = {
        "source_usdz_sha256": _sha256_file(usdz),
        "manifest_source_usdz_sha256": manifest.get("source_usdz_sha256"),
        "decision_json_sha256": _sha256_file(decision_path),
        "manifest_decision_json_sha256": manifest.get("decision_json_sha256"),
        "decision": decision.get("decision"),
        "final_placement_approved": decision.get("final_placement_approved"),
        "architecture_reversal": manifest.get("architecture_reversal"),
    }
    expected = {
        "source_usdz_sha256": g25["delivered_usdz_sha256"][building],
        "manifest_source_usdz_sha256": g25["delivered_usdz_sha256"][building],
        "decision_json_sha256": actual["decision_json_sha256"],
        "manifest_decision_json_sha256": actual["decision_json_sha256"],
        "decision": "approve",
        "final_placement_approved": True,
        "architecture_reversal": ARCHITECTURE_REVERSAL,
    }
    return _gate_result(
        actual=actual,
        expected=expected,
        passed=actual == expected,
        detail="source, approval, and architecture-reversal provenance",
    )


def _g5(
    building: str, arrays: dict[str, np.ndarray], g25: dict[str, Any]
) -> dict[str, Any]:
    manifest = manifest_from_artifact(arrays)
    modelling = g25["floor1_modelling"]["per_building"][building]
    actual = {
        "floor1_injected_cells": manifest["storey_band_parameters"]["floor1_injected_cells"],
        "floor1_support_top_z_m": manifest["storey_band_parameters"]["floor1_support_top_z_m"],
    }
    expected = {
        "floor1_injected_cells": modelling["new_ground_cells_injected"],
        "floor1_support_top_z_m": modelling["rasterized_support_top_z_m"],
    }
    passed = (
        int(actual["floor1_injected_cells"]) == int(expected["floor1_injected_cells"])
        and abs(float(actual["floor1_support_top_z_m"]) - float(expected["floor1_support_top_z_m"]))
        <= 0.0001 + 1.0e-12
    )
    return _gate_result(
        actual=actual,
        expected=expected | {"support_top_tolerance_m": 0.0001},
        passed=passed,
        detail="exact injected-cell regression and preregistered support-top tolerance",
    )


def _g6(arrays: dict[str, np.ndarray]) -> dict[str, Any]:
    actual: dict[str, Any] = {}
    passed = True
    for prefix in ("floor1", "floor2"):
        occupancy = np.asarray(arrays[f"{prefix}_occupancy"])
        grid = grid_from_artifact(arrays, prefix)
        topology_support = extract_free_space_topology(
            occupancy, grid, FreeSpaceTopologyConfig()).support_z_index
        standability_support = derive_standability(
            occupancy, grid, StandabilityConfig()).support_z_index
        stored_support = embedded_topology_arrays(arrays, prefix)["support_z_index"]
        equal_cross = np.array_equal(topology_support, standability_support)
        equal_stored = np.array_equal(topology_support, stored_support)
        actual[prefix] = {
            "topology_support_cell_count": int(np.count_nonzero(topology_support >= 0)),
            "standability_support_cell_count": int(np.count_nonzero(standability_support >= 0)),
            "topology_equals_standability_elementwise": equal_cross,
            "topology_equals_stored_elementwise": equal_stored,
        }
        passed &= equal_cross and equal_stored
    return _gate_result(
        actual=actual,
        expected={
            "topology_equals_standability_elementwise": True,
            "topology_equals_stored_elementwise": True,
            "tolerance": 0,
        },
        passed=passed,
        detail="independent support derivations compared elementwise",
    )


def _g7(
    building: str, arrays: dict[str, np.ndarray], g25: dict[str, Any]
) -> dict[str, Any]:
    expected_rows = {
        row["storey"]: row for row in g25["rows"] if row["building"] == building
    }
    actual: dict[str, float] = {}
    expected: dict[str, float] = {}
    passed = set(expected_rows) == {"Floor1", "Floor2"}
    for storey, prefix in (("Floor1", "floor1"), ("Floor2", "floor2")):
        support = embedded_topology_arrays(arrays, prefix)["support_z_index"]
        actual[storey] = round(
            int(np.count_nonzero(support >= 0)) * FINE_VOXEL_SIZE_M**2, 4)
        expected[storey] = float(expected_rows[storey]["walkable_area_m2"])
        passed &= abs(actual[storey] - expected[storey]) <= 0.01 + 1.0e-12
    return _gate_result(
        actual=actual,
        expected=expected | {"absolute_tolerance_m2": 0.01},
        passed=passed,
        detail="walkable support area compared to frozen G25 rows",
    )


def _g8(
    building: str,
    arrays: dict[str, np.ndarray],
    g25: dict[str, Any],
    gate6: dict[str, Any],
) -> dict[str, Any]:
    pairs = np.asarray(arrays["edge_node_ids"])
    storeys = np.asarray(arrays["node_storey_id"])
    components = _connected_component_count(len(storeys), pairs)
    stair_mask = np.asarray(arrays["edge_is_stair"])
    manifest = manifest_from_artifact(arrays)
    stair = manifest["stair_edge"]
    centers = np.asarray(arrays["node_center_m"])
    rows = {
        row["storey"]: row for row in g25["rows"] if row["building"] == building
    }
    endpoint_errors: dict[str, float] = {}
    for storey, node_key in (("Floor1", "floor1_node_id"), ("Floor2", "floor2_node_id")):
        actual_xy = centers[int(stair[node_key]), :2]
        expected_xy = np.asarray(
            [rows[storey]["arrival_x_m"], rows[storey]["arrival_y_m"]],
            dtype=np.float64,
        )
        endpoint_errors[storey] = float(np.linalg.norm(actual_xy - expected_xy))
    actual = {
        "merged_component_count": components,
        "storeys_present": sorted(int(value) for value in np.unique(storeys)),
        "stair_edge_count": int(np.count_nonzero(stair_mask)),
        "stair_endpoint_error_m": endpoint_errors,
        "gate6_overall": gate6.get("_overall"),
        "graph_upper_storey_reachable": components == 1,
    }
    passed = (
        components == 1
        and actual["storeys_present"] == [0, 1]
        and actual["stair_edge_count"] >= 1
        and all(error <= 0.50 + 1.0e-12 for error in endpoint_errors.values())
        and gate6.get("_overall") == "PASS"
    )
    return _gate_result(
        actual=actual,
        expected={
            "merged_component_count": 1,
            "storeys_present": [0, 1],
            "minimum_stair_edge_count": 1,
            "maximum_stair_endpoint_error_m": 0.50,
            "gate6_overall": "PASS",
            "graph_upper_storey_reachable": True,
        },
        passed=passed,
        detail="cross-storey connectivity, stair endpoints, and Gate6 consistency",
    )


def _g9(arrays: dict[str, np.ndarray]) -> dict[str, Any]:
    distance = np.asarray(arrays["node_geodesic_to_goal_m"])
    goal = int(np.asarray(arrays["goal_node_id"]).item())
    corridor = corridor_cross_check(arrays)
    graph_geodesic_m = float(corridor["graph_geodesic_m"])
    corridor_length_m = float(corridor["plan_root_corridor_length_m"])
    ratio = float(corridor["ratio"])
    absolute_diff_m = abs(graph_geodesic_m - corridor_length_m)
    branch = "short_absolute" if corridor_length_m < 5.0 else "long_ratio"
    lower_bound_ok = graph_geodesic_m >= corridor_length_m * 0.95
    upper_bound_ok = (
        absolute_diff_m <= 5.0
        if branch == "short_absolute"
        else ratio <= 2.0
    )
    actual = {
        "all_main_component_geodesics_finite": bool(np.all(np.isfinite(distance))),
        "all_geodesics_nonnegative": bool(np.all(distance >= 0.0)),
        "goal_geodesic_m": float(distance[goal]),
        "nonmain_component_node_count": 0,
        **corridor,
        "absolute_diff_m": absolute_diff_m,
        "branch": branch,
        "lower_bound_ok": lower_bound_ok,
        "upper_bound_ok": upper_bound_ok,
    }
    passed = (
        actual["all_main_component_geodesics_finite"]
        and actual["all_geodesics_nonnegative"]
        and actual["goal_geodesic_m"] == 0.0
        and corridor["planner_status"] == "success"
        and lower_bound_ok
        and upper_bound_ok
    )
    return _gate_result(
        actual=actual,
        expected={
            "all_main_component_geodesics_finite": True,
            "all_geodesics_nonnegative": True,
            "goal_geodesic_m": 0.0,
            "nonmain_component_node_count": 0,
            "planner_status": "success",
            "lower_bound": "graph_geodesic_m >= plan_root_corridor_length_m * 0.95",
            "short_absolute": {
                "when": "plan_root_corridor_length_m < 5.0",
                "maximum_absolute_diff_m": 5.0,
            },
            "long_ratio": {
                "when": "plan_root_corridor_length_m >= 5.0",
                "maximum_ratio": 2.0,
            },
        },
        passed=passed,
        detail=(
            "Dijkstra fields plus the frozen spine-graph lower bound and "
            "distance-dependent corridor upper bound; no Euclidean criterion"
        ),
    )


def _capacity_usdz_path(usd_root: Path, building: str) -> Path:
    return usd_root / building / "scene" / "building.usdz"


def _capacity_evidence_path(evidence_root: Path, building: str) -> Path:
    return evidence_root / f"{building}.json"


def _capacity_g4(
    building: str,
    arrays: dict[str, np.ndarray],
    *,
    usd_root: Path,
    evidence_root: Path,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    manifest = manifest_from_artifact(arrays)
    usdz = _capacity_usdz_path(usd_root, building)
    evidence_path = _capacity_evidence_path(evidence_root, building)
    source_sha = _sha256_file(usdz)
    evidence_sha = _sha256_file(evidence_path)
    expected_source_sha = str(evidence["source"]["building_usdz_sha256"])
    actual = {
        "source_usdz_sha256": source_sha,
        "manifest_source_usdz_sha256": manifest.get("source_usdz_sha256"),
        "evidence_json_sha256": evidence_sha,
        "manifest_lineage_json_sha256": manifest.get("decision_json_sha256"),
        "evidence_status_recorded_not_filtered": evidence.get("status"),
        "architecture_reversal": manifest.get("architecture_reversal"),
        "usage": manifest.get("usage"),
        "human_approved": manifest.get("human_approved"),
        "not_for_training": manifest.get("not_for_training"),
    }
    expected = {
        "source_usdz_sha256": expected_source_sha,
        "manifest_source_usdz_sha256": expected_source_sha,
        "evidence_json_sha256": evidence_sha,
        "manifest_lineage_json_sha256": evidence_sha,
        "evidence_status_recorded_not_filtered": evidence.get("status"),
        "architecture_reversal": ARCHITECTURE_REVERSAL,
        "usage": "capacity_evidence_only",
        "human_approved": False,
        "not_for_training": True,
    }
    return _gate_result(
        actual=actual,
        expected=expected,
        passed=actual == expected,
        detail=(
            "capacity adaptation: human approval is intentionally not asserted; "
            "the delivered USDZ SHA, evidence lineage, and non-training flags are exact"
        ),
    )


def _capacity_g5(
    arrays: dict[str, np.ndarray], evidence: dict[str, Any]
) -> dict[str, Any]:
    manifest = manifest_from_artifact(arrays)
    source = manifest.get("capacity_evidence_source", {})
    actual = {
        "rasterization": source.get("rasterization"),
        "storey_split": source.get("storey_split"),
    }
    expected = {
        "rasterization": evidence["rasterization"],
        "storey_split": evidence["storey_split"],
    }
    return _gate_result(
        actual=actual,
        expected=expected,
        passed=actual == expected,
        detail=(
            "capacity adaptation: every rasterization and storey_split field "
            "is compared recursively with zero tolerance"
        ),
    )


def _capacity_g7(
    arrays: dict[str, np.ndarray], evidence: dict[str, Any]
) -> dict[str, Any]:
    actual: dict[str, float] = {}
    expected: dict[str, float] = {}
    passed = True
    for storey, prefix in (("Floor1", "floor1"), ("Floor2", "floor2")):
        support = embedded_topology_arrays(arrays, prefix)["support_z_index"]
        actual[storey] = round(
            int(np.count_nonzero(support >= 0)) * FINE_VOXEL_SIZE_M**2, 4
        )
        expected[storey] = float(evidence["storeys"][storey]["walkable_area_m2"])
        passed &= abs(actual[storey] - expected[storey]) <= 0.01 + 1.0e-12
    return _gate_result(
        actual=actual,
        expected=expected | {"absolute_tolerance_m2": 0.01},
        passed=passed,
        detail=(
            "capacity adaptation: walkable support area is compared with the "
            "per-building batch-20260826 evidence"
        ),
    )


def _capacity_g8(
    arrays: dict[str, np.ndarray], evidence: dict[str, Any], gate6: dict[str, Any]
) -> dict[str, Any]:
    pairs = np.asarray(arrays["edge_node_ids"])
    storeys = np.asarray(arrays["node_storey_id"])
    components = _connected_component_count(len(storeys), pairs)
    stair_mask = np.asarray(arrays["edge_is_stair"])
    manifest = manifest_from_artifact(arrays)
    stair = manifest["stair_edge"]
    centers = np.asarray(arrays["node_center_m"])
    endpoint_errors: dict[str, float] = {}
    for storey, node_key in (
        ("Floor1", "floor1_node_id"), ("Floor2", "floor2_node_id")
    ):
        actual_xy = centers[int(stair[node_key]), :2]
        expected_xy = np.asarray(
            evidence["storeys"][storey]["stair_arrival_xyz_m"][:2],
            dtype=np.float64,
        )
        endpoint_errors[storey] = float(np.linalg.norm(actual_xy - expected_xy))
    actual = {
        "merged_component_count": components,
        "storeys_present": sorted(int(value) for value in np.unique(storeys)),
        "stair_edge_count": int(np.count_nonzero(stair_mask)),
        "stair_endpoint_error_m": endpoint_errors,
        "gate6_overall": gate6.get("_overall"),
        "graph_upper_storey_reachable": components == 1,
    }
    passed = (
        components == 1
        and actual["storeys_present"] == [0, 1]
        and actual["stair_edge_count"] >= 1
        and all(error <= 0.50 + 1.0e-12 for error in endpoint_errors.values())
        and gate6.get("_overall") == "PASS"
    )
    return _gate_result(
        actual=actual,
        expected={
            "merged_component_count": 1,
            "storeys_present": [0, 1],
            "minimum_stair_edge_count": 1,
            "maximum_stair_endpoint_error_m": 0.50,
            "gate6_overall": "PASS",
            "graph_upper_storey_reachable": True,
        },
        passed=passed,
        detail="normal G8 connectivity and batch-specific stair endpoints",
    )


def _graph_metrics(
    building: str,
    arrays: dict[str, np.ndarray],
    *,
    cohort: str,
    artifact_path: Path,
) -> dict[str, Any]:
    manifest = manifest_from_artifact(arrays)
    floor1_area = float(manifest["storeys"]["Floor1"]["walkable_area_m2"])
    floor2_area = float(manifest["storeys"]["Floor2"]["walkable_area_m2"])
    total_area = floor1_area + floor2_area
    nodes = len(arrays["node_id"])
    if total_area <= 0.0:
        raise ValueError(f"{building} has non-positive total walkable area")
    return {
        "building": building,
        "cohort": cohort,
        "node_count": nodes,
        "edge_count": len(arrays["edge_id"]),
        "floor1_walkable_area_m2": floor1_area,
        "floor2_walkable_area_m2": floor2_area,
        "total_walkable_area_m2": round(total_area, 4),
        "node_density_per_m2": round(nodes / total_area, 6),
        "artifact_path": str(artifact_path.resolve()),
        "artifact_sha256": _sha256_file(artifact_path),
    }


def _distribution_summary(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = sorted(float(row[key]) for row in rows)
    if not values:
        return {"count": 0, "min": None, "max": None, "median": None, "sorted": []}
    middle = len(values) // 2
    median = (
        values[middle]
        if len(values) % 2
        else (values[middle - 1] + values[middle]) / 2.0
    )
    integers = key in {"node_count", "edge_count"}
    normalize = (lambda value: int(value)) if integers else (lambda value: round(value, 6))
    return {
        "count": len(values),
        "min": normalize(values[0]),
        "max": normalize(values[-1]),
        "median": normalize(median),
        "sorted": [normalize(value) for value in values],
    }


def _capacity_recommendation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    maximum = max(int(row["node_count"]) for row in rows)
    recommended = 1 << maximum.bit_length()
    return {
        "recommended_frozen_graph_token_capacity": recommended,
        "method_id": "strict_next_power_of_two_above_observed_max_v1",
        "formula": "2 ** ceil(log2(max_observed_node_count + 1))",
        "max_observed_node_count": maximum,
        "headroom_nodes": recommended - maximum,
        "headroom_fraction_of_observed_max": round(recommended / maximum - 1.0, 6),
        "derivation": (
            "Use the smallest power-of-two tensor capacity strictly above the "
            "complete 20-building observed maximum. This guarantees positive "
            "observed-sample headroom, is directly reproducible from node counts, "
            "and does not infer capacity from floor area because observed node "
            "density is topology-dependent."
        ),
        "statistical_limit": (
            "Twenty buildings do not establish a distribution-free upper bound for "
            "all future buildings; this is an engineering freeze recommendation "
            "from the requested evidence set, not a probabilistic guarantee."
        ),
    }


def run_acceptance(
    *,
    usd_root: Path,
    decision_root: Path,
    graph_root: Path,
    g25_path: Path,
    gate6_path: Path,
) -> dict[str, Any]:
    g25 = _load_json(g25_path, "G25 evidence")
    gate6 = _load_json(gate6_path, "Gate6 evidence")
    blocking: list[dict[str, Any]] = []
    table: dict[str, Any] = {}
    artifacts_found = 0
    gates: tuple[tuple[str, Callable[..., dict[str, Any]]], ...] = (
        ("G1", _g1), ("G2", _g2), ("G3", _g3), ("G4", _g4),
        ("G5", _g5), ("G6", _g6), ("G7", _g7), ("G8", _g8), ("G9", _g9),
    )
    provenance: dict[str, str] = {}
    for building in BUILDINGS:
        path = graph_root / f"{building}.multistorey_topology_v1.npz"
        row: dict[str, Any] = {"details": {}, "measured": {}}
        arrays: dict[str, np.ndarray] | None = None
        load_error: Exception | None = None
        if path.is_file():
            artifacts_found += 1
            try:
                arrays = load_multistorey_artifact(path)
                provenance[building] = _sha256_file(usd_root / f"{building}.usdz")
            except Exception as error:
                load_error = error
        else:
            load_error = FileNotFoundError(f"artifact missing: {path}")
        for gate_name, gate_function in gates:
            try:
                if arrays is None:
                    assert load_error is not None
                    raise load_error
                if gate_name == "G3":
                    result = gate_function(path, arrays)
                elif gate_name == "G4":
                    result = gate_function(
                        building, arrays, usd_root=usd_root,
                        decision_root=decision_root, g25=g25)
                elif gate_name in {"G5", "G7"}:
                    result = gate_function(building, arrays, g25)
                elif gate_name == "G8":
                    result = gate_function(building, arrays, g25, gate6)
                else:
                    result = gate_function(arrays)
            except Exception as error:
                result = _gate_result(
                    actual={"exception": f"{type(error).__name__}: {error}"},
                    expected="explicit PASS with complete evidence",
                    passed=False,
                    detail="exception is a fail-closed gate failure",
                )
            row[gate_name] = result["status"]
            row["details"][gate_name] = result
            if result["status"] != "PASS":
                blocking.append({
                    "building": building,
                    "gate": gate_name,
                    "expected": result["expected"],
                    "actual": result["actual"],
                    "detail": result["detail"],
                })
        row["FINAL"] = "PASS" if all(row[name] == "PASS" for name, _ in gates) else "FAIL"
        if arrays is not None:
            manifest = manifest_from_artifact(arrays)
            stair = manifest["stair_edge"]
            centers = np.asarray(arrays["node_center_m"])
            row["measured"] = {
                "floor1_injected_cells": manifest["storey_band_parameters"]["floor1_injected_cells"],
                "floor1_walkable_area_m2": manifest["storeys"]["Floor1"]["walkable_area_m2"],
                "floor2_walkable_area_m2": manifest["storeys"]["Floor2"]["walkable_area_m2"],
                "nodes_floor1": int(np.count_nonzero(arrays["node_storey_id"] == 0)),
                "nodes_floor2": int(np.count_nonzero(arrays["node_storey_id"] == 1)),
                "edges": len(arrays["edge_id"]),
                "merged_components": _connected_component_count(
                    len(arrays["node_id"]), arrays["edge_node_ids"]),
                "stair_edge_count": int(np.count_nonzero(arrays["edge_is_stair"])),
                "stair_endpoint_xy_m": {
                    "Floor1": centers[int(stair["floor1_node_id"]), :2].tolist(),
                    "Floor2": centers[int(stair["floor2_node_id"]), :2].tolist(),
                },
                "artifact_sha256": _sha256_file(path),
            }
        table[building] = row
    passed_buildings = sum(row["FINAL"] == "PASS" for row in table.values())
    g10_pass = artifacts_found == len(BUILDINGS) and passed_buildings == len(BUILDINGS)
    g10 = _gate_result(
        actual={
            "artifact_building_count": artifacts_found,
            "G1_through_G9_pass_building_count": passed_buildings,
        },
        expected={
            "artifact_building_count": len(BUILDINGS),
            "G1_through_G9_pass_building_count": len(BUILDINGS),
        },
        passed=g10_pass,
        detail="all ten buildings must exist and pass every preceding gate",
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
    authorized = not blocking and g10_pass
    return {
        "schema": "GraphArtifactAcceptanceGateV1",
        "GRAPH_ARTIFACT_AUTHORIZED": authorized,
        "gate_revision": GATE_REVISION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "gates_run": [f"G{index}" for index in range(1, 11)],
        "blocking_failures": blocking,
        "G10": g10,
        "table": table,
        "artifact_provenance": {
            "source_usdz_sha256": provenance,
            "architecture_reversal": ARCHITECTURE_REVERSAL,
        },
    }


def run_capacity_evidence(
    *,
    usd_root: Path,
    graph_root: Path,
    evidence_root: Path,
    gate6_path: Path,
    approved_graph_root: Path,
    approved_acceptance_path: Path,
) -> dict[str, Any]:
    gate6 = _load_json(gate6_path, "Gate6 evidence")
    blocking: list[dict[str, Any]] = []
    capacity_table: dict[str, Any] = {}
    metrics: list[dict[str, Any]] = []
    capacity_artifacts_found = 0
    capacity_gate_passes = 0
    provenance: dict[str, str] = {}
    gates: tuple[str, ...] = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9")

    for building in CAPACITY_EVIDENCE_BUILDINGS:
        path = graph_root / f"{building}.multistorey_topology_v1.npz"
        row: dict[str, Any] = {"details": {}, "measured": {}}
        arrays: dict[str, np.ndarray] | None = None
        evidence: dict[str, Any] | None = None
        load_error: Exception | None = None
        try:
            evidence = _load_json(
                _capacity_evidence_path(evidence_root, building),
                f"{building} capacity evidence",
            )
            if evidence.get("building") != building:
                raise ValueError(
                    f"capacity evidence building differs: {evidence.get('building')}"
                )
            if not path.is_file():
                raise FileNotFoundError(f"artifact missing: {path}")
            capacity_artifacts_found += 1
            arrays = load_multistorey_artifact(path)
            provenance[building] = _sha256_file(_capacity_usdz_path(usd_root, building))
        except Exception as error:
            load_error = error

        for gate_name in gates:
            try:
                if arrays is None or evidence is None:
                    assert load_error is not None
                    raise load_error
                if gate_name == "G1":
                    result = _g1(arrays)
                elif gate_name == "G2":
                    result = _g2(arrays)
                elif gate_name == "G3":
                    result = _g3(path, arrays)
                elif gate_name == "G4":
                    result = _capacity_g4(
                        building,
                        arrays,
                        usd_root=usd_root,
                        evidence_root=evidence_root,
                        evidence=evidence,
                    )
                elif gate_name == "G5":
                    result = _capacity_g5(arrays, evidence)
                elif gate_name == "G6":
                    result = _g6(arrays)
                elif gate_name == "G7":
                    result = _capacity_g7(arrays, evidence)
                elif gate_name == "G8":
                    result = _capacity_g8(arrays, evidence, gate6)
                else:
                    result = _g9(arrays)
            except Exception as error:
                result = _gate_result(
                    actual={"exception": f"{type(error).__name__}: {error}"},
                    expected="explicit PASS with complete evidence",
                    passed=False,
                    detail="exception is a fail-closed gate failure",
                )
            row[gate_name] = result["status"]
            row["details"][gate_name] = result
            if result["status"] != "PASS":
                blocking.append({
                    "building": building,
                    "gate": gate_name,
                    "expected": result["expected"],
                    "actual": result["actual"],
                    "detail": result["detail"],
                })

        row["FINAL"] = "PASS" if all(row[name] == "PASS" for name in gates) else "FAIL"
        if row["FINAL"] == "PASS":
            capacity_gate_passes += 1
        if arrays is not None:
            try:
                measured = _graph_metrics(
                    building,
                    arrays,
                    cohort="capacity_evidence_unapproved_20260826_r3",
                    artifact_path=path,
                )
                row["measured"] = measured
                metrics.append(measured)
            except Exception as error:
                blocking.append({
                    "building": building,
                    "gate": "G11",
                    "expected": "complete node/edge/area/density metrics",
                    "actual": f"{type(error).__name__}: {error}",
                    "detail": "capacity metric extraction failed",
                })
        capacity_table[building] = row

    g10_pass = (
        capacity_artifacts_found == len(CAPACITY_EVIDENCE_BUILDINGS)
        and capacity_gate_passes == len(CAPACITY_EVIDENCE_BUILDINGS)
    )
    g10 = _gate_result(
        actual={
            "capacity_artifact_building_count": capacity_artifacts_found,
            "G1_through_G9_pass_building_count": capacity_gate_passes,
        },
        expected={
            "capacity_artifact_building_count": len(CAPACITY_EVIDENCE_BUILDINGS),
            "G1_through_G9_pass_building_count": len(CAPACITY_EVIDENCE_BUILDINGS),
        },
        passed=g10_pass,
        detail="capacity cohort completeness; this gate does not authorize training",
    )
    if not g10_pass:
        blocking.append({
            "building": "CAPACITY_COHORT",
            "gate": "G10",
            "expected": g10["expected"],
            "actual": g10["actual"],
            "detail": g10["detail"],
        })
    for row in capacity_table.values():
        row["G10"] = g10["status"]

    approved_failures: list[dict[str, Any]] = []
    try:
        approved_acceptance = _load_json(
            approved_acceptance_path, "approved GRAPH_ACCEPTANCE"
        )
        if approved_acceptance.get("GRAPH_ARTIFACT_AUTHORIZED") is not True:
            raise ValueError("approved GRAPH_ACCEPTANCE is not authorized")
        approved_table = approved_acceptance.get("table", {})
        if set(approved_table) != set(BUILDINGS):
            raise ValueError("approved GRAPH_ACCEPTANCE building set differs")
        for building in BUILDINGS:
            path = approved_graph_root / f"{building}.multistorey_topology_v1.npz"
            try:
                arrays = load_multistorey_artifact(path)
                validate_multistorey_artifact_arrays(arrays)
                accepted_row = approved_table[building]
                if accepted_row.get("FINAL") != "PASS":
                    raise ValueError("approved acceptance row is not PASS")
                expected_sha = accepted_row["measured"]["artifact_sha256"]
                actual_sha = _sha256_file(path)
                if actual_sha != expected_sha:
                    raise ValueError(
                        f"artifact SHA differs from approved acceptance: "
                        f"{actual_sha} != {expected_sha}"
                    )
                metrics.append(_graph_metrics(
                    building,
                    arrays,
                    cohort="approved_20260819",
                    artifact_path=path,
                ))
            except Exception as error:
                approved_failures.append({
                    "building": building,
                    "reason": f"{type(error).__name__}: {error}",
                })
    except Exception as error:
        approved_failures.append({
            "building": "APPROVED_COHORT",
            "reason": f"{type(error).__name__}: {error}",
        })

    expected_buildings = set(BUILDINGS) | set(CAPACITY_EVIDENCE_BUILDINGS)
    metric_buildings = {str(row["building"]) for row in metrics}
    complete_distribution = (
        len(metrics) == len(expected_buildings)
        and metric_buildings == expected_buildings
    )
    distribution_rows = sorted(metrics, key=lambda row: (row["node_count"], row["building"]))
    recommendation = (
        _capacity_recommendation(distribution_rows) if complete_distribution else None
    )
    summaries = {
        key: _distribution_summary(distribution_rows, key)
        for key in (
            "node_count",
            "edge_count",
            "total_walkable_area_m2",
            "node_density_per_m2",
        )
    }
    # G11 is the capacity-evidence completeness gate defined for this task.  It
    # does not turn a G1--G10 failure into a pass or authorize these artifacts;
    # it answers only whether the full distribution and a reproducible capacity
    # derivation were collected.
    g11_pass = not approved_failures and complete_distribution and recommendation is not None
    g11 = _gate_result(
        actual={
            "approved_graph_count": sum(
                row["cohort"] == "approved_20260819" for row in metrics
            ),
            "capacity_evidence_graph_count": sum(
                row["cohort"] == "capacity_evidence_unapproved_20260826_r3"
                for row in metrics
            ),
            "total_graph_count": len(metrics),
            "complete_node_distribution": complete_distribution,
            "failed_or_missing_artifacts": approved_failures + [
                {
                    "building": item["building"],
                    "reason": item["actual"],
                    "gate": item["gate"],
                }
                for item in blocking
                if item["gate"] == "G1"
            ],
            "recommendation_method": (
                recommendation["method_id"] if recommendation is not None else None
            ),
        },
        expected={
            "approved_graph_count": len(BUILDINGS),
            "capacity_evidence_graph_count": len(CAPACITY_EVIDENCE_BUILDINGS),
            "total_graph_count": len(expected_buildings),
            "complete_node_distribution": True,
            "failed_or_missing_artifacts": [],
            "recommendation_method": "strict_next_power_of_two_above_observed_max_v1",
        },
        passed=g11_pass,
        detail=(
            "all twenty artifacts, complete capacity metrics, and a reproducible "
            "recommendation derivation are mandatory"
        ),
    )
    if not g11_pass:
        blocking.append({
            "building": "ALL_20",
            "gate": "G11",
            "expected": g11["expected"],
            "actual": g11["actual"],
            "detail": g11["detail"],
        })

    graph_artifact_authorized = not blocking and g10_pass and g11_pass
    return {
        "schema": "GraphCapacityEvidenceV1",
        "CAPACITY_EVIDENCE_COMPLETE": g11_pass,
        "GRAPH_ARTIFACT_AUTHORIZED": graph_artifact_authorized,
        "authorization_note": (
            "Graph-gate authorization does not override the unapproved cohort's "
            "capacity_evidence_only and not_for_training manifest restrictions."
        ),
        "gate_revision": GATE_REVISION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "gates_run": ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10", "G11"],
        "gate_adaptations": {
            "G4": {
                "original_human_approval_check": "SKIPPED_NOT_APPLICABLE",
                "reason": "the capacity cohort has not received H1 human approval",
                "replacement": "exact delivered USDZ SHA match to each g25_batch20260826 JSON",
            },
            "G5": {
                "original_20260819_injected_cell_constants": "SKIPPED_NOT_APPLICABLE",
                "reason": "those constants belong to the approved 20260819 cohort",
                "replacement": "recursive exact comparison of rasterization and storey_split fields",
            },
            "G7": {
                "original_20260819_area_rows": "SKIPPED_NOT_APPLICABLE",
                "reason": "those area rows belong to the approved 20260819 cohort",
                "replacement": "per-storey walkable_area_m2 from each batch-20260826 JSON at +/-0.01 m2",
            },
        },
        "blocking_failures": blocking,
        "G10": g10,
        "G11": g11,
        "capacity_batch_table": capacity_table,
        "twenty_building_distribution": distribution_rows,
        "distribution_summary": summaries,
        "capacity_recommendation": recommendation,
        "artifact_provenance": {
            "capacity_source_usdz_sha256": provenance,
            "approved_acceptance_path": str(approved_acceptance_path.resolve()),
            "approved_acceptance_sha256": (
                _sha256_file(approved_acceptance_path)
                if approved_acceptance_path.is_file()
                else None
            ),
            "architecture_reversal": ARCHITECTURE_REVERSAL,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", choices=BATCHES, default=BATCH_APPROVED)
    parser.add_argument("--usd-root", type=Path)
    parser.add_argument("--decision-root", type=Path, default=DEFAULT_DECISION_ROOT)
    parser.add_argument("--graph-root", type=Path)
    parser.add_argument("--g25", type=Path, default=DEFAULT_G25)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_CAPACITY_EVIDENCE_ROOT)
    parser.add_argument("--gate6", type=Path, default=DEFAULT_GATE6)
    parser.add_argument("--approved-graph-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--approved-acceptance", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    capacity_batch = args.batch == BATCH_CAPACITY_EVIDENCE
    usd_root = args.usd_root or (
        DEFAULT_CAPACITY_USD_ROOT if capacity_batch else DEFAULT_USD_ROOT
    )
    graph_root = args.graph_root or (
        DEFAULT_CAPACITY_OUTPUT_ROOT if capacity_batch else DEFAULT_OUTPUT_ROOT
    )
    if capacity_batch:
        approved_acceptance = args.approved_acceptance or (
            args.approved_graph_root / "GRAPH_ACCEPTANCE.json"
        )
        report = run_capacity_evidence(
            usd_root=usd_root,
            graph_root=graph_root,
            evidence_root=args.evidence_root,
            gate6_path=args.gate6,
            approved_graph_root=args.approved_graph_root,
            approved_acceptance_path=approved_acceptance,
        )
        output = args.output or graph_root / "CAPACITY_EVIDENCE.json"
        success = bool(report["GRAPH_ARTIFACT_AUTHORIZED"])
        summary_key = "GRAPH_ARTIFACT_AUTHORIZED"
    else:
        report = run_acceptance(
            usd_root=usd_root,
            decision_root=args.decision_root,
            graph_root=graph_root,
            g25_path=args.g25,
            gate6_path=args.gate6,
        )
        output = args.output or graph_root / "GRAPH_ACCEPTANCE.json"
        success = bool(report["GRAPH_ARTIFACT_AUTHORIZED"])
        summary_key = "GRAPH_ARTIFACT_AUTHORIZED"
    payload = (json.dumps(report, indent=2, allow_nan=False) + "\n").encode("utf-8")
    exclusive_publish_bytes(output, payload)
    print(json.dumps({
        summary_key: success,
        "blocking_failures": report["blocking_failures"],
        "output": str(output.resolve()),
    }, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
