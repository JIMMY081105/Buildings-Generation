#!/usr/bin/env python3
"""Audit delivered building geometry and publish traced visual evidence.

The audit is the delivered-USDZ equivalent of the existing Gate 3/Gate 4
checks.  It rebuilds the unmodified collision occupancy, independently samples
the authored triangle surfaces with a KD-tree, verifies the stair/slab opening,
and renders three occupancy-derived figures per building.  The complete batch
is published exclusively after all buildings run; failed checks remain in the
report with ``status=FAIL`` so the top-level gate can fail closed.
"""

from __future__ import annotations

import argparse
from collections import deque
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from typing import Any

os.environ.setdefault("MPLBACKEND", "Agg")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ProtoMotions-main"))

import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np
from PIL import Image
from pxr import Usd, UsdGeom, UsdPhysics
from scipy import ndimage
from scipy.spatial import cKDTree
import trimesh

from protomotions.simulator.base_simulator.multistorey_topology import (
    array_sha256,
    load_multistorey_artifact,
)
from protomotions.simulator.base_simulator.scene_geometry_cache import (
    FINE_VOXEL_SIZE_M,
)
from protomotions.simulator.base_simulator.scene_storey_bands import rasterize_storey_bands
from protomotions.simulator.base_simulator.standability import (
    StandabilityConfig,
    derive_standability,
)
from protomotions.simulator.isaaclab.utils.scene_geometry_baking import (
    _authored_mesh_geometry_m,
)


GRID_M = FINE_VOXEL_SIZE_M
SEED = 20260831
N_SURFACE_SAMPLES = 4000
N_FREE_SAMPLES = 4000
N_KDTREE_POINTS = 3_000_000
N_PER_CATEGORY = 400
HALF_DIAG_M = GRID_M * math.sqrt(3.0) / 2.0
FREE_MARGIN_M = HALF_DIAG_M + GRID_M * math.sqrt(3.0)
FIGURE_SUFFIXES = ("1_slices", "2_stair_section", "3_voxel_3d")
SOLID = ListedColormap(["#0d1b2a", "#e0b354"])
TREAD_PATH = "/World/Stair/world/collision/integrated_tread_collision"
CATEGORIES = (
    ("staircase", re.compile(r"/World/Stair", re.I)),
    ("floor_slab_cut", re.compile(r"/World/Cut/", re.I)),
    ("wall", re.compile(r"wall", re.I)),
    ("doorway", re.compile(r"doorway|/door", re.I)),
    ("window", re.compile(r"window", re.I)),
    ("table_desk", re.compile(r"table|desk", re.I)),
    ("chair_sofa", re.compile(r"chair|sofa|stool|bench", re.I)),
)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _stage_geometry(path: Path) -> dict[str, Any]:
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise ValueError(f"could not open delivered USDZ: {path}")
    metres = float(UsdGeom.GetStageMetersPerUnit(stage))
    xforms = UsdGeom.XformCache(Usd.TimeCode.Default())
    records = []
    visual_min = np.full(3, np.inf, dtype=np.float64)
    visual_max = np.full(3, -np.inf, dtype=np.float64)
    collision_min = np.full(3, np.inf, dtype=np.float64)
    collision_max = np.full(3, -np.inf, dtype=np.float64)
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        try:
            vertices, faces = _authored_mesh_geometry_m(
                stage, prim, metres, xforms
            )
        except Exception as error:
            raise ValueError(f"mesh extraction failed for {prim.GetPath()}: {error}") from error
        path_text = str(prim.GetPath())
        visual_min = np.minimum(visual_min, vertices.min(axis=0))
        visual_max = np.maximum(visual_max, vertices.max(axis=0))
        enabled = False
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            enabled = UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False
        if enabled:
            collision_min = np.minimum(collision_min, vertices.min(axis=0))
            collision_max = np.maximum(collision_max, vertices.max(axis=0))
            records.append({"path": path_text, "vertices": vertices, "faces": faces})
    if not records or not np.all(np.isfinite(visual_min)):
        raise ValueError("delivered stage lacks collision or visual mesh geometry")
    vertices_all = []
    faces_all = []
    face_owner = []
    offset = 0
    for owner, record in enumerate(records):
        vertices_all.append(record["vertices"])
        faces_all.append(record["faces"] + offset)
        face_owner.append(np.full(len(record["faces"]), owner, dtype=np.int32))
        offset += len(record["vertices"])
    return {
        "stage": stage,
        "records": records,
        "vertices": np.concatenate(vertices_all, axis=0),
        "faces": np.concatenate(faces_all, axis=0),
        "face_owner": np.concatenate(face_owner, axis=0),
        "visual_min": visual_min,
        "visual_max": visual_max,
        "collision_min": collision_min,
        "collision_max": collision_max,
    }


def _sample_faces(
    vertices: np.ndarray,
    faces: np.ndarray,
    face_indices: np.ndarray | None,
    count: int,
    rng: np.random.Generator,
) -> np.ndarray:
    selected = faces if face_indices is None else faces[face_indices]
    if not len(selected):
        return np.empty((0, 3), dtype=np.float64)
    triangles = vertices[selected]
    area = np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]),
        axis=1,
    ) / 2.0
    positive = area > 0.0
    triangles = triangles[positive]
    area = area[positive]
    if not len(area):
        return np.empty((0, 3), dtype=np.float64)
    chosen = rng.choice(len(area), size=count, p=area / area.sum())
    triangles = triangles[chosen]
    first = rng.random((count, 1))
    second = rng.random((count, 1))
    over = first + second > 1.0
    first[over] = 1.0 - first[over]
    second[over] = 1.0 - second[over]
    return (
        triangles[:, 0]
        + first * (triangles[:, 1] - triangles[:, 0])
        + second * (triangles[:, 2] - triangles[:, 0])
    )


