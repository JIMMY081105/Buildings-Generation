#!/usr/bin/env python3
"""Build deterministic T0--T3 graphs for the exact-54 opened batch."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import numpy as np

from build_multistorey_graphs import _load_scratch
from build_multistorey_graphs48 import (
    ARCHITECTURE_REVERSAL,
    build_multistorey_artifact_arrays,
    canonical_json_bytes,
    deterministic_multistorey_npz_bytes,
    exclusive_json,
    exclusive_publish_bytes,
    expected_storey_source,
    file_sha256,
    frozen_corridor_gate,
    load_json,
    manifest_from_artifact,
    require,
    validate_multistorey_artifact_arrays,
)


EXPECTED = 54


def add_exact54_lineage(
    arrays: dict[str, np.ndarray],
    *,
    input_manifest: Path,
    collision_audit: Path,
    door_report: Path,
    g25_path: Path,
) -> dict[str, np.ndarray]:
    marked = dict(arrays)
    manifest = manifest_from_artifact(marked)
    manifest.update(
        {
            "usage": "training_candidate_pre_freeze",
            "human_approved": True,
            "approval_scope": "user-authorized exact-54 unification and open-door rebuild",
            "exact54_input_manifest": str(input_manifest),
            "exact54_input_manifest_sha256": file_sha256(input_manifest),
            "collision_audit": str(collision_audit),
            "collision_audit_sha256": file_sha256(collision_audit),
            "door_opening_report": str(door_report),
            "door_opening_report_sha256": file_sha256(door_report),
            "g25_evidence": str(g25_path),
            "g25_evidence_sha256": file_sha256(g25_path),
            "graph_token_capacity_evidence_invalidated": True,
            "OBSERVATION_MANIFEST_FROZEN": False,
        }
    )
    marked["manifest_json_utf8"] = np.frombuffer(
        canonical_json_bytes(manifest), dtype=np.uint8
    ).copy()
    validate_multistorey_artifact_arrays(marked)
    return marked


def build_one(
    bid: str,
    *,
    input_root: Path,
    g25_root: Path,
    graph_root: Path,
    scratch_root: Path,
    input_manifest: Path,
    input_row: dict[str, Any],
    collision_audit: Path,
) -> dict[str, Any]:
    usdz = Path(input_row["geometry_link"])
    require(usdz == input_root / bid / "scene" / "building.usdz", f"{bid}: input path differs")
    require(usdz.is_file(), f"{bid}: USDZ absent")
    source_sha = file_sha256(usdz)
    require(source_sha == input_row["geometry_sha256"], f"{bid}: USDZ binding differs")
    door_report = Path(input_row["door_opening_report"])
    require(
        door_report.is_file()
        and file_sha256(door_report) == input_row["door_opening_report_sha256"],
        f"{bid}: door report binding differs",
    )
    door = load_json(door_report)
    require(
        door.get("status") == "PASS" and door.get("output_sha256") == source_sha,
        f"{bid}: door report is not bound to graph source",
    )
    g25_path = g25_root / f"{bid}.json"
    g25 = load_json(g25_path)
    require(g25.get("building") == bid, f"{bid}: G25 BID differs")
    require(g25.get("status") in {"PASS", "FAIL"}, f"{bid}: G25 not terminal")
    require(set(g25.get("storeys", {})) == {"Floor1", "Floor2"}, f"{bid}: G25 storeys differ")
    require(
        all(g25["storeys"][name].get("status") in {"PASS", "FAIL"} for name in ("Floor1", "Floor2")),
        f"{bid}: G25 storey not terminal",
    )
    require(
        g25.get("source", {}).get("building_usdz_sha256") == source_sha,
        f"{bid}: G25 source hash differs",
    )
    require(g25.get("source", {}).get("device") == "CPU", f"{bid}: G25 device differs")
    require(
        g25["storeys"]["Floor1"].get("arrival_source", {}).get("source_sha256")
        == input_row["isaac_report_sha256"],
        f"{bid}: G25 arrival evidence differs",
    )
    scratch = scratch_root / f"{bid}.storey_bands_scratch.npz"
    binding_path = scratch.with_suffix(scratch.suffix + ".binding.json")
    binding = load_json(binding_path)
    require(
        binding
        == {
            "schema": "StoreyBandScratchBindingV1",
            "building": bid,
            "source_usdz": str(usdz),
            "source_usdz_sha256": source_sha,
            "scratch": str(scratch.resolve()),
            "scratch_sha256": file_sha256(scratch),
        },
        f"{bid}: scratch binding differs",
    )
    extraction = _load_scratch(scratch)
    expected_source = expected_storey_source(extraction)
    require(expected_source["rasterization"] == g25["rasterization"], f"{bid}: rasterization differs")
    require(expected_source["storey_split"] == g25["storey_split"], f"{bid}: storey split differs")
    modelling = g25["storeys"]["Floor1"]["modelling_decision"]
    actual_injected = int(extraction.floor1_modelling["new_ground_cells_injected"])
    actual_support = float(extraction.floor1_modelling["rasterized_support_top_z_m"])
    require(
        actual_injected == int(modelling["new_ground_cells_injected"]),
        f"{bid}: injected-cell regression differs",
    )
    require(
        abs(actual_support - float(modelling["rasterized_support_top_z_m"]))
        <= 0.0001 + 1.0e-12,
        f"{bid}: Floor1 support-top regression differs",
    )
    arrays = build_multistorey_artifact_arrays(
        extraction,
        building=bid,
        source_usdz_path=usdz,
        source_usdz_sha256=source_sha,
        decision_path=input_manifest,
        decision_sha256=file_sha256(input_manifest),
    )
    arrays = add_exact54_lineage(
        arrays,
        input_manifest=input_manifest,
        collision_audit=collision_audit,
        door_report=door_report,
        g25_path=g25_path,
    )
    manifest = manifest_from_artifact(arrays)
    centers = np.asarray(arrays["node_center_m"])
    stair = manifest["stair_edge"]
    endpoint_errors = {}
    for storey, node_key in (("Floor1", "floor1_node_id"), ("Floor2", "floor2_node_id")):
        expected_row = g25["storeys"][storey]
        area = float(manifest["storeys"][storey]["walkable_area_m2"])
        require(
            abs(area - float(expected_row["walkable_area_m2"])) <= 0.01 + 1.0e-12,
            f"{bid}: {storey} walkable area differs",
        )
        endpoint = centers[int(stair[node_key]), :2]
        expected_xy = np.asarray(expected_row["stair_arrival_xyz_m"][:2], dtype=np.float64)
        error = float(np.linalg.norm(endpoint - expected_xy))
        endpoint_errors[storey] = error
        require(error <= 0.50 + 1.0e-12, f"{bid}: {storey} stair endpoint exceeds 0.50 m")
    corridor, corridor_pass = frozen_corridor_gate(arrays)
    first = deterministic_multistorey_npz_bytes(arrays)
    second = deterministic_multistorey_npz_bytes(arrays)
    require(first == second, f"{bid}: nondeterministic graph serialization")
    output = graph_root / f"{bid}.multistorey_topology_v1.npz"
    artifact_sha = sha256(first).hexdigest()
    published_sha = exclusive_publish_bytes(output, first)
    require(published_sha == artifact_sha, f"{bid}: graph publication hash differs")
    return {
        "BID": bid,
        "status": "PASS" if corridor_pass else "FAIL",
        "terminal_reason": None if corridor_pass else "frozen_G9_corridor_gate_failed",
        "artifact_role": "exact54_pre_freeze_candidate_and_capacity_evidence",
        "output": str(output.resolve()),
        "artifact_sha256": artifact_sha,
        "source_usdz_sha256": source_sha,
        "g25_evidence_sha256": file_sha256(g25_path),
        "door_opening_report_sha256": file_sha256(door_report),
        "floor1_injected_cells": actual_injected,
        "floor1_walkable_area_m2": manifest["storeys"]["Floor1"]["walkable_area_m2"],
        "floor2_walkable_area_m2": manifest["storeys"]["Floor2"]["walkable_area_m2"],
        "nodes": len(arrays["node_id"]),
        "edges": len(arrays["edge_id"]),
        "stair_endpoint_error_m": endpoint_errors,
        "corridor_cross_check": corridor,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--g25-root", required=True, type=Path)
    parser.add_argument("--g25-summary", required=True, type=Path)
    parser.add_argument("--scratch-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--collision-audit", required=True, type=Path)
    args = parser.parse_args()
    input_root = args.input_root.resolve()
    input_manifest = input_root / "G25_INPUT_MANIFEST.json"
    manifest = load_json(input_manifest)
    bids = sorted(manifest.get("building_bids", []))
    require(
        manifest.get("schema") == "G25InputView54V1"
        and manifest.get("status") == "PASS"
        and manifest.get("building_count") == EXPECTED
        and len(bids) == len(set(bids)) == EXPECTED,
        "input manifest is not exact-54 PASS",
    )
    input_rows = {row["BID"]: row for row in manifest.get("rows", [])}
    require(set(input_rows) == set(bids), "input row set differs")
    g25_summary = load_json(args.g25_summary.resolve())
    require(
        g25_summary.get("building_count") == EXPECTED
        and g25_summary.get("terminal_coverage_complete") is True
        and g25_summary.get("passed_buildings", 0) + g25_summary.get("failed_buildings", 0) == EXPECTED
        and {row["BID"] for row in g25_summary.get("rows", [])} == set(bids),
        "G25 lacks exact-54 terminal coverage",
    )
    collision_audit = args.collision_audit.resolve()
    collision = load_json(collision_audit)
    require(
        collision.get("status") == "PASS"
        and collision.get("scope", {}).get("observed_buildings") == EXPECTED,
        "collision audit is not exact-54 PASS",
    )
    graph_root = args.output_root.resolve()
    summary_path = args.summary.resolve()
    require(not graph_root.exists(), f"graph output exists: {graph_root}")
    require(not summary_path.exists(), f"graph summary exists: {summary_path}")
    graph_root.mkdir(parents=True)
    rows = []
    failures = []
    for index, bid in enumerate(bids, 1):
        try:
            row = build_one(
                bid,
                input_root=input_root,
                g25_root=args.g25_root.resolve(),
                graph_root=graph_root,
                scratch_root=args.scratch_root.resolve(),
                input_manifest=input_manifest,
                input_row=input_rows[bid],
                collision_audit=collision_audit,
            )
            rows.append(row)
            if row["status"] != "PASS":
                failures.append(
                    {
                        "BID": bid,
                        "gate": "G9_CORRIDOR",
                        "error": "frozen G9 corridor gate failed",
                        "measured": row["corridor_cross_check"],
                    }
                )
            print(
                f"graph {index}/{EXPECTED} {bid} {row['status']} nodes={row['nodes']}",
                flush=True,
            )
        except Exception as error:
            failures.append(
                {"BID": bid, "gate": "BUILD_OR_BINDING", "error": f"{type(error).__name__}: {error}"}
            )
            print(f"graph {index}/{EXPECTED} {bid} FAIL: {error}", flush=True)
    passed = sum(row["status"] == "PASS" for row in rows)
    binding_failures = sum(item["gate"] == "BUILD_OR_BINDING" for item in failures)
    terminal = len(rows) + binding_failures
    payload = {
        "schema": "MultistoreyGraphBuild54V1",
        "status": "PASS" if len(rows) == EXPECTED and passed == EXPECTED else "FAIL",
        "coverage_status": "PASS" if terminal == EXPECTED else "FAIL",
        "expected_building_count": EXPECTED,
        "built_building_count": len(rows),
        "terminal_building_count": terminal,
        "passed_building_count": passed,
        "failed_building_count": EXPECTED - passed if terminal == EXPECTED else len(failures),
        "capacity_measured_building_count": len(rows),
        "building_bids": bids,
        "input_manifest": str(input_manifest),
        "input_manifest_sha256": file_sha256(input_manifest),
        "g25_summary": str(args.g25_summary.resolve()),
        "g25_summary_sha256": file_sha256(args.g25_summary.resolve()),
        "collision_audit": str(collision_audit),
        "collision_audit_sha256": file_sha256(collision_audit),
        "architecture_reversal": ARCHITECTURE_REVERSAL,
        "graph_token_capacity_evidence_invalidated": True,
        "capacity_change_performed": False,
        "OBSERVATION_MANIFEST_FROZEN": False,
        "rows": rows,
        "blocking_failures": failures,
    }
    exclusive_json(summary_path, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "built_building_count": len(rows),
                "blocking_failure_count": len(failures),
                "summary": str(summary_path),
            },
            indent=2,
        )
    )
    return 0 if terminal == EXPECTED and len(rows) == EXPECTED else 1


if __name__ == "__main__":
    raise SystemExit(main())
