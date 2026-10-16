#!/usr/bin/env python3
"""G25 per-storey navigation gate and its sealed-closet negative control."""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ProtoMotions-main"))

import numpy as np
from scipy import ndimage
from pxr import Usd, UsdGeom, UsdPhysics

from protomotions.simulator.base_simulator.scene_geometry_cache import (
    FINE_VOXEL_SIZE_M,
    SceneGeometryGridSpec,
    make_aligned_grid_spec,
)
from protomotions.simulator.base_simulator.scene_storey_bands import (
    _bounded_floor1_terrain as _promoted_bounded_floor1_terrain,
    _crop_grid as _promoted_crop_grid,
    _rasterize_delivered_usdz as _promoted_rasterize_delivered_usdz,
    _split_storeys as _promoted_split_storeys,
)
from protomotions.simulator.base_simulator.standability import (
    CARDINAL_NEIGHBORS,
    StandabilityConfig,
    derive_standability,
)
from protomotions.simulator.isaaclab.utils.scene_geometry_baking import (
    _authored_mesh_geometry_m,
    rasterize_triangle_mesh,
)


CONFIG = StandabilityConfig()
GRID_M = FINE_VOXEL_SIZE_M
MIN_REACHABLE_FROM_STAIR_M2 = 15.0
MAX_UNREACHABLE_STANDABLE_COMPONENT_M2 = 4.0
DEFAULT_BATCH_ROOT = Path(
    r"E:\AI_AGENT_WORKSPACE\PRODUCTS\04_buildings\Final_Physics_Export"
    r"\batch_20260819\validation"
)


def _components(support: np.ndarray) -> tuple[np.ndarray, list[list[tuple[int, int]]]]:
    """Label standable cells using the pinned cardinal/step graph."""
    labels = np.zeros(support.shape, dtype=np.int32)
    components: list[list[tuple[int, int]]] = []
    max_step_cells = int(round(CONFIG.max_support_step_m / GRID_M))
    for x_index, y_index in np.argwhere(support >= 0):
        x = int(x_index)
        y = int(y_index)
        if labels[x, y]:
            continue
        label = len(components) + 1
        labels[x, y] = label
        queue = deque([(x, y)])
        cells: list[tuple[int, int]] = []
        while queue:
            cell = queue.popleft()
            cells.append(cell)
            cell_z = int(support[cell])
            for x_offset, y_offset in CARDINAL_NEIGHBORS:
                neighbor = (cell[0] + x_offset, cell[1] + y_offset)
                if (
                    neighbor[0] < 0
                    or neighbor[0] >= support.shape[0]
                    or neighbor[1] < 0
                    or neighbor[1] >= support.shape[1]
                    or labels[neighbor]
                    or support[neighbor] < 0
                    or abs(int(support[neighbor]) - cell_z) > max_step_cells
                ):
                    continue
                labels[neighbor] = label
                queue.append(neighbor)
        components.append(cells)
    return labels, components


def _headroom_m(occupancy: np.ndarray, support: np.ndarray) -> np.ndarray:
    """Return uncapped vertical headroom above each retained support cell."""
    headroom = np.full(support.shape, np.nan, dtype=np.float32)
    nz = occupancy.shape[2]
    for x_index, y_index in np.argwhere(support >= 0):
        x = int(x_index)
        y = int(y_index)
        first_free = int(support[x, y]) + 1
        blocked = np.flatnonzero(occupancy[x, y, first_free:])
        first_blocked = first_free + int(blocked[0]) if blocked.size else nz
        headroom[x, y] = (first_blocked - first_free) * GRID_M
    return headroom


