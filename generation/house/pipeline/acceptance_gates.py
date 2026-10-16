#!/usr/bin/env python3
"""CPU acceptance gates G19-G23 for composed building USD stages.

The module is deliberately path-agnostic. Callers supply a stage and, for G23,
the JSON report emitted by voxel_bake/bake_buildings.py.
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics

from floor_collider_contract import floor_profile as absolute_floor_profile


EXTENT_TOLERANCE = 1e-5
COLLIDER_BOUNDS_TOLERANCE_M = 1e-5
FLOOR_NAME = re.compile(r"^floor\s*([0-9]+)$", re.IGNORECASE)


def _enabled_mesh_collider(prim: Any) -> bool:
    return bool(
        prim.IsA(UsdGeom.Mesh)
        and prim.HasAPI(UsdPhysics.CollisionAPI)
        and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False
    )


def _approximation(prim: Any) -> str:
    # The USD Physics fallback for a mesh without an authored approximation is
    # exact triangle mesh. Treat that effective value as "none".
    return str(UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get() or "none")


def _floor_roots(stage: Any) -> list[str]:
    roots: list[tuple[int, str]] = []
    for prim in stage.GetPseudoRoot().GetChildren():
        # Most deliveries place storeys below /World, so inspect one more level.
        candidates = [prim, *list(prim.GetChildren())]
        for candidate in candidates:
            match = FLOOR_NAME.match(candidate.GetName())
            if match:
                roots.append((int(match.group(1)), str(candidate.GetPath())))
    return [path for _, path in sorted(set(roots))]


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _floor_profile(stage: Any, root: str) -> dict[str, Any]:
    profile = absolute_floor_profile(stage, root)
    # Compatibility aliases retained for describe_building.py and downstream
    # tables.  The G21 decision below uses the absolute mesh expectation, not
    # equality of these raw counts between two different source scenes.
    profile["enabled_mesh_colliders"] = profile[
        "total_enabled_mesh_collider_count"]
    return profile


def _extent_check(stage: Any, tolerance: float) -> dict[str, Any]:
    checked_mesh_samples = 0
    unathored_extent_meshes = 0
    empty_point_meshes = 0
    mismatches: list[dict[str, Any]] = []
    max_error = 0.0

    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        points_attr = mesh.GetPointsAttr()
        extent_attr = mesh.GetExtentAttr()
        if not extent_attr.HasAuthoredValue():
            # G22b is the informational stale-authored-cache diagnostic. An
            # absent optional cache is not stale; USD may bind from points.
            unathored_extent_meshes += 1
            continue

        times = sorted(set(points_attr.GetTimeSamples()) | set(extent_attr.GetTimeSamples()))
        samples = times or [Usd.TimeCode.Default()]
        for sample in samples:
            points = points_attr.Get(sample)
            extent = extent_attr.Get(sample)
            if points is None or len(points) == 0:
                empty_point_meshes += 1
                mismatches.append({
                    "prim_path": str(prim.GetPath()),
                    "time": str(sample),
                    "reason": "authored extent has no point sample",
                })
                continue
            if extent is None or len(extent) != 2:
                mismatches.append({
                    "prim_path": str(prim.GetPath()),
                    "time": str(sample),
                    "reason": "extent is not a two-corner bound",
                })
                continue
            point_array = np.asarray(points, dtype=np.float64)
            extent_array = np.asarray(extent, dtype=np.float64)
            actual = np.stack((point_array.min(axis=0), point_array.max(axis=0)))
            error = float(np.max(np.abs(extent_array - actual)))
            checked_mesh_samples += 1
            max_error = max(max_error, error)
            if not np.isfinite(error) or error > tolerance:
                mismatches.append({
                    "prim_path": str(prim.GetPath()),
                    "time": str(sample),
                    "max_abs_error": error,
                    "authored_extent": extent_array.tolist(),
                    "point_bounds": actual.tolist(),
                })

    return {
        "tolerance": tolerance,
        "checked_mesh_samples": checked_mesh_samples,
        "unathored_extent_meshes": unathored_extent_meshes,
        "empty_point_meshes": empty_point_meshes,
        "mismatch_count": len(mismatches),
        "max_abs_error": max_error,
        "mismatches": mismatches[:50],
        "passed": checked_mesh_samples > 0 and not mismatches,
    }


def _load_baker_api(voxel_bake_root: Path | None):
    """Load the actual baker implementation whose bounds function G22 guards."""
    if voxel_bake_root is not None:
        voxel_bake_root = voxel_bake_root.resolve()
        proto_root = voxel_bake_root.parent / "ProtoMotions-main"
        for root in (voxel_bake_root, proto_root):
            if not root.is_dir():
                raise FileNotFoundError("baker source root is absent: " + str(root))
            value = str(root)
            if value not in sys.path:
                sys.path.insert(0, value)
    try:
        cpu_baker = importlib.import_module("bake_buildings")
        stock_baker = importlib.import_module(
            "protomotions.simulator.isaaclab.utils.scene_geometry_baking"
        )
    except ModuleNotFoundError as exc:
        raise ValueError(
            "G22 requires the actual voxel baker source; pass voxel_bake_root"
        ) from exc
    return cpu_baker, stock_baker


def collider_bounds_check(
    stage: Any,
    voxel_bake_root: Path | None,
    *,
    tolerance_m: float = COLLIDER_BOUNDS_TOLERANCE_M,
    bounds_fn: Callable | None = None,
    implementation_label: str = "actual__collider_bounds_m",
) -> dict[str, Any]:
    """Compare the baker's aggregate bounds with its rasterised vertex union."""
    cpu_baker, stock_baker = _load_baker_api(voxel_bake_root)
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage))
    exclusions = cpu_baker.collect_movable_exclusions(stage)
    records, excluded_count = stock_baker._iter_collision_meshes(stage, exclusions)
    actual_bounds_fn = bounds_fn or stock_baker._collider_bounds_m

    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    vertex_min = np.full(3, np.inf, dtype=np.float64)
    vertex_max = np.full(3, -np.inf, dtype=np.float64)
    purposes: Counter[str] = Counter()
    failures: list[dict[str, str]] = []
    vertex_count = 0
    for record in records:
        prim = stage.GetPrimAtPath(record.prim_path)
        purposes[str(UsdGeom.Imageable(prim).ComputePurpose())] += 1
        try:
            vertices, _ = stock_baker._authored_mesh_geometry_m(
                stage, prim, meters_per_unit, xform_cache
            )
            vertex_count += int(vertices.shape[0])
            vertex_min = np.minimum(vertex_min, vertices.min(axis=0))
            vertex_max = np.maximum(vertex_max, vertices.max(axis=0))
        except Exception as exc:
            failures.append({"prim_path": record.prim_path, "error": str(exc)})

    bounds_error = None
    try:
        returned_min, returned_max = actual_bounds_fn(
            stage, records, meters_per_unit
        )
        returned_min = np.asarray(returned_min, dtype=np.float64)
        returned_max = np.asarray(returned_max, dtype=np.float64)
    except Exception as exc:
        bounds_error = str(exc)
        returned_min = np.full(3, np.nan, dtype=np.float64)
        returned_max = np.full(3, np.nan, dtype=np.float64)

    gap = np.concatenate((returned_min - vertex_min, vertex_max - returned_max))
    finite = bool(
        np.all(np.isfinite(returned_min))
        and np.all(np.isfinite(returned_max))
        and np.all(np.isfinite(vertex_min))
        and np.all(np.isfinite(vertex_max))
        and np.all(np.isfinite(gap))
    )
    max_abs_difference = float(np.max(np.abs(gap))) if finite else float("inf")
    # Positive values are the dangerous case: rasterised vertices extend
    # outside the grid bounds and are clipped. Negative values mean BBoxCache
    # conservatively over-bounds a rotated mesh and are safe, but remain
    # recorded rather than silently treated as exact equality.
    max_undercoverage = float(np.max(np.maximum(gap, 0.0))) if finite else float("inf")
    max_conservative_overbound = (
        float(np.max(np.maximum(-gap, 0.0))) if finite else float("inf")
    )
    guide_count = int(purposes.get("guide", 0))
    passed = bool(records and vertex_count and not failures and bounds_error is None and finite
                  and max_undercoverage <= tolerance_m)
    return {
        "status": "PASS" if passed else "FAIL",
        "definition": (
            "actual baker _collider_bounds_m must contain the aggregate world-space "
            "bounds of every vertex the bake record set can rasterise; any positive "
            "shortfall fails because it clips the bake"
        ),
        "implementation": implementation_label,
        "tolerance_m": tolerance_m,
        "collider_record_count": len(records),
        "excluded_collider_count": int(excluded_count),
        "movable_exclusion_root_count": len(exclusions),
        "vertex_count": vertex_count,
        "purpose_mix": dict(sorted(purposes.items())),
        "guide_collider_count": guide_count,
        "guide_collider_ratio": guide_count / len(records) if records else 0.0,
        "returned_bounds_min_m": returned_min.tolist(),
        "returned_bounds_max_m": returned_max.tolist(),
        "vertex_union_min_m": vertex_min.tolist(),
        "vertex_union_max_m": vertex_max.tolist(),
        "gap_returned_min_minus_vertex_min__vertex_max_minus_returned_max_m":
            gap.tolist(),
        "comparison_note": (
            "zero positive gap is the bake-safety equality criterion; negative gap "
            "is conservative BBoxCache over-bound and is reported separately"
        ),
        "max_undercoverage_m": max_undercoverage,
        "max_conservative_overbound_m": max_conservative_overbound,
        "max_abs_difference_m": max_abs_difference,
        "geometry_read_failure_count": len(failures),
        "geometry_read_failures": failures[:50],
        "bounds_function_error": bounds_error,
    }


