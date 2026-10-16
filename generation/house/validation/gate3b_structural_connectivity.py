"""GATE 3b - structural connectivity. Third formulation; the first two are kept
in the evidence folder because each failed for an instructive reason.

Attempt 1 (inside gate3_usd_voxel_consistency.py) labelled navigable space over
the whole grid. These buildings have no roof collider, so interior free space
joins the outside over the wall tops and both storeys connect through the
exterior whether or not the stairwell is open. That test could not fail.

Attempt 2 confined the analysis to the filled XY projection of the Floor 2
slab. Measured by gate3c: the walls extend up to 7 m BEYOND that slab, because
part of every building is a double-height space with no second floor. The mask
therefore clipped the building and severed a corridor, inventing 41-77 m3
"sealed rooms" that do not exist. Re-running with a wall-derived footprint
leaves exactly one region above 1 m3 per storey.

This version:

  * takes the building envelope from the WALLS, not the slab, and asks whether
    any room is unreachable;
  * tests the stairwell directly, by finding every vertical passage through the
    slab plane inside the slab's own footprint. A hole in the slab is the only
    way to cross there. The teeth: each opening found must coincide with the
    staircase footprint, and plugging them must leave zero crossings.

Read-only. Writes evidence/gate3b_structural_connectivity.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "ProtoMotions-main"))

import numpy as np
from scipy import ndimage
from pxr import Usd, UsdGeom, UsdPhysics

from protomotions.simulator.base_simulator.scene_geometry_cache import (
    load_scene_geometry_cache,
)
from protomotions.simulator.isaaclab.utils.scene_geometry_baking import (
    _authored_mesh_geometry_m,
)

PATCHED = HERE.parent / "voxel_bake" / "patched"
CACHES = HERE.parent / "voxel_bake" / "caches"
OUT = HERE / "evidence"
NAMES = ["1_l", "2_u", "3_s300", "4_s270"]
VOXEL = 0.05
CLEARANCE_M = 0.15
MIN_ROOM_M3 = 1.0
MIN_ROOM_CELLS = int(round(MIN_ROOM_M3 / VOXEL ** 3))
MIN_OPENING_M2 = 0.25


def collider_extents(name):
    stage = Usd.Stage.Open(str(PATCHED / (name + "_physics_fixed.usda")))
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    xc = UsdGeom.XformCache(Usd.TimeCode.Default())
    acc = {}

    def add(key, v):
        a, b = acc.get(key, (np.full(3, np.inf), np.full(3, -np.inf)))
        acc[key] = (np.minimum(a, v.min(axis=0)), np.maximum(b, v.max(axis=0)))

    for prim in stage.Traverse():
        p = str(prim.GetPath())
        if not prim.IsA(UsdGeom.Mesh) or not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        try:
            v, _ = _authored_mesh_geometry_m(stage, prim, mpu, xc)
        except Exception:
            continue
        if p.startswith("/World/Stair"):
            add("stair", v)
        if "/World/Cut/Floor2Slab" in p:
            add("slab2", v)
        if "wall" in p.lower():
            add("wall", v)
    return acc


def analyse(name):
    loaded = load_scene_geometry_cache(
        CACHES / (name + "_fixed.scene_geometry_v1"), verify_hashes=True)
    occ = np.ascontiguousarray(loaded.occupancy)
    clear = np.asarray(loaded.signed_clearance_fine).astype(np.float32)
    grid = loaded.manifest["grid"]
    lo = np.asarray(grid["scene_bounds_min_m"], float)
    nx, ny, nz = occ.shape
    zs = lo[2] + (np.arange(nz) + 0.5) * VOXEL

    acc = collider_extents(name)
    slab_lo, slab_hi = acc["slab2"]
    stair_lo, stair_hi = acc["stair"]
    wall_top = float(acc["wall"][1][2])
    navigable = clear >= CLEARANCE_M

    # --- building envelope from the WALLS (see module docstring) --------------
    band1 = (zs >= lo[2] + 0.30) & (zs <= float(slab_lo[2]) - 0.10)
    wall_fp = ndimage.binary_erosion(
        ndimage.binary_fill_holes(occ[:, :, band1].any(axis=2)), iterations=1)

    # --- per-storey room reachability, each capped at its own ceiling ---------
    storeys = {}
    bands = {
        "floor1": (lo[2] + 0.05, float(slab_lo[2]) - 0.05),
        "floor2": (float(slab_hi[2]) + 0.05, wall_top - 0.05),
    }
    for sname, (za, zb) in bands.items():
        band = (zs >= za) & (zs <= zb)
        sub = navigable & wall_fp[:, :, None] & band[None, None, :]
        lab, n = ndimage.label(sub)
        if n == 0:
            storeys[sname] = {"error": "no navigable space"}
            continue
        ids, cnt = np.unique(lab[lab > 0], return_counts=True)
        main = int(ids[np.argmax(cnt)])
        unreachable = []
        for i, c in zip(ids, cnt):
            if int(i) == main or c < MIN_ROOM_CELLS:
                continue
            xs, ys, zz = np.nonzero(lab == int(i))
            unreachable.append({
                "volume_m3": round(float(c) * VOXEL ** 3, 3),
                "centroid_xyz_m": [round(float(lo[0] + xs.mean() * VOXEL), 2),
                                   round(float(lo[1] + ys.mean() * VOXEL), 2),
                                   round(float(lo[2] + zz.mean() * VOXEL), 2)],
            })
        unreachable.sort(key=lambda r: -r["volume_m3"])
        storeys[sname] = {
            "z_band_m": [round(za, 3), round(zb, 3)],
            "components": int(n),
            "main_share": round(float(cnt.max() / cnt.sum()), 4),
            "main_volume_m3": round(float(cnt.max()) * VOXEL ** 3, 2),
            "unreachable_rooms": len(unreachable),
            "unreachable_detail": unreachable[:8],
            "small_voids_below_min_room": int(n - 1 - len(unreachable)),
        }

    # --- stairwell: vertical passages through the slab plane -----------------
    slab_band = (zs >= float(slab_lo[2]) - 0.05) & (zs <= float(slab_hi[2]) + 0.05)
    slab_fp = ndimage.binary_fill_holes(
        occ[:, :, (zs >= float(slab_lo[2])) & (zs <= float(slab_hi[2]))].any(axis=2))
    # a column is a passage when every layer of the slab band is navigable
    passage = navigable[:, :, slab_band].all(axis=2) & slab_fp
    lab2, n2 = ndimage.label(passage)
    openings = []
    if n2:
        ids, cnt = np.unique(lab2[lab2 > 0], return_counts=True)
        for i, c in zip(ids, cnt):
            area = float(c) * VOXEL ** 2
            if area < MIN_OPENING_M2:
                continue
            xs, ys = np.nonzero(lab2 == int(i))
            cx = lo[0] + xs.mean() * VOXEL
            cy = lo[1] + ys.mean() * VOXEL
            in_stair = bool(stair_lo[0] - 0.5 <= cx <= stair_hi[0] + 0.5
                            and stair_lo[1] - 0.5 <= cy <= stair_hi[1] + 0.5)
            openings.append({
                "area_m2": round(area, 3),
                "centroid_xy_m": [round(float(cx), 2), round(float(cy), 2)],
                "coincides_with_staircase": in_stair,
            })
    openings.sort(key=lambda r: -r["area_m2"])

    # teeth: plug every opening that coincides with the stair, expect none left
    plugged = passage.copy()
    sx0 = max(int((stair_lo[0] - 0.5 - lo[0]) / VOXEL), 0)
    sx1 = min(int((stair_hi[0] + 0.5 - lo[0]) / VOXEL) + 1, nx)
    sy0 = max(int((stair_lo[1] - 0.5 - lo[1]) / VOXEL), 0)
    sy1 = min(int((stair_hi[1] + 0.5 - lo[1]) / VOXEL) + 1, ny)
    plugged[sx0:sx1, sy0:sy1] = False
    lab3, n3 = ndimage.label(plugged)
    residual = 0
    if n3:
        _, c3 = np.unique(lab3[lab3 > 0], return_counts=True)
        residual = int((c3 * VOXEL ** 2 >= MIN_OPENING_M2).sum())

    stair_openings = [o for o in openings if o["coincides_with_staircase"]]
    checks = {
        "floor1_no_sealed_rooms": storeys["floor1"].get("unreachable_rooms", 1) == 0,
        "floor2_no_sealed_rooms": storeys["floor2"].get("unreachable_rooms", 1) == 0,
        "stairwell_opening_exists": len(stair_openings) >= 1,
        "stairwell_opening_is_at_the_staircase": (
            len(stair_openings) >= 1
            and sum(o["area_m2"] for o in stair_openings)
            >= 0.5 * sum(o["area_m2"] for o in openings)),
        "control_no_slab_crossing_once_stairwell_plugged": residual == 0,
    }
    return {
        "building": name,
        "clearance_threshold_m": CLEARANCE_M,
        "envelope": "wall-derived filled footprint, eroded 1 voxel",
        "floor1_surface_z_m": round(float(lo[2]), 3),
        "slab2_z_range_m": [round(float(slab_lo[2]), 3), round(float(slab_hi[2]), 3)],
        "wall_top_z_m": round(wall_top, 3),
        "stair_bounds_min": [round(float(v), 3) for v in stair_lo],
        "stair_bounds_max": [round(float(v), 3) for v in stair_hi],
        "wall_footprint_m2": round(float(wall_fp.sum()) * VOXEL ** 2, 2),
        "storeys": storeys,
        "slab_openings": openings,
        "slab_opening_total_area_m2": round(sum(o["area_m2"] for o in openings), 3),
        "control_residual_openings_after_plugging_stairwell": residual,
        "checks": checks,
        "GATE3B": "PASS" if all(checks.values()) else "FAIL",
    }


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    results = {n: analyse(n) for n in NAMES}
    (OUT / "gate3b_structural_connectivity.json").write_text(
        json.dumps(results, indent=2))
    print(f"{'bldg':8} {'F1 share':>9} {'F1 sealed':>10} {'F2 share':>9} {'F2 sealed':>10} "
          f"{'openings':>9} {'at stair':>9} {'control':>8}  verdict")
    for n, r in results.items():
        s = r["storeys"]
        at = sum(1 for o in r["slab_openings"] if o["coincides_with_staircase"])
        print(f"{n:8} {s['floor1']['main_share']:9.4f} {s['floor1']['unreachable_rooms']:10d} "
              f"{s['floor2']['main_share']:9.4f} {s['floor2']['unreachable_rooms']:10d} "
              f"{len(r['slab_openings']):9d} {at:9d} "
              f"{r['control_residual_openings_after_plugging_stairwell']:8d}  {r['GATE3B']}")
        for o in r["slab_openings"][:3]:
            print(f"           opening {o['area_m2']:6.2f} m2 at {o['centroid_xy_m']} "
                  f"stair={o['coincides_with_staircase']}")
    overall = all(r["GATE3B"] == "PASS" for r in results.values())
    print("\nGATE 3b overall: " + ("PASS" if overall else "FAIL"))
    print("wrote " + str(OUT / "gate3b_structural_connectivity.json"))