def _reachable_extent(mask: np.ndarray) -> dict[str, float]:
    """Measure the full cell-union AABB of the stair-reachable set."""
    cells = np.argwhere(mask)
    if not cells.size:
        return {
            "reachable_x_extent_m": 0.0,
            "reachable_y_extent_m": 0.0,
            "reachable_bbox_diagonal_m": 0.0,
        }
    x_extent = (int(cells[:, 0].max()) - int(cells[:, 0].min()) + 1) * GRID_M
    y_extent = (int(cells[:, 1].max()) - int(cells[:, 1].min()) + 1) * GRID_M
    return {
        "reachable_x_extent_m": round(x_extent, 4),
        "reachable_y_extent_m": round(y_extent, 4),
        "reachable_bbox_diagonal_m": round(math.hypot(x_extent, y_extent), 4),
    }


def _maximum_free_radius_m(
    occupancy: np.ndarray,
    support: np.ndarray,
    reachable: np.ndarray,
) -> float | None:
    """Maximum shin/torso horizontal clearance on the reachable support.

    This is the same physical clearance measure used by the earlier mobility
    diagnostic: Euclidean distance to the nearest occupied XY cell at 0.30 m
    and 0.90 m above each cell's own support top, taking the smaller radius.
    It is reported, not used to alter G25's pinned 15 m2 pass criterion.
    """
    cells = np.argwhere(reachable)
    if not cells.size:
        return None
    horizontal = np.empty(occupancy.shape, dtype=np.float32)
    for z_index in range(occupancy.shape[2]):
        horizontal[:, :, z_index] = ndimage.distance_transform_edt(
            ~occupancy[:, :, z_index], sampling=GRID_M).astype(np.float32)
    offsets = (int(round(0.30 / GRID_M)), int(round(0.90 / GRID_M)))
    radii = []
    z_max = occupancy.shape[2] - 1
    for x_index, y_index in cells:
        support_top = int(support[x_index, y_index]) + 1
        samples = [horizontal[x_index, y_index, min(support_top + offset, z_max)]
                   for offset in offsets]
        radii.append(min(float(value) for value in samples))
    return round(max(radii), 4)


