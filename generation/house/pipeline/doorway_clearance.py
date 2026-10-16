#!/usr/bin/env python3
"""A4 doorway-centreline collision-occupancy clearance gate.

The source scenes are fixed layouts.  A4 admits one only when the rasterized
collision occupancy at every doorway centre line leaves a 0.60 m lateral gap.
The raster includes every enabled mesh collider intersecting the aperture,
after applying the human-selected generation policy that disables swinging
door leaves and their attached handles.  Frames, jambs and unrelated appliance
doors remain included.  Clearance is never inferred from root extents.
"""
from __future__ import annotations

import math
import argparse
import json
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import trimesh
from pxr import Usd, UsdGeom, UsdPhysics

from door_collision_policy import leaf_member_kind


BODY_DIAMETER_M = 0.50
CLEARANCE_MARGIN_M = 0.10
MIN_CLEAR_OPENING_M = BODY_DIAMETER_M + CLEARANCE_MARGIN_M
CENTRELINE_HALF_LENGTH_M = 1.50
BODY_HEIGHT_M = 1.60
RASTER_GRID_M = 0.05
FALLBACK_JAMB_WIDTH_M = 0.075
PURPOSES = (
    UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.guide,
)
STRUCTURAL_PREFIXES = (
    "ceiling", "cut", "doorway_", "floor", "room_", "wall_", "window_",
)
DOORWAY_ROOT_RE = re.compile(r"^doorway_[0-9a-f]{32}_")
DOORWAY_CLEARANCE_CONTRACT_VERSION = 2


def _bounds(cache: UsdGeom.BBoxCache, prim: Usd.Prim) -> list[float] | None:
    value = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    if value.IsEmpty():
        return None
    lower, upper = value.GetMin(), value.GetMax()
    return [float(lower[i]) for i in range(3)] + [float(upper[i]) for i in range(3)]


def _union_bounds(rows: list[list[float]]) -> list[float] | None:
    if not rows:
        return None
    return ([min(row[axis] for row in rows) for axis in range(3)]
            + [max(row[axis] for row in rows) for axis in range(3, 6)])


def _merge_intervals(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, stop))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
    return merged


def _subtract(
    interval: tuple[float, float], blockers: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    pieces = [interval]
    for blocker in _merge_intervals(blockers):
        updated = []
        for start, stop in pieces:
            if blocker[1] <= start or stop <= blocker[0]:
                updated.append((start, stop))
                continue
            if start < blocker[0]:
                updated.append((start, min(stop, blocker[0])))
            if blocker[1] < stop:
                updated.append((max(start, blocker[1]), stop))
        pieces = updated
    return pieces


def _maximum_gap(
    aperture: tuple[float, float], blockers: Iterable[tuple[float, float]],
) -> float:
    return max((stop - start for start, stop in _subtract(aperture, blockers)),
               default=0.0)


def _triangulate(counts: Iterable[int], indices: Iterable[int]) -> np.ndarray:
    counts = [int(value) for value in counts]
    indices = [int(value) for value in indices]
    if any(count < 3 for count in counts) or sum(counts) != len(indices):
        raise ValueError("invalid collider polygon index stream")
    triangles = []
    cursor = 0
    for count in counts:
        face = indices[cursor:cursor + count]
        triangles.extend((face[0], face[k], face[k + 1])
                         for k in range(1, count - 1))
        cursor += count
    return np.asarray(triangles, dtype=np.int64).reshape(-1, 3)


def _world_mesh_m(
    stage: Usd.Stage, prim: Usd.Prim, xforms: UsdGeom.XformCache,
) -> tuple[np.ndarray, np.ndarray]:
    mesh = UsdGeom.Mesh(prim)
    points = mesh.GetPointsAttr().Get()
    counts = mesh.GetFaceVertexCountsAttr().Get()
    indices = mesh.GetFaceVertexIndicesAttr().Get()
    if not points or not counts or not indices:
        raise ValueError(f"empty enabled collider mesh: {prim.GetPath()}")
    vertices = np.asarray([[p[0], p[1], p[2]] for p in points], dtype=np.float64)
    transform = np.asarray(xforms.GetLocalToWorldTransform(prim), dtype=np.float64)
    homogeneous = np.concatenate(
        [vertices, np.ones((vertices.shape[0], 1), dtype=np.float64)], axis=1)
    transformed = homogeneous @ transform
    if np.any(np.abs(transformed[:, 3]) < 1.0e-12):
        raise ValueError(f"collider transform reaches infinity: {prim.GetPath()}")
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    vertices_m = transformed[:, :3] / transformed[:, 3:4] * mpu
    return np.ascontiguousarray(vertices_m), _triangulate(counts, indices)


def _enabled_collision_meshes(
    stage: Usd.Stage, cache: UsdGeom.BBoxCache,
) -> list[dict]:
    rows = []
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh) or not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:
            continue
        value = _bounds(cache, prim)
        if value is not None:
            rows.append({"prim": prim, "path": str(prim.GetPath()), "bounds_m": value})
    return rows


