#!/usr/bin/env python3
"""Mesh-derived floor collider coverage contract shared by authoring and G21."""
from __future__ import annotations

from collections import Counter
from typing import Any

from pxr import Usd, UsdGeom, UsdPhysics

from door_collision_policy import leaf_member_kind


FLOOR_ROOTS = ("/World/Floor1", "/World/Floor2")
GEOMETRY_CHILD = "Geometry"
LEGACY_AGGREGATE_NAME = "PhysicsNormalizedExactCollider"
EXCLUDED_SURFACE_PREFIXES = ("room_", "ceiling_")
EXCLUDED_SURFACE_NAMES = {"floor"}


def enabled_mesh_collider(prim: Any) -> bool:
    return bool(
        prim.IsA(UsdGeom.Mesh)
        and prim.HasAPI(UsdPhysics.CollisionAPI)
        and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False
    )


def approximation(prim: Any) -> str:
    return str(UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get() or "none")


def stage_prims_with_instance_proxies(stage: Any) -> list[Any]:
    return list(Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()))


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _purpose(prim: Any) -> str:
    imageable = UsdGeom.Imageable(prim)
    if not imageable:
        return "default"
    return str(imageable.ComputePurpose() or UsdGeom.Tokens.default_)


def _has_authored_mesh_geometry(prim: Any) -> bool:
    mesh = UsdGeom.Mesh(prim)
    points = mesh.GetPointsAttr().Get()
    counts = mesh.GetFaceVertexCountsAttr().Get()
    indices = mesh.GetFaceVertexIndicesAttr().Get()
    return bool(points and counts and indices)


def _object_meshes(stage_prims: list[Any], object_path: str) -> list[Any]:
    return [
        prim for prim in stage_prims
        if _under(str(prim.GetPath()), object_path)
        and prim.IsA(UsdGeom.Mesh)
        and _has_authored_mesh_geometry(prim)
    ]


def expected_mesh_prims(stage: Any, root_path: str) -> tuple[list[Any], list[dict]]:
    """Return unique mesh prims required by the floor-object contract."""
    geometry = stage.GetPrimAtPath(root_path + "/" + GEOMETRY_CHILD)
    if not geometry or not geometry.IsValid():
        return [], []
    all_prims = stage_prims_with_instance_proxies(stage)
    expected_by_path: dict[str, Any] = {}
    object_records = []
    for object_prim in geometry.GetChildren():
        object_name = object_prim.GetName().lower()
        if (object_name in EXCLUDED_SURFACE_NAMES
                or object_name.startswith(EXCLUDED_SURFACE_PREFIXES)):
            # Walkable slabs/ceilings are handled by /World/Cut.  Colliding
            # these visual surfaces would close the stairwell opening and
            # differs from the original four-building policy.
            continue
        object_path = str(object_prim.GetPath())
        all_meshes = _object_meshes(all_prims, object_path)
        policy_excluded = [
            prim for prim in all_meshes if leaf_member_kind(prim) is not None
        ]
        meshes = [
            prim for prim in all_meshes if leaf_member_kind(prim) is None
        ]
        guide = [prim for prim in meshes if _purpose(prim) == "guide"]
        fallback = [prim for prim in meshes if _purpose(prim) in {"default", "render"}]
        expected = guide or fallback
        if not expected:
            continue
        for prim in expected:
            expected_by_path[str(prim.GetPath())] = prim
        enabled = [prim for prim in expected if enabled_mesh_collider(prim)]
        object_records.append({
            "object_path": object_path,
            "expected_source": "guide" if guide else "visual_fallback",
            "mesh_count": len(all_meshes),
            "door_policy_excluded_mesh_count": len(policy_excluded),
            "expected_mesh_collider_count": len(expected),
            "enabled_expected_mesh_collider_count": len(enabled),
            "collidable": len(enabled) == len(expected),
        })
    return [expected_by_path[path] for path in sorted(expected_by_path)], object_records


