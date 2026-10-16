#!/usr/bin/env python3
"""CPU-only strict-door counterfactual and minimum-barrier attribution.

Delivered USDZ files are opened read-only.  The doorless counterfactual is a
fresh in-memory raster excluding only leaf/handle descendants of authored
doorway/doorframe roots; it never
edits a layer or package.  For each component still sealed, a lexicographic
minimum-removal path is solved on the 5 cm body-centre grid, converted to the
raw body-band cells that must be freed, and verified by re-running standability.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import heapq
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
from scipy import ndimage
from pxr import Usd, UsdGeom, UsdPhysics

from diagnose_g25_failures import _standability_state
from gate25_storey_navigation import (
    CARDINAL_NEIGHBORS,
    CONFIG,
    DEFAULT_BATCH_ROOT,
    GRID_M,
    _bounded_floor1_terrain,
    _crop_grid,
    _floor1_approach_xy,
    _floor2_arrival,
    _split_storeys,
)
from protomotions.simulator.base_simulator.scene_geometry_cache import (
    make_aligned_grid_spec,
)
from protomotions.simulator.isaaclab.utils.scene_geometry_baking import (
    _authored_mesh_geometry_m,
    rasterize_triangle_mesh,
)


BUILDINGS = (
    "home_train54_train84_ushaped_v001_h320",
    "home_train138_train104_straight_v001_h320",
    "home_train104_train160_straight_v001_h320",
    "home_train178_train132_straight_v001_h270",
    "home_train160_train92_straight_v001_h320",
)
STRICT_DOOR_MEMBER_RE = re.compile(
    r"_(?:doorway|doorframe)_(?:door|handle)(?:_|/|$)", re.I
)
APPLIANCE_ROOT_RE = re.compile(
    r"(?:^|_)(?:fridge|refrigerator|microwave|oven|dishwasher|"
    r"washing_?machine|dryer)(?:_|$)", re.I
)


@dataclass
class Collider:
    path: str
    prim_name: str
    vertices: np.ndarray
    faces: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    is_door_panel: bool
    is_tall_thin_leaf: bool
    is_repair_protected: bool


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def object_root_name(path: str) -> str:
    parts = path.split("/")
    if (len(parts) > 4 and parts[1] == "World"
            and parts[2] in ("Floor1", "Floor2") and parts[3] == "Geometry"):
        return parts[4]
    return ""


def full_door_panel_member(path: str, prim_name: str) -> bool:
    """Select only leaf/handle branches under doorway/doorframe roots.

    Frames and jambs remain physical. Appliance doors are necessarily outside
    the selected object roots and cannot enter this counterfactual.
    """
    root = object_root_name(path).lower()
    return bool(
        root.startswith(("doorway_", "doorframe_"))
        and STRICT_DOOR_MEMBER_RE.search(path)
    )


def repair_protected_root(path: str) -> bool:
    """Keep door/frame and appliance roots physical but ineligible for cuts."""
    root = object_root_name(path)
    return bool(
        root.lower().startswith(("doorway_", "doorframe_", "parc_storage_crate_"))
        or APPLIANCE_ROOT_RE.search(root)
    )


def collect_colliders(usdz: Path) -> tuple[Usd.Stage, list[Collider]]:
    stage = Usd.Stage.Open(str(usdz))
    if stage is None:
        raise ValueError(f"cannot open delivered USDZ: {usdz}")
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    xforms = UsdGeom.XformCache(Usd.TimeCode.Default())
    rows = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh) or not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:
            continue
        vertices, faces = _authored_mesh_geometry_m(stage, prim, mpu, xforms)
        path = str(prim.GetPath())
        extent = vertices.max(axis=0) - vertices.min(axis=0)
        member = full_door_panel_member(path, prim.GetName())
        tall_thin = bool(
            member and extent[2] > 1.5 and min(extent[0], extent[1]) < 0.15
            and "door" in prim.GetName().lower()
            and "handle" not in prim.GetName().lower())
        rows.append(Collider(
            path=path,
            prim_name=prim.GetName(),
            vertices=vertices,
            faces=faces,
            lower=vertices.min(axis=0),
            upper=vertices.max(axis=0),
            is_door_panel=member,
            is_tall_thin_leaf=tall_thin,
            is_repair_protected=repair_protected_root(path),
        ))
    if not rows:
        raise ValueError(f"no enabled mesh colliders: {usdz}")
    return stage, rows


def rasterize(
    rows: list[Collider],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, Any]:
    lower = np.minimum.reduce([row.lower for row in rows])
    upper = np.maximum.reduce([row.upper for row in rows])
    grid = make_aligned_grid_spec(lower, upper)
    non_door = np.zeros(grid.fine_shape, dtype=np.bool_)
    door = np.zeros(grid.fine_shape, dtype=np.bool_)
    stair = np.zeros(grid.fine_shape, dtype=np.bool_)
    repair_protected = np.zeros(grid.fine_shape, dtype=np.bool_)
    for row in rows:
        target = door if row.is_door_panel else non_door
        rasterize_triangle_mesh(target, grid, row.vertices, row.faces, fill=False)
        if row.path == "/World/Stair" or row.path.startswith("/World/Stair/"):
            rasterize_triangle_mesh(stair, grid, row.vertices, row.faces, fill=False)
        if row.is_repair_protected and not row.is_door_panel:
            rasterize_triangle_mesh(
                repair_protected, grid, row.vertices, row.faces, fill=False
            )
    return non_door | door, non_door, stair, repair_protected, grid


def storey_state(
    building_dir: Path,
    occupancy: np.ndarray,
    stair: np.ndarray,
    grid: Any,
    peak_z: int,
    cut_z: int,
    storey: str,
) -> tuple[np.ndarray, Any, tuple[float, float, float], dict[str, Any]]:
    if storey == "Floor2":
        cropped, cropped_grid = _crop_grid(
            grid, occupancy, cut_z, occupancy.shape[2])
        arrival, _ = _floor2_arrival(
            occupancy, stair, grid, peak_z, cropped, cropped_grid)
    else:
        with_terrain, arrival_z, _ = _bounded_floor1_terrain(
            occupancy, grid, peak_z)
        cropped, cropped_grid = _crop_grid(
            grid, with_terrain, 0, min(cut_z + 1, occupancy.shape[2]))
        arrival_xy, _ = _floor1_approach_xy(building_dir)
        arrival = (arrival_xy[0], arrival_xy[1], arrival_z)
    state = _standability_state(cropped, cropped_grid, arrival)
    return cropped, cropped_grid, arrival, state


def state_summary(state: dict[str, Any]) -> dict[str, Any]:
    support = state["support"]
    return {
        "reachable_area_m2": round(
            int(np.count_nonzero(state["reachable"])) * GRID_M * GRID_M, 4),
        "walkable_area_m2": round(
            int(np.count_nonzero(support >= 0)) * GRID_M * GRID_M, 4),
        "connected_component_count": len(state["components"]),
        "component_areas_m2": [
            round(len(cells) * GRID_M * GRID_M, 4)
            for cells in state["components"]
        ],
    }


def disk(radius_cells: int) -> np.ndarray:
    axis = np.arange(-radius_cells, radius_cells + 1)
    return (axis[:, None] ** 2 + axis[None, :] ** 2 <= radius_cells**2)


def floor_domain(occupancy: np.ndarray, state: dict[str, Any]) -> np.ndarray:
    support = state["support"]
    valid_support = support[support >= 0]
    if not valid_support.size:
        raise ValueError("storey has no support")
    base = int(np.median(valid_support))
    lo = max(0, base - 2)
    hi = min(occupancy.shape[2], base + 2)
    counts = occupancy[:, :, lo:hi].sum(axis=(0, 1))
    layer = lo + int(np.argmax(counts))
    slab = occupancy[:, :, layer]
    filled = np.asarray(ndimage.binary_fill_holes(slab), dtype=np.bool_)
    # Retain every known support cell even if a thin/fragmented slab raster did
    # not close perfectly.  A one-cell closing joins lattice-phase pinholes but
    # cannot bridge a real wall or exterior gap.
    return filled | ndimage.binary_closing(
        support >= 0, structure=np.ones((3, 3), dtype=np.bool_))


def minimum_removal_path(
    occupancy: np.ndarray,
    state: dict[str, Any],
    target: np.ndarray,
    forbidden_plan_mask: np.ndarray,
) -> dict[str, Any]:
    source = np.asarray(state["reachable"], dtype=np.bool_)
    target = np.asarray(target, dtype=np.bool_) & (state["support"] >= 0)
    if not np.any(source) or not np.any(target):
        raise ValueError("minimum cut source or target is empty")
    support_values = np.concatenate((
        state["support"][source], state["support"][target]))
    base = int(np.median(support_values[support_values >= 0]))
    body_cells = int(round(CONFIG.body_height_m / GRID_M))
    z0 = base + 1
    z1 = min(z0 + body_cells, occupancy.shape[2])
    raw_blocked = occupancy[:, :, z0:z1].any(axis=2)
    radius_cells = int(math.ceil(CONFIG.body_radius_m / GRID_M))
    center_blocked = ndimage.binary_dilation(
        raw_blocked, structure=disk(radius_cells))
    domain = floor_domain(occupancy, state)
    forbidden = np.asarray(forbidden_plan_mask, dtype=np.bool_)
    if forbidden.shape != domain.shape:
        raise ValueError("minimum-cut forbidden mask shape differs")
    forbidden_centers = ndimage.binary_dilation(
        forbidden, structure=disk(radius_cells))
    domain &= ~forbidden_centers
    source &= domain
    target &= domain
    if not np.any(source) or not np.any(target):
        raise ValueError("stair exclusion consumed minimum-cut source or target")

    shape = source.shape
    inf = np.iinfo(np.int32).max
    missing_support = (state["support"] < 0) & ~center_blocked
    missing_cost = np.full(shape, inf, dtype=np.int32)
    removal = np.full(shape, inf, dtype=np.int32)
    step_edit = np.full(shape, inf, dtype=np.int32)
    steps = np.full(shape, inf, dtype=np.int32)
    predecessor = np.full((*shape, 2), -1, dtype=np.int32)
    heap: list[tuple[int, int, int, int, int, int]] = []
    for x_value, y_value in np.argwhere(source):
        x, y = int(x_value), int(y_value)
        missing_cost[x, y] = 0
        removal[x, y] = 0
        step_edit[x, y] = 0
        steps[x, y] = 0
        heapq.heappush(heap, (0, 0, 0, 0, x, y))
    end = None
    while heap:
        missing, cost, excessive_step, length, x, y = heapq.heappop(heap)
        if ((missing, cost, excessive_step, length) !=
                (missing_cost[x, y], removal[x, y], step_edit[x, y], steps[x, y])):
            continue
        if target[x, y]:
            end = (x, y)
            break
        for dx, dy in CARDINAL_NEIGHBORS:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < shape[0] and 0 <= ny < shape[1] and domain[nx, ny]):
                continue
            current_z = int(state["support"][x, y])
            next_z = int(state["support"][nx, ny])
            step_over = int(
                current_z >= 0 and next_z >= 0
                and abs(next_z - current_z) > int(round(
                    CONFIG.max_support_step_m / GRID_M)))
            candidate = (
                missing + int(missing_support[nx, ny]),
                cost + int(center_blocked[nx, ny]),
                excessive_step + step_over,
                length + 1,
            )
            current = (
                int(missing_cost[nx, ny]), int(removal[nx, ny]),
                int(step_edit[nx, ny]), int(steps[nx, ny]))
            if candidate < current:
                (missing_cost[nx, ny], removal[nx, ny],
                 step_edit[nx, ny], steps[nx, ny]) = candidate
                predecessor[nx, ny] = (x, y)
                heapq.heappush(heap, (*candidate, nx, ny))
    if end is None:
        raise ValueError("no in-footprint minimum-removal path")
    path = [end]
    cursor = end
    while not source[cursor]:
        previous = predecessor[cursor]
        if previous[0] < 0:
            raise ValueError("minimum-removal predecessor chain broke")
        cursor = (int(previous[0]), int(previous[1]))
        path.append(cursor)
    path.reverse()
    blocked_centers = np.zeros(shape, dtype=np.bool_)
    for cell in path:
        if center_blocked[cell]:
            blocked_centers[cell] = True
    clearance_tunnel = ndimage.binary_dilation(
        blocked_centers, structure=disk(radius_cells))
    raw_cut = raw_blocked & clearance_tunnel
    return {
        "base_support_z_index": base,
        "body_band_z_index": [z0, z1],
        "minimum_blocked_body_center_cell_count": int(removal[end]),
        "minimum_missing_support_cell_count": int(missing_cost[end]),
        "minimum_step_over_limit_edge_count": int(step_edit[end]),
        "path_length_cells": len(path),
        "path": path,
        "blocked_centers": blocked_centers,
        "raw_cut": raw_cut,
        "domain": domain,
    }


def attribute_cut(
    rows: list[Collider],
    full_grid: Any,
    floor_grid: Any,
    crop_start_z: int,
    cut: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    full_query = np.zeros(full_grid.fine_shape, dtype=np.bool_)
    z0, z1 = cut["body_band_z_index"]
    full_z0 = crop_start_z + z0
    full_z1 = min(crop_start_z + z1, full_query.shape[2])
    raw_cut = cut["raw_cut"]
    full_query[:, :, full_z0:full_z1] = raw_cut[:, :, None]
    query_cells = np.argwhere(full_query)
    if not query_cells.size:
        return [], []
    query_min = query_cells.min(axis=0)
    query_max = query_cells.max(axis=0) + 1
    origin = np.asarray(full_grid.scene_bounds_min_m, dtype=np.float64)
    scratch = np.zeros(full_grid.fine_shape, dtype=np.bool_)
    prim_rows = []
    per_cell: dict[tuple[int, int], set[str]] = {
        (int(x), int(y)): set() for x, y in np.argwhere(raw_cut)}
    for row in rows:
        lower = np.floor((row.lower - origin) / GRID_M).astype(int) - 1
        upper = np.ceil((row.upper - origin) / GRID_M).astype(int) + 2
        lower = np.maximum(lower, 0)
        upper = np.minimum(upper, np.asarray(full_grid.fine_shape))
        if np.any(upper <= query_min) or np.any(lower >= query_max):
            continue
        rasterize_triangle_mesh(scratch, full_grid, row.vertices, row.faces, fill=False)
        overlap = scratch & full_query
        if np.any(overlap):
            plan_cells_full = np.unique(np.argwhere(overlap)[:, :2], axis=0)
            plan_cells = [(int(x), int(y)) for x, y in plan_cells_full]
            for cell in plan_cells:
                if cell in per_cell:
                    per_cell[cell].add(row.path)
            prim_rows.append({
                "prim_path": row.path,
                "object_root": object_root_name(row.path),
                "door_panel_set_member": row.is_door_panel,
                "cut_voxel_count": int(np.count_nonzero(overlap)),
                "cut_plan_cell_count": len(plan_cells),
                "barrier_plan_area_m2": round(len(plan_cells) * GRID_M * GRID_M, 4),
            })
        slices = tuple(slice(int(lower[i]), int(upper[i])) for i in range(3))
        scratch[slices] = False
    prim_rows.sort(key=lambda row: (-row["barrier_plan_area_m2"], row["prim_path"]))
    cells = []
    floor_origin = np.asarray(floor_grid.scene_bounds_min_m, dtype=np.float64)
    for x, y in sorted(per_cell):
        cells.append({
            "cell_xy_index": [x, y],
            "world_xy_bounds_m": [
                round(float(floor_origin[0] + x * GRID_M), 4),
                round(float(floor_origin[1] + y * GRID_M), 4),
                round(float(floor_origin[0] + (x + 1) * GRID_M), 4),
                round(float(floor_origin[1] + (y + 1) * GRID_M), 4),
            ],
            "collider_prim_paths": sorted(per_cell[(x, y)]),
        })
    missing = [row for row in cells if not row["collider_prim_paths"]]
    if missing:
        raise ValueError(f"{len(missing)} raw cut cells have no collider attribution")
    return prim_rows, cells


def verify_cut(
    occupancy: np.ndarray,
    grid: Any,
    arrival: tuple[float, float, float],
    target: np.ndarray,
    cut: dict[str, Any],
) -> dict[str, Any]:
    trial = occupancy.copy()
    z0, z1 = cut["body_band_z_index"]
    raw = cut["raw_cut"]
    for z in range(z0, z1):
        trial[:, :, z][raw] = False
    state = _standability_state(trial, grid, arrival)
    joined = bool(np.any(state["reachable"] & target))
    path = cut["path"]
    path_support = [int(state["support"][cell]) for cell in path]
    grid_origin = np.asarray(grid.scene_bounds_min_m, dtype=np.float64)
    missing_support_cells = []
    for cell, support_value in zip(path, path_support):
        if support_value >= 0:
            continue
        x, y = cell
        missing_support_cells.append({
            "cell_xy_index": [int(x), int(y)],
            "world_xy_bounds_m": [
                round(float(grid_origin[0] + x * GRID_M), 4),
                round(float(grid_origin[1] + y * GRID_M), 4),
                round(float(grid_origin[0] + (x + 1) * GRID_M), 4),
                round(float(grid_origin[1] + (y + 1) * GRID_M), 4),
            ],
            "world_xy_center_m": [
                round(float(grid_origin[0] + (x + 0.5) * GRID_M), 4),
                round(float(grid_origin[1] + (y + 0.5) * GRID_M), 4),
            ],
        })
    max_step = int(round(CONFIG.max_support_step_m / GRID_M))
    excessive_edges = sum(
        first >= 0 and second >= 0 and abs(second - first) > max_step
        for first, second in zip(path_support, path_support[1:]))
    return {
        "cut_reconnects_target": joined,
        "reachable_area_after_cut_m2": round(
            int(np.count_nonzero(state["reachable"])) * GRID_M * GRID_M, 4),
        "post_cut_path_missing_support_cell_count": sum(
            value < 0 for value in path_support),
        "post_cut_path_missing_support_cells": missing_support_cells,
        "post_cut_path_step_over_limit_edge_count": int(excessive_edges),
    }


def analyze_building(
    bid: str,
    batch_root: Path,
    storey: str,
) -> dict[str, Any]:
    building_dir = batch_root / bid
    usdz = building_dir / "scene" / "building.usdz"
    _, colliders = collect_colliders(usdz)
    door_rows = [row for row in colliders if row.is_door_panel]
    tall_thin = [row for row in door_rows if row.is_tall_thin_leaf]
    original, doorless, stair, repair_protected, full_grid = rasterize(colliders)
    peak_z, cut_z, split = _split_storeys(original, full_grid)
    original_occ, floor_grid, arrival, original_state = storey_state(
        building_dir, original, stair, full_grid, peak_z, cut_z, storey)
    doorless_occ, doorless_grid, doorless_arrival, doorless_state = storey_state(
        building_dir, doorless, stair, full_grid, peak_z, cut_z, storey)
    if tuple(floor_grid.fine_shape) != tuple(doorless_grid.fine_shape):
        raise ValueError("door counterfactual changed the registered storey grid")
    crop_start_z = cut_z if storey == "Floor2" else 0
    stair_stop_z = min(crop_start_z + doorless_occ.shape[2], stair.shape[2])
    stair_crop = np.zeros(doorless_occ.shape, dtype=np.bool_)
    stair_crop[:, :, :stair_stop_z-crop_start_z] = stair[
        :, :, crop_start_z:stair_stop_z]
    protected_crop = np.zeros(doorless_occ.shape, dtype=np.bool_)
    protected_crop[:, :, :stair_stop_z-crop_start_z] = repair_protected[
        :, :, crop_start_z:stair_stop_z]
    forbidden_repair_footprint = (
        stair_crop.any(axis=2) | protected_crop.any(axis=2)
    )
    before = state_summary(original_state)
    after = state_summary(doorless_state)
    component_rows = []
    remaining_targets = []
    for label, cells in enumerate(original_state["components"], start=1):
        if label in original_state["reached_labels"]:
            continue
        target = original_state["labels"] == label
        overlap = int(np.count_nonzero(doorless_state["reachable"] & target))
        reopened = overlap > 0
        row = {
            "original_component_label": label,
            "original_component_area_m2": round(len(cells) * GRID_M * GRID_M, 4),
            "original_cells_reached_after_door_removal": overlap,
            "reconnected_by_full_door_panel_removal": reopened,
        }
        component_rows.append(row)
        if not reopened:
            remaining_targets.append((row, target))
    cuts = []
    for component, target in remaining_targets:
        cut = minimum_removal_path(
            doorless_occ, doorless_state, target, forbidden_repair_footprint)
        prim_rows, cells = attribute_cut(
            [row for row in colliders if not row.is_door_panel],
            full_grid, doorless_grid, crop_start_z, cut)
        protected_attribution = [
            row["prim_path"] for row in prim_rows
            if repair_protected_root(row["prim_path"])
        ]
        if protected_attribution:
            raise ValueError(
                "minimum cut still intersects a protected door/frame or appliance "
                f"root: {protected_attribution}"
            )
        verification = verify_cut(
            doorless_occ, doorless_grid, doorless_arrival, target, cut)
        cuts.append({
            **component,
            "algorithm": (
                "lexicographic minimum (blocked body-centre cells, then path "
                "length) over cardinal 5 cm cells inside the floor footprint; "
                "raw obstacle cells are the radius-0.25 m clearance tunnel "
                "through the minimum path"),
            "minimum_blocked_body_center_cell_count": cut[
                "minimum_blocked_body_center_cell_count"],
            "minimum_missing_support_cell_count": cut[
                "minimum_missing_support_cell_count"],
            "minimum_step_over_limit_edge_count": cut[
                "minimum_step_over_limit_edge_count"],
            "raw_cut_plan_cell_count": int(np.count_nonzero(cut["raw_cut"])),
            "raw_cut_plan_area_m2": round(
                int(np.count_nonzero(cut["raw_cut"])) * GRID_M * GRID_M, 4),
            "path_length_cells": cut["path_length_cells"],
            "cut_cells": cells,
            "collider_prims": prim_rows,
            "collider_area_is_non_additive_when_prims_overlap": True,
            "verification": verification,
            "classification": (
                "COLLIDER_CUT_VERIFIED" if verification["cut_reconnects_target"]
                and cut["minimum_missing_support_cell_count"] == 0
                and cut["minimum_step_over_limit_edge_count"] == 0 else
                "CUT_COLLIDERS_IDENTIFIED_BUT_FLOOR_SUPPORT_OR_STEP_EDIT_ALSO_REQUIRED"
            ),
        })
    return {
        "building": bid,
        "storey": storey,
        "source_usdz": str(usdz),
        "source_usdz_sha256": sha256(usdz),
        "door_panel_selector": (
            "only door leaf/handle branches below doorway_/doorframe_ object "
            "roots; frames/jambs retained; appliance doors excluded"),
        "door_panel_collider_count": len(door_rows),
        "door_panel_collision_enabled_count": len(door_rows),
        "tall_thin_closed_leaf_count": len(tall_thin),
        "tall_thin_closed_leaf_paths": [row.path for row in tall_thin],
        "storey_split": split,
        "delivered": before,
        "without_full_door_panel_set": after,
        "reachable_area_gain_m2": round(
            after["reachable_area_m2"] - before["reachable_area_m2"], 4),
        "walkable_area_gain_m2": round(
            after["walkable_area_m2"] - before["walkable_area_m2"], 4),
        "inaccessible_component_split": component_rows,
        "remaining_component_minimum_cuts": cuts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-root", type=Path, default=DEFAULT_BATCH_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--building", action="append",
                        help="limit a retained rerun; may be repeated")
    parser.add_argument("--storey", choices=("Floor1", "Floor2"), action="append",
                        help="limit storeys; may be repeated")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite evidence: {args.output}")
    selected_buildings = tuple(args.building) if args.building else BUILDINGS
    selected_storeys = tuple(args.storey) if args.storey else ("Floor1", "Floor2")
    rows = []
    for bid in selected_buildings:
        for storey in selected_storeys:
            print(f"START {bid} {storey}", flush=True)
            rows.append(analyze_building(bid, args.batch_root, storey))
            print(f"DONE {bid} {storey}", flush=True)
    payload = {
        "schema": "Factory10StrictDoorScopeMinimumCutV1",
        "device": "CPU",
        "delivered_usdz_modified": False,
        "failed_candidates_retained": True,
        "door_counterfactual_uses_only_leaf_handle_below_doorway_roots": True,
        "frames_and_jambs_retained": True,
        "appliance_doors_excluded": True,
        "door_frame_appliance_and_parc_roots_forbidden_from_repair_paths": True,
        "requested_buildings": list(selected_buildings),
        "requested_storeys": list(selected_storeys),
        "buildings": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({f'{row["building"]}/{row["storey"]}': {
        "storey": row["storey"],
        "door_counts": [row["door_panel_collider_count"],
                        row["door_panel_collision_enabled_count"],
                        row["tall_thin_closed_leaf_count"]],
        "delivered": row["delivered"],
        "without_doors": row["without_full_door_panel_set"],
        "minimum_cut_count": len(row["remaining_component_minimum_cuts"]),
    } for row in rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
