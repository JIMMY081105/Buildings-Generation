"""Exact triangle voxelisation into the aligned grid.

Derived from NVIDIA ProtoMotions ``scene_geometry_baking`` (Apache-2.0); see
NOTICE and docs/provenance.md.

"Exact" means: every collider is rasterised from the triangles it actually
authors.  No convex cooking, no hull substitution, no simplification.  A stair
whose treads are cooked into a convex hull is a ramp, and a ramp passes every
reachability check for the wrong reason - this is the single most important
property of the bake, which is why the policy string is written into the
manifest and Gate 2 refuses a cache that does not carry it.

The splat is *conservative*: a source voxel cube marks every target cell it
overlaps.  Errors therefore make the scene more solid, never more open.  The
resulting tolerance is derived, not tuned - see ``SPLAT_TOLERANCE_M``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from .grid import FINE_VOXEL_SIZE_M, GridSpec

#: Half the diagonal of one fine voxel: the furthest a marked cell centre can
#: sit from the surface that marked it under ``trimesh`` subdivision.
SUBDIVISION_TOLERANCE_M = 0.5 * math.sqrt(3.0) * FINE_VOXEL_SIZE_M
#: The conservative splat may add at most one further voxel on each axis.
SPLAT_TOLERANCE_M = SUBDIVISION_TOLERANCE_M + FINE_VOXEL_SIZE_M * math.sqrt(3.0) / 2.0 * 2.0

EXACT_BAKE_POLICY = (
    "Exact bake: every collider is approximation=none and is rasterised from "
    "its authored triangle mesh. No convex cooking is involved."
)


def voxelize_triangle_mesh(
    vertices_m: np.ndarray,
    triangles: np.ndarray,
    *,
    voxel_size_m: float = FINE_VOXEL_SIZE_M,
    fill: bool,
) -> np.ndarray:
    """Return surface (``fill=False``) or solid (``fill=True``) voxel centres."""

    import trimesh

    vertices = np.asarray(vertices_m, dtype=np.float64)
    faces = np.asarray(triangles, dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or vertices.shape[0] < 3:
        raise ValueError("vertices_m must have shape [V>=3, 3]")
    if faces.ndim != 2 or faces.shape[1] != 3 or faces.shape[0] == 0:
        raise ValueError("triangles must have shape [T>0, 3]")
    if faces.min() < 0 or faces.max() >= vertices.shape[0]:
        raise ValueError("triangle index is outside the vertex array")
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    voxels = mesh.voxelized(voxel_size_m, method="subdivide")
    if fill:
        voxels = voxels.fill()
    points = np.asarray(voxels.points, dtype=np.float64)
    if points.size == 0:
        raise RuntimeError("triangle mesh produced no occupied voxels")
    return np.ascontiguousarray(points.reshape(-1, 3))


def conservative_splat(
    occupancy: np.ndarray,
    source_centers_m: np.ndarray,
    *,
    grid_origin_m: Sequence[float],
    voxel_size_m: float = FINE_VOXEL_SIZE_M,
) -> int:
    """Mark every target cell overlapped by an equal-size source voxel cube.

    Returns the number of source centres that hit at least one in-bounds cell.
    Lattice phases may differ, so one cube can mark up to eight cells.
    """

    if occupancy.ndim != 3 or occupancy.dtype != np.bool_:
        raise ValueError("occupancy must be a rank-3 bool array")
    centers = np.asarray(source_centers_m, dtype=np.float64)
    if centers.ndim != 2 or centers.shape[1] != 3:
        raise ValueError("source_centers_m must have shape [N, 3]")
    if not np.all(np.isfinite(centers)):
        raise ValueError("source voxel centres must be finite")
    origin = np.asarray(grid_origin_m, dtype=np.float64)
    if origin.shape != (3,) or not np.all(np.isfinite(origin)):
        raise ValueError("grid_origin_m must contain three finite values")
    if voxel_size_m <= 0 or not math.isfinite(voxel_size_m):
        raise ValueError("voxel_size_m must be positive and finite")
    if centers.shape[0] == 0:
        return 0

    epsilon = voxel_size_m * 1.0e-9
    lower = np.floor((centers - 0.5 * voxel_size_m - origin + epsilon) / voxel_size_m)
    upper = np.floor((centers + 0.5 * voxel_size_m - origin - epsilon) / voxel_size_m)
    lower = lower.astype(np.int64)
    upper = upper.astype(np.int64)
    if np.any(upper - lower > 1):
        raise RuntimeError("equal-size source voxel overlapped more than two cells per axis")

    shape = np.asarray(occupancy.shape, dtype=np.int64)
    hit = np.zeros(centers.shape[0], dtype=bool)
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                offset = np.array([dx, dy, dz], dtype=np.int64)
                index = np.minimum(lower + offset, upper)
                inside = np.all((index >= 0) & (index < shape), axis=1)
                if not inside.any():
                    continue
                selected = index[inside]
                occupancy[selected[:, 0], selected[:, 1], selected[:, 2]] = True
                hit[inside] = True
    return int(hit.sum())


def is_watertight(vertices_m: np.ndarray, triangles: np.ndarray) -> bool:
    import trimesh

    mesh = trimesh.Trimesh(vertices=vertices_m, faces=triangles, process=False)
    return bool(mesh.is_watertight)


def rasterize_into(
    occupancy: np.ndarray,
    grid: GridSpec,
    vertices_m: np.ndarray,
    triangles: np.ndarray,
    *,
    fill: bool | str = "auto",
) -> int:
    """Voxelise one mesh and splat it into ``occupancy``.

    ``fill="auto"`` fills watertight colliders and surface-rasterises open
    ones.  This matters more than it looks: surface-rasterising a closed box
    leaves its interior marked free, and under the terrain-following clearance
    model the cavity inside a hollow slab then reads as a standable floor one
    slab-thickness below the real one.  A watertight collider is a solid, so
    its interior is solid.  An open mesh has no defined interior and is left
    as a surface.

    Raises if the mesh lands entirely outside the grid, because that always
    means the grid bounds were computed from a different collider set than the
    one being baked.
    """

    if fill == "auto":
        fill = is_watertight(vertices_m, triangles)

    centers = voxelize_triangle_mesh(
        vertices_m, triangles, voxel_size_m=grid.fine_voxel_size_m, fill=fill
    )
    splatted = conservative_splat(
        occupancy,
        centers,
        grid_origin_m=grid.scene_bounds_min_m,
        voxel_size_m=grid.fine_voxel_size_m,
    )
    if splatted == 0:
        raise RuntimeError("voxelised mesh lies entirely outside the cache grid")
    return splatted


def signed_clearance(occupancy: np.ndarray, voxel_size_m: float) -> np.ndarray:
    """Distance to the nearest occupied cell, negative inside occupied cells.

    Free cells get ``+distance to the nearest occupied cell``; occupied cells
    get ``-distance to the nearest free cell``.  Both are Euclidean distance
    transforms in metres.
    """

    from scipy import ndimage

    occupied = np.asarray(occupancy, dtype=bool)
    outside = ndimage.distance_transform_edt(~occupied, sampling=voxel_size_m)
    inside = ndimage.distance_transform_edt(occupied, sampling=voxel_size_m)
    return np.asarray(outside - inside, dtype=np.float32)


def downsample_max(values: np.ndarray, factor: int) -> np.ndarray:
    """Reduce a fine field to the coarse tier by taking the block minimum.

    Clearance must not be optimistic after downsampling, so the *minimum* of
    each block is kept: a coarse cell is only as clear as its tightest fine
    cell.
    """

    if any(size % factor for size in values.shape):
        raise ValueError("fine shape must tile whole coarse blocks")
    shape = (
        values.shape[0] // factor, factor,
        values.shape[1] // factor, factor,
        values.shape[2] // factor, factor,
    )
    return values.reshape(shape).min(axis=(1, 3, 5))
