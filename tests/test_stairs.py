"""Stair design and the asset it produces, across building types."""

from __future__ import annotations

import numpy as np
import pytest
from pxr import Usd, UsdGeom, UsdPhysics

from buildingsgen.config import STAIR_FAMILIES, available_profiles, load_profile
from buildingsgen.stairs.asset import build_stair_usd, local_bounds_m, tread_boxes
from buildingsgen.stairs.spec import StairDesignError, design_stair, traversal_path
from buildingsgen.voxel.standability import StandabilityV1

PINNED = StandabilityV1()


@pytest.mark.parametrize("building_type", sorted(available_profiles()))
@pytest.mark.parametrize("rise", [2.8, 3.0, 3.4, 4.0])
def test_every_type_designs_a_climbable_flight(building_type, rise):
    profile = load_profile(building_type)
    if not (profile.storeys.min_storey_height_m <= rise <= profile.storeys.max_storey_height_m):
        pytest.skip(f"{rise} m is outside the {building_type} storey range")
    spec = design_stair(rise, profile.stair)
    # The single hard constraint: a riser above the pinned support step cannot
    # be climbed by the body proxy, whatever the building type says.
    assert spec.riser_m <= PINNED.max_support_step_m + 1e-9
    assert profile.stair.min_riser_m - 1e-9 <= spec.riser_m <= profile.stair.max_riser_m + 1e-9
    assert profile.stair.min_going_m - 1e-9 <= spec.going_m <= profile.stair.max_going_m + 1e-9
    assert spec.riser_count * spec.riser_m == pytest.approx(rise)


def test_the_upper_floor_supplies_the_last_tread():
    spec = design_stair(3.0, load_profile("house").stair)
    assert spec.authored_tread_count == spec.riser_count - 1
    boxes = tread_boxes(spec)
    assert len(boxes) == spec.riser_count - 1


def test_tread_tops_sit_on_the_riser_grid():
    """Tread *tops*, not undersides, are the walking surfaces.

    Placing the underside on the grid leaks the plate thickness into the first
    rise - the defect that made four buildings unclimbable while every render
    looked correct.
    """

    spec = design_stair(3.0, load_profile("house").stair)
    thickness = 0.06
    for index, box in enumerate(tread_boxes(spec, thickness_m=thickness)):
        top = box.center[2] + 0.5 * box.extent[2]
        assert top == pytest.approx((index + 1) * spec.riser_m)


def test_every_rise_including_the_first_and_last_is_inside_the_step_limit():
    for family in STAIR_FAMILIES:
        spec = design_stair(3.0, load_profile("generic").stair, family=family)
        rises = spec.expected_rises_m()
        assert len(rises) == spec.riser_count
        assert max(rises) <= PINNED.max_support_step_m + 1e-9


def test_traversal_path_ends_on_the_upper_landing():
    spec = design_stair(3.0, load_profile("house").stair)
    path = traversal_path(spec)
    assert path[0] == (0.0, 0.0, 0.0)
    assert path[-1][2] == pytest.approx(spec.total_rise_m)
    assert len(path) == spec.riser_count + 1


@pytest.mark.parametrize("family", STAIR_FAMILIES)
def test_turning_families_change_direction(family):
    spec = design_stair(3.0, load_profile("generic").stair, family=family)
    path = np.asarray(traversal_path(spec), dtype=float)
    spread_x = path[:, 0].ptp()
    if family == "straight":
        assert spread_x == pytest.approx(0.0)
        assert spec.turn_after_riser is None
    else:
        assert spread_x > 0.5, "an L- or U-shaped flight must move in X"
        assert spec.turn_after_riser is not None


def test_u_shaped_flights_use_an_even_riser_count():
    spec = design_stair(3.0, load_profile("generic").stair, family="u_shaped")
    assert spec.riser_count % 2 == 0
    with pytest.raises(StairDesignError, match="even"):
        design_stair(3.0, load_profile("generic").stair, family="u_shaped", riser_count=17)


def test_a_family_the_profile_disables_is_refused():
    factory = load_profile("factory").stair
    assert "l_shaped" not in factory.families
    with pytest.raises(StairDesignError, match="not enabled"):
        design_stair(3.5, factory, family="l_shaped")


def test_an_out_of_range_override_raises_instead_of_producing_junk():
    design = load_profile("house").stair
    with pytest.raises(StairDesignError, match="going"):
        design_stair(3.0, design, going_m=0.50)
    with pytest.raises(StairDesignError, match="width"):
        design_stair(3.0, design, width_m=0.10)
    with pytest.raises(StairDesignError, match="riser"):
        design_stair(3.0, design, riser_count=4)


@pytest.mark.parametrize("family", STAIR_FAMILIES)
def test_asset_authors_exact_colliders_and_a_traversal_path(tmp_path, family):
    spec = design_stair(3.0, load_profile("generic").stair, family=family)
    destination = tmp_path / family / "stair.usda"
    record = build_stair_usd(spec, destination)
    assert record["authored_tread_count"] == spec.riser_count - 1
    assert (destination.parent / "traversal_path.json").is_file()

    stage = Usd.Stage.Open(str(destination))
    assert str(UsdGeom.GetStageUpAxis(stage)) == "Z"
    assert UsdGeom.GetStageMetersPerUnit(stage) == pytest.approx(1.0)
    meshes = [prim for prim in stage.Traverse() if prim.IsA(UsdGeom.Mesh)]
    assert meshes
    for prim in meshes:
        assert prim.HasAPI(UsdPhysics.CollisionAPI)
        approximation = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get()
        assert str(approximation) == "none", "a cooked hull turns a stair into a ramp"


def test_handrails_are_marked_as_obstacles_not_support(tmp_path):
    spec = design_stair(3.0, load_profile("school").stair)  # school uses double rails
    record = build_stair_usd(spec, tmp_path / "stair.usda")
    assert record["prims"]["handrails"], "this profile asks for handrails"
    stage = Usd.Stage.Open(record["stage"])
    scope = stage.GetPrimAtPath("/Stair/Handrail")
    assert scope.GetCustomDataByKey("navigationRole") == (
        "physical_obstacle_not_walking_support"
    )


def test_local_bounds_cover_the_authored_geometry(tmp_path):
    spec = design_stair(3.0, load_profile("generic").stair, family="u_shaped")
    lower, upper = local_bounds_m(spec)
    record = build_stair_usd(spec, tmp_path / "stair.usda")
    stage = Usd.Stage.Open(record["stage"])
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    points = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        raw = np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get(), dtype=float)
        matrix = np.asarray(cache.GetLocalToWorldTransform(prim), dtype=float)
        homogeneous = np.concatenate([raw, np.ones((raw.shape[0], 1))], axis=1)
        points.append((homogeneous @ matrix)[:, :3])
    world = np.concatenate(points, axis=0)
    assert np.all(world.min(axis=0) >= lower - 1e-6)
    assert np.all(world.max(axis=0) <= upper + 1e-6)