def legacy_collider_bounds_without_guide(
    stage: Any, records: Any, meters_per_unit: float
):
    """Exact negative-control copy of the shipped pre-fix bounds function."""
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=False,
    )
    lower = np.full(3, np.inf, dtype=np.float64)
    upper = np.full(3, -np.inf, dtype=np.float64)
    for record in records:
        prim = stage.GetPrimAtPath(record.prim_path)
        world_range = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
        minimum = np.asarray(world_range.GetMin(), dtype=np.float64) * meters_per_unit
        maximum = np.asarray(world_range.GetMax(), dtype=np.float64) * meters_per_unit
        if not np.all(np.isfinite(minimum)) or not np.all(np.isfinite(maximum)):
            raise ValueError("non-finite collision bounds for " + record.prim_path)
        lower = np.minimum(lower, minimum)
        upper = np.maximum(upper, maximum)
    return tuple(lower), tuple(upper)


def audit_structural(stage_path: Path, *, tolerance: float = EXTENT_TOLERANCE,
                     voxel_bake_root: Path | None = None,
                     bounds_fn: Callable | None = None,
                     bounds_implementation_label: str =
                     "actual__collider_bounds_m") -> dict[str, Any]:
    stage_path = stage_path.resolve()
    stage = Usd.Stage.Open(str(stage_path))
    if stage is None:
        raise ValueError("could not open USD stage: " + str(stage_path))

    rigid_paths: list[str] = []
    approximation_mix: Counter[str] = Counter()
    non_none_paths: list[dict[str, str]] = []
    enabled_colliders = 0
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            rigid_paths.append(str(prim.GetPath()))
        if _enabled_mesh_collider(prim):
            enabled_colliders += 1
            approximation = _approximation(prim)
            approximation_mix[approximation] += 1
            if approximation != "none":
                non_none_paths.append({
                    "prim_path": str(prim.GetPath()),
                    "approximation": approximation,
                })

    floor_roots = _floor_roots(stage)
    floor_profiles = {root: _floor_profile(stage, root) for root in floor_roots}
    floor_policy_signatures = {
        json.dumps({
            "coverage_complete": profile.get("coverage_ratio") == 1.0,
            "approximation_mix": profile.get("approximation_mix"),
            "rigid_body_api_count": profile.get("rigid_body_api_count"),
        }, sort_keys=True)
        for profile in floor_profiles.values()
    }
    bounds = collider_bounds_check(
        stage, voxel_bake_root, tolerance_m=tolerance, bounds_fn=bounds_fn,
        implementation_label=bounds_implementation_label,
    )
    extent = _extent_check(stage, tolerance)

    gates = {
        "G19_zero_rigid_body_api": {
            "status": "PASS" if not rigid_paths else "FAIL",
            "rigid_body_api_count": len(rigid_paths),
            "sample_paths": rigid_paths[:50],
        },
        "G20_all_approximation_none": {
            "status": "PASS" if enabled_colliders > 0 and not non_none_paths else "FAIL",
            "enabled_mesh_colliders": enabled_colliders,
            "approximation_mix": dict(sorted(approximation_mix.items())),
            "non_none_count": len(non_none_paths),
            "sample_non_none": non_none_paths[:50],
        },
        "G21_per_floor_parity": {
            "status": "PASS" if (
                len(floor_profiles) == 2
                and all(profile.get("status") == "PASS"
                        for profile in floor_profiles.values())
            ) else "FAIL",
            "definition": (
                "each floor independently has 100% of its mesh-derived expected "
                "object colliders; count equality alone cannot pass"
            ),
            "floor_roots": floor_roots,
            "floor_profiles": floor_profiles,
            "distinct_profile_count": len(floor_policy_signatures),
        },
        "G22_collider_bounds_match_vertex_union": bounds,
    }
    return {
        "stage": str(stage_path),
        "gates": gates,
        "informational_checks": {
            "G22b_mesh_extent_matches_points": {
                "status": "PASS" if extent["passed"] else "FAIL",
                "scope_note": (
                    "diagnostic only; stale per-mesh extent was never observed in "
                    "this project and was not the shipped defect"
                ),
                **{key: value for key, value in extent.items() if key != "passed"},
            },
        },
        "all_structural_pass": all(gate["status"] == "PASS" for gate in gates.values()),
    }


