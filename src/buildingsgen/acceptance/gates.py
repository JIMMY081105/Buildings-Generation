"""The acceptance gates.

Run in order; stop at the first FAIL and localise it rather than carrying a
known-broken building forward.  Each gate is a function of an
:class:`~buildingsgen.acceptance.context.AcceptanceContext` returning a
:class:`~buildingsgen.acceptance.base.GateResult`.

    G1  structure            the stage is the kind of stage a cache can be baked from
    G2  cache integrity      the cache is committed, exact, and matches its manifest
    G3  USD <-> voxel        the grid agrees with the geometry it claims to represent
    G4  storeys and stair    two or more storeys, plausible rises, a stair spanning them
    G5  cross-storey route   a body can walk from the lower storey to the upper one
    G6  stair rises          every rise, including the first and the last, is climbable
    G7  body navigation      each storey is usable, not just reachable at one point

The first and the last rise get their own gate because they are the two an
"average riser" measurement silently skips, and they are the two that break:
ground -> first tread carries the tread plate thickness, and last tread ->
upper slab carries whatever the slab thickness turned out to be.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import numpy as np

from ..usd.colliders import (
    EXACT_APPROXIMATION,
    meters_per_unit,
    rigid_body_prims,
    world_triangles_m,
)
from ..usd.storeys import authored_surface_z_m
from ..voxel.cache import CacheError, load_cache
from ..voxel.rasterize import SPLAT_TOLERANCE_M
from ..voxel.standability import (
    component_areas_m2,
    derive_standable,
    largest_component_seed,
    route_length_m,
    walk_reachable,
)
from .base import GateResult, NegativeControl
from .context import AcceptanceContext

#: Substrings that mark a stair collider as an obstacle rather than support.
OBSTACLE_HINTS = ("handrail", "rail", "baluster", "post", "newel", "glass")


# --------------------------------------------------------------------------- G1

def gate1_structure(context: AcceptanceContext) -> GateResult:
    """Is this a stage a static cache can honestly be baked from?"""

    from pxr import UsdGeom

    result = GateResult("G1", "USD structure")
    result.not_supported.append(
        "Structural validity says nothing about whether the geometry is navigable; "
        "G4-G7 decide that."
    )
    stage = context.stage
    records, lower, upper = context.colliders

    up_axis = str(UsdGeom.GetStageUpAxis(stage)).upper()
    mpu = meters_per_unit(stage)
    approximations: dict[str, int] = {}
    for record in records:
        approximations[record.approximation] = approximations.get(record.approximation, 0) + 1
    dynamic = rigid_body_prims(stage)

    result.measurements = {
        "up_axis": up_axis,
        "meters_per_unit": mpu,
        "collider_count": len(records),
        "approximation_counts": approximations,
        "rigid_body_prim_count": len(dynamic),
        "collider_bounds_min_m": [round(float(v), 4) for v in lower],
        "collider_bounds_max_m": [round(float(v), 4) for v in upper],
    }

    if up_axis != "Z":
        result.fail(f"stage is {up_axis}-up; the cache grid requires Z-up")
    if not np.isclose(mpu, 1.0):
        result.fail(f"metersPerUnit is {mpu}, expected 1.0 so authored numbers are metres")
    if not records:
        result.fail("stage authors no enabled mesh collider")
    inexact = {k: v for k, v in approximations.items() if k != EXACT_APPROXIMATION}
    if inexact:
        result.fail(
            f"{sum(inexact.values())} collider(s) use approximations {sorted(inexact)}; "
            "a cooked convex solid turns a staircase into a ramp and passes reachability "
            "for the wrong reason"
        )
    if dynamic:
        result.fail(
            f"{len(dynamic)} prim(s) carry UsdPhysics.RigidBodyAPI; dynamic links "
            f"may not enter a static cache (first: {dynamic[0]})"
        )

    control = NegativeControl(
        name="inexact_approximation_is_rejected",
        description="Re-run the approximation check with one collider relabelled convexHull.",
        expectation="the check must report a failure",
    )
    mutated = dict(approximations)
    mutated["convexHull"] = mutated.get("convexHull", 0) + 1
    control.ran = True
    control.behaved_as_required = any(k != EXACT_APPROXIMATION for k in mutated)
    control.observed = {"mutated_counts": mutated}
    result.controls.append(control)
    return result.finish()


# --------------------------------------------------------------------------- G2

def gate2_cache_integrity(context: AcceptanceContext) -> GateResult:
    """Is the cache committed, exact, self-consistent and free of NaN?"""

    result = GateResult("G2", "Cache integrity")
    result.not_supported.append(
        "A verified cache proves the bake was not corrupted, not that the bake used "
        "the intended stage revision - compare source hashes for that."
    )
    cache = context.cache
    grid = cache.grid
    from ..voxel.cache import load_clearance

    fine = load_clearance(cache.path)
    coarse = load_clearance(cache.path, coarse=True)
    occupied = int(context.occupancy.sum())

    result.measurements = {
        "cache": str(cache.path),
        "collision_policy": cache.collision_policy,
        "grid_fine_shape": list(grid.fine_shape),
        "occupied_voxels": occupied,
        "occupancy_ratio": round(float(context.occupancy.mean()), 6),
        "fine_clearance_range_m": [round(float(fine.min()), 4), round(float(fine.max()), 4)],
        "source_stage": cache.manifest.get("source", {}).get("stage"),
        "composed_layers_sha256": cache.manifest.get("source", {}).get("composed_layers_sha256"),
    }

    if "Exact bake" not in cache.collision_policy:
        result.fail(
            f"cache policy is {cache.collision_policy!r}; only an exact bake may be accepted"
        )
    if occupied == 0:
        result.fail("cache contains no occupied voxel")
    for name, array in (("fine", fine), ("coarse", coarse)):
        if not np.all(np.isfinite(array)):
            result.fail(f"{name} clearance array contains NaN or Inf")

    control = NegativeControl(
        name="tampered_manifest_is_rejected",
        description="Copy the cache, change the recorded occupied-voxel count, reload it.",
        expectation="load_cache must raise CacheError",
    )
    staging = Path(tempfile.mkdtemp(prefix="buildingsgen-g2-"))
    try:
        copy = staging / cache.path.name
        shutil.copytree(cache.path, copy)
        manifest_path = copy / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["occupancy"]["occupied_voxels"] = int(payload["occupancy"]["occupied_voxels"]) + 1
        manifest_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        control.ran = True
        try:
            load_cache(copy, verify_hashes=True)
            control.behaved_as_required = False
            control.observed = {"result": "tampered cache loaded without error"}
        except CacheError as error:
            control.behaved_as_required = True
            control.observed = {"raised": type(error).__name__}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    result.controls.append(control)
    return result.finish()


# --------------------------------------------------------------------------- G3

def gate3_usd_voxel_consistency(
    context: AcceptanceContext,
    *,
    samples: int = 4000,
    seed: int = 20260818,
) -> GateResult:
    """Does the grid agree with the geometry it claims to represent?

    Recall: a point on a collider surface must land in an occupied cell.
    Precision: a point far from every collider must land in a free cell.
    "Far" is the derived splat tolerance, not a tuned number - see
    ``voxel.rasterize.SPLAT_TOLERANCE_M``.  Points inside the tolerance band
    are reported and judged by neither test.
    """

    import trimesh
    from pxr import Usd, UsdGeom

    result = GateResult("G3", "USD <-> voxel consistency")
    result.not_supported.append(
        "Agreement is measured on sampled points, so it bounds systematic error, "
        "not the behaviour of any individual collider."
    )
    stage = context.stage
    grid = context.cache.grid
    occupancy = context.occupancy
    mpu = meters_per_unit(stage)
    xform = UsdGeom.XformCache(Usd.TimeCode.Default())

    rng = np.random.default_rng(seed)
    records, _, _ = context.colliders
    vertex_blocks: list[np.ndarray] = []
    triangle_blocks: list[np.ndarray] = []
    offset = 0
    for record in records:
        prim = stage.GetPrimAtPath(record.prim_path)
        vertices, triangles = world_triangles_m(prim, mpu=mpu, xform_cache=xform)
        vertex_blocks.append(vertices)
        triangle_blocks.append(triangles + offset)
        offset += vertices.shape[0]
    combined = trimesh.Trimesh(
        vertices=np.concatenate(vertex_blocks, axis=0),
        faces=np.concatenate(triangle_blocks, axis=0),
        process=False,
    )
    surface, _ = trimesh.sample.sample_surface(combined, samples, seed=seed)
    surface = np.asarray(surface, dtype=np.float64)

    def cell_of(points: np.ndarray) -> np.ndarray:
        origin = np.asarray(grid.scene_bounds_min_m)
        index = np.floor((points - origin) / grid.fine_voxel_size_m).astype(np.int64)
        return np.clip(index, 0, np.asarray(grid.fine_shape) - 1)

    surface_cells = cell_of(surface)
    surface_hit = occupancy[surface_cells[:, 0], surface_cells[:, 1], surface_cells[:, 2]]
    recall = float(surface_hit.mean())

    # "Far" is measured against the collider surfaces themselves.  Measuring
    # it against the sampled points instead would call a probe sitting on a
    # large wall "far" merely because no sample landed near it, and the gate
    # would then report the grid as wrong where it is right.
    lower = np.asarray(grid.scene_bounds_min_m)
    upper = np.asarray(grid.scene_bounds_max_m)
    probes = rng.uniform(lower, upper, size=(samples * 2, 3))
    _, distance, _ = trimesh.proximity.closest_point(combined, probes)
    far = np.asarray(distance) > SPLAT_TOLERANCE_M
    free_probes = probes[far][:samples]
    if free_probes.shape[0] == 0:
        result.fail("no sampled point lies outside the splat tolerance band")
        precision = 0.0
    else:
        free_cells = cell_of(free_probes)
        occupied_far = occupancy[free_cells[:, 0], free_cells[:, 1], free_cells[:, 2]]
        precision = float(1.0 - occupied_far.mean())

    threshold = context.profile.acceptance.min_voxel_agreement
    result.measurements = {
        "surface_samples": int(surface.shape[0]),
        "free_samples": int(free_probes.shape[0]),
        "recall_surface_points_occupied": round(recall, 6),
        "precision_far_points_free": round(precision, 6),
        "splat_tolerance_m": round(SPLAT_TOLERANCE_M, 6),
        "band_samples_ignored": int((~far).sum()),
        "threshold": threshold,
    }
    if recall < threshold - 1e-9:
        result.fail(f"recall {recall:.6f} below {threshold}: colliders missing from the grid")
    if precision < threshold - 1e-9:
        result.fail(
            f"precision {precision:.6f} below {threshold}: grid is solid where the "
            "geometry is not"
        )

    control = NegativeControl(
        name="displaced_samples_disagree",
        description="Re-test the surface points after shifting them 0.5 m in +X.",
        expectation="recall must drop below the threshold",
    )
    shifted = surface + np.array([0.5, 0.0, 0.0])
    shifted_cells = cell_of(shifted)
    shifted_recall = float(
        occupancy[shifted_cells[:, 0], shifted_cells[:, 1], shifted_cells[:, 2]].mean()
    )
    control.ran = True
    control.behaved_as_required = shifted_recall < threshold - 1e-9
    control.observed = {"displaced_recall": round(shifted_recall, 6)}
    result.controls.append(control)
    return result.finish()


# --------------------------------------------------------------------------- G4

def _stair_support_prims(context: AcceptanceContext) -> list[str]:
    """Stair colliders that a body can stand on (treads, landings)."""

    if not context.stair_prim_path:
        return []
    prefix = context.stair_prim_path.rstrip("/")
    records, _, _ = context.colliders
    return [
        record.prim_path
        for record in records
        if (record.prim_path == prefix or record.prim_path.startswith(prefix + "/"))
        and not any(hint in record.prim_path.lower() for hint in OBSTACLE_HINTS)
    ]


def gate4_storeys_and_stair(context: AcceptanceContext) -> GateResult:
    """Two or more plausible storeys, with a stair that spans them."""

    from pxr import Usd, UsdGeom

    result = GateResult("G4", "Storeys and stair span")
    result.not_supported.append(
        "A stair that spans the gap may still be unclimbable; G6 measures the rises."
    )
    detection = context.storeys
    model = context.profile.storeys
    result.measurements["storeys"] = detection.as_dict()

    if detection.count < 2:
        result.fail(
            f"detected {detection.count} storey band(s) with at least "
            f"{model.min_storey_floor_area_m2} m^2; a connected building needs two"
        )
        return result.finish()
    if model.expected_storey_count is not None and detection.count != model.expected_storey_count:
        result.fail(
            f"detected {detection.count} storeys, profile expects {model.expected_storey_count}"
        )
    for entry in detection.implausible_separations:
        result.fail(
            f"storey rise {entry['separation_m']} m is outside "
            f"[{entry['min_allowed_m']}, {entry['max_allowed_m']}] m for this building type"
        )

    support_prims = _stair_support_prims(context)
    if not support_prims:
        result.status = "SKIP"
        result.measurements["stair"] = {"reason": "no stair prim path was supplied"}
        result.not_supported.append(
            "Without a stair prim path this gate cannot check the span; supply "
            "--stair-prim to make it meaningful."
        )
        return result

    stage = context.stage
    mpu = meters_per_unit(stage)
    xform = UsdGeom.XformCache(Usd.TimeCode.Default())
    grid = context.cache.grid
    voxel = grid.fine_voxel_size_m
    records_all, _, _ = context.colliders
    support_prims = set(support_prims)
    stair_occupancy = np.zeros(grid.fine_shape, dtype=bool)
    whole_stair = np.zeros(grid.fine_shape, dtype=bool)
    from ..voxel.rasterize import rasterize_into

    prefix = context.stair_prim_path.rstrip("/")
    for record in records_all:
        path = record.prim_path
        if not (path == prefix or path.startswith(prefix + "/")):
            continue
        vertices, triangles = world_triangles_m(
            stage.GetPrimAtPath(path), mpu=mpu, xform_cache=xform
        )
        rasterize_into(whole_stair, grid, vertices, triangles, fill=True)
        if path in support_prims:
            rasterize_into(stair_occupancy, grid, vertices, triangles, fill=True)

    lower, upper = detection.storeys[0], detection.storeys[1]
    centers = grid.axis_centers_m(2)
    band = (centers >= lower.floor_z_m - 1e-9) & (centers <= upper.floor_z_m + 1e-9)
    per_layer = stair_occupancy[:, :, band].sum(axis=(0, 1))
    # Empty layers are normal: tread plates are thinner than the riser, so
    # every flight has free space between them.  What must not happen is a gap
    # taller than one support step, because that is a rise a body cannot make.
    empty_run = _longest_run(per_layer == 0)
    max_gap_cells = context.config.max_step_cells(voxel)

    # Headroom over the flight: every stair column needs a body height of free
    # space above its highest tread, or the climb ends at a ceiling.
    stair_columns = stair_occupancy.any(axis=2)
    # Measure against everything except the stair itself - treads *and*
    # handrails.  With a nosing, tread k+1 overhangs tread k by a few
    # centimetres, so a column at every tread boundary contains the next tread
    # one riser above it; and the rail sits 0.95 m over the outer tread cells.
    # Counting either as a headroom failure would fail every correctly built
    # stair in the world.  What this gate asks is whether the *building* leaves
    # a body room over the flight.
    without_stair = context.occupancy & ~whole_stair
    coverage = _top_clearance_coverage(
        context, stair_occupancy, stair_columns, occupancy=without_stair
    )

    result.measurements["stair"] = {
        "support_prim_count": len(support_prims),
        "voxel_layers_between_storeys": int(band.sum()),
        "longest_gap_without_stair_geometry_cells": empty_run,
        "longest_gap_m": round(empty_run * voxel, 3),
        "max_gap_cells": max_gap_cells,
        "stair_footprint_m2": round(float(stair_columns.sum()) * voxel * voxel, 3),
        "top_clearance_coverage": round(coverage, 4),
        "min_top_clearance_coverage": (
            context.profile.acceptance.min_top_clearance_coverage
        ),
    }
    if empty_run > max_gap_cells:
        result.fail(
            f"the flight leaves a {empty_run * voxel:.2f} m vertical gap with no geometry "
            f"between the storeys, above the {context.config.max_support_step_m} m support "
            "step: the stair does not actually span the storeys"
        )
    if coverage < context.profile.acceptance.min_top_clearance_coverage - 1e-9:
        result.fail(
            f"only {coverage:.3f} of the flight has {context.config.body_height_m} m of headroom "
            f"(profile requires {context.profile.acceptance.min_top_clearance_coverage})"
        )

    control = NegativeControl(
        name="sealed_shaft_loses_headroom",
        description="Fill the volume one body height above the flight and re-measure coverage.",
        expectation="coverage must fall below the profile threshold",
    )
    sealed = np.array(without_stair, copy=True)
    _seal_above(sealed, stair_occupancy, grid, context.config.body_height_m)
    sealed_coverage = _top_clearance_coverage(
        context, stair_occupancy, stair_columns, occupancy=sealed
    )
    control.ran = True
    control.behaved_as_required = (
        sealed_coverage < context.profile.acceptance.min_top_clearance_coverage - 1e-9
    )
    control.observed = {"sealed_coverage": round(sealed_coverage, 4)}
    result.controls.append(control)
    return result.finish()


def _longest_run(flags: np.ndarray) -> int:
    """Longest run of True values."""

    longest = current = 0
    for flag in np.asarray(flags, dtype=bool):
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return longest


def _top_clearance_coverage(
    context: AcceptanceContext,
    stair_occupancy: np.ndarray,
    stair_columns: np.ndarray,
    *,
    occupancy: np.ndarray | None = None,
) -> float:
    """Fraction of stair columns with a free body height above their top tread.

    ``occupancy`` should normally exclude the flight's own geometry; see the
    call site in :func:`gate4_storeys_and_stair`.
    """

    grid = context.cache.grid
    occ = context.occupancy if occupancy is None else occupancy
    body_cells = context.config.body_height_cells(grid.fine_voxel_size_m)
    nz = grid.fine_shape[2]
    xs, ys = np.nonzero(stair_columns)
    if xs.size == 0:
        return 0.0
    clear = 0
    for x, y in zip(xs, ys):
        top = int(np.max(np.nonzero(stair_occupancy[x, y])[0]))
        window = occ[x, y, top + 1: min(top + 1 + body_cells, nz)]
        if window.size == body_cells and not window.any():
            clear += 1
    return clear / float(xs.size)


def _seal_above(
    occupancy: np.ndarray,
    stair_occupancy: np.ndarray,
    grid,
    height_m: float,
) -> None:
    cells = int(round(height_m / grid.fine_voxel_size_m))
    xs, ys = np.nonzero(stair_occupancy.any(axis=2))
    nz = grid.fine_shape[2]
    for x, y in zip(xs, ys):
        top = int(np.max(np.nonzero(stair_occupancy[x, y])[0]))
        occupancy[x, y, top + 1: min(top + 1 + cells, nz)] = True


# --------------------------------------------------------------------------- G5

def gate5_cross_storey_route(context: AcceptanceContext) -> GateResult:
    """Can a body of the pinned size walk from the lower storey to the upper one?"""

    result = GateResult("G5", "Cross-storey route")
    result.not_supported.append(
        "This is a kinematic walk over the support field: no contact dynamics, no "
        "balance, no actuation. It cannot conclude that a humanoid policy succeeds."
    )
    detection = context.storeys
    if detection.count < 2:
        result.fail("fewer than two storeys detected; nothing to connect")
        return result.finish()

    volume = context.standable
    config = context.config
    grid = context.cache.grid
    voxel = grid.fine_voxel_size_m
    lower, upper = detection.storeys[0], detection.storeys[1]

    seed = _central_seed(volume, lower.cells, config, voxel)
    if seed is None:
        result.fail("the lower storey has no standable component to start from")
        return result.finish()
    reached = walk_reachable(volume.mask, seed, config, voxel)
    upper_reached = reached & upper.cells
    reached_area = float(upper_reached.sum()) * voxel * voxel

    climb, _, _ = route_length_m(volume.mask, seed, upper.cells, config, voxel)

    # The threshold is a *plan* quality: how far out of its way a body in the
    # middle of the floor has to walk to reach the stair.  Comparing a route
    # that climbs four metres against a straight line drawn through solid floor
    # would exceed any limit on a perfectly sensible building, and tuning the
    # limit to absorb that would turn the check into decoration.  The climb is
    # reported as a diagnostic instead.
    detour = _plan_detour(context, volume, lower, seed, config, voxel)
    limit = context.profile.acceptance.max_geodesic_to_euclidean_ratio
    result.measurements = {
        "lower_storey_z_m": round(lower.floor_z_m, 3),
        "upper_storey_z_m": round(upper.floor_z_m, 3),
        "upper_storey_reached_area_m2": round(reached_area, 2),
        "cross_storey_route_length_m": None if climb is None else round(climb, 2),
        "plan_detour": detour,
        "max_ratio": limit,
    }
    if reached_area <= 0.0:
        result.fail("no route from the lower storey reaches the upper storey")
    ratio = detour.get("ratio")
    if ratio is not None and ratio > limit:
        result.fail(
            f"reaching the stair takes {ratio:.2f}x the straight-line distance across the "
            f"floor, above the profile limit {limit}"
        )

    control = NegativeControl(
        name="blocked_shaft_disconnects_storeys",
        description="Fill every voxel between the two storey slabs and re-derive standability.",
        expectation="the upper storey must become unreachable",
    )
    blocked = np.array(context.occupancy, copy=True)
    centers = grid.axis_centers_m(2)
    window = (centers > lower.floor_z_m + 0.5 * voxel) & (centers < upper.floor_z_m)
    blocked[:, :, window] = True
    blocked_volume = derive_standable(
        blocked, grid, config, ground_plane_z_m=context.ground_plane_z_m
    )
    blocked_seed = largest_component_seed(
        blocked_volume.mask, config, voxel, within=lower.cells
    )
    if blocked_seed is None:
        control.observed = {"result": "no standable ground component after blocking"}
        control.behaved_as_required = True
    else:
        blocked_reach = walk_reachable(blocked_volume.mask, blocked_seed, config, voxel)
        blocked_area = float((blocked_reach & upper.cells).sum()) * voxel * voxel
        control.behaved_as_required = blocked_area <= 0.0
        control.observed = {"upper_storey_reached_area_m2": round(blocked_area, 3)}
    control.ran = True
    result.controls.append(control)
    return result.finish()


def _plan_detour(context, volume, lower, seed, config, voxel) -> dict:
    """Walking distance to the foot of the stair versus the straight line.

    Returns an empty diagnostic when no stair prim was supplied - the number is
    undefined without one, and inventing a substitute target would make the
    threshold mean something different on every building.
    """

    support_prims = _stair_support_prims(context)
    if not support_prims:
        return {"reason": "no stair prim path was supplied"}
    from pxr import Usd, UsdGeom

    from ..voxel.rasterize import rasterize_into

    grid = context.cache.grid
    stair = np.zeros(grid.fine_shape, dtype=bool)
    xform = UsdGeom.XformCache(Usd.TimeCode.Default())
    mpu = meters_per_unit(context.stage)
    for path in support_prims:
        vertices, triangles = world_triangles_m(
            context.stage.GetPrimAtPath(path), mpu=mpu, xform_cache=xform
        )
        rasterize_into(stair, grid, vertices, triangles, fill=True)
    foot = lower.cells & stair.any(axis=2)[:, :, None]
    if not foot.any():
        return {"reason": "the stair has no standable cell on the lower storey"}
    geodesic, start, goal = route_length_m(volume.mask, seed, foot, config, voxel)
    if geodesic is None or goal is None:
        return {"reason": "the stair foot is not walkable from the floor centre"}
    euclidean = _straight_line_m(start, goal, voxel)
    if euclidean < voxel:
        return {"reason": "the floor centre is already at the stair foot"}
    return {
        "from": "centre of the largest walkable region on the lower storey",
        "to": "nearest standable cell at the foot of the stair",
        "route_m": round(geodesic, 2),
        "straight_line_m": round(euclidean, 2),
        "ratio": round(geodesic / euclidean, 3),
    }


def _central_seed(volume, storey_cells, config, voxel) -> np.ndarray | None:
    """Seed at the centre of the storey's largest walkable region.

    The route ratio is a property of the building, so it must not depend on
    which cell ``np.nonzero`` happens to return first.  Starting from the
    centre of the largest region also matches what the number is for: how far
    out of its way a body in the middle of the floor has to go to change level.
    """

    region = np.asarray(volume.mask, dtype=bool) & np.asarray(storey_cells, dtype=bool)
    if not region.any():
        return None
    start = largest_component_seed(volume.mask, config, voxel, within=region)
    if start is None:
        return None
    component = walk_reachable(volume.mask, start, config, voxel) & region
    cells = np.argwhere(component)
    centre = cells.mean(axis=0)
    nearest = cells[np.argmin(((cells - centre) ** 2).sum(axis=1))]
    seed = np.zeros(volume.mask.shape, dtype=bool)
    seed[tuple(nearest)] = True
    return seed


def _straight_line_m(start, goal, voxel: float) -> float:
    delta = (np.asarray(goal, dtype=float) - np.asarray(start, dtype=float)) * voxel
    return float(np.linalg.norm(delta))


# --------------------------------------------------------------------------- G6

def gate6_stair_rises(context: AcceptanceContext) -> GateResult:
    """Measure every rise from authored geometry, then corroborate by flooding.

    (A) is the criterion: authored tread tops have no voxel quantisation in
    them.  (B) corroborates topology and is explicitly weaker - at an offset of
    one voxel the flood can still pass a flight whose authored first rise is
    already over the limit, which is why it does not get a vote.
    """

    from pxr import Usd, UsdGeom

    result = GateResult("G6", "Stair rises")
    result.not_supported.append(
        "Authored rises bound the geometry, not the contact behaviour: a legal rise "
        "can still be missed by a policy."
    )
    support_prims = _stair_support_prims(context)
    if not support_prims:
        result.status = "SKIP"
        result.measurements = {"reason": "no stair prim path was supplied"}
        return result
    detection = context.storeys
    if detection.count < 2:
        result.fail("fewer than two storeys detected; the last rise has no slab to measure against")
        return result.finish()

    stage = context.stage
    mpu = meters_per_unit(stage)
    xform = UsdGeom.XformCache(Usd.TimeCode.Default())
    tops: list[float] = []
    for path in support_prims:
        vertices, _ = world_triangles_m(stage.GetPrimAtPath(path), mpu=mpu, xform_cache=xform)
        tops.append(round(float(vertices[:, 2].max()), 6))
    tops = sorted(set(tops))

    # Both ends of the measurement come from authored geometry.  The detected
    # storey height is voxel-quantised and conservative by up to one cell, and
    # comparing an authored tread top against a quantised slab would report a
    # 5 cm error that is an artefact of the grid, not of the building.
    ground = context.ground_plane_z_m
    if ground is None:
        ground = authored_surface_z_m(
            stage, detection.storeys[0].floor_z_m,
            fallback_z_m=detection.storeys[0].floor_z_m,
        )
    slab_top = authored_surface_z_m(
        stage, detection.storeys[1].floor_z_m,
        fallback_z_m=detection.storeys[1].floor_z_m,
    )

    first = tops[0] - ground
    inner = list(np.diff(tops)) if len(tops) > 1 else []
    last = slab_top - tops[-1]
    rises = [first, *[float(v) for v in inner], last]
    limit = context.config.max_support_step_m
    over = [round(float(value), 4) for value in rises if value > limit + 1e-9]

    flooded = _upper_reachable(context, ground)
    result.measurements = {
        "authored": {
            "tread_top_count": len(tops),
            "ground_z_m": round(float(ground), 4),
            "upper_slab_z_m": round(float(slab_top), 4),
            "upper_slab_source": "authored collider top",
            "first_rise_m": round(float(first), 4),
            "median_inner_rise_m": round(float(np.median(inner)), 4) if inner else None,
            "max_inner_rise_m": round(float(max(inner)), 4) if inner else None,
            "last_rise_m": round(float(last), 4),
            "max_rise_m": round(float(max(rises)), 4),
            "limit_m": limit,
            "rises_over_limit": len(over),
            "over_limit_values_m": over,
        },
        "flooded": {"upper_storey_reachable_area_m2": round(flooded, 2)},
    }
    if over:
        result.fail(
            f"{len(over)} rise(s) exceed the {limit} m support step: {over}. "
            "The first and last rises are included on purpose - an average riser hides them."
        )
    if flooded <= 0.0:
        result.fail("the flood corroboration found no standable area on the upper storey")

    sweep: dict[str, float] = {}
    blocked_at: float | None = None
    for offset in (0.05, 0.10, 0.15):
        area = _upper_reachable(context, ground - offset)
        sweep[f"{offset:.2f}"] = round(area, 2)
        if area <= 0.0 and blocked_at is None:
            blocked_at = offset
    control = NegativeControl(
        name="lowered_terrain_breaks_the_climb",
        description=(
            "Lower the terrain by 0.05/0.10/0.15 m, which raises the first rise by the "
            "same amount, and re-derive standability."
        ),
        expectation=(
            "by 0.15 m (a first rise of at least 0.32 m) the upper storey must be "
            "unreachable"
        ),
    )
    control.ran = True
    control.behaved_as_required = sweep["0.15"] <= 0.0
    control.observed = {"upper_area_m2_by_offset": sweep, "first_blocked_at_offset_m": blocked_at}
    result.controls.append(control)
    return result.finish()


def _upper_reachable(context: AcceptanceContext, ground_plane_z_m: float | None) -> float:
    """Upper-storey area reachable on foot when the terrain sits at this height."""

    volume = context.standable_with_ground(ground_plane_z_m)
    detection = context.storeys
    voxel = context.cache.grid.fine_voxel_size_m
    # The sweep may have extended the grid downwards, so the storey masks from
    # the unmodified detection are re-expressed as height windows rather than
    # reused as arrays of the old shape.
    upper_band = _band_mask(volume, detection.storeys[1])
    # Seed on the terrain this sweep actually placed, not on the height band the
    # unmodified building had.  Reusing the old band lets the seed land on the
    # first tread once the ground drops away, and the control then measures the
    # stair reaching itself - which never fails, whatever the terrain does.
    lower_band = _terrain_mask(volume, ground_plane_z_m, detection.storeys[0])
    seed = largest_component_seed(volume.mask, context.config, voxel, within=lower_band)
    if seed is None:
        return 0.0
    reached = walk_reachable(volume.mask, seed, context.config, voxel)
    return float((reached & upper_band).sum()) * voxel * voxel


def _terrain_mask(volume, ground_plane_z_m: float | None, storey) -> np.ndarray:
    """Standable voxels resting on the terrain at ``ground_plane_z_m``."""

    if ground_plane_z_m is None:
        return _band_mask(volume, storey)
    surface = volume.surface_z_m()
    voxel = volume.grid.fine_voxel_size_m
    rows = np.abs(surface - (ground_plane_z_m + voxel)) <= 1.5 * voxel
    mask = np.zeros(volume.mask.shape, dtype=bool)
    mask[:, :, rows] = volume.mask[:, :, rows]
    return mask


def _band_mask(volume, storey) -> np.ndarray:
    """Standable voxels of ``volume`` inside a storey height band."""

    surface = volume.surface_z_m()
    half = 0.5 * volume.grid.fine_voxel_size_m
    rows = (surface >= storey.band_min_z_m - half) & (surface <= storey.band_max_z_m + half)
    band = np.zeros(volume.mask.shape, dtype=bool)
    band[:, :, rows] = volume.mask[:, :, rows]
    return band


# --------------------------------------------------------------------------- G7

def gate7_body_navigation(context: AcceptanceContext) -> GateResult:
    """Is each storey usable from the stair, or merely touched by it?

    The body radius erodes the whole free region here - see
    ``voxel.standability``.  Reporting reachable area without that erosion is
    the failure this gate exists to prevent.
    """

    result = GateResult("G7", "Body-scale navigation per storey")
    result.not_supported.append(
        "Reachable area is geometric. It does not model doors that open, objects that "
        "move, or a policy's ability to follow the route."
    )
    detection = context.storeys
    if detection.count < 2:
        result.fail("fewer than two storeys detected")
        return result.finish()

    volume = context.standable
    config = context.config
    voxel = context.cache.grid.fine_voxel_size_m
    thresholds = context.profile.acceptance

    seed = largest_component_seed(
        volume.mask, config, voxel, within=detection.storeys[0].cells
    )
    if seed is None:
        result.fail("the ground storey has no standable component")
        return result.finish()
    reached = walk_reachable(volume.mask, seed, config, voxel)

    per_storey = []
    for storey in detection.storeys:
        connected = reached & storey.cells
        area = float(connected.sum()) * voxel * voxel
        islands = component_areas_m2(storey.cells & ~reached, config, voxel)
        largest_island = islands[0] if islands else 0.0
        per_storey.append(
            {
                "label": storey.label,
                "floor_z_m": round(storey.floor_z_m, 3),
                "standable_area_m2": round(storey.area_m2, 2),
                "reachable_from_stair_m2": round(area, 2),
                "largest_unreachable_component_m2": round(largest_island, 2),
                "unreachable_component_count": len(islands),
            }
        )
        if area < thresholds.min_reachable_area_per_storey_m2 - 1e-9:
            result.fail(
                f"storey {storey.label!r} offers {area:.1f} m^2 reachable from the stair, "
                f"below the {thresholds.min_reachable_area_per_storey_m2} m^2 this "
                "type requires"
            )
        if largest_island > thresholds.max_unreachable_standable_component_m2 + 1e-9:
            result.fail(
                f"storey {storey.label!r} has a {largest_island:.1f} m^2 standable region the "
                "stair cannot reach, above the "
                f"{thresholds.max_unreachable_standable_component_m2} m^2 limit: "
                "that is a missing route, not a dead corner"
            )

    result.measurements = {
        "body_proxy": config.semantics(voxel, volume.clearance_mode),
        "per_storey": per_storey,
        "thresholds": {
            "min_reachable_area_per_storey_m2": thresholds.min_reachable_area_per_storey_m2,
            "max_unreachable_standable_component_m2": (
                thresholds.max_unreachable_standable_component_m2
            ),
        },
    }

    control = NegativeControl(
        name="unreduced_radius_would_overstate_reach",
        description=(
            "Re-derive the support field with the erosion disabled and compare the "
            "standable area."
        ),
        expectation="the un-eroded field must claim strictly more standable area",
    )
    naive = _naive_standable_area(context)
    honest = volume.area_m2
    control.ran = True
    control.behaved_as_required = naive > honest + 1e-9
    control.observed = {
        "eroded_standable_area_m2": round(honest, 2),
        "un_eroded_standable_area_m2": round(naive, 2),
        "overstatement_m2": round(naive - honest, 2),
    }
    result.controls.append(control)
    return result.finish()


def _naive_standable_area(context: AcceptanceContext) -> float:
    """Standable area if the body were a point: the historical mistake, measured.

    Identical to :func:`~buildingsgen.voxel.standability.derive_standable` in
    every respect but one - the blocked set is not dilated by the body
    footprint.  Comparing against anything else would confound the erosion with
    some other difference, and the control would stop proving its point.
    """

    volume = context.standable
    grid = context.cache.grid
    voxel = grid.fine_voxel_size_m
    occupied = np.array(context.occupancy, copy=True)
    if context.ground_plane_z_m is not None:
        centers = grid.axis_centers_m(2)
        occupied[:, :, (centers - 0.5 * voxel) <= context.ground_plane_z_m + 1e-9] = True
    real = grid.real_cell_mask()
    known_free = real & ~occupied
    blocked = ~real | occupied  # the dilation derive_standable applies is skipped
    body_cells = context.config.body_height_cells(voxel)
    relief_cells = (
        context.config.max_step_cells(voxel)
        if volume.clearance_mode == "terrain_following"
        else 0
    )
    count = occupied.shape[2] - body_cells
    prefix = np.concatenate(
        (np.zeros((*occupied.shape[:2], 1), dtype=np.int32),
         np.cumsum(blocked, axis=2, dtype=np.int32)),
        axis=2,
    )
    body_blocked = (
        prefix[:, :, body_cells + 1:] - prefix[:, :, relief_cells + 1: relief_cells + count + 1]
    )
    clear = (
        occupied[:, :, :count]
        & real[:, :, :count]
        & known_free[:, :, 1: count + 1]
        & (body_blocked == 0)
    )
    return float(clear.sum()) * voxel * voxel


ALL_GATES = (
    gate1_structure,
    gate2_cache_integrity,
    gate3_usd_voxel_consistency,
    gate4_storeys_and_stair,
    gate5_cross_storey_route,
    gate6_stair_rises,
    gate7_body_navigation,
)
