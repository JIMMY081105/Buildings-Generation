"""Bake a composed stage into an immutable occupancy cache.

Two decisions here are worth knowing about before reading a number that came
out of this pipeline.

**The grid is sized from the geometry that is actually rasterised**, not from a
bounding-box cache.  A ``UsdGeom.BBoxCache`` that omits ``purpose=guide``
under-bounds a stage whose collision meshes are authored as guides - which is
common - and the grid then silently clips colliders near the edge of the
building.  Reading the vertices once and taking the bounds from them cannot
disagree with what is splatted.  The bbox-derived shape is reported alongside
so the gap stays visible.

**Watertight colliders are filled; open ones are surface-rasterised.**  A
closed box is a solid, and leaving its interior marked free is not a harmless
approximation: under the terrain-following clearance model the cavity inside a
hollow slab reads as a second, standable floor one slab-thickness below the
real one, and storey detection then finds twice the area it should.  An open
mesh has no defined interior, so it stays a surface.  The per-collider split is
reported, because "how many colliders were treated as solid" is the first thing
to check when an occupancy count looks wrong.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from pxr import Usd, UsdGeom

from ..usd.colliders import (
    EXACT_APPROXIMATION,
    collect_colliders,
    iter_colliders,
    meters_per_unit,
    top_level_rigid_body_roots,
    world_triangles_m,
)
from ..usd.stage import open_stage
from .cache import CACHE_SUFFIX, write_cache
from .grid import COARSE_FACTOR, make_aligned_grid
from .rasterize import (
    EXACT_BAKE_POLICY,
    downsample_max,
    is_watertight,
    rasterize_into,
    signed_clearance,
)

MIXED_BAKE_POLICY = (
    "Mixed bake: approximated colliders were filled rather than cooked by a "
    "physics engine; not bit-identical to a runtime-cooked hull"
)
EXCLUSION_NOTE = "; UsdPhysics.RigidBodyAPI subtrees excluded as movable links"


def _composed_layer_hash(stage: Usd.Stage) -> tuple[str, list[str]]:
    digest = hashlib.sha256()
    identifiers: list[str] = []
    for layer in stage.GetUsedLayers():
        identifier = str(layer.identifier)
        identifiers.append(identifier)
        digest.update(identifier.encode("utf-8"))
        text = layer.ExportToString() or ""
        digest.update(text.encode("utf-8"))
    return digest.hexdigest(), sorted(identifiers)


def bake_stage(
    stage_path: Path,
    cache_path: Path,
    *,
    report_path: Path | None = None,
    exclude_rigid_bodies: bool = True,
    verbose: bool = False,
) -> dict[str, Any]:
    """Rasterise every enabled static collider and publish one cache."""

    stage_path = Path(stage_path)
    cache_path = Path(cache_path)
    # Accept either "name" or "name.scene_geometry_v1".
    if not str(cache_path).endswith(CACHE_SUFFIX):
        cache_path = cache_path.with_name(cache_path.name + CACHE_SUFFIX)

    started = time.perf_counter()
    stage = open_stage(stage_path)
    up_axis = str(UsdGeom.GetStageUpAxis(stage)).upper()
    if up_axis != "Z":
        raise ValueError(f"the cache grid requires a Z-up stage, got {up_axis}")
    mpu = meters_per_unit(stage)

    exclusions = top_level_rigid_body_roots(stage) if exclude_rigid_bodies else []
    records, _, _ = collect_colliders(stage, exclude_prefixes=exclusions)

    cache_xform = UsdGeom.XformCache(Usd.TimeCode.Default())
    approximations: dict[str, int] = {}
    geometry: list[tuple[str, str, np.ndarray, np.ndarray]] = []
    lower = np.full(3, np.inf)
    upper = np.full(3, -np.inf)
    failures: list[dict[str, str]] = []
    for prim in iter_colliders(stage, exclude_prefixes=exclusions):
        path = str(prim.GetPath())
        record = next((r for r in records if r.prim_path == path), None)
        if record is None:
            continue
        approximations[record.approximation] = approximations.get(record.approximation, 0) + 1
        try:
            vertices, triangles = world_triangles_m(prim, mpu=mpu, xform_cache=cache_xform)
        except Exception as error:  # noqa: BLE001 - recorded, not swallowed
            failures.append({"prim_path": path, "stage": "read", "error": str(error)})
            continue
        geometry.append((path, record.approximation, vertices, triangles))
        lower = np.minimum(lower, vertices.min(axis=0))
        upper = np.maximum(upper, vertices.max(axis=0))

    if not geometry:
        raise RuntimeError("no collider geometry could be read from the stage")

    grid = make_aligned_grid(lower, upper)
    occupancy = np.zeros(grid.fine_shape, dtype=np.bool_)
    triangle_total = 0
    splat_total = 0
    solid_total = 0
    for path, approximation, vertices, triangles in geometry:
        try:
            triangle_total += int(triangles.shape[0])
            solid = approximation != EXACT_APPROXIMATION or is_watertight(vertices, triangles)
            solid_total += int(solid)
            splat_total += rasterize_into(
                occupancy, grid, vertices, triangles, fill=solid,
            )
        except Exception as error:  # noqa: BLE001
            failures.append({"prim_path": path, "stage": "rasterise", "error": str(error)})
    if not occupancy.any():
        raise RuntimeError("bake produced an empty occupancy grid")

    if verbose:
        print(
            f"colliders={len(geometry)} excluded={len(exclusions)} "
            f"grid={grid.fine_shape} occupied={int(occupancy.sum())}",
            flush=True,
        )

    fine_clearance = signed_clearance(occupancy, grid.fine_voxel_size_m)
    coarse_clearance = downsample_max(fine_clearance, COARSE_FACTOR)

    exact_only = set(approximations) <= {EXACT_APPROXIMATION}
    policy = EXACT_BAKE_POLICY if exact_only else MIXED_BAKE_POLICY
    if exclusions:
        policy += EXCLUSION_NOTE

    layer_hash, layer_identifiers = _composed_layer_hash(stage)
    manifest = write_cache(
        cache_path,
        grid,
        occupancy,
        fine_clearance,
        coarse_clearance,
        collision_policy=policy,
        source={
            "stage": str(stage_path),
            "composed_layers_sha256": layer_hash,
            "composed_layer_count": len(layer_identifiers),
            "meters_per_unit": mpu,
            "up_axis": up_axis,
            "included_collider_paths": [path for path, _, _, _ in geometry],
            "movable_link_exclusions": exclusions,
        },
    )

    report: dict[str, Any] = {
        "schema": "BakeReportV1",
        "status": "PASS" if not failures else "PASS_WITH_FAILURES",
        "stage": str(stage_path),
        "cache": str(cache_path),
        "elapsed_s": round(time.perf_counter() - started, 2),
        "meters_per_unit": mpu,
        "up_axis": up_axis,
        "grid_fine_shape": list(grid.fine_shape),
        "grid_coarse_shape": list(grid.coarse_shape),
        "scene_bounds_min_m": [round(float(v), 4) for v in grid.scene_bounds_min_m],
        "scene_bounds_max_m": [round(float(v), 4) for v in grid.scene_bounds_max_m],
        "static_colliders": len(geometry),
        "movable_exclusions": exclusions,
        "approximation_counts": approximations,
        "exact_bake": exact_only,
        "collision_policy": policy,
        "solid_filled_colliders": solid_total,
        "surface_only_colliders": len(geometry) - solid_total,
        "triangles": triangle_total,
        "source_voxel_splats": splat_total,
        "occupied_voxels": int(occupancy.sum()),
        "occupancy_ratio": round(float(occupancy.mean()), 6),
        "failures": failures,
        "manifest_arrays": manifest["arrays"],
    }
    if report_path is not None:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
