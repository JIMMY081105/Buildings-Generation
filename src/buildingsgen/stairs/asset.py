"""Author a stair asset as USD with exact colliders.

This is the dependency-light backend: pure OpenUSD, no Blender, no Infinigen.
It produces a flight that is geometrically correct for the acceptance gates -
treads on the riser grid, ``n - 1`` authored treads, a turn platform where the
family needs one - which is what the rest of the pipeline actually consumes.

For richly detailed assets (materials, balusters, stringer profiles) use
:mod:`buildingsgen.stairs.infinigen_backend` instead.  Both emit the same
prim layout and the same ``traversal_path.json``, so downstream code does not
care which produced a given asset:

    /Stair
        /Stair/Treads/tread_000 ...   Mesh, CollisionAPI, approximation=none
        /Stair/Landing/platform_0     Mesh, present only for L- and U-shaped
        /Stair/Handrail/rail_*        Mesh, CollisionAPI (obstacle, not support)

The last tread is deliberately absent: the upper slab is tread ``n``.  See
:mod:`buildingsgen.stairs.spec`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pxr import Sdf, Usd, UsdGeom, UsdPhysics

from .spec import StairSpec, traversal_path

DEFAULT_TREAD_THICKNESS_M = 0.06
DEFAULT_HANDRAIL_HEIGHT_M = 0.95
DEFAULT_HANDRAIL_THICKNESS_M = 0.05

STAIR_ROOT = "/Stair"
TREAD_SCOPE = f"{STAIR_ROOT}/Treads"
LANDING_SCOPE = f"{STAIR_ROOT}/Landing"
HANDRAIL_SCOPE = f"{STAIR_ROOT}/Handrail"


@dataclass(frozen=True)
class Box:
    """Axis-aligned box in the stair's local frame, metres."""

    center: tuple[float, float, float]
    extent: tuple[float, float, float]

    def corners(self) -> np.ndarray:
        cx, cy, cz = self.center
        hx, hy, hz = (value * 0.5 for value in self.extent)
        return np.array(
            [
                (cx - hx, cy - hy, cz - hz), (cx + hx, cy - hy, cz - hz),
                (cx + hx, cy + hy, cz - hz), (cx - hx, cy + hy, cz - hz),
                (cx - hx, cy - hy, cz + hz), (cx + hx, cy - hy, cz + hz),
                (cx + hx, cy + hy, cz + hz), (cx - hx, cy + hy, cz + hz),
            ],
            dtype=np.float64,
        )


#: Quad faces of a box, outward-facing.
_BOX_FACES = (
    (0, 3, 2, 1),  # bottom
    (4, 5, 6, 7),  # top
    (0, 1, 5, 4),
    (1, 2, 6, 5),
    (2, 3, 7, 6),
    (3, 0, 4, 7),
)


def tread_boxes(spec: StairSpec, *, thickness_m: float = DEFAULT_TREAD_THICKNESS_M) -> list[Box]:
    """One box per authored tread, its **top** face on the riser grid.

    Placing the top rather than the underside on the grid is the difference
    between a first step of ``riser`` and one of ``riser + thickness``.  The
    latter is what an un-corrected upstream factory produces, and at a 0.175 m
    riser plus a 0.06 m plate it lands above the 0.20 m support step - the
    flight becomes unclimbable while every render still looks fine.
    """

    if thickness_m <= 0 or thickness_m >= spec.riser_m:
        raise ValueError("tread thickness must be positive and below one riser")
    path = traversal_path(spec)
    boxes: list[Box] = []
    nosing = min(0.03, 0.5 * spec.going_m)
    for index in range(spec.authored_tread_count):
        point = path[index + 1]
        previous = path[index]
        top_z = (index + 1) * spec.riser_m
        along_x = abs(point[0] - previous[0]) > abs(point[1] - previous[1])
        length = spec.going_m + nosing
        extent = (
            (length, spec.width_m, thickness_m) if along_x
            else (spec.width_m, length, thickness_m)
        )
        boxes.append(Box(center=(point[0], point[1], top_z - 0.5 * thickness_m), extent=extent))
    return boxes


