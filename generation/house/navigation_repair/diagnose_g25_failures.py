#!/usr/bin/env python3
"""CPU-only cause diagnostics for the three retained G25 failures."""
from __future__ import annotations

import argparse
from collections import Counter, deque
import heapq
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from scipy import ndimage
from pxr import Usd, UsdGeom, UsdPhysics

from gate25_storey_navigation import (
    CARDINAL_NEIGHBORS,
    CONFIG,
    DEFAULT_BATCH_ROOT,
    GRID_M,
    MIN_REACHABLE_FROM_STAIR_M2,
    _bounded_floor1_terrain,
    _components,
    _crop_grid,
    _floor1_approach_xy,
    _floor2_arrival,
    _rasterize_delivered_usdz,
    sealed_closet_fixture,
    _split_storeys,
)
from protomotions.simulator.base_simulator.standability import (
    _derive_standability_from_evidence,
)
from protomotions.simulator.isaaclab.utils.scene_geometry_baking import (
    _authored_mesh_geometry_m,
    rasterize_triangle_mesh,
)


BIDS = (
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
FLOOR2_FAILURE = "home_train74_train98_lshaped_h300"
FLOOR1_FAILURES = (
    "home_train76_train123_straight_h300",
    "home_train92_train96_straight_h300",
)
DIAGONAL_NEIGHBORS = CARDINAL_NEIGHBORS + (
    (-1, -1), (-1, 1), (1, -1), (1, 1),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _standability_state(
    occupancy: np.ndarray,
    grid: Any,
    arrival: tuple[float, float, float],
) -> dict[str, Any]:
    standability, candidate_support = _derive_standability_from_evidence(
        occupancy,
        np.ones(occupancy.shape, dtype=np.bool_),
        grid,
        CONFIG,
        clearance_mode="fixed_base",
    )
    support = np.asarray(standability.support_z_index)
    labels, components = _components(support)
    origin = np.asarray(grid.scene_bounds_min_m, dtype=np.float64)
    x_centers = origin[0] + (np.arange(support.shape[0]) + 0.5) * GRID_M
    y_centers = origin[1] + (np.arange(support.shape[1]) + 0.5) * GRID_M
    support_top = np.where(
        support >= 0, origin[2] + (support + 1) * GRID_M, np.nan)
    horizontal = (
        (x_centers[:, None] - arrival[0]) ** 2
        + (y_centers[None, :] - arrival[1]) ** 2
        <= CONFIG.body_radius_m**2 + 1.0e-12
    )
    vertical = (
        np.abs(support_top - arrival[2])
        <= CONFIG.max_support_step_m + GRID_M / 2.0 + 1.0e-12
    )
    seed = (support >= 0) & horizontal & vertical
    reached_labels = tuple(
        sorted(int(value) for value in np.unique(labels[seed]) if value))
    reachable = np.isin(labels, reached_labels)
    return {
        "support": support,
        "candidate_support": np.asarray(candidate_support),
        "labels": labels,
        "components": components,
        "seed": seed,
        "reached_labels": reached_labels,
        "reachable": reachable,
        "origin": origin,
    }


def _bbox_diagonal_m(mask: np.ndarray) -> tuple[float, dict[str, float]]:
    cells = np.argwhere(mask)
    if not cells.size:
        return 0.0, {"x_extent_m": 0.0, "y_extent_m": 0.0}
    # Cell-union bounds, not center-to-center distance: one occupied cell spans
    # one full 5 cm voxel in each plan-view axis.
    x_extent = (int(cells[:, 0].max()) - int(cells[:, 0].min()) + 1) * GRID_M
    y_extent = (int(cells[:, 1].max()) - int(cells[:, 1].min()) + 1) * GRID_M
    return math.hypot(x_extent, y_extent), {
        "x_extent_m": round(x_extent, 4),
        "y_extent_m": round(y_extent, 4),
    }


def _label_components(
    support: np.ndarray,
    neighbors: Iterable[tuple[int, int]],
) -> tuple[np.ndarray, list[list[tuple[int, int]]]]:
    labels = np.zeros(support.shape, dtype=np.int32)
    components: list[list[tuple[int, int]]] = []
    max_step = int(round(CONFIG.max_support_step_m / GRID_M))
    for x_index, y_index in np.argwhere(support >= 0):
        x, y = int(x_index), int(y_index)
        if labels[x, y]:
            continue
        label = len(components) + 1
        labels[x, y] = label
        queue = deque([(x, y)])
        cells: list[tuple[int, int]] = []
        while queue:
            cell = queue.popleft()
            cells.append(cell)
            z = int(support[cell])
            for dx, dy in neighbors:
                neighbor = (cell[0] + dx, cell[1] + dy)
                if (
                    neighbor[0] < 0
                    or neighbor[0] >= support.shape[0]
                    or neighbor[1] < 0
                    or neighbor[1] >= support.shape[1]
                    or labels[neighbor]
                    or support[neighbor] < 0
                    or abs(int(support[neighbor]) - z) > int(round(
                        CONFIG.max_support_step_m / GRID_M))
                ):
                    continue
                labels[neighbor] = label
                queue.append(neighbor)
        components.append(cells)
    return labels, components


def _component_histogram(components: list[list[tuple[int, int]]]) -> list[float]:
    return sorted(
        (round(len(cells) * GRID_M * GRID_M, 4) for cells in components),
        reverse=True,
    )


def _diagonal_merge_diagnostic(state: dict[str, Any]) -> dict[str, Any]:
    labels4 = state["labels"]
    labels8, components8 = _label_components(
        state["support"], DIAGONAL_NEIGHBORS)
    merge_groups = []
    for label8 in range(1, len(components8) + 1):
        labels = sorted(
            int(value) for value in np.unique(labels4[labels8 == label8]) if value)
        if len(labels) > 1:
            merge_groups.append(labels)
    return {
        "cardinal_component_count": len(state["components"]),
        "diagonal_component_count": len(components8),
        "components_merged_count": len(state["components"]) - len(components8),
        "cardinal_component_groups_joined_by_diagonals": merge_groups,
        "diagnostic_only": True,
    }


def _stair_candidates(
    occupancy: np.ndarray,
    stair: np.ndarray,
    origin: np.ndarray,
    peak_z: int,
) -> list[tuple[int, int, int]]:
    centers_z = origin[2] + (np.arange(occupancy.shape[2]) + 0.5) * GRID_M
    slab_center = float(centers_z[peak_z])
    candidates = []
    for x_index, y_index in np.argwhere(stair.any(axis=2)):
        x, y = int(x_index), int(y_index)
        for z_index in np.flatnonzero(stair[x, y, :]):
            z = int(z_index)
            if (
                z + 1 < occupancy.shape[2]
                and not occupancy[x, y, z + 1]
                and float(centers_z[z]) <= slab_center + 0.06
            ):
                candidates.append((x, y, z))
    candidates.sort(key=lambda cell: (-cell[2], cell[0], cell[1]))
    return candidates


def _seed_spread(
    occupancy: np.ndarray,
    stair: np.ndarray,
    grid: Any,
    peak_z: int,
    state: dict[str, Any],
) -> dict[str, Any]:
    origin = np.asarray(grid.scene_bounds_min_m, dtype=np.float64)
    floor_origin = state["origin"]
    support = state["support"]
    labels = state["labels"]
    x_centers = floor_origin[0] + (np.arange(support.shape[0]) + 0.5) * GRID_M
    y_centers = floor_origin[1] + (np.arange(support.shape[1]) + 0.5) * GRID_M
    support_top = np.where(
        support >= 0, floor_origin[2] + (support + 1) * GRID_M, np.nan)
    component_cells = np.bincount(labels.ravel())
    rows = []
    candidates = _stair_candidates(occupancy, stair, origin, peak_z)
    for rank, (x, y, z) in enumerate(candidates, start=1):
        arrival = (
            float(origin[0] + (x + 0.5) * GRID_M),
            float(origin[1] + (y + 0.5) * GRID_M),
            float(origin[2] + (z + 1) * GRID_M),
        )
        seed = (
            (support >= 0)
            & (
                (x_centers[:, None] - arrival[0]) ** 2
                + (y_centers[None, :] - arrival[1]) ** 2
                <= CONFIG.body_radius_m**2 + 1.0e-12
            )
            & (
                np.abs(support_top - arrival[2])
                <= CONFIG.max_support_step_m + GRID_M / 2.0 + 1.0e-12
            )
        )
        seed_labels = sorted(int(value) for value in np.unique(labels[seed]) if value)
        if not seed_labels:
            continue
        reachable_cells = sum(int(component_cells[label]) for label in seed_labels)
        rows.append({
            "all_candidate_rank": rank,
            "arrival_xyz_m": [round(value, 4) for value in arrival],
            "seed_cell_count": int(np.count_nonzero(seed)),
            "reached_component_labels": seed_labels,
            "reachable_area_m2": round(reachable_cells * GRID_M * GRID_M, 4),
            "status": (
                "PASS" if reachable_cells * GRID_M * GRID_M + 1.0e-9
                >= MIN_REACHABLE_FROM_STAIR_M2 else "FAIL"
            ),
        })
    if not rows:
        raise ValueError("no seedable Floor2 stair candidates")
    areas = [float(row["reachable_area_m2"]) for row in rows]
    histogram = Counter(areas)
    statuses = {row["status"] for row in rows}
    return {
        "exposed_candidate_count": len(candidates),
        "seedable_candidate_count": len(rows),
        "minimum_reachable_area_m2": min(areas),
        "maximum_reachable_area_m2": max(areas),
        "unique_reachable_area_histogram": [
            {"reachable_area_m2": area, "candidate_count": count}
            for area, count in sorted(histogram.items())
        ],
        "verdict_stability": (
            "SEED_DEPENDENT" if statuses == {"PASS", "FAIL"}
            else "ALL_SEEDABLE_CANDIDATES_" + next(iter(statuses))
        ),
        "candidates": rows,
    }


def _floor1_model_diagnostic(
    original_occupancy: np.ndarray,
    with_terrain: np.ndarray,
    grid: Any,
    floor1_occupancy: np.ndarray,
    floor1_grid: Any,
    peak_z: int,
    cut_z: int,
    state: dict[str, Any],
) -> dict[str, Any]:
    component_histogram = _component_histogram(state["components"])
    discarded = (
        (state["candidate_support"] >= 0) & (state["support"] < 0))
    raw_footprint = np.asarray(original_occupancy[:, :, peak_z], dtype=np.bool_)
    footprint = np.asarray(ndimage.binary_fill_holes(raw_footprint), dtype=np.bool_)
    footprint_labels, footprint_count = ndimage.label(
        footprint,
        structure=np.asarray([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8),
    )

    origin_z = float(grid.scene_bounds_min_m[2])
    support_tops = origin_z + (np.arange(with_terrain.shape[2]) + 1) * GRID_M
    terrain_z = int(np.argmin(np.abs(support_tops)))
    terrain_only = np.zeros_like(with_terrain)
    terrain_only[:, :, terrain_z][footprint] = True
    terrain_crop, terrain_grid = _crop_grid(
        grid, terrain_only, 0, min(cut_z + 1, terrain_only.shape[2]))
    terrain_result, _ = _derive_standability_from_evidence(
        terrain_crop,
        np.ones(terrain_crop.shape, dtype=np.bool_),
        terrain_grid,
        CONFIG,
        clearance_mode="fixed_base",
    )
    terrain_support = np.asarray(terrain_result.support_z_index)
    terrain_labels, terrain_components = _components(terrain_support)
    reached_terrain_labels = set(
        int(value) for value in np.unique(terrain_labels[state["reachable"]]) if value)
    inaccessible = (state["support"] >= 0) & ~state["reachable"]
    inaccessible_terrain_labels = set(
        int(value) for value in np.unique(terrain_labels[inaccessible]) if value)
    outside_footprint_inaccessible = int(np.count_nonzero(inaccessible & ~footprint))
    disconnected_by_terrain = sorted(
        inaccessible_terrain_labels - reached_terrain_labels)
    return {
        "retained_component_area_histogram_m2": component_histogram,
        "retained_component_count": len(component_histogram),
        "diagonal_adjacency": _diagonal_merge_diagnostic(state),
        "small_component_filter": {
            "discarded_cell_count": int(np.count_nonzero(discarded)),
            "discarded_total_area_m2": round(
                int(np.count_nonzero(discarded)) * GRID_M * GRID_M, 4),
        },
        "bounded_terrain": {
            "raw_footprint_cell_count": int(np.count_nonzero(raw_footprint)),
            "filled_footprint_cell_count": int(np.count_nonzero(footprint)),
            "footprint_cardinal_component_count": int(footprint_count),
            "terrain_only_retained_component_area_histogram_m2": (
                _component_histogram(terrain_components)
            ),
            "terrain_only_labels_overlapping_reachable": sorted(reached_terrain_labels),
            "terrain_only_labels_overlapping_inaccessible": sorted(
                inaccessible_terrain_labels),
            "inaccessible_cells_outside_filled_footprint": (
                outside_footprint_inaccessible
            ),
            "terrain_only_components_not_shared_with_reachable": (
                disconnected_by_terrain
            ),
            "footprint_mask_shapes_verdict": bool(
                outside_footprint_inaccessible or disconnected_by_terrain),
            "test": (
                "remove all delivered vertical geometry, retain only the exact "
                "finite injected footprint, then compare exact pinned support "
                "components overlapping current reachable/inaccessible cells"
            ),
        },
    }


def _central_column_support(occupancy: np.ndarray) -> np.ndarray:
    """Diagnostic zero-radius support; keeps vertical clearance and step rules."""
    body_cells = int(round(CONFIG.body_height_m / GRID_M))
    candidate_count = occupancy.shape[2] - body_cells
    support = np.full(occupancy.shape[:2], -1, dtype=np.int32)
    if candidate_count <= 0:
        return support
    prefix = np.concatenate((
        np.zeros((*occupancy.shape[:2], 1), dtype=np.int32),
        np.cumsum(occupancy, axis=2, dtype=np.int32),
    ), axis=2)
    body_blocked = prefix[:, :, body_cells + 1:] - prefix[:, :, 1:candidate_count + 1]
    exposed = occupancy[:, :, :candidate_count] & ~occupancy[:, :, 1:candidate_count + 1]
    clear = exposed & (body_blocked == 0)
    has = clear.any(axis=2)
    support[has] = np.argmax(clear[has], axis=1).astype(np.int32)
    return support


def _boundary_cause_topology(
    occupancy: np.ndarray,
    state: dict[str, Any],
) -> dict[str, Any]:
    support = state["support"]
    reachable = state["reachable"]
    step_edges = 0
    blocked_edges = 0
    max_step = int(round(CONFIG.max_support_step_m / GRID_M))
    for x, y in np.argwhere(reachable):
        for dx, dy in CARDINAL_NEIGHBORS:
            nx, ny = int(x) + dx, int(y) + dy
            if not (0 <= nx < support.shape[0] and 0 <= ny < support.shape[1]):
                continue
            if reachable[nx, ny]:
                continue
            if support[nx, ny] >= 0:
                if abs(int(support[nx, ny]) - int(support[x, y])) > max_step:
                    step_edges += 1
            else:
                blocked_edges += 1
    central_support = _central_column_support(occupancy)
    central_labels, _ = _label_components(central_support, CARDINAL_NEIGHBORS)
    reachable_central = set(
        int(value) for value in np.unique(central_labels[reachable]) if value)
    inaccessible = (support >= 0) & ~reachable
    inaccessible_central = set(
        int(value) for value in np.unique(central_labels[inaccessible]) if value)
    zero_radius_connects = bool(reachable_central & inaccessible_central)
    return {
        "cardinal_step_over_limit_transition_edge_count": step_edges,
        "nonstandable_transition_edge_count": blocked_edges,
        "zero_radius_vertical_clearance_components_connect_both_sides": (
            zero_radius_connects
        ),
        "interpretation": (
            "opening narrower than 0.50 m" if zero_radius_connects
            else "wall with no vertically clear center-line opening"
        ) if step_edges == 0 else "step over max_support_step_m is present",
    }


def _boundary_query_mask(
    state: dict[str, Any],
    occupancy: np.ndarray,
) -> np.ndarray:
    """Body-height voxels in voids lying between reached and cut-off support."""
    support = state["support"]
    reachable = state["reachable"]
    inaccessible = (support >= 0) & ~reachable
    radius_cells = int(math.ceil((2 * CONFIG.body_radius_m + 0.25) / GRID_M))
    structure = ndimage.generate_binary_structure(2, 2)
    near_reachable = ndimage.binary_dilation(
        reachable, structure=structure, iterations=radius_cells)
    near_inaccessible = ndimage.binary_dilation(
        inaccessible, structure=structure, iterations=radius_cells)
    between = near_reachable & near_inaccessible & ~(support >= 0)
    query = np.zeros_like(occupancy)
    support_values = support[reachable]
    if not support_values.size:
        return query
    z0 = max(int(support_values.min()) + 1, 0)
    z1 = min(
        int(support_values.max()) + 1
        + int(round(CONFIG.body_height_m / GRID_M)) + 1,
        occupancy.shape[2],
    )
    query[:, :, z0:z1] = between[:, :, None]
    return query


def _widest_centerline_path(
    central_support: np.ndarray,
    body_clearance: np.ndarray,
    source: np.ndarray,
    target: np.ndarray,
) -> dict[str, Any] | None:
    valid = central_support >= 0
    source = valid & source
    target = valid & target
    if not np.any(source) or not np.any(target):
        return None
    clearance = np.where(valid, body_clearance, 0.0)
    best = np.full(valid.shape, -1.0, dtype=np.float64)
    predecessor = np.full((*valid.shape, 2), -1, dtype=np.int32)
    heap: list[tuple[float, int, int]] = []
    for x_index, y_index in np.argwhere(source):
        x, y = int(x_index), int(y_index)
        best[x, y] = float(clearance[x, y])
        heapq.heappush(heap, (-best[x, y], x, y))
    max_step = int(round(CONFIG.max_support_step_m / GRID_M))
    end: tuple[int, int] | None = None
    while heap:
        negative_capacity, x, y = heapq.heappop(heap)
        capacity = -negative_capacity
        if capacity + 1.0e-12 < best[x, y]:
            continue
        if target[x, y]:
            end = (x, y)
            break
        for dx, dy in CARDINAL_NEIGHBORS:
            nx, ny = x + dx, y + dy
            if (
                nx < 0 or nx >= valid.shape[0]
                or ny < 0 or ny >= valid.shape[1]
                or not valid[nx, ny]
                or abs(int(central_support[nx, ny]) - int(central_support[x, y]))
                > max_step
            ):
                continue
            next_capacity = min(capacity, float(clearance[nx, ny]))
            if next_capacity > best[nx, ny] + 1.0e-12:
                best[nx, ny] = next_capacity
                predecessor[nx, ny] = (x, y)
                heapq.heappush(heap, (-next_capacity, nx, ny))
    if end is None:
        return None
    path = [end]
    cursor = end
    while not source[cursor]:
        previous = predecessor[cursor]
        if previous[0] < 0:
            raise ValueError("widest-path predecessor chain broke")
        cursor = (int(previous[0]), int(previous[1]))
        path.append(cursor)
    path.reverse()
    bottleneck = min(path, key=lambda cell: float(clearance[cell]))
    return {
        "maximin_body_clearance_m": round(float(best[end]), 4),
        "estimated_opening_width_m": round(2.0 * float(best[end]), 4),
        "clearance_definition": (
            "conservative plan-view distance from the candidate center to "
            "occupied voxel squares over its support-specific 1.60 m body band"
        ),
        "path_length_cells": len(path),
        "bottleneck_cell": [int(bottleneck[0]), int(bottleneck[1])],
        "bottleneck_support_z_index": int(central_support[bottleneck]),
    }


def _support_specific_body_clearance(
    occupancy: np.ndarray,
    central_support: np.ndarray,
) -> np.ndarray:
    """Conservative plan-view clearance through each candidate's body band."""
    clearance = np.zeros(central_support.shape, dtype=np.float32)
    body_cells = int(round(CONFIG.body_height_m / GRID_M))
    layer_clearance: dict[int, np.ndarray] = {}
    for support_z in sorted(int(value) for value in np.unique(
            central_support[central_support >= 0])):
        stop = min(support_z + 1 + body_cells, occupancy.shape[2])
        bands = []
        for z_index in range(support_z + 1, stop):
            if z_index not in layer_clearance:
                center_distance = ndimage.distance_transform_edt(
                    ~occupancy[:, :, z_index], sampling=GRID_M)
                # Convert center-to-center EDT to a conservative distance to
                # the closed occupied voxel square.
                layer_clearance[z_index] = np.maximum(
                    center_distance - GRID_M / math.sqrt(2.0), 0.0)
            bands.append(layer_clearance[z_index])
        mask = central_support == support_z
        if bands:
            minimum = np.minimum.reduce(bands)
            clearance[mask] = minimum[mask]
    return clearance


def _closest_pair(
    first: np.ndarray,
    second: np.ndarray,
) -> tuple[tuple[int, int], tuple[int, int]]:
    if not np.any(first) or not np.any(second):
        raise ValueError("cannot find closest pair for an empty mask")
    _, indices = ndimage.distance_transform_edt(~first, return_indices=True)
    second_cells = np.argwhere(second)
    distances = np.hypot(
        second_cells[:, 0] - indices[0][second],
        second_cells[:, 1] - indices[1][second],
    )
    target_index = int(np.argmin(distances))
    target = tuple(int(value) for value in second_cells[target_index])
    source = (
        int(indices[0][target]),
        int(indices[1][target]),
    )
    return source, target


def _local_body_query(
    occupancy: np.ndarray,
    center_xy: tuple[float, float],
    support_z: int,
    *,
    radius_m: float = 0.40,
) -> np.ndarray:
    x_index = np.arange(occupancy.shape[0], dtype=np.float64)[:, None]
    y_index = np.arange(occupancy.shape[1], dtype=np.float64)[None, :]
    local_xy = (
        (x_index - center_xy[0]) ** 2 + (y_index - center_xy[1]) ** 2
        <= (radius_m / GRID_M) ** 2 + 1.0e-12
    )
    query = np.zeros_like(occupancy)
    z0 = max(int(support_z) + 1, 0)
    z1 = min(
        z0 + int(round(CONFIG.body_height_m / GRID_M)),
        occupancy.shape[2],
    )
    query[:, :, z0:z1] = local_xy[:, :, None]
    return query


def _barrier_queries(
    occupancy: np.ndarray,
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, np.ndarray]]:
    support = state["support"]
    reachable = state["reachable"]
    labels = state["labels"]
    central_support = _central_column_support(occupancy)
    body_clearance = _support_specific_body_clearance(
        occupancy, central_support)
    barriers = []
    queries: dict[str, np.ndarray] = {}
    for label, cells in enumerate(state["components"], start=1):
        if label in state["reached_labels"]:
            continue
        target = labels == label
        widest = _widest_centerline_path(
            central_support, body_clearance, reachable, target)
        query_id = f"cutoff_component_{label}"
        if widest is not None:
            bottleneck = tuple(widest["bottleneck_cell"])
            center = (float(bottleneck[0]), float(bottleneck[1]))
            support_z = int(widest["bottleneck_support_z_index"])
            mechanism = "opening narrower than 0.50 m body diameter"
        else:
            source_cell, target_cell = _closest_pair(reachable, target)
            center = (
                (source_cell[0] + target_cell[0]) * 0.5,
                (source_cell[1] + target_cell[1]) * 0.5,
            )
            support_z = int(support[source_cell])
            mechanism = "wall with no vertically clear center-line opening"
        queries[query_id] = _local_body_query(
            occupancy, center, support_z)
        barriers.append({
            "query_id": query_id,
            "cutoff_component_label": label,
            "cutoff_component_area_m2": round(len(cells) * GRID_M * GRID_M, 4),
            "mechanism": mechanism,
            "widest_centerline_path": widest,
            "query_center_xy_cell": [round(center[0], 4), round(center[1], 4)],
            "query_radius_m": 0.40,
        })
    return barriers, queries


def _extract_boundary_prims(
    usdz_path: Path,
    full_grid: Any,
    floor_queries: dict[str, np.ndarray],
    crop_start_z: int,
) -> dict[str, Any]:
    full_queries: dict[str, np.ndarray] = {}
    all_query = np.zeros(full_grid.fine_shape, dtype=np.bool_)
    for query_id, floor_query in floor_queries.items():
        full_query = np.zeros(full_grid.fine_shape, dtype=np.bool_)
        stop = min(crop_start_z + floor_query.shape[2], full_query.shape[2])
        full_query[:, :, crop_start_z:stop] = floor_query[:, :, :stop-crop_start_z]
        full_queries[query_id] = full_query
        all_query |= full_query
    query_cells = np.argwhere(all_query)
    if not query_cells.size:
        return {query_id: {"prims": [], "query_voxel_count": 0}
                for query_id in floor_queries}
    query_min = query_cells.min(axis=0)
    query_max = query_cells.max(axis=0) + 1

    stage = Usd.Stage.Open(str(usdz_path))
    if stage is None:
        raise ValueError(f"could not reopen {usdz_path}")
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage))
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    origin = np.asarray(full_grid.scene_bounds_min_m, dtype=np.float64)
    scratch = np.zeros(full_grid.fine_shape, dtype=np.bool_)
    hits: dict[str, list[dict[str, Any]]] = {
        query_id: [] for query_id in full_queries}
    for prim in stage.Traverse():
        if not (prim.IsA(UsdGeom.Mesh) and prim.HasAPI(UsdPhysics.CollisionAPI)):
            continue
        if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:
            continue
        vertices, faces = _authored_mesh_geometry_m(
            stage, prim, meters_per_unit, cache)
        lower = np.floor((vertices.min(axis=0) - origin) / GRID_M).astype(int) - 1
        upper = np.ceil((vertices.max(axis=0) - origin) / GRID_M).astype(int) + 2
        lower = np.maximum(lower, 0)
        upper = np.minimum(upper, np.asarray(full_grid.fine_shape))
        if np.any(upper <= query_min) or np.any(lower >= query_max):
            continue
        rasterize_triangle_mesh(scratch, full_grid, vertices, faces, fill=False)
        imageable = UsdGeom.Imageable(prim)
        for query_id, full_query in full_queries.items():
            overlap = int(np.count_nonzero(scratch & full_query))
            if overlap:
                hits[query_id].append({
                    "prim_path": str(prim.GetPath()),
                    "purpose": str(imageable.ComputePurpose()),
                    "local_query_voxel_count": overlap,
                })
        slices = tuple(slice(int(lower[i]), int(upper[i])) for i in range(3))
        scratch[slices] = False
    result = {}
    for query_id, rows in hits.items():
        rows.sort(key=lambda row: (-row["local_query_voxel_count"], row["prim_path"]))
        result[query_id] = {
            "query_definition": (
                "enabled collider voxels within a 0.40 m plan-view radius of "
                "the component barrier point and through the 1.60 m body band"
            ),
            "query_voxel_count": int(np.count_nonzero(full_queries[query_id])),
            "prims": rows,
        }
    return result


