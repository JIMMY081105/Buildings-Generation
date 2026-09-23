"""Where an upright body of pinned size can stand, and what it can walk to.

Derived from NVIDIA ProtoMotions ``standability`` (Apache-2.0); see NOTICE and
docs/provenance.md.  The candidate-support derivation is the upstream one.  The
representation is not: upstream keeps one support height per XY column, which
is enough for a single-storey scene and structurally unable to describe a
building where the same column is floor on level 0 and ceiling on level 1.  A
multi-storey pipeline needs the full volume, so that is what is kept here.

--------------------------------------------------------------------------------
The one thing to get right
--------------------------------------------------------------------------------

The body radius must erode the **whole** free region, not just the seeds a
flood starts from.

An earlier version of this check used the 0.25 m radius only to pick valid
start cells and then flooded over un-eroded connectivity.  Narrow gaps - the
side of a staircase, a gap between a wall and a cabinet, a half-blocked
doorway - stayed connected, so buildings looked globally reachable.  Under the
correct semantics the same 50 buildings went from 50/50 "pass" to 0/50, and
only 5 of 100 storeys retained any usable region at all.

:func:`derive_standable` therefore dilates the blocked set by the footprint
before anything else, and every consumer in this repository works off that
eroded volume.  If you add a check, take the volume from here; do not
re-derive connectivity from raw occupancy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .grid import FINE_VOXEL_SIZE_M, GridSpec

#: 4-neighbourhood in XY.  An 8-neighbourhood would let a body cross a diagonal
#: gap it is too wide to fit through.
CARDINAL_NEIGHBORS = ((-1, 0), (0, -1), (0, 1), (1, 0))


class PinnedSemanticsError(ValueError):
    """Someone tried to move a pinned Standability V1 number."""


@dataclass(frozen=True)
class StandabilityV1:
    """The pinned upright-body proxy.

    Not configurable per building type.  A humanoid is the same size in a
    school and in a factory; letting a profile widen the body is how a building
    that cannot be traversed gets accepted.
    """

    body_radius_m: float = 0.25
    body_height_m: float = 1.60
    max_support_step_m: float = 0.20
    min_component_area_m2: float = 1.00

    def validate(self, voxel_size_m: float = FINE_VOXEL_SIZE_M) -> None:
        pinned = (0.25, 1.60, 0.20, 1.00, FINE_VOXEL_SIZE_M)
        actual = (
            self.body_radius_m,
            self.body_height_m,
            self.max_support_step_m,
            self.min_component_area_m2,
            voxel_size_m,
        )
        if not all(
            math.isfinite(value) and math.isclose(value, expected, abs_tol=1.0e-12)
            for value, expected in zip(actual, pinned)
        ):
            raise PinnedSemanticsError(
                "Standability V1 numerical semantics are pinned "
                "(0.25 m radius, 1.60 m height, 0.20 m step, 1.00 m^2 component, 5 cm voxel)"
            )

    def body_height_cells(self, voxel_size_m: float = FINE_VOXEL_SIZE_M) -> int:
        return int(round(self.body_height_m / voxel_size_m))

    def max_step_cells(self, voxel_size_m: float = FINE_VOXEL_SIZE_M) -> int:
        return int(round(self.max_support_step_m / voxel_size_m))

    def min_component_cells(self, voxel_size_m: float = FINE_VOXEL_SIZE_M) -> int:
        return int(math.ceil(self.min_component_area_m2 / voxel_size_m**2 - 1.0e-12))

    def semantics(
        self,
        voxel_size_m: float = FINE_VOXEL_SIZE_M,
        clearance_mode: str = "terrain_following",
    ) -> dict[str, Any]:
        self.validate(voxel_size_m)
        return {
            "schema": "StandabilityV1",
            "body_radius_m": self.body_radius_m,
            "body_height_m": self.body_height_m,
            "body_height_cells": self.body_height_cells(voxel_size_m),
            "max_support_step_m": self.max_support_step_m,
            "max_support_step_cells": self.max_step_cells(voxel_size_m),
            "min_component_area_m2": self.min_component_area_m2,
            "min_component_area_cells": self.min_component_cells(voxel_size_m),
            "voxel_size_m": voxel_size_m,
            "neighbourhood": "cardinal in XY, +/- max_support_step in Z",
            "footprint": "closed_disk_intersects_closed_xy_voxel_square",
            "radius_applied_to": "whole free volume (dilation of the blocked set), not seeds only",
            "clearance_mode": clearance_mode,
            "support_top_z": "scene_bounds_min_z + (z_index + 1) * voxel_size_m",
        }


def proxy_footprint(radius_m: float, voxel_size_m: float) -> np.ndarray:
    """Closed-disk-vs-closed-square footprint stamp, as a boolean XY kernel."""

    max_offset = int(math.ceil(radius_m / voxel_size_m + 0.5))
    offsets = range(-max_offset, max_offset + 1)
    footprint = np.zeros((2 * max_offset + 1, 2 * max_offset + 1), dtype=np.bool_)
    half = 0.5 * voxel_size_m
    for ix, x_offset in enumerate(offsets):
        dx = max(abs(x_offset) * voxel_size_m - half, 0.0)
        for iy, y_offset in enumerate(offsets):
            dy = max(abs(y_offset) * voxel_size_m - half, 0.0)
            footprint[ix, iy] = dx * dx + dy * dy <= radius_m * radius_m
    return footprint


def step_structure(config: StandabilityV1, voxel_size_m: float) -> np.ndarray:
    """Walking contract as a dilation structure: cardinal XY, bounded Z step."""

    cells = config.max_step_cells(voxel_size_m)
    structure = np.zeros((3, 3, 2 * cells + 1), dtype=bool)
    structure[1, 1, :] = True
    structure[0, 1, :] = True
    structure[2, 1, :] = True
    structure[1, 0, :] = True
    structure[1, 2, :] = True
    return structure


@dataclass(frozen=True)
class StandableVolume:
    """Voxels a body can stand on, after erosion and component filtering.

    ``mask[x, y, z]`` is True when the voxel at ``z`` is solid, the voxel above
    it is free, and a 1.60 m body of 0.25 m radius fits there.  The walking
    surface is the *top* of that voxel.
    """

    mask: np.ndarray
    grid: GridSpec
    config: StandabilityV1
    clearance_mode: str = "terrain_following"

    @property
    def area_m2(self) -> float:
        """Total walkable area, summed over every storey."""

        return float(self.mask.sum()) * self.grid.cell_area_m2

    @property
    def footprint(self) -> np.ndarray:
        """XY columns with at least one standable voxel."""

        return self.mask.any(axis=2)

    def z_centers_m(self) -> np.ndarray:
        return self.grid.axis_centers_m(2)

    def surface_z_m(self) -> np.ndarray:
        """World Z of the walking surface for every voxel index."""

        return (
            self.grid.scene_bounds_min_m[2]
            + (np.arange(self.grid.fine_shape[2], dtype=np.float64) + 1.0)
            * self.grid.fine_voxel_size_m
        )

    def area_of(self, selection: np.ndarray) -> float:
        return float(np.asarray(selection, dtype=bool).sum()) * self.grid.cell_area_m2


#: Clearance models.  See :func:`derive_standable`.
CLEARANCE_MODES = ("terrain_following", "fixed_base")
DEFAULT_CLEARANCE_MODE = "terrain_following"


def derive_standable(
    occupancy: np.ndarray,
    grid: GridSpec,
    config: StandabilityV1 = StandabilityV1(),
    *,
    ground_plane_z_m: float | None = None,
    clearance_mode: str = DEFAULT_CLEARANCE_MODE,
    filter_small_components: bool = True,
) -> StandableVolume:
    """Every voxel an upright body of the pinned size can stand on.

    ``ground_plane_z_m`` reinstates the simulator's flat terrain when the source
    building authors no slab collider for its lowest storey.  Without it the
    ground floor has nothing to stand on and every later check reports an
    unreachable building for a reason unrelated to its geometry.  The plane is
    rasterised the way the conservative splat would: every cell the half-space
    ``z <= ground_plane_z_m`` touches.

    ``clearance_mode`` picks how the body is modelled above its support, and on
    a staircase the choice decides the answer:

    ``fixed_base``
        A rigid cylinder from the support up to 1.60 m.  Correct for a flat
        floor and *necessarily wrong on a stair*: standing on tread ``k``, the
        cylinder of 0.25 m radius reaches tread ``k+1``, which is only one riser
        above it.  Every flight then reports zero standable treads - not
        because the stair is bad, but because the proxy cannot bend a knee.

    ``terrain_following`` (default here)
        The first ``max_support_step_m`` above the support is treated as a
        foot/lower-leg terrain band rather than rigid body; above that band the
        eroded clearance must hold all the way to 1.60 m.  This is what makes a
        legal flight measurable.  It deliberately does **not** claim the body
        can pass through anything: tread ``k+2`` and everything higher still
        has to clear the eroded footprint.

    Both modes use the same pinned numbers; the mode is recorded in the
    evidence so a result is never ambiguous about which one produced it.
    """

    from scipy import ndimage

    if clearance_mode not in CLEARANCE_MODES:
        raise ValueError(f"clearance_mode must be one of {CLEARANCE_MODES}")
    config.validate(grid.fine_voxel_size_m)
    occupied = np.array(occupancy, dtype=bool, copy=True)
    if occupied.shape != tuple(grid.fine_shape):
        raise ValueError("occupancy shape does not match the grid fine_shape")

    if ground_plane_z_m is not None:
        centers = grid.axis_centers_m(2)
        below = (centers - 0.5 * grid.fine_voxel_size_m) <= ground_plane_z_m + 1.0e-9
        occupied[:, :, below] = True

    real = grid.real_cell_mask()
    known_free = real & ~occupied

    # THE erosion.  Dilating the blocked set by the body footprint is
    # equivalent to eroding the free volume, and it applies everywhere - not
    # only where a flood happens to be seeded.
    footprint = proxy_footprint(config.body_radius_m, grid.fine_voxel_size_m)[:, :, None]
    body_cells = config.body_height_cells(grid.fine_voxel_size_m)
    relief_cells = (
        config.max_step_cells(grid.fine_voxel_size_m)
        if clearance_mode == "terrain_following"
        else 0
    )
    candidate_count = occupied.shape[2] - body_cells
    if candidate_count <= 0:
        raise ValueError("scene Z extent is shorter than the pinned 1.60 m body proxy")

    blocked = ~real | occupied
    dilated = ndimage.binary_dilation(blocked, structure=footprint, iterations=1, border_value=1)
    prefix = np.concatenate(
        (
            np.zeros((*dilated.shape[:2], 1), dtype=np.int32),
            np.cumsum(dilated, axis=2, dtype=np.int32),
        ),
        axis=2,
    )
    body_blocked = (
        prefix[:, :, body_cells + 1:]
        - prefix[:, :, relief_cells + 1: relief_cells + candidate_count + 1]
    )

    mask = np.zeros(occupied.shape, dtype=bool)
    mask[:, :, :candidate_count] = (
        occupied[:, :, :candidate_count]            # something solid to stand on
        & real[:, :, :candidate_count]
        & known_free[:, :, 1: candidate_count + 1]  # the voxel above it is free
        & (body_blocked == 0)                       # eroded clearance to 1.60 m
    )

    if filter_small_components:
        mask = drop_small_components(mask, config, grid.fine_voxel_size_m)
    return StandableVolume(
        mask=mask, grid=grid, config=config, clearance_mode=clearance_mode
    )


def _pair_slices(
    shape: tuple[int, int, int],
    shift: tuple[int, int, int],
) -> tuple[tuple[slice, ...], tuple[slice, ...]]:
    """Slices selecting every in-bounds pair ``(p, p + shift)``."""

    source: list[slice] = []
    target: list[slice] = []
    for size, delta in zip(shape, shift):
        if delta >= 0:
            source.append(slice(0, size - delta))
            target.append(slice(delta, size))
        else:
            source.append(slice(-delta, size))
            target.append(slice(0, size + delta))
    return tuple(source), tuple(target)


def walk_graph(mask: np.ndarray, config: StandabilityV1, voxel_size_m: float):
    """Sparse adjacency of standable voxels under the walking contract.

    Returns ``(index, graph)``: ``index`` maps a voxel to its node id (-1 when
    not standable), ``graph`` is the symmetric CSR adjacency.  Connectivity is
    exactly the contract a route must satisfy - cardinal in XY, at most
    ``max_support_step_m`` in Z - so one component is a region a body can walk
    around inside without ever leaving it.
    """

    from scipy.sparse import coo_matrix

    mask = np.asarray(mask, dtype=bool)
    index = np.full(mask.shape, -1, dtype=np.int64)
    count = int(mask.sum())
    if count == 0:
        return index, coo_matrix((0, 0), dtype=np.int8).tocsr()
    index[mask] = np.arange(count, dtype=np.int64)

    max_step = config.max_step_cells(voxel_size_m)
    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    for dx, dy in ((1, 0), (0, 1)):  # the opposite directions are the same edges
        for dz in range(-max_step, max_step + 1):
            source, target = _pair_slices(mask.shape, (dx, dy, dz))
            a = index[source]
            b = index[target]
            pairs = (a >= 0) & (b >= 0)
            if not pairs.any():
                continue
            rows.append(a[pairs])
            cols.append(b[pairs])
    if rows:
        row = np.concatenate(rows)
        col = np.concatenate(cols)
    else:
        row = col = np.zeros(0, dtype=np.int64)
    graph = coo_matrix(
        (np.ones(row.size, dtype=np.int8), (row, col)), shape=(count, count)
    ).tocsr()
    return index, graph


def label_components(
    mask: np.ndarray,
    config: StandabilityV1,
    voxel_size_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """``(labels, sizes)``: per-voxel component id (-1 outside) and voxel counts."""

    from scipy.sparse.csgraph import connected_components

    mask = np.asarray(mask, dtype=bool)
    index, graph = walk_graph(mask, config, voxel_size_m)
    if graph.shape[0] == 0:
        return index, np.zeros(0, dtype=np.int64)
    _, labels = connected_components(graph, directed=False)
    sizes = np.bincount(labels)
    labelled = np.full(mask.shape, -1, dtype=np.int64)
    labelled[mask] = labels
    return labelled, sizes


def drop_small_components(
    mask: np.ndarray,
    config: StandabilityV1,
    voxel_size_m: float,
) -> np.ndarray:
    """Remove standable islands below the pinned minimum area."""

    labels, sizes = label_components(mask, config, voxel_size_m)
    if sizes.size == 0:
        return np.asarray(mask, dtype=bool)
    minimum = config.min_component_cells(voxel_size_m)
    keep = sizes >= minimum
    result = np.zeros(mask.shape, dtype=bool)
    inside = labels >= 0
    result[inside] = keep[labels[inside]]
    return result


def walk_reachable(
    mask: np.ndarray,
    seeds: np.ndarray,
    config: StandabilityV1,
    voxel_size_m: float,
) -> np.ndarray:
    """Standable voxels reachable from ``seeds`` under the walking contract."""

    from scipy import ndimage

    mask = np.asarray(mask, dtype=bool)
    seed = np.asarray(seeds, dtype=bool) & mask
    if not seed.any():
        return np.zeros_like(mask)
    return ndimage.binary_dilation(
        seed,
        structure=step_structure(config, voxel_size_m),
        mask=mask,
        iterations=-1,
    )


def largest_component_seed(
    mask: np.ndarray,
    config: StandabilityV1,
    voxel_size_m: float,
    *,
    within: np.ndarray | None = None,
) -> np.ndarray | None:
    """A one-voxel seed inside the largest component (optionally within a region)."""

    region = np.asarray(mask, dtype=bool)
    if within is not None:
        region = region & np.asarray(within, dtype=bool)
    if not region.any():
        return None
    labels, sizes = label_components(region, config, voxel_size_m)
    best = int(np.argmax(sizes))
    where = np.argwhere(labels == best)
    seed = np.zeros(mask.shape, dtype=bool)
    seed[tuple(where[0])] = True
    return seed


def component_areas_m2(
    mask: np.ndarray,
    config: StandabilityV1,
    voxel_size_m: float,
) -> list[float]:
    """Area of every standable component, largest first."""

    _, sizes = label_components(mask, config, voxel_size_m)
    cell_area = voxel_size_m * voxel_size_m
    return sorted((float(size) * cell_area for size in sizes), reverse=True)


def route_length_m(
    mask: np.ndarray,
    seeds: np.ndarray,
    targets: np.ndarray,
    config: StandabilityV1,
    voxel_size_m: float,
) -> tuple[float | None, tuple[int, int, int] | None, tuple[int, int, int] | None]:
    """Shortest walking route from ``seeds`` to any target voxel, in metres.

    Measured in walking steps over the same contract used for connectivity, so
    a route this returns is one the component filter would keep.  One step is
    one XY cell; a step that also changes height is still one step, which is
    why the result is compared against a straight-line distance that includes
    the height difference.
    """

    from scipy.sparse.csgraph import dijkstra

    mask = np.asarray(mask, dtype=bool)
    frontier = np.asarray(seeds, dtype=bool) & mask
    goals = np.asarray(targets, dtype=bool) & mask
    if not frontier.any() or not goals.any():
        return None, None, None
    index, graph = walk_graph(mask, config, voxel_size_m)
    start_cell = tuple(int(value) for value in np.argwhere(frontier)[0])
    distances = dijkstra(
        graph, directed=False, indices=[int(index[start_cell])], unweighted=True
    )[0]
    goal_ids = index[goals]
    goal_distances = distances[goal_ids]
    finite = np.isfinite(goal_distances)
    if not finite.any():
        return None, start_cell, None
    best = int(np.argmin(np.where(finite, goal_distances, np.inf)))
    goal_cell = tuple(int(value) for value in np.argwhere(goals)[best])
    return float(goal_distances[best]) * voxel_size_m, start_cell, goal_cell