def landing_boxes(spec: StairSpec, *, thickness_m: float = DEFAULT_TREAD_THICKNESS_M) -> list[Box]:
    """The turn platform of an L- or U-shaped flight, if any."""

    if spec.turn_after_riser is None:
        return []
    path = traversal_path(spec)
    top_z = spec.turn_after_riser * spec.riser_m
    turning = [point for point in path if abs(point[2] - top_z) < 1e-9]
    if len(turning) < 2:
        return []
    xs = [point[0] for point in turning]
    ys = [point[1] for point in turning]
    size_x = max(max(xs) - min(xs) + spec.width_m, spec.width_m)
    size_y = max(max(ys) - min(ys) + spec.width_m, spec.width_m)
    center = (0.5 * (max(xs) + min(xs)), 0.5 * (max(ys) + min(ys)), top_z - 0.5 * thickness_m)
    return [Box(center=center, extent=(size_x, size_y, thickness_m))]


def handrail_boxes(
    spec: StairSpec,
    *,
    height_m: float = DEFAULT_HANDRAIL_HEIGHT_M,
    thickness_m: float = DEFAULT_HANDRAIL_THICKNESS_M,
) -> list[Box]:
    """Rail volumes alongside the flight.

    These are **obstacles**, not walking support: they exist so the bake sees
    the real free width of the flight.  A stair that passes a width check only
    because its handrails were left out is a stair that fails in simulation.
    """

    if spec.handrail == "none":
        return []
    path = traversal_path(spec)
    sides = (1, -1) if spec.handrail == "double" else (1,)
    boxes: list[Box] = []
    for index in range(len(path) - 1):
        start = np.asarray(path[index], dtype=np.float64)
        end = np.asarray(path[index + 1], dtype=np.float64)
        delta = end - start
        if np.hypot(delta[0], delta[1]) < 1e-9:
            continue
        along_x = abs(delta[0]) > abs(delta[1])
        mid = 0.5 * (start + end)
        length = float(abs(delta[0] if along_x else delta[1]))
        offset = 0.5 * spec.width_m
        for side in sides:
            if along_x:
                center = (mid[0], mid[1] + side * offset, mid[2] + 0.5 * height_m)
                extent = (length, thickness_m, thickness_m)
            else:
                center = (mid[0] + side * offset, mid[1], mid[2] + 0.5 * height_m)
                extent = (thickness_m, length, thickness_m)
            boxes.append(Box(center=center, extent=extent))
    return boxes


