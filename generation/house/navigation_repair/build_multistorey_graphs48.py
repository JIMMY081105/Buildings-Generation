#!/usr/bin/env python3
"""Build deterministic T0--T3 graphs for the exact 48-building H1 release."""

from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ProtoMotions-main"))

from build_multistorey_graphs import _load_scratch, _save_scratch  # noqa: E402
from protomotions.simulator.base_simulator.free_space_topology import (  # noqa: E402
    exclusive_publish_bytes,
)
from protomotions.simulator.base_simulator.multistorey_topology import (  # noqa: E402
    ARCHITECTURE_REVERSAL,
    build_multistorey_artifact_arrays,
    canonical_json_bytes,
    corridor_cross_check,
    deterministic_multistorey_npz_bytes,
    manifest_from_artifact,
    validate_multistorey_artifact_arrays,
)
from protomotions.simulator.base_simulator.scene_storey_bands import (  # noqa: E402
    StoreyBandExtraction,
    rasterize_storey_bands,
)


EXPECTED = 48
DEFERRED = {
    "home_train123_train142_straight_v001_h320",
    "home_train84_train64_straight_v001_h270",
}


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def expected_storey_source(extraction: StoreyBandExtraction) -> dict[str, Any]:
    return {
        "rasterization": extraction.rasterization,
        "storey_split": extraction.storey_split | {
            "Floor2": "slab-bottom layer and above",
            "Floor1": "below slab plus duplicated slab-bottom layer as ceiling",
        },
    }


def add_release_lineage(
    arrays: dict[str, np.ndarray],
    *,
    h1_manifest: Path,
    candidate_review: Path,
    g25_path: Path,
    stage_d_row: dict[str, Any],
) -> dict[str, np.ndarray]:
    marked = dict(arrays)
    manifest = manifest_from_artifact(marked)
    manifest.update({
        "usage": "training_candidate_pre_freeze",
        "human_approved": True,
        "h1_manifest": str(h1_manifest),
        "h1_manifest_sha256": file_sha256(h1_manifest),
        "candidate_review_csv": str(candidate_review),
        "candidate_review_csv_sha256": file_sha256(candidate_review),
        "g25_evidence": str(g25_path),
        "g25_evidence_sha256": file_sha256(g25_path),
        "stage_d_isaac_report": stage_d_row["isaac_report"],
        "stage_d_isaac_report_sha256": stage_d_row["isaac_report_sha256"],
        "stage_d_isaac_verdict": stage_d_row["stage_b_isaac_verdict"],
        "stage_d_isaac_verdict_sha256": stage_d_row[
            "stage_b_isaac_verdict_sha256"],
        "graph_token_capacity_evidence_invalidated": True,
        "OBSERVATION_MANIFEST_FROZEN": False,
    })
    marked["manifest_json_utf8"] = np.frombuffer(
        canonical_json_bytes(manifest), dtype=np.uint8).copy()
    validate_multistorey_artifact_arrays(marked)
    return marked


def frozen_corridor_gate(arrays: dict[str, np.ndarray]) -> tuple[dict[str, Any], bool]:
    result = corridor_cross_check(arrays)
    graph = float(result["graph_geodesic_m"])
    corridor = float(result["plan_root_corridor_length_m"])
    ratio = float(result["ratio"])
    absolute = abs(graph - corridor)
    lower = graph >= corridor * 0.95
    branch = "short_absolute" if corridor < 5.0 else "long_ratio"
    upper = absolute <= 5.0 if branch == "short_absolute" else ratio <= 2.0
    measured = result | {
        "absolute_diff_m": absolute,
        "branch": branch,
        "lower_bound_ok": lower,
        "upper_bound_ok": upper,
    }
    return measured, bool(
        result.get("planner_status") == "success" and lower and upper)


