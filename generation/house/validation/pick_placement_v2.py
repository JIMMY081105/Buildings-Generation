#!/usr/bin/env python3
"""Choose a placement that respects the stair's ENTRY and EXIT directions.

The footprint-only search put a U-stair with its exit facing an exterior wall:
you reach the top, walk into `wall_0_6`, and the next steps are outside the
building with nothing under them. Fitting the bounding box is not enough --
both ends need somewhere to walk.

Adds, per candidate:
  * Floor 1 approach corridor behind the entry, clear of furniture
  * Floor 2 egress corridor ahead of the exit, inside a room, off the walls
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from pxr import Usd, UsdGeom
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from pair_scenes import scan  # noqa: E402
from build_building import tri_box_overlap, world_tris  # noqa: E402
from pipeline_contract import (BODY_HEIGHT_M, BODY_RADIUS_M, CORRIDOR_HALF_WIDTH_M,
                               CORRIDOR_M, walk_prism_obstacle_hits)  # noqa: E402

CORRIDOR = CORRIDOR_M
HALF_W = CORRIDOR_HALF_WIDTH_M
BODY_RADIUS = BODY_RADIUS_M
MARGIN = 0.25


def stair_local(stair_usd: Path, path_json: Path):
    st = Usd.Stage.Open(str(stair_usd), load=Usd.Stage.LoadAll)
    c = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                          [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    r = c.ComputeWorldBound(st.GetPrimAtPath("/Asset")).ComputeAlignedRange()
    mn, mx = r.GetMin(), r.GetMax()
    wp = json.loads(path_json.read_text(encoding="utf-8"))["points_xyz_m"]
    triangles = []
    asset = st.GetPrimAtPath("/Asset")
    for prim in Usd.PrimRange(asset):
        if not prim.IsA(UsdGeom.Mesh) or not prim.IsActive():
            continue
        if UsdGeom.Imageable(prim).ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        values = world_tris(prim)
        if len(values):
            triangles.append(values)
    # The path starts on the first tread, not at floor z=0, so max-min drops
    # one riser.  Its final waypoint is authored exactly at the target floor.
    rise = max(float(p[2]) for p in wp)
    return {
        "min": [float(mn[0]), float(mn[1])],
        "size": [float(mx[0] - mn[0]), float(mx[1] - mn[1])],
        "height": rise,
        "visual_height": float(mx[2] - mn[2]),
        "triangles": (np.concatenate(triangles) if triangles
                      else np.zeros((0, 3, 3), dtype=float)),
        "entry": [wp[0][0], wp[0][1]], "entry_next": [wp[1][0], wp[1][1]],
        "exit": [wp[-1][0], wp[-1][1]], "exit_prev": [wp[-2][0], wp[-2][1]],
    }


def rotate_point(point, degrees):
    """Exact quarter-turn rotation about the stair asset's local origin."""
    x, y = float(point[0]), float(point[1])
    degrees %= 360
    if degrees == 0:
        return (x, y)
    if degrees == 90:
        return (-y, x)
    if degrees == 180:
        return (-x, -y)
    if degrees == 270:
        return (y, -x)
    raise ValueError("rotation must be one of 0, 90, 180, 270")