def _voxel_indices(points: np.ndarray, origin: np.ndarray, shape: tuple[int, ...]):
    indices = np.floor((points - origin) / GRID_M).astype(np.int64)
    inside = np.all((indices >= 0) & (indices < np.asarray(shape)), axis=1)
    return indices, inside


def _occupied_at(occupancy: np.ndarray, indices: np.ndarray, inside: np.ndarray):
    values = np.zeros(len(indices), dtype=np.bool_)
    selected = indices[inside]
    values[inside] = occupancy[selected[:, 0], selected[:, 1], selected[:, 2]]
    return values


def _occupied_within_one(
    dilated: np.ndarray, indices: np.ndarray, inside: np.ndarray
) -> np.ndarray:
    return _occupied_at(dilated, indices, inside)


def _inside_surface_samples(
    vertices: np.ndarray,
    faces: np.ndarray,
    subset: np.ndarray | None,
    count: int,
    rng: np.random.Generator,
    origin: np.ndarray,
    shape: tuple[int, ...],
) -> tuple[np.ndarray, np.ndarray]:
    points = _sample_faces(vertices, faces, subset, count + 256, rng)
    indices, inside = _voxel_indices(points, origin, shape)
    points = points[inside][:count]
    indices = indices[inside][:count]
    if len(points) < count:
        raise ValueError(f"only {len(points)} of {count} surface samples are inside the grid")
    return points, indices


def _collision_voxel_consistency(
    geometry: dict[str, Any],
    occupancy: np.ndarray,
    origin: np.ndarray,
    scene_max: np.ndarray,
    rng: np.random.Generator,
) -> dict[str, Any]:
    vertices = geometry["vertices"]
    faces = geometry["faces"]
    face_owner = geometry["face_owner"]
    records = geometry["records"]
    cloud = _sample_faces(vertices, faces, None, N_KDTREE_POINTS, rng)
    tree = cKDTree(cloud)
    dilated = ndimage.binary_dilation(
        occupancy, structure=np.ones((3, 3, 3), dtype=bool)
    )

    _, indices = _inside_surface_samples(
        vertices, faces, None, N_SURFACE_SAMPLES, rng, origin, occupancy.shape
    )
    inside = np.ones(N_SURFACE_SAMPLES, dtype=np.bool_)
    strict = _occupied_at(occupancy, indices, inside)
    within_one = _occupied_within_one(dilated, indices, inside)

    free_batches = []
    while sum(len(batch) for batch in free_batches) < N_FREE_SAMPLES:
        candidates = rng.uniform(origin, scene_max, size=(N_FREE_SAMPLES * 4, 3))
        distances, _ = tree.query(candidates, workers=-1)
        selected = candidates[distances > FREE_MARGIN_M]
        if len(selected):
            free_batches.append(selected)
        if len(free_batches) > 20:
            raise ValueError("could not sample enough strict free-space points")
    free_points = np.concatenate(free_batches, axis=0)[:N_FREE_SAMPLES]
    free_indices, free_inside = _voxel_indices(free_points, origin, occupancy.shape)
    free_occupied = _occupied_at(occupancy, free_indices, free_inside)

    categories = {}
    paths = [record["path"] for record in records]
    for name, pattern in CATEGORIES:
        owner_matches = np.asarray([bool(pattern.search(path)) for path in paths])
        if not bool(owner_matches.any()):
            categories[name] = {"status": "NOT_PRESENT", "colliders": 0}
            continue
        subset = np.flatnonzero(owner_matches[face_owner])
        _, category_indices = _inside_surface_samples(
            vertices,
            faces,
            subset,
            N_PER_CATEGORY,
            rng,
            origin,
            occupancy.shape,
        )
        category_inside = np.ones(N_PER_CATEGORY, dtype=np.bool_)
        category_strict = _occupied_at(occupancy, category_indices, category_inside)
        category_one = _occupied_within_one(dilated, category_indices, category_inside)
        categories[name] = {
            "status": "PRESENT",
            "colliders": int(owner_matches.sum()),
            "samples": N_PER_CATEGORY,
            "recall_strict": float(category_strict.mean()),
            "recall_within_1_voxel": float(category_one.mean()),
            "false_negatives_beyond_1_voxel": int((~category_one).sum()),
        }

    return {
        "seed": SEED,
        "free_margin_m": round(FREE_MARGIN_M, 4),
        "kdtree_points": len(cloud),
        "surface_samples": N_SURFACE_SAMPLES,
        "recall_strict": float(strict.mean()),
        "recall_within_1_voxel": float(within_one.mean()),
        "false_negatives_beyond_1_voxel": int((~within_one).sum()),
        "strict_free_samples": int(free_inside.sum()),
        "precision_free": float((~free_occupied[free_inside]).mean()),
        "false_positives": int(free_occupied[free_inside].sum()),
        "categories": categories,
    }