def analyze_storey(
    occupancy: np.ndarray,
    grid: SceneGeometryGridSpec,
    stair_arrival_xyz_m: tuple[float, float, float],
    *,
    storey: str,
    enforce_unreachable_component_limit: bool = False,
) -> dict[str, Any]:
    """Evaluate G25 for one storey crop from the exact 5 cm occupancy bake."""
    grid.validate()
    CONFIG.validate(grid.fine_voxel_size_m)
    occupancy = np.asarray(occupancy)
    if occupancy.dtype != np.bool_ or tuple(occupancy.shape) != grid.fine_shape:
        raise ValueError("occupancy must be a bool array matching the 5 cm grid")

    standability = derive_standability(occupancy, grid, CONFIG)
    support = np.asarray(standability.support_z_index)
    labels, components = _components(support)
    cell_area = GRID_M * GRID_M
    origin = np.asarray(grid.scene_bounds_min_m, dtype=np.float64)
    arrival = np.asarray(stair_arrival_xyz_m, dtype=np.float64)

    x_centers = origin[0] + (np.arange(support.shape[0]) + 0.5) * GRID_M
    y_centers = origin[1] + (np.arange(support.shape[1]) + 0.5) * GRID_M
    horizontal_seed = (
        (x_centers[:, None] - arrival[0]) ** 2
        + (y_centers[None, :] - arrival[1]) ** 2
        <= CONFIG.body_radius_m**2 + 1.0e-12
    )
    support_top = np.where(
        support >= 0, origin[2] + (support + 1) * GRID_M, np.nan)
    vertical_seed = (
        np.abs(support_top - arrival[2])
        <= CONFIG.max_support_step_m + GRID_M / 2.0 + 1.0e-12
    )
    seed_mask = (support >= 0) & horizontal_seed & vertical_seed
    reached_labels = sorted(int(value) for value in np.unique(labels[seed_mask]) if value)
    reachable = np.isin(labels, reached_labels)

    headroom = _headroom_m(occupancy, support)
    region_rows = []
    for label, cells in enumerate(components, start=1):
        cell_array = np.asarray(cells, dtype=np.int64)
        region_support = support[cell_array[:, 0], cell_array[:, 1]]
        region_rows.append({
            "region": label,
            "area_m2": round(len(cells) * cell_area, 4),
            "minimum_support_top_m": round(
                float(origin[2] + (region_support.min() + 1) * GRID_M), 4),
            "maximum_support_top_m": round(
                float(origin[2] + (region_support.max() + 1) * GRID_M), 4),
            "reachable_from_stair_arrival": label in reached_labels,
        })
    region_rows.sort(key=lambda row: (-row["area_m2"], row["region"]))

    walkable_area = float(np.count_nonzero(support >= 0)) * cell_area
    reachable_area = float(np.count_nonzero(reachable)) * cell_area
    reachable_extent = _reachable_extent(reachable)
    maximum_free_radius = _maximum_free_radius_m(
        occupancy, support, reachable)
    minimum_headroom = (
        float(np.nanmin(headroom)) if np.any(support >= 0) else None)
    oversized_unreachable = [
        row for row in region_rows
        if (not row["reachable_from_stair_arrival"]
            and float(row["area_m2"])
            > MAX_UNREACHABLE_STANDABLE_COMPONENT_M2 + 1.0e-9)
    ]
    area_passed = reachable_area + 1.0e-9 >= MIN_REACHABLE_FROM_STAIR_M2
    component_passed = not oversized_unreachable
    passed = area_passed and (
        component_passed or not enforce_unreachable_component_limit)
    failure_reasons = [] if area_passed else [
        "reachable_from_stair_area_below_15_m2"
        if reached_labels else "no_standable_stair_arrival_seed"
    ]
    if enforce_unreachable_component_limit and not component_passed:
        failure_reasons.append("stair_unreachable_component_above_4_m2")
    return {
        "gate": "G25_storey_navigation",
        "status": "PASS" if passed else "FAIL",
        "storey": storey,
        "grid_m": GRID_M,
        "standability": {
            "source": "standability.StandabilityConfig",
            "body_radius_m": CONFIG.body_radius_m,
            "body_height_m": CONFIG.body_height_m,
            "max_support_step_m": CONFIG.max_support_step_m,
            "min_component_area_m2": CONFIG.min_component_area_m2,
        },
        "stair_arrival_xyz_m": [float(value) for value in arrival],
        "stair_arrival_seed_cell_count": int(np.count_nonzero(seed_mask)),
        "walkable_area_m2": round(walkable_area, 4),
        "connected_region_count": len(region_rows),
        "connected_regions": region_rows,
        "fragmented": len(region_rows) > 1,
        "fragmentation_is_failure": False,
        "A4_unreachable_component_rule": {
            "enforced": enforce_unreachable_component_limit,
            "maximum_allowed_area_m2": MAX_UNREACHABLE_STANDABLE_COMPONENT_M2,
            "justification": (
                "4.0 m2 is a 2 m by 2 m room-scale standable region, large "
                "enough for translation and turning rather than a raster sliver"
            ),
            "oversized_unreachable_component_count": len(oversized_unreachable),
            "oversized_unreachable_components": oversized_unreachable,
            "pass": component_passed,
        },
        "minimum_headroom_m": (
            round(minimum_headroom, 4) if minimum_headroom is not None else None),
        "area_reachable_from_stair_arrival_m2": round(reachable_area, 4),
        **reachable_extent,
        "maximum_free_radius_on_reachable_m": maximum_free_radius,
        "maximum_free_radius_measurement": (
            "maximum over stair-reachable standable cells of the smaller "
            "Euclidean XY distance-to-occupied at 0.30 m and 0.90 m above "
            "that cell's support top"
        ),
        "minimum_reachable_from_stair_m2": MIN_REACHABLE_FROM_STAIR_M2,
        "failure_reasons": failure_reasons,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _crop_grid(
    grid: SceneGeometryGridSpec,
    occupancy: np.ndarray,
    start_z: int,
    stop_z: int,
) -> tuple[np.ndarray, SceneGeometryGridSpec]:
    """Slice a world-registered Z band and retain complete 10 cm voxels."""
    if not 0 <= start_z < stop_z <= occupancy.shape[2]:
        raise ValueError("invalid storey crop")
    cropped = np.ascontiguousarray(occupancy[:, :, start_z:stop_z])
    if cropped.shape[2] % 2:
        cropped = np.pad(cropped, ((0, 0), (0, 0), (0, 1)))
    origin = np.asarray(grid.scene_bounds_min_m, dtype=np.float64)
    cropped_origin = origin.copy()
    cropped_origin[2] += start_z * GRID_M
    scene_max = np.asarray(grid.scene_bounds_max_m, dtype=np.float64)
    # A storey crop retains the complete registered XY bake.  Its at-most-one
    # coarse-voxel alignment band is part of that raster window; occupancy still
    # controls support, so this does not synthesize floor outside the mesh.
    cropped_scene_max = np.asarray(grid.grid_bounds_max_m, dtype=np.float64)
    cropped_scene_max[2] = min(
        scene_max[2], origin[2] + stop_z * GRID_M)
    grid_max = cropped_origin + np.asarray(cropped.shape) * GRID_M
    cropped_grid = SceneGeometryGridSpec(
        scene_bounds_min_m=tuple(float(value) for value in cropped_origin),
        scene_bounds_max_m=tuple(float(value) for value in cropped_scene_max),
        grid_bounds_max_m=tuple(float(value) for value in grid_max),
        fine_shape=tuple(int(value) for value in cropped.shape),
        coarse_shape=tuple(int(value // 2) for value in cropped.shape),
    )
    cropped_grid.validate()
    return cropped, cropped_grid


def _rasterize_delivered_usdz(
    usdz_path: Path,
) -> tuple[np.ndarray, np.ndarray, SceneGeometryGridSpec, dict[str, Any]]:
    """Rasterize every enabled delivered mesh collider, including guides."""
    stage = Usd.Stage.Open(str(usdz_path))
    if stage is None:
        raise ValueError(f"could not open delivered USDZ: {usdz_path}")
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage))
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    geometry: list[tuple[np.ndarray, np.ndarray]] = []
    stair_geometry: list[tuple[np.ndarray, np.ndarray]] = []
    collider_paths: list[str] = []
    bounds_min = np.full(3, np.inf, dtype=np.float64)
    bounds_max = np.full(3, -np.inf, dtype=np.float64)
    for prim in stage.Traverse():
        if not (prim.IsA(UsdGeom.Mesh) and prim.HasAPI(UsdPhysics.CollisionAPI)):
            continue
        collision = UsdPhysics.CollisionAPI(prim)
        if collision.GetCollisionEnabledAttr().Get() is False:
            continue
        try:
            vertices, faces = _authored_mesh_geometry_m(
                stage, prim, meters_per_unit, xform_cache)
        except Exception as error:
            raise ValueError(
                f"enabled collider extraction failed for {prim.GetPath()}: {error}"
            ) from error
        geometry.append((vertices, faces))
        path = str(prim.GetPath())
        collider_paths.append(path)
        if path == "/World/Stair" or path.startswith("/World/Stair/"):
            stair_geometry.append((vertices, faces))
        bounds_min = np.minimum(bounds_min, vertices.min(axis=0))
        bounds_max = np.maximum(bounds_max, vertices.max(axis=0))
    if not geometry or not stair_geometry:
        raise ValueError("delivered stage lacks enabled mesh or stair colliders")

    grid = make_aligned_grid_spec(bounds_min, bounds_max)
    occupancy = np.zeros(grid.fine_shape, dtype=np.bool_)
    stair = np.zeros(grid.fine_shape, dtype=np.bool_)
    for vertices, faces in geometry:
        rasterize_triangle_mesh(occupancy, grid, vertices, faces, fill=False)
    for vertices, faces in stair_geometry:
        rasterize_triangle_mesh(stair, grid, vertices, faces, fill=False)
    return occupancy, stair, grid, {
        "enabled_mesh_collider_count": len(collider_paths),
        "enabled_stair_mesh_collider_count": len(stair_geometry),
        "grid_scene_bounds_min_m": list(grid.scene_bounds_min_m),
        "grid_scene_bounds_max_m": list(grid.scene_bounds_max_m),
        "grid_fine_shape": list(grid.fine_shape),
    }


def _split_storeys(
    occupancy: np.ndarray,
    grid: SceneGeometryGridSpec,
) -> tuple[int, int, dict[str, Any]]:
    """Locate the F2 slab and its lower boundary using the specified density cut."""
    origin_z = float(grid.scene_bounds_min_m[2])
    centers_z = origin_z + (np.arange(occupancy.shape[2]) + 0.5) * GRID_M
    layer_counts = occupancy.sum(axis=(0, 1), dtype=np.int64)
    candidates = np.flatnonzero(centers_z > 2.0)
    if not candidates.size:
        raise ValueError("no occupancy layer above 2 m")
    peak_z = int(candidates[np.argmax(layer_counts[candidates])])
    if int(layer_counts[peak_z]) == 0:
        raise ValueError("no dense Floor2 slab layer")
    threshold = float(layer_counts[peak_z]) * 0.35
    cut_z = peak_z
    while cut_z > 0 and float(layer_counts[cut_z - 1]) > threshold:
        cut_z -= 1
    return peak_z, cut_z, {
        "method": (
            "densest occupied layer above 2 m; walk downward while the "
            "preceding layer exceeds 35% of peak density"
        ),
        "peak_layer_index": peak_z,
        "peak_layer_center_z_m": round(float(centers_z[peak_z]), 4),
        "peak_layer_occupied_cell_count": int(layer_counts[peak_z]),
        "density_walkdown_threshold_cell_count": round(threshold, 4),
        "slab_bottom_layer_index": cut_z,
        "slab_bottom_z_m": round(origin_z + cut_z * GRID_M, 4),
    }


def _floor2_arrival(
    occupancy: np.ndarray,
    stair: np.ndarray,
    grid: SceneGeometryGridSpec,
    peak_z: int,
    floor2_occupancy: np.ndarray,
    floor2_grid: SceneGeometryGridSpec,
) -> tuple[tuple[float, float, float], dict[str, Any]]:
    """Return the highest exposed stair support that can seed standability."""
    origin = np.asarray(grid.scene_bounds_min_m, dtype=np.float64)
    centers_z = origin[2] + (np.arange(occupancy.shape[2]) + 0.5) * GRID_M
    slab_center_z = float(centers_z[peak_z])
    candidates: list[tuple[int, int, int]] = []
    for x_index, y_index in np.argwhere(stair.any(axis=2)):
        x = int(x_index)
        y = int(y_index)
        for z_index in np.flatnonzero(stair[x, y, :]):
            z = int(z_index)
            if (
                z + 1 < occupancy.shape[2]
                and not occupancy[x, y, z + 1]
                and float(centers_z[z]) <= slab_center_z + 0.06
            ):
                candidates.append((x, y, z))
    if not candidates:
        raise ValueError("no exposed Floor2 stair-arrival support found")
    candidates.sort(key=lambda cell: (-cell[2], cell[0], cell[1]))

    standability = derive_standability(floor2_occupancy, floor2_grid, CONFIG)
    support = np.asarray(standability.support_z_index)
    floor2_origin = np.asarray(
        floor2_grid.scene_bounds_min_m, dtype=np.float64)
    x_centers = floor2_origin[0] + (np.arange(support.shape[0]) + 0.5) * GRID_M
    y_centers = floor2_origin[1] + (np.arange(support.shape[1]) + 0.5) * GRID_M
    support_top = np.where(
        support >= 0, floor2_origin[2] + (support + 1) * GRID_M, np.nan)
    for rank, candidate in enumerate(candidates, start=1):
        arrival = (
            float(origin[0] + (candidate[0] + 0.5) * GRID_M),
            float(origin[1] + (candidate[1] + 0.5) * GRID_M),
            float(origin[2] + (candidate[2] + 1) * GRID_M),
        )
        horizontal = (
            (x_centers[:, None] - arrival[0]) ** 2
            + (y_centers[None, :] - arrival[1]) ** 2
            <= CONFIG.body_radius_m**2 + 1.0e-12
        )
        vertical = (
            np.abs(support_top - arrival[2])
            <= CONFIG.max_support_step_m + GRID_M / 2.0 + 1.0e-12
        )
        seed_count = int(np.count_nonzero((support >= 0) & horizontal & vertical))
        if seed_count:
            return arrival, {
                "candidate_order": "descending support z, then x index, then y index",
                "candidate_rank_selected": rank,
                "higher_or_earlier_candidates_rejected_without_seed": rank - 1,
                "candidate_seed_cell_count": seed_count,
            }
    raise ValueError(
        "no exposed Floor2 stair-arrival candidate can seed standability")


def _floor1_approach_xy(building_dir: Path) -> tuple[tuple[float, float], dict[str, Any]]:
    """Read the last delivered Floor1 path point before the first stair sample."""
    report_path = building_dir / "reports" / "isaac_validation.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    samples = report.get("samples")
    if not isinstance(samples, list):
        raise ValueError(f"missing delivered Isaac path samples: {report_path}")
    first_stair = next(
        (index for index, row in enumerate(samples) if row.get("seg") == "stair"),
        None,
    )
    if first_stair is None:
        raise ValueError("delivered Isaac path contains no stair samples")
    approach = next(
        (
            samples[index]
            for index in range(first_stair - 1, -1, -1)
            if samples[index].get("seg") == "floor1"
        ),
        None,
    )
    if approach is None:
        raise ValueError("delivered Isaac path contains no Floor1 approach sample")
    xyz = approach.get("xyz")
    if not isinstance(xyz, list) or len(xyz) != 3:
        raise ValueError("Floor1 approach sample has no xyz triplet")
    return (float(xyz[0]), float(xyz[1])), {
        "source": "delivered reports/isaac_validation.json",
        "source_sha256": _sha256_file(report_path),
        "rule": "last seg=floor1 sample before first seg=stair sample",
        "source_xyz_m": [float(value) for value in xyz],
    }


def _bounded_floor1_terrain(
    occupancy: np.ndarray,
    grid: SceneGeometryGridSpec,
    peak_z: int,
) -> tuple[np.ndarray, float, dict[str, Any]]:
    """Inject a z=0-equivalent support only inside the delivered slab footprint."""
    raw_footprint = np.asarray(occupancy[:, :, peak_z], dtype=np.bool_)
    # The Floor2 stair cut is an interior hole, not an outdoor boundary. Filling
    # enclosed holes gives Floor1 its real continuous ground while preserving the
    # delivered slab's finite outer boundary, so the flood cannot escape outside.
    bounded_footprint = np.asarray(
        ndimage.binary_fill_holes(raw_footprint), dtype=np.bool_)
    if not np.any(bounded_footprint):
        raise ValueError("could not derive bounded building footprint")
    origin_z = float(grid.scene_bounds_min_m[2])
    support_tops = origin_z + (np.arange(occupancy.shape[2]) + 1) * GRID_M
    terrain_z = int(np.argmin(np.abs(support_tops)))
    result = occupancy.copy()
    layer = result[:, :, terrain_z]
    before = int(np.count_nonzero(layer))
    layer[bounded_footprint] = True
    support_top_z = float(support_tops[terrain_z])
    return result, support_top_z, {
        "decision": "SCORABLE_WITH_BOUNDED_INJECTED_TERRAIN",
        "nominal_terrain_z_m": 0.0,
        "rasterized_support_top_z_m": round(support_top_z, 4),
        "footprint_source": "densest delivered Floor2 slab layer",
        "footprint_hole_rule": "fill enclosed holes; preserve finite exterior boundary",
        "raw_footprint_cell_count": int(np.count_nonzero(raw_footprint)),
        "bounded_footprint_cell_count": int(np.count_nonzero(bounded_footprint)),
        "new_ground_cells_injected": int(np.count_nonzero(layer)) - before,
        "justification": (
            "Floor1 has no delivered slab collider. A finite support surface is "
            "injected only under the building footprint inferred from delivered "
            "geometry; there is no unbounded exterior terrain to flood into."
        ),
    }


# T0 promotion: keep the original definitions above as an auditable source
# regression reference, while all G25 execution goes through the reusable
# simulator-neutral module.
_rasterize_delivered_usdz = _promoted_rasterize_delivered_usdz
_split_storeys = _promoted_split_storeys
_crop_grid = _promoted_crop_grid
_bounded_floor1_terrain = _promoted_bounded_floor1_terrain


def run_building(
    building: str,
    batch_root: Path = DEFAULT_BATCH_ROOT,
    stage_path: Path | None = None,
    enforce_unreachable_component_limit: bool = False,
) -> dict[str, Any]:
    """Run G25 on both storeys of one delivered building using CPU rasterization."""
    building_dir = batch_root / building
    geometry_path = (stage_path.resolve() if stage_path is not None
                     else building_dir / "scene" / "building.usdz")
    if not geometry_path.is_file():
        raise FileNotFoundError(f"delivered stage not found: {geometry_path}")
    occupancy, stair, grid, raster = _rasterize_delivered_usdz(geometry_path)
    peak_z, cut_z, split = _split_storeys(occupancy, grid)

    floor2_occupancy, floor2_grid = _crop_grid(
        grid, occupancy, cut_z, occupancy.shape[2])
    floor2_arrival, floor2_arrival_selection = _floor2_arrival(
        occupancy, stair, grid, peak_z, floor2_occupancy, floor2_grid)
    floor2 = analyze_storey(
        floor2_occupancy, floor2_grid, floor2_arrival, storey="Floor2",
        enforce_unreachable_component_limit=enforce_unreachable_component_limit)
    floor2["arrival_source"] = {
        "source": "delivered USDZ stair collider raster",
        "rule": (
            "first seedable candidate among exposed stair supports at/below "
            "the Floor2 slab"
        ),
        "coordinate_semantics": "world metres; z is support top, never voxel center",
    } | floor2_arrival_selection

    with_terrain, floor1_support_top, modelling = _bounded_floor1_terrain(
        occupancy, grid, peak_z)
    floor1_occupancy, floor1_grid = _crop_grid(
        grid, with_terrain, 0, min(cut_z + 1, occupancy.shape[2]))
    floor1_xy, floor1_arrival_source = _floor1_approach_xy(building_dir)
    floor1_arrival = (floor1_xy[0], floor1_xy[1], floor1_support_top)
    floor1 = analyze_storey(
        floor1_occupancy, floor1_grid, floor1_arrival, storey="Floor1",
        enforce_unreachable_component_limit=enforce_unreachable_component_limit)
    floor1["arrival_source"] = floor1_arrival_source | {
        "coordinate_semantics": (
            "XY is in world metres; z is the injected terrain support top"
        )
    }
    floor1["modelling_decision"] = modelling

    return {
        "schema": "G25DeliveredBuildingV1",
        "building": building,
        "status": (
            "PASS" if floor1["status"] == floor2["status"] == "PASS" else "FAIL"
        ),
        "source": {
            "building_usdz": str((building_dir / "scene" / "building.usdz").resolve()),
            "building_usdz_sha256": _sha256_file(
                building_dir / "scene" / "building.usdz"),
            "evaluated_root_layer": str(geometry_path),
            "evaluated_root_layer_sha256": _sha256_file(geometry_path),
            "geometry": (
                "enabled mesh colliders in the delivered USDZ"
                if stage_path is None else
                "enabled mesh colliders in the explicitly supplied composed root layer"
            ),
            "rasterizer": "scene_geometry_baking.rasterize_triangle_mesh(fill=False)",
            "device": "CPU",
        },
        "rasterization": raster,
        "storey_split": split | {
            "Floor2": "slab-bottom layer and above",
            "Floor1": "below slab plus duplicated slab-bottom layer as ceiling",
        },
        "storeys": {"Floor1": floor1, "Floor2": floor2},
    }


def sealed_closet_fixture(*, door_open: bool) -> tuple[
        np.ndarray, SceneGeometryGridSpec, tuple[float, float, float]]:
    """Make a 12x10 m storey whose stair arrives inside a 2.6 m closet."""
    grid = make_aligned_grid_spec((0.0, 0.0, 0.0), (12.0, 10.0, 2.5))
    occupancy = np.zeros(grid.fine_shape, dtype=np.bool_)
    occupancy[:, :, 0] = True
    wall_top = int(round(2.2 / GRID_M))
    occupancy[:2, :, 1:wall_top] = True
    occupancy[-2:, :, 1:wall_top] = True
    occupancy[:, :2, 1:wall_top] = True
    occupancy[:, -2:, 1:wall_top] = True

    x0, x1, y0, y1 = 36, 92, 46, 102
    occupancy[x0:x0 + 2, y0:y1 + 2, 1:wall_top] = True
    occupancy[x1:x1 + 2, y0:y1 + 2, 1:wall_top] = True
    occupancy[x0:x1 + 2, y0:y0 + 2, 1:wall_top] = True
    occupancy[x0:x1 + 2, y1:y1 + 2, 1:wall_top] = True
    if door_open:
        half_gap = 9  # 0.90 m authored gap; > 0.50 m body diameter.
        center = (y0 + y1) // 2
        occupancy[x1:x1 + 2, center - half_gap:center + half_gap,
                  1:wall_top] = False

    arrival = (
        (x0 + x1 + 2) * 0.5 * GRID_M,
        (y0 + y1 + 2) * 0.5 * GRID_M,
        GRID_M,
    )
    return occupancy, grid, arrival


def run_fixture(name: str) -> dict[str, Any]:
    door_open = name == "open_door_control"
    occupancy, grid, arrival = sealed_closet_fixture(door_open=door_open)
    result = analyze_storey(
        occupancy, grid, arrival, storey="fixture_storey")
    expected = "PASS" if door_open else "FAIL"
    return {
        "fixture": name,
        "description": (
            "same large storey; stair arrival is inside the closet; only the "
            "control has a 0.90 m doorway"),
        "expected_status": expected,
        "observed_status": result["status"],
        "fixture_expectation_met": result["status"] == expected,
        "G25": result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--fixture", choices=("sealed_closet", "open_door_control"))
    source.add_argument("--building")
    parser.add_argument("--batch-root", type=Path, default=DEFAULT_BATCH_ROOT)
    parser.add_argument(
        "--stage-path", type=Path,
        help="optional composed root layer (for example a non-destructive overlay)",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--enforce-unreachable-component-limit", action="store_true",
        help="enforce the new-batch A4 rule: no unreachable component above 4 m2",
    )
    args = parser.parse_args(argv)
    report = (
        run_fixture(args.fixture)
        if args.fixture is not None
        else run_building(
            args.building, args.batch_root, args.stage_path,
            args.enforce_unreachable_component_limit)
    )
    payload = json.dumps(report, indent=2) + "\n"
    if args.output:
        if args.output.exists():
            raise FileExistsError("refusing to overwrite immutable evidence: " + str(args.output))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if args.fixture is not None:
        return 0 if report["fixture_expectation_met"] else 1
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
