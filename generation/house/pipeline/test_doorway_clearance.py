#!/usr/bin/env python3
"""Fail-capable fixtures for the A4 collision-raster doorway rule."""
import numpy as np

from doorway_clearance import (
    evaluate_rasterized_centerline,
    evaluate_scene_records,
)


def test_furniture_blocked_doorway_is_rejected() -> None:
    # Legacy A4 checked only the proposed stair footprint and its two tails.
    # This furniture is elsewhere in the source scene, so that contract would
    # accept the candidate even though the doorway retains only 0.55 m.
    legacy_a4_footprint_and_tail_clear = True
    doorways = [{
        "prim": "/World/Geometry/doorway_fixture_1_0_0",
        "frame_bounds_m": [0.0, -0.05, 0.0, 0.90, 0.05, 2.10],
        "width_axis": 0,
        "centre_depth_m": 0.0,
        "aperture_interval_m": [0.0, 0.90],
        "aperture_source": "fixture",
    }]
    furniture = [{
        "prim": "/World/Geometry/chair_fixture_1_0_0",
        "bounds_m": [0.55, 0.20, 0.0, 0.90, 0.80, 1.00],
    }]
    result = evaluate_scene_records(doorways, furniture)
    assert legacy_a4_footprint_and_tail_clear is True
    assert result["pass"] is False
    assert result["doorways"][0]["minimum_clear_opening_m"] == 0.55
    assert result["doorways"][0]["furniture_blockers"] == [
        "/World/Geometry/chair_fixture_1_0_0"]


def test_hung_closed_door_is_rejected_by_collision_raster() -> None:
    doorway = {
        "prim": "/World/Geometry/doorway_fixture/Geometry/Doorway_1",
        "frame_bounds_m": [0.0, -0.05, 0.0, 1.0, 0.05, 2.1],
        "width_axis": 0,
        "centre_depth_m": 0.0,
        "aperture_interval_m": [0.0, 1.0],
        "aperture_source": "fixture_1m_aperture",
    }
    # The nominal 1.0 m aperture passes the obsolete rule.  A closed 0.90 m
    # leaf fills 18 of its 20 collision cells and leaves only 0.10 m clear.
    nominal_aperture_only_pass = True
    occupied = np.zeros(20, dtype=np.bool_)
    occupied[:18] = True
    leaf = "/World/Geometry/doorway_fixture/Geometry/Doorway_1_door_1"
    blockers = [{leaf} if index < 18 else set() for index in range(20)]
    closed = evaluate_rasterized_centerline(doorway, occupied, blockers)
    opened = evaluate_rasterized_centerline(
        doorway, np.zeros(20, dtype=np.bool_), [set() for _ in range(20)])
    assert nominal_aperture_only_pass is True
    assert closed["pass"] is False
    assert closed["minimum_clear_opening_m"] == 0.10
    assert closed["collision_blockers"] == [leaf]
    assert opened["pass"] is True
    assert opened["minimum_clear_opening_m"] == 1.0


if __name__ == "__main__":
    test_furniture_blocked_doorway_is_rejected()
    test_hung_closed_door_is_rejected_by_collision_raster()
    print("PASS: 1.0 m nominal aperture passes the obsolete rule; a hung 0.90 m "
          "closed collider leaf leaves 0.10 m and the raster gate returns RED")
