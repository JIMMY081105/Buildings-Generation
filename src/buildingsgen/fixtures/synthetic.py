"""Synthetic buildings, so the pipeline can be tested without a corpus.

These are not toys for a README: they are how the gates get inputs that are
*known* to be good or *known* to be broken.  A gate that has only ever seen
real buildings has never been shown to fail.

``build_synthetic_building`` writes a Z-up, metre-scale USD with exact mesh
colliders - perimeter walls, a slab per upper storey with a rectangular
opening, and no ground slab (the simulator terrain is the ground floor, which
is what real corpora do).  ``defect`` injects one specific, realistic fault:

    "sealed_opening"   the upper slab has no hole - the storeys are separate
    "low_headroom"     the roof sits 1.2 m over the upper floor - standable by
                       a point, not by a 1.60 m body
    "narrow_slot"      the only route on the upper storey is a 0.30 m slot -
                       passable unless the body radius erodes the whole region
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics

Defect = Literal["sealed_opening", "low_headroom", "narrow_slot"]

_BOX_FACES = (
    (0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
)


@dataclass(frozen=True)
class SyntheticPlan:
    storeys: int
    storey_height_m: float
    interior_m: tuple[float, float]
    wall_thickness_m: float
    slab_thickness_m: float
    opening_min_m: tuple[float, float]
    opening_max_m: tuple[float, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "storeys": self.storeys,
            "storey_height_m": self.storey_height_m,
            "interior_m": list(self.interior_m),
            "wall_thickness_m": self.wall_thickness_m,
            "slab_thickness_m": self.slab_thickness_m,
            "opening_min_m": list(self.opening_min_m),
            "opening_max_m": list(self.opening_max_m),
        }


def _box(stage: Usd.Stage, path: str, low: tuple[float, ...], high: tuple[float, ...]) -> None:
    low = np.asarray(low, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    if np.any(high - low <= 0):
        raise ValueError(f"degenerate box at {path}: {low} -> {high}")
    corners = np.array(
        [
            (low[0], low[1], low[2]), (high[0], low[1], low[2]),
            (high[0], high[1], low[2]), (low[0], high[1], low[2]),
            (low[0], low[1], high[2]), (high[0], low[1], high[2]),
            (high[0], high[1], high[2]), (low[0], high[1], high[2]),
        ],
        dtype=np.float64,
    )
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([tuple(point) for point in corners])
    mesh.CreateFaceVertexCountsAttr([4] * len(_BOX_FACES))
    mesh.CreateFaceVertexIndicesAttr([index for face in _BOX_FACES for index in face])
    mesh.CreateExtentAttr([tuple(low), tuple(high)])
    prim = mesh.GetPrim()
    UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr().Set(True)
    UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr().Set(UsdPhysics.Tokens.none)


def _slab_with_opening(
    stage: Usd.Stage,
    scope: str,
    z_low: float,
    z_high: float,
    outer_min: tuple[float, float],
    outer_max: tuple[float, float],
    opening_min: tuple[float, float] | None,
    opening_max: tuple[float, float] | None,
) -> None:
    """A slab as up to four boxes around a rectangular hole."""

    if opening_min is None or opening_max is None:
        _box(stage, f"{scope}/slab_full", (*outer_min, z_low), (*outer_max, z_high))
        return
    x0, y0 = outer_min
    x1, y1 = outer_max
    ax, ay = opening_min
    bx, by = opening_max
    pieces = {
        "south": ((x0, y0), (x1, ay)),
        "north": ((x0, by), (x1, y1)),
        "west": ((x0, ay), (ax, by)),
        "east": ((bx, ay), (x1, by)),
    }
    for name, (low, high) in pieces.items():
        if high[0] - low[0] <= 1e-6 or high[1] - low[1] <= 1e-6:
            continue
        _box(stage, f"{scope}/slab_{name}", (*low, z_low), (*high, z_high))


def build_synthetic_building(
    destination: Path,
    *,
    storeys: int = 2,
    storey_height_m: float = 3.0,
    interior_m: tuple[float, float] = (8.0, 10.0),
    wall_thickness_m: float = 0.20,
    slab_thickness_m: float = 0.20,
    opening_m: tuple[float, float] = (1.6, 5.2),
    opening_origin_m: tuple[float, float] | None = None,
    defect: Defect | None = None,
    storey_prefix: str = "Floor",
) -> dict[str, Any]:
    """Write a multi-storey building and return its plan.

    ``storey_prefix`` exists to prove the point: storey detection is geometric,
    so a test can rename the roots to ``Level`` or ``Deck`` and nothing changes.
    """

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    width, depth = interior_m
    t = wall_thickness_m
    outer_min = (-t, -t)
    outer_max = (width + t, depth + t)
    top_z = storeys * storey_height_m

    if opening_origin_m is None:
        opening_origin_m = (width - opening_m[0] - 0.4, 0.4)
    opening_min = opening_origin_m
    opening_max = (opening_min[0] + opening_m[0], opening_min[1] + opening_m[1])

    stage = Usd.Stage.CreateNew(str(destination))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    shell = "/World/Shell"
    UsdGeom.Scope.Define(stage, shell)
    _box(stage, f"{shell}/wall_south",
         (outer_min[0], outer_min[1], 0.0), (outer_max[0], 0.0, top_z))
    _box(stage, f"{shell}/wall_north",
         (outer_min[0], depth, 0.0), (outer_max[0], outer_max[1], top_z))
    _box(stage, f"{shell}/wall_west", (outer_min[0], 0.0, 0.0), (0.0, depth, top_z))
    _box(stage, f"{shell}/wall_east", (width, 0.0, 0.0), (outer_max[0], depth, top_z))

    for level in range(1, storeys):
        scope = f"/World/{storey_prefix}{level + 1}"
        UsdGeom.Scope.Define(stage, scope)
        z_top = level * storey_height_m
        sealed = defect == "sealed_opening"
        _slab_with_opening(
            stage,
            scope,
            z_top - slab_thickness_m,
            z_top,
            outer_min,
            outer_max,
            None if sealed else opening_min,
            None if sealed else opening_max,
        )

    # Roof, so the top storey has a bounded volume like a real building.  The
    # low-headroom defect simply drops it to 1.20 m over the top floor: a point
    # still stands there, a 1.60 m body does not, and no new walkable surface
    # is created in the process.
    roof_z = (
        (storeys - 1) * storey_height_m + 1.20 if defect == "low_headroom" else top_z
    )
    UsdGeom.Scope.Define(stage, "/World/Roof")
    _box(stage, "/World/Roof/slab", (*outer_min, roof_z), (*outer_max, roof_z + slab_thickness_m))
    if defect == "narrow_slot":
        # Split the upper storey with a wall pierced by a 0.30 m slot: wide
        # enough for a point, far too narrow for a 0.50 m body diameter.
        z0 = (storeys - 1) * storey_height_m
        # Placed clear of the stairwell opening: the point of this defect is a
        # storey the stair reaches but cannot cross, not a blocked shaft.
        mid = depth * 0.78
        slot_low = width * 0.5 - 0.15
        slot_high = width * 0.5 + 0.15
        UsdGeom.Scope.Define(stage, "/World/Defect")
        _box(stage, "/World/Defect/partition_west",
             (0.0, mid, z0), (slot_low, mid + 0.1, z0 + storey_height_m))
        _box(stage, "/World/Defect/partition_east",
             (slot_high, mid, z0), (width, mid + 0.1, z0 + storey_height_m))

    stage.GetRootLayer().Save()
    plan = SyntheticPlan(
        storeys=storeys,
        storey_height_m=storey_height_m,
        interior_m=interior_m,
        wall_thickness_m=wall_thickness_m,
        slab_thickness_m=slab_thickness_m,
        opening_min_m=opening_min,
        opening_max_m=opening_max,
    )
    return {
        "stage": str(destination),
        "plan": plan.as_dict(),
        "defect": defect,
        "storey_prefix": storey_prefix,
        "expected_storey_floor_z_m": [level * storey_height_m for level in range(storeys)],
    }
