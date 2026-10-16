"""GATE 3 - independent USD collision <-> 5 cm voxel consistency.

The cache is NOT validated against itself. Ground truth is rebuilt from the
composed USD collision meshes and turned into a distance field by a completely
different discretisation: an area-weighted surface point cloud in a cKDTree.
The voxel cache is then interrogated against that.

TOLERANCE IS DERIVED, NOT TUNED
-------------------------------
The bake marks a voxel via trimesh voxelized(pitch=5 cm, method="subdivide")
followed by conservative_splat_voxel_centers. Therefore:

  * subdivide marks the voxel containing each surface sample, so an occupied
    voxel centre lies within half a voxel diagonal of the surface,
    0.05*sqrt(3)/2 = 0.0433 m;
  * the conservative splat additionally marks every target cell overlapping the
    source cube, at most one further voxel, 0.05*sqrt(3) = 0.0866 m.

So the furthest an occupied voxel centre can legitimately sit from real
geometry is 0.0433 + 0.0866 = 0.1299 m. FREE_MARGIN_M is that bound: any point
beyond it must be free, and anything closer is inside the rasterisation's own
uncertainty band and is reported separately rather than judged.

Run:  python gate3_usd_voxel_consistency.py
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent / "ProtoMotions-main"
sys.path.insert(0, str(REPO))

import numpy as np
import trimesh
from scipy import ndimage
from scipy.spatial import cKDTree
from pxr import Usd, UsdGeom, UsdPhysics

from protomotions.simulator.base_simulator.scene_geometry_cache import (
    load_scene_geometry_cache,
)
from protomotions.simulator.isaaclab.utils.scene_geometry_baking import (
    _authored_mesh_geometry_m,
    _iter_collision_meshes,
)

PATCHED = HERE.parent / "voxel_bake" / "patched"
CACHES = HERE.parent / "voxel_bake" / "caches"
OUT = HERE / "evidence"
NAMES = ["1_l", "2_u", "3_s300", "4_s270"]

SEED = 20260818
VOXEL = 0.05
HALF_DIAG = VOXEL * math.sqrt(3) / 2.0          # 0.0433
FULL_DIAG = VOXEL * math.sqrt(3)                # 0.0866
FREE_MARGIN_M = HALF_DIAG + FULL_DIAG           # 0.1299  <- derived, not tuned

N_SURFACE_SAMPLES = 4000        # >= 500 required
N_FREE_SAMPLES = 4000           # >= 500 required
N_KDTREE_POINTS = 3_000_000
N_PER_CATEGORY = 400

CATEGORIES = [
    ("staircase", re.compile(r"/World/Stair", re.I)),
    ("floor_slab_cut", re.compile(r"/World/Cut/", re.I)),
    ("wall", re.compile(r"wall", re.I)),
    ("doorway", re.compile(r"doorway|/door", re.I)),
    ("window", re.compile(r"window", re.I)),
    ("table_desk", re.compile(r"table|desk", re.I)),
    ("chair_sofa", re.compile(r"chair|sofa|stool|bench", re.I)),
]


def load_usd_geometry(name):
    """Composed USD collision triangles, per prim, in world metres."""
    stage = Usd.Stage.Open(str(PATCHED / (name + "_physics_fixed.usda")))
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    records, _ = _iter_collision_meshes(stage, ())
    verts_all, faces_all, owner = [], [], []
    offset = 0
    for rec in records:
        prim = stage.GetPrimAtPath(rec.prim_path)
        v, f = _authored_mesh_geometry_m(stage, prim, mpu, xform_cache)
        verts_all.append(v)
        faces_all.append(f + offset)
        owner.append(np.full(f.shape[0], len(owner), dtype=np.int64))
        offset += v.shape[0]
    vertices = np.concatenate(verts_all, axis=0)
    faces = np.concatenate(faces_all, axis=0)
    face_owner = np.concatenate(owner, axis=0)
    paths = [r.prim_path for r in records]
    return vertices, faces, face_owner, paths


def sample_faces(vertices, faces, subset, n, rng):
    """Area-weighted point sampling over a subset of faces."""
    f = faces[subset] if subset is not None else faces
    if f.shape[0] == 0:
        return np.zeros((0, 3))
    tri = vertices[f]
    a = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) / 2
    total = a.sum()
    if total <= 0:
        return np.zeros((0, 3))
    idx = rng.choice(len(a), size=n, p=a / total)
    t = tri[idx]
    u = rng.random((n, 1))
    v = rng.random((n, 1))
    over = (u + v) > 1
    u[over] = 1 - u[over]
    v[over] = 1 - v[over]
    return t[:, 0] + u * (t[:, 1] - t[:, 0]) + v * (t[:, 2] - t[:, 0])


def voxel_index(points, lo, shape):
    idx = np.floor((points - lo) / VOXEL).astype(np.int64)
    inside = np.all((idx >= 0) & (idx < np.asarray(shape)), axis=1)
    return idx, inside


def occupied_at(occ, idx, inside):
    out = np.zeros(len(idx), dtype=bool)
    if inside.any():
        s = idx[inside]
        out[inside] = occ[s[:, 0], s[:, 1], s[:, 2]]
    return out


def occupied_within_radius(occ, idx, inside, r=1):
    """Is any voxel within a Chebyshev radius occupied?"""
    out = np.zeros(len(idx), dtype=bool)
    shape = np.asarray(occ.shape)
    for i in np.nonzero(inside)[0]:
        c = idx[i]
        lo = np.maximum(c - r, 0)
        hi = np.minimum(c + r + 1, shape)
        out[i] = occ[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]].any()
    return out


def connectivity_analysis(occ, clearance, lo, thresholds=(0.10, 0.15, 0.20, 0.25)):
    """Free-space connectivity between the two storeys.

    Navigable = clearance >= threshold, which keeps a body-sized region and
    stops connectivity leaking through hairline gaps between voxels.
    """
    nz = occ.shape[2]
    z_of = lambda k: lo[2] + (k + 0.5) * VOXEL
    results = {}
    clear = clearance.astype(np.float32)
    for thr in thresholds:
        navigable = clear >= thr
        labels, n = ndimage.label(navigable)          # 6-connectivity
        # seed each storey at the height with the most navigable cells
        counts_by_z = navigable.sum(axis=(0, 1))
        lower_band = [k for k in range(nz) if 0.6 <= z_of(k) - lo[2] <= 1.6]
        upper_band = [k for k in range(nz) if (z_of(k) - lo[2]) >= 3.3]
        if not lower_band or not upper_band:
            results[str(thr)] = {"error": "storey bands outside grid"}
            continue
        k_lo = max(lower_band, key=lambda k: counts_by_z[k])
        k_hi = max(upper_band, key=lambda k: counts_by_z[k])
        lab_lo = labels[:, :, k_lo]
        lab_hi = labels[:, :, k_hi]
        lo_ids, lo_cnt = np.unique(lab_lo[lab_lo > 0], return_counts=True)
        hi_ids, hi_cnt = np.unique(lab_hi[lab_hi > 0], return_counts=True)
        if lo_ids.size == 0 or hi_ids.size == 0:
            results[str(thr)] = {"connected": False, "reason": "no navigable space"}
            continue
        main_lo = int(lo_ids[np.argmax(lo_cnt)])
        main_hi = int(hi_ids[np.argmax(hi_cnt)])
        results[str(thr)] = {
            "floor1_probe_z_m": round(float(z_of(k_lo)), 3),
            "floor2_probe_z_m": round(float(z_of(k_hi)), 3),
            "floor1_main_component": main_lo,
            "floor2_main_component": main_hi,
            "connected_across_storeys": main_lo == main_hi,
            "components_total": int(n),
            "floor1_main_component_cells": int(lo_cnt.max()),
            "floor2_main_component_cells": int(hi_cnt.max()),
            "floor1_component_count_in_slice": int(lo_ids.size),
            "floor2_component_count_in_slice": int(hi_ids.size),
            "floor1_main_share_of_slice":
                round(float(lo_cnt.max() / lo_cnt.sum()), 4),
            "floor2_main_share_of_slice":
                round(float(hi_cnt.max() / hi_cnt.sum()), 4),
        }
    return results


def validate(name):
    t0 = time.time()
    rng = np.random.default_rng(SEED)
    print("\n=== " + name + " ===", flush=True)

    loaded = load_scene_geometry_cache(
        CACHES / (name + "_fixed.scene_geometry_v1"), verify_hashes=True)
    occ = np.ascontiguousarray(loaded.occupancy)
    clearance = np.asarray(loaded.signed_clearance_fine)
    grid = loaded.manifest["grid"]
    lo = np.asarray(grid["scene_bounds_min_m"], dtype=float)
    hi_bounds = np.asarray(grid["scene_bounds_max_m"], dtype=float)
    shape = tuple(grid["fine_shape"])

    vertices, faces, face_owner, paths = load_usd_geometry(name)
    print("  USD colliders=%d triangles=%d" % (len(paths), faces.shape[0]), flush=True)

    # independent distance field: dense surface cloud -> KDTree
    cloud = sample_faces(vertices, faces, None, N_KDTREE_POINTS, rng)
    tree = cKDTree(cloud)
    print("  KDTree over %d surface points built (%.0fs)"
          % (len(cloud), time.time() - t0), flush=True)

    report = {"building": name, "seed": SEED,
              "free_margin_m": round(FREE_MARGIN_M, 4),
              "usd_colliders": len(paths), "usd_triangles": int(faces.shape[0]),
              "kdtree_points": int(len(cloud))}

    # ---- A. USD-solid -> voxel-occupied recall -------------------------------
    surf = sample_faces(vertices, faces, None, N_SURFACE_SAMPLES, rng)
    idx, inside = voxel_index(surf, lo, shape)
    hit_strict = occupied_at(occ, idx, inside)
    hit_1vox = occupied_within_radius(occ, idx, inside, r=1)
    fn_mask = inside & ~hit_1vox                    # genuinely missing geometry
    fn_pts = surf[fn_mask]
    worst_fn = 0.0
    if fn_pts.size:
        # distance from the missed surface point to the nearest occupied voxel
        occ_idx = np.argwhere(occ)
        occ_centers = lo + (occ_idx + 0.5) * VOXEL
        d, _ = cKDTree(occ_centers).query(fn_pts)
        worst_fn = float(d.max())
    report["recall"] = {
        "samples": int(inside.sum()),
        "samples_outside_grid": int((~inside).sum()),
        "occupied_same_voxel": int(hit_strict.sum()),
        "occupied_within_1_voxel": int(hit_1vox.sum()),
        "recall_strict": round(float(hit_strict[inside].mean()), 6) if inside.any() else None,
        "recall_within_1_voxel": round(float(hit_1vox[inside].mean()), 6) if inside.any() else None,
        "false_negatives_beyond_1_voxel": int(fn_mask.sum()),
        "worst_false_negative_distance_m": round(worst_fn, 4),
    }

    # ---- B. USD-free -> voxel-free precision ---------------------------------
    cand = rng.uniform(lo, hi_bounds, size=(N_FREE_SAMPLES * 12, 3))
    dist, _ = tree.query(cand)
    strict_free = cand[dist > FREE_MARGIN_M][:N_FREE_SAMPLES]
    band = cand[(dist > HALF_DIAG) & (dist <= FREE_MARGIN_M)]
    idxf, insidef = voxel_index(strict_free, lo, shape)
    occf = occupied_at(occ, idxf, insidef)
    fp_mask = insidef & occf
    fp_pts = strict_free[fp_mask]
    worst_fp = float(tree.query(fp_pts)[0].max()) if fp_pts.size else 0.0
    idxb, insideb = voxel_index(band, lo, shape)
    occb = occupied_at(occ, idxb, insideb)
    report["precision"] = {
        "strict_free_samples": int(insidef.sum()),
        "false_positives": int(fp_mask.sum()),
        "precision_free": round(float((~occf[insidef]).mean()), 6) if insidef.any() else None,
        "worst_false_positive_distance_from_surface_m": round(worst_fp, 4),
        "rasterisation_band_samples": int(insideb.sum()),
        "rasterisation_band_occupied": int(occb[insideb].sum()) if insideb.any() else 0,
        "rasterisation_band_note":
            "points between one half-diagonal and FREE_MARGIN_M of the surface; "
            "either verdict is legitimate here, reported not judged",
    }

    # ---- C. targeted category samples ----------------------------------------
    cats = {}
    for cat, pattern in CATEGORIES:
        sel = np.array([bool(pattern.search(paths[o])) for o in range(len(paths))])
        if not sel.any():
            cats[cat] = {"colliders": 0, "skipped": "no matching prims"}
            continue
        face_sel = sel[face_owner]
        pts = sample_faces(vertices, faces, np.nonzero(face_sel)[0], N_PER_CATEGORY, rng)
        i2, in2 = voxel_index(pts, lo, shape)
        h_strict = occupied_at(occ, i2, in2)
        h_1 = occupied_within_radius(occ, i2, in2, r=1)
        cats[cat] = {
            "colliders": int(sel.sum()),
            "samples": int(in2.sum()),
            "recall_strict": round(float(h_strict[in2].mean()), 6) if in2.any() else None,
            "recall_within_1_voxel": round(float(h_1[in2].mean()), 6) if in2.any() else None,
            "missing_beyond_1_voxel": int((in2 & ~h_1).sum()),
        }
    report["categories"] = cats

    # ---- D. structural: storey separation and connectivity -------------------
    report["connectivity"] = connectivity_analysis(occ, clearance, lo)

    # occupancy profile per height: proves two distinct storeys and a slab
    prof = occ.sum(axis=(0, 1))
    zs = lo[2] + (np.arange(occ.shape[2]) + 0.5) * VOXEL
    report["height_profile"] = {
        "z_m": [round(float(z), 3) for z in zs],
        "occupied_per_layer": [int(v) for v in prof],
    }

    # ---- E. gate verdict ------------------------------------------------------
    conn15 = report["connectivity"].get("0.15", {})
    checks = {
        "no_missing_geometry_beyond_1_voxel":
            report["recall"]["false_negatives_beyond_1_voxel"] == 0,
        "no_phantom_geometry_in_strict_free_space":
            report["precision"]["false_positives"] == 0,
        "staircase_present":
            cats.get("staircase", {}).get("missing_beyond_1_voxel", 1) == 0
            and cats.get("staircase", {}).get("colliders", 0) > 0,
        "floor_slabs_present":
            cats.get("floor_slab_cut", {}).get("missing_beyond_1_voxel", 1) == 0,
        "walls_present":
            cats.get("wall", {}).get("missing_beyond_1_voxel", 1) == 0,
    }
    # NOTE: three structural checks lived here in attempt 1 and were removed,
    # not weakened. They labelled navigable space over the whole grid, but these
    # buildings have no roof collider, so both storeys connect through the
    # OUTSIDE regardless of whether the stairwell is open -- the check could not
    # fail. Structure is now proven in gate3b_structural_connectivity.py, which
    # confines the analysis to the building envelope and includes a control that
    # must disconnect. Attempt 1's output is preserved verbatim in
    # evidence/gate3_ATTEMPT1_superseded_toothless_connectivity.json.
    # The connectivity numbers below are retained as measurements only.
    report["checks"] = checks
    report["GATE3"] = "PASS" if all(checks.values()) else "FAIL"
    report["elapsed_seconds"] = round(time.time() - t0, 1)

    # mismatch coordinates for inspection
    mism = {
        "false_negative_points_xyz": fn_pts.tolist()[:500],
        "false_positive_points_xyz": fp_pts.tolist()[:500],
    }
    (OUT / ("gate3_mismatches_" + name + ".json")).write_text(json.dumps(mism, indent=2))

    print("  recall strict=%.4f within1vox=%.4f  FN>1vox=%d" %
          (report["recall"]["recall_strict"], report["recall"]["recall_within_1_voxel"],
           report["recall"]["false_negatives_beyond_1_voxel"]), flush=True)
    print("  free precision=%.4f  FP=%d" %
          (report["precision"]["precision_free"], report["precision"]["false_positives"]),
          flush=True)
    print("  storeys connected @0.15 m clearance: %s" %
          conn15.get("connected_across_storeys"), flush=True)
    print("  GATE3 %s" % report["GATE3"], flush=True)
    return report


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    results = {n: validate(n) for n in NAMES}
    (OUT / "gate3_usd_voxel_consistency.json").write_text(json.dumps(results, indent=2))
    overall = all(r["GATE3"] == "PASS" for r in results.values())
    print("\nGATE 3 overall: " + ("PASS" if overall else "FAIL"))
    for n, r in results.items():
        bad = [k for k, v in r["checks"].items() if not v]
        if bad:
            print("  %s failed: %s" % (n, ", ".join(bad)))
    print("wrote " + str(OUT / "gate3_usd_voxel_consistency.json"))