def build_one(
    bid: str,
    *,
    delivery_root: Path,
    g25_root: Path,
    output_root: Path,
    scratch_root: Path | None,
    h1_manifest: Path,
    candidate_review: Path,
    stage_d_row: dict[str, Any],
) -> dict[str, Any]:
    usdz = require_file(
        delivery_root / bid / "scene" / "building.usdz", f"{bid} USDZ")
    g25_path = require_file(g25_root / f"{bid}.json", f"{bid} G25 evidence")
    g25 = load_json(g25_path)
    require(g25.get("building") == bid, f"{bid}: G25 BID differs")
    require(g25.get("status") in {"PASS", "FAIL"},
            f"{bid}: G25 status is not terminal PASS/FAIL")
    require(set(g25.get("storeys", {})) == {"Floor1", "Floor2"},
            f"{bid}: G25 does not contain exactly two storeys")
    require(all(g25["storeys"][name].get("status") in {"PASS", "FAIL"}
                for name in ("Floor1", "Floor2")),
            f"{bid}: a G25 storey status is not terminal PASS/FAIL")
    source_sha = file_sha256(usdz)
    require(g25.get("source", {}).get("building_usdz_sha256") == source_sha,
            f"{bid}: G25 source USDZ hash differs")
    require(g25["source"].get("device") == "CPU",
            f"{bid}: G25 device is not CPU")
    require(g25["storeys"]["Floor1"].get("arrival_source", {}).get(
        "source_sha256") == stage_d_row["isaac_report_sha256"],
        f"{bid}: G25 is not bound to the accepted Stage D report")
    stage_d_report = require_file(
        Path(stage_d_row["isaac_report"]), f"{bid} Stage D report")
    stage_d_verdict = require_file(
        Path(stage_d_row["stage_b_isaac_verdict"]),
        f"{bid} Stage D verdict")
    require(file_sha256(stage_d_report) == stage_d_row["isaac_report_sha256"],
            f"{bid}: live Stage D report hash differs")
    require(file_sha256(stage_d_verdict)
            == stage_d_row["stage_b_isaac_verdict_sha256"],
            f"{bid}: live Stage D verdict hash differs")

    scratch_path = (
        scratch_root / f"{bid}.storey_bands_scratch.npz"
        if scratch_root is not None else None
    )
    if scratch_path is not None and scratch_path.is_file():
        binding_path = require_file(
            scratch_path.with_suffix(scratch_path.suffix + ".binding.json"),
            f"{bid} scratch binding")
        binding = load_json(binding_path)
        require(binding == {
            "schema": "StoreyBandScratchBindingV1",
            "building": bid,
            "source_usdz": str(usdz),
            "source_usdz_sha256": source_sha,
            "scratch": str(scratch_path.resolve()),
            "scratch_sha256": file_sha256(scratch_path),
        }, f"{bid}: scratch binding differs")
        extraction = _load_scratch(scratch_path)
    else:
        if scratch_path is not None:
            require(not scratch_path.with_suffix(
                scratch_path.suffix + ".binding.json").exists(),
                f"{bid}: orphan scratch binding exists")
        extraction = rasterize_storey_bands(usdz)
        if scratch_path is not None:
            _save_scratch(scratch_path, extraction)
            exclusive_json(
                scratch_path.with_suffix(scratch_path.suffix + ".binding.json"),
                {
                    "schema": "StoreyBandScratchBindingV1",
                    "building": bid,
                    "source_usdz": str(usdz),
                    "source_usdz_sha256": source_sha,
                    "scratch": str(scratch_path.resolve()),
                    "scratch_sha256": file_sha256(scratch_path),
                },
            )
    expected_source = expected_storey_source(extraction)
    require(expected_source["rasterization"] == g25["rasterization"],
            f"{bid}: T0 rasterization fields differ from G25")
    require(expected_source["storey_split"] == g25["storey_split"],
            f"{bid}: T0 storey split fields differ from G25")
    modelling = g25["storeys"]["Floor1"]["modelling_decision"]
    actual_injected = int(
        extraction.floor1_modelling["new_ground_cells_injected"])
    actual_support = float(
        extraction.floor1_modelling["rasterized_support_top_z_m"])
    require(actual_injected == int(modelling["new_ground_cells_injected"]),
            f"{bid}: injected-cell regression differs")
    require(abs(actual_support - float(
        modelling["rasterized_support_top_z_m"])) <= 0.0001 + 1.0e-12,
        f"{bid}: Floor1 support-top regression differs")

    arrays = build_multistorey_artifact_arrays(
        extraction,
        building=bid,
        source_usdz_path=usdz,
        source_usdz_sha256=source_sha,
        decision_path=candidate_review,
        decision_sha256=file_sha256(candidate_review),
    )
    arrays = add_release_lineage(
        arrays,
        h1_manifest=h1_manifest,
        candidate_review=candidate_review,
        g25_path=g25_path,
        stage_d_row=stage_d_row,
    )
    manifest = manifest_from_artifact(arrays)
    centers = np.asarray(arrays["node_center_m"])
    stair = manifest["stair_edge"]
    endpoint_errors: dict[str, float] = {}
    for storey, node_key in (
        ("Floor1", "floor1_node_id"),
        ("Floor2", "floor2_node_id"),
    ):
        expected_row = g25["storeys"][storey]
        actual_area = float(manifest["storeys"][storey]["walkable_area_m2"])
        require(abs(actual_area - float(expected_row["walkable_area_m2"]))
                <= 0.01 + 1.0e-12,
                f"{bid}: {storey} walkable area differs from G25")
        endpoint = centers[int(stair[node_key]), :2]
        expected_xy = np.asarray(
            expected_row["stair_arrival_xyz_m"][:2], dtype=np.float64)
        error = float(np.linalg.norm(endpoint - expected_xy))
        endpoint_errors[storey] = error
        require(error <= 0.50 + 1.0e-12,
                f"{bid}: {storey} stair endpoint exceeds 0.50 m")
    corridor, corridor_pass = frozen_corridor_gate(arrays)
    first = deterministic_multistorey_npz_bytes(arrays)
    second = deterministic_multistorey_npz_bytes(arrays)
    require(first == second, f"{bid}: graph serialization is nondeterministic")
    output = output_root / f"{bid}.multistorey_topology_v1.npz"
    artifact_sha = sha256(first).hexdigest()
    published_sha = exclusive_publish_bytes(output, first)
    require(published_sha == artifact_sha, f"{bid}: published hash differs")
    return {
        "BID": bid,
        "status": "PASS" if corridor_pass else "FAIL",
        "terminal_reason": (
            None if corridor_pass else "frozen_G9_corridor_gate_failed"
        ),
        "artifact_role": "pre_freeze_candidate_and_capacity_evidence",
        "training_tensor_release_authorized_by_this_record": False,
        "output": str(output.resolve()),
        "artifact_sha256": artifact_sha,
        "source_usdz_sha256": source_sha,
        "g25_evidence_sha256": file_sha256(g25_path),
        "stage_d_isaac_report_sha256": stage_d_row["isaac_report_sha256"],
        "floor1_injected_cells": actual_injected,
        "floor1_walkable_area_m2": manifest["storeys"]["Floor1"][
            "walkable_area_m2"],
        "floor2_walkable_area_m2": manifest["storeys"]["Floor2"][
            "walkable_area_m2"],
        "nodes": len(arrays["node_id"]),
        "edges": len(arrays["edge_id"]),
        "stair_endpoint_error_m": endpoint_errors,
        "corridor_cross_check": corridor,
    }


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
    candidate_review = require_file(
        h1_root / "candidate_review.csv", "candidate review")
    manifest = load_json(h1_manifest)
    decisions = read_decisions(candidate_review)
    approved = sorted(manifest.get("approved_building_bids", []))
    require(manifest.get("status") == "H1_APPROVED_48_DEFERRED_2",
            "H1 manifest status differs")
    require(len(approved) == EXPECTED and len(set(approved)) == EXPECTED,
            "H1 approved set is not 48 unique BIDs")
    require(set(manifest.get("deferred_building_bids", [])) == DEFERRED,
            "H1 deferred set differs")
    require(set(decisions) == set(approved) | DEFERRED,
            "candidate decision set differs")
    require(all(decisions[bid] == "approve" for bid in approved),
            "an approved BID lacks decision=approve")
    require(all(decisions[bid] == "deferred_no_gpu_spatial_review"
                for bid in DEFERRED), "a deferred decision differs")
    require(manifest.get("graph_token_capacity_evidence_invalidated") is True,
            "capacity invalidation flag is absent")
    require(manifest.get("graph_token_capacity_change_authorized_now") is False,
            "capacity changed before remeasurement")
    require(manifest.get("OBSERVATION_MANIFEST_FROZEN") is False,
            "T7 is already frozen")

    stage_d_path = require_file(args.stage_d_summary, "Stage D summary")
    stage_d = load_json(stage_d_path)
    require(stage_d.get("status") in {"PASS", "FAIL"},
            "Stage D status is not terminal")
    require(stage_d.get("building_count") == EXPECTED
            and isinstance(stage_d.get("passed_buildings"), int)
            and isinstance(stage_d.get("failed_buildings"), int)
            and stage_d.get("passed_buildings")
            + stage_d.get("failed_buildings") == EXPECTED,
            "Stage D terminal coverage differs")
    stage_d_rows = {row["BID"]: row for row in stage_d.get("rows", [])}
    require(set(stage_d_rows) == set(approved),
            "Stage D set is not the exact approved set")

    g25_summary_path = require_file(args.g25_summary, "G25 summary")
    g25_summary = load_json(g25_summary_path)
    require(g25_summary.get("status") in {"PASS", "FAIL"},
            "G25 status is not terminal")
    require(g25_summary.get("building_count") == EXPECTED
            and isinstance(g25_summary.get("passed_buildings"), int)
            and isinstance(g25_summary.get("failed_buildings"), int)
            and g25_summary.get("passed_buildings")
            + g25_summary.get("failed_buildings") == EXPECTED,
            "G25 terminal coverage differs")
    require({row["BID"] for row in g25_summary.get("rows", [])} == set(approved),
            "G25 set is not the exact approved set")

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, bid in enumerate(approved, start=1):
        try:
            row = build_one(
                bid,
                delivery_root=delivery_root,
                g25_root=g25_root,
                output_root=output_root,
                scratch_root=(
                    args.scratch_root.expanduser().resolve()
                    if args.scratch_root is not None else None),
                h1_manifest=h1_manifest,
                candidate_review=candidate_review,
                stage_d_row=stage_d_rows[bid],
            )
            rows.append(row)
            if row["status"] != "PASS":
                failures.append({
                    "BID": bid,
                    "gate": "G9_CORRIDOR",
                    "error": "frozen G9 corridor gate failed",
                    "measured": row["corridor_cross_check"],
                })
            print(
                f"graph {index}/{EXPECTED} {bid} {row['status']} "
                f"nodes={row['nodes']}",
                flush=True,
            )
        except Exception as error:  # fail closed, but continue exact-batch coverage
            failures.append({
                "BID": bid,
                "gate": "BUILD_OR_BINDING",
                "error": f"{type(error).__name__}: {error}",
            })
            print(f"graph {index}/{EXPECTED} {bid} FAIL: {error}", flush=True)
    passed = sum(row["status"] == "PASS" for row in rows)
    terminal = len(rows) + sum(
        failure.get("gate") == "BUILD_OR_BINDING" for failure in failures
    )
    coverage_complete = terminal == EXPECTED
    capacity_complete = len(rows) == EXPECTED
    payload = {
        "schema": "MultistoreyGraphBuild48V2",
        "status": "PASS" if len(rows) == EXPECTED and passed == EXPECTED else "FAIL",
        "coverage_status": "PASS" if coverage_complete else "FAIL",
        "expected_building_count": EXPECTED,
        "built_building_count": len(rows),
        "terminal_building_count": terminal,
        "passed_building_count": passed,
        "failed_building_count": EXPECTED - passed if coverage_complete else len(failures),
        "capacity_measured_building_count": len(rows),
        "approved_building_bids": approved,
        "deferred_building_bids": sorted(DEFERRED),
        "deferred_buildings_are_failures": False,
        "h1_manifest_sha256": file_sha256(h1_manifest),
        "candidate_review_sha256": file_sha256(candidate_review),
        "stage_d_summary": str(stage_d_path),
        "stage_d_summary_sha256": file_sha256(stage_d_path),
        "g25_summary": str(g25_summary_path),
        "g25_summary_sha256": file_sha256(g25_summary_path),
        "architecture_reversal": ARCHITECTURE_REVERSAL,
        "graph_token_capacity_evidence_invalidated": True,
        "capacity_change_performed": False,
        "OBSERVATION_MANIFEST_FROZEN": False,
        "rows": rows,
        "blocking_failures": failures,
    }
    exclusive_json(summary_path, payload)
    print(json.dumps({
        "status": payload["status"],
        "built_building_count": len(rows),
        "blocking_failures": failures,
        "summary": str(summary_path),
    }, indent=2))
    # Quality FAIL remains encoded in the payload.  A nonzero process status is
    # reserved for incomplete terminal/capacity coverage so orchestration can
    # continue to the independent exact-48 capacity measurement.
    return 0 if coverage_complete and capacity_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