def rotated_stair(sl, degrees):
    x0, y0 = sl["min"]
    x1, y1 = x0 + sl["size"][0], y0 + sl["size"][1]
    corners = [rotate_point(p, degrees) for p in
               ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    rx0 = min(p[0] for p in corners); ry0 = min(p[1] for p in corners)
    rx1 = max(p[0] for p in corners); ry1 = max(p[1] for p in corners)
    return {
        "rotation_deg": degrees,
        "min": [rx0, ry0],
        "size": [rx1 - rx0, ry1 - ry0],
        "entry": rotate_point(sl["entry"], degrees),
        "entry_next": rotate_point(sl["entry_next"], degrees),
        "exit": rotate_point(sl["exit"], degrees),
        "exit_prev": rotate_point(sl["exit_prev"], degrees),
    }


def unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = (dx * dx + dy * dy) ** 0.5
    return (dx / n, dy / n) if n > 1e-9 else (0.0, 1.0)


def corridor_poly(p, d, length, half):
    """Rect from p extending `length` along d, `half` to each side."""
    nx, ny = -d[1], d[0]
    a = (p[0] + nx * half, p[1] + ny * half)
    b = (p[0] - nx * half, p[1] - ny * half)
    c = (p[0] + d[0] * length - nx * half, p[1] + d[1] * length - ny * half)
    e = (p[0] + d[0] * length + nx * half, p[1] + d[1] * length + ny * half)
    from shapely.geometry import Polygon
    return Polygon([a, e, c, b])


def corridor_obstacle_hits(shape, scene, max_height):
    """All furniture AABBs intersecting the walkable body prism.

    `scene["obstacles"]` is a legacy navigation projection that intentionally
    omits some visual-only furniture.  That is acceptable for cheap search
    pruning, but never as a final corridor gate: the brief requires furniture
    without colliders to count.  Use every audited 3-D obstacle box and limit
    it only by the actual body-height interval.
    """
    if abs(float(max_height) - BODY_HEIGHT_M) <= 1e-9:
        return walk_prism_obstacle_hits(shape, scene)
    hits = []
    sx0, sy0, sx1, sy1 = shape.bounds
    for record in scene.get("obstacle_boxes", []):
        raw = record["box"]
        if float(raw[5]) > 0.0 and float(raw[2]) < float(max_height) and not (
                sx1 < raw[0] or raw[3] < sx0 or sy1 < raw[1] or raw[4] < sy0):
            hits.append(record)
    return hits


def walk_clearance_shapes(scene):
    """Conservative 2-D shapes that matter to a standing person's clearance."""
    shapes = [box(*rect) for rect in scene.get("walls", [])]
    shapes.extend(
        box(raw[0], raw[1], raw[3], raw[4])
        for record in scene.get("obstacle_boxes", [])
        for raw in (record["box"],)
        if float(raw[5]) > 0.0 and float(raw[2]) < BODY_HEIGHT_M
    )
    return shapes


def containing_room_boundaries(scene, shape):
    rooms = [box(*record["rect"]) for record in scene.get("rooms", [])]
    boundaries = [room.boundary for room in rooms
                  if room.buffer(1e-6).contains(shape)]
    return boundaries or [scene["room_union"].boundary]


def forward_clearance(start, direction, scene, obstacle_shapes, max_ray_m=20.0):
    """Distance on the centre ray to the first wall, furniture, or room edge."""
    origin = Point(start)
    ray = LineString([
        start,
        (start[0] + direction[0] * max_ray_m,
         start[1] + direction[1] * max_ray_m),
    ])
    distances = []
    for shape in obstacle_shapes + containing_room_boundaries(scene, origin):
        hit = ray.intersection(shape)
        if hit.is_empty:
            continue
        distance = origin.distance(hit)
        if distance > 1e-6:
            distances.append(float(distance))
    return min(distances) if distances else float(max_ray_m)


def placement_rank_metrics(candidate, lower, upper, approach, egress,
                           lower_shapes, upper_shapes):
    """Soft ranking only; hard acceptance remains the unchanged A4 gates."""
    approach_shapes = lower_shapes + containing_room_boundaries(lower, approach)
    egress_shapes = upper_shapes + containing_room_boundaries(upper, egress)
    approach_extra = min((approach.distance(shape) for shape in approach_shapes),
                         default=0.0)
    egress_extra = min((egress.distance(shape) for shape in egress_shapes),
                       default=0.0)
    entry_out = (-candidate["entry_dir"][0], -candidate["entry_dir"][1])
    return {
        "approach_corridor_extra_clearance_m": float(approach_extra),
        "egress_corridor_extra_clearance_m": float(egress_extra),
        "minimum_corridor_extra_clearance_m": float(
            min(approach_extra, egress_extra)),
        "approach_forward_clearance_m": forward_clearance(
            candidate["entry_world"], entry_out, lower, lower_shapes),
        "egress_forward_clearance_m": forward_clearance(
            candidate["exit_world"], candidate["exit_dir"], upper, upper_shapes),
    }


def rect_array(rectangles):
    values = np.asarray(list(rectangles), dtype=float)
    return values.reshape((-1, 4)) if values.size else np.zeros((0, 4))


def rect_intersects_any(bounds, rectangles):
    """Fast inclusive AABB intersection for hot placement-search loops."""
    if len(rectangles) == 0:
        return False
    x0, y0, x1, y1 = bounds
    return bool(np.any((rectangles[:, 0] <= x1)
                       & (rectangles[:, 2] >= x0)
                       & (rectangles[:, 1] <= y1)
                       & (rectangles[:, 3] >= y0)))


def cached_scan(record: dict) -> dict:
    """Restore the transient Shapely fields omitted from the JSON A1 cache."""
    scene = dict(record["geometry"])
    rooms = [box(*row["rect"]) for row in scene["rooms"]]
    walls = [box(*row) for row in scene["walls"]]
    scene["room_union"] = unary_union(rooms)
    scene["wall_union"] = unary_union(walls) if walls else None
    if scene.get("doorway_clearance") is None:
        raise ValueError("A1 scene cache lacks doorway-clearance evidence")
    return scene


def transform_triangles(triangles, degrees, offset):
    """Apply the same quarter-turn + translation used by build_building.py."""
    values = np.array(triangles, dtype=float, copy=True)
    x, y = values[:, :, 0].copy(), values[:, :, 1].copy()
    if degrees == 90:
        values[:, :, 0], values[:, :, 1] = -y, x
    elif degrees == 180:
        values[:, :, 0], values[:, :, 1] = -x, -y
    elif degrees == 270:
        values[:, :, 0], values[:, :, 1] = y, -x
    elif degrees != 0:
        raise ValueError("rotation must be one of 0, 90, 180, 270")
    values[:, :, 0] += float(offset[0])
    values[:, :, 1] += float(offset[1])
    return values


def stair_clip_hits(triangles, scene, z_offset=0.0):
    """Mirror C2's exact stair-triangle versus scene-AABB gate in A4."""
    hits = []
    if not len(triangles):
        return [{"kind": "invalid_stair", "prim": "", "triangles_intersecting": 0}]
    stair_min = triangles.min(axis=(0, 1))
    stair_max = triangles.max(axis=(0, 1))
    for kind, records in (("wall", scene.get("wall_boxes", [])),
                          ("furniture", scene.get("obstacle_boxes", []))):
        for record in records:
            raw = np.asarray(record["box"], dtype=float)
            bmin = raw[:3].copy(); bmax = raw[3:].copy()
            bmin[2] += z_offset; bmax[2] += z_offset
            if np.any(bmin > stair_max) or np.any(bmax < stair_min):
                continue
            overlap = tri_box_overlap(triangles, bmin + 0.005, bmax - 0.005)
            if overlap.any():
                hits.append({
                    "kind": kind, "prim": record["prim"],
                    "triangles_intersecting": int(overlap.sum()),
                    "box": [round(float(v), 3) for v in list(bmin) + list(bmax)],
                })
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--scene-cache", type=Path,
                    help="A1 cache with doorway-clearance evidence")
    ap.add_argument("--stair", type=Path, required=True)
    ap.add_argument("--stair-path", type=Path, required=True)
    ap.add_argument("--kind", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-pairs", type=int, default=60)
    ap.add_argument("--max-candidates", type=int, default=60)
    ap.add_argument("--max-alternatives-per-pair", type=int, default=250)
    ap.add_argument("--height-tolerance", type=float, default=0.001,
                    help="maximum |scene floor-to-floor - stair rise| in metres")
    ap.add_argument("--rotations", default="0,90,180,270")
    ap.add_argument("--only-floor1", default=None)
    ap.add_argument("--only-floor2", default=None)
    ap.add_argument("--grid-step", type=float, default=0.25,
                    help="placement search spacing in metres")
    args = ap.parse_args()
    if not 0.0 < args.grid_step <= 0.5:
        raise SystemExit("--grid-step must be in (0, 0.5]")

    sl = stair_local(args.stair, args.stair_path)
    rotations = [int(v.strip()) % 360 for v in args.rotations.split(",") if v.strip()]
    if not rotations or any(v not in (0, 90, 180, 270) for v in rotations):
        raise SystemExit("--rotations must contain only 0,90,180,270")

    pairs_doc = json.loads(args.pairs.read_text(encoding="utf-8"))
    pairs = pairs_doc["results"]
    scene_cache_doc = (json.loads(args.scene_cache.read_text(encoding="utf-8"))
                       if args.scene_cache else None)
    if scene_cache_doc is not None:
        if scene_cache_doc.get("cache_contract_version") not in (2, 3):
            raise ValueError("A1 scene cache contract is stale")
        if scene_cache_doc.get("complete_for_pair_cache") is not True:
            raise ValueError("A1 scene cache is partial")
    seen, ordered = set(), []
    for r in pairs:
        if r.get("kind") != args.kind or not r.get("usable", False):
            continue
        if args.only_floor1 and r.get("floor1") != args.only_floor1:
            continue
        if args.only_floor2 and r.get("floor2") != args.only_floor2:
            continue
        if abs(float(r["floor1_f2f"]) - sl["height"]) > args.height_tolerance:
            continue
        key = (r["floor1"], r["floor2"])
        if key in seen:
            continue
        seen.add(key)
        ordered.append(r)
    ordered = ordered[:args.max_pairs]

    cache, found = {}, []
    doorway_clearance_rejections = []
    strict_placement_rejections = []
    precision_clip_rejections = []
    for r in ordered:
        for nm in (r["floor1"], r["floor2"]):
            if nm not in cache:
                cache[nm] = (cached_scan(scene_cache_doc["scenes"][nm])
                             if scene_cache_doc is not None else scan(nm))
        lo, up = cache[r["floor1"]], cache[r["floor2"]]
        if not lo or not up:
            continue
        floor_failures = {
            floor: scene["doorway_clearance"]
            for floor, scene in (("floor1", lo), ("floor2", up))
            if scene["doorway_clearance"]["pass"] is not True
        }
        if floor_failures:
            doorway_clearance_rejections.append({
                "candidate_name": "{}_{}_{}_{}_h{}".format(
                    r["scene_type"], r["floor1"].replace("_ceiling", "").replace("train_", "train"),
                    r["floor2"].replace("_ceiling", "").replace("train_", "train"), args.kind,
                    int(round(float(r["floor1_f2f"]) * 100))),
                "floor1": r["floor1"], "floor2": r["floor2"],
                "kind": args.kind, "height_m": r["floor1_f2f"],
                "failed_storeys": floor_failures,
                "reason": "A4_DOORWAY_CENTRELINE_CLEARANCE_BELOW_0.60M",
            })
            continue
        lo_obs = rect_array(lo["obstacles"])
        up_obs = rect_array(up["obstacles"])
        up_walls = up["wall_union"]
        lower_clearance_shapes = walk_clearance_shapes(lo)
        upper_clearance_shapes = walk_clearance_shapes(up)

        selected = None
        alternatives = []
        for rotation_deg in rotations:
            rs = rotated_stair(sl, rotation_deg)
            fx, fy = rs["size"]
            e_dir = unit(rs["entry"], rs["entry_next"])
            x_dir = unit(rs["exit_prev"], rs["exit"])
            for room in sorted(lo["rooms"], key=lambda q: -((q["rect"][2] - q["rect"][0]) * (q["rect"][3] - q["rect"][1]))):
                rx0, ry0, rx1, ry1 = room["rect"]
                if rx1 - rx0 < fx + 2 * MARGIN or ry1 - ry0 < fy + 2 * MARGIN:
                    continue
                step = args.grid_step
                i = 0
                while rx0 + MARGIN + i * step + fx <= rx1 - MARGIN:
                    j = 0
                    while ry0 + MARGIN + j * step + fy <= ry1 - MARGIN:
                        x0 = rx0 + MARGIN + i * step
                        y0 = ry0 + MARGIN + j * step
                        j += 1
                        fp = box(x0, y0, x0 + fx, y0 + fy)
                        if rect_intersects_any(fp.bounds, lo_obs):
                            continue
                        # The upper opening footprint itself must be usable too,
                        # not merely the egress tail beyond it.
                        if not up["room_union"].contains(fp):
                            continue
                        if up_walls is not None and fp.intersects(up_walls):
                            continue
                        if rect_intersects_any(fp.bounds, up_obs):
                            continue
                        off = (x0 - rs["min"][0], y0 - rs["min"][1])
                        entry_w = (rs["entry"][0] + off[0], rs["entry"][1] + off[1])
                        exit_w = (rs["exit"][0] + off[0], rs["exit"][1] + off[1])
                        # approach runs BACKWARD from the entry
                        # The traversal contract places a radius-0.22 m body
                        # centre at the 1.2 m endpoint.  Reserve the far-end
                        # cap too; otherwise a visually clear corridor can
                        # still fail the PhysX sweep against the end wall.
                        appr = corridor_poly(
                            entry_w, (-e_dir[0], -e_dir[1]),
                            CORRIDOR + BODY_RADIUS, HALF_W)
                        if not lo["room_union"].contains(appr):
                            continue
                        if lo["wall_union"] is not None and appr.intersects(lo["wall_union"]):
                            continue
                        if corridor_obstacle_hits(appr, lo, BODY_HEIGHT_M):
                            continue
                        # egress runs FORWARD from the exit, on floor 2
                        egr = corridor_poly(
                            exit_w, x_dir, CORRIDOR + BODY_RADIUS, HALF_W)
                        if not up["room_union"].contains(egr):
                            continue
                        if up_walls is not None and egr.intersects(up_walls):
                            continue
                        if corridor_obstacle_hits(egr, up, BODY_HEIGHT_M):
                            continue
                        # Do the expensive 3-D triangle/AABB gate only after
                        # all cheap footprint/corridor predicates pass.  This
                        # preserves the exact acceptance set while making fine
                        # grid searches practical.
                        stair_world = transform_triangles(
                            sl["triangles"], rotation_deg, off)
                        clip_hits = (stair_clip_hits(stair_world, lo)
                                     + stair_clip_hits(
                                         stair_world, up,
                                         z_offset=float(r["floor1_f2f"])))
                        if clip_hits:
                            if len(precision_clip_rejections) < 100:
                                precision_clip_rejections.append({
                                    "floor1": r["floor1"], "floor2": r["floor2"],
                                    "rotation_deg": rotation_deg,
                                    "rect": [x0, y0, x0 + fx, y0 + fy],
                                    "hits": clip_hits,
                                })
                            continue
                        candidate = {
                            "placement_contract_version": 3,
                            "corridor_obstacle_source": "all_obstacle_boxes_walk_prism",
                            "doorway_clearance_contract_version": 2,
                            "doorway_clearance_gate_pass": True,
                            "floor1_doorway_clearance": lo["doorway_clearance"],
                            "floor2_doorway_clearance": up["doorway_clearance"],
                            "floor1": r["floor1"], "floor2": r["floor2"],
                            "scene_type": r["scene_type"],
                            "floor1_type_votes": r["floor1_type_votes"],
                            "floor2_type_votes": r["floor2_type_votes"],
                            "floor1_type_bearing_rooms": r["floor1_type_bearing_rooms"],
                            "floor2_type_bearing_rooms": r["floor2_type_bearing_rooms"],
                            "floor1_cross_room_vote_pass": r["floor1_cross_room_vote_pass"],
                            "floor2_cross_room_vote_pass": r["floor2_cross_room_vote_pass"],
                            "kind": args.kind, "footprint_iou": r["footprint_iou"],
                            "floor2_overhang_area_m2": r["floor2_overhang_area_m2"],
                            "floor2_overhang_ratio": r["floor2_overhang_ratio"],
                            "floor2_overhang_pass": r["floor2_overhang_pass"],
                            "floor1_f2f": r["floor1_f2f"],
                            "stair_height_m": sl["height"],
                            "height_error_m": abs(float(r["floor1_f2f"]) - sl["height"]),
                            "room": room["prim"], "room_rect": list(room["rect"]),
                            "rect": [x0, y0, x0 + fx, y0 + fy],
                            "rotation_deg": rotation_deg,
                            "rotated_stair_min": rs["min"],
                            "stair_size": [fx, fy],
                            "entry_world": list(entry_w), "exit_world": list(exit_w),
                            "entry_dir": list(e_dir), "exit_dir": list(x_dir),
                            "approach_clear": True, "egress_clear": True,
                            "precision_clip_gate_pass": True,
                        }
                        alternative = {
                            key: candidate[key] for key in (
                                "room", "room_rect", "rect", "rotation_deg",
                                "rotated_stair_min", "stair_size",
                                "entry_world", "exit_world", "entry_dir", "exit_dir",
                                "approach_clear", "egress_clear",
                                "precision_clip_gate_pass")
                        }
                        alternative["rank_metrics"] = placement_rank_metrics(
                            alternative, lo, up, appr, egr,
                            lower_clearance_shapes, upper_clearance_shapes)
                        alternatives.append(alternative)
                        if selected is None:
                            selected = candidate
                        if len(alternatives) >= args.max_alternatives_per_pair:
                            break
                    if len(alternatives) >= args.max_alternatives_per_pair:
                        break
                    i += 1
                if len(alternatives) >= args.max_alternatives_per_pair:
                    break
            if len(alternatives) >= args.max_alternatives_per_pair:
                break
        if selected:
            alternatives.sort(key=lambda item: (
                item["rank_metrics"]["minimum_corridor_extra_clearance_m"],
                item["rank_metrics"]["egress_forward_clearance_m"],
                item["rank_metrics"]["approach_forward_clearance_m"],
            ), reverse=True)
            promoted = alternatives[0]
            selected.update({key: promoted[key] for key in (
                "room", "room_rect", "rect", "rotation_deg",
                "rotated_stair_min", "stair_size",
                "entry_world", "exit_world", "entry_dir", "exit_dir",
                "approach_clear", "egress_clear", "precision_clip_gate_pass",
            )})
            selected["selection_policy"] = (
                "maximize minimum extra approach/egress corridor clearance; "
                "then maximize Floor2 forward arrival clearance"
            )
            selected["selection_rank_metrics"] = promoted["rank_metrics"]
            selected["feasible_placement_count"] = len(alternatives)
            selected["feasible_placements"] = alternatives
            found.append(selected)
        else:
            strict_placement_rejections.append({
                "candidate_name": "{}_{}_{}_{}_h{}".format(
                    r["scene_type"],
                    r["floor1"].replace("_ceiling", "").replace("train_", "train"),
                    r["floor2"].replace("_ceiling", "").replace("train_", "train"),
                    args.kind, int(round(float(r["floor1_f2f"]) * 100))),
                "floor1": r["floor1"], "floor2": r["floor2"],
                "kind": args.kind, "height_m": r["floor1_f2f"],
                "reason": "A4_NO_STRICT_PLACEMENT_AFTER_EXHAUSTIVE_GRID_SEARCH",
            })

    evaluated_count = (len(found) + len(doorway_clearance_rejections)
                       + len(strict_placement_rejections))
    if evaluated_count != len(ordered):
        raise RuntimeError(
            "A4 outcome accounting mismatch: {} outcomes for {} pairs".format(
                evaluated_count, len(ordered)))

    out = {
        "placement_contract_version": 3,
        "corridor_obstacle_source": "all_obstacle_boxes_walk_prism",
        "doorway_clearance_contract_version": 2,
        "doorway_clearance_rule": (
            "maximum unoccupied lateral run in 5 cm rasterized enabled-collider "
            "occupancy at each doorway centre line >= 0.60 m; swinging doorway "
            "leaves/handles excluded by the generation policy, while frames, "
            "jambs, and unrelated appliance doors remain included "
            "(0.50 m body + 0.10 m margin)"
        ),
        "doorway_clearance_rejection_count": len(doorway_clearance_rejections),
        "doorway_clearance_rejections": doorway_clearance_rejections,
        "strict_placement_rejection_count": len(strict_placement_rejections),
        "strict_placement_rejections": strict_placement_rejections,
        "stair_unrotated_size": sl["size"],
        "stair_height_m": sl["height"],
        "stair_visual_height_m": sl["visual_height"],
        "height_tolerance_m": args.height_tolerance,
        "rotations_tested_deg": rotations,
        "pairs_height_and_kind_eligible": len(ordered),
        "pairs_evaluated_count": evaluated_count,
        "search_exhaustive": True,
        "corridor_m": CORRIDOR,
        "corridor_body_radius_m": BODY_RADIUS,
        "corridor_reserved_length_m": CORRIDOR + BODY_RADIUS,
        "grid_step_m": args.grid_step,
        "selection_policy": (
            "maximize minimum extra approach/egress corridor clearance; "
            "then maximize Floor2 forward arrival clearance"
        ),
        "precision_clip_gate": "stair triangles vs wall/furniture AABBs; 5 mm touch tolerance",
        "precision_clip_rejection_count": len(precision_clip_rejections),
        "precision_clip_rejections": precision_clip_rejections,
        "candidates": found,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
