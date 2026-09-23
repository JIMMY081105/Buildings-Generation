"""End-to-end: source building in, connected and accepted building out.

    scan     bake the source and report what is there (storeys, heights, area)
    connect  design a stair per storey pair, place it, author it into an overlay
    bake     rasterise the connected stage into an immutable cache
    accept   run the gates and write the evidence record

``connect`` never writes to the source.  It composes the source under a new
root layer and authors the stair references there; see
``buildingsgen.usd.stage`` for why.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .acceptance.context import AcceptanceContext
from .acceptance.runner import run_acceptance, utc_now
from .config import BuildingProfile
from .stairs.asset import build_stair_usd, reference_stair_into, world_triangles
from .stairs.placement import find_placements
from .stairs.spec import design_stair
from .usd.stage import (
    assert_source_unchanged,
    export_flattened,
    open_with_overlay,
    source_ref,
)
from .usd.storeys import authored_surface_z_m, detect_storeys, name_hint_labels
from .voxel.bake import bake_stage
from .voxel.cache import CACHE_SUFFIX, load_cache
from .voxel.rasterize import rasterize_into
from .voxel.standability import (
    StandabilityV1,
    derive_standable,
    largest_component_seed,
    walk_reachable,
)


@dataclass
class Workspace:
    """Where a run writes.  One directory per building, nothing outside it."""

    root: Path
    name: str

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        (self.root / "stages").mkdir(parents=True, exist_ok=True)
        (self.root / "caches").mkdir(parents=True, exist_ok=True)
        (self.root / "assets").mkdir(parents=True, exist_ok=True)
        (self.root / "evidence").mkdir(parents=True, exist_ok=True)

    def stage(self, suffix: str) -> Path:
        return self.root / "stages" / f"{self.name}_{suffix}.usda"

    def cache(self, suffix: str) -> Path:
        return self.root / "caches" / f"{self.name}_{suffix}{CACHE_SUFFIX}"

    def asset(self, suffix: str) -> Path:
        return self.root / "assets" / f"{self.name}_{suffix}" / "stair.usda"

    def evidence(self, name: str) -> Path:
        return self.root / "evidence" / name


def scan_building(
    source: Path,
    profile: BuildingProfile,
    *,
    workspace: Workspace | None = None,
    ground_plane_z_m: float | None = 0.0,
    config: StandabilityV1 = StandabilityV1(),
) -> dict[str, Any]:
    """Bake a source building and report its storeys without changing anything.

    Uses a throwaway cache when no workspace is given, so scanning a corpus
    leaves nothing behind.
    """

    from .usd.stage import open_stage

    source = Path(source)
    reference = source_ref(source)
    temporary = None
    if workspace is None:
        temporary = Path(tempfile.mkdtemp(prefix="buildingsgen-scan-"))
        cache_path = temporary / f"scan{CACHE_SUFFIX}"
    else:
        cache_path = workspace.cache("source")
        if cache_path.exists():
            shutil.rmtree(cache_path)
    try:
        bake = bake_stage(source, cache_path)
        cache = load_cache(cache_path, verify_hashes=False)
        volume = derive_standable(
            cache.occupancy, cache.grid, config, ground_plane_z_m=ground_plane_z_m
        )
        stage = open_stage(source)
        detection = detect_storeys(
            volume, profile.storeys, labels=name_hint_labels(stage, profile.storeys)
        )
        record = {
            "schema": "BuildingScanV1",
            "generated_at_utc": utc_now(),
            "source": reference.as_dict(),
            "building_type": profile.name,
            "bake": {
                "grid_fine_shape": bake["grid_fine_shape"],
                "static_colliders": bake["static_colliders"],
                "occupied_voxels": bake["occupied_voxels"],
                "exact_bake": bake["exact_bake"],
            },
            "standable_area_m2": round(volume.area_m2, 2),
            "storeys": detection.as_dict(),
            "needs_stair": detection.count >= 2,
        }
    finally:
        assert_source_unchanged(reference)
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    if workspace is not None:
        workspace.evidence("scan.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
    return record


def connect_storeys(
    source: Path,
    workspace: Workspace,
    profile: BuildingProfile,
    *,
    families: list[str] | None = None,
    ground_plane_z_m: float | None = 0.0,
    config: StandabilityV1 = StandabilityV1(),
    max_pairs: int | None = None,
    verify_attempts: int = 4,
) -> dict[str, Any]:
    """Design, place and author one stair per consecutive storey pair.

    Returns the connection record.  Raises when no admissible placement exists
    for a pair - which is a real answer about that building, not a bug to route
    around by relaxing the footprint.
    """

    source = Path(source).resolve()
    reference = source_ref(source)

    cache_path = workspace.cache("source")
    if cache_path.exists():
        shutil.rmtree(cache_path)
    bake_stage(source, cache_path)
    cache = load_cache(cache_path, verify_hashes=False)
    volume = derive_standable(
        cache.occupancy, cache.grid, config, ground_plane_z_m=ground_plane_z_m
    )
    from .usd.stage import open_stage

    detection = detect_storeys(
        volume, profile.storeys, labels=name_hint_labels(open_stage(source), profile.storeys)
    )
    if detection.count < 2:
        raise RuntimeError(
            f"{source.name} has {detection.count} detected storey band(s); a stair needs two. "
            "Check the profile's min_storey_floor_area_m2 before assuming the building is flat."
        )

    overlay_path = workspace.stage("connected")
    if overlay_path.exists():
        overlay_path.unlink()
    stage, _ = open_with_overlay(source, overlay_path)

    pairs = list(zip(detection.storeys[:-1], detection.storeys[1:]))
    if max_pairs is not None:
        pairs = pairs[:max_pairs]

    connections: list[dict[str, Any]] = []
    for index, (lower, upper) in enumerate(pairs):
        rise = upper.floor_z_m - lower.floor_z_m
        attempts: list[dict[str, Any]] = []
        chosen = None
        for family in (families or list(profile.stair.families)):
            try:
                spec = design_stair(rise, profile.stair, family=family)
                placements = find_placements(
                    np.asarray(cache.occupancy), cache.grid, volume,
                    lower, upper, spec, profile,
                )
            except Exception as error:  # noqa: BLE001 - each attempt is recorded
                attempts.append({"family": family, "rejected": f"{type(error).__name__}: {error}"})
                continue
            # The search works on a proxy of the flight; commit only to a pose
            # whose stair, rasterised where it will actually go, connects the
            # two storeys.  Without this the pipeline can ship a flight that
            # measures perfectly and lands a voxel short of the slab.
            floor_z = authored_surface_z_m(
                open_stage(source), lower.floor_z_m, fallback_z_m=ground_plane_z_m
            )
            verified = None
            checks: list[dict[str, Any]] = []
            for candidate in placements[:verify_attempts]:
                check = verify_placement(
                    np.asarray(cache.occupancy), cache.grid, lower, upper, spec, candidate,
                    floor_z_m=floor_z, config=config, ground_plane_z_m=ground_plane_z_m,
                    min_connected_area_m2=profile.acceptance.min_reachable_area_per_storey_m2,
                )
                checks.append(check)
                if check["connects"]:
                    verified = candidate
                    break
            if verified is None:
                attempts.append({
                    "family": family,
                    "rejected": "no placement connected the storeys once the flight was placed",
                    "placements_checked": checks,
                })
                continue
            chosen = (spec, verified)
            attempts.append(
                {
                    "family": family,
                    "accepted": True,
                    "spec": spec.as_dict(),
                    "placements_considered": len(placements),
                    "placements_verified": checks,
                }
            )
            break
        if chosen is None:
            raise RuntimeError(
                f"no stair family fits the {rise:.2f} m rise between "
                f"{lower.label} and {upper.label} in {source.name}:\n"
                + json.dumps(attempts, indent=2)
            )

        spec, placement = chosen
        # The detected floor height is voxel-quantised and conservative; the
        # stair sits on the surface the building actually authors, or the
        # placement itself adds up to one voxel to the first rise.
        translate = (placement.translate_m[0], placement.translate_m[1], floor_z)
        asset_path = workspace.asset(f"stair_{index}")
        if asset_path.parent.exists():
            shutil.rmtree(asset_path.parent)
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset = build_stair_usd(spec, asset_path)
        prim_path = f"/World/Stair_{index}"
        reference_stair_into(
            stage,
            asset_path,
            prim_path=prim_path,
            translate_m=translate,
            rotate_z_deg=float(placement.orientation_deg),
        )
        connections.append(
            {
                "index": index,
                "lower_storey": lower.as_dict(),
                "upper_storey": upper.as_dict(),
                "rise_m": round(rise, 4),
                "family_attempts": attempts,
                "spec": spec.as_dict(),
                "placement": placement.as_dict(),
                "authored_floor_z_m": round(float(floor_z), 4),
                "applied_translate_m": [round(float(value), 4) for value in translate],
                "asset": asset,
                "prim_path": prim_path,
            }
        )

    stage.GetRootLayer().Save()
    flattened = export_flattened(stage, workspace.stage("flat"))
    assert_source_unchanged(reference)

    record = {
        "schema": "StoreyConnectionV1",
        "generated_at_utc": utc_now(),
        "source": reference.as_dict(),
        "building_type": profile.name,
        "overlay_stage": str(overlay_path),
        "flattened_stage": str(flattened),
        "source_unmodified": True,
        "storeys": detection.as_dict(),
        "connections": connections,
        "stair_prim_paths": [entry["prim_path"] for entry in connections],
    }
    workspace.evidence("connect.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    return record


def verify_placement(
    occupancy: np.ndarray,
    grid,
    lower,
    upper,
    spec,
    placement,
    *,
    floor_z_m: float,
    config: StandabilityV1,
    ground_plane_z_m: float | None,
    min_connected_area_m2: float = 0.0,
) -> dict[str, Any]:
    """Would this pose actually let a body climb to the upper storey?

    Rasterises the flight where it would go, re-derives standability over the
    combined occupancy, and floods from the lower storey.  It is the same
    measurement Gate 5 and Gate 7 make later, done early enough to reject a
    pose instead of shipping it.

    Two voxels of horizontal slack at the top of a flight are invisible to a
    footprint search and fatal to a walk, so this check is not redundant with
    the search - it is what makes the search's approximations safe.
    """

    vertices, triangles = world_triangles(
        spec,
        translate_m=(placement.translate_m[0], placement.translate_m[1], floor_z_m),
        rotate_z_deg=float(placement.orientation_deg),
    )
    combined = np.array(occupancy, dtype=bool, copy=True)
    try:
        rasterize_into(combined, grid, vertices, triangles, fill=True)
    except RuntimeError as error:
        return {"connects": False, "reason": f"stair falls outside the grid: {error}"}

    volume = derive_standable(combined, grid, config, ground_plane_z_m=ground_plane_z_m)
    voxel = grid.fine_voxel_size_m
    lower_band = _band_of(volume, lower)
    upper_band = _band_of(volume, upper)
    seed = largest_component_seed(volume.mask, config, voxel, within=lower_band)
    if seed is None:
        return {
            "connects": False,
            "reason": "no standable ground component with the stair in place",
        }
    reached = walk_reachable(volume.mask, seed, config, voxel)
    # The stair's own treads sit inside the upper band, so reaching the top
    # tread is not reaching the storey.  Require area the flight does not
    # itself provide.
    stair_columns = np.zeros(grid.fine_shape, dtype=bool)
    rasterize_into(stair_columns, grid, vertices, triangles, fill=True)
    on_storey = reached & upper_band & ~stair_columns
    area = float(on_storey.sum()) * voxel * voxel
    # Touching the slab is not connecting to the storey.  A flight that lands
    # in a pocket the rest of the floor cannot be walked to from is exactly the
    # failure Gate 7 would report later; reject it here instead.
    return {
        "connects": area >= max(min_connected_area_m2, 1e-9),
        "min_connected_area_m2": min_connected_area_m2,
        "orientation_deg": placement.orientation_deg,
        "origin_cell": list(placement.origin_cell),
        "arrival_gap_m": round(placement.arrival_gap_m, 3),
        "upper_storey_area_reached_m2": round(area, 2),
    }


def _band_of(volume, storey) -> np.ndarray:
    surface = volume.surface_z_m()
    half = 0.5 * volume.grid.fine_voxel_size_m
    rows = (surface >= storey.band_min_z_m - half) & (surface <= storey.band_max_z_m + half)
    band = np.zeros(volume.mask.shape, dtype=bool)
    band[:, :, rows] = volume.mask[:, :, rows]
    return band


def bake_connected(workspace: Workspace, *, stage_suffix: str = "flat") -> dict[str, Any]:
    """Bake the connected stage into the cache the gates will read."""

    cache_path = workspace.cache("connected")
    if cache_path.exists():
        shutil.rmtree(cache_path)
    return bake_stage(
        workspace.stage(stage_suffix),
        cache_path,
        report_path=workspace.evidence("bake.json"),
    )


def accept_building(
    workspace: Workspace,
    profile: BuildingProfile,
    *,
    stair_prim_path: str | None = None,
    ground_plane_z_m: float | None = 0.0,
    keep_going: bool = False,
) -> dict[str, Any]:
    """Run the gates against the connected stage and cache."""

    context = AcceptanceContext(
        name=workspace.name,
        stage_path=workspace.stage("flat"),
        cache_path=workspace.cache("connected"),
        profile=profile,
        ground_plane_z_m=ground_plane_z_m,
        stair_prim_path=stair_prim_path,
    )
    return run_acceptance(
        context, keep_going=keep_going, report_path=workspace.evidence("acceptance.json")
    )


def run_all(
    source: Path,
    workspace: Workspace,
    profile: BuildingProfile,
    *,
    ground_plane_z_m: float | None = 0.0,
    keep_going: bool = False,
) -> dict[str, Any]:
    """connect -> bake -> accept, with every intermediate record kept."""

    connection = connect_storeys(
        source, workspace, profile, ground_plane_z_m=ground_plane_z_m
    )
    bake = bake_connected(workspace)
    acceptance = accept_building(
        workspace,
        profile,
        stair_prim_path=(
            connection["stair_prim_paths"][0] if connection["stair_prim_paths"] else None
        ),
        ground_plane_z_m=ground_plane_z_m,
        keep_going=keep_going,
    )
    return {"connect": connection, "bake": bake, "acceptance": acceptance}