def _building_rows(
    bid: str,
    batch_root: Path,
    retained_parts: Path,
    *,
    deep: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    building_dir = batch_root / bid
    usdz_path = building_dir / "scene" / "building.usdz"
    occupancy, stair, grid, raster = _rasterize_delivered_usdz(usdz_path)
    peak_z, cut_z, split = _split_storeys(occupancy, grid)
    floor2_occupancy, floor2_grid = _crop_grid(
        grid, occupancy, cut_z, occupancy.shape[2])
    floor2_arrival, _ = _floor2_arrival(
        occupancy, stair, grid, peak_z, floor2_occupancy, floor2_grid)
    floor2_state = _standability_state(
        floor2_occupancy, floor2_grid, floor2_arrival)

    with_terrain, floor1_z, modelling = _bounded_floor1_terrain(
        occupancy, grid, peak_z)
    floor1_occupancy, floor1_grid = _crop_grid(
        grid, with_terrain, 0, min(cut_z + 1, occupancy.shape[2]))
    floor1_xy, _ = _floor1_approach_xy(building_dir)
    floor1_arrival = (floor1_xy[0], floor1_xy[1], floor1_z)
    floor1_state = _standability_state(
        floor1_occupancy, floor1_grid, floor1_arrival)

    retained_path = retained_parts / f"{bid}.json"
    retained = json.loads(retained_path.read_text(encoding="utf-8"))
    rows = []
    for storey, state, arrival in (
        ("Floor1", floor1_state, floor1_arrival),
        ("Floor2", floor2_state, floor2_arrival),
    ):
        reachable_cells = int(np.count_nonzero(state["reachable"]))
        reachable_area = round(reachable_cells * GRID_M * GRID_M, 4)
        expected_area = retained["storeys"][storey][
            "area_reachable_from_stair_arrival_m2"]
        if reachable_area != expected_area:
            raise ValueError(
                f"{bid} {storey} area changed: {reachable_area} != {expected_area}")
        diagonal, extents = _bbox_diagonal_m(state["reachable"])
        rows.append({
            "building": bid,
            "storey": storey,
            "retained_status": retained["storeys"][storey]["status"],
            "arrival_xyz_m": [round(float(value), 4) for value in arrival],
            "reachable_area_m2": reachable_area,
            "reachable_bbox_diagonal_m": round(diagonal, 4),
            **extents,
        })

    diagnostics: dict[str, Any] = {
        "source_usdz_sha256": _sha256(usdz_path),
        "retained_g25_part_sha256": _sha256(retained_path),
        "rasterization": raster,
        "storey_split": split,
    }
    if deep and bid == FLOOR2_FAILURE:
        seed_spread = _seed_spread(
            occupancy, stair, grid, peak_z, floor2_state)
        topology = _boundary_cause_topology(floor2_occupancy, floor2_state)
        barriers, queries = _barrier_queries(floor2_occupancy, floor2_state)
        boundary_prims = _extract_boundary_prims(
            usdz_path, grid, queries, cut_z)
        for barrier in barriers:
            barrier["boundary_prims"] = boundary_prims[barrier["query_id"]]
        diagnostics["Floor2"] = {
            "seed_spread": seed_spread,
            "component_area_histogram_m2": _component_histogram(
                floor2_state["components"]),
            "boundary_topology": topology,
            "barriers_to_cutoff_components": barriers,
        }
    if deep and bid in FLOOR1_FAILURES:
        diagnostics["Floor1"] = _floor1_model_diagnostic(
            occupancy,
            with_terrain,
            grid,
            floor1_occupancy,
            floor1_grid,
            peak_z,
            cut_z,
            floor1_state,
        )
    return rows, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, default=DEFAULT_BATCH_ROOT)
    parser.add_argument(
        "--retained-parts",
        type=Path,
        default=Path(__file__).resolve().parent / "evidence" / "g25_delivered_parts_v3",
    )
    parser.add_argument(
        "--refine-from",
        type=Path,
        help="retain a complete V1 run and recompute only the Floor2 deep diagnostic",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite evidence: {args.output}")
    if args.refine_from is not None:
        retained_diagnostic = json.loads(
            args.refine_from.read_text(encoding="utf-8"))
        if retained_diagnostic.get("schema") != "G25FailureCauseDiagnosticV1":
            raise ValueError("--refine-from must be a complete V1 diagnostic")
        rows, diagnostic = _building_rows(
            FLOOR2_FAILURE,
            args.batch_root,
            args.retained_parts,
            deep=True,
        )
        retained_diagnostic["extent_rows"] = [
            row for row in retained_diagnostic["extent_rows"]
            if row["building"] != FLOOR2_FAILURE
        ] + rows
        order = {(bid, storey): index for index, (bid, storey) in enumerate(
            (pair for bid in BIDS for pair in ((bid, "Floor1"), (bid, "Floor2"))))}
        retained_diagnostic["extent_rows"].sort(
            key=lambda row: order[(row["building"], row["storey"])])
        retained_diagnostic["failure_diagnostics"][FLOOR2_FAILURE] = diagnostic
        retained_diagnostic["schema"] = "G25FailureCauseDiagnosticV2"
        retained_diagnostic["supersedes"] = {
            "path": str(args.refine_from.resolve()),
            "sha256": _sha256(args.refine_from),
            "reason": (
                "V1's diagnostic opening-width estimate used a 2D mask EDT; "
                "V2 uses support-specific 3D body-band clearance. Retained G25 "
                "areas, seed spread, Floor1 diagnostics, and batch extents do not change."
            ),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(retained_diagnostic, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "output": str(args.output.resolve()),
            "supersedes": str(args.refine_from.resolve()),
            "refined_building": FLOOR2_FAILURE,
        }, indent=2))
        return 0
    all_rows = []
    deep_diagnostics = {}
    for bid in BIDS:
        print(f"START {bid}", flush=True)
        rows, diagnostics = _building_rows(
            bid,
            args.batch_root,
            args.retained_parts,
            deep=bid in {FLOOR2_FAILURE, *FLOOR1_FAILURES},
        )
        all_rows.extend(rows)
        if bid in {FLOOR2_FAILURE, *FLOOR1_FAILURES}:
            deep_diagnostics[bid] = diagnostics
        print(f"DONE {bid}", flush=True)

    fixture_rows = []
    for fixture, door_open in (("sealed_closet", False), ("open_door_control", True)):
        fixture_occupancy, fixture_grid, fixture_arrival = sealed_closet_fixture(
            door_open=door_open)
        fixture_state = _standability_state(
            fixture_occupancy, fixture_grid, fixture_arrival)
        fixture_area = round(
            int(np.count_nonzero(fixture_state["reachable"])) * GRID_M * GRID_M, 4)
        fixture_diagonal, fixture_extents = _bbox_diagonal_m(
            fixture_state["reachable"])
        fixture_rows.append({
            "fixture": fixture,
            "reachable_area_m2": fixture_area,
            "reachable_bbox_diagonal_m": round(fixture_diagonal, 4),
            **fixture_extents,
        })

    proposed_extent_m = 7.0
    proposed_rule_rows = []
    for row in all_rows:
        proposed_rule_rows.append({
            "building": row["building"],
            "storey": row["storey"],
            "current_status": row["retained_status"],
            "diagnostic_proposed_status": (
                "PASS" if (
                    row["reachable_area_m2"] + 1.0e-9
                    >= MIN_REACHABLE_FROM_STAIR_M2
                    or row["reachable_bbox_diagonal_m"] + 1.0e-9
                    >= proposed_extent_m
                ) else "FAIL"
            ),
        })
    payload = {
        "schema": "G25FailureCauseDiagnosticV1",
        "batch_id": "building_batch_20260819",
        "device": "CPU",
        "gate_semantics_changed": False,
        "extent_measure": {
            "name": "reachable_cell_union_axis_aligned_bbox_diagonal",
            "definition": (
                "sqrt(x_extent^2+y_extent^2), with extents measured over the "
                "outer edges of the union of reachable 5 cm cells"
            ),
        },
        "extent_rows": all_rows,
        "fixture_extent_rows": fixture_rows,
        "next_batch_rule_proposal": {
            "rule": (
                "PASS iff reachable_area_m2 >= 15.0 OR "
                "reachable_bbox_diagonal_m >= 7.0"
            ),
            "status": "DIAGNOSTIC_PROPOSAL_ONLY",
            "current_run_changed": False,
            "rationale": (
                "preserve the existing usable-area path while admitting long "
                "connected corridor regions; the retained sealed-closet negative "
                "control remains below both thresholds"
            ),
            "batch_rows": proposed_rule_rows,
        },
        "failure_diagnostics": deep_diagnostics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "extent_row_count": len(all_rows),
        "diagnosed_buildings": list(deep_diagnostics),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