def evaluate_rasterized_centerline(
    doorway: dict,
    occupied_lateral_cells: np.ndarray,
    cell_blockers: list[set[str]],
    *,
    grid_m: float = RASTER_GRID_M,
    minimum_clear_opening_m: float = MIN_CLEAR_OPENING_M,
) -> dict:
    """Measure the largest unoccupied run on a rasterized doorway line."""
    occupied = np.asarray(occupied_lateral_cells, dtype=np.bool_)
    if occupied.ndim != 1 or occupied.size == 0:
        raise ValueError("doorway centre-line occupancy must be a non-empty 1D raster")
    if len(cell_blockers) != occupied.size or grid_m <= 0:
        raise ValueError("doorway raster provenance differs from its occupancy")
    aperture = tuple(float(value) for value in doorway["aperture_interval_m"])
    cell_widths = np.full(occupied.size, grid_m, dtype=np.float64)
    cell_widths[-1] = min(grid_m, aperture[1] - (
        aperture[0] + (occupied.size - 1) * grid_m))
    best_width = 0.0
    best_start = 0
    best_stop = 0
    cursor = 0
    while cursor < occupied.size:
        if occupied[cursor]:
            cursor += 1
            continue
        stop = cursor
        while stop < occupied.size and not occupied[stop]:
            stop += 1
        width = float(cell_widths[cursor:stop].sum())
        if width > best_width:
            best_width, best_start, best_stop = width, cursor, stop
        cursor = stop
    blocking_paths = sorted({path for index, paths in enumerate(cell_blockers)
                             if occupied[index] for path in paths})
    return {
        "doorway_prim": doorway["prim"],
        "measurement": (
            "maximum unoccupied lateral run in 5 cm rasterized enabled-collider "
            "occupancy at the doorway centre line"),
        "minimum_clear_opening_m": round(best_width, 4),
        "threshold_m": minimum_clear_opening_m,
        "pass": best_width + 1.0e-9 >= minimum_clear_opening_m,
        "nominal_aperture_width_m": round(aperture[1] - aperture[0], 4),
        "aperture_source": doorway["aperture_source"],
        "width_axis": "x" if int(doorway["width_axis"]) == 0 else "y",
        "raster_grid_m": grid_m,
        "raster_cell_count": int(occupied.size),
        "occupied_cell_count": int(np.count_nonzero(occupied)),
        "best_clear_run_cell_range": [best_start, best_stop],
        "collision_blockers": blocking_paths,
    }


