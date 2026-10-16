"""GATE 4 - visual representation audit.

Seven figures per building plus the quantitative claims they are meant to make.
Storey heights and the staircase footprint are measured from the composed USD,
never hardcoded, because 4_s270 has a 2.7 m storey and the others 3.0 m.

Run:  python gate4_visual_audit.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

HERE = Path(__file__).parent
REPO = HERE.parent / "ProtoMotions-main"
sys.path.insert(0, str(REPO))

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, TwoSlopeNorm
from pxr import Usd, UsdGeom, UsdPhysics

from protomotions.simulator.base_simulator.scene_geometry_cache import (
    load_scene_geometry_cache,
)
from protomotions.simulator.isaaclab.utils.scene_geometry_baking import (
    _authored_mesh_geometry_m,
)

PATCHED = HERE.parent / "voxel_bake" / "patched"
CACHES = HERE.parent / "voxel_bake" / "caches"
OUT = HERE / "evidence" / "figures"
NAMES = ["1_l", "2_u", "3_s300", "4_s270"]
VOXEL = 0.05
SOLID = ListedColormap(["#0d1b2a", "#e0b354"])


def measure_stage(name):
    """World-metre bounds of the stair, the slabs, and all visual geometry.

    Uses authored points, not UsdGeom extent: extent is cached on these stages
    and disagrees with the geometry by up to 0.9 m in Z.
    """
    stage = Usd.Stage.Open(str(PATCHED / (name + "_physics_fixed.usda")))
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    xc = UsdGeom.XformCache(Usd.TimeCode.Default())

    def bounds_of(pred):
        lo = np.full(3, np.inf)
        hi = np.full(3, -np.inf)
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            if not pred(str(prim.GetPath()), prim):
                continue
            try:
                v, _ = _authored_mesh_geometry_m(stage, prim, mpu, xc)
            except Exception:
                continue
            lo = np.minimum(lo, v.min(axis=0))
            hi = np.maximum(hi, v.max(axis=0))
        return lo, hi

    stair_lo, stair_hi = bounds_of(lambda p, _: p.startswith("/World/Stair"))
    slab_lo, slab_hi = bounds_of(lambda p, _: "/World/Cut/Floor2Slab" in p)
    vis_lo, vis_hi = bounds_of(lambda p, _: True)         # every mesh, visual included
    coll_lo, coll_hi = bounds_of(
        lambda p, prim: prim.HasAPI(UsdPhysics.CollisionAPI))
    return {
        "stair_min": stair_lo, "stair_max": stair_hi,
        "slab2_min": slab_lo, "slab2_max": slab_hi,
        "visual_min": vis_lo, "visual_max": vis_hi,
        "collider_min": coll_lo, "collider_max": coll_hi,
    }


def k_of(z, lo, nz):
    return int(np.clip(round((z - lo[2]) / VOXEL - 0.5), 0, nz - 1))


def figure_slices(name, occ, lo, meas, heights, out):
    fig, axes = plt.subplots(1, 4, figsize=(19, 5.4), constrained_layout=True)
    labels = ["Floor 1 ankle", "Floor 1 torso", "Floor 2 ankle", "Floor 2 torso"]
    for ax, z, lab in zip(axes, heights, labels):
        k = k_of(z, lo, occ.shape[2])
        ax.imshow(occ[:, :, k].T, origin="lower", cmap=SOLID, interpolation="nearest")
        ax.set_title("%s\nz = %.2f m   %d solid voxels"
                     % (lab, z, int(occ[:, :, k].sum())), fontsize=10)
        ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle("%s - horizontal occupancy slices (gold = solid)" % name, fontsize=13)
    p = out / (name + "_1_horizontal_slices.png")
    fig.savefig(p, dpi=110, bbox_inches="tight"); plt.close(fig)
    return p


def stair_voxel_mask(name, occ, lo):
    """Voxels belonging to the staircase itself, by sampling its collider surface."""
    stage = Usd.Stage.Open(str(PATCHED / (name + "_physics_fixed.usda")))
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    xc = UsdGeom.XformCache(Usd.TimeCode.Default())
    rng = np.random.default_rng(7)
    mask = np.zeros(occ.shape, dtype=bool)
    for prim in stage.Traverse():
        p = str(prim.GetPath())
        if not p.startswith("/World/Stair") or not prim.IsA(UsdGeom.Mesh):
            continue
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        try:
            v, f = _authored_mesh_geometry_m(stage, prim, mpu, xc)
        except Exception:
            continue
        tri = v[f]
        area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0],
                                       tri[:, 2] - tri[:, 0]), axis=1) / 2
        if area.sum() <= 0:
            continue
        n = max(int(area.sum() / (VOXEL * VOXEL) * 6), 64)
        idx = rng.choice(len(area), size=n, p=area / area.sum())
        t = tri[idx]
        u = rng.random((n, 1)); w = rng.random((n, 1))
        over = (u + w) > 1
        u[over] = 1 - u[over]; w[over] = 1 - w[over]
        pts = t[:, 0] + u * (t[:, 1] - t[:, 0]) + w * (t[:, 2] - t[:, 0])
        vi = np.floor((pts - lo) / VOXEL).astype(int)
        ok = np.all((vi >= 0) & (vi < np.asarray(occ.shape)), axis=1)
        vi = vi[ok]
        mask[vi[:, 0], vi[:, 1], vi[:, 2]] = True
    return mask


def figure_stair_section(name, occ, clearance, lo, meas, out, stair_mask):
    """Vertical section placed where the staircase actually has geometry.

    The stair bounding-box centre is not usable: on an L- or U-shaped flight it
    lands in the stairwell void and the section shows no treads at all.
    """
    sx = int(np.argmax(stair_mask.sum(axis=(1, 2))))
    sy = int(np.argmax(stair_mask.sum(axis=(0, 2))))
    fig, axes = plt.subplots(1, 3, figsize=(19, 5.6), constrained_layout=True)

    def draw(ax, solid, stair, extent, title, xlabel):
        rgb = np.zeros(solid.shape + (3,), dtype=float)
        rgb[...] = (0.05, 0.11, 0.16)
        rgb[solid] = (0.88, 0.70, 0.33)
        rgb[stair] = (0.20, 0.80, 0.55)
        ax.imshow(np.transpose(rgb, (1, 0, 2)), origin="lower",
                  interpolation="nearest", aspect="auto", extent=extent)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel(xlabel); ax.set_ylabel("z (m)")

    ext_y = [lo[1], lo[1] + occ.shape[1] * VOXEL, lo[2], lo[2] + occ.shape[2] * VOXEL]
    draw(axes[0], occ[sx], stair_mask[sx], ext_y,
         "section at x = %.2f m, %d stair voxels\ngreen = staircase, gold = other solid"
         % (lo[0] + sx * VOXEL, int(stair_mask[sx].sum())), "y (m)")

    ext_x = [lo[0], lo[0] + occ.shape[0] * VOXEL, lo[2], lo[2] + occ.shape[2] * VOXEL]
    draw(axes[1], occ[:, sy], stair_mask[:, sy], ext_x,
         "section at y = %.2f m, %d stair voxels"
         % (lo[1] + sy * VOXEL, int(stair_mask[:, sy].sum())), "x (m)")

    cl = clearance[sx, :, :].T.astype(np.float32)
    vmax = float(np.abs(cl).max()) or 1.0
    im = axes[2].imshow(cl, origin="lower", cmap="RdBu",
                        norm=TwoSlopeNorm(vcenter=0.0, vmin=-vmax, vmax=vmax),
                        interpolation="nearest", aspect="auto", extent=ext_y)
    axes[2].contour(np.linspace(ext_y[0], ext_y[1], cl.shape[1]),
                    np.linspace(ext_y[2], ext_y[3], cl.shape[0]),
                    cl, levels=[0.0], colors="k", linewidths=0.8)
    # RdBu maps low to red and high to blue, and clearance is negative inside
    # solid, so red is the solid side. The first version of this label had the
    # two reversed.
    axes[2].set_title("signed clearance on the x section\n"
                      "blue = free space, red = inside solid, black = surface",
                      fontsize=10)
    axes[2].set_xlabel("y (m)"); axes[2].set_ylabel("z (m)")
    fig.colorbar(im, ax=axes[2], shrink=0.85, label="signed clearance (m)")

    fig.suptitle("%s - staircase sections; the stair must bridge both slabs" % name,
                 fontsize=13)
    p = out / (name + "_2_stair_sections.png")
    fig.savefig(p, dpi=110, bbox_inches="tight"); plt.close(fig)
    return p


def figure_3d(name, occ, lo, out):
    """Isometric point cloud of surface voxels, downsampled for legibility."""
    step = 2                                   # 10 cm
    small = occ[::step, ::step, ::step]
    # keep only voxels adjacent to free space so interiors do not hide the shape
    filled = small.copy()
    nb = np.zeros_like(small)
    for ax_ in range(3):
        for sh in (-1, 1):
            nb |= np.roll(small, sh, axis=ax_)
    surface = filled & ~np.all([np.roll(small, s, axis=a)
                                for a in range(3) for s in (-1, 1)], axis=0)
    idx = np.argwhere(surface)
    pts = lo + idx * (VOXEL * step) + 0.5 * VOXEL * step
    if len(pts) > 220000:
        sel = np.random.default_rng(0).choice(len(pts), 220000, replace=False)
        pts = pts[sel]

    fig = plt.figure(figsize=(11, 9))
    ax = fig.add_subplot(111, projection="3d")
    sc = ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], c=pts[:, 2], cmap="viridis",
                    s=1.4, marker="s", linewidths=0, alpha=0.85)
    ax.set_box_aspect((np.ptp(pts[:, 0]), np.ptp(pts[:, 1]), np.ptp(pts[:, 2])))
    ax.view_init(elev=22, azim=-58)
    ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)"); ax.set_zlabel("z (m)")
    ax.set_title("%s - baked 5 cm occupancy, surface voxels at 10 cm\n"
                 "%d points" % (name, len(pts)), fontsize=12)
    fig.colorbar(sc, ax=ax, shrink=0.6, label="height (m)")
    p = out / (name + "_3_voxel_3d.png")
    fig.savefig(p, dpi=115, bbox_inches="tight"); plt.close(fig)
    return p


def furniture_fill_test(name, occ, lo, meas):
    """Are tables/chairs precise geometry or filled convex blocks?

    Attempt 1 measured the occupied fraction of each collider's bounding box.
    That answered the wrong question twice over: per collider piece, a tabletop
    panel legitimately fills its own thin bbox; per instance, the bbox also
    contains the floor, the wall behind it and neighbouring objects. Both
    inflate the number no matter how the furniture is represented.

    This compares against the thing actually in question. For each furniture
    instance the convex hull is built and voxelised SOLID -- precisely what a
    convexHull collider would have contributed -- and the baked occupancy is
    intersected with it. A filled convex block scores near 1.0; precise
    triangle geometry marks only part of its own hull.
    """
    import re
    import trimesh

    stage = Usd.Stage.Open(str(PATCHED / (name + "_physics_fixed.usda")))
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    xc = UsdGeom.XformCache(Usd.TimeCode.Default())
    pat = re.compile(r"(/World/Floor\d/Geometry/[^/]*"
                     r"(?:table|desk|chair|sofa|stool|bench)[^/]*)/", re.I)

    inst = {}
    for prim in stage.Traverse():
        p = str(prim.GetPath())
        if not prim.IsA(UsdGeom.Mesh) or not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        m = pat.match(p)
        if not m:
            continue
        try:
            v, f = _authored_mesh_geometry_m(stage, prim, mpu, xc)
        except Exception:
            continue
        vs, fs, off = inst.setdefault(m.group(1), ([], [], [0]))
        fs.append(f + off[0])
        vs.append(v)
        off[0] += v.shape[0]

    ratios, hull_cells = [], []
    for vs, fs, _ in inst.values():
        verts = np.concatenate(vs, axis=0)
        faces = np.concatenate(fs, axis=0)
        try:
            hull = trimesh.Trimesh(vertices=verts, faces=faces,
                                   process=False).convex_hull
            pts = np.asarray(hull.voxelized(VOXEL, method="subdivide").fill().points,
                             dtype=float)
        except Exception:
            continue
        if pts.size == 0:
            continue
        idx = np.floor((pts - lo) / VOXEL).astype(int)
        ok = np.all((idx >= 0) & (idx < np.asarray(occ.shape)), axis=1)
        idx = np.unique(idx[ok], axis=0)
        if idx.shape[0] < 8:
            continue
        ratios.append(float(occ[idx[:, 0], idx[:, 1], idx[:, 2]].mean()))
        hull_cells.append(int(idx.shape[0]))

    ratios = np.asarray(ratios)
    # The decisive quantity is the void the bake LEAVES inside each hull: space
    # under a tabletop, between chair legs, inside a shelf. A convexHull
    # collider seals all of it by construction (fill=True marks the whole
    # hull), so any surviving void is space precise geometry kept open and a
    # convex block would have taken away.
    #
    # Note the raw fill ratio cannot discriminate on its own: at 5 cm a thin
    # panel is almost entirely shell, so even exact geometry marks most of a
    # small hull. The void volume is the honest measure.
    voids = 1.0 - ratios
    open_m3 = voids * np.asarray(hull_cells, dtype=float) * VOXEL ** 3
    return {
        "furniture_instances_measured": int(ratios.size),
        "median_hull_fill_ratio": round(float(np.median(ratios)), 4) if ratios.size else None,
        "p95_hull_fill_ratio": round(float(np.percentile(ratios, 95)), 4) if ratios.size else None,
        "max_hull_fill_ratio": round(float(ratios.max()), 4) if ratios.size else None,
        "median_hull_voxels": int(np.median(hull_cells)) if hull_cells else None,
        "median_fraction_of_hull_left_open":
            round(float(np.median(voids)), 4) if voids.size else None,
        "total_void_preserved_m3": round(float(open_m3.sum()), 4) if open_m3.size else None,
        "instances_fully_sealed_like_a_convex_block":
            int((ratios >= 0.99).sum()) if ratios.size else None,
        "interpretation": "a convexHull collider marks 100% of its hull by "
                          "construction. Void left open is space that precise "
                          "triangle geometry preserves and a convex block removes.",
    }


def ledge_test(name, occ, lo, meas):
    """Collision geometry must not extend beyond the visible building footprint."""
    vis_lo, vis_hi = meas["visual_min"], meas["visual_max"]
    idx = np.argwhere(occ)
    centers = lo + (idx + 0.5) * VOXEL
    over_min = (vis_lo[:2] - centers[:, :2]).max(initial=0.0)
    over_max = (centers[:, :2] - vis_hi[:2]).max(initial=0.0)
    worst = float(max(over_min, over_max))
    return {
        "visual_footprint_min_xy": [round(float(v), 4) for v in vis_lo[:2]],
        "visual_footprint_max_xy": [round(float(v), 4) for v in vis_hi[:2]],
        "collider_footprint_min_xy": [round(float(v), 4) for v in meas["collider_min"][:2]],
        "collider_footprint_max_xy": [round(float(v), 4) for v in meas["collider_max"][:2]],
        "worst_collision_overhang_beyond_visual_m": round(worst, 4),
        "within_one_voxel": bool(worst <= VOXEL + 1e-9),
    }


def audit(name):
    print("\n=== " + name + " ===", flush=True)
    loaded = load_scene_geometry_cache(
        CACHES / (name + "_fixed.scene_geometry_v1"), verify_hashes=True)
    occ = np.ascontiguousarray(loaded.occupancy)
    clearance = np.asarray(loaded.signed_clearance_fine)
    grid = loaded.manifest["grid"]
    lo = np.asarray(grid["scene_bounds_min_m"], dtype=float)

    meas = measure_stage(name)
    slab_top = float(meas["slab2_max"][2])
    storey2 = slab_top
    heights = [lo[2] + 0.10, lo[2] + 1.20, storey2 + 0.10, storey2 + 1.20]

    stair_mask = stair_voxel_mask(name, occ, lo)
    files = [
        figure_slices(name, occ, lo, meas, heights, OUT),
        figure_stair_section(name, occ, clearance, lo, meas, OUT, stair_mask),
        figure_3d(name, occ, lo, OUT),
    ]

    # quantitative claims the figures are supposed to support
    k1 = k_of(heights[1], lo, occ.shape[2])
    k2 = k_of(heights[3], lo, occ.shape[2])
    s1, s2 = occ[:, :, k1], occ[:, :, k2]
    union = float(max((s1 | s2).sum(), 1))
    distinct = float((s1 ^ s2).sum()) / union
    plan_iou = float((s1 & s2).sum()) / union
    # Storey 2 is a duplicate of storey 1 by construction: the two floors carry
    # identical mesh counts and identical relative prim paths, measured in
    # voxel_bake/check_floor_correspondence.py. Demanding that the two plans
    # LOOK different was a false premise. What must hold is that they are
    # separate levels: a solid slab between them, a real height gap, both
    # furnished.
    slab_k = k_of(0.5 * (float(meas["slab2_min"][2]) + float(meas["slab2_max"][2])),
                  lo, occ.shape[2])
    slab_layer = occ[:, :, slab_k]
    # coverage against the slab's OWN filled footprint: a sound slab is a
    # continuous plate pierced only by the stairwell
    from scipy import ndimage as _nd
    slab_fp = _nd.binary_fill_holes(slab_layer)
    slab_coverage = float(slab_layer.sum()) / float(max(slab_fp.sum(), 1))

    stair_x = slice(max(int((meas["stair_min"][0] - lo[0]) / VOXEL), 0),
                    int((meas["stair_max"][0] - lo[0]) / VOXEL) + 1)
    stair_y = slice(max(int((meas["stair_min"][1] - lo[1]) / VOXEL), 0),
                    int((meas["stair_max"][1] - lo[1]) / VOXEL) + 1)
    stair_col = occ[stair_x, stair_y, :]
    z_lo = k_of(lo[2] + 0.10, lo, occ.shape[2])
    z_hi = k_of(storey2 + 0.10, lo, occ.shape[2])
    layers = stair_col[:, :, z_lo:z_hi + 1].sum(axis=(0, 1))
    stair_continuous = bool((layers > 0).all())

    result = {
        "building": name,
        "slice_heights_m": [round(float(h), 3) for h in heights],
        "storey2_slab_top_m": round(storey2, 3),
        "stair_bounds_min": [round(float(v), 3) for v in meas["stair_min"]],
        "stair_bounds_max": [round(float(v), 3) for v in meas["stair_max"]],
        "floor1_torso_solid_voxels": int(s1.sum()),
        "floor2_torso_solid_voxels": int(s2.sum()),
        "floors_symmetric_difference_ratio": round(distinct, 4),
        "floors_plan_iou": round(plan_iou, 4),
        "floors_are_duplicate_plans_by_construction": True,
        "slab_layer_coverage_vs_storey_footprint": round(slab_coverage, 4),
        "storey_height_separation_m": round(float(heights[3] - heights[1]), 3),
        "stair_layers_between_storeys": int(len(layers)),
        "stair_occupied_on_every_layer": stair_continuous,
        "furniture": furniture_fill_test(name, occ, lo, meas),
        "ledge": ledge_test(name, occ, lo, meas),
        "figures": [str(f) for f in files],
    }
    checks = {
        "storeys_are_separate_levels":
            slab_coverage >= 0.80 and float(heights[3] - heights[1]) >= 2.0,
        "both_storeys_have_furniture":
            int(s1.sum()) > 0 and int(s2.sum()) > 0,
        "stair_continuous_between_storeys": stair_continuous,
        # NB: written with explicit None tests. The `x or default` idiom is
        # wrong here because a legitimate 0 is falsy and would take the default.
        "furniture_is_not_filled_convex_blocks":
            result["furniture"]["median_fraction_of_hull_left_open"] is not None
            and result["furniture"]["median_fraction_of_hull_left_open"] >= 0.10
            and result["furniture"]["instances_fully_sealed_like_a_convex_block"] == 0,
        "no_invisible_ledge_outside_visible_footprint":
            result["ledge"]["within_one_voxel"],
    }
    result["checks"] = checks
    result["GATE4"] = "PASS" if all(checks.values()) else "FAIL"
    print("  plan IoU=%.3f slab cover=%.3f  stair cont=%s  hull fill median=%s "
          "p95=%s  ledge=%.3f m  -> %s"
          % (plan_iou, slab_coverage, stair_continuous,
             result["furniture"]["median_fraction_of_hull_left_open"],
             result["furniture"]["total_void_preserved_m3"],
             result["ledge"]["worst_collision_overhang_beyond_visual_m"],
             result["GATE4"]), flush=True)
    return result


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    results = {n: audit(n) for n in NAMES}
    (HERE / "evidence" / "gate4_visual_audit.json").write_text(
        json.dumps(results, indent=2))
    overall = all(r["GATE4"] == "PASS" for r in results.values())
    print("\nGATE 4 overall: " + ("PASS" if overall else "FAIL"))
    for n, r in results.items():
        bad = [k for k, v in r["checks"].items() if not v]
        if bad:
            print("  %s failed: %s" % (n, ", ".join(bad)))
    print("figures in " + str(OUT))