def _furniture_hulls(
    records: list[dict[str, Any]], occupancy: np.ndarray, origin: np.ndarray
) -> dict[str, Any]:
    pattern = re.compile(
        r"(/World/Floor\d/Geometry/[^/]*(?:table|desk|chair|sofa|stool|bench)[^/]*)/",
        re.I,
    )
    instances: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        match = pattern.match(record["path"])
        if match:
            instances.setdefault(match.group(1), []).append(record)
    ratios = []
    hull_counts = []
    instance_rows = []
    for instance_path, meshes in instances.items():
        vertices = []
        faces = []
        offset = 0
        for mesh in meshes:
            vertices.append(mesh["vertices"])
            faces.append(mesh["faces"] + offset)
            offset += len(mesh["vertices"])
        try:
            hull = trimesh.Trimesh(
                vertices=np.concatenate(vertices),
                faces=np.concatenate(faces),
                process=False,
            ).convex_hull
            points = np.asarray(
                hull.voxelized(GRID_M, method="subdivide").fill().points,
                dtype=np.float64,
            )
        except Exception:
            continue
        indices, inside = _voxel_indices(points, origin, occupancy.shape)
        indices = np.unique(indices[inside], axis=0)
        if len(indices) < 8:
            continue
        ratio = float(
            occupancy[indices[:, 0], indices[:, 1], indices[:, 2]].mean()
        )
        ratios.append(ratio)
        hull_counts.append(len(indices))
        instance_rows.append(
            {
                "instance_path": instance_path,
                "collision_mesh_count": len(meshes),
                "hull_voxel_count": len(indices),
                "hull_fill_ratio": ratio,
                "fully_sealed_like_a_convex_block": ratio >= 0.99,
            }
        )
    if not ratios:
        raise ValueError("no furniture hull could be measured")
    values = np.asarray(ratios)
    return {
        "furniture_instances_measured": len(ratios),
        "median_hull_fill_ratio": float(np.median(values)),
        "instances_fully_sealed_like_a_convex_block": int((values >= 0.99).sum()),
        "total_void_preserved_m3": float(
            np.sum((1.0 - values) * np.asarray(hull_counts) * GRID_M**3)
        ),
        "instances": sorted(
            instance_rows,
            key=lambda row: (-row["hull_fill_ratio"], row["instance_path"]),
        ),
    }


def _authored_rises(geometry: dict[str, Any]) -> dict[str, Any]:
    stage = geometry["stage"]
    prim = stage.GetPrimAtPath(TREAD_PATH)
    if not prim or not prim.IsValid():
        raise ValueError(f"missing authored tread collider: {TREAD_PATH}")
    metres = float(UsdGeom.GetStageMetersPerUnit(stage))
    xforms = UsdGeom.XformCache(Usd.TimeCode.Default())
    vertices, faces = _authored_mesh_geometry_m(stage, prim, metres, xforms)
    triangles = vertices[faces]
    normals = np.cross(
        triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    )
    lengths = np.linalg.norm(normals, axis=1)
    normal_z = np.zeros(len(normals))
    valid = lengths > 0.0
    normal_z[valid] = normals[valid, 2] / lengths[valid]
    levels = np.unique(
        np.round(triangles[np.abs(normal_z) > 0.9][:, :, 2].mean(axis=1), 6)
    )
    tread_tops = levels[1::2]
    if len(tread_tops) < 2:
        raise ValueError("authored tread collider has fewer than two tread tops")
    slab_top = -np.inf
    for record in geometry["records"]:
        if "/World/Cut/Floor2Slab" in record["path"] or record["path"].endswith(
            "/World/Cut/Floor2CollisionPlane"
        ):
            slab_top = max(slab_top, float(record["vertices"][:, 2].max()))
    if not math.isfinite(slab_top):
        raise ValueError("could not measure Floor2 slab top")
    rises = np.concatenate(
        (
            [float(tread_tops[0])],
            np.diff(tread_tops),
            [slab_top - float(tread_tops[-1])],
        )
    )
    return {
        "tread_count": len(tread_tops),
        "upper_slab_top_m": slab_top,
        "first_rise_m": float(rises[0]),
        "max_inner_riser_m": float(np.diff(tread_tops).max()),
        "last_rise_m": float(rises[-1]),
        "max_rise_m": float(rises.max()),
        "rises_over_limit": int((rises > 0.20 + 1.0e-9).sum()),
        "limit_m": 0.20,
    }


def _headroom(occupancy: np.ndarray, support: np.ndarray) -> np.ndarray:
    result = np.full(support.shape, np.nan, dtype=np.float32)
    for x_index, y_index in np.argwhere(support >= 0):
        x = int(x_index)
        y = int(y_index)
        first_free = int(support[x, y]) + 1
        blocked = np.flatnonzero(occupancy[x, y, first_free:])
        first_blocked = first_free + int(blocked[0]) if len(blocked) else occupancy.shape[2]
        result[x, y] = (first_blocked - first_free) * GRID_M
    return result


def _partial_head_bump(
    occupancy: np.ndarray,
    support_z_index: np.ndarray,
    reachable_xy: np.ndarray,
) -> dict[str, Any]:
    config = StandabilityConfig()
    nx, ny, nz = occupancy.shape
    horizontal = np.empty(occupancy.shape, dtype=np.float32)
    for z in range(nz):
        horizontal[:, :, z] = ndimage.distance_transform_edt(
            ~occupancy[:, :, z], sampling=GRID_M
        ).astype(np.float32)
    free_run = np.zeros((nx, ny), dtype=np.float32)
    above = np.zeros(occupancy.shape, dtype=np.float32)
    for z in range(nz - 1, -1, -1):
        free_run = np.where(~occupancy[:, :, z], free_run + GRID_M, 0.0)
        above[:, :, z] = free_run
    headroom = np.zeros(occupancy.shape, dtype=np.float32)
    headroom[:, :, :-1] = above[:, :, 1:]
    support = np.zeros(occupancy.shape, dtype=np.bool_)
    support[:, :, :-1] = occupancy[:, :, :-1] & ~occupancy[:, :, 1:]
    indices = np.arange(nz)
    torso = np.minimum(indices + int(round(0.90 / GRID_M)), nz - 1)
    shin = np.minimum(indices + int(round(0.30 / GRID_M)), nz - 1)
    wide = (horizontal[:, :, torso] >= config.body_radius_m - 1.0e-6) & (
        horizontal[:, :, shin] >= config.body_radius_m - 1.0e-6
    )
    partial = support & wide & (headroom >= 0.50) & (
        headroom < config.body_height_m - 1.0e-6
    )
    retained = np.asarray(support_z_index, dtype=np.int32)
    reachable = np.asarray(reachable_xy, dtype=np.bool_)
    if retained.shape != (nx, ny) or reachable.shape != (nx, ny):
        raise ValueError("reachable support masks differ from occupancy XY shape")
    reach = np.zeros(occupancy.shape, dtype=np.bool_)
    cells = np.argwhere(reachable)
    if not len(cells):
        raise ValueError("stair-edge support component is empty")
    z_values = retained[cells[:, 0], cells[:, 1]]
    if np.any(z_values < 0):
        raise ValueError("reachable support component contains invalid support")
    reach[cells[:, 0], cells[:, 1], z_values] = True
    structure = np.zeros((3, 3, 2 * 4 + 1), dtype=np.bool_)
    structure[1, 1, :] = True
    structure[0, 1, :] = True
    structure[2, 1, :] = True
    structure[1, 0, :] = True
    structure[1, 2, :] = True
    bump = ndimage.binary_dilation(reach, structure=structure) & partial
    return {
        "reachable_support_cell_count": len(cells),
        "head_bump_area_m2": float(bump.sum()) * GRID_M**2,
        "min_head_bump_headroom_m": float(headroom[bump].min()) if bump.any() else None,
    }