def _rasterize_doorway_centerline(
    stage: Usd.Stage,
    doorway: dict,
    collider_rows: list[dict],
    xforms: UsdGeom.XformCache,
    voxel_cache: dict[str, np.ndarray],
) -> dict:
    width_axis = int(doorway["width_axis"])
    depth_axis = 1 - width_axis
    aperture = tuple(float(value) for value in doorway["aperture_interval_m"])
    centre_depth = float(doorway["centre_depth_m"])
    base_z = float(doorway["frame_bounds_m"][2])
    cell_count = max(1, int(math.ceil((aperture[1] - aperture[0]) / RASTER_GRID_M)))
    occupied = np.zeros(cell_count, dtype=np.bool_)
    provenance: list[set[str]] = [set() for _ in range(cell_count)]
    relevant = []
    for row in collider_rows:
        bounds = row["bounds_m"]
        if (bounds[width_axis + 3] <= aperture[0]
                or bounds[width_axis] >= aperture[1]
                or bounds[depth_axis + 3] < centre_depth - RASTER_GRID_M
                or bounds[depth_axis] > centre_depth + RASTER_GRID_M
                or bounds[5] <= base_z + RASTER_GRID_M / 2.0
                or bounds[2] >= base_z + BODY_HEIGHT_M):
            continue
        path = row["path"]
        relevant.append(path)
        if path not in voxel_cache:
            vertices, triangles = _world_mesh_m(stage, row["prim"], xforms)
            mesh = trimesh.Trimesh(vertices=vertices, faces=triangles, process=False)
            voxels = mesh.voxelized(RASTER_GRID_M, method="subdivide")
            points = np.asarray(voxels.points, dtype=np.float64)
            if points.size == 0:
                raise ValueError(f"enabled collider produced no raster cells: {path}")
            voxel_cache[path] = points.reshape(-1, 3)
        points = voxel_cache[path]
        selected = points[
            (np.abs(points[:, depth_axis] - centre_depth) <= RASTER_GRID_M)
            & (points[:, 2] > base_z + RASTER_GRID_M / 2.0)
            & (points[:, 2] < base_z + BODY_HEIGHT_M)
            & (points[:, width_axis] >= aperture[0] - RASTER_GRID_M / 2.0)
            & (points[:, width_axis] <= aperture[1] + RASTER_GRID_M / 2.0)
        ]
        for lateral in selected[:, width_axis] if selected.size else ():
            # Equal-size source voxels are conservatively splatted onto every
            # aperture cell they overlap, matching the G25 bake semantics.
            low = int(math.floor((lateral - RASTER_GRID_M / 2.0 - aperture[0]
                                  + 1.0e-10) / RASTER_GRID_M))
            high = int(math.floor((lateral + RASTER_GRID_M / 2.0 - aperture[0]
                                   - 1.0e-10) / RASTER_GRID_M))
            for index in range(max(0, low), min(cell_count - 1, high) + 1):
                occupied[index] = True
                provenance[index].add(path)
    result = evaluate_rasterized_centerline(doorway, occupied, provenance)
    result["enabled_colliders_intersecting_aperture_bbox"] = sorted(relevant)
    return result


def _frame_geometry(
    cache: UsdGeom.BBoxCache, root: Usd.Prim,
) -> tuple[list[float] | None, list[list[float]]]:
    frame_rows = []
    jamb_rows = []
    root_path = str(root.GetPath()).lower()
    for prim in Usd.PrimRange(root):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        relative_path = str(prim.GetPath()).lower()[len(root_path):]
        name = prim.GetName().lower()
        if "doorway_" not in name or "doorway_door" in relative_path or "doorway_handle" in relative_path:
            continue
        value = _bounds(cache, prim)
        if value is None:
            continue
        frame_rows.append(value)
        if "meshcollider" in name and value[5] - value[2] >= 0.75:
            jamb_rows.append(value)
    return _union_bounds(frame_rows), jamb_rows


