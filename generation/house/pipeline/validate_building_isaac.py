#!/usr/bin/env python3
"""Building-level Isaac validation + the render set for human sign-off.

One job does all of it, because reopening a stage in a live Kit session
segfaults:

  C3  PhysX proof the collider has the hole (up clear / side blocked / down
      lands on the stair)
  T   traversal Floor 1 -> stair -> Floor 2 (support, headroom, sweep, settle)
  R   five review renders, so the scene can be signed off by eye in Isaac

Named honestly: the traversal part is swept-volume + support checking, not a
locomotion policy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import time
from pathlib import Path

from pipeline_contract import (BODY_HEIGHT_M, BODY_RADIUS_M,
                               CAMERA_POSE_CONTRACT,
                               CAMERA_POSE_CONTRACT_SHA256, CORRIDOR_M,
                               DYNAMIC_DROP_HEIGHT_M,
                               DYNAMIC_DROP_PROBE_RADIUS_M,
                               DYNAMIC_FINAL_HEIGHT_TOLERANCE_M,
                               DYNAMIC_LATE_SETTLE_TOLERANCE_M,
                               vertical_ccd_drop_pass)

from isaacsim import SimulationApp

ap = argparse.ArgumentParser()
ap.add_argument("--stage", type=Path, required=True)
ap.add_argument("--build-report", type=Path, required=True)
ap.add_argument("--stair-path", type=Path, required=True)
ap.add_argument("--out-dir", type=Path, required=True)
ap.add_argument("--report", type=Path, required=True)
ap.add_argument("--body-radius", type=float, default=BODY_RADIUS_M)
ap.add_argument("--body-height", type=float, default=BODY_HEIGHT_M)
ap.add_argument("--tail-m", type=float, default=CORRIDOR_M,
                help="approach/egress run; MUST match pick_placement CORRIDOR")
ap.add_argument("--width", type=int, default=1500)
ap.add_argument("--height", type=int, default=1100)
args, _ = ap.parse_known_args()

APP = SimulationApp({"headless": True, "renderer": "RayTracedLighting",
                     "width": args.width, "height": args.height,
                     "active_gpu": 0, "physics_gpu": 0, "multi_gpu": False})

import carb  # noqa: E402
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport  # noqa: E402
from omni.physx import get_physx_scene_query_interface  # noqa: E402
from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade  # noqa: E402

from collision_render_isolation import (  # noqa: E402
    assert_collision_render_isolated,
    isolate_collision_render,
)

R = {"evidence_kind": "physx_collider_proof_plus_swept_traversability_NOT_locomotion_policy"}


def updates(n):
    for _ in range(n):
        APP.update()


def make_building_colliders_static(stage):
    """Keep imported furniture/building colliders at their authored poses.

    Infinigen simulation exports may carry RigidBodyAPI on scene objects.  The
    building validator needs those objects as static collision obstacles; if
    they remain dynamic, overlapping furnished-scene colliders can explode to
    non-finite transforms as soon as the timeline plays and invalidate both
    the renders and collision queries.
    """
    records = []
    roots = ("/World/Floor1", "/World/Floor2")
    for root_path in roots:
        root = stage.GetPrimAtPath(root_path)
        if not root or not root.IsValid():
            raise RuntimeError("missing building root: {}".format(root_path))
        for prim in Usd.PrimRange(root):
            if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
                continue
            body = UsdPhysics.RigidBodyAPI(prim)
            enabled = body.GetRigidBodyEnabledAttr().Get()
            kinematic = body.GetKinematicEnabledAttr().Get()
            if not prim.RemoveAPI(UsdPhysics.RigidBodyAPI):
                raise RuntimeError(
                    "could not remove building RigidBodyAPI: {}".format(
                        prim.GetPath()))
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                raise RuntimeError(
                    "building RigidBodyAPI remained after removal: {}".format(
                        prim.GetPath()))
            records.append({
                "prim": str(prim.GetPath()),
                "rigid_body_enabled_before": bool(enabled),
                "kinematic_enabled_before": bool(kinematic),
                "rigid_body_removed": True,
            })
    remaining = [
        str(prim.GetPath())
        for root_path in roots
        for prim in Usd.PrimRange(stage.GetPrimAtPath(root_path))
        if prim.HasAPI(UsdPhysics.RigidBodyAPI)
    ]
    if remaining:
        raise RuntimeError(
            "dynamic building bodies remain: {}".format(remaining))
    return records


def look_at(eye, target, up=Gf.Vec3d(0, 0, 1)):
    fwd = (target - eye).GetNormalized()
    if abs(Gf.Dot(fwd, up)) > 0.999:
        up = Gf.Vec3d(0, 1, 0)
    right = Gf.Cross(fwd, up).GetNormalized()
    tu = Gf.Cross(right, fwd)
    m = Gf.Matrix4d(1.0)
    m.SetRow3(0, right); m.SetRow3(1, tu); m.SetRow3(2, -fwd); m.SetRow3(3, eye)
    return m


def rotate_xy(point, degrees):
    """Exact quarter-turn used by placement/building assembly."""
    x, y = float(point[0]), float(point[1])
    degrees = int(degrees) % 360
    if degrees == 0:
        return (x, y)
    if degrees == 90:
        return (-y, x)
    if degrees == 180:
        return (-x, -y)
    if degrees == 270:
        return (y, -x)
    raise ValueError("stair_rotation_deg must be one of 0, 90, 180, 270")


def make_gray_material(stage):
    material = UsdShade.Material.Define(stage, "/Rev/GrayCollisionMaterial")
    shader = UsdShade.Shader.Define(stage, "/Rev/GrayCollisionMaterial/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(0.42, 0.42, 0.42))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.82)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def clean(h):
    o = {}
    for k in ("hit", "distance", "collision", "position"):
        if k in h:
            v = h[k]
            o[k] = bool(v) if k == "hit" else (float(v) if k == "distance" else (
                [round(float(x), 4) for x in v] if k == "position" else str(v)))
    return o


def ray(q, o, d, dist):
    return clean(q.raycast_closest(origin=[float(v) for v in o],
                                   dir=[float(v) for v in d], distance=float(dist)))


def sweep(q, r, o, d, dist):
    try:
        return clean(q.sweep_sphere_closest(float(r), [float(v) for v in o],
                                            [float(v) for v in d], float(dist), False))
    except Exception as exc:  # noqa: BLE001
        return {"unavailable": str(exc)}


def settle_file(p: Path, budget=200.0):
    dl, last, stable = time.time() + budget, -1, 0
    while time.time() < dl:
        APP.update()
        if p.exists():
            s = p.stat().st_size
            if s > 0 and s == last:
                stable += 1
                if stable >= 3:
                    return
            else:
                stable = 0
            last = s
    raise TimeoutError("capture never settled: {}".format(p))


def main():
    b = json.loads(args.build_report.read_text(encoding="utf-8"))
    hole = b["opening_rect"]
    f2f = float(b["floor_to_floor_m"])
    off = b["stair_offset"]
    slab_key = next(k for k in b["cut_meshes"] if k.startswith("Floor2Slab"))
    slab_z = b["cut_meshes"][slab_key]["z"]
    cx, cy = (hole[0] + hole[2]) / 2.0, (hole[1] + hole[3]) / 2.0
    R["opening_rect"] = hole
    R["stair_rotation_deg"] = int(b.get("stair_rotation_deg", 0))
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,uuid,memory.total",
             "--format=csv,noheader"], check=True, capture_output=True,
            text=True, timeout=20).stdout.strip().splitlines()
    except Exception as exc:  # noqa: BLE001
        gpu = ["query failed: {}".format(exc)]
    R["renderer_device_evidence"] = {
        "requested_active_gpu": 0,
        "requested_physics_gpu": 0,
        "nvidia_smi": gpu,
        "note": "Isaac startup log must also name the Vulkan physical device",
    }
    R["build_cpu_gates"] = {
        "no_clipping_walls": bool(b.get("QA_no_clipping_walls")),
        "no_clipping_furniture": bool(b.get("QA_no_clipping_furniture")),
        "sources_unmodified": bool(b.get("sources_unmodified")),
        "height_error_m": float(b.get("height_error_m", 0.0)),
        "upper_floor_is_final_landing": bool(
            b.get("upper_floor_is_final_landing")
        ),
        "opening_exit_aligned_to_final_riser": bool(
            b.get("QA_opening_exit_aligned_to_final_riser")
        ),
        "final_landing_is_on_floor_side_of_opening": bool(
            b.get("QA_final_landing_is_on_floor_side_of_opening")
        ),
        "approach_corridor_clear": bool(
            b.get("QA_approach_corridor_clear")
        ),
        "egress_corridor_clear": bool(
            b.get("QA_egress_corridor_clear")
        ),
    }

    ctx = omni.usd.get_context()
    if not ctx.open_stage(str(args.stage)):
        raise RuntimeError("cannot open building stage")
    for _ in range(20000):
        APP.update()
        if not ctx.get_stage_loading_status()[2]:
            break
    updates(250)
    stage = ctx.get_stage()
    stage.SetEditTarget(stage.GetSessionLayer())
    static_body_records = make_building_colliders_static(stage)
    R["building_static_rigid_body_normalization"] = {
        "status": "pass",
        "removed_rigid_body_count": len(static_body_records),
        "remaining_rigid_body_count": 0,
        "records": static_body_records,
    }
    updates(30)

    approx = {}
    for k in b["cut_meshes"]:
        p = stage.GetPrimAtPath("/World/Cut/" + k)
        if p and p.IsValid():
            a = p.GetAttribute("physics:approximation")
            approx[k] = {"collisionAPI": bool(p.HasAPI(UsdPhysics.CollisionAPI)),
                         "meshCollisionAPI": bool(p.HasAPI(UsdPhysics.MeshCollisionAPI)),
                         "approximation": str(a.Get()) if a and a.HasAuthoredValue() else None}
    R["cut_collider_apis"] = approx
    R["originals_inactive"] = {p: (not stage.GetPrimAtPath(p).IsValid())
                               or (not stage.GetPrimAtPath(p).IsActive())
                               for p in b["deactivated"]}

    UsdPhysics.Scene.Define(stage, "/PhysicsScene").CreateGravityMagnitudeAttr(9.81)
    updates(60)
    omni.timeline.get_timeline_interface().play()
    updates(120)
    q = get_physx_scene_query_interface()

    path_contract = json.loads(args.stair_path.read_text(encoding="utf-8"))
    local = path_contract["points_xyz_m"]
    rotation_deg = R["stair_rotation_deg"]
    sp = []
    for p in local:
        x, y = rotate_xy(p, rotation_deg)
        sp.append([x + off[0], y + off[1], p[2] + off[2]])

    # ---------------- C3
    z0 = slab_z[0] - 0.30
    up_hit = ray(q, (cx, cy, z0), (0, 0, 1), 0.90)
    beside = {}
    beside_offset_m = 0.15
    for nm, (px, py) in {
            "east": (hole[2] + beside_offset_m, cy),
            "west": (hole[0] - beside_offset_m, cy),
            "north": (cx, hole[3] + beside_offset_m),
            "south": (cx, hole[1] - beside_offset_m),
    }.items():
        h = ray(q, (px, py, z0), (0, 0, 1), 0.90)
        if h.get("hit"):
            beside[nm] = h
    # The center of a rectangular opening can be intentionally empty for an
    # L- or U-shaped stair.  Probe the highest real traversal sample that is
    # still inside the opening and below Floor 2, so this gate always tests a
    # stair-aligned point rather than assuming straight-flight geometry.
    down_candidates = [
        p for p in sp
        if hole[0] - 1e-4 <= p[0] <= hole[2] + 1e-4
        and hole[1] - 1e-4 <= p[1] <= hole[3] + 1e-4
        and p[2] < f2f - 1e-4
    ]
    if not down_candidates:
        raise RuntimeError("no traversal sample lies below Floor 2 inside opening")
    down_probe = max(down_candidates, key=lambda p: p[2])
    down = ray(
        q,
        (down_probe[0], down_probe[1], f2f + 0.60),
        (0, 0, -1),
        f2f + 1.0,
    )
    R["C3_up_through_stairwell"] = up_hit
    R["C3_beside_probe_offset_m"] = beside_offset_m
    R["C3_beside_hits"] = beside
    R["C3_down_probe"] = {
        "selection": "highest_traversal_sample_inside_opening_below_floor2",
        "xyz": [round(v, 4) for v in down_probe],
    }
    R["C3_down_from_floor2"] = down
    R["C3_hole_open"] = not up_hit.get("hit")
    _BLOCKERS = ("/World/Cut/", "/World/Stair")
    R["C3_beside_blocked"] = len(beside) == 4 and all(
        v.get("hit") and any(
            blocker in str(v.get("collision", "")) for blocker in _BLOCKERS
        )
        for v in beside.values()
    )
    R["C3_down_lands_on_stair"] = bool(down.get("hit") and "/World/Stair" in str(down.get("collision", "")))

    # ---------------- traversal
    def uxy(a, c):
        dx, dy = c[0] - a[0], c[1] - a[1]
        n = math.hypot(dx, dy)
        return (dx / n, dy / n) if n > 1e-9 else (0.0, 1.0)

    en, ex = uxy(sp[0], sp[1]), uxy(sp[-2], sp[-1])
    # View 03 is a mandatory human gate for the arrival experience.  Measure
    # what is directly in front of a person stepping off the final tread, but
    # do not invent a subjective pass/fail threshold for crampedness.
    arrival_eye_xyz = [sp[-1][0] + ex[0] * 0.12,
                       sp[-1][1] + ex[1] * 0.12,
                       f2f + 1.62]
    arrival_forward = ray(q, arrival_eye_xyz, (ex[0], ex[1], 0.0), 4.0)
    R["view03_human_review"] = {
        "required": True,
        "status": "pending",
        "view": "03_arrival_render.png",
        "criterion": ("Judge whether the Floor-2 arrival feels too cramped; "
                      "if unacceptable, return to A4 placement selection."),
        "arrival_eye_xyz": [round(v, 4) for v in arrival_eye_xyz],
        "arrival_forward_direction_xy": [round(ex[0], 4), round(ex[1], 4)],
        "open_area_direction_xy": [round(-ex[0], 4), round(-ex[1], 4)],
        "forward_obstruction": arrival_forward,
    }
    # The approach/egress tails must not exceed the corridor length that
    # placement actually validated. They were hard-coded at 2.4 m and 2.5 m
    # while pick_placement.py only guarantees 1.2 m, so the tails ran past the
    # building envelope and the sweep hit exterior walls -- a contract mismatch
    # in the harness, not a defect in the scene. One parameter, both ends.
    tail = args.tail_m
    approach = [[sp[0][0] - en[0] * d, sp[0][1] - en[1] * d, 0.0]
                for d in sorted([round(0.4 * k, 2)
                                 for k in range(1, int(tail / 0.4) + 1)], reverse=True)]
    exits = [[sp[-1][0] + ex[0] * d, sp[-1][1] + ex[1] * d, slab_z[1]]
             for d in [round(0.3 * k, 2) for k in range(1, int(tail / 0.3) + 1)]]
    R["tail_m"] = tail
    R["tail_note"] = "must equal pick_placement.py CORRIDOR"
    path = approach + sp + exits
    stair_seg = ["stair"] * len(sp)
    if path_contract.get("final_landing_support_provider") == "upper_floor":
        stair_seg[-1] = "floor2_landing"
    seg = ["floor1"] * len(approach) + stair_seg + ["floor2"] * len(exits)
    half = args.body_height / 2.0

    samples, blocked = [], []
    for i, p in enumerate(path):
        s = ray(q, (p[0], p[1], p[2] + 0.35), (0, 0, -1), 0.80)
        surf = (p[2] + 0.35 - s["distance"]) if s.get("hit") else None
        dz = (surf - p[2]) if surf is not None else None
        h = ray(q, (p[0], p[1], p[2] + 0.15), (0, 0, 1), args.body_height + 0.45)
        head = (h["distance"] + 0.15) if h.get("hit") else args.body_height + 0.60
        samples.append({"i": i, "seg": seg[i], "xyz": [round(v, 4) for v in p],
                        "support_prim": s.get("collision"),
                        "support_dz": round(dz, 4) if dz is not None else None,
                        "headroom_m": round(head, 4),
                        "S_ok": bool(s.get("hit") and dz is not None and abs(dz) <= 0.22),
                        "H_ok": bool(head >= args.body_height)})
    for i in range(len(path) - 1):
        a, c = path[i], path[i + 1]
        d = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        L = math.sqrt(sum(v * v for v in d))
        if L < 1e-6:
            samples[i]["W_ok"] = True
            continue
        sw = sweep(q, args.body_radius, (a[0], a[1], a[2] + half),
                   (d[0] / L, d[1] / L, d[2] / L), L)
        ok = ("unavailable" in sw) or (not sw.get("hit")) or float(sw.get("distance", L)) >= L - 1e-3
        samples[i]["W_ok"] = bool(ok)
        if not ok:
            blocked.append({"i": i, "seg": seg[i], "hit": sw})
    samples[-1]["W_ok"] = True

    # Brief 7.8/acceptance gate 13 is a 0.25 m *vertical* CCD landing test.
    # A free body-sized capsule on a straight flight instead measures whether
    # unactuated rigid body rolls down stairs.  That is neither locomotion nor
    # landing stability, and it makes straight stairs fail while U stairs pass
    # merely because their midpoint happens to be a wide turning platform.
    # Worse, a 0.22 m radius spans adjacent treads whose pitch is about 0.274 m,
    # so it bridges on the upper nosing instead of landing on the sampled tread.
    # Use a dedicated 0.08 m spherical CCD probe that fits within one tread and
    # constrain it to vertical translation, as a drop-test fixture would, while
    # keeping gravity, CCD, real contact geometry, and a fail-capable Z error
    # check. Support/headroom/full-body sweep remain separate gates at the full
    # 0.22 m radius and 1.75 m body height.
    drop_height_m = DYNAMIC_DROP_HEIGHT_M
    drop_probe_radius_m = DYNAMIC_DROP_PROBE_RADIUS_M
    locked_pos_axis = 3  # X | Y; Z remains dynamic.
    locked_rot_axis = 7  # X | Y | Z; upright body proxy cannot roll.
    final_height_tolerance_m = DYNAMIC_FINAL_HEIGHT_TOLERANCE_M
    late_settle_tolerance_m = DYNAMIC_LATE_SETTLE_TOLERANCE_M
    settles = {}
    for nm, idx in (("floor1_start", 0), ("mid_stair", len(approach) + len(sp) // 2),
                    ("floor2_end", len(path) - 1)):
        p = path[idx]
        cap = UsdGeom.Sphere.Define(stage, "/Probe/" + nm)
        cap.CreateRadiusAttr(drop_probe_radius_m)
        support_dz = samples[idx]["support_dz"]
        if support_dz is None:
            # Still author the probe so the test remains diagnostic, but make
            # the expected surface impossible to satisfy and fail closed.
            expected = p[2] + drop_probe_radius_m
        else:
            expected = p[2] + float(support_dz) + drop_probe_radius_m
        sz = expected + drop_height_m
        UsdGeom.Xformable(cap.GetPrim()).AddTranslateOp().Set(Gf.Vec3d(p[0], p[1], sz))
        UsdPhysics.CollisionAPI.Apply(cap.GetPrim())
        UsdPhysics.RigidBodyAPI.Apply(cap.GetPrim())
        UsdPhysics.MassAPI.Apply(cap.GetPrim()).CreateMassAttr(75.0)
        physx_body = PhysxSchema.PhysxRigidBodyAPI.Apply(cap.GetPrim())
        physx_body.CreateEnableCCDAttr(True)
        physx_body.CreateLockedPosAxisAttr(locked_pos_axis)
        physx_body.CreateLockedRotAxisAttr(locked_rot_axis)
        configuration_verified = bool(
            physx_body.GetEnableCCDAttr().Get()
            and int(physx_body.GetLockedPosAxisAttr().Get()) == locked_pos_axis
            and int(physx_body.GetLockedRotAxisAttr().Get()) == locked_rot_axis
        )
        updates(180)
        e1 = UsdGeom.XformCache(
            Usd.TimeCode.Default()).GetLocalToWorldTransform(
                cap.GetPrim()).ExtractTranslation()
        updates(100)
        e2 = UsdGeom.XformCache(
            Usd.TimeCode.Default()).GetLocalToWorldTransform(
                cap.GetPrim()).ExtractTranslation()
        drift = math.hypot(float(e2[0]) - p[0], float(e2[1]) - p[1])
        late_delta = math.sqrt(sum(
            (float(e2[axis]) - float(e1[axis])) ** 2 for axis in range(3)
        ))
        vertical_error = abs(float(e2[2]) - expected)
        settles[nm] = {
            "start_z": round(sz, 4),
            "end_z": round(float(e2[2]), 4),
            "expected_surface_supported_center_z": round(expected, 4),
            "observed_drop_m": round(sz - float(e2[2]), 4),
            "vertical_error_m": round(vertical_error, 4),
            "horizontal_drift_m": round(drift, 4),
            "late_pose_delta_m": round(late_delta, 6),
            "ccd_enabled": bool(physx_body.GetEnableCCDAttr().Get()),
            "locked_pos_axis_mask": int(physx_body.GetLockedPosAxisAttr().Get()),
            "locked_rot_axis_mask": int(physx_body.GetLockedRotAxisAttr().Get()),
            "configuration_verified": configuration_verified,
            "ok": vertical_ccd_drop_pass(
                configuration_verified=configuration_verified,
                support_present=support_dz is not None,
                end_z=float(e2[2]),
                expected_z=expected,
                horizontal_drift_m=drift,
                late_pose_delta_m=late_delta,
            ),
        }
        UsdGeom.Imageable(cap.GetPrim()).MakeInvisible()
    omni.timeline.get_timeline_interface().stop()
    updates(30)

    R["samples"] = samples
    landing_sample_index = len(approach) + len(sp) - 1
    landing_sample = samples[landing_sample_index]
    R["floor2_final_landing_support"] = {
        "sample_index": landing_sample_index,
        "xyz": landing_sample["xyz"],
        "support_prim": landing_sample["support_prim"],
        "support_dz": landing_sample["support_dz"],
        "stair_did_not_supply_final_landing": (
            "/World/Stair" not in str(landing_sample["support_prim"])
        ),
        "upper_floor_supplied_final_landing": (
            "/World/Cut/" in str(landing_sample["support_prim"])
            or "/World/Floor2/" in str(landing_sample["support_prim"])
        ),
    }
    R["floor2_final_landing_support"]["status"] = (
        "pass" if (
            landing_sample["S_ok"]
            and R["floor2_final_landing_support"]["stair_did_not_supply_final_landing"]
            and R["floor2_final_landing_support"]["upper_floor_supplied_final_landing"]
        ) else "fail"
    )
    R["S_all_ok"] = all(s["S_ok"] for s in samples)
    R["H_all_ok"] = all(s["H_ok"] for s in samples)
    R["W_all_ok"] = all(s["W_ok"] for s in samples)
    R["D_all_ok"] = all(v["ok"] for v in settles.values())
    R["dynamic_drop_contract"] = {
        "kind": "vertical_CCD_drop_fixture_NOT_passive_stair_locomotion",
        "probe_shape": "UsdGeomSphere",
        "probe_radius_m": drop_probe_radius_m,
        "full_body_radius_m_for_independent_sweep": args.body_radius,
        "full_body_height_m_for_independent_headroom": args.body_height,
        "drop_height_m": drop_height_m,
        "locked_position_axes": ["X", "Y"],
        "free_position_axis": "Z",
        "locked_rotation_axes": ["X", "Y", "Z"],
        "final_height_tolerance_m": final_height_tolerance_m,
        "late_settle_window_updates": 100,
        "late_settle_tolerance_m": late_settle_tolerance_m,
        "all_probe_configurations_verified": all(
            value["configuration_verified"] for value in settles.values()
        ),
        "failure_capability": (
            "A missing or tunneled collider falls below the ray-derived "
            "supported center Z and fails the 0.03 m final-height gate."
        ),
    }
    R["dynamic_settle"] = settles
    R["unsupported"] = [s for s in samples if not s["S_ok"]]
    R["W_blocked"] = blocked
    R["min_headroom_m"] = round(min(s["headroom_m"] for s in samples), 4)
    R["climbed_m"] = round(path[-1][2] - path[0][2], 4)
    R["traversable"] = bool(R["S_all_ok"] and R["H_all_ok"] and R["W_all_ok"] and R["D_all_ok"])

    # ---------------- review renders
    # Define an Imageable parent explicitly.  Defining only child spheres leaves
    # an untyped ancestor whose visibility Hydra does not reliably inherit.
    pathviz_xform = UsdGeom.Xform.Define(stage, "/PathViz")
    for i, p in enumerate(path):
        s = UsdGeom.Sphere.Define(stage, "/PathViz/p{}".format(i))
        s.CreateRadiusAttr(0.075)
        s.CreateDisplayColorAttr([Gf.Vec3f(*{"floor1": (0.15, 0.5, 0.95),
                                             "stair": (0.95, 0.65, 0.1),
                                             "floor2_landing": (0.55, 0.9, 0.25),
                                             "floor2": (0.2, 0.8, 0.35)}[seg[i]])])
        UsdGeom.Xformable(s.GetPrim()).AddTranslateOp().Set(Gf.Vec3d(p[0], p[1], p[2] + 0.09))
    viz = pathviz_xform.GetPrim()

    dome = UsdLux.DomeLight.Define(stage, "/Rev/Dome"); dome.CreateIntensityAttr(1000.0)
    dome.CreateColorAttr(Gf.Vec3f(0.5, 0.5, 0.5))
    k = UsdLux.DistantLight.Define(stage, "/Rev/Key"); k.CreateIntensityAttr(2400.0)
    k.CreateColorAttr(Gf.Vec3f(1.0, 1.0, 1.0))
    k.CreateAngleAttr(2.0)
    UsdGeom.Xformable(k.GetPrim()).AddRotateXYZOp().Set(Gf.Vec3f(-48.0, 0.0, 32.0))
    fl = UsdLux.SphereLight.Define(stage, "/Rev/Fill"); fl.CreateIntensityAttr(90000.0)
    fl.CreateColorAttr(Gf.Vec3f(1.0, 1.0, 1.0))
    fl.CreateRadiusAttr(0.5)
    UsdGeom.Xformable(fl.GetPrim()).AddTranslateOp().Set(Gf.Vec3d(cx, cy, f2f - 0.5))

    def set_hidden(root, prefixes, hide):
        n = 0
        rp = stage.GetPrimAtPath(root)
        if not rp or not rp.IsValid():
            return 0
        for prim in Usd.PrimRange(rp):
            # Some envelope assets (notably windows) are referenced Xform
            # subtrees whose child mesh names no longer carry the scene-level
            # ``window_`` prefix. Author visibility on the matching root so the
            # full referenced subtree participates in the cutaway.
            if any(prim.GetName().startswith(x) for x in prefixes):
                img = UsdGeom.Imageable(prim)
                if img:
                    img.MakeInvisible() if hide else img.MakeVisible()
                    n += 1
        return n

    cam = UsdGeom.Camera.Define(stage, "/Rev/Cam")
    cam.CreateFocalLengthAttr(24.0)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.05, 5000.0))
    op = UsdGeom.Xformable(cam.GetPrim()).AddTransformOp()
    vp = get_active_viewport()
    vp.camera_path = cam.GetPath()
    vp.resolution = (args.width, args.height)
    st = carb.settings.get_settings()
    st.set_string("/rtx/rendermode", "PathTracing")
    st.set_int("/rtx/pathtracing/spp", 32)
    st.set_int("/rtx/pathtracing/totalSpp", 280)
    st.set_int("/rtx/pathtracing/maxBounces", 6)
    updates(400)

    camera_poses = {}

    def shoot(name, eye, tgt, frames=280, render_kind="render"):
        op.Set(look_at(eye, tgt))
        updates(frames)
        d = args.out_dir / (name + ".png")
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            d.unlink()
        hh = capture_viewport_to_file(vp, file_path=str(d.resolve()))
        t = asyncio.ensure_future(hh.wait_for_result())
        dl = time.time() + 500
        while not t.done() and time.time() < dl:
            APP.update()
        settle_file(d)
        from PIL import Image, ImageStat
        with Image.open(d) as im:
            rgb = im.convert("RGB")
            stt = ImageStat.Stat(rgb)
            width, height = rgb.size
            central = rgb.crop((int(width * 0.20), int(height * 0.15),
                                int(width * 0.80), int(height * 0.85)))
            central_stat = ImageStat.Stat(central)
            pixels = list(central.getdata())
            exact_black = sum(1 for pixel in pixels if pixel == (0, 0, 0))
        record = {
            "file": d.name,
            "kind": render_kind,
            "mean_rgb": [round(v, 1) for v in stt.mean],
            "central_mean_rgb": [round(v, 1) for v in central_stat.mean],
            "central_exact_black_pct": round(100.0 * exact_black / len(pixels), 3),
            "bytes": d.stat().st_size,
        }
        if render_kind == "collision":
            record["gray_mean_channel_spread"] = round(
                max(central_stat.mean) - min(central_stat.mean), 3)
        R.setdefault("renders", {})[name] = record
        pose_name = name.rsplit("_", 1)[0]
        pose = {
            "eye": [float(v) for v in eye],
            "target": [float(v) for v in tgt],
        }
        previous = camera_poses.get(pose_name)
        if previous is not None and previous != pose:
            raise RuntimeError(
                "render/collision camera pose drift for {}: {} != {}".format(
                    pose_name, previous, pose
                )
            )
        camera_poses[pose_name] = pose

    def around(dx, dy, z):
        rx, ry = rotate_xy((dx, dy), rotation_deg)
        return Gf.Vec3d(cx + rx, cy + ry, z)

    # Priority order is intentional: placement approval needs the actual
    # arrival, stairwell, and top-down cutaway before secondary review views.
    # Filenames keep the fixed 01..05 contract regardless of capture order.
    views = [
        # Stand at the actual arrival point and face in the stair's exit
        # direction.  This deliberately exposes the wall/open-space choice the
        # reviewer must judge instead of using a flattering exterior angle.
        ("03_arrival", Gf.Vec3d(*arrival_eye_xyz),
         Gf.Vec3d(arrival_eye_xyz[0] + ex[0] * 2.0,
                   arrival_eye_xyz[1] + ex[1] * 2.0, f2f + 1.48)),
        ("02_stairwell", around(7.5, -1.0, f2f * 0.62),
         Gf.Vec3d(cx, cy, f2f * 0.45)),
        ("05_topdown", Gf.Vec3d(cx, cy, f2f + 16.0),
         Gf.Vec3d(cx, cy, f2f * 0.5)),
        ("01_overview", around(11.0, -11.0, f2f + 9.0),
         Gf.Vec3d(cx, cy, f2f * 0.5)),
        ("04_eyelevel", Gf.Vec3d(path[0][0], path[0][1], 1.62),
         Gf.Vec3d(sp[len(sp) // 3][0], sp[len(sp) // 3][1], 1.9)),
    ]

    def configure_beauty_view(name):
        # Reset every envelope class touched by any preceding cutaway. Views
        # are captured in priority order, so missing this reset makes the 02
        # window cut leak into 05 and turns render output into order-dependent
        # evidence.
        set_hidden("/World/Floor1", ("wall_", "ceiling_", "window_"), False)
        set_hidden("/World/Floor2", ("wall_", "ceiling_", "window_"), False)
        if name == "01_overview":
            set_hidden("/World/Floor2", ("wall_", "ceiling_"), True)
        elif name == "02_stairwell":
            # The section camera is outside the room on the stair's broadside.
            # Without a cutaway it records only the exterior wall and provides
            # no evidence about the stair, opening, or upper-floor landing.
            # Keep all furniture and floor slabs visible, but remove enclosing
            # walls/ceilings from both source floors for this evidence frame.
            set_hidden("/World/Floor1", ("wall_", "ceiling_", "window_"), True)
            set_hidden("/World/Floor2", ("wall_", "ceiling_", "window_"), True)
        elif name == "05_topdown":
            set_hidden("/World/Floor2", ("ceiling_",), True)

    # Five beauty frames first.  Collision material overrides are applied only
    # afterward, so no gray session binding can contaminate a beauty frame.
    UsdGeom.Imageable(viz).MakeInvisible()
    for name, eye, tgt in views:
        configure_beauty_view(name)
        updates(90)
        shoot(name + "_render", eye, tgt, render_kind="render")

    def has_collision_ancestor(prim):
        current = prim
        while current and current.IsValid() and str(current.GetPath()) != "/":
            if current.HasAPI(UsdPhysics.CollisionAPI):
                return True
            current = current.GetParent()
        return False

    world_gprims = [prim for prim in Usd.PrimRange(stage.GetPrimAtPath("/World"))
                    if prim.IsA(UsdGeom.Gprim) and prim.IsActive()]
    collision_gprims = [prim for prim in world_gprims if has_collision_ancestor(prim)]
    collision_paths = {str(prim.GetPath()) for prim in collision_gprims}
    if not collision_gprims:
        raise RuntimeError("no renderable building collision geometry found")
    gray = make_gray_material(stage)
    for prim in world_gprims:
        imageable = UsdGeom.Imageable(prim)
        if str(prim.GetPath()) not in collision_paths:
            imageable.MakeInvisible()
            continue
        # Collision meshes are commonly purpose=guide and explicitly hidden.
        # The session layer exposes them as opaque volume for review only.
        imageable.MakeVisible()
        imageable.CreatePurposeAttr(UsdGeom.Tokens.default_)
        gprim = UsdGeom.Gprim(prim)
        gprim.CreateDisplayColorAttr([Gf.Vec3f(0.42, 0.42, 0.42)])
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(
            gray, bindingStrength=UsdShade.Tokens.strongerThanDescendants)
        parent = prim.GetParent()
        while parent and parent.IsValid() and str(parent.GetPath()).startswith("/World"):
            parent_imageable = UsdGeom.Imageable(parent)
            if parent_imageable:
                parent_imageable.MakeVisible()
            parent = parent.GetParent()
    R["collision_gray_geometry"] = {
        "collision_api_prims": sum(
            1 for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.CollisionAPI)),
        "renderable_gprims": len(collision_gprims),
        "material_rgb": [0.42, 0.42, 0.42],
        "roughness": 0.82,
        "mode": "opaque_gray_volume_not_debug_wireframe",
    }

    # Beauty capture is complete.  Collision evidence must now be isolated
    # from every composed source light/background, including non-Gprims, and
    # from the colored traversal markers.  The helper enumerates LightAPI
    # rather than assuming the two source paths seen in the current batch.
    collision_isolation = isolate_collision_render(stage)
    R["collision_render_isolation"] = {
        **collision_isolation,
        "assertions_before_each_frame": {},
        "background": "constant neutral untextured /Rev/Dome",
        "only_allowed_lights": ["/Rev/Dome", "/Rev/Key", "/Rev/Fill"],
    }

    def set_collision_hidden(root, prefixes, hide):
        rp = stage.GetPrimAtPath(root)
        if not rp or not rp.IsValid():
            return
        for prim in Usd.PrimRange(rp):
            ancestor = prim
            prefix_match = False
            while ancestor and ancestor.IsValid() and str(ancestor.GetPath()).startswith(root):
                if any(ancestor.GetName().startswith(p) for p in prefixes):
                    prefix_match = True
                    break
                ancestor = ancestor.GetParent()
            if str(prim.GetPath()) in collision_paths and prefix_match:
                imageable = UsdGeom.Imageable(prim)
                imageable.MakeInvisible() if hide else imageable.MakeVisible()

    def configure_collision_view(name):
        set_collision_hidden("/World/Floor1", ("wall_", "ceiling_", "window_"), False)
        set_collision_hidden("/World/Floor2", ("wall_", "ceiling_", "window_"), False)
        if name == "01_overview":
            set_collision_hidden("/World/Floor2", ("wall_", "ceiling_"), True)
        elif name == "02_stairwell":
            # Match the beauty-frame cutaway so the gray pair exposes the
            # physical stair/opening/landing volumes instead of the side wall.
            set_collision_hidden("/World/Floor1", ("wall_", "ceiling_", "window_"), True)
            set_collision_hidden("/World/Floor2", ("wall_", "ceiling_", "window_"), True)
        elif name == "05_topdown":
            set_collision_hidden("/World/Floor2", ("ceiling_",), True)

    updates(180)
    for name, eye, tgt in views:
        configure_collision_view(name)
        # Fail before capture rather than silently writing invalid evidence.
        R["collision_render_isolation"]["assertions_before_each_frame"][name] = (
            assert_collision_render_isolated(stage)
        )
        updates(90)
        shoot(name + "_collision", eye, tgt, render_kind="collision")

    expected = [name + suffix for name, _, _ in views
                for suffix in ("_render", "_collision")]
    R["camera_poses"] = camera_poses
    R["camera_pose_contract"] = CAMERA_POSE_CONTRACT
    R["camera_pose_contract_sha256"] = CAMERA_POSE_CONTRACT_SHA256
    R["camera_pair_pose_consistency"] = (
        set(camera_poses) == set(CAMERA_POSE_CONTRACT["views"])
    )
    R["render_cutaways"] = {
        "02_stairwell": {
            "floor1_hidden": ["wall_visual", "ceiling_visual", "window_subtrees"],
            "floor2_hidden": ["wall_visual", "ceiling_visual", "window_subtrees"],
            "preserved": ["furniture", "floor_slabs", "stair", "opening", "floor2_landing"],
            "purpose": "broadside stair/opening/upper-floor-landing evidence",
        },
        "05_topdown": {
            "floor2_hidden": ["ceiling_visual"],
            "purpose": "top-down cutaway evidence",
        },
    }
    R["render_pairs_complete"] = all(name in R["renders"] for name in expected)
    eye_render = R["renders"].get("04_eyelevel_render", {})
    R["render_nonblack_warm"] = bool(
        eye_render.get("central_exact_black_pct", 100.0) < 20.0
        and eye_render.get("central_mean_rgb", [0, 0, 0])[0]
        > eye_render.get("central_mean_rgb", [0, 0, 0])[2])
    gray_frames = [R["renders"][name] for name in expected if name.endswith("_collision")]
    R["collision_frames_gray"] = all(
        frame.get("gray_mean_channel_spread", 999.0) <= 12.0 for frame in gray_frames)
    R["acceptance_gates"] = {
        "C3_hole_open": R["C3_hole_open"],
        "C3_four_sides_blocked": R["C3_beside_blocked"],
        "C3_down_lands_on_stair": R["C3_down_lands_on_stair"],
        "traversable": R["traversable"],
        "floor2_final_landing_support": (
            R["floor2_final_landing_support"]["status"] == "pass"
        ),
        "build_no_clipping_walls": R["build_cpu_gates"]["no_clipping_walls"],
        "build_no_clipping_furniture": R["build_cpu_gates"]["no_clipping_furniture"],
        "build_upper_floor_is_final_landing": R["build_cpu_gates"][
            "upper_floor_is_final_landing"
        ],
        "build_opening_exit_aligned_to_final_riser": R["build_cpu_gates"][
            "opening_exit_aligned_to_final_riser"
        ],
        "build_final_landing_is_on_floor_side": R["build_cpu_gates"][
            "final_landing_is_on_floor_side_of_opening"
        ],
        "build_approach_corridor_clear": R["build_cpu_gates"][
            "approach_corridor_clear"
        ],
        "build_egress_corridor_clear": R["build_cpu_gates"][
            "egress_corridor_clear"
        ],
        "sources_unmodified": R["build_cpu_gates"]["sources_unmodified"],
        "render_pairs_complete": R["render_pairs_complete"],
        "render_nonblack_warm": R["render_nonblack_warm"],
        "collision_frames_gray": R["collision_frames_gray"],
    }
    R["machine_status"] = (
        "pass" if all(R["acceptance_gates"].values()) else "fail")
    R["status"] = ("awaiting_human_review_view03"
                   if R["machine_status"] == "pass" else "fail")


_validation_failed = False
try:
    main()
except Exception as exc:  # noqa: BLE001
    import traceback
    _validation_failed = True
    R["status"] = "fail"
    R["machine_status"] = "fail"
    R["error"] = "{}: {}".format(type(exc).__name__, exc)
    R["traceback"] = traceback.format_exc()
finally:
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(R, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in R.items()
                      if k not in ("samples", "traceback", "unsupported")}, indent=2))
    APP.close()

if _validation_failed:
    raise SystemExit(1)