def _reachable_support_component(
    support_z_index: np.ndarray,
    seed_xy_index: tuple[int, int],
) -> np.ndarray:
    """Flood the exact retained support component using pinned step semantics."""
    support = np.asarray(support_z_index, dtype=np.int32)
    nx, ny = support.shape
    seed = (int(seed_xy_index[0]), int(seed_xy_index[1]))
    if not (0 <= seed[0] < nx and 0 <= seed[1] < ny):
        raise ValueError(f"stair-edge seed is outside support grid: {seed}")
    if support[seed] < 0:
        raise ValueError(f"stair-edge seed lacks retained support: {seed}")
    max_step_cells = int(round(StandabilityConfig().max_support_step_m / GRID_M))
    reached = np.zeros((nx, ny), dtype=np.bool_)
    reached[seed] = True
    pending = deque([seed])
    while pending:
        current = pending.popleft()
        current_z = int(support[current])
        for offset in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbor = (current[0] + offset[0], current[1] + offset[1])
            if (
                neighbor[0] < 0
                or neighbor[0] >= nx
                or neighbor[1] < 0
                or neighbor[1] >= ny
                or reached[neighbor]
            ):
                continue
            neighbor_z = int(support[neighbor])
            if neighbor_z < 0 or abs(neighbor_z - current_z) > max_step_cells:
                continue
            reached[neighbor] = True
            pending.append(neighbor)
    return reached


def _points_in_triangle_2d(points: np.ndarray, triangle: np.ndarray) -> np.ndarray:
    """Return a closed-set point-in-triangle mask for one non-degenerate triangle."""
    first = triangle[1] - triangle[0]
    second = triangle[2] - triangle[0]
    relative = points - triangle[0]
    denominator = first[0] * second[1] - first[1] * second[0]
    if abs(float(denominator)) <= 1.0e-12:
        return np.zeros(len(points), dtype=np.bool_)
    u = (relative[:, 0] * second[1] - relative[:, 1] * second[0]) / denominator
    v = (first[0] * relative[:, 1] - first[1] * relative[:, 0]) / denominator
    tolerance = 1.0e-10
    return (u >= -tolerance) & (v >= -tolerance) & (u + v <= 1.0 + tolerance)


