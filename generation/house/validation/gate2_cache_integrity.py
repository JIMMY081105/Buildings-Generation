"""GATE 2 - ProtoMotions cache integrity for the four _fixed caches.

Loads each cache through the repository's own loader with verify_hashes=True,
which re-hashes every array and cross-checks the occupancy count against the
manifest, then asserts the remaining contract properties.

Run:  python gate2_cache_integrity.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent / "ProtoMotions-main"
sys.path.insert(0, str(REPO))

import numpy as np

from protomotions.simulator.base_simulator.scene_geometry_cache import (
    CACHE_SCHEMA_VERSION,
    COARSE_VOXEL_SIZE_M,
    FINE_VOXEL_SIZE_M,
    load_scene_geometry_cache,
)

CACHES = HERE.parent / "voxel_bake" / "caches"
OUT = HERE / "evidence"
NAMES = ["1_l", "2_u", "3_s300", "4_s270"]


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.iterdir() if p.is_file())


def check(name):
    cache = CACHES / (name + "_fixed.scene_geometry_v1")
    hash_ok, hash_error = True, None
    try:
        loaded = load_scene_geometry_cache(cache, verify_hashes=True)
    except Exception as exc:                      # preserve the failure verbatim
        return {"building": name, "cache": str(cache), "GATE2": "FAIL",
                "load_error": f"{type(exc).__name__}: {exc}"}

    m = loaded.manifest
    grid = m["grid"]
    occ = loaded.occupancy
    fine = np.asarray(loaded.signed_clearance_fine)
    coarse = np.asarray(loaded.signed_clearance_coarse)

    fine_shape = tuple(grid["fine_shape"])
    coarse_shape = tuple(grid["coarse_shape"])
    policy = m["source"]["collision_policy"]

    finite_fine = bool(np.isfinite(fine.astype(np.float32)).all())
    finite_coarse = bool(np.isfinite(coarse.astype(np.float32)).all())

    checks = {
        "schema_version_supported": m.get("schema_version") == CACHE_SCHEMA_VERSION,
        "hash_verification_passed": hash_ok,
        "occupancy_count_matches_manifest":
            int(occ.sum()) == m["occupancy"]["occupied_voxels"],
        "fine_voxel_is_5cm":
            math.isclose(float(grid["fine_voxel_size_m"]), FINE_VOXEL_SIZE_M),
        "coarse_voxel_is_10cm":
            math.isclose(float(grid["coarse_voxel_size_m"]), COARSE_VOXEL_SIZE_M),
        "occupancy_shape_matches_grid": tuple(occ.shape) == fine_shape,
        "fine_clearance_shape_correct": tuple(fine.shape) == fine_shape,
        "coarse_clearance_shape_correct": tuple(coarse.shape) == coarse_shape,
        "coarse_is_exact_half_of_fine":
            tuple(s // 2 for s in fine_shape) == coarse_shape,
        "no_nan_or_inf_fine": finite_fine,
        "no_nan_or_inf_coarse": finite_coarse,
        "no_movable_link_exclusions":
            len(m.get("movable_link_exclusions", [])) == 0,
        "source_provenance_recorded":
            bool(m["source"].get("scene")) and len(m["source"].get("scene_sha256", "")) == 64,
        "policy_is_exact_bake":
            policy.lower().startswith("exact bake"),
        "policy_is_not_cpu_preview":
            "preview" not in policy.lower(),
        "clearance_sign_convention_holds":
            bool((fine[occ].astype(np.float32) <= 0).all()
                 and (fine[~occ].astype(np.float32) >= 0).all()),
    }

    report = {
        "building": name,
        "cache": str(cache),
        "schema_version": m.get("schema_version"),
        "grid_fine_shape": list(fine_shape),
        "grid_coarse_shape": list(coarse_shape),
        "fine_voxel_size_m": float(grid["fine_voxel_size_m"]),
        "coarse_voxel_size_m": float(grid["coarse_voxel_size_m"]),
        "scene_bounds_min_m": grid["scene_bounds_min_m"],
        "scene_bounds_max_m": grid["scene_bounds_max_m"],
        "occupied_voxels": int(occ.sum()),
        "free_voxels": int((~occ).sum()),
        "total_voxels": int(occ.size),
        "occupancy_ratio": round(float(occ.mean()), 6),
        "clearance_fine_min_m": round(float(fine.astype(np.float32).min()), 4),
        "clearance_fine_max_m": round(float(fine.astype(np.float32).max()), 4),
        "clearance_coarse_min_m": round(float(coarse.astype(np.float32).min()), 4),
        "clearance_coarse_max_m": round(float(coarse.astype(np.float32).max()), 4),
        "cache_bytes": dir_bytes(cache),
        "hash_verification": "PASS",
        "movable_link_exclusions": len(m.get("movable_link_exclusions", [])),
        "included_collider_paths": len(m.get("included_collider_paths", [])),
        "source_scene": m["source"]["scene"],
        "source_scene_sha256": m["source"]["scene_sha256"],
        "collision_policy": policy,
        "checks": checks,
        "GATE2": "PASS" if all(checks.values()) else "FAIL",
    }
    return report


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    for name in NAMES:
        r = check(name)
        results[name] = r
        if r["GATE2"] == "FAIL" and "load_error" in r:
            print("%-8s FAIL  %s" % (name, r["load_error"]), flush=True)
            continue
        failed = [k for k, v in r["checks"].items() if not v]
        print("%-8s %s  grid=%s occ=%d free=%d ratio=%.4f clr=[%.2f, %.2f] %.1f MB%s"
              % (name, r["GATE2"], r["grid_fine_shape"], r["occupied_voxels"],
                 r["free_voxels"], r["occupancy_ratio"],
                 r["clearance_fine_min_m"], r["clearance_fine_max_m"],
                 r["cache_bytes"] / 1e6,
                 ("  FAILED: " + ", ".join(failed)) if failed else ""), flush=True)
    (OUT / "gate2_cache_integrity.json").write_text(json.dumps(results, indent=2))
    overall = all(r["GATE2"] == "PASS" for r in results.values())
    print("\nGATE 2 overall: " + ("PASS" if overall else "FAIL"))
    print("wrote " + str(OUT / "gate2_cache_integrity.json"))
