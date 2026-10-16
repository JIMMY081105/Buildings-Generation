#!/usr/bin/env python3
"""Shared hard thresholds for placement, CPU build, and Isaac validation."""

import hashlib
import json

CORRIDOR_M = 1.2
BODY_RADIUS_M = 0.22
BODY_HEIGHT_M = 1.75
CORRIDOR_HALF_WIDTH_M = 0.45
FLOOR2_OVERHANG_MAX = 0.12
HEIGHT_TOLERANCE_M = 0.001
DYNAMIC_DROP_HEIGHT_M = 0.25
DYNAMIC_DROP_PROBE_RADIUS_M = 0.08
DYNAMIC_FINAL_HEIGHT_TOLERANCE_M = 0.03
DYNAMIC_LATE_SETTLE_TOLERANCE_M = 0.005

# Every building uses these same formulas in a stair-local frame. H is the
# measured floor-to-floor height; "arrival" and "path" are semantic staircase
# anchors produced by the same traversal-path contract for every shape.
CAMERA_POSE_CONTRACT_VERSION = 1
CAMERA_POSE_CONTRACT = {
    "version": CAMERA_POSE_CONTRACT_VERSION,
    "frame": "stair_local_xy_rotated_by_placement_rotation_world_z_up",
    "views": {
        "01_overview": {
            "eye": [11.0, -11.0, "H+9.0"],
            "target": [0.0, 0.0, "H*0.5"],
        },
        "02_stairwell": {
            "eye": [7.5, -1.0, "H*0.62"],
            "target": [0.0, 0.0, "H*0.45"],
        },
        "03_arrival": {
            "eye": "arrival_path_point+exit_direction*0.12,z=H+1.62",
            "target": "eye+exit_direction*2.0,z=H+1.48",
        },
        "04_eyelevel": {
            "eye": "path_start_xy,z=1.62",
            "target": "path_one_third_xy,z=1.9",
        },
        "05_topdown": {
            "eye": [0.0, 0.0, "H+16.0"],
            "target": [0.0, 0.0, "H*0.5"],
        },
    },
    "render_collision_same_pose_required": True,
}
CAMERA_POSE_CONTRACT_SHA256 = hashlib.sha256(
    json.dumps(
        CAMERA_POSE_CONTRACT, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
).hexdigest()


def vertical_ccd_drop_pass(*, configuration_verified, support_present,
                           end_z, expected_z, horizontal_drift_m,
                           late_pose_delta_m):
    """Fail-capable vertical landing gate shared by validator and tests."""
    return bool(
        configuration_verified
        and support_present
        and abs(float(end_z) - float(expected_z))
        <= DYNAMIC_FINAL_HEIGHT_TOLERANCE_M
        and float(horizontal_drift_m) <= DYNAMIC_LATE_SETTLE_TOLERANCE_M
        and float(late_pose_delta_m) <= DYNAMIC_LATE_SETTLE_TOLERANCE_M
    )


def corridor_polygon(start, direction):
    """Reserved walk rectangle: 1.2 m centre travel plus capsule end cap."""
    from shapely.geometry import Polygon
    px, py = float(start[0]), float(start[1])
    dx, dy = float(direction[0]), float(direction[1])
    length = CORRIDOR_M + BODY_RADIUS_M
    nx, ny = -dy, dx
    a = (px + nx * CORRIDOR_HALF_WIDTH_M,
         py + ny * CORRIDOR_HALF_WIDTH_M)
    b = (px - nx * CORRIDOR_HALF_WIDTH_M,
         py - ny * CORRIDOR_HALF_WIDTH_M)
    c = (px + dx * length - nx * CORRIDOR_HALF_WIDTH_M,
         py + dy * length - ny * CORRIDOR_HALF_WIDTH_M)
    d = (px + dx * length + nx * CORRIDOR_HALF_WIDTH_M,
         py + dy * length + ny * CORRIDOR_HALF_WIDTH_M)
    return Polygon([a, d, c, b])


def walk_prism_obstacle_hits(shape, scene):
    """Furniture AABBs intersecting XY corridor and z=[0, body height]."""
    sx0, sy0, sx1, sy1 = shape.bounds
    hits = []
    for record in scene.get("obstacle_boxes", []):
        raw = record["box"]
        if float(raw[5]) <= 0.0 or float(raw[2]) >= BODY_HEIGHT_M:
            continue
        if not (sx1 < raw[0] or raw[3] < sx0
                or sy1 < raw[1] or raw[4] < sy0):
            hits.append(record)
    return hits
