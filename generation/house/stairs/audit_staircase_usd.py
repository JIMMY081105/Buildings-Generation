#!/usr/bin/env python3
"""Fail-closed structural audit for an exported staircase USD."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pxr import Usd, UsdGeom, UsdPhysics, UsdShade


def mesh_triangles(mesh: UsdGeom.Mesh) -> int:
    counts = mesh.GetFaceVertexCountsAttr().Get() or []
    return int(sum(max(0, int(count) - 2) for count in counts))


def closed_outward_mesh(mesh: UsdGeom.Mesh) -> tuple[bool, float]:
    """Return closed-manifold status and signed volume for triangle geometry."""
    counts = [int(value) for value in (mesh.GetFaceVertexCountsAttr().Get() or [])]
    indices = [int(value) for value in (mesh.GetFaceVertexIndicesAttr().Get() or [])]
    points = mesh.GetPointsAttr().Get() or []
    if not counts or any(count != 3 for count in counts) or sum(counts) != len(indices):
        return False, 0.0
    edges: dict[tuple[int, int], int] = {}
    signed_volume = 0.0
    for offset in range(0, len(indices), 3):
        a, b, c = indices[offset:offset + 3]
        if min(a, b, c) < 0 or max(a, b, c) >= len(points):
            return False, 0.0
        for start, end in ((a, b), (b, c), (c, a)):
            edge = tuple(sorted((start, end)))
            edges[edge] = edges.get(edge, 0) + 1
        pa, pb, pc = points[a], points[b], points[c]
        cross_x = float(pb[1]) * float(pc[2]) - float(pb[2]) * float(pc[1])
        cross_y = float(pb[2]) * float(pc[0]) - float(pb[0]) * float(pc[2])
        cross_z = float(pb[0]) * float(pc[1]) - float(pb[1]) * float(pc[0])
        signed_volume += (
            float(pa[0]) * cross_x
            + float(pa[1]) * cross_y
            + float(pa[2]) * cross_z
        ) / 6.0
    return bool(edges) and all(count == 2 for count in edges.values()), signed_volume


def has_rigid_body_ancestor(prim: Usd.Prim) -> bool:
    current = prim
    while current and current.IsValid() and str(current.GetPath()) != "/":
        if current.HasAPI(UsdPhysics.RigidBodyAPI):
            return True
        current = current.GetParent()
    return False


def topology_issue(prim: Usd.Prim) -> str | None:
    """Return a description if the mesh topology is self-inconsistent.

    Hydra silently DROPS a mesh whose sum(faceVertexCounts) != len(faceVertexIndices)
    ("Invalid mesh topology found ... Geometry will be ignored"), so the asset
    renders empty while every schema/API check still passes. This check exists
    because that exact failure slipped through a fully green audit.
    """
    mesh = UsdGeom.Mesh(prim)
    counts = [int(c) for c in (mesh.GetFaceVertexCountsAttr().Get() or [])]
    indices = [int(i) for i in (mesh.GetFaceVertexIndicesAttr().Get() or [])]
    points = mesh.GetPointsAttr().Get() or []
    if sum(counts) != len(indices):
        return (
            f"{prim.GetPath()}: sum(faceVertexCounts)={sum(counts)} != "
            f"len(faceVertexIndices)={len(indices)}"
        )
    if indices and max(indices) >= len(points):
        return (
            f"{prim.GetPath()}: index {max(indices)} out of range for "
            f"{len(points)} points"
        )
    return None


def bounds(cache: UsdGeom.BBoxCache, prims: list[Usd.Prim]) -> dict[str, Any] | None:
    if not prims:
        return None
    ranges = []
    for prim in prims:
        value = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if not value.IsEmpty():
            ranges.append(value)
    if not ranges:
        return None
    lo = [min(float(value.GetMin()[i]) for value in ranges) for i in range(3)]
    hi = [max(float(value.GetMax()[i]) for value in ranges) for i in range(3)]
    return {
        "min_m": lo,
        "max_m": hi,
        "extent_m": [hi[i] - lo[i] for i in range(3)],
        "footprint_m": [hi[0] - lo[0], hi[1] - lo[1]],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("usd", type=Path)
    parser.add_argument("--generation-metrics", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    stage = Usd.Stage.Open(str(args.usd), Usd.Stage.LoadAll)
    if stage is None:
        raise RuntimeError(f"Could not open {args.usd}")
    generation = json.loads(args.generation_metrics.read_text(encoding="utf-8"))
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=True,
    )

    visual_prims: list[Usd.Prim] = []
    collision_prims: list[Usd.Prim] = []
    per_collision = []
    bound_material_paths = []
    visual_triangles = 0
    collision_triangles = 0

    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        is_collision = prim.HasAPI(UsdPhysics.CollisionAPI)
        if is_collision:
            collision_prims.append(prim)
            triangles = mesh_triangles(mesh)
            collision_triangles += triangles
            enabled = UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()
            approximation = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get()
            visibility = UsdGeom.Imageable(prim).ComputeVisibility()
            closed_manifold, signed_volume = closed_outward_mesh(mesh)
            per_collision.append(
                {
                    "path": str(prim.GetPath()),
                    "vertices": len(mesh.GetPointsAttr().Get() or []),
                    "triangles": triangles,
                    "has_collision_api": True,
                    "has_mesh_collision_api": prim.HasAPI(UsdPhysics.MeshCollisionAPI),
                    "collision_enabled": bool(enabled),
                    "approximation": str(approximation),
                    "double_sided": bool(mesh.GetDoubleSidedAttr().Get()),
                    "extent_authored": bool(mesh.GetExtentAttr().HasAuthoredValue()),
                    "closed_manifold": closed_manifold,
                    "signed_volume_m3": signed_volume,
                    "computed_visibility": str(visibility),
                }
            )
        else:
            visual_prims.append(prim)
            visual_triangles += mesh_triangles(mesh)
            try:
                material, _relationship = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
            except Exception:
                material = None
            if material:
                bound_material_paths.append(str(material.GetPath()))

    texture_files = sorted(
        str(path.relative_to(args.usd.parent))
        for path in (args.usd.parent / "assets").glob("*.png")
    )
    coacd_trace = generation.get("coacd_trace", {})
    topology_issues = [
        issue
        for issue in (topology_issue(prim) for prim in visual_prims + collision_prims)
        if issue is not None
    ]
    checks = {
        "usd_opened": stage is not None,
        "all_mesh_topology_consistent": len(topology_issues) == 0,
        "visual_mesh_exists": len(visual_prims) > 0,
        "collision_mesh_exists_separately": len(collision_prims) > 0,
        "coacd_actually_ran": int(coacd_trace.get("invocations", 0)) > 0,
        "all_collision_api": all(item["has_collision_api"] for item in per_collision),
        "all_mesh_collision_api": all(item["has_mesh_collision_api"] for item in per_collision),
        "all_collision_enabled": all(item["collision_enabled"] for item in per_collision),
        "all_collision_invisible": all(
            item["computed_visibility"] == str(UsdGeom.Tokens.invisible)
            for item in per_collision
        ),
        "visual_material_binding_exists": len(bound_material_paths) > 0,
        "exported_textures_exist": len(texture_files) > 0,
        "identity_scale_generation": all(
            abs(float(value) - 1.0) <= 1e-9
            for value in generation["visual_mesh"]["object_scale"]
        ),
        "computed_climb_matches_target": abs(
            float(generation["adapted_dimensions"]["computed_climb_m"])
            - float(generation["adapted_dimensions"]["target_height_m"])
        )
        <= 1e-9,
    }
    integration = generation.get("floor_integration", {})
    if integration.get("upper_floor_is_final_landing"):
        target = float(generation["adapted_dimensions"]["target_height_m"])
        normalization = integration.get("tread_collision_normalization", {})
        source_tread_paths = set(
            record.get("path") for record in normalization.get("records", [])
        )
        combined_path = normalization.get("combined_tread_collision_prim")
        combined_meshes = [
            item for item in per_collision if item["path"] == combined_path
        ]
        checks.update({
            "upper_floor_declared_as_final_landing": (
                integration.get("final_landing_support_provider") == "upper_floor"
            ),
            "geometry_regenerated_not_scaled": bool(
                integration.get("geometry_regenerated_not_scaled")
            ),
            "one_fewer_authored_tread_than_riser": (
                int(integration.get("authored_stair_tread_count", -1))
                == int(integration.get("number_of_risers", -1)) - 1
            ),
            "integrated_tread_count_matches_risers": (
                int(integration.get("integrated_total_tread_count", -1))
                == int(integration.get("number_of_risers", -2))
            ),
            "removed_tread_was_at_upper_floor": abs(
                # The integration contract is about the walking surface.  The
                # corrected generator places the tread top (AABB max), rather
                # than the plate underside, on the riser grid.
                float(integration.get("removed_top_tread_aabb_max_m", [0, 0, -999])[2])
                - target
            ) <= 1e-5,
            "final_landing_entry_at_target_height": abs(
                float(integration.get("final_landing_entry_xyz_m", [0, 0, -999])[2])
                - target
            ) <= 1e-9,
            "all_authored_tread_colliders_normalized": (
                int(normalization.get("normalized_tread_collision_hull_count", -1))
                == int(integration.get("authored_stair_tread_count", -2))
            ),
            "source_tread_collision_prims_deactivated": (
                len(source_tread_paths)
                == int(integration.get("authored_stair_tread_count", -1))
                and all(
                    stage.GetPrimAtPath(path).IsValid()
                    and not stage.GetPrimAtPath(path).IsActive()
                    for path in source_tread_paths
                )
            ),
            "combined_tread_collider_contains_all_boxes": (
                len(combined_meshes) == 1
                and combined_meshes[0]["vertices"]
                == 8 * int(integration.get("authored_stair_tread_count", -1))
                and combined_meshes[0]["triangles"]
                == 12 * int(integration.get("authored_stair_tread_count", -1))
                and int(normalization.get("combined_box_count", -1))
                == int(integration.get("authored_stair_tread_count", -2))
            ),
            "combined_tread_collider_is_static_closed_triangle_mesh": (
                len(combined_meshes) == 1
                and all(
                    item["approximation"] == str(UsdPhysics.Tokens.none)
                    and item["double_sided"]
                    and item["extent_authored"]
                    and item["closed_manifold"]
                    and item["signed_volume_m3"] > 1e-9
                    for item in combined_meshes
                )
                and not has_rigid_body_ancestor(
                    stage.GetPrimAtPath(combined_path)
                )
                and bool(normalization.get("static_building_element"))
                and bool(normalization.get("removed_rigid_body_api_prims"))
            ),
        })
    audit = {
        "usd": str(args.usd.resolve()),
        "source_commit": generation.get("source_commit"),
        "kind": generation.get("kind"),
        "target_height_m": generation["adapted_dimensions"]["target_height_m"],
        "number_of_steps": generation["adapted_dimensions"]["number_of_steps"],
        "riser_height_m": generation["adapted_dimensions"]["riser_height_m"],
        "tread_depth_m": generation["adapted_dimensions"]["tread_depth_m"],
        "staircase_width_m": generation["adapted_dimensions"]["staircase_width_m"],
        "visual_mesh_count": len(visual_prims),
        "collision_piece_count": len(collision_prims),
        "visual_triangle_count": visual_triangles,
        "collision_triangle_count": collision_triangles,
        "visual_bounds": bounds(cache, visual_prims),
        "collision_bounds": bounds(cache, collision_prims),
        "bound_material_paths": sorted(set(bound_material_paths)),
        "texture_files": texture_files,
        "topology_issues": topology_issues,
        "coacd_trace": coacd_trace,
        "floor_integration": integration,
        "collision_meshes": per_collision,
        "checks": checks,
        "passed": all(checks.values()),
    }
    args.output.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "kind": audit["kind"],
        "target_height_m": audit["target_height_m"],
        "visual_mesh_count": audit["visual_mesh_count"],
        "collision_piece_count": audit["collision_piece_count"],
        "visual_triangle_count": audit["visual_triangle_count"],
        "collision_triangle_count": audit["collision_triangle_count"],
        "checks": checks,
        "passed": audit["passed"],
    }, indent=2))
    if not audit["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
