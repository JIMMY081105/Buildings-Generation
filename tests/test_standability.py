"""The body proxy, and the erosion that must not be optional.

The regression test that matters is ``test_narrow_slot_is_not_walkable``: it
encodes the defect that once passed 50 buildings out of 50 and, once fixed,
passed none of them.
"""

from __future__ import annotations

import numpy as np
import pytest

from buildingsgen.voxel.grid import make_aligned_grid
from buildingsgen.voxel.standability import (
    PinnedSemanticsError,
    StandabilityV1,
    component_areas_m2,
    derive_standable,
    label_components,
    proxy_footprint,
    walk_reachable,
)

VOXEL = 0.05


def _empty_room(size_m=(6.0, 6.0, 3.0)):
    """A box room: floor at z=0, walls around it, open above."""

    grid = make_aligned_grid((0.0, 0.0, 0.0), size_m)
    occupancy = np.zeros(grid.fine_shape, dtype=bool)
    occupancy[:, :, 0] = True          # floor
    occupancy[0, :, :] = True          # walls
    occupancy[-1, :, :] = True
    occupancy[:, 0, :] = True
    occupancy[:, -1, :] = True
    return grid, occupancy


def test_pinned_values_cannot_be_moved():
    StandabilityV1().validate()
    with pytest.raises(PinnedSemanticsError):
        StandabilityV1(body_radius_m=0.20).validate()
    with pytest.raises(PinnedSemanticsError):
        StandabilityV1(max_support_step_m=0.30).validate()
    with pytest.raises(PinnedSemanticsError):
        StandabilityV1().validate(voxel_size_m=0.10)


def test_footprint_is_a_closed_disk_against_closed_squares():
    footprint = proxy_footprint(0.25, VOXEL)
    # ceil(radius / voxel + 0.5) offsets each way: the half-voxel accounts for
    # the closed square, so the stamp is 13 wide, not 11.
    assert footprint.shape == (13, 13)
    assert footprint[6, 6]
    # A cell whose nearest corner is beyond the radius is outside the stamp.
    assert not footprint[0, 0]
    assert footprint.sum() < footprint.size


def test_open_floor_is_standable_and_eroded_at_the_walls():
    grid, occupancy = _empty_room()
    volume = derive_standable(occupancy, grid)
    # The 0.25 m radius must cost a band around the whole perimeter.
    assert volume.area_m2 > 0.0
    interior = (6.0 - 2 * VOXEL) ** 2
    assert volume.area_m2 < interior
    assert volume.area_m2 > 0.5 * interior


def test_narrow_slot_is_not_walkable():
    """A 0.30 m slot is passable by a point and not by a 0.50 m body.

    This is the regression: with the radius used only to pick seeds, the flood
    crossed the slot and the building reported one connected region.
    """

    grid, occupancy = _empty_room()
    mid = grid.fine_shape[1] // 2
    slot_low = grid.fine_shape[0] // 2 - 3     # 0.30 m of opening
    slot_high = grid.fine_shape[0] // 2 + 3
    occupancy[:slot_low, mid, :] = True
    occupancy[slot_high:, mid, :] = True

    volume = derive_standable(occupancy, grid)
    areas = component_areas_m2(volume.mask, volume.config, VOXEL)
    assert len(areas) >= 2, "the partition must split the room in two"

    labels, _ = label_components(volume.mask, volume.config, VOXEL)
    south = labels[:, :mid, :]
    north = labels[:, mid + 1:, :]
    south_ids = {int(v) for v in np.unique(south) if v >= 0}
    north_ids = {int(v) for v in np.unique(north) if v >= 0}
    assert south_ids and north_ids
    assert not (south_ids & north_ids), "a 0.50 m body crossed a 0.30 m slot"


def test_wide_doorway_is_walkable():
    """The same partition with a 0.90 m opening must stay connected.

    Without this, the previous test would also pass on a check that simply
    refuses everything.
    """

    grid, occupancy = _empty_room()
    mid = grid.fine_shape[1] // 2
    slot_low = grid.fine_shape[0] // 2 - 9     # 0.90 m of opening
    slot_high = grid.fine_shape[0] // 2 + 9
    occupancy[:slot_low, mid, :] = True
    occupancy[slot_high:, mid, :] = True

    volume = derive_standable(occupancy, grid)
    labels, _ = label_components(volume.mask, volume.config, VOXEL)
    south_ids = {int(v) for v in np.unique(labels[:, :mid, :]) if v >= 0}
    north_ids = {int(v) for v in np.unique(labels[:, mid + 1:, :]) if v >= 0}
    assert south_ids & north_ids, "a 0.90 m doorway must remain passable"


def test_small_islands_are_dropped():
    grid, occupancy = _empty_room()
    # A 0.40 m x 0.40 m pedestal, 0.40 m tall: 0.16 m^2 of top surface, below
    # the 1.00 m^2 minimum, and too tall to be a step up from the floor - so it
    # is an island, not part of the floor's component.
    occupancy[30:38, 30:38, 1:9] = True
    volume = derive_standable(occupancy, grid)
    surface = volume.surface_z_m()
    pedestal_rows = (surface > 0.30) & (surface < 0.55)
    assert not volume.mask[:, :, pedestal_rows].any()

    kept = derive_standable(occupancy, grid, filter_small_components=False)
    assert kept.mask[:, :, pedestal_rows].any(), (
        "without the filter the pedestal must be standable, or this test is "
        "passing for the wrong reason"
    )


def test_clearance_mode_changes_a_stair_and_nothing_else():
    """A flight is unmeasurable under the rigid-cylinder proxy, by construction."""

    grid = make_aligned_grid((0.0, 0.0, 0.0), (4.0, 6.0, 4.0))
    occupancy = np.zeros(grid.fine_shape, dtype=bool)
    occupancy[:, :, 0] = True
    riser_cells = 3          # 0.15 m, inside the 0.20 m support step
    going_cells = 6          # 0.30 m
    for step in range(1, 12):
        y0 = 20 + step * going_cells
        occupancy[20:60, y0: y0 + going_cells, : 1 + step * riser_cells] = True

    following = derive_standable(occupancy, grid, clearance_mode="terrain_following")
    fixed = derive_standable(occupancy, grid, clearance_mode="fixed_base")
    tread_rows = (following.surface_z_m() > 0.10) & (following.surface_z_m() < 1.60)
    assert following.mask[:, :, tread_rows].sum() > 0
    assert fixed.mask[:, :, tread_rows].sum() < following.mask[:, :, tread_rows].sum()


def test_walk_reachable_respects_the_support_step():
    grid = make_aligned_grid((0.0, 0.0, 0.0), (4.0, 4.0, 4.0))
    occupancy = np.zeros(grid.fine_shape, dtype=bool)
    occupancy[:, :, 0] = True
    # A 0.30 m ledge: above the 0.20 m support step, so unreachable on foot.
    occupancy[40:70, :, :6] = True
    volume = derive_standable(occupancy, grid, filter_small_components=False)
    seed = np.zeros(volume.mask.shape, dtype=bool)
    ground = np.argwhere(volume.mask[:20])
    seed[tuple(ground[0])] = True
    reached = walk_reachable(volume.mask, seed, volume.config, VOXEL)
    assert not reached[40:70, :, 5:].any(), "a 0.30 m step must not be walkable"
