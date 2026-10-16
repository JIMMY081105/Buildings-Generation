#!/usr/bin/env python3
"""Building-level: stack TWO different MolmoSpaces scenes and connect them.

Floor 1 and Floor 2 are different scenes, so nothing can be hard-coded by prim
path the way the single-scene proof was. Slabs, ceilings and the plane collider
are all discovered by predicate against the stairwell opening.

Does, and verifies on CPU:
  * Floor 2 slab   cut around the stair
  * Floor 1 ceiling cut to match
  * Floor 2 UsdGeomPlane collider replaced by a holed mesh collider
  * clipping       stair vs both floors' walls and furniture   <-- new
Sources are referenced read-only and checksummed before/after.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

from door_collision_policy import disable_swinging_assemblies
from pair_scenes import scan
from pipeline_contract import (BODY_HEIGHT_M, BODY_RADIUS_M, CORRIDOR_M,
                               corridor_polygon, walk_prism_obstacle_hits)

SCENES = Path(os.environ.get(
    "BUILDING_SCENES_ROOT",
    "/ssd_xs/scvj260/scene_comparison/restore/assets_index/scenes/holodeck-objaverse-val",
))


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def rotate_xy(point, degrees):
    """Exact quarter-turn rotation about the referenced stair's local origin."""
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
    raise ValueError("rotation_deg must be one of 0, 90, 180, 270")


def unit_xy(a, b):
    dx, dy = float(b[0]) - float(a[0]), float(b[1]) - float(a[1])
    length = (dx * dx + dy * dy) ** 0.5
    return (dx / length, dy / length) if length > 1e-9 else (0.0, 1.0)


def rect_difference(room, hole):
    rx0, ry0, rx1, ry1 = room
    hx0, hy0 = max(hole[0], rx0), max(hole[1], ry0)
    hx1, hy1 = min(hole[2], rx1), min(hole[3], ry1)
    if hx0 >= hx1 or hy0 >= hy1:
        return [tuple(room)]
    out = []
    if hy0 > ry0:
        out.append((rx0, ry0, rx1, hy0))
    if hy1 < ry1:
        out.append((rx0, hy1, rx1, ry1))
    if hx0 > rx0:
        out.append((rx0, hy0, hx0, hy1))
    if hx1 < rx1:
        out.append((hx1, hy0, rx1, hy1))
    return [r for r in out if r[2] - r[0] > 1e-6 and r[3] - r[1] > 1e-6]


