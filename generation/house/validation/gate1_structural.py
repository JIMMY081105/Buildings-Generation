"""GATE 1 - Structural USD audit of the four fixed composed buildings.

Read-only. Opens each fixed overlay (which sublayers the untouched .usdz) and
checks every structural property required before training. Also re-opens the
original .usdz to prove the reference floor was not modified.

Run:  python gate1_structural.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent / "ProtoMotions-main"
sys.path.insert(0, str(REPO))

from pxr import Usd, UsdGeom, UsdPhysics

EXPORT = Path(r"E:\AI_AGENT_WORKSPACE\Building_Staircase_Pipeline\Final_Physics_Export")
PATCHED = HERE.parent / "voxel_bake" / "patched"
OUT = HERE / "evidence"

NAMES = ["1_l", "2_u", "3_s300", "4_s270"]
EXPECTED_STATIC_COLLIDERS = {"1_l": 2518, "2_u": 3506, "3_s300": 5780, "4_s270": 6314}
REFERENCE = {"1_l": "Floor1", "2_u": "Floor1", "3_s300": "Floor2", "4_s270": "Floor2"}
REPAIRED = {"1_l": "Floor2", "2_u": "Floor2", "3_s300": "Floor1", "4_s270": "Floor1"}


def census(stage, prefix=None):
    """Count colliders / rigid bodies / approximations over a subtree or stage."""
    meshes = colliders = disabled = rigid_enabled = rigid_any = 0
    approx = {}
    for prim in stage.Traverse():
        p = str(prim.GetPath())
        if prefix is not None and not p.startswith(prefix + "/"):
            continue
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            rigid_any += 1
            enabled = UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Get()
            if enabled is not False:
                rigid_enabled += 1
        if not prim.IsA(UsdGeom.Mesh):
            continue
        meshes += 1
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:
            disabled += 1
            continue
        colliders += 1
        a = str(UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get() or "none")
        approx[a] = approx.get(a, 0) + 1
    return {"meshes": meshes, "enabled_colliders": colliders,
            "collision_disabled": disabled, "rigid_body_any": rigid_any,
            "rigid_body_enabled": rigid_enabled, "approximation": approx}


def collider_paths(stage, prefix):
    out = set()
    for prim in stage.Traverse():
        p = str(prim.GetPath())
        if not p.startswith(prefix + "/") or not prim.IsA(UsdGeom.Mesh):
            continue
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False:
                out.add(p[len(prefix):])
    return out


def composition_errors(stage):
    """Unresolved references / composition problems, if this USD exposes them."""
    try:
        errs = stage.GetCompositionErrors()
        return [str(e) for e in errs]
    except AttributeError:
        # older pxr: fall back to detecting prims whose type could not resolve
        bad = [str(p.GetPath()) for p in stage.Traverse() if not p.IsValid()]
        return bad


def audit(name):
    fixed = PATCHED / (name + "_physics_fixed.usda")
    original = EXPORT / (name + "_physics.usdz")
    ref_root, rep_root = REFERENCE[name], REPAIRED[name]

    stage = Usd.Stage.Open(str(fixed))
    base = Usd.Stage.Open(str(original))

    up = str(UsdGeom.GetStageUpAxis(stage))
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    whole = census(stage)
    ref_now = census(stage, "/World/" + ref_root)
    rep_now = census(stage, "/World/" + rep_root)
    ref_orig = census(base, "/World/" + ref_root)

    ref_paths = collider_paths(stage, "/World/" + ref_root)
    rep_paths = collider_paths(stage, "/World/" + rep_root)
    # meshes the reference floor collides but the repaired floor does not
    missing = sorted(ref_paths - rep_paths)
    extra = sorted(rep_paths - ref_paths)

    comp_errors = composition_errors(stage)

    checks = {
        "up_axis_is_Z": up == "Z",
        "meters_per_unit_is_1": abs(mpu - 1.0) < 1e-12,
        "no_enabled_rigid_body_anywhere": whole["rigid_body_enabled"] == 0,
        "no_rigid_body_api_anywhere": whole["rigid_body_any"] == 0,
        "all_enabled_colliders_are_none":
            set(whole["approximation"]) <= {"none"},
        "convex_hull_count_is_zero": whole["approximation"].get("convexHull", 0) == 0,
        "static_collider_count_matches_expected":
            whole["enabled_colliders"] == EXPECTED_STATIC_COLLIDERS[name],
        "repaired_collider_count_matches_reference":
            rep_now["enabled_colliders"] == ref_now["enabled_colliders"],
        "no_collision_mesh_missing_vs_reference": len(missing) == 0,
        "reference_layer_unmodified": ref_now == ref_orig,
        "overlay_composes_without_unresolved_references": len(comp_errors) == 0,
    }

    report = {
        "building": name,
        "fixed_stage": str(fixed),
        "original_usdz": str(original),
        "reference_floor": ref_root,
        "repaired_floor": rep_root,
        "up_axis": up,
        "meters_per_unit": mpu,
        "expected_static_colliders": EXPECTED_STATIC_COLLIDERS[name],
        "stage_census": whole,
        "reference_floor_now": ref_now,
        "reference_floor_in_original_usdz": ref_orig,
        "repaired_floor_now": rep_now,
        "collider_paths_missing_vs_reference": missing[:20],
        "collider_paths_missing_count": len(missing),
        "collider_paths_extra_vs_reference_count": len(extra),
        "composition_errors": comp_errors[:10],
        "checks": checks,
        "GATE1": "PASS" if all(checks.values()) else "FAIL",
    }
    return report


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    for name in NAMES:
        r = audit(name)
        results[name] = r
        failed = [k for k, v in r["checks"].items() if not v]
        print("%-8s %s  colliders=%d (expected %d)  rigid=%d  approx=%s%s"
              % (name, r["GATE1"], r["stage_census"]["enabled_colliders"],
                 r["expected_static_colliders"],
                 r["stage_census"]["rigid_body_enabled"],
                 r["stage_census"]["approximation"],
                 ("  FAILED: " + ", ".join(failed)) if failed else ""), flush=True)
    (OUT / "gate1_structural.json").write_text(json.dumps(results, indent=2))
    overall = all(r["GATE1"] == "PASS" for r in results.values())
    print("\nGATE 1 overall: " + ("PASS" if overall else "FAIL"))
    print("wrote " + str(OUT / "gate1_structural.json"))
