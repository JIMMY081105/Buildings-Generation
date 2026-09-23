"""Choose where a stair can actually go.

A stair connects two storeys only if four things hold at once, and checking
fewer than four is how a flight ends up in a wall or under a slab:

1. **Base** - the whole footprint rests on standable lower-storey floor.
2. **Shaft** - the volume the flight climbs through is clear from just above
   the lower floor to a body height above the upper floor.  This is also the
   opening in the upper slab: no separate "cut a hole" step is needed, because
   a placement without a hole simply fails this test.
3. **Arrival** - the cell the flight lands on is standable *on the upper
   storey*, not merely inside the shaft.  A flight ending in mid-air over the
   opening passes 1 and 2.
4. **Departure** - the cell the flight starts from is standable on the lower
   storey, so a policy can reach the stair at all.

Candidates are scored by how much of each storey the arrival and departure can
reach, which is the quantity the acceptance gates measure later.  Scoring by
"fits" alone reliably picks a corner of the building nothing else connects to.

Everything here works on the voxel grid, so it is independent of how the source
building names its prims.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import BuildingProfile
from ..usd.storeys import Storey
from ..voxel.grid import GridSpec
from ..voxel.standability import StandabilityV1, StandableVolume, walk_reachable
from .asset import local_bounds_m
from .spec import StairSpec, traversal_path

#: Rotations tried, in degrees about +Z.
ORIENTATIONS = (0, 90, 180, 270)


@dataclass(frozen=True)
class Placement:
    """One admissible stair pose, in world metres and grid cells."""

    orientation_deg: int
    origin_cell: tuple[int, int]
    footprint_cells: tuple[int, int]
    translate_m: tuple[float, float, float]
    departure_cell: tuple[int, int]
    arrival_cell: tuple[int, int]
    #: Distance from the last tread's far edge to the first standable upper cell.
    arrival_gap_m: float
    lower_reachable_m2: float
    upper_reachable_m2: float

    @property
    def score(self) -> float:
        return min(self.lower_reachable_m2, self.upper_reachable_m2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "orientation_deg": self.orientation_deg,
            "origin_cell": list(self.origin_cell),
            "footprint_cells": list(self.footprint_cells),
            "translate_m": [round(value, 4) for value in self.translate_m],
            "departure_cell": list(self.departure_cell),
            "arrival_cell": list(self.arrival_cell),
            "arrival_gap_m": round(self.arrival_gap_m, 3),
            "lower_reachable_area_m2": round(self.lower_reachable_m2, 2),
            "upper_reachable_area_m2": round(self.upper_reachable_m2, 2),
            "score_m2": round(self.score, 2),
        }


class PlacementError(RuntimeError):
    """No pose in the search satisfies base, shaft, arrival and departure."""


def shaft_mask(
    occupancy: np.ndarray,
    grid: GridSpec,
    lower_floor_z_m: float,
    upper_floor_z_m: float,
    config: StandabilityV1 = StandabilityV1(),
) -> np.ndarray:
    """Columns whose volume is clear from the lower floor past the upper one.

    The top of the window sits a full body height above the upper floor: a
    shaft that stops at the slab lets a flight arrive into a ceiling.
    """

    voxel = grid.fine_voxel_size_m
    centers = grid.axis_centers_m(2)
    window = (centers >= lower_floor_z_m + 0.5 * voxel) & (
        centers <= upper_floor_z_m + config.body_height_m
    )
    if not window.any():
        raise ValueError("the storey pair does not span any voxel rows")
    return ~np.asarray(occupancy, dtype=bool)[:, :, window].any(axis=2)


def _integral(mask: np.ndarray) -> np.ndarray:
    padded = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), dtype=np.int64)
    padded[1:, 1:] = np.cumsum(np.cumsum(mask.astype(np.int64), axis=0), axis=1)
    return padded


def _all_true_rectangles(mask: np.ndarray, height: int, width: int) -> np.ndarray:
    """Boolean map of rectangle origins whose whole ``height x width`` is True."""

    nx, ny = mask.shape
    if height > nx or width > ny or height <= 0 or width <= 0:
        return np.zeros((0, 0), dtype=bool)
    integral = _integral(mask)
    total = (
        integral[height:, width:]
        - integral[:-height, width:]
        - integral[height:, :-width]
        + integral[:-height, :-width]
    )
    return total == height * width


def _rotate_xy(point, degrees: float) -> tuple[float, float]:
    """Rotate about +Z, matching UsdGeom's RotateZOp convention."""

    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    return (point[0] * cos - point[1] * sin, point[0] * sin + point[1] * cos)


def _rotated_bounds(lower, upper, degrees: float):
    corners = [
        (lower[0], lower[1]), (upper[0], lower[1]),
        (upper[0], upper[1]), (lower[0], upper[1]),
    ]
    rotated = [_rotate_xy(corner, degrees) for corner in corners]
    xs = [point[0] for point in rotated]
    ys = [point[1] for point in rotated]
    return (min(xs), min(ys)), (max(xs), max(ys))