def slab_mesh(rects, z0, z1):
    pts, faces, counts = [], [], []
    for (x0, y0, x1, y1) in rects:
        b = len(pts)
        pts += [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        for q in [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                  (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]:
            faces += [b + q[0], b + q[1], b + q[2], b + q[3]]
            counts.append(4)
    return pts, faces, counts


def world_tris(prim) -> np.ndarray:
    mesh = UsdGeom.Mesh(prim)
    pts = mesh.GetPointsAttr().Get()
    cnt = mesh.GetFaceVertexCountsAttr().Get()
    ind = mesh.GetFaceVertexIndicesAttr().Get()
    if not pts or not cnt or not ind:
        return np.zeros((0, 3, 3))
    xf = UsdGeom.XformCache(Usd.TimeCode.Default()).GetLocalToWorldTransform(prim)
    wp = [xf.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in pts]
    wp = np.array([(v[0], v[1], v[2]) for v in wp], dtype=float)
    tris, k = [], 0
    for c in cnt:
        poly = [ind[k + i] for i in range(c)]
        for i in range(1, c - 1):
            tris.append([wp[poly[0]], wp[poly[i]], wp[poly[i + 1]]])
        k += c
    return np.asarray(tris, dtype=float) if tris else np.zeros((0, 3, 3))


def tri_box_overlap(tris: np.ndarray, bmin, bmax) -> np.ndarray:
    """Akenine-Moller triangle/AABB overlap, vectorised over triangles."""
    if len(tris) == 0:
        return np.zeros(0, dtype=bool)
    c = (np.asarray(bmin) + np.asarray(bmax)) / 2.0
    h = (np.asarray(bmax) - np.asarray(bmin)) / 2.0
    v = tris - c
    ok = np.ones(len(tris), dtype=bool)
    # 3 box face normals
    for a in range(3):
        ok &= ~((v[:, :, a].min(axis=1) > h[a]) | (v[:, :, a].max(axis=1) < -h[a]))
    if not ok.any():
        return ok
    e = [v[:, 1] - v[:, 0], v[:, 2] - v[:, 1], v[:, 0] - v[:, 2]]
    # triangle plane vs box
    n = np.cross(e[0], e[1])
    d = np.einsum("ij,ij->i", n, v[:, 0])
    r = np.abs(n) @ h
    ok &= ~(np.abs(d) > r)
    # 9 edge cross axes
    for ei in e:
        for a in range(3):
            ax = np.zeros_like(ei)
            ax[:, a] = 1.0
            axis = np.cross(ei, ax)
            p = np.einsum("ijk,ik->ij", v, axis)
            rr = np.abs(axis) @ h
            ok &= ~((p.min(axis=1) > rr) | (p.max(axis=1) < -rr))
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor1-scene", required=True)
    ap.add_argument("--floor2-scene", required=True)
    ap.add_argument("--kind", required=True)
    ap.add_argument("--stair", type=Path, required=True)
    ap.add_argument("--stair-path", type=Path, required=True)
    ap.add_argument("--placement", type=json.loads, required=True,
                    help='JSON: {"rect":[x0,y0,x1,y1],"room_rect":[...]}')
    ap.add_argument("--floor-to-floor", type=float, required=True)
    ap.add_argument("--clearance", type=float, default=0.20)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()

    s1 = SCENES / args.floor1_scene / "scene.usda"
    s2 = SCENES / args.floor2_scene / "scene.usda"
    f2f = float(args.floor_to_floor)
    R = {"floor1_scene": args.floor1_scene, "floor2_scene": args.floor2_scene,
         "kind": args.kind, "floor_to_floor_m": f2f,
         "sha_before": {"f1": sha(s1), "f2": sha(s2), "stair": sha(args.stair)}}
    control_tris = np.asarray([[[0.0, 0.0, 0.0],
                                [1.0, 0.0, 0.0],
                                [0.0, 1.0, 0.0]]])
    control_hit = bool(tri_box_overlap(
        control_tris, [-0.1, -0.1, -0.1], [0.2, 0.2, 0.1]).any())
    control_clear = not bool(tri_box_overlap(
        control_tris, [3.0, 3.0, 3.0], [4.0, 4.0, 4.0]).any())
    R["clip_check_controls"] = {
        "known_overlap_detected": control_hit,
        "known_clear_rejected": control_clear,
        "pass": control_hit and control_clear,
    }
    if not R["clip_check_controls"]["pass"]:
        raise RuntimeError("triangle/AABB clipping controls failed")

    # ---- stage
    if args.out.exists():
        args.out.unlink()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(args.out))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    f1 = UsdGeom.Xform.Define(stage, "/World/Floor1")
    f1.GetPrim().GetReferences().AddReference(str(s1))
    f2 = UsdGeom.Xform.Define(stage, "/World/Floor2")
    f2.GetPrim().GetReferences().AddReference(str(s2))
    UsdGeom.Xformable(f2.GetPrim()).AddTranslateOp().Set(Gf.Vec3d(0, 0, f2f))

    # ---- stair placement
    stair_stage = Usd.Stage.Open(str(args.stair))
    sc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    sr = sc.ComputeWorldBound(stair_stage.GetPrimAtPath("/Asset")).ComputeAlignedRange()
    smin, smax = sr.GetMin(), sr.GetMax()
    path_contract = json.loads(args.stair_path.read_text(encoding="utf-8"))
    wp = path_contract["points_xyz_m"]
    # points_xyz_m starts on the first tread; the final/top z is the actual
    # floor-to-floor rise (max-min would incorrectly subtract one riser).
    stair_rise = max(float(p[2]) for p in wp)
    place = args.placement["rect"]
    rotation_deg = int(args.placement.get("rotation_deg", 0)) % 360
    corners = [rotate_xy(p, rotation_deg) for p in
               ((smin[0], smin[1]), (smax[0], smin[1]),
                (smax[0], smax[1]), (smin[0], smax[1]))]
    rotated_min = (min(p[0] for p in corners), min(p[1] for p in corners))
    rotated_max = (max(p[0] for p in corners), max(p[1] for p in corners))
    off = (float(place[0]) - rotated_min[0], float(place[1]) - rotated_min[1], 0.0)
    expected_size = (float(place[2]) - float(place[0]),
                     float(place[3]) - float(place[1]))
    actual_size = (rotated_max[0] - rotated_min[0], rotated_max[1] - rotated_min[1])
    if max(abs(expected_size[i] - actual_size[i]) for i in (0, 1)) > 0.001:
        raise SystemExit("placement rect does not match the rotated stair footprint")
    stair_visual_height = float(smax[2] - smin[2])
    if abs(stair_rise - f2f) > 0.001:
        raise SystemExit("stair rise and floor-to-floor height differ by more than 1 mm")
    stair = UsdGeom.Xform.Define(stage, "/World/Stair")
    stair.GetPrim().GetReferences().AddReference(str(args.stair))
    stair_xf = UsdGeom.XformCommonAPI(stair.GetPrim())
    stair_xf.SetTranslate(Gf.Vec3d(*off))
    stair_xf.SetRotate(Gf.Vec3f(0.0, 0.0, float(rotation_deg)),
                       UsdGeom.XformCommonAPI.RotationOrderXYZ)
    fx0, fy0 = rotated_min[0] + off[0], rotated_min[1] + off[1]
    fx1, fy1 = rotated_max[0] + off[0], rotated_max[1] + off[1]
    R["stair_footprint_world"] = [fx0, fy0, fx1, fy1]
    R["stair_offset"] = list(off)
    R["stair_rotation_deg"] = rotation_deg
    R["stair_height_m"] = stair_rise
    R["stair_visual_height_m"] = stair_visual_height
    R["height_error_m"] = abs(stair_rise - f2f)

    # ---- opening, exit edge flush with the final stair/floor interface
    c = args.clearance
    hole = [fx0 - c, fy0 - c, fx1 + c, fy1 + c]
    last_local = rotate_xy(wp[-1], rotation_deg)
    prev_local = rotate_xy(wp[-2], rotation_deg)
    last = [last_local[0] + off[0], last_local[1] + off[1]]
    prev = [prev_local[0] + off[0], prev_local[1] + off[1]]
    ex, ey = last[0] - prev[0], last[1] - prev[1]
    interface_xy = path_contract.get("final_riser_interface_xy_m")
    if interface_xy is not None:
        interface_local = rotate_xy(interface_xy, rotation_deg)
        opening_exit = [interface_local[0] + off[0], interface_local[1] + off[1]]
        tol = 0.0
    else:
        # Legacy path contract: the old endpoint sat at an authored top tread
        # centre, so retain its historical 20 mm opening inset.
        opening_exit = last
        tol = 0.02
    if abs(ex) >= abs(ey):
        if ex < 0:
            hole[0] = max(hole[0], opening_exit[0] + tol)
        else:
            hole[2] = min(hole[2], opening_exit[0] - tol)
    else:
        if ey < 0:
            hole[1] = max(hole[1], opening_exit[1] + tol)
        else:
            hole[3] = min(hole[3], opening_exit[1] - tol)
    hole = tuple(hole)
    R["opening_rect"] = list(hole)
    R["last_walkable_xy"] = last
    R["opening_exit_interface_xy"] = opening_exit
    R["upper_floor_is_final_landing"] = bool(
        path_contract.get("upper_floor_is_final_landing", False)
    )
    R["final_landing_support_provider"] = path_contract.get(
        "final_landing_support_provider", "stair_asset"
    )
    R["final_landing_floor_inset_m"] = (
        ((last[0] - opening_exit[0]) ** 2 + (last[1] - opening_exit[1]) ** 2) ** 0.5
    )
    R["QA_opening_exit_aligned_to_final_riser"] = (
        abs(
            (hole[0] if ex < 0 else hole[2]) - opening_exit[0]
        ) <= 1e-6
        if abs(ex) >= abs(ey)
        else abs(
            (hole[1] if ey < 0 else hole[3]) - opening_exit[1]
        ) <= 1e-6
    )
    R["QA_final_landing_is_on_floor_side_of_opening"] = (
        (last[0] < hole[0] if ex < 0 else last[0] > hole[2])
        if abs(ex) >= abs(ey)
        else (last[1] < hole[1] if ey < 0 else last[1] > hole[3])
    )

    # Repeat A4's walk-prism audit at build time.  PhysX alone is insufficient:
    # MolmoSpaces contains visual-only furniture with no collider, and the brief
    # explicitly requires those AABBs to veto both approach and egress.
    entry_local = rotate_xy(wp[0], rotation_deg)
    entry_next_local = rotate_xy(wp[1], rotation_deg)
    entry = [entry_local[0] + off[0], entry_local[1] + off[1]]
    entry_next = [entry_next_local[0] + off[0], entry_next_local[1] + off[1]]
    en_dir = unit_xy(entry, entry_next)
    ex_dir = unit_xy(prev, last)
    approach = corridor_polygon(entry, (-en_dir[0], -en_dir[1]))
    egress = corridor_polygon(last, ex_dir)
    lower_scan, upper_scan = scan(args.floor1_scene), scan(args.floor2_scene)
    if not lower_scan or not upper_scan:
        raise RuntimeError("could not scan scenes for corridor audit")
    approach_hits = walk_prism_obstacle_hits(approach, lower_scan)
    egress_hits = walk_prism_obstacle_hits(egress, upper_scan)
    approach_wall = bool(lower_scan["wall_union"] is not None
                         and approach.intersects(lower_scan["wall_union"]))
    egress_wall = bool(upper_scan["wall_union"] is not None
                       and egress.intersects(upper_scan["wall_union"]))
    R["corridor_contract"] = {
        "corridor_m": CORRIDOR_M,
        "body_radius_m": BODY_RADIUS_M,
        "reserved_length_m": CORRIDOR_M + BODY_RADIUS_M,
        "body_height_m": BODY_HEIGHT_M,
    }
    R["approach_corridor_audit"] = {
        "bounds": list(approach.bounds),
        "room_contains": bool(lower_scan["room_union"].contains(approach)),
        "wall_intersects": approach_wall,
        "furniture_hits": approach_hits,
    }
    R["egress_corridor_audit"] = {
        "bounds": list(egress.bounds),
        "room_contains": bool(upper_scan["room_union"].contains(egress)),
        "wall_intersects": egress_wall,
        "furniture_hits": egress_hits,
    }
    R["QA_approach_corridor_clear"] = bool(
        R["approach_corridor_audit"]["room_contains"]
        and not approach_wall and not approach_hits)
    R["QA_egress_corridor_clear"] = bool(
        R["egress_corridor_audit"]["room_contains"]
        and not egress_wall and not egress_hits)

    stage.GetRootLayer().Save()
    live = Usd.Stage.Open(str(args.out), load=Usd.Stage.LoadAll)
    bb = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                           [UsdGeom.Tokens.default_, UsdGeom.Tokens.guide])
    stair_bb = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        useExtentsHint=False).ComputeWorldBound(
            live.GetPrimAtPath("/World/Stair")).ComputeAlignedRange()
    if stair_bb.IsEmpty():
        R["QA_stair_transform_matches_placement"] = False
        R["actual_stair_footprint_world"] = None
    else:
        actual_min, actual_max = stair_bb.GetMin(), stair_bb.GetMax()
        actual_fp = [float(actual_min[0]), float(actual_min[1]),
                     float(actual_max[0]), float(actual_max[1])]
        R["actual_stair_footprint_world"] = actual_fp
        R["QA_stair_transform_matches_placement"] = max(
            abs(actual_fp[i] - R["stair_footprint_world"][i]) for i in range(4)) <= 0.001

    def overlaps_hole(rng) -> bool:
        mn, mx = rng.GetMin(), rng.GetMax()
        return (mn[0] < hole[2] and mx[0] > hole[0]
                and mn[1] < hole[3] and mx[1] > hole[1])

    # Every Floor 2 room, captured BEFORE anything is deactivated. The plane
    # replacement must span the whole storey; computing it from surviving rooms
    # only produced a collision floor that missed the stairwell entirely.
    all_f2_rooms = []
    for prim in Usd.PrimRange(live.GetPrimAtPath("/World/Floor2")):
        if prim.IsA(UsdGeom.Mesh) and prim.GetName().startswith("room_") and "_visual" in prim.GetName():
            rr = bb.ComputeWorldBound(prim).ComputeAlignedRange()
            if not rr.IsEmpty():
                all_f2_rooms.append((float(rr.GetMin()[0]), float(rr.GetMin()[1]),
                                     float(rr.GetMax()[0]), float(rr.GetMax()[1])))
    R["floor2_room_count"] = len(all_f2_rooms)

    # ---- discover what to cut (no hard-coded prim paths: scenes differ)
    cuts, deactivated = [], []
    targets = [("/World/Floor2", "room_", "Floor2Slab"),
               ("/World/Floor1", "ceiling_", "Floor1Ceiling")]
    for root, prefix, label in targets:
        rp = live.GetPrimAtPath(root)
        idx = 0
        for prim in Usd.PrimRange(rp):
            if not prim.IsA(UsdGeom.Mesh):
                continue
            n = prim.GetName()
            if not (n.startswith(prefix) and "_visual" in n):
                continue
            rng = bb.ComputeWorldBound(prim).ComputeAlignedRange()
            if rng.IsEmpty() or not overlaps_hole(rng):
                continue
            mn, mx = rng.GetMin(), rng.GetMax()
            rect = (float(mn[0]), float(mn[1]), float(mx[0]), float(mx[1]))
            z0, z1 = float(mn[2]), float(mx[2])
            if label == "Floor1Ceiling":
                z0, z1 = z0 - 0.002, z1 - 0.002
            mat = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
            cuts.append({"label": "{}_{}".format(label, idx), "src": str(prim.GetPath()),
                         "rect": list(rect), "z": [z0, z1],
                         "material": str(mat.GetPath()) if mat else None})
            idx += 1

    # ---- the per-storey UsdGeomPlane collider on Floor 2
    plane_info = None
    for prim in Usd.PrimRange(live.GetPrimAtPath("/World/Floor2")):
        if prim.GetName() == "floor" and prim.HasAPI(UsdPhysics.CollisionAPI):
            pr = bb.ComputeWorldBound(prim).ComputeAlignedRange()
            plane_info = {"path": str(prim.GetPath()), "type": str(prim.GetTypeName()),
                          "z": float(pr.GetMin()[2]) if not pr.IsEmpty() else f2f}
            break

    # ---- author into the live stage
    live.SetEditTarget(live.GetRootLayer())
    for cinfo in cuts:
        p = live.GetPrimAtPath(cinfo["src"])
        p.SetActive(False)
        deactivated.append(cinfo["src"])
    if plane_info:
        live.GetPrimAtPath(plane_info["path"]).SetActive(False)
        deactivated.append(plane_info["path"])
    UsdGeom.Scope.Define(live, "/World/Cut")

    made = {}
    for cinfo in cuts:
        rects = rect_difference(cinfo["rect"], hole)
        pts, idx_, cnt_ = slab_mesh(rects, cinfo["z"][0], cinfo["z"][1])
        m = UsdGeom.Mesh.Define(live, "/World/Cut/" + cinfo["label"])
        m.CreatePointsAttr([Gf.Vec3f(*p) for p in pts])
        m.CreateFaceVertexIndicesAttr(idx_)
        m.CreateFaceVertexCountsAttr(cnt_)
        m.CreateSubdivisionSchemeAttr("none")
        m.CreateExtentAttr([Gf.Vec3f(cinfo["rect"][0], cinfo["rect"][1], cinfo["z"][0]),
                            Gf.Vec3f(cinfo["rect"][2], cinfo["rect"][3], cinfo["z"][1])])
        if cinfo["material"]:
            mat = UsdShade.Material.Get(live, Sdf.Path(cinfo["material"]))
            if mat:
                UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
        UsdPhysics.CollisionAPI.Apply(m.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(m.GetPrim()).CreateApproximationAttr(
            UsdPhysics.Tokens.none)
        made[cinfo["label"]] = {"bands": len(rects), "faces": len(cnt_),
                                "z": cinfo["z"], "src": cinfo["src"]}

    if plane_info and all_f2_rooms:
        bx0 = min(r[0] for r in all_f2_rooms); by0 = min(r[1] for r in all_f2_rooms)
        bx1 = max(r[2] for r in all_f2_rooms); by1 = max(r[3] for r in all_f2_rooms)
        building = (bx0 - 0.5, by0 - 0.5, bx1 + 0.5, by1 + 0.5)
        pz = plane_info["z"]
        rects = rect_difference(building, hole)
        pts, idx_, cnt_ = slab_mesh(rects, pz - 0.04, pz)
        pm = UsdGeom.Mesh.Define(live, "/World/Cut/Floor2CollisionPlane")
        pm.CreatePointsAttr([Gf.Vec3f(*p) for p in pts])
        pm.CreateFaceVertexIndicesAttr(idx_)
        pm.CreateFaceVertexCountsAttr(cnt_)
        pm.CreateSubdivisionSchemeAttr("none")
        pm.CreateExtentAttr([Gf.Vec3f(building[0], building[1], pz - 0.04),
                             Gf.Vec3f(building[2], building[3], pz)])
        UsdPhysics.CollisionAPI.Apply(pm.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(pm.GetPrim()).CreateApproximationAttr(
            UsdPhysics.Tokens.none)
        UsdGeom.Imageable(pm.GetPrim()).MakeInvisible()
        made["Floor2CollisionPlane"] = {"bands": len(rects), "faces": len(cnt_),
                                        "building_rect": list(building), "visible": False}
    R["cut_meshes"] = made
    R["deactivated"] = deactivated
    R["replaced_plane"] = plane_info

    # Human-selected locomotion policy: an authored closed door must never
    # become a navigation barrier.  Apply this to the composed building rather
    # than shipping a companion overlay, while retaining collidable frames,
    # jambs and every unrelated appliance/cabinet door.
    R["door_collision_policy"] = disable_swinging_assemblies(live)
    live.GetRootLayer().Save()

    # ---------------------------------------------------------- clipping check
    chk = Usd.Stage.Open(str(args.out), load=Usd.Stage.LoadAll)
    cb = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    stair_prim = chk.GetPrimAtPath("/World/Stair")
    stair_tris = []
    for prim in Usd.PrimRange(stair_prim):
        if prim.IsA(UsdGeom.Mesh) and prim.IsActive():
            if UsdGeom.Imageable(prim).ComputeVisibility() == UsdGeom.Tokens.invisible:
                continue
            t = world_tris(prim)
            if len(t):
                stair_tris.append(t)
    stair_tris = np.concatenate(stair_tris) if stair_tris else np.zeros((0, 3, 3))
    srng = cb.ComputeWorldBound(stair_prim).ComputeAlignedRange()
    smn, smx = np.array(srng.GetMin()), np.array(srng.GetMax())
    R["clip_stair_triangles"] = int(len(stair_tris))

    wall_hits, furn_hits, tested = [], [], 0
    for root in ("/World/Floor1", "/World/Floor2"):
        for prim in Usd.PrimRange(chk.GetPrimAtPath(root)):
            if not prim.IsA(UsdGeom.Mesh) or not prim.IsActive():
                continue
            n = prim.GetName()
            # Anything that is not architecture counts as furniture. A prefix
            # allowlist (obja_/stand_/armchair_) only matched 3 boxes in this
            # scene pair and would have quietly skipped most of the contents.
            is_wall = n.startswith("wall_") and "_visual" in n
            is_arch = (n.startswith("wall_") or n.startswith("room_")
                       or n.startswith("ceiling_") or n == "floor")
            is_furn = (not is_arch) and ("_collider" not in n)
            if not (is_wall or is_furn):
                continue
            if UsdGeom.Imageable(prim).ComputeVisibility() == UsdGeom.Tokens.invisible:
                continue
            rr = cb.ComputeWorldBound(prim).ComputeAlignedRange()
            if rr.IsEmpty():
                continue
            bmn, bmx = np.array(rr.GetMin()), np.array(rr.GetMax())
            if np.any(bmn > smx) or np.any(bmx < smn):
                continue
            tested += 1
            # shrink 5 mm so surface-touching does not read as clipping
            hit = tri_box_overlap(stair_tris, bmn + 0.005, bmx - 0.005)
            if hit.any():
                rec = {"prim": str(prim.GetPath()), "floor": root.split("/")[-1],
                       "triangles_intersecting": int(hit.sum()),
                       "box": [round(float(v), 3) for v in list(bmn) + list(bmx)]}
                (wall_hits if is_wall else furn_hits).append(rec)
    R["clip_boxes_tested"] = tested
    R["clip_wall_intersections"] = wall_hits
    R["clip_furniture_intersections"] = furn_hits
    R["QA_no_clipping_walls"] = len(wall_hits) == 0
    R["QA_no_clipping_furniture"] = len(furn_hits) == 0
    R["clip_note"] = ("walls are axis-aligned boxes so wall results are exact; "
                      "furniture uses its AABB, so furniture hits are conservative "
                      "and may over-report on concave pieces")

    R["sha_after"] = {"f1": sha(s1), "f2": sha(s2), "stair": sha(args.stair)}
    R["sources_unmodified"] = R["sha_before"] == R["sha_after"]
    R["out_usd"] = str(args.out)
    R["build_acceptance_gates"] = {
        "clip_check_controls": R["clip_check_controls"]["pass"],
        "height_match": R["height_error_m"] <= 0.001,
        "stair_transform_matches_placement": R["QA_stair_transform_matches_placement"],
        "opening_exit_aligned_to_final_riser": R[
            "QA_opening_exit_aligned_to_final_riser"
        ],
        "final_landing_is_on_floor_side_of_opening": R[
            "QA_final_landing_is_on_floor_side_of_opening"
        ],
        "approach_corridor_clear": R["QA_approach_corridor_clear"],
        "egress_corridor_clear": R["QA_egress_corridor_clear"],
        "door_swinging_members_collision_disabled": R[
            "door_collision_policy"
        ]["checks"]["every_selected_swinging_member_collision_disabled"],
        "door_frames_and_jambs_collision_enabled": R[
            "door_collision_policy"
        ]["checks"]["every_frame_and_jamb_collision_enabled"],
        "no_clipping_walls": R["QA_no_clipping_walls"],
        "no_clipping_furniture": R["QA_no_clipping_furniture"],
        "sources_unmodified": R["sources_unmodified"],
    }
    R["status"] = ("pass" if all(R["build_acceptance_gates"].values()) else "fail")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(R, indent=2), encoding="utf-8")
    brief = {k: v for k, v in R.items() if k not in ("clip_wall_intersections",
                                                     "clip_furniture_intersections")}
    brief["wall_hits"] = len(wall_hits)
    brief["furn_hits"] = len(furn_hits)
    print(json.dumps(brief, indent=2))


if __name__ == "__main__":
    main()