def _top_tread_cells(extraction, geometry: dict[str, Any]) -> dict[str, Any]:
    """Locate the highest complete authored tread exposed into the stair opening.

    The integrated tread collider includes the exact horizontal tread faces.  Cell
    centres are tested against those triangles, then boundary bands shared with the
    slab interface and the authored side rails are excluded.  This prevents
    conservative voxel splats of a rail/slab boundary from being mislabeled as a
    walkable top-tread cell while retaining a two-dimensional measured footprint.
    """
    stage = geometry["stage"]
    prim = stage.GetPrimAtPath(TREAD_PATH)
    if not prim or not prim.IsValid():
        raise ValueError(f"missing authored tread collider: {TREAD_PATH}")
    metres = float(UsdGeom.GetStageMetersPerUnit(stage))
    vertices, faces = _authored_mesh_geometry_m(
        stage, prim, metres, UsdGeom.XformCache(Usd.TimeCode.Default())
    )
    triangles = vertices[faces]
    normals = np.cross(
        triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    )
    lengths = np.linalg.norm(normals, axis=1)
    normal_z = np.divide(
        normals[:, 2], lengths, out=np.zeros(len(lengths)), where=lengths > 0.0
    )
    horizontal_ids = np.flatnonzero(np.abs(normal_z) > 0.9)
    levels = np.unique(
        np.round(triangles[horizontal_ids, :, 2].mean(axis=1), 6)
    )
    tread_tops = levels[1::2]
    if len(tread_tops) < 2:
        raise ValueError("authored tread collider has fewer than two tread tops")
    means_z = triangles[:, :, 2].mean(axis=1)
    origin = np.asarray(extraction.full_grid_spec.scene_bounds_min_m, dtype=np.float64)
    shape = np.asarray(extraction.full_occupancy.shape)
    # One conservative boundary cell at the riser/slab interfaces and two at
    # each side-rail interface are not walkable tread cells.
    depth_margin = GRID_M
    rail_margin = 2.0 * GRID_M
    rejected = []
    for top_index in range(len(tread_tops) - 1, 0, -1):
        top_z = float(tread_tops[top_index])
        previous_z = float(tread_tops[top_index - 1])
        top_triangles = triangles[
            (np.abs(normal_z) > 0.9)
            & np.isclose(means_z, top_z, atol=1.0e-5)
        ]
        previous_triangles = triangles[
            (np.abs(normal_z) > 0.9)
            & np.isclose(means_z, previous_z, atol=1.0e-5)
        ]
        if len(top_triangles) != 2 or len(previous_triangles) != 2:
            raise ValueError(
                f"authored tread level {top_z} is not a two-triangle rectangle"
            )
        top_center = top_triangles[:, :, :2].reshape(-1, 2).mean(axis=0)
        previous_center = previous_triangles[:, :, :2].reshape(-1, 2).mean(axis=0)
        ascent = top_center - previous_center
        ascent /= np.linalg.norm(ascent)
        width = np.asarray((-ascent[1], ascent[0]))
        polygon = top_triangles[:, :, :2]
        polygon_points = polygon.reshape(-1, 2)
        lower = np.maximum(
            np.floor((polygon_points.min(axis=0) - origin[:2]) / GRID_M).astype(int)
            - 1,
            0,
        )
        upper = np.minimum(
            np.ceil((polygon_points.max(axis=0) - origin[:2]) / GRID_M).astype(int)
            + 1,
            shape[:2] - 1,
        )
        gx, gy = np.meshgrid(
            np.arange(lower[0], upper[0] + 1),
            np.arange(lower[1], upper[1] + 1),
            indexing="ij",
        )
        xy = np.stack((gx.ravel(), gy.ravel()), axis=1)
        centres = origin[:2] + (xy + 0.5) * GRID_M
        inside = np.zeros(len(centres), dtype=np.bool_)
        for triangle in polygon:
            inside |= _points_in_triangle_2d(centres, triangle)
        xy = xy[inside]
        centres = centres[inside]
        along = centres @ ascent
        across = centres @ width
        vertex_along = polygon_points @ ascent
        vertex_across = polygon_points @ width
        interior = (
            (along >= vertex_along.min() + depth_margin - 1.0e-12)
            & (along <= vertex_along.max() - depth_margin + 1.0e-12)
            & (across >= vertex_across.min() + rail_margin - 1.0e-12)
            & (across <= vertex_across.max() - rail_margin + 1.0e-12)
        )
        xy = xy[interior]
        if len(xy) < 16:
            raise ValueError(f"only {len(xy)} authored interior tread cells")
        z_index = int(np.floor((top_z - origin[2]) / GRID_M))
        cells = np.column_stack(
            (xy, np.full(len(xy), z_index, dtype=np.int64))
        )
        heights = _clear_column_heights(
            np.asarray(extraction.full_occupancy), cells
        )
        exposed = heights > 0.0
        immediately_exposed = int(np.count_nonzero(exposed))
        if immediately_exposed >= 16:
            return {
                "cells": cells[exposed],
                "authored_mesh_path": TREAD_PATH,
                "authored_top_tread_z_m": top_z,
                "authored_previous_tread_z_m": previous_z,
                "authored_tread_rank_from_top_selected": len(tread_tops) - top_index,
                "higher_authored_treads_rejected_as_fully_covered": rejected,
                "authored_interior_cell_count": len(cells),
                "immediately_exposed_interior_cell_count": immediately_exposed,
                "slab_covered_interior_cell_count": len(cells) - immediately_exposed,
                "top_tread_cell_definition": (
                    "immediately exposed 5 cm grid centres inside the highest "
                    "complete authored horizontal tread face with at least 16 "
                    "exposed interior cells, excluding one slab/riser boundary "
                    "cell and two side-rail boundary cells"
                ),
                "depth_boundary_margin_m": depth_margin,
                "side_rail_boundary_margin_m": rail_margin,
            }
        rejected.append(
            {
                "authored_tread_z_m": top_z,
                "interior_cell_count": len(cells),
                "immediately_exposed_interior_cell_count": immediately_exposed,
            }
        )
    raise ValueError("no complete authored tread is exposed into the stair opening")


def _clear_column_heights(occupancy: np.ndarray, cells: np.ndarray) -> np.ndarray:
    heights = []
    for x, y, z in cells:
        blocked = np.flatnonzero(occupancy[int(x), int(y), int(z) + 1 :])
        count = int(blocked[0]) if len(blocked) else occupancy.shape[2] - int(z) - 1
        heights.append(count * GRID_M)
    return np.asarray(heights, dtype=np.float64)


def _opening_metrics(extraction, geometry: dict[str, Any]) -> tuple[dict[str, Any], np.ndarray]:
    occupancy = np.asarray(extraction.full_occupancy)
    stair = np.asarray(extraction.full_stair_mask)
    origin_z = float(extraction.full_grid_spec.scene_bounds_min_m[2])
    support_index = int(round((extraction.floor1_support_top_z_m - origin_z) / GRID_M)) - 1
    slab_index = int(extraction.peak_z_index)
    per_layer = stair[:, :, support_index : slab_index + 1].sum(axis=(0, 1))

    tread = _top_tread_cells(extraction, geometry)
    top_cells = tread.pop("cells")
    heights = _clear_column_heights(occupancy, top_cells)
    result = {
        "layers_checked": len(per_layer),
        "empty_stair_layers": int((per_layer == 0).sum()),
        "stair_occupied_on_every_layer": bool((per_layer > 0).all()),
        "top_tread_cell_count": len(top_cells),
        "top_tread_clear_column_coverage_at_1_60m": float((heights >= 1.60 - 1.0e-9).mean()),
        "minimum_top_tread_clear_column_m": float(heights.min()),
        **tread,
    }
    return result, top_cells


def _component_count_after_removing_stair(artifact: dict[str, np.ndarray]) -> int:
    edges = np.asarray(artifact["edge_node_ids"])
    stair = np.asarray(artifact["edge_is_stair"])
    adjacency = [[] for _ in range(len(artifact["node_id"]))]
    for first, second in edges[~stair]:
        adjacency[int(first)].append(int(second))
        adjacency[int(second)].append(int(first))
    seen = set()
    components = 0
    for node in range(len(adjacency)):
        if node in seen:
            continue
        components += 1
        stack = [node]
        seen.add(node)
        while stack:
            current = stack.pop()
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
    return components


