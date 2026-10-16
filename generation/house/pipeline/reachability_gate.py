#!/usr/bin/env python3
"""G24: exact stair rises plus ProtoMotions support-connectivity corroboration."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage

from protomotions.simulator.base_simulator.scene_geometry_cache import (
    load_scene_geometry_cache,
)
from protomotions.simulator.base_simulator.standability import StandabilityConfig
from stair_rise_contract import measure_stair_rises


CONFIG = StandabilityConfig()
GROUND_Z_M = 0.0
TORSO_HEIGHT_M = 0.90
SHIN_HEIGHT_M = 0.30
PARTIAL_HEADROOM_M = 0.50


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _standable(
        occupancy: np.ndarray,
        lower_m: np.ndarray,
        voxel_m: float,
        ground_z_m: float,
) -> tuple[np.ndarray, np.ndarray, int]:
    nx, ny, nz = occupancy.shape
    centers_z = lower_m[2] + (np.arange(nz) + 0.5) * voxel_m
    occupied = np.array(occupancy, dtype=bool, copy=True)
    # Restore the flat simulator terrain explicitly.  A collider-only scene
    # cache is not allowed to make the ground-to-first-tread transition vanish.
    occupied[:, :, centers_z - voxel_m / 2.0 <= ground_z_m + 1e-9] = True

    horizontal_clearance = np.empty(occupied.shape, dtype=np.float32)
    for level in range(nz):
        horizontal_clearance[:, :, level] = ndimage.distance_transform_edt(
            ~occupied[:, :, level], sampling=voxel_m).astype(np.float32)

    free_run = np.zeros((nx, ny), dtype=np.float32)
    free_above = np.zeros(occupied.shape, dtype=np.float32)
    for level in range(nz - 1, -1, -1):
        free_run = np.where(~occupied[:, :, level],
                            free_run + voxel_m, 0.0)
        free_above[:, :, level] = free_run
    headroom = np.zeros(occupied.shape, dtype=np.float32)
    headroom[:, :, :-1] = free_above[:, :, 1:]

    support = np.zeros(occupied.shape, dtype=bool)
    support[:, :, :-1] = occupied[:, :, :-1] & ~occupied[:, :, 1:]
    levels = np.arange(nz)
    torso = np.minimum(
        levels + int(round(TORSO_HEIGHT_M / voxel_m)), nz - 1)
    shin = np.minimum(
        levels + int(round(SHIN_HEIGHT_M / voxel_m)), nz - 1)
    wide = (
        (horizontal_clearance[:, :, torso] >= CONFIG.body_radius_m - 1e-6)
        & (horizontal_clearance[:, :, shin] >= CONFIG.body_radius_m - 1e-6)
    )
    standable = support & wide & (headroom >= CONFIG.body_height_m - 1e-6)
    ground_levels = np.nonzero(
        centers_z - voxel_m / 2.0 <= ground_z_m + 1e-9)[0]
    if not len(ground_levels):
        raise ValueError("restored terrain plane is outside the cache grid")
    return standable, centers_z, int(ground_levels.max())


def _step_structure(voxel_m: float) -> tuple[np.ndarray, int, int]:
    # The conservative triangle rasterizer can move a support top up by one
    # fine voxel relative to the restored terrain grid.  Without this single-
    # cell allowance the same authored 0.18 m rise is represented as either
    # four or five cells solely from the cache-origin phase.  Gate A remains
    # the exact <=0.20 m geometry check; this allowance is only for Gate B's
    # topology corroboration.
    nominal_cells = int(np.ceil(CONFIG.max_support_step_m / voxel_m - 1e-9))
    quantization_slack_cells = 1
    max_step_cells = nominal_cells + quantization_slack_cells
    structure = np.zeros((3, 3, 2 * max_step_cells + 1), dtype=bool)
    structure[1, 1, :] = True
    structure[0, 1, :] = True
    structure[2, 1, :] = True
    structure[1, 0, :] = True
    structure[1, 2, :] = True
    return structure, nominal_cells, quantization_slack_cells


def connectivity(
        cache_path: Path,
        upper_slab_top_m: float,
        *,
        ground_z_m: float = GROUND_Z_M,
) -> dict[str, Any]:
    cache_path = cache_path.resolve()
    loaded = load_scene_geometry_cache(cache_path, verify_hashes=True)
    occupancy = np.asarray(loaded.occupancy, dtype=bool)
    grid = loaded.manifest["grid"]
    lower_m = np.asarray(grid["scene_bounds_min_m"], dtype=np.float64)
    voxel_m = float(grid.get("fine_voxel_size_m", 0.05))
    if ground_z_m < lower_m[2]:
        pad = int(np.ceil((lower_m[2] - ground_z_m) / voxel_m)) + 1
        occupancy = np.concatenate([
            np.zeros(occupancy.shape[:2] + (pad,), dtype=bool), occupancy,
        ], axis=2)
        lower_m = lower_m.copy()
        lower_m[2] -= pad * voxel_m

    standable, centers_z, ground_level = _standable(
        occupancy, lower_m, voxel_m, ground_z_m)
    seed = np.zeros_like(standable)
    seed[:, :, ground_level] = standable[:, :, ground_level]
    step_structure, nominal_cells, quantization_slack_cells = _step_structure(
        voxel_m)
    reached = ndimage.binary_dilation(
        seed, structure=step_structure, mask=standable,
        iterations=-1)
    support_top_z = centers_z + voxel_m / 2.0
    upper_levels = np.nonzero(
        np.abs(support_top_z - float(upper_slab_top_m))
        <= max(0.15, 2.5 * voxel_m))[0]
    upper_cells = int(reached[:, :, upper_levels].sum()) if len(upper_levels) else 0
    passed = bool(seed.any() and upper_cells > 0)
    return {
        "status": "PASS" if passed else "FAIL",
        "cache": str(cache_path),
        "cache_manifest_sha256": _sha256(cache_path / "manifest.json"),
        "hashes_verified": True,
        "ground_plane_z_m": float(ground_z_m),
        "upper_slab_top_m": float(upper_slab_top_m),
        "fine_voxel_size_m": voxel_m,
        "max_support_step_m": CONFIG.max_support_step_m,
        "max_support_step_cells": nominal_cells,
        "rasterization_quantization_slack_cells": quantization_slack_cells,
        "effective_connectivity_step_cells": (
            nominal_cells + quantization_slack_cells),
        "connectivity_slack_scope": (
            "one fine voxel for conservative-raster/cache-origin phase only; "
            "exact authored rises remain bounded by max_support_step_m in A"
        ),
        "cardinal_neighbors_only": True,
        "ground_seed_cell_count": int(seed.sum()),
        "reachable_support_cell_count": int(reached.sum()),
        "reachable_upper_storey_cell_count": upper_cells,
        "upper_storey_reachable": passed,
    }


def audit_reachability(
        stage_path: Path,
        build_report_path: Path,
        cache_path: Path,
        *,
        control_stage_path: Path,
        control_build_report_path: Path,
        control_cache_path: Path,
) -> dict[str, Any]:
    build = json.loads(build_report_path.read_text(encoding="utf-8-sig"))
    upper = float(build["floor_to_floor_m"])
    exact = measure_stair_rises(stage_path, upper)
    flooded = connectivity(cache_path, upper)

    control_build = json.loads(
        control_build_report_path.read_text(encoding="utf-8-sig"))
    control_upper = float(control_build["floor_to_floor_m"])
    control_exact = measure_stair_rises(control_stage_path, control_upper)
    control_flooded = connectivity(control_cache_path, control_upper)
    control_failed = bool(
        not control_exact["passes"]
        and not control_flooded["upper_storey_reachable"])
    passed = bool(exact["passes"] and flooded["upper_storey_reachable"]
                  and control_failed)
    return {
        "status": "PASS" if passed else "FAIL",
        "definition": (
            "A: exact authored ground/tread/slab rises are <= ProtoMotions "
            "max_support_step_m; B: cardinal-neighbor support flood from the "
            "restored z=0 terrain reaches the upper storey"
        ),
        "robot_model": {
            "source": "standability.StandabilityConfig",
            "body_radius_m": CONFIG.body_radius_m,
            "body_height_m": CONFIG.body_height_m,
            "max_support_step_m": CONFIG.max_support_step_m,
        },
        "A_authored_geometry_exact": exact,
        "B_baked_connectivity": flooded,
        "negative_control": {
            "kind": "preserved_pre_generator-fix_building_and_cache",
            "A_authored_geometry_exact": control_exact,
            "B_baked_connectivity": control_flooded,
            "correctly_fails_both": control_failed,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--build-report", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--control-stage", required=True, type=Path)
    parser.add_argument("--control-build-report", required=True, type=Path)
    parser.add_argument("--control-cache", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = audit_reachability(
        args.stage.resolve(), args.build_report.resolve(), args.cache.resolve(),
        control_stage_path=args.control_stage.resolve(),
        control_build_report_path=args.control_build_report.resolve(),
        control_cache_path=args.control_cache.resolve(),
    )
    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        if args.output.exists():
            raise FileExistsError("refusing to overwrite: " + str(args.output))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
