"""Gate 6 - can the robot actually get to the upper storey?

Gates 1-5 prove the geometry is well formed, consistent with the cache, and
loadable at runtime.  None of them asks the one question training depends on:
starting on the ground floor, can ProtoMotions' own robot proxy climb the
stair?

Two independent measurements, because either alone can lie.

A. AUTHORED GEOMETRY, exact, from the composed USD.
   The walking surfaces of the flight are the tread plates' upper facets.  The
   rises that matter are ground -> first tread, tread -> tread, and last tread
   -> upper slab.  All three must be <= StandabilityConfig.max_support_step_m.
   This measurement has no voxel quantisation in it.

B. BAKED OCCUPANCY, the same connectivity rule the repo uses.
   standability._filter_small_components walks CARDINAL_NEIGHBORS and refuses a
   neighbour whose support row differs by more than max_support_step_m.  This
   reproduces that walk over the 5 cm cache and asks whether a flood started on
   the ground floor reaches the upper slab.

   The buildings carry no Floor-1 slab collider - the simulator's terrain is
   the ground floor (the object-playground region is always flat at z=0), so
   the terrain plane is put back before flooding.  It is rasterised the way the
   baker's conservative splat would rasterise it: every voxel the half-space
   z <= 0 touches.  Anchoring it to the grid's bottom row instead would float
   the ground wherever the grid happens to start.

C. CONTROLS, so the gate can fail.
   1. The pre-fix caches, if the backup is present.  Real data, the exact
      defect: the first tread plate was authored with its underside one riser
      above the floor, so the first walking surface sat one plate-thickness
      (0.0615 m) too high and the flight was unreachable.  Must be unreachable.
   2. A sweep that lowers the terrain under the fixed building by 0.05, 0.10
      and 0.15 m, raising the first rise by the same amount.  By 0.15 m - a
      first rise of at least 0.32 m - the flood must have failed.

   The sweep is also the honest statement of what B can and cannot resolve.  At
   an offset of 0.0615 m the first rise is 0.228-0.242 m, one voxel over the
   limit, and whether the flood blocks depends on where the 5 cm grid
   boundaries happen to fall: 2_u passes the flood at that offset even though
   the authored rise is over the limit.  That is why A, which has no
   quantisation in it, is the primary criterion and B corroborates topology.

Read-only.  Writes evidence/gate6_stair_reachability.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ProtoMotions-main"))

import numpy as np
from scipy import ndimage
from pxr import Usd, UsdGeom

from protomotions.simulator.base_simulator.scene_geometry_cache import (
    load_scene_geometry_cache,
)
from protomotions.simulator.base_simulator.standability import StandabilityConfig
from protomotions.simulator.isaaclab.utils.scene_geometry_baking import (
    _authored_mesh_geometry_m,
)

CACHES = HERE.parent / "voxel_bake" / "caches"
PREFIX_CACHES = HERE.parent / "voxel_bake" / "caches_backup_prestepfix"
PATCHED = HERE.parent / "voxel_bake" / "patched"
OUT = HERE / "evidence"
NAMES = ["1_l", "2_u", "3_s300", "4_s270"]

VOXEL = 0.05
CFG = StandabilityConfig()
R, H, STEP = CFG.body_radius_m, CFG.body_height_m, CFG.max_support_step_m
KSTEP = int(round(STEP / VOXEL))
TORSO_H, SHIN_H, PARTIAL_H = 0.90, 0.30, 0.50
TREAD = "/World/Stair/world/collision/integrated_tread_collision"
GROUND_Z = 0.0                      # the simulator's flat terrain
PLATE_THICKNESS_M = 0.0615          # the defect this gate exists to catch


# ---------------------------------------------------------------- A. authored

def authored_rises(name):
    stage = Usd.Stage.Open(str(PATCHED / f"{name}_physics_fixed.usda"))
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    xc = UsdGeom.XformCache(Usd.TimeCode.Default())
    prim = stage.GetPrimAtPath(TREAD)
    v, f = _authored_mesh_geometry_m(stage, prim, mpu, xc)
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1)
    keep = ln > 0
    nz = np.zeros(len(n))
    nz[keep] = n[keep, 2] / ln[keep]
    levels = np.unique(np.round(tri[np.abs(nz) > 0.9][:, :, 2].mean(axis=1), 6))
    tops = levels[1::2]

    slab = -np.inf
    for child in ("Floor2Slab_0", "Floor2CollisionPlane"):
        p = stage.GetPrimAtPath(f"/World/Cut/{child}")
        if p and p.IsValid():
            vv, _ = _authored_mesh_geometry_m(stage, p, mpu, xc)
            slab = max(slab, float(vv[:, 2].max()))

    first = float(tops[0]) - GROUND_Z
    inner = np.diff(tops)
    last = slab - float(tops[-1])
    rises = np.concatenate([[first], inner, [last]])
    return {
        "treads": int(tops.size),
        "upper_slab_top_m": round(float(slab), 4),
        "first_rise_m": round(first, 4),
        "median_inner_riser_m": round(float(np.median(inner)), 4),
        "max_inner_riser_m": round(float(inner.max()), 4),
        "last_rise_m": round(last, 4),
        "max_rise_m": round(float(rises.max()), 4),
        "limit_m": STEP,
        "rises_over_limit": int((rises > STEP + 1e-9).sum()),
        "passes": bool(rises.max() <= STEP + 1e-9),
    }


# ------------------------------------------------------------------ B. flooded

def _standable(occ, lo, ground_z):
    nx, ny, nz = occ.shape
    zc = lo[2] + (np.arange(nz) + 0.5) * VOXEL
    occ = occ.copy()
    occ[:, :, (zc - VOXEL / 2.0) <= ground_z + 1e-9] = True
    horiz = np.empty(occ.shape, np.float32)
    for k in range(nz):
        horiz[:, :, k] = ndimage.distance_transform_edt(
            ~occ[:, :, k], sampling=VOXEL).astype(np.float32)
    run = np.zeros((nx, ny), np.float32)
    above = np.zeros(occ.shape, np.float32)
    for k in range(nz - 1, -1, -1):
        run = np.where(~occ[:, :, k], run + VOXEL, 0.0)
        above[:, :, k] = run
    headroom = np.zeros(occ.shape, np.float32)
    headroom[:, :, :-1] = above[:, :, 1:]
    sup = np.zeros(occ.shape, bool)
    sup[:, :, :-1] = occ[:, :, :-1] & ~occ[:, :, 1:]
    ki = np.arange(nz)
    kt = np.minimum(ki + int(round(TORSO_H / VOXEL)), nz - 1)
    ks = np.minimum(ki + int(round(SHIN_H / VOXEL)), nz - 1)
    wide = (horiz[:, :, kt] >= R - 1e-6) & (horiz[:, :, ks] >= R - 1e-6)
    standable = sup & wide & (headroom >= H - 1e-6)
    partial = sup & wide & (headroom >= PARTIAL_H) & (headroom < H - 1e-6)
    gk = int(np.nonzero((zc - VOXEL / 2.0) <= ground_z + 1e-9)[0].max())
    return standable, partial, headroom, zc, gk


def _step_structure():
    st = np.zeros((3, 3, 2 * KSTEP + 1), bool)
    st[1, 1, :] = True
    st[0, 1, :] = True
    st[2, 1, :] = True
    st[1, 0, :] = True
    st[1, 2, :] = True
    return st


def _flood(nodes, seed):
    return ndimage.binary_dilation(
        seed, structure=_step_structure(), mask=nodes, iterations=-1)


def reachability(cache_dir, name, ground_z=GROUND_Z):
    loaded = load_scene_geometry_cache(
        cache_dir / f"{name}_fixed.scene_geometry_v1", verify_hashes=False)
    occ = np.array(loaded.occupancy, dtype=bool, copy=True)
    lo = np.asarray(loaded.manifest["grid"]["scene_bounds_min_m"], float)
    if ground_z < lo[2]:
        # the control lowered the terrain below the baked grid: extend the grid
        # downwards with free rows so the plane is still representable
        pad = int(np.ceil((lo[2] - ground_z) / VOXEL)) + 1
        occ = np.concatenate(
            [np.zeros(occ.shape[:2] + (pad,), bool), occ], axis=2)
        lo = lo.copy()
        lo[2] -= pad * VOXEL
    standable, partial, headroom, zc, gk = _standable(occ, lo, ground_z)
    seed = np.zeros_like(standable)
    seed[:, :, gk] = standable[:, :, gk]
    reach = _flood(standable, seed)
    if not reach.any():
        return {"error": "no standable ground-floor cell", "upper_storey_reachable": False}
    per = reach.sum(axis=(0, 1))
    peaks = []
    for k in np.argsort(per)[::-1]:
        if per[k] == 0:
            break
        if all(abs(int(k) - p) > 10 for p in peaks):
            peaks.append(int(k))
        if len(peaks) == 2:
            break
    peaks.sort()
    upper = bool(len(peaks) == 2 and zc[peaks[1]] - zc[peaks[0]] > 1.5)
    bump = ndimage.binary_dilation(reach, structure=_step_structure()) & partial
    return {
        "ground_plane_z_m": ground_z,
        "reachable_area_m2": round(float(reach.sum()) * VOXEL * VOXEL, 2),
        "storey_support_z_m": [round(float(zc[k]), 3) for k in peaks],
        "storey_area_m2": [round(float(per[k]) * VOXEL * VOXEL, 2) for k in peaks],
        "upper_storey_reachable": upper,
        "head_bump_area_m2": round(float(bump.sum()) * VOXEL * VOXEL, 3),
        "min_head_bump_headroom_m": (
            round(float(headroom[bump].min()), 2) if bump.any() else None),
        "max_support_step_m": STEP,
        "body_radius_m": R,
        "body_height_m": H,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "_robot_model": {
            "source": "standability.StandabilityConfig",
            "body_radius_m": R, "body_height_m": H, "max_support_step_m": STEP,
        },
        "_ground_plane_note": "simulator terrain, flat at z=0",
    }
    ok = True
    for name in NAMES:
        a = authored_rises(name)
        b = reachability(CACHES, name)
        sweep = {
            f"{off:.4f}": reachability(CACHES, name, ground_z=-off)
            for off in (PLATE_THICKNESS_M, 0.05, 0.10, 0.15)
        }
        entry = {
            "authored_rises": a,
            "reachable": b,
            "control_terrain_lowered_sweep": sweep,
        }
        controls_fail = not sweep[f"{0.15:.4f}"].get("upper_storey_reachable")
        if PREFIX_CACHES.is_dir():
            d = reachability(PREFIX_CACHES, name)
            entry["control_prefix_cache"] = d
            controls_fail = controls_fail and not d.get("upper_storey_reachable")
        entry["controls_correctly_fail"] = bool(controls_fail)
        entry["GATE6"] = "PASS" if (
            a["passes"] and b.get("upper_storey_reachable") and controls_fail
        ) else "FAIL"
        ok = ok and entry["GATE6"] == "PASS"
        report[name] = entry
        blocked_at = next(
            (k for k in sorted(sweep, key=float)
             if not sweep[k].get("upper_storey_reachable")), None)
        entry["flood_first_blocks_at_offset_m"] = (
            float(blocked_at) if blocked_at else None)
        print(f"{name:8} max rise {a['max_rise_m']:.4f} (limit {STEP})  "
              f"over={a['rises_over_limit']}   "
              f"reachable={b.get('upper_storey_reachable')} "
              f"{b.get('reachable_area_m2')} m2  "
              f"storeys {b.get('storey_support_z_m')}  "
              f"flood blocks at offset {blocked_at}  -> {entry['GATE6']}")
    report["_overall"] = "PASS" if ok else "FAIL"
    (OUT / "gate6_stair_reachability.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nGATE 6 overall: {report['_overall']}")
    print("wrote", OUT / "gate6_stair_reachability.json")


if __name__ == "__main__":
    main()