def _aperture(
    frame_bounds: list[float], jamb_bounds: list[list[float]], width_axis: int,
) -> tuple[tuple[float, float], str]:
    outer = (frame_bounds[width_axis], frame_bounds[width_axis + 3])
    jamb_intervals = _merge_intervals(
        (row[width_axis], row[width_axis + 3]) for row in jamb_bounds)
    candidates = _subtract(outer, jamb_intervals)
    if candidates:
        return max(candidates, key=lambda row: row[1] - row[0]), "jamb_collider_gap"
    fallback = (outer[0] + FALLBACK_JAMB_WIDTH_M,
                outer[1] - FALLBACK_JAMB_WIDTH_M)
    return fallback, "outer_frame_minus_0.075m_per_jamb"


def evaluate_doorway(
    doorway: dict, furniture: list[dict],
    *, minimum_clear_opening_m: float = MIN_CLEAR_OPENING_M,
    centreline_half_length_m: float = CENTRELINE_HALF_LENGTH_M,
) -> dict:
    """Return the worst clear cross-section along one doorway centre line."""
    width_axis = int(doorway["width_axis"])
    depth_axis = 1 - width_axis
    aperture = tuple(float(value) for value in doorway["aperture_interval_m"])
    centre_depth = float(doorway["centre_depth_m"])
    depth_min = centre_depth - centreline_half_length_m
    depth_max = centre_depth + centreline_half_length_m
    base_z = float(doorway["frame_bounds_m"][2])
    blockers = []
    for record in furniture:
        bounds = [float(value) for value in record["bounds_m"]]
        if min(base_z + BODY_HEIGHT_M, bounds[5]) <= max(base_z, bounds[2]):
            continue
        longitudinal = (
            max(depth_min, bounds[depth_axis]),
            min(depth_max, bounds[depth_axis + 3]),
        )
        lateral = (
            max(aperture[0], bounds[width_axis]),
            min(aperture[1], bounds[width_axis + 3]),
        )
        if longitudinal[1] <= longitudinal[0] or lateral[1] <= lateral[0]:
            continue
        blockers.append({
            "prim": record["prim"],
            "longitudinal_interval_m": longitudinal,
            "lateral_interval_m": lateral,
        })

    events = sorted({depth_min, depth_max, *(
        value for blocker in blockers
        for value in blocker["longitudinal_interval_m"]
    )})
    samples = [(events[i] + events[i + 1]) / 2.0
               for i in range(len(events) - 1) if events[i + 1] > events[i]]
    if not samples:
        samples = [centre_depth]
    cross_sections = []
    for depth in samples:
        active = [blocker for blocker in blockers
                  if blocker["longitudinal_interval_m"][0] <= depth
                  <= blocker["longitudinal_interval_m"][1]]
        clear = _maximum_gap(
            aperture, (blocker["lateral_interval_m"] for blocker in active))
        cross_sections.append((clear, depth, active))
    clear, depth, active = min(cross_sections, key=lambda row: row[0])
    return {
        "doorway_prim": doorway["prim"],
        "measurement": "minimum maximum-free lateral cross-section along doorway centre line",
        "minimum_clear_opening_m": round(clear, 4),
        "threshold_m": minimum_clear_opening_m,
        "pass": clear + 1.0e-9 >= minimum_clear_opening_m,
        "nominal_aperture_width_m": round(aperture[1] - aperture[0], 4),
        "aperture_source": doorway["aperture_source"],
        "width_axis": "x" if width_axis == 0 else "y",
        "centreline_half_length_m": centreline_half_length_m,
        "worst_cross_section_depth_m": round(depth, 4),
        "furniture_blockers": sorted(blocker["prim"] for blocker in active),
    }


def evaluate_scene_records(doorways: list[dict], furniture: list[dict]) -> dict:
    """Legacy extent-only fixture helper; not used by the contract-v2 scan."""
    rows = [evaluate_doorway(doorway, furniture) for doorway in doorways]
    return {
        "contract_version": 1,
        "rule": "no furniture may reduce a doorway centre-line cross-section below 0.60 m",
        "body_diameter_m": BODY_DIAMETER_M,
        "clearance_margin_m": CLEARANCE_MARGIN_M,
        "minimum_clear_opening_m": MIN_CLEAR_OPENING_M,
        "centreline_half_length_m": CENTRELINE_HALF_LENGTH_M,
        "doorway_count": len(rows),
        "failed_doorway_count": sum(not row["pass"] for row in rows),
        "pass": all(row["pass"] for row in rows),
        "doorways": rows,
    }


