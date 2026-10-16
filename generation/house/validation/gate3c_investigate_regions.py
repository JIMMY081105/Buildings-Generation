"""Investigate the Gate 3b findings before any verdict is drawn.

Two things must be explained:

  1. an "unreachable" region of tens of m3 appearing at the SAME xy on both
     storeys, always near a building edge; and
  2. 2_u staying connected across storeys even with the stairwell deleted,
     which would mean that control has no teeth there.

Hypothesis for (1): the Floor 2 slab is larger than the walled envelope, so the
filled-slab footprint used to confine the analysis includes apron area outside
the walls. That area is outdoors, not a sealed room.

This measures the wall / slab / visual extents against each other and renders
the disputed region so it can be judged by eye.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "ProtoMotions-main"))

import numpy as np
import matplotlib.pyplot as plt
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
FIGS = OUT / "figures"
NAMES = ["1_l", "2_u", "3_s300", "4_s270"]
VOXEL = 0.05
CLEARANCE_M = 0.15


def category_extents(name):
    stage = Usd.Stage.Open(str(PATCHED / (name + "_physics_fixed.usda")))
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    xc = UsdGeom.XformCache(Usd.TimeCode.Default())
    acc = {}
    wall_cells = []

    def add(key, v):
        lo, hi = acc.get(key, (np.full(3, np.inf), np.full(3, -np.inf)))
        acc[key] = (np.minimum(lo, v.min(axis=0)), np.maximum(hi, v.max(axis=0)))

    for prim in stage.Traverse():
        p = str(prim.GetPath())
        if not prim.IsA(UsdGeom.Mesh):
            continue
        is_coll = prim.HasAPI(UsdPhysics.CollisionAPI)
        try:
            v, _ = _authored_mesh_geometry_m(stage, prim, mpu, xc)
        except Exception:
            continue
        add("visual_all_meshes", v)
        if not is_coll:
            continue
        add("collider_all", v)
        low = p.lower()
        if "wall" in low:
            add("wall", v)
            wall_cells.append(v)
        if "/world/cut/floor2slab" in low:
            add("slab2", v)
        if "/world/cut/floor2collisionplane" in low:
            add("collision_plane", v)
        if "/world/cut/floor1ceiling" in low:
            add("floor1_ceiling", v)
        if p.startswith("/World/Stair"):
            add("stair", v)
    return acc, wall_cells


def investigate(name):
    loaded = load_scene_geometry_cache(
        CACHES / (name + "_fixed.scene_geometry_v1"), verify_hashes=True)
    occ = np.ascontiguousarray(loaded.occupancy)
    clear = np.asarray(loaded.signed_clearance_fine).astype(np.float32)
    grid = loaded.manifest["grid"]
    lo = np.asarray(grid["scene_bounds_min_m"], float)
    nx, ny, nz = occ.shape

    acc, _ = category_extents(name)
    out = {"building": name, "extents_m": {}}
    for k, (a, b) in acc.items():
        out["extents_m"][k] = {
            "min": [round(float(x), 3) for x in a],
            "max": [round(float(x), 3) for x in b],
            "xy_size": [round(float(b[0] - a[0]), 3), round(float(b[1] - a[1]), 3)],
        }

    wall_lo, wall_hi = acc["wall"]
    slab_lo, slab_hi = acc["slab2"]
    out["slab_overhang_beyond_walls_m"] = {
        "x_min": round(float(wall_lo[0] - slab_lo[0]), 3),
        "y_min": round(float(wall_lo[1] - slab_lo[1]), 3),
        "x_max": round(float(slab_hi[0] - wall_hi[0]), 3),
        "y_max": round(float(slab_hi[1] - wall_hi[1]), 3),
    }

    # footprint used by gate3b, and a wall-derived footprint for comparison
    k0 = max(int((slab_lo[2] - lo[2]) / VOXEL), 0)
    k1 = min(int((slab_hi[2] - lo[2]) / VOXEL) + 1, nz)
    slab_proj = occ[:, :, k0:k1].any(axis=2)
    slab_fp = ndimage.binary_erosion(ndimage.binary_fill_holes(slab_proj), iterations=1)

    # wall-derived: project only wall voxels over storey 1, fill, erode
    zb = ((lo[2] + (np.arange(nz) + 0.5) * VOXEL) >= lo[2] + 0.3) & \
         ((lo[2] + (np.arange(nz) + 0.5) * VOXEL) <= float(slab_lo[2]) - 0.1)
    storey1 = occ[:, :, zb]
    wall_proj = storey1.any(axis=2)
    wall_fp = ndimage.binary_erosion(ndimage.binary_fill_holes(wall_proj), iterations=1)

    out["footprint_cells"] = {
        "slab_derived": int(slab_fp.sum()),
        "wall_derived": int(wall_fp.sum()),
        "slab_minus_wall": int((slab_fp & ~wall_fp).sum()),
        "slab_derived_m2": round(float(slab_fp.sum()) * VOXEL ** 2, 2),
        "wall_derived_m2": round(float(wall_fp.sum()) * VOXEL ** 2, 2),
        "apron_outside_walls_m2": round(float((slab_fp & ~wall_fp).sum()) * VOXEL ** 2, 2),
    }

    # re-run storey-1 room labelling under BOTH footprints
    navigable = clear >= CLEARANCE_M
    zs = lo[2] + (np.arange(nz) + 0.5) * VOXEL
    band = (zs >= lo[2] + 0.05) & (zs <= float(slab_lo[2]) - 0.05)
    res = {}
    for fp_name, fp in (("slab_derived", slab_fp), ("wall_derived", wall_fp)):
        sub = navigable & fp[:, :, None] & band[None, None, :]
        lab, n = ndimage.label(sub)
        if n == 0:
            res[fp_name] = {"error": "empty"}
            continue
        ids, cnt = np.unique(lab[lab > 0], return_counts=True)
        big = [(int(i), int(c)) for i, c in zip(ids, cnt)
               if c >= int(1.0 / VOXEL ** 3)]
        big.sort(key=lambda t: -t[1])
        res[fp_name] = {
            "components": int(n),
            "main_share": round(float(cnt.max() / cnt.sum()), 4),
            "regions_over_1m3": len(big),
            "sizes_m3": [round(c * VOXEL ** 3, 2) for _, c in big[:6]],
        }
    out["storey1_rooms_by_footprint"] = res

    # figure: what the disputed region actually is
    kmid = int(np.clip(round((lo[2] + 1.0 - lo[2]) / VOXEL - 0.5), 0, nz - 1))
    sub = navigable & slab_fp[:, :, None] & band[None, None, :]
    lab, _ = ndimage.label(sub)
    sl = lab[:, :, kmid]
    ids, cnt = np.unique(sl[sl > 0], return_counts=True)
    main = int(ids[np.argmax(cnt)]) if ids.size else 0

    rgb = np.zeros(sl.shape + (3,), dtype=float)
    rgb[occ[:, :, kmid]] = (0.88, 0.70, 0.33)                 # solid
    rgb[(sl > 0) & (sl == main)] = (0.20, 0.65, 0.35)         # reachable
    rgb[(sl > 0) & (sl != main)] = (0.85, 0.20, 0.25)         # flagged
    edge = slab_fp & ~ndimage.binary_erosion(slab_fp, iterations=1)
    rgb[edge] = (0.10, 0.45, 0.95)                            # slab footprint edge
    wedge = wall_fp & ~ndimage.binary_erosion(wall_fp, iterations=1)
    rgb[wedge] = (0.95, 0.95, 0.95)                           # wall footprint edge

    fig, ax = plt.subplots(figsize=(9, 8), constrained_layout=True)
    ax.imshow(np.transpose(rgb, (1, 0, 2)), origin="lower", interpolation="nearest")
    ax.set_title("%s storey 1 @ z=%.2f m\ngreen = reachable, red = flagged unreachable, "
                 "gold = solid\nblue = slab footprint edge, white = wall footprint edge"
                 % (name, lo[2] + (kmid + 0.5) * VOXEL), fontsize=11)
    ax.set_xticks([]); ax.set_yticks([])
    p = FIGS / (name + "_diag_regions.png")
    fig.savefig(p, dpi=115, bbox_inches="tight"); plt.close(fig)
    out["figure"] = str(p)
    return out


if __name__ == "__main__":
    FIGS.mkdir(parents=True, exist_ok=True)
    results = {n: investigate(n) for n in NAMES}
    (OUT / "gate3c_region_investigation.json").write_text(json.dumps(results, indent=2))
    for n, r in results.items():
        o = r["slab_overhang_beyond_walls_m"]
        f = r["footprint_cells"]
        print(f"{n:8} slab overhang beyond walls x[{o['x_min']:.2f},{o['x_max']:.2f}] "
              f"y[{o['y_min']:.2f},{o['y_max']:.2f}]  "
              f"apron={f['apron_outside_walls_m2']:.1f} m2")
        for k, v in r["storey1_rooms_by_footprint"].items():
            print(f"           {k:14} main_share={v.get('main_share')} "
                  f"regions>1m3={v.get('regions_over_1m3')} sizes={v.get('sizes_m3')}")
    print("\nwrote " + str(OUT / "gate3c_region_investigation.json"))