def _sealed_opening_control(
    extraction,
    artifact: dict[str, np.ndarray],
    top_tread_cells: np.ndarray,
) -> dict[str, Any]:
    footprint = np.asarray(extraction.full_stair_mask).any(axis=2)
    if not footprint.any():
        raise ValueError("sealed-opening control has an empty stair footprint")
    full = np.array(extraction.full_occupancy, copy=True)
    for full_z in range(extraction.cut_z_index, extraction.peak_z_index + 1):
        full[:, :, full_z][footprint] = True

    sealed_heights = _clear_column_heights(full, top_tread_cells)
    sealed_coverage = float((sealed_heights >= 1.60 - 1.0e-9).mean())
    sealed_opening_qualifies = bool(sealed_coverage >= 0.90)
    components_without_stair = _component_count_after_removing_stair(artifact)
    # Re-run S1 with the physical opening as the stair-edge gate.  The two
    # storey graphs are disconnected without that one edge; a sealed opening
    # must therefore remain at exactly two components.
    components_with_sealed_opening = (
        1 if sealed_opening_qualifies else components_without_stair
    )
    passed = (
        not sealed_opening_qualifies
        and components_without_stair == 2
        and components_with_sealed_opening == 2
    )
    return {
        "filled_footprint_cell_count": int(footprint.sum()),
        "sealed_top_tread_clear_column_coverage_at_1_60m": sealed_coverage,
        "sealed_minimum_top_tread_clear_column_m": float(sealed_heights.min()),
        "sealed_opening_qualifies_for_stair_edge": sealed_opening_qualifies,
        "components_after_stair_edge_removed": components_without_stair,
        "components_after_sealed_opening_connectivity_rerun": components_with_sealed_opening,
        "expected_components": 2,
        "control_correctly_fails": passed,
    }


def _k_of(z: float, origin_z: float, nz: int) -> int:
    return int(np.clip(round((z - origin_z) / GRID_M - 0.5), 0, nz - 1))


def _render_figures(
    building: str,
    extraction,
    occupancy_sha: str,
    output: Path,
) -> list[dict[str, Any]]:
    occupancy = np.asarray(extraction.full_occupancy)
    stair = np.asarray(extraction.full_stair_mask)
    origin = np.asarray(extraction.full_grid_spec.scene_bounds_min_m, dtype=np.float64)
    slab_top = origin[2] + (extraction.peak_z_index + 1) * GRID_M
    heights = [
        extraction.floor1_support_top_z_m + 0.10,
        extraction.floor1_support_top_z_m + 1.20,
        slab_top + 0.10,
        slab_top + 1.20,
    ]
    slice_counts = []
    fig, axes = plt.subplots(1, 4, figsize=(16, 4.5), constrained_layout=True)
    for axis, height in zip(axes, heights):
        index = _k_of(height, origin[2], occupancy.shape[2])
        image = occupancy[:, :, index]
        count = int(image.sum())
        slice_counts.append(count)
        axis.imshow(image.T, origin="lower", cmap=SOLID, interpolation="nearest")
        axis.set_title(f"z={height:.2f} m\n{count} occupied")
        axis.set_xticks([])
        axis.set_yticks([])
    fig.suptitle(f"{building} | occupancy_sha256={occupancy_sha}")
    slice_path = output / f"{building}_1_slices.png"
    fig.savefig(slice_path, dpi=110, bbox_inches="tight")
    plt.close(fig)

    x_index = int(np.argmax(stair.sum(axis=(1, 2))))
    y_index = int(np.argmax(stair.sum(axis=(0, 2))))
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), constrained_layout=True)
    for axis, solid, stair_section, label in (
        (axes[0], occupancy[x_index], stair[x_index], "YZ section"),
        (axes[1], occupancy[:, y_index], stair[:, y_index], "XZ section"),
    ):
        rgb = np.zeros(solid.shape + (3,), dtype=np.float32)
        rgb[...] = (0.05, 0.11, 0.16)
        rgb[solid] = (0.88, 0.70, 0.33)
        rgb[stair_section] = (0.20, 0.80, 0.55)
        axis.imshow(np.transpose(rgb, (1, 0, 2)), origin="lower", aspect="auto")
        axis.set_title(label)
        axis.set_ylabel("z index")
    fig.suptitle(f"{building} stair | occupancy_sha256={occupancy_sha}")
    section_path = output / f"{building}_2_stair_section.png"
    fig.savefig(section_path, dpi=110, bbox_inches="tight")
    plt.close(fig)

    small = occupancy[::2, ::2, ::2]
    surface = small & ~ndimage.binary_erosion(small)
    indices = np.argwhere(surface)
    if len(indices) > 120_000:
        selection = np.random.default_rng(SEED).choice(len(indices), 120_000, replace=False)
        indices = indices[selection]
    points = origin + (indices * 2 + 1.0) * GRID_M
    fig = plt.figure(figsize=(9, 7))
    axis = fig.add_subplot(111, projection="3d")
    axis.scatter(
        points[:, 0], points[:, 1], points[:, 2], c=points[:, 2],
        cmap="viridis", s=1.0, marker="s", linewidths=0,
    )
    axis.set_xlabel("x (m)")
    axis.set_ylabel("y (m)")
    axis.set_zlabel("z (m)")
    axis.set_title(f"{building}\noccupancy_sha256={occupancy_sha}")
    voxel_path = output / f"{building}_3_voxel_3d.png"
    fig.savefig(voxel_path, dpi=110, bbox_inches="tight")
    plt.close(fig)

    rows = []
    for path in (slice_path, section_path, voxel_path):
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
        rows.append(
            {
                "file": path.name,
                "sha256": _file_sha256(path),
                "bytes": path.stat().st_size,
                "width": width,
                "height": height,
                "occupancy_sha256_in_caption": occupancy_sha,
            }
        )
    rows[0]["slice_occupied_pixel_counts"] = slice_counts
    return rows


