#!/usr/bin/env python3
"""Build deterministic T0--T3 global navigation graphs for a named batch."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ProtoMotions-main"))

from protomotions.simulator.base_simulator.free_space_topology import (
    exclusive_publish_bytes,
)
from protomotions.simulator.base_simulator.multistorey_topology import (
    build_multistorey_artifact_arrays,
    canonical_json_bytes,
    corridor_cross_check,
    deterministic_multistorey_npz_bytes,
    manifest_from_artifact,
    validate_multistorey_artifact_arrays,
)
from protomotions.simulator.base_simulator.scene_storey_bands import (
    StoreyBand,
    StoreyBandExtraction,
    rasterize_storey_bands,
)
from protomotions.simulator.base_simulator.scene_geometry_cache import (
    SceneGeometryGridSpec,
)


BUILDINGS = (
    "home_train107_train20_ushaped_h300",
    "home_train74_train98_lshaped_h300",
    "home_train76_train123_straight_h300",
    "home_train84_train105_straight_h270",
    "home_train84_train115_straight_h270",
    "home_train84_train22_straight_h270",
    "home_train92_train96_straight_h300",
    "home_train96_train20_straight_h270",
    "home_train98_train74_lshaped_h320",
    "home_train98_train74_ushaped_h320",
)
CAPACITY_EVIDENCE_BUILDINGS = (
    "home_train104_train96_straight_h320",
    "home_train249_train74_lshaped_h360",
    "home_train138_train104_straight_h320",
    "home_train249_train74_ushaped_h360",
    "home_train138_train107_lshaped_h320",
    "home_train50_train104_straight_h320",
    "home_train138_train107_ushaped_h320",
    "home_train74_train157_straight_h300",
    "home_train178_train132_straight_h270",
    "home_train96_train99_straight_h270",
)
BATCH_APPROVED = "approved-20260819"
BATCH_CAPACITY_EVIDENCE = "capacity-evidence-20260826-r3"
BATCHES = (BATCH_APPROVED, BATCH_CAPACITY_EVIDENCE)
DEFAULT_USD_ROOT = Path(r"F:\成果\USD")
DEFAULT_DECISION_ROOT = Path(r"F:\成果\01_可训练_已批准_batch_20260819\validation")
DEFAULT_OUTPUT_ROOT = Path(r"F:\成果\05_下一阶段_全局场景编码器\graphs")
DEFAULT_G25 = HERE / "evidence" / "g25_building_batch_20260819.json"
DEFAULT_CAPACITY_USD_ROOT = Path(
    r"E:\AI_AGENT_WORKSPACE\PRODUCTS\04_buildings\H1_Review"
    r"\batch_20260826_r3\geometry"
)
DEFAULT_CAPACITY_EVIDENCE_ROOT = HERE / "evidence" / "g25_batch20260826"
DEFAULT_CAPACITY_OUTPUT_ROOT = Path(
    r"F:\成果\05_下一阶段_全局场景编码器\graphs_capacity_evidence"
)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_g25(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("G25 evidence must be a JSON object")
    return value


def _capacity_evidence_source(
    extraction: StoreyBandExtraction,
) -> dict[str, Any]:
    """Return the exact T0 fields preregistered for the capacity batch."""
    return {
        "rasterization": extraction.rasterization,
        "storey_split": extraction.storey_split | {
            "Floor2": "slab-bottom layer and above",
            "Floor1": "below slab plus duplicated slab-bottom layer as ceiling",
        },
    }


def _mark_capacity_evidence_only(
    arrays: dict[str, np.ndarray], extraction: StoreyBandExtraction
) -> dict[str, np.ndarray]:
    """Add use restrictions without changing any T0--T3 graph calculation."""
    marked = dict(arrays)
    manifest = manifest_from_artifact(marked)
    manifest.update({
        "usage": "capacity_evidence_only",
        "human_approved": False,
        "not_for_training": True,
        "capacity_evidence_source": _capacity_evidence_source(extraction),
    })
    marked["manifest_json_utf8"] = np.frombuffer(
        canonical_json_bytes(manifest), dtype=np.uint8
    ).copy()
    validate_multistorey_artifact_arrays(marked)
    return marked


def _grid_payload(grid: SceneGeometryGridSpec) -> dict[str, Any]:
    return {
        "scene_bounds_min_m": list(grid.scene_bounds_min_m),
        "scene_bounds_max_m": list(grid.scene_bounds_max_m),
        "grid_bounds_max_m": list(grid.grid_bounds_max_m),
        "fine_shape": list(grid.fine_shape),
        "coarse_shape": list(grid.coarse_shape),
    }


def _grid_from_payload(value: dict[str, Any]) -> SceneGeometryGridSpec:
    grid = SceneGeometryGridSpec(
        scene_bounds_min_m=tuple(value["scene_bounds_min_m"]),
        scene_bounds_max_m=tuple(value["scene_bounds_max_m"]),
        grid_bounds_max_m=tuple(value["grid_bounds_max_m"]),
        fine_shape=tuple(value["fine_shape"]),
        coarse_shape=tuple(value["coarse_shape"]),
    )
    grid.validate()
    return grid


def _save_scratch(path: Path, extraction: StoreyBandExtraction) -> None:
    if path.exists():
        raise FileExistsError(f"refusing pre-existing scratch: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "full_grid": _grid_payload(extraction.full_grid_spec),
        "floor1_grid": _grid_payload(extraction.bands[0].grid_spec),
        "floor2_grid": _grid_payload(extraction.bands[1].grid_spec),
        "peak_z_index": extraction.peak_z_index,
        "cut_z_index": extraction.cut_z_index,
        "floor1_support_top_z_m": extraction.floor1_support_top_z_m,
        "rasterization": extraction.rasterization,
        "storey_split": extraction.storey_split,
        "floor1_modelling": extraction.floor1_modelling,
    }
    np.savez(
        path,
        metadata_json_utf8=np.frombuffer(
            json.dumps(metadata, sort_keys=True).encode("utf-8"), dtype=np.uint8),
        full_occupancy=extraction.full_occupancy,
        full_stair_mask=extraction.full_stair_mask,
        floor1_occupancy=extraction.bands[0].occupancy,
        floor1_stair_mask=extraction.bands[0].stair_mask,
        floor2_occupancy=extraction.bands[1].occupancy,
        floor2_stair_mask=extraction.bands[1].stair_mask,
    )


def _load_scratch(path: Path) -> StoreyBandExtraction:
    with np.load(path, allow_pickle=False) as archive:
        metadata = json.loads(
            np.asarray(archive["metadata_json_utf8"], dtype=np.uint8)
            .tobytes().decode("utf-8"))
        extraction = StoreyBandExtraction(
            bands=(
                StoreyBand(
                    _grid_from_payload(metadata["floor1_grid"]),
                    np.asarray(archive["floor1_occupancy"]),
                    np.asarray(archive["floor1_stair_mask"]),
                    "Floor1",
                ),
                StoreyBand(
                    _grid_from_payload(metadata["floor2_grid"]),
                    np.asarray(archive["floor2_occupancy"]),
                    np.asarray(archive["floor2_stair_mask"]),
                    "Floor2",
                ),
            ),
            full_occupancy=np.asarray(archive["full_occupancy"]),
            full_stair_mask=np.asarray(archive["full_stair_mask"]),
            full_grid_spec=_grid_from_payload(metadata["full_grid"]),
            peak_z_index=int(metadata["peak_z_index"]),
            cut_z_index=int(metadata["cut_z_index"]),
            floor1_support_top_z_m=float(metadata["floor1_support_top_z_m"]),
            rasterization=metadata["rasterization"],
            storey_split=metadata["storey_split"],
            floor1_modelling=metadata["floor1_modelling"],
        )
    extraction.validate()
    return extraction


def build_one(
    building: str,
    *,
    usd_root: Path,
    decision_root: Path,
    output_root: Path,
    g25: dict[str, Any],
    dry_run: bool,
    scratch_root: Path | None,
    batch: str = BATCH_APPROVED,
    evidence_path: Path | None = None,
) -> dict[str, Any]:
    if batch == BATCH_APPROVED:
        usdz = (usd_root / f"{building}.usdz").resolve()
        decision_path = (decision_root / building / "decision.json").resolve()
    elif batch == BATCH_CAPACITY_EVIDENCE:
        if evidence_path is None:
            raise ValueError("capacity-evidence batch requires evidence_path")
        usdz = (usd_root / building / "scene" / "building.usdz").resolve()
        decision_path = evidence_path.resolve()
    else:
        raise ValueError(f"unknown batch: {batch}")
    if not usdz.is_file():
        raise FileNotFoundError(f"source USDZ missing: {usdz}")
    if not decision_path.is_file():
        raise FileNotFoundError(f"lineage evidence missing: {decision_path}")
    if batch == BATCH_APPROVED:
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        if decision.get("decision") != "approve" or decision.get(
            "final_placement_approved") is not True:
            raise ValueError(f"{building} is not authoritatively approved")
    source_sha = _sha256_file(usdz)
    expected_source_sha = (
        str(g25["delivered_usdz_sha256"][building])
        if batch == BATCH_APPROVED
        else str(g25["source"]["building_usdz_sha256"])
    )
    if source_sha != expected_source_sha:
        raise ValueError(
            f"{building} source USDZ SHA-256 differs: {source_sha} != "
            f"{expected_source_sha}")
    decision_sha = _sha256_file(decision_path)
    scratch_path = (
        scratch_root / f"{building}.storey_bands_scratch.npz"
        if scratch_root is not None else None)
    if scratch_path is not None and scratch_path.is_file():
        print(f"{building}: loading development T0 scratch", flush=True)
        extraction = _load_scratch(scratch_path)
    else:
        print(f"{building}: T0 CPU rasterization started", flush=True)
        extraction = rasterize_storey_bands(usdz)
        if scratch_path is not None:
            _save_scratch(scratch_path, extraction)
    if batch == BATCH_CAPACITY_EVIDENCE:
        if extraction.rasterization != g25["rasterization"]:
            raise ValueError(
                f"{building} T0 rasterization fields differ from capacity evidence"
            )
        if _capacity_evidence_source(extraction)["storey_split"] != g25["storey_split"]:
            raise ValueError(
                f"{building} T0 storey_split fields differ from capacity evidence"
            )
    expected_modelling = (
        g25["floor1_modelling"]["per_building"][building]
        if batch == BATCH_APPROVED
        else g25["storeys"]["Floor1"]["modelling_decision"]
    )
    actual_injected = int(extraction.floor1_modelling["new_ground_cells_injected"])
    expected_injected = int(expected_modelling["new_ground_cells_injected"])
    actual_support_z = float(
        extraction.floor1_modelling["rasterized_support_top_z_m"])
    expected_support_z = float(expected_modelling["rasterized_support_top_z_m"])
    if actual_injected != expected_injected or abs(actual_support_z - expected_support_z) > 0.0001:
        raise ValueError(
            f"{building} T0 regression differs: injected {actual_injected} != "
            f"{expected_injected}, support z {actual_support_z} != {expected_support_z}")
    print(
        f"{building}: T0 exact regression passed; T1-T3 extraction started",
        flush=True,
    )
    arrays = build_multistorey_artifact_arrays(
        extraction,
        building=building,
        source_usdz_path=usdz,
        source_usdz_sha256=source_sha,
        decision_path=decision_path,
        decision_sha256=decision_sha,
    )
    if batch == BATCH_CAPACITY_EVIDENCE:
        arrays = _mark_capacity_evidence_only(arrays, extraction)
    manifest = manifest_from_artifact(arrays)
    expected_rows = (
        {
            row["storey"]: row
            for row in g25["rows"]
            if row["building"] == building
        }
        if batch == BATCH_APPROVED
        else g25["storeys"]
    )
    if set(expected_rows) != {"Floor1", "Floor2"}:
        raise ValueError(f"{building} lacks exact two-storey G25 evidence")
    stair_record = manifest["stair_edge"]
    node_centers = np.asarray(arrays["node_center_m"])
    endpoint_errors: dict[str, float] = {}
    for storey, node_key in (
        ("Floor1", "floor1_node_id"), ("Floor2", "floor2_node_id")
    ):
        expected_row = expected_rows[storey]
        actual_area = float(manifest["storeys"][storey]["walkable_area_m2"])
        expected_area = float(expected_row["walkable_area_m2"])
        if abs(actual_area - expected_area) > 0.01 + 1.0e-12:
            raise ValueError(
                f"{building} {storey} walkable area {actual_area} differs from "
                f"{expected_area}")
        endpoint = node_centers[int(stair_record[node_key]), :2]
        expected_xy = np.asarray(
            [expected_row["arrival_x_m"], expected_row["arrival_y_m"]]
            if batch == BATCH_APPROVED
            else expected_row["stair_arrival_xyz_m"][:2],
            dtype=np.float64,
        )
        endpoint_error = float(np.linalg.norm(endpoint - expected_xy))
        endpoint_errors[storey] = endpoint_error
        if (
            endpoint_error > 0.50 + 1.0e-12
            and batch == BATCH_APPROVED
        ):
            raise ValueError(
                f"{building} {storey} stair endpoint error "
                f"{endpoint_error:.6f} m exceeds 0.50 m")
    corridor = corridor_cross_check(arrays)
    corridor_passed = corridor["planner_status"] == "success" and (
        0.5 <= float(corridor["ratio"]) <= 2.0
    )
    if not corridor_passed and batch == BATCH_APPROVED:
        raise ValueError(f"{building} corridor cross-check failed: {corridor}")
    first = deterministic_multistorey_npz_bytes(arrays)
    second = deterministic_multistorey_npz_bytes(arrays)
    if first != second:
        raise AssertionError(f"{building} wrapper serialization differs")
    output_path = output_root / f"{building}.multistorey_topology_v1.npz"
    artifact_sha = sha256(first).hexdigest()
    if not dry_run:
        published_sha = exclusive_publish_bytes(output_path, first)
        if published_sha != artifact_sha:
            raise AssertionError("publisher SHA-256 differs")
    build_outcome = (
        "T1-T3 passed"
        if batch == BATCH_APPROVED
        else f"T1-T3 built; G9 corridor={'PASS' if corridor_passed else 'FAIL'}"
    )
    print(
        f"{building}: {build_outcome}; nodes={len(arrays['node_id'])}, "
        f"edges={len(arrays['edge_id'])}, ratio={corridor['ratio']:.4f}",
        flush=True,
    )
    return {
        "building": building,
        "batch": batch,
        "dry_run": dry_run,
        "output": str(output_path.resolve()),
        "artifact_sha256": artifact_sha,
        "floor1_injected_cells": actual_injected,
        "floor1_walkable_area_m2": manifest["storeys"]["Floor1"]["walkable_area_m2"],
        "floor2_walkable_area_m2": manifest["storeys"]["Floor2"]["walkable_area_m2"],
        "nodes": len(arrays["node_id"]),
        "edges": len(arrays["edge_id"]),
        "stair_endpoint_error_m": endpoint_errors,
        "corridor_cross_check": corridor,
        "corridor_cross_check_passed": corridor_passed,
        "usage": manifest.get("usage"),
        "human_approved": manifest.get("human_approved"),
        "not_for_training": manifest.get("not_for_training"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", choices=BATCHES, default=BATCH_APPROVED)
    parser.add_argument("--building", action="append")
    parser.add_argument("--usd-root", type=Path)
    parser.add_argument("--decision-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--g25", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--scratch-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    capacity_batch = args.batch == BATCH_CAPACITY_EVIDENCE
    batch_buildings = CAPACITY_EVIDENCE_BUILDINGS if capacity_batch else BUILDINGS
    selected = tuple(args.building) if args.building else batch_buildings
    if len(set(selected)) != len(selected):
        raise ValueError("duplicate --building values are forbidden")
    unknown = sorted(set(selected) - set(batch_buildings))
    if unknown:
        raise ValueError(f"buildings are not in {args.batch}: {unknown}")
    usd_root = args.usd_root or (
        DEFAULT_CAPACITY_USD_ROOT if capacity_batch else DEFAULT_USD_ROOT
    )
    decision_root = args.decision_root or DEFAULT_DECISION_ROOT
    output_root = args.output_root or (
        DEFAULT_CAPACITY_OUTPUT_ROOT if capacity_batch else DEFAULT_OUTPUT_ROOT
    )
    evidence_root = args.evidence_root or DEFAULT_CAPACITY_EVIDENCE_ROOT
    approved_g25 = _load_g25(args.g25 or DEFAULT_G25) if not capacity_batch else None
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for building in selected:
        evidence_path = evidence_root / f"{building}.json" if capacity_batch else None
        try:
            evidence = _load_g25(evidence_path) if evidence_path is not None else approved_g25
            assert evidence is not None
            rows.append(build_one(
                building,
                usd_root=usd_root,
                decision_root=decision_root,
                output_root=output_root,
                g25=evidence,
                dry_run=args.dry_run,
                scratch_root=args.scratch_root,
                batch=args.batch,
                evidence_path=evidence_path,
            ))
        except Exception as error:
            failures.append({
                "building": building,
                "reason": f"{type(error).__name__}: {error}",
            })
            print(f"{building}: FAILED: {type(error).__name__}: {error}", flush=True)
    print(json.dumps({"schema": "MultistoreyGraphBuildV1", "rows": rows}, indent=2))
    if failures:
        print(json.dumps({"failures": failures}, indent=2), file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