def find_placements(
    occupancy: np.ndarray,
    grid: GridSpec,
    volume: StandableVolume,
    lower: Storey,
    upper: Storey,
    spec: StairSpec,
    profile: BuildingProfile,
    *,
    max_results: int = 8,
    clearance_margin_m: float = 0.10,
    max_arrival_gap_m: float = 0.12,
    scored_candidates: int = 16,
) -> list[Placement]:
    """Admissible stair poses, best first.

    ``clearance_margin_m`` pads the reserved footprint so the flight is not
    flush against a wall; the handrail needs the room and so does a body
    walking beside it.

    ``max_arrival_gap_m`` is the real constraint on the top of the flight.  A
    slab opening is usually longer than the flight, so a pose that merely
    "fits" can leave the last tread floating half a metre short of the slab
    edge, with nothing but the stairwell in between.  That pose satisfies base,
    shaft and a standable arrival somewhere ahead - and is unwalkable, because
    the walking contract steps between *adjacent* cells only.  Two voxels is
    already too far, so the default is deliberately tight.
    """

    del profile  # thresholds belong to the gates, not to the search
    config = volume.config
    voxel = grid.fine_voxel_size_m
    # The reserved rectangle covers what must sit over the shaft: treads and
    # the turn platform.  Handrails are excluded because they run on past the
    # last authored tread, over the slab the flight arrives on.
    local_min, local_max = local_bounds_m(spec, include_handrail=False)
    path = traversal_path(spec)
    arrival_dir_local = _direction(path[-2][:2], path[-1][:2])
    departure_dir_local = _direction(path[1][:2], path[0][:2])
    # Probe from the far EDGE of the last authored tread, not from the landing
    # centre.  The landing centre sits on the slab by construction, so a gap
    # measured there is always ~0 and tells you nothing; what decides whether a
    # body can step off the flight is the distance from the tread edge to the
    # first standable upper-storey cell.
    tread_half_depth = 0.5 * spec.going_m + 0.03
    edge_local = (
        path[-2][0] + arrival_dir_local[0] * tread_half_depth,
        path[-2][1] + arrival_dir_local[1] * tread_half_depth,
    )

    shaft = shaft_mask(occupancy, grid, lower.floor_z_m, upper.floor_z_m, config)
    base = lower.footprint
    landing = upper.footprint
    origin_m = np.asarray(grid.scene_bounds_min_m[:2], dtype=np.float64)

    # Pass 1 - geometry.  Cheap per origin, so every candidate is tested: the
    # thinning below must not discard the one origin that puts the flight flush
    # against the slab edge.
    candidates: list[tuple[float, int, tuple[int, int], tuple[int, int],
                           tuple[float, float], tuple[int, int], tuple[int, int]]] = []
    for orientation in ORIENTATIONS:
        rotated_min, rotated_max = _rotated_bounds(local_min[:2], local_max[:2], orientation)
        extent = (
            rotated_max[0] - rotated_min[0] + 2.0 * clearance_margin_m,
            rotated_max[1] - rotated_min[1] + 2.0 * clearance_margin_m,
        )
        cells = (int(np.ceil(extent[0] / voxel)), int(np.ceil(extent[1] / voxel)))
        # The flight needs standable base under it and a clear shaft above it.
        # Requiring both over the whole rectangle is stricter than the flight
        # strictly needs at its first step, and deliberately so: that margin is
        # what keeps a body from clipping the opening edge on the way up.
        fits = _all_true_rectangles(base & shaft, cells[0], cells[1])
        if fits.size == 0 or not fits.any():
            continue
        arrival_dir = _rotate_xy(arrival_dir_local, orientation)
        departure_dir = _rotate_xy(departure_dir_local, orientation)
        edge_xy = _rotate_xy(edge_local, orientation)
        for x, y in zip(*np.nonzero(fits)):
            origin = (int(x), int(y))
            rect_min = origin_m + np.asarray(origin, dtype=np.float64) * voxel
            # The margin protects the sides of the flight, but at the top it
            # would leave a two-voxel void between the last authored tread and
            # the slab edge - and the walking contract steps only between
            # *adjacent* cells, so that void silently disconnects a flight
            # whose every rise measures perfectly.  Slide the flight forward by
            # the margin so its top is flush with the edge it has to meet; the
            # departure end keeps the full clearance.
            translate = (
                float(rect_min[0] + clearance_margin_m - rotated_min[0]
                      + arrival_dir[0] * clearance_margin_m),
                float(rect_min[1] + clearance_margin_m - rotated_min[1]
                      + arrival_dir[1] * clearance_margin_m),
            )
            edge = (translate[0] + edge_xy[0], translate[1] + edge_xy[1])
            arrival = _first_standable_along(
                grid, landing, edge, arrival_dir, search_m=max_arrival_gap_m,
            )
            departure = _first_standable_along(
                grid, base, translate, departure_dir, search_m=0.35,
            )
            if arrival is None or departure is None:
                continue
            gap = _gap_m(grid, edge, arrival)
            candidates.append((gap, orientation, origin, cells, translate, departure, arrival))

    if not candidates:
        raise PlacementError(
            f"no {spec.family} flight of "
            f"{local_max[0] - local_min[0]:.2f} x {local_max[1] - local_min[1]:.2f} m fits "
            f"between z={lower.floor_z_m:.2f} m and z={upper.floor_z_m:.2f} m with a clear "
            f"shaft, a standable departure, and a standable upper-storey arrival within "
            f"{max_arrival_gap_m:.2f} m of the top of the flight"
        )

    # Pass 2 - reachability.  Thin near-duplicates first (smallest arrival gap
    # wins its neighbourhood), then score what is left.
    candidates.sort(key=lambda item: item[0])
    thinned: list[tuple] = []
    for candidate in candidates:
        if any(
            candidate[1] == other[1]
            and max(abs(candidate[2][0] - other[2][0]), abs(candidate[2][1] - other[2][1]))
            < max(4, min(candidate[3]) // 2)
            for other in thinned
        ):
            continue
        thinned.append(candidate)

    results: list[Placement] = []
    for candidate in thinned[:scored_candidates]:
        gap, orientation, origin, cells, translate, departure, arrival = candidate
        lower_area = _reach_area(volume, lower, departure, config, voxel)
        upper_area = _reach_area(volume, upper, arrival, config, voxel)
        if lower_area <= 0.0 or upper_area <= 0.0:
            continue
        results.append(
            Placement(
                orientation_deg=orientation,
                origin_cell=origin,
                footprint_cells=cells,
                translate_m=(translate[0], translate[1], float(lower.floor_z_m)),
                departure_cell=departure,
                arrival_cell=arrival,
                arrival_gap_m=gap,
                lower_reachable_m2=lower_area,
                upper_reachable_m2=upper_area,
            )
        )
    if not results:
        raise PlacementError(
            f"every geometrically admissible {spec.family} pose reaches nothing: "
            f"{len(candidates)} candidate(s) had a standable arrival, none of them "
            "connected to usable area on both storeys"
        )
    results.sort(key=lambda placement: placement.score, reverse=True)
    return results[:max_results]


def _direction(start, end) -> tuple[float, float]:
    delta = (end[0] - start[0], end[1] - start[1])
    norm = math.hypot(*delta)
    return (0.0, 0.0) if norm < 1e-9 else (delta[0] / norm, delta[1] / norm)


def _first_standable_along(
    grid: GridSpec,
    columns: np.ndarray,
    point_m,
    direction,
    *,
    search_m: float,
) -> tuple[int, int] | None:
    """First standable cell from ``point_m`` walking along ``direction``.

    The flight's last authored tread stops at the edge of the slab opening, and
    a body of 0.25 m radius cannot stand within a radius of that edge.  So the
    arrival is not the geometric end of the flight: it is the first cell beyond
    it that the upper storey can actually support.  The same reasoning gives
    the departure on the lower storey, walking backwards off the first tread.
    """

    voxel = grid.fine_voxel_size_m
    steps = int(math.ceil(search_m / voxel))
    for step in range(steps + 1):
        probe = (
            point_m[0] + direction[0] * step * voxel,
            point_m[1] + direction[1] * step * voxel,
        )
        cell = _cell_of(grid, probe)
        if _inside(columns, cell):
            return cell
    return None


def _gap_m(grid: GridSpec, point_m, cell: tuple[int, int]) -> float:
    """Distance from a world point to the centre of a grid cell, in XY."""

    voxel = grid.fine_voxel_size_m
    centre = (
        grid.scene_bounds_min_m[0] + (cell[0] + 0.5) * voxel,
        grid.scene_bounds_min_m[1] + (cell[1] + 0.5) * voxel,
    )
    return float(math.hypot(centre[0] - point_m[0], centre[1] - point_m[1]))


def _cell_of(grid: GridSpec, point_m) -> tuple[int, int]:
    voxel = grid.fine_voxel_size_m
    x = int(np.floor((point_m[0] - grid.scene_bounds_min_m[0]) / voxel))
    y = int(np.floor((point_m[1] - grid.scene_bounds_min_m[1]) / voxel))
    return (x, y)


def _inside(columns: np.ndarray, cell: tuple[int, int]) -> bool:
    x, y = cell
    if not (0 <= x < columns.shape[0] and 0 <= y < columns.shape[1]):
        return False
    return bool(columns[x, y])


def _reach_area(
    volume: StandableVolume,
    storey: Storey,
    cell: tuple[int, int],
    config: StandabilityV1,
    voxel: float,
) -> float:
    """Area of the storey a body starting at ``cell`` on that storey can reach."""

    column = storey.cells[cell[0], cell[1]]
    if not column.any():
        return 0.0
    seed = np.zeros(volume.mask.shape, dtype=bool)
    seed[cell[0], cell[1], int(np.argmax(column))] = True
    reached = walk_reachable(volume.mask, seed, config, voxel)
    return float((reached & storey.cells).sum()) * voxel * voxel
