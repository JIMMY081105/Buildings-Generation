"""Read the collision geometry a stage actually authors.

Everything downstream - bake, storey detection, stair measurement, every gate -
consumes colliders through this module, so there is exactly one answer to
"which prims are solid" and one triangulation path.  Visual meshes without a
``UsdPhysics.CollisionAPI`` are deliberately invisible here: a policy collides
with colliders, not with renders.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics

#: Approximations this pipeline accepts.  Anything else means the collider was
#: cooked into a convex solid somewhere upstream, which turns a staircase into
#: a ramp; Gate 1 refuses the stage rather than baking it.
EXACT_APPROXIMATION = "none"
SUPPORTED_APPROXIMATIONS = (EXACT_APPROXIMATION,)
ALL_KNOWN_APPROXIMATIONS = (
    "none", "convexHull", "convexDecomposition", "meshSimplification",
    "boundingCube", "boundingSphere", "sdf", "sphereFill",
)


@dataclass(frozen=True)
class ColliderRecord:
    prim_path: str
    approximation: str
    triangle_count: int
    bounds_min_m: tuple[float, float, float]
    bounds_max_m: tuple[float, float, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "prim_path": self.prim_path,
            "approximation": self.approximation,
            "triangle_count": self.triangle_count,
            "bounds_min_m": list(self.bounds_min_m),
            "bounds_max_m": list(self.bounds_max_m),
        }


def meters_per_unit(stage: Usd.Stage) -> float:
    value = float(UsdGeom.GetStageMetersPerUnit(stage))
    if not np.isfinite(value) or value <= 0.0:
        raise ValueError("stage metersPerUnit must be finite and positive")
    return value


def collision_approximation(prim: Usd.Prim) -> str:
    """The authored approximation, defaulting to the exact mesh."""

    attribute = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr()
    value = attribute.Get() if attribute else None
    return str(value or EXACT_APPROXIMATION)


def collision_enabled(prim: Usd.Prim) -> bool:
    api = UsdPhysics.CollisionAPI(prim)
    if not api:
        return False
    attribute = api.GetCollisionEnabledAttr()
    value = attribute.Get() if attribute else None
    # An unauthored attribute means "enabled"; only an explicit False disables.
    return value is not False


def triangulate(counts: Sequence[int], indices: Sequence[int]) -> np.ndarray:
    """Fan-triangulate arbitrary polygons into a [T, 3] index array."""

    counts = np.asarray(counts, dtype=np.int64)
    indices = np.asarray(indices, dtype=np.int64)
    if counts.size == 0:
        return np.zeros((0, 3), dtype=np.int64)
    if int(counts.sum()) != indices.size:
        raise ValueError("faceVertexIndices length does not match faceVertexCounts")
    triangles: list[tuple[int, int, int]] = []
    cursor = 0
    for count in counts:
        count = int(count)
        if count < 3:
            cursor += count
            continue
        face = indices[cursor: cursor + count]
        for offset in range(1, count - 1):
            triangles.append((int(face[0]), int(face[offset]), int(face[offset + 1])))
        cursor += count
    return np.asarray(triangles, dtype=np.int64).reshape(-1, 3)


def world_triangles_m(
    prim: Usd.Prim,
    *,
    mpu: float,
    xform_cache: UsdGeom.XformCache,
) -> tuple[np.ndarray, np.ndarray]:
    """World-space vertices in metres and their triangle indices."""

    mesh = UsdGeom.Mesh(prim)
    if not mesh:
        raise ValueError(f"{prim.GetPath()} is not a UsdGeom.Mesh")
    points = np.asarray(mesh.GetPointsAttr().Get() or [], dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or points.shape[0] == 0:
        raise ValueError(f"{prim.GetPath()} authors no points")
    triangles = triangulate(
        mesh.GetFaceVertexCountsAttr().Get() or [],
        mesh.GetFaceVertexIndicesAttr().Get() or [],
    )
    if triangles.shape[0] == 0:
        raise ValueError(f"{prim.GetPath()} authors no triangles")
    matrix = np.asarray(xform_cache.GetLocalToWorldTransform(prim), dtype=np.float64)
    homogeneous = np.concatenate([points, np.ones((points.shape[0], 1))], axis=1)
    # USD matrices are row-vector convention: p' = p * M.
    world = homogeneous @ matrix
    return np.ascontiguousarray(world[:, :3] * mpu), triangles


def iter_colliders(
    stage: Usd.Stage,
    *,
    exclude_prefixes: Sequence[str] = (),
) -> Iterator[Usd.Prim]:
    """Every enabled mesh collider on the composed stage, in traversal order."""

    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        if not prim.HasAPI(UsdPhysics.CollisionAPI) or not collision_enabled(prim):
            continue
        path = str(prim.GetPath())
        if any(path == prefix or path.startswith(prefix + "/") for prefix in exclude_prefixes):
            continue
        yield prim


def collect_colliders(
    stage: Usd.Stage,
    *,
    exclude_prefixes: Sequence[str] = (),
) -> tuple[list[ColliderRecord], np.ndarray, np.ndarray]:
    """Records plus the world-space AABB of the whole collider set, in metres.

    Raises when the stage has no enabled collider: an "empty" building is
    almost always a composition or path mistake, and baking it would produce a
    cache that every gate then passes vacuously.
    """

    mpu = meters_per_unit(stage)
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    records: list[ColliderRecord] = []
    lower = np.full(3, np.inf)
    upper = np.full(3, -np.inf)
    for prim in iter_colliders(stage, exclude_prefixes=exclude_prefixes):
        vertices, triangles = world_triangles_m(prim, mpu=mpu, xform_cache=cache)
        low = vertices.min(axis=0)
        high = vertices.max(axis=0)
        lower = np.minimum(lower, low)
        upper = np.maximum(upper, high)
        records.append(
            ColliderRecord(
                prim_path=str(prim.GetPath()),
                approximation=collision_approximation(prim),
                triangle_count=int(triangles.shape[0]),
                bounds_min_m=tuple(float(v) for v in low),  # type: ignore[arg-type]
                bounds_max_m=tuple(float(v) for v in high),  # type: ignore[arg-type]
            )
        )
    if not records:
        raise ValueError("composed stage contains no enabled mesh colliders")
    return records, lower, upper


def rigid_body_prims(stage: Usd.Stage) -> list[str]:
    """Prims carrying ``UsdPhysics.RigidBodyAPI``.

    A static geometry cache may not contain dynamic links: they move at
    runtime, so anything baked from them is wrong from the first physics step.
    Gate 1 reports these; the bake excludes their subtrees.
    """

    return [
        str(prim.GetPath())
        for prim in stage.Traverse()
        if prim.HasAPI(UsdPhysics.RigidBodyAPI)
    ]


def top_level_rigid_body_roots(stage: Usd.Stage) -> list[str]:
    """``rigid_body_prims`` with nested entries dropped."""

    roots: list[str] = []
    for path in sorted(rigid_body_prims(stage)):
        if any(path.startswith(root + "/") for root in roots):
            continue
        roots.append(path)
    return roots