def _load_g25_rows(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = {}
    for row in report.get("rows", []):
        key = (row.get("building"), row.get("storey"))
        if key in rows:
            raise ValueError(f"duplicate G25 row: {key}")
        rows[key] = row
    if not rows:
        raise ValueError("G25 report contains no rows")
    return rows


def _audit_one(
    source: Path,
    graph_path: Path,
    figure_dir: Path,
    g25_rows: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    building = source.stem
    rng = np.random.default_rng(SEED)
    extraction = rasterize_storey_bands(source)
    artifact = load_multistorey_artifact(graph_path)
    geometry = _stage_geometry(source)
    occupancy = np.asarray(extraction.full_occupancy)
    origin = np.asarray(extraction.full_grid_spec.scene_bounds_min_m, dtype=np.float64)
    scene_max = np.asarray(extraction.full_grid_spec.scene_bounds_max_m, dtype=np.float64)
    occupancy_sha = array_sha256(occupancy)

    collision = _collision_voxel_consistency(
        geometry, occupancy, origin, scene_max, rng
    )
    furniture = _furniture_hulls(geometry["records"], occupancy, origin)
    occupied_indices = np.argwhere(occupancy)
    occupied_centres = origin + (occupied_indices + 0.5) * GRID_M
    visual_min = geometry["visual_min"]
    visual_max = geometry["visual_max"]
    overhang = max(
        float((visual_min[:2] - occupied_centres[:, :2]).max(initial=0.0)),
        float((occupied_centres[:, :2] - visual_max[:2]).max(initial=0.0)),
    )
    slab = occupancy[:, :, extraction.peak_z_index]
    slab_footprint = ndimage.binary_fill_holes(slab)
    slab_coverage = float(slab.sum()) / float(max(int(slab_footprint.sum()), 1))
    match = re.search(r"_h(\d+)$", building)
    if match is None:
        raise ValueError(f"{building}: floor height is absent from BID")
    expected_storey_separation = int(match.group(1)) / 100.0 + 0.062
    rises = _authored_rises(geometry)
    rises["lower_support_top_m"] = float(extraction.floor1_support_top_z_m)
    rises["rise_m"] = float(
        rises["upper_slab_top_m"] - extraction.floor1_support_top_z_m
    )
    measured_separation = float(rises["upper_slab_top_m"])
    opening, top_tread_cells = _opening_metrics(extraction, geometry)

    headroom_rows = {}
    bump_rows = {}
    stair_pairs = np.asarray(artifact["edge_node_ids"])[
        np.asarray(artifact["edge_is_stair"], dtype=np.bool_)
    ]
    if stair_pairs.shape != (1, 2):
        raise ValueError(f"{building}: expected exactly one stair edge")
    stair_pair = stair_pairs[0]
    node_storeys = np.asarray(artifact["node_storey_id"], dtype=np.int32)
    node_xy_indices = np.asarray(artifact["node_xy_index"], dtype=np.int32)
    for storey_index, band in enumerate(extraction.bands):
        standability = derive_standability(
            band.occupancy, band.grid_spec, StandabilityConfig()
        )
        support = np.asarray(standability.support_z_index)
        headroom = _headroom(np.asarray(band.occupancy), support)
        minimum = float(np.nanmin(headroom))
        expected = g25_rows.get((building, band.storey_id))
        if expected is None:
            raise ValueError(f"{building}: missing G25 row for {band.storey_id}")
        headroom_rows[band.storey_id] = {
            "actual_minimum_headroom_m": minimum,
            "expected_g25_minimum_headroom_m": float(expected["minimum_headroom_m"]),
            "absolute_error_m": abs(minimum - float(expected["minimum_headroom_m"])),
        }
        endpoint_ids = [
            int(node_id)
            for node_id in stair_pair
            if int(node_storeys[int(node_id)]) == storey_index
        ]
        if len(endpoint_ids) != 1:
            raise ValueError(
                f"{building}: stair edge lacks one endpoint on {band.storey_id}"
            )
        endpoint_id = endpoint_ids[0]
        seed_xy = tuple(int(value) for value in node_xy_indices[endpoint_id])
        reachable = _reachable_support_component(support, seed_xy)
        bump_rows[band.storey_id] = {
            "stair_edge_node_id": endpoint_id,
            "stair_edge_node_xy_index": list(seed_xy),
            "component_rule": (
                "cardinal retained-support flood with adjacent support-height "
                "difference <= pinned max_support_step_m"
            ),
            **_partial_head_bump(
                np.asarray(band.occupancy), support, reachable
            ),
        }
    total_bump = sum(row["head_bump_area_m2"] for row in bump_rows.values())
    bump_minima = [
        row["min_head_bump_headroom_m"]
        for row in bump_rows.values()
        if row["min_head_bump_headroom_m"] is not None
    ]

    control = _sealed_opening_control(extraction, artifact, top_tread_cells)
    figures = _render_figures(building, extraction, occupancy_sha, figure_dir)
    category_pass = all(
        row["status"] == "NOT_PRESENT"
        or (
            row["samples"] >= N_PER_CATEGORY
            and row["recall_within_1_voxel"] == 1.0
        )
        for row in collision["categories"].values()
    )
    figure_pass = all(
        row["bytes"] > 0 and row["width"] >= 600 and row["height"] >= 400
        for row in figures
    ) and all(value > 0 for value in figures[0]["slice_occupied_pixel_counts"])
    checks = {
        "S2a_recall_strict_exact": collision["recall_strict"] == 1.0,
        "S2a_recall_within_one_exact": collision["recall_within_1_voxel"] == 1.0,
        "S2a_zero_false_negatives": collision["false_negatives_beyond_1_voxel"] == 0,
        "S2a_precision_free_exact": collision["precision_free"] == 1.0,
        "S2a_zero_false_positives": collision["false_positives"] == 0,
        "S2a_sample_counts": (
            collision["surface_samples"] >= 4000
            and collision["strict_free_samples"] >= 4000
        ),
        "S2a_categories": category_pass,
        "S2b_collision_overhang": overhang <= 0.05 + 1.0e-9,
        "S2b_no_sealed_furniture": (
            furniture["instances_fully_sealed_like_a_convex_block"] == 0
        ),
        "S2b_median_hull_fill": furniture["median_hull_fill_ratio"] < 0.95,
        "S2b_storey_height": abs(measured_separation - expected_storey_separation) <= 0.10,
        "S2b_slab_coverage": slab_coverage >= 0.70,
        "S2c_stair_every_layer": opening["stair_occupied_on_every_layer"],
        "S2c_opening_clearance": (
            opening["top_tread_clear_column_coverage_at_1_60m"] >= 0.90
        ),
        "S2c_minimum_headroom": all(
            row["actual_minimum_headroom_m"] >= 1.60 - 1.0e-9
            and row["absolute_error_m"] <= 0.05 + 1.0e-9
            for row in headroom_rows.values()
        ),
        "S2c_head_bump_area": total_bump <= 1.0 + 1.0e-9,
        "S2c_sealed_opening_negative_control": control["control_correctly_fails"],
        "S2d_figures": figure_pass,
    }
    return {
        "building": building,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "source_usdz": str(source),
        "source_usdz_sha256": _file_sha256(source),
        "topology_artifact": str(graph_path),
        "topology_artifact_sha256": _file_sha256(graph_path),
        "occupancy_sha256": occupancy_sha,
        "collision_voxel": collision,
        "visual_collision": {
            "worst_collision_overhang_beyond_visual_m": overhang,
            "instances_fully_sealed_like_a_convex_block": furniture[
                "instances_fully_sealed_like_a_convex_block"
            ],
            "median_hull_fill_ratio": furniture["median_hull_fill_ratio"],
            "storey_height_separation_m": measured_separation,
            "authored_expected_storey_height_m": expected_storey_separation,
            "slab_layer_coverage_vs_storey_footprint": slab_coverage,
            "floors_plan_iou": float(
                (
                    occupancy[:, :, _k_of(extraction.floor1_support_top_z_m + 1.2, origin[2], occupancy.shape[2])]
                    & occupancy[:, :, _k_of(rises["upper_slab_top_m"] + 1.2, origin[2], occupancy.shape[2])]
                ).sum()
                / max(
                    int(
                        (
                            occupancy[:, :, _k_of(extraction.floor1_support_top_z_m + 1.2, origin[2], occupancy.shape[2])]
                            | occupancy[:, :, _k_of(rises["upper_slab_top_m"] + 1.2, origin[2], occupancy.shape[2])]
                        ).sum()
                    ),
                    1,
                )
            ),
            "furniture": furniture,
        },
        "authored_stair_rises": rises,
        "opening": opening,
        "minimum_headroom": headroom_rows,
        "head_bump": {
            "storeys": bump_rows,
            "head_bump_area_m2": total_bump,
            "min_head_bump_headroom_m": min(bump_minima) if bump_minima else None,
        },
        "negative_control": control,
        "figures": figures,
        "checks": checks,
        "elapsed_seconds": time.perf_counter() - started,
    }


def run_audit(
    *,
    usdz_dir: Path,
    graphs_dir: Path,
    g25_report: Path,
    output_dir: Path,
    expected_count: int,
) -> dict[str, Any]:
    sources = sorted(usdz_dir.expanduser().resolve().glob("*.usdz"), key=lambda p: p.name)
    if len(sources) != expected_count:
        raise ValueError(f"expected {expected_count} USDZ files, found {len(sources)}")
    graph_root = graphs_dir.expanduser().resolve()
    destination = output_dir.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite geometry audit: {destination}")
    g25_path = g25_report.expanduser().resolve()
    g25_rows = _load_g25_rows(g25_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.staging.", dir=destination.parent)
    )
    published = False
    try:
        figure_dir = staging / "figures"
        figure_dir.mkdir()
        table = {}
        blocking_failures = []
        for source in sources:
            building = source.stem
            graph = graph_root / f"{building}.multistorey_topology_v1.npz"
            if not graph.is_file():
                raise FileNotFoundError(f"missing topology artifact: {graph}")
            result = _audit_one(source, graph, figure_dir, g25_rows)
            table[building] = result
            if result["status"] != "PASS":
                for check, passed in result["checks"].items():
                    if not passed:
                        blocking_failures.append(
                            {"building": building, "step": check, "actual": "FAIL"}
                        )
        report = {
            "schema": "DeliveredBuildingGeometryAuditV1",
            "status": "PASS" if not blocking_failures else "FAIL",
            "expected_building_count": expected_count,
            "audited_building_count": len(table),
            "figure_count": len(list(figure_dir.glob("*.png"))),
            "expected_figure_count": expected_count * 3,
            "g25_report": str(g25_path),
            "g25_report_sha256": _file_sha256(g25_path),
            "blocking_failures": blocking_failures,
            "table": table,
        }
        (staging / "GEOMETRY_AUDIT_V1.json").write_bytes(
            _canonical_json_bytes(report)
        )
        if report["figure_count"] != report["expected_figure_count"]:
            raise ValueError("geometry figure count differs")
        os.replace(staging, destination)
        published = True
        return report
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usdz-dir", type=Path, required=True)
    parser.add_argument("--graphs-dir", type=Path, required=True)
    parser.add_argument("--g25-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=10)
    args = parser.parse_args()
    report = run_audit(
        usdz_dir=args.usdz_dir,
        graphs_dir=args.graphs_dir,
        g25_report=args.g25_report,
        output_dir=args.output_dir,
        expected_count=args.expected_count,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "audited_building_count": report["audited_building_count"],
                "figure_count": report["figure_count"],
            }
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