def evaluate_bake_report(report_path: Path) -> dict[str, Any]:
    report_path = report_path.resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    failures = int(report.get("failures", -1))
    exclusions = int(report.get("excluded_colliders", -1))
    movable = int(report.get("movable_exclusions", -1))
    colliders = int(report.get("static_colliders", 0))
    occupied = int(report.get("occupied_voxels", 0))
    clean = bool(
        report.get("status") == "PASS"
        and failures == 0
        and exclusions == 0
        and movable == 0
        and colliders > 0
        and occupied > 0
    )
    return {
        "status": "PASS" if clean else "FAIL",
        "bake_report": str(report_path),
        "bake_status": report.get("status"),
        "failures": failures,
        "excluded_colliders": exclusions,
        "movable_exclusions": movable,
        "static_colliders": colliders,
        "occupied_voxels": occupied,
    }


def audit(stage_path: Path, bake_report: Path | None = None,
          *, voxel_bake_root: Path | None = None,
          bounds_fn: Callable | None = None,
          bounds_implementation_label: str =
          "actual__collider_bounds_m") -> dict[str, Any]:
    report = audit_structural(
        stage_path, voxel_bake_root=voxel_bake_root, bounds_fn=bounds_fn,
        bounds_implementation_label=bounds_implementation_label,
    )
    if bake_report is not None:
        report["gates"]["G23_voxel_bake_clean"] = evaluate_bake_report(bake_report)
    report["all_pass"] = all(
        gate["status"] == "PASS" for gate in report["gates"].values()
    )
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    parser.add_argument("--bake-report", type=Path)
    parser.add_argument("--voxel-bake-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = audit(args.stage, args.bake_report,
                   voxel_bake_root=args.voxel_bake_root)
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