def scan_stage(stage: Usd.Stage) -> dict:
    """Rasterize actual enabled collision meshes at every doorway centre line."""
    if abs(float(UsdGeom.GetStageMetersPerUnit(stage)) - 1.0) > 1.0e-9:
        raise ValueError("A4 doorway raster currently requires metre-authored source stages")
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), list(PURPOSES))
    doorway_roots = [prim for prim in stage.Traverse()
                     if DOORWAY_ROOT_RE.match(prim.GetName().lower())]
    doorways = []
    for root in doorway_roots:
        root_bounds = _bounds(cache, root)
        frame_bounds, jamb_bounds = _frame_geometry(cache, root)
        frame_bounds = frame_bounds or root_bounds
        if frame_bounds is None:
            continue
        dx = frame_bounds[3] - frame_bounds[0]
        dy = frame_bounds[4] - frame_bounds[1]
        width_axis = 0 if dx >= dy else 1
        depth_axis = 1 - width_axis
        aperture, source = _aperture(frame_bounds, jamb_bounds, width_axis)
        doorways.append({
            "prim": str(root.GetPath()),
            "frame_bounds_m": frame_bounds,
            "width_axis": width_axis,
            "centre_depth_m": (
                frame_bounds[depth_axis] + frame_bounds[depth_axis + 3]) / 2.0,
            "aperture_interval_m": aperture,
            "aperture_source": source,
        })
    source_colliders = _enabled_collision_meshes(stage, cache)
    policy_excluded = [
        row for row in source_colliders
        if leaf_member_kind(row["prim"]) is not None
    ]
    colliders = [
        row for row in source_colliders
        if leaf_member_kind(row["prim"]) is None
    ]
    xforms = UsdGeom.XformCache(Usd.TimeCode.Default())
    voxel_cache: dict[str, np.ndarray] = {}
    rows = [_rasterize_doorway_centerline(
        stage, doorway, colliders, xforms, voxel_cache) for doorway in doorways]
    return {
        "contract_version": DOORWAY_CLEARANCE_CONTRACT_VERSION,
        "rule": (
            "the 5 cm rasterized enabled-collider occupancy at every doorway "
            "centre line must retain a lateral clear gap of at least 0.60 m; "
            "swinging leaves/handles are excluded by the generation policy; "
            "frames, jambs and unrelated appliance doors remain included"),
        "door_collision_policy": "doors_must_not_block_locomotion",
        "door_policy_selection": (
            "doorway_door/doorway_handle branches below doorway asset roots"),
        "body_diameter_m": BODY_DIAMETER_M,
        "clearance_margin_m": CLEARANCE_MARGIN_M,
        "minimum_clear_opening_m": MIN_CLEAR_OPENING_M,
        "raster_grid_m": RASTER_GRID_M,
        "source_enabled_mesh_collider_count": len(source_colliders),
        "door_policy_excluded_collider_count": len(policy_excluded),
        "door_policy_excluded_collider_paths": [
            row["path"] for row in policy_excluded
        ],
        "enabled_mesh_collider_count": len(colliders),
        "rasterized_collider_count": len(voxel_cache),
        "doorway_count": len(rows),
        "failed_doorway_count": sum(not row["pass"] for row in rows),
        "pass": all(row["pass"] for row in rows),
        "doorways": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stage = Usd.Stage.Open(str(args.stage))
    if stage is None:
        raise SystemExit(f"cannot open stage: {args.stage}")
    result = scan_stage(stage)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