def local_bounds_m(
    spec: StairSpec,
    *,
    thickness_m: float = DEFAULT_TREAD_THICKNESS_M,
    include_handrail: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """AABB of the whole flight in its own frame, metres.

    Placement needs the real extent, not the nominal ``run x width``: a
    U-shaped flight reaches into -X, and its handrails stick out further than
    its treads.  Deriving the box from the same geometry the asset will author
    keeps the placed stair inside the footprint the search reserved for it.
    """

    boxes = tread_boxes(spec, thickness_m=thickness_m)
    boxes += landing_boxes(spec, thickness_m=thickness_m)
    if include_handrail:
        boxes += handrail_boxes(spec)
    corners = np.concatenate([box.corners() for box in boxes], axis=0)
    return corners.min(axis=0), corners.max(axis=0)


def world_triangles(
    spec: StairSpec,
    *,
    translate_m: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotate_z_deg: float = 0.0,
    thickness_m: float = DEFAULT_TREAD_THICKNESS_M,
    include_handrail: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """The flight's geometry at a pose, without writing a USD file.

    Placement needs to know what the stair will actually occupy *before*
    committing to it.  Deriving that from the same box functions the asset
    writer uses means the check and the asset cannot drift apart.
    """

    import math

    boxes = tread_boxes(spec, thickness_m=thickness_m)
    boxes += landing_boxes(spec, thickness_m=thickness_m)
    if include_handrail:
        boxes += handrail_boxes(spec)
    angle = math.radians(rotate_z_deg)
    cos, sin = math.cos(angle), math.sin(angle)
    rotation = np.array([[cos, -sin, 0.0], [sin, cos, 0.0], [0.0, 0.0, 1.0]])
    offset = np.asarray(translate_m, dtype=np.float64)

    vertices: list[np.ndarray] = []
    triangles: list[np.ndarray] = []
    base = 0
    for box in boxes:
        corners = box.corners() @ rotation.T + offset
        vertices.append(corners)
        for face in _BOX_FACES:
            triangles.append(np.array([face[0], face[1], face[2]]) + base)
            triangles.append(np.array([face[0], face[2], face[3]]) + base)
        base += corners.shape[0]
    return (
        np.concatenate(vertices, axis=0),
        np.asarray(triangles, dtype=np.int64).reshape(-1, 3),
    )


def _define_box_mesh(stage: Usd.Stage, path: str, box: Box, *, collider: bool) -> Usd.Prim:
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([tuple(point) for point in box.corners()])
    mesh.CreateFaceVertexCountsAttr([4] * len(_BOX_FACES))
    mesh.CreateFaceVertexIndicesAttr([index for face in _BOX_FACES for index in face])
    mesh.CreateExtentAttr(
        [
            tuple(box.corners().min(axis=0)),
            tuple(box.corners().max(axis=0)),
        ]
    )
    prim = mesh.GetPrim()
    if collider:
        UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr().Set(True)
        UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr().Set(UsdPhysics.Tokens.none)
    return prim


def build_stair_usd(
    spec: StairSpec,
    destination: Path,
    *,
    tread_thickness_m: float = DEFAULT_TREAD_THICKNESS_M,
    include_handrail: bool = True,
) -> dict[str, Any]:
    """Write a stair asset and return its record.

    The record - dimensions, prim paths, traversal path - is what an evidence
    file should cite; ``traversal_path.json`` is written next to the stage.
    """

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(destination))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, STAIR_ROOT)
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.Scope.Define(stage, TREAD_SCOPE)

    treads = tread_boxes(spec, thickness_m=tread_thickness_m)
    tread_paths = []
    for index, box in enumerate(treads):
        path = f"{TREAD_SCOPE}/tread_{index:03d}"
        _define_box_mesh(stage, path, box, collider=True)
        tread_paths.append(path)

    landing_paths = []
    landings = landing_boxes(spec, thickness_m=tread_thickness_m)
    if landings:
        UsdGeom.Scope.Define(stage, LANDING_SCOPE)
        for index, box in enumerate(landings):
            path = f"{LANDING_SCOPE}/platform_{index}"
            _define_box_mesh(stage, path, box, collider=True)
            landing_paths.append(path)

    rail_paths = []
    rails = handrail_boxes(spec) if include_handrail else []
    if rails:
        scope = UsdGeom.Scope.Define(stage, HANDRAIL_SCOPE)
        scope.GetPrim().SetCustomDataByKey(
            "navigationRole", "physical_obstacle_not_walking_support"
        )
        for index, box in enumerate(rails):
            path = f"{HANDRAIL_SCOPE}/rail_{index:03d}"
            _define_box_mesh(stage, path, box, collider=True)
            rail_paths.append(path)

    stage.GetRootLayer().Save()

    path_points = traversal_path(spec)
    record: dict[str, Any] = {
        "schema": "StairAssetV1",
        "backend": "buildingsgen.stairs.asset",
        "stage": str(destination),
        "spec": spec.as_dict(),
        "tread_thickness_m": tread_thickness_m,
        "prims": {
            "root": STAIR_ROOT,
            "treads": tread_paths,
            "landings": landing_paths,
            "handrails": rail_paths,
        },
        "authored_tread_count": len(tread_paths),
        "upper_floor_supplies_last_tread": True,
        "traversal_path_m": [list(point) for point in path_points],
    }
    (destination.parent / "traversal_path.json").write_text(
        json.dumps(
            {
                "schema": "StairTraversalPathV1",
                "stage": str(destination),
                "points_m": [list(point) for point in path_points],
                "total_rise_m": spec.total_rise_m,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return record


def reference_stair_into(
    stage: Usd.Stage,
    asset_path: Path,
    *,
    prim_path: str,
    translate_m: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotate_z_deg: float = 0.0,
) -> Usd.Prim:
    """Reference a stair asset into a building stage at a pose.

    Referencing rather than copying keeps the asset a single source of truth:
    one stair file, many placements, and a hash that still identifies the
    geometry after the building is flattened.
    """

    xform = UsdGeom.Xform.Define(stage, prim_path)
    prim = xform.GetPrim()
    prim.GetReferences().AddReference(Sdf.Reference(str(Path(asset_path))))
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    # xformOpOrder applies its *last* entry first, so [translate, rotateZ]
    # means "rotate the asset about its own origin, then move it into place".
    # Authoring them the other way round rotates the placement itself, which
    # puts the flight outside the building for every orientation but 0.
    xform.AddTranslateOp().Set(tuple(value / mpu for value in translate_m))
    if rotate_z_deg:
        xform.AddRotateZOp().Set(float(rotate_z_deg))
    return prim