def floor_profile(stage: Any, root_path: str, *, sample_limit: int = 50) -> dict:
    """Return the absolute expected and enabled collider inventory for one floor.

    Every direct object below ``FloorN/Geometry`` must be collidable.  Existing
    guide-purpose meshes are its authored collision representation.  Objects
    with no guide mesh use their default/render meshes as the exact fallback,
    matching the collider-authoring repair used for visual-only furniture in
    the original four-building pilot.
    """
    root = stage.GetPrimAtPath(root_path)
    geometry = stage.GetPrimAtPath(root_path + "/" + GEOMETRY_CHILD)
    if not root or not root.IsValid() or not geometry or not geometry.IsValid():
        return {
            "floor_root": root_path,
            "status": "FAIL",
            "reason": "missing floor or FloorN/Geometry root",
            "object_count": 0,
            "expected_mesh_collider_count": 0,
            "enabled_expected_mesh_collider_count": 0,
            "total_enabled_mesh_collider_count": 0,
            "coverage_ratio": 0.0,
        }

    all_prims = stage_prims_with_instance_proxies(stage)
    floor_prims = [prim for prim in all_prims if _under(
        str(prim.GetPath()), root_path)]
    expected, object_records = expected_mesh_prims(stage, root_path)
    expected_paths = [str(prim.GetPath()) for prim in expected]
    visual_fallback_objects = sum(
        record["expected_source"] == "visual_fallback" for record in object_records
    )

    expected_set = set(expected_paths)
    enabled_floor = [prim for prim in floor_prims if enabled_mesh_collider(prim)]
    enabled_expected = [
        prim for prim in enabled_floor if str(prim.GetPath()) in expected_set
    ]
    missing = sorted(expected_set - {
        str(prim.GetPath()) for prim in enabled_expected
    })
    unexpected = sorted(
        str(prim.GetPath()) for prim in enabled_floor
        if str(prim.GetPath()) not in expected_set
    )
    approximations = Counter(approximation(prim) for prim in enabled_expected)
    rigid_paths = sorted(
        str(prim.GetPath()) for prim in floor_prims
        if prim.HasAPI(UsdPhysics.RigidBodyAPI)
    )
    expected_count = len(expected_set)
    enabled_count = len(enabled_expected)
    coverage = enabled_count / expected_count if expected_count else 0.0
    uncollidable_objects = [
        record["object_path"] for record in object_records
        if not record["collidable"]
    ]
    checks = {
        "absolute_mesh_expectation_nonzero": expected_count > 0,
        "all_expected_meshes_collidable": enabled_count == expected_count,
        "every_geometric_object_collidable": not uncollidable_objects,
        "all_expected_approximation_none": bool(enabled_expected)
        and set(approximations) == {"none"},
        "static_zero_rigid_body_api": not rigid_paths,
        "no_unexpected_floor_colliders": not unexpected,
    }
    return {
        "floor_root": root_path,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "expectation_method": (
            "per FloorN/Geometry object: all guide meshes, else all "
            "default/render meshes; swinging door leaves/handles are excluded "
            "by human locomotion policy; 100% of the remainder must be static "
            "exact colliders"
        ),
        "door_collision_policy": "doors_must_not_block_locomotion",
        "door_policy_excluded_mesh_count": sum(
            record["door_policy_excluded_mesh_count"]
            for record in object_records),
        "object_count": len(object_records),
        "visual_fallback_object_count": visual_fallback_objects,
        "expected_mesh_collider_count": expected_count,
        "enabled_expected_mesh_collider_count": enabled_count,
        "total_enabled_mesh_collider_count": len(enabled_floor),
        "coverage_ratio": coverage,
        "approximation_mix": dict(sorted(approximations.items())),
        "rigid_body_api_count": len(rigid_paths),
        "uncollidable_object_count": len(uncollidable_objects),
        "missing_expected_mesh_count": len(missing),
        "unexpected_floor_collider_count": len(unexpected),
        "checks": checks,
        "sample_missing_expected_meshes": missing[:sample_limit],
        "sample_uncollidable_objects": uncollidable_objects[:sample_limit],
        "sample_unexpected_floor_colliders": unexpected[:sample_limit],
        "object_records": object_records,
    }


def batch_floor_profiles(stage: Any) -> dict[str, dict]:
    return {root: floor_profile(stage, root) for root in FLOOR_ROOTS}
