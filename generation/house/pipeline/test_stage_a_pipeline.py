#!/usr/bin/env python3
"""Fail-capable negative controls for the building inventory pipeline."""

from pair_scenes import (apply_type_overrides, classify_assets,
                         enforce_cross_room_votes, floor2_overhang, type_compatible)
from pick_placement import (BODY_RADIUS, CORRIDOR, corridor_obstacle_hits,
                            corridor_poly, forward_clearance, rect_array,
                            rect_intersects_any,
                            rotate_point, rotated_stair, stair_clip_hits,
                            transform_triangles, walk_clearance_shapes)
from prepare_manifest import validate
from pipeline_contract import (BODY_RADIUS_M, DYNAMIC_DROP_PROBE_RADIUS_M,
                               vertical_ccd_drop_pass)
import numpy as np
from shapely.geometry import box


def main():
    assert DYNAMIC_DROP_PROBE_RADIUS_M == 0.08
    assert DYNAMIC_DROP_PROBE_RADIUS_M < BODY_RADIUS_M
    assert vertical_ccd_drop_pass(
        configuration_verified=True, support_present=True,
        end_z=2.500, expected_z=2.500,
        horizontal_drift_m=0.0, late_pose_delta_m=0.0)
    assert not vertical_ccd_drop_pass(
        configuration_verified=True, support_present=False,
        end_z=0.875, expected_z=2.500,
        horizontal_drift_m=0.0, late_pose_delta_m=0.0)
    assert not vertical_ccd_drop_pass(
        configuration_verified=True, support_present=True,
        end_z=2.460, expected_z=2.500,
        horizontal_drift_m=0.0, late_pose_delta_m=0.0)
    assert not vertical_ccd_drop_pass(
        configuration_verified=True, support_present=True,
        end_z=2.500, expected_z=2.500,
        horizontal_drift_m=0.0, late_pose_delta_m=0.006)
    home = classify_assets(["Sofa_1", "Bed_2", "Fridge_3"])
    school = classify_assets(["Desk_1", "Whiteboard_2", "Projector_3"])
    too_few = classify_assets(["Sofa_1", "Bed_2"])
    tied = classify_assets(["Sofa_1", "Bed_2", "Fridge_3",
                            "Desk_1", "Whiteboard_2", "Projector_3"])
    assert home["type"] == "home", home
    assert school["type"] == "school", school
    assert too_few["type"] == "unknown", too_few
    assert tied["type"] == "unknown", tied
    concentrated = dict(home)
    enforce_cross_room_votes(concentrated, {"room_a": home})
    assert concentrated["type"] == "unknown"
    assert not concentrated["cross_room_vote_pass"]
    one_plus_unassigned = dict(home)
    enforce_cross_room_votes(one_plus_unassigned, {"room_a": home, "unassigned": school})
    assert one_plus_unassigned["type"] == "unknown"
    distributed = dict(home)
    enforce_cross_room_votes(distributed, {"room_a": home, "room_b": school})
    assert distributed["type"] == "home"
    assert distributed["cross_room_vote_pass"]

    # Known-bad mixed and unknown pairs must go red at the first gate.
    assert type_compatible(home, home)
    assert not type_compatible(home, school)
    assert not type_compatible(home, too_few)
    override_records = {"mixed": dict(home)}
    apply_type_overrides(override_records, {
        "mixed": {"type": "unknown", "reason": "room-level mixed evidence"}})
    assert override_records["mixed"]["automatic_type"] == "home"
    assert override_records["mixed"]["type"] == "unknown"
    overhang_area, overhang_ratio = floor2_overhang(
        {"room_union": box(0, 0, 10, 10)},
        {"room_union": box(0, 0, 12.5, 10)})
    assert abs(overhang_area - 25.0) < 1e-9
    assert abs(overhang_ratio - 0.20) < 1e-9

    assert rotate_point((2, 3), 0) == (2, 3)
    assert rotate_point((2, 3), 90) == (-3, 2)
    assert rotate_point((2, 3), 180) == (-2, -3)
    assert rotate_point((2, 3), 270) == (3, -2)
    try:
        rotate_point((2, 3), 45)
    except ValueError:
        pass
    else:
        raise AssertionError("a non-quarter-turn rotation was incorrectly accepted")

    stair = {
        "min": [-1.0, -2.0], "size": [3.0, 5.0],
        "entry": [0.0, -2.0], "entry_next": [0.0, -1.0],
        "exit_prev": [1.0, 2.0], "exit": [1.0, 3.0],
    }
    assert rotated_stair(stair, 0)["size"] == [3.0, 5.0]
    assert rotated_stair(stair, 90)["size"] == [5.0, 3.0]

    # A high-mounted object must still veto a stair even though it is not a
    # floor-navigation obstacle.  The distant control must remain clear.
    high_stair = np.asarray([[[0.0, 0.0, 2.8],
                              [1.0, 0.0, 2.8],
                              [0.0, 1.0, 2.8]]])
    high_scene = {"wall_boxes": [], "obstacle_boxes": [
        {"prim": "/high_fixture", "box": [0.1, 0.1, 2.7, 0.3, 0.3, 2.9]}]}
    clear_scene = {"wall_boxes": [], "obstacle_boxes": [
        {"prim": "/distant_fixture", "box": [3.0, 3.0, 2.7, 3.3, 3.3, 2.9]}]}
    assert stair_clip_hits(high_stair, high_scene)
    assert not stair_clip_hits(high_stair, clear_scene)
    rotated = transform_triangles(high_stair, 180, (2.0, 3.0))
    assert np.allclose(rotated[0, 0], [2.0, 3.0, 2.8])
    reserved = corridor_poly((0.0, 0.0), (0.0, 1.0),
                             CORRIDOR + BODY_RADIUS, 0.45)
    assert abs(reserved.bounds[3] - 1.42) < 1e-9
    # This wall is 1.298 m from the landing: a point-only 1.2 m corridor
    # clears it, but the body-radius-aware traversal reservation must fail.
    end_wall = box(-1.0, 1.298, 1.0, 1.31)
    assert not corridor_poly((0.0, 0.0), (0.0, 1.0),
                             CORRIDOR, 0.45).intersects(end_wall)
    assert reserved.intersects(end_wall)
    # Visual-only furniture must veto the walk corridor even when it was
    # omitted from the legacy 2-D navigation obstacle list.
    visual_only = {"obstacles": [], "obstacle_boxes": [{
        "prim": "/visual_only_chair",
        "box": [-0.2, 1.25, 0.0, 0.2, 1.55, 1.0],
    }]}
    assert corridor_obstacle_hits(reserved, visual_only, 1.75)
    overhead = {"obstacles": [], "obstacle_boxes": [{
        "prim": "/overhead_light",
        "box": [-0.2, 1.25, 2.2, 0.2, 1.55, 2.5],
    }]}
    assert not corridor_obstacle_hits(reserved, overhead, 1.75)
    rank_scene = {
        "walls": [(0.0, 3.0, 2.0, 3.1)],
        "obstacle_boxes": [],
        "rooms": [{"rect": (-1.0, -1.0, 3.0, 4.0)}],
        "room_union": box(-1.0, -1.0, 3.0, 4.0),
    }
    assert abs(forward_clearance(
        (1.0, 0.0), (0.0, 1.0), rank_scene,
        walk_clearance_shapes(rank_scene)) - 3.0) < 1e-9
    controls = rect_array([(2.0, 2.0, 3.0, 3.0), (4.0, 4.0, 5.0, 5.0)])
    assert rect_intersects_any((2.5, 2.5, 2.6, 2.6), controls)
    assert not rect_intersects_any((6.0, 6.0, 7.0, 7.0), controls)

    placement = {
        "floor1": "f1", "floor2": "f2", "scene_type": "home",
        "height_error_m": 0.0, "approach_clear": True, "egress_clear": True,
        "rotation_deg": 90, "floor2_overhang_ratio": 0.11,
        "precision_clip_gate_pass": True,
        "placement_contract_version": 2,
        "corridor_obstacle_source": "all_obstacle_boxes_walk_prism",
        "floor1_cross_room_vote_pass": True,
        "floor2_cross_room_vote_pass": True,
    }
    validate(placement, {"f1": home, "f2": home})
    try:
        validate(placement, {"f1": home, "f2": school})
    except ValueError:
        pass
    else:
        raise AssertionError("manifest accepted a known-bad mixed-type pair")
    bad_height = dict(placement, height_error_m=0.0011)
    try:
        validate(bad_height, {"f1": home, "f2": home})
    except ValueError:
        pass
    else:
        raise AssertionError("manifest accepted a stair height error above 1 mm")
    bad_overhang = dict(placement, floor2_overhang_ratio=0.1201)
    try:
        validate(bad_overhang, {"f1": home, "f2": home})
    except ValueError:
        pass
    else:
        raise AssertionError("manifest accepted Floor-2 overhang above 12%")
    bad_room_span = dict(placement, floor2_cross_room_vote_pass=False)
    try:
        validate(bad_room_span, {"f1": home, "f2": home})
    except ValueError:
        pass
    else:
        raise AssertionError("manifest accepted single-room type evidence")
    bad_clip = dict(placement, precision_clip_gate_pass=False)
    try:
        validate(bad_clip, {"f1": home, "f2": home})
    except ValueError:
        pass
    else:
        raise AssertionError("manifest accepted a failed precision clipping gate")
    stale_corridor = dict(placement)
    stale_corridor.pop("placement_contract_version")
    try:
        validate(stale_corridor, {"f1": home, "f2": home})
    except ValueError:
        pass
    else:
        raise AssertionError("manifest accepted a stale corridor audit")
    print("PASS: typing, cross-room/overhang/height/clip negative gates, and rotations")


if __name__ == "__main__":
    main()
