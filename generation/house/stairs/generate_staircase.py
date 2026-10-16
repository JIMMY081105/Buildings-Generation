#!/usr/bin/env python3
"""Generate an adaptive staircase with Infinigen and its official USD exporter."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import bpy
import coacd
import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from infinigen.assets.objects.elements.staircases.l_shaped import LShapedStaircaseFactory
from infinigen.assets.objects.elements.staircases.straight import StraightStaircaseFactory
from infinigen.assets.objects.elements.staircases.u_shaped import UShapedStaircaseFactory
from infinigen.core.constraints.constraint_language.constants import RoomConstants
from infinigen.core.util.math import FixedSeed, int_hash
from infinigen.core.sim import kinematic_compiler
from infinigen.core.sim.exporters import usd_exporter
from infinigen.core.util import blender as butil


class WalkingSurfaceTreadPlacementMixin:
    """Place each tread *top* on the riser grid.

    Infinigen's staircase factories place the underside of every tread plate at
    ``(i + 1) * step_height``.  The plate thickness therefore leaks into the
    first rise: ground -> first tread is ``step_height + tread_height`` while
    the final tread -> upper-floor rise is short by the same amount.  Shift the
    tread objects (including an L/U turning platform) down by their own
    thickness before export.  This changes tread placement only; it is not the
    downstream workaround of translating the complete staircase.
    """

    def make_treads(self):
        parts = super().make_treads()
        for tread in parts:
            tread.location[-1] -= float(self.tread_height)
            butil.apply_transform(tread, loc=True)
        return parts


class WalkingSurfaceStraightStaircaseFactory(
        WalkingSurfaceTreadPlacementMixin, StraightStaircaseFactory):
    pass


class WalkingSurfaceLShapedStaircaseFactory(
        WalkingSurfaceTreadPlacementMixin, LShapedStaircaseFactory):
    pass


class WalkingSurfaceUShapedStaircaseFactory(
        WalkingSurfaceTreadPlacementMixin, UShapedStaircaseFactory):
    pass


FACTORIES = {
    "straight": WalkingSurfaceStraightStaircaseFactory,
    "l_shaped": WalkingSurfaceLShapedStaircaseFactory,
    "u_shaped": WalkingSurfaceUShapedStaircaseFactory,
}


class UpperFloorLandingMixin:
    """Stair whose final tread is supplied by the integrated upper floor.

    The upstream factory authors one tread for every riser.  That is useful for
    a freestanding asset, but it duplicates the upper-floor slab when the stair
    is integrated into a building.  Keep all ``n`` risers and regenerate the
    asset with only ``n - 1`` staircase treads; the floor is tread/landing ``n``.
    """

    def make_treads(self):
        parts = super().make_treads()
        # Straight returns n flight treads. L/U return those same n flight
        # treads followed by one mid-flight turning platform.
        platform_count = int(isinstance(self, (
            LShapedStaircaseFactory, UShapedStaircaseFactory
        )))
        if len(parts) != self.n + platform_count:
            raise RuntimeError(
                "Unexpected stair tread topology: {} parts for n={} and {} platform(s)".
                format(len(parts), self.n, platform_count)
            )
        # Flight treads always occupy [0:n]; any turning platform follows them.
        # Remove flight tread n before the official exporter builds both visual
        # and CoACD collision geometry. The upper floor supplies that tread.
        top_index = self.n - 1
        top_tread = parts[top_index]
        top_tread.data.calc_loop_triangles()
        vertices = np.array(
            [top_tread.matrix_world @ vertex.co for vertex in top_tread.data.vertices]
        )
        self.upper_floor_integration_trace = {
            "removed_top_tread_vertex_count": int(len(top_tread.data.vertices)),
            "removed_top_tread_triangle_count": int(len(top_tread.data.loop_triangles)),
            "removed_top_tread_aabb_min_m": [float(v) for v in vertices.min(axis=0)],
            "removed_top_tread_aabb_max_m": [float(v) for v in vertices.max(axis=0)],
        }
        butil.delete(top_tread)
        return parts[:top_index] + parts[top_index + 1 :]


class UpperFloorLandingStraightStaircaseFactory(
        UpperFloorLandingMixin, WalkingSurfaceStraightStaircaseFactory):
    pass


class UpperFloorLandingLShapedStaircaseFactory(
        UpperFloorLandingMixin, WalkingSurfaceLShapedStaircaseFactory):
    pass


class UpperFloorLandingUShapedStaircaseFactory(
        UpperFloorLandingMixin, WalkingSurfaceUShapedStaircaseFactory):
    pass


INTEGRATED_FACTORIES = {
    "straight": UpperFloorLandingStraightStaircaseFactory,
    "l_shaped": UpperFloorLandingLShapedStaircaseFactory,
    "u_shaped": UpperFloorLandingUShapedStaircaseFactory,
}


def integration_centerline(kind: str, factory: StraightStaircaseFactory) -> list[list[float]]:
    """Reproduce make_line_offset(0.5), ending on the upper-floor landing."""
    n = int(factory.n)
    m = int(factory.m) if hasattr(factory, "m") else None
    rise = float(factory.step_height)
    depth = float(factory.step_length)
    width = float(factory.step_width)
    alpha = 0.5

    if kind == "straight":
        points = [
            [alpha * width, depth * i + depth / 2.0, rise * (i + 1)]
            for i in range(n + 1)
        ]
        points[-1][2] -= rise
    elif kind == "l_shaped":
        assert m is not None
        x = [alpha * width] * (m + 2) + [-i * depth for i in range(n - m + 1)]
        y = (
            [i * depth for i in range(m + 1)]
            + [m * depth + alpha * width]
            + [m * depth + alpha * width] * (n - m + 1)
        )
        z = (
            [i * rise for i in range(m + 1)]
            + [m * rise]
            + [i * rise for i in range(m, n + 1)]
        )
        points = [[x[i], y[i], z[i]] for i in range(n + 3)]
        points[m : m + 2] = [list(points[m + 1]), list(points[m + 2])]
        points[m + 1][0] += min(depth / 2.0, alpha * width)
        for i in range(m + 2, len(points)):
            points[i][0] -= depth / 2.0
        for i in range(m):
            points[i][1] += depth / 2.0
        for point in points:
            point[2] += rise
        for i in (m, m + 1, len(points) - 1):
            points[i][2] -= rise
    else:
        assert kind == "u_shaped" and m is not None
        x = [alpha * width] * (m + 2) + [0.0] + [-alpha * width] * (m + 2)
        y = (
            [i * depth for i in range(m + 1)]
            + [m * depth + alpha * width] * 3
            + [i * depth for i in range(m, -1, -1)]
        )
        z = (
            [i * rise for i in range(m + 1)]
            + [m * rise] * 3
            + [i * rise for i in range(m, n + 1)]
        )
        points = [[x[i], y[i], z[i]] for i in range(n + 5)]
        points[m : m + 4] = [list(points[i]) for i in range(m + 1, m + 5)]
        for i in range(m):
            points[i][1] += depth / 2.0
        points[m + 3][1] += min(depth / 2.0, alpha * width)
        for i in range(m + 4, len(points)):
            points[i][1] -= depth / 2.0
        for point in points:
            point[2] += rise
        for i in (m, m + 1, m + 2, m + 3, len(points) - 1):
            points[i][2] -= rise

    # The upstream curve has one additional horizontal point beyond the tread
    # centre.  The preceding point is the centre of the final floor-provided
    # tread/landing and is the integration traversal endpoint.
    points = points[:-1]
    deduped: list[list[float]] = []
    for point in points:
        if not deduped or math.dist(point, deduped[-1]) > 1e-6:
            deduped.append([float(value) for value in point])
    return deduped


def callable_name(value: object) -> str:
    return getattr(value, "__name__", value.__class__.__name__)


def configure_for_height(factory: StraightStaircaseFactory, height_m: float) -> dict[str, Any]:
    """Adapt official factory dimensions without scaling the generated object."""
    original = {
        "n": int(factory.n),
        "step_height": float(factory.step_height),
        "step_length": float(factory.step_length),
        "tread_length": float(factory.tread_length),
        "side_height": float(factory.side_height),
        "glass_margin": float(factory.glass_margin),
        "m": int(factory.m) if hasattr(factory, "m") else None,
    }

    preferred_riser_m = 0.175
    n = max(2, int(round(height_m / preferred_riser_m)))
    if isinstance(factory, UShapedStaircaseFactory) and n % 2:
        n += 1
    riser_m = height_m / n

    # Blondel comfort relation: 2 * riser + going ~= 0.63 m.
    tread_depth_m = min(0.30, max(0.26, 0.63 - 2.0 * riser_m))
    nosing_m = original["tread_length"] - original["step_length"]
    side_height_ratio = original["side_height"] / original["step_height"]
    glass_margin_extra = original["glass_margin"] - original["step_height"] / 2.0

    factory.n = n
    factory.step_height = riser_m
    factory.step_length = tread_depth_m
    factory.tread_length = tread_depth_m + nosing_m
    factory.side_height = riser_m * side_height_ratio
    factory.glass_margin = riser_m / 2.0 + glass_margin_extra
    factory.post_k = int(math.ceil(factory.step_width / factory.step_length))
    factory.end_margin = factory.step_length * 8

    if isinstance(factory, LShapedStaircaseFactory):
        split_ratio = original["m"] / original["n"]
        factory.m = min(n - 2, max(2, int(round(n * split_ratio))))
    elif isinstance(factory, UShapedStaircaseFactory):
        factory.m = n // 2

    # Normalized orientation makes cross-type review and camera placement repeatable.
    factory.mirror = False
    factory.rot_z = 0.0

    return {
        "original_factory_dimensions": original,
        "adapted_dimensions": {
            "target_height_m": height_m,
            "number_of_steps": n,
            "riser_height_m": riser_m,
            "tread_depth_m": tread_depth_m,
            "tread_length_with_nosing_m": factory.tread_length,
            "staircase_width_m": float(factory.step_width),
            "flight_length_straight_equivalent_m": n * tread_depth_m,
            "landing_step_index": int(factory.m) if hasattr(factory, "m") else None,
            "rail_post_stride_steps": int(factory.post_k),
            "computed_climb_m": n * riser_m,
        },
        "adaptation_method": (
            "step-count/riser/tread/flight/landing/rail paths recomputed before "
            "calling the official Infinigen create_asset(); object scale remains identity"
        ),
    }


def evaluated_mesh_metrics(obj: bpy.types.Object) -> dict[str, Any]:
    obj.data.calc_loop_triangles()
    vertices = np.array([obj.matrix_world @ vertex.co for vertex in obj.data.vertices])
    lo = vertices.min(axis=0)
    hi = vertices.max(axis=0)
    return {
        "vertex_count": int(len(obj.data.vertices)),
        "triangle_count": int(len(obj.data.loop_triangles)),
        "aabb_min_m": [float(v) for v in lo],
        "aabb_max_m": [float(v) for v in hi],
        "aabb_extent_m": [float(v) for v in hi - lo],
        "footprint_m": [float(hi[0] - lo[0]), float(hi[1] - lo[1])],
        "object_scale": [float(v) for v in obj.scale],
        "material_slots": [
            slot.material.name if slot.material is not None else None
            for slot in obj.material_slots
        ],
    }


def add_static_export_modifier(obj: bpy.types.Object) -> None:
    """Add an identity Geometry Nodes graph required by Infinigen's sim compiler."""
    node_group = bpy.data.node_groups.new(
        f"{obj.name}_static_export_graph", "GeometryNodeTree"
    )
    node_group.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    node_group.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    group_input = node_group.nodes.new("NodeGroupInput")
    group_output = node_group.nodes.new("NodeGroupOutput")
    node_group.links.new(group_input.outputs["Geometry"], group_output.inputs["Geometry"])
    modifier = obj.modifiers.new(name="InfinigenStaticSimExport", type="NODES")
    modifier.node_group = node_group


def copy_export(exported_usd: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exported_usd, destination / "staircase.usd")
    source_assets = exported_usd.parent / "assets"
    if source_assets.exists():
        shutil.copytree(source_assets, destination / "assets", dirs_exist_ok=True)


BOX_FACE_COUNTS = [3] * 12
BOX_FACE_INDICES = [
    0, 2, 1, 0, 3, 2,
    4, 5, 6, 4, 6, 7,
    0, 1, 5, 0, 5, 4,
    1, 2, 6, 1, 6, 5,
    2, 3, 7, 2, 7, 6,
    3, 0, 4, 3, 4, 7,
]


def normalize_tread_collision_hulls(usd_path: Path) -> dict[str, Any]:
    """Replace CoACD tread hulls with one exact static multi-box mesh.

    CoACD remains the source of collision decomposition for the asset.  On a
    straight stair it produced two nearly-coplanar 10-vertex tread hulls that
    passed topology/schema checks but PhysX omitted, leaving two unsupported
    steps.  Treads are generated axis-aligned rectangular prisms, so their AABB
    is exact rather than an approximation.  Apply one geometry-based rule to
    every tread, never a path/index exception.  The resulting closed,
    outward-wound mesh deliberately uses PhysX's static triangle-mesh path
    instead of convex-hull cooking: two valid-looking convex tread meshes were
    silently absent from scene queries even after their point topology was
    normalized.  Stair treads are static, so the exact triangle mesh is both
    supported and the least ambiguous representation.  All tread boxes are
    combined into one collider and their source pieces are deactivated.  This
    also avoids a reproduced PhysX failure where two individual, schema-valid
    tread prims were absent from scene queries regardless of their mesh
    approximation mode.
    """
    stage = Usd.Stage.Open(str(usd_path), Usd.Stage.LoadAll)
    if stage is None:
        raise RuntimeError(f"Could not reopen exported USD: {usd_path}")
    records = []
    tread_prims = []
    combined_points = []
    combined_counts = []
    combined_indices = []
    collision_parent_paths = set()
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    for prim in list(stage.Traverse()):
        if not prim.IsA(UsdGeom.Mesh) or "/collision/" not in str(prim.GetPath()):
            continue
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get() or []
        if not points:
            continue
        values = np.asarray([[float(v) for v in point] for point in points])
        lo, hi = values.min(axis=0), values.max(axis=0)
        extent = hi - lo
        short_xy, long_xy = sorted((float(extent[0]), float(extent[1])))
        is_tread = (
            0.18 <= short_xy <= 0.40
            and long_xy >= 0.75
            and 0.015 <= float(extent[2]) <= 0.12
        )
        if not is_tread:
            continue
        vertices = [
            (lo[0], lo[1], lo[2]), (hi[0], lo[1], lo[2]),
            (hi[0], hi[1], lo[2]), (lo[0], hi[1], lo[2]),
            (lo[0], lo[1], hi[2]), (hi[0], lo[1], hi[2]),
            (hi[0], hi[1], hi[2]), (lo[0], hi[1], hi[2]),
        ]
        parent = prim.GetParent()
        collision_parent_paths.add(str(parent.GetPath()))
        local_to_parent = (
            xform_cache.GetLocalToWorldTransform(prim)
            * xform_cache.GetLocalToWorldTransform(parent).GetInverse()
        )
        parent_vertices = [
            local_to_parent.Transform(Gf.Vec3d(*(float(value) for value in vertex)))
            for vertex in vertices
        ]
        index_offset = len(combined_points)
        combined_points.extend(parent_vertices)
        combined_counts.extend(BOX_FACE_COUNTS)
        combined_indices.extend(index_offset + value for value in BOX_FACE_INDICES)
        before_triangles = sum(
            max(0, int(count) - 2)
            for count in (mesh.GetFaceVertexCountsAttr().Get() or [])
        )
        tread_prims.append(prim)
        records.append({
            "path": str(prim.GetPath()),
            "before_vertex_count": int(len(points)),
            "before_triangle_count": int(before_triangles),
            "combined_box_vertex_count": 8,
            "combined_box_triangle_count": 12,
            "local_aabb_min_m": [float(v) for v in lo],
            "local_aabb_max_m": [float(v) for v in hi],
        })
    if not records:
        raise RuntimeError("No geometry-classified tread collision hulls found")
    if len(collision_parent_paths) != 1:
        raise RuntimeError(
            f"Tread collision prims do not share one parent: {collision_parent_paths}"
        )
    collision_parent_path = next(iter(collision_parent_paths))
    collision_parent = stage.GetPrimAtPath(collision_parent_path)
    rigid_body_records = []
    ancestor = collision_parent
    while ancestor and ancestor.IsValid() and str(ancestor.GetPath()) != "/":
        if ancestor.HasAPI(UsdPhysics.RigidBodyAPI):
            rigid_body = UsdPhysics.RigidBodyAPI(ancestor)
            rigid_body_records.append({
                "path": str(ancestor.GetPath()),
                "rigid_body_enabled_before": bool(
                    rigid_body.GetRigidBodyEnabledAttr().Get()
                ),
                "kinematic_enabled_before": bool(
                    rigid_body.GetKinematicEnabledAttr().Get()
                ),
            })
            if not ancestor.RemoveAPI(UsdPhysics.RigidBodyAPI):
                raise RuntimeError(
                    f"Could not remove RigidBodyAPI from integrated stair {ancestor.GetPath()}"
                )
        ancestor = ancestor.GetParent()
    if not rigid_body_records:
        raise RuntimeError("Integrated staircase exporter authored no RigidBodyAPI to remove")
    combined_path = str(
        stage.GetPrimAtPath(collision_parent_path).GetPath().AppendChild(
            "integrated_tread_collision"
        )
    )
    combined = UsdGeom.Mesh.Define(stage, combined_path)
    combined.CreatePointsAttr().Set(combined_points)
    combined.CreateFaceVertexCountsAttr().Set(combined_counts)
    combined.CreateFaceVertexIndicesAttr().Set(combined_indices)
    combined.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    combined.CreateDoubleSidedAttr().Set(True)
    combined.CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    values = np.asarray([[float(value) for value in point] for point in combined_points])
    combined_lo, combined_hi = values.min(axis=0), values.max(axis=0)
    combined.CreateExtentAttr().Set([
        Gf.Vec3f(*(float(value) for value in combined_lo)),
        Gf.Vec3f(*(float(value) for value in combined_hi)),
    ])
    UsdPhysics.CollisionAPI.Apply(combined.GetPrim()).CreateCollisionEnabledAttr().Set(True)
    UsdPhysics.MeshCollisionAPI.Apply(combined.GetPrim()).CreateApproximationAttr().Set(
        UsdPhysics.Tokens.none
    )
    for prim in tread_prims:
        prim.SetActive(False)
    stage.GetRootLayer().Save()
    return {
        "policy": (
            "all geometry-classified tread hulls combined into one exact closed "
            "AABB static triangle mesh"
        ),
        "normalized_tread_collision_hull_count": len(records),
        "source_tread_collision_prims_deactivated": len(tread_prims),
        "static_building_element": True,
        "removed_rigid_body_api_prims": rigid_body_records,
        "combined_tread_collision_prim": combined_path,
        "combined_box_count": len(records),
        "combined_vertex_count": len(combined_points),
        "combined_triangle_count": len(combined_indices) // 3,
        "collision_approximation": "none",
        "closed_outward_wound_boxes": True,
        "double_sided": True,
        "extent_authored": True,
        "records": records,
    }


def no_joint_parameters(*_args: Any, **_kwargs: Any) -> dict:
    return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=sorted(FACTORIES), required=True)
    parser.add_argument("--height", type=float, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--image-res", type=int, default=512)
    parser.add_argument(
        "--upper-floor-final-landing",
        action="store_true",
        help="regenerate with n risers, n-1 asset treads, and Floor2 as tread n",
    )
    args = parser.parse_args()

    started = time.time()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    constants = RoomConstants()
    constants.wall_height = args.height
    factory_type = FACTORIES[args.kind]
    if args.upper_floor_final_landing:
        factory_type = INTEGRATED_FACTORIES[args.kind]
    factory = factory_type(args.seed, constants=constants)
    if not factory.has_tread:
        raise RuntimeError(f"Seed {args.seed} unexpectedly has no treads")
    adaptation = configure_for_height(factory, args.height)
    integration_path = integration_centerline(args.kind, factory)
    final_riser_interface = [
        (integration_path[-2][0] + integration_path[-1][0]) / 2.0,
        (integration_path[-2][1] + integration_path[-1][1]) / 2.0,
    ]
    if args.upper_floor_final_landing:
        # The upstream final point is the centre of the now-floor-provided
        # tread.  For building integration, traversal/corridor measurements
        # start at the stair/floor interface instead.  Move 5 mm onto the slab
        # so an actual floor raycast is unambiguous at the polygon boundary.
        dx = integration_path[-1][0] - integration_path[-2][0]
        dy = integration_path[-1][1] - integration_path[-2][1]
        direction_length = math.hypot(dx, dy)
        if direction_length <= 1e-9:
            raise AssertionError("Final riser has no horizontal travel direction")
        integration_path[-1] = [
            final_riser_interface[0] + 0.005 * dx / direction_length,
            final_riser_interface[1] + 0.005 * dy / direction_length,
            float(args.height),
        ]
    if abs(max(point[2] for point in integration_path) - args.height) > 1e-9:
        raise AssertionError("Integration traversal does not preserve the requested total rise")

    # AssetFactory.spawn_asset() runs create_asset() inside
    # FixedSeed(int_hash((factory_seed, i))). Calling create_asset()/
    # finalize_assets() directly -- as this script must, to inject the
    # height-adapted dimensions -- skips that context, so every material
    # parameter is drawn from whatever global RNG state happens to exist.
    # Seed 42 therefore baked lilac treads on one run and oak on the next.
    # Re-enter the official seed context so appearance is a function of --seed.
    with FixedSeed(int_hash((args.seed, 0))):
        obj = factory.create_asset(i=0)
        obj.name = f"{args.kind}_staircase_h{args.height:.2f}m"
        factory.finalize_assets([obj])
    obj.hide_render = False
    obj.hide_viewport = False

    # Match the official sim_factory order: compile the geometry-node graph, then
    # apply modifiers, then call the selected official exporter.
    if not any(modifier.type == "NODES" for modifier in obj.modifiers):
        add_static_export_modifier(obj)
    sim_blueprint = kinematic_compiler.compile(obj)
    butil.apply_modifiers(obj)
    sim_blueprint["name"] = "staircase"
    mesh_metrics = evaluated_mesh_metrics(obj)
    if not np.allclose(mesh_metrics["object_scale"], [1.0, 1.0, 1.0], atol=1e-9):
        raise AssertionError(f"Non-identity object scale detected: {mesh_metrics['object_scale']}")

    blend_path = args.output_dir / "source.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    coacd_trace: dict[str, Any] = {"invocations": 0, "inputs": [], "outputs": []}
    original_run_coacd = coacd.run_coacd

    def traced_run_coacd(*call_args: Any, **call_kwargs: Any):
        mesh = call_kwargs.get("mesh", call_args[0] if call_args else None)
        coacd_trace["invocations"] += 1
        if mesh is not None:
            vertices = getattr(mesh, "vertices", None)
            indices = getattr(mesh, "indices", None)
            coacd_trace["inputs"].append(
                {
                    "vertex_count": int(len(vertices)) if vertices is not None else None,
                    "face_count": int(len(indices)) if indices is not None else None,
                }
            )
        result = original_run_coacd(*call_args, **call_kwargs)
        coacd_trace["outputs"].append({"convex_piece_count": int(len(result))})
        return result

    coacd.run_coacd = traced_run_coacd
    usd_exporter.coacd.run_coacd = traced_run_coacd
    official_export_root = args.output_dir / "official_export"
    try:
        exported_usd, exported_metadata = usd_exporter.export(
            blend_obj=obj,
            sim_blueprint=sim_blueprint,
            seed=args.seed,
            sample_joint_params_fn=no_joint_parameters,
            export_dir=official_export_root,
            image_res=args.image_res,
            visual_only=False,
            file_extension="usd",
        )
    finally:
        coacd.run_coacd = original_run_coacd
        usd_exporter.coacd.run_coacd = original_run_coacd

    exported_usd = Path(exported_usd)
    copy_export(exported_usd, args.output_dir)
    tread_collision_normalization = (
        normalize_tread_collision_hulls(args.output_dir / "staircase.usd")
        if args.upper_floor_final_landing else {
            "policy": "not_requested",
            "normalized_tread_collision_hull_count": 0,
            "records": [],
        }
    )

    repo = Path(__file__).resolve().parents[1] / "infinigen"
    try:
        source_commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        source_commit = "unknown"

    metrics = {
        "kind": args.kind,
        "seed": args.seed,
        "source_commit": source_commit,
        "official_factory": FACTORIES[args.kind].__name__,
        "effective_factory": factory_type.__name__,
        "tread_walking_surface_placement": {
            "policy": "tread_top_on_riser_grid",
            "vertical_offset_from_upstream_m": -float(factory.tread_height),
            "whole_stair_translated": False,
        },
        "official_exporter": "infinigen.core.sim.exporters.usd_exporter.export",
        "visual_only": False,
        "style": {
            "support_type": factory.support_type,
            "handrail_type": factory.handrail_type,
            "has_tread": bool(factory.has_tread),
            "has_step": bool(factory.has_step),
            "has_sides": bool(factory.has_sides),
            "has_rail": bool(factory.has_rail),
            "step_material_factory": callable_name(factory.step_surface),
            "tread_material_factory": callable_name(factory.tread_surface),
            "handrail_material_factory": callable_name(factory.handrail_surface),
        },
        **adaptation,
        "floor_integration": {
            "upper_floor_is_final_landing": bool(args.upper_floor_final_landing),
            "number_of_risers": int(factory.n),
            "authored_stair_tread_count": (
                int(factory.n - 1) if args.upper_floor_final_landing else int(factory.n)
            ),
            "integrated_total_tread_count": int(factory.n),
            "final_landing_support_provider": (
                "upper_floor" if args.upper_floor_final_landing else "stair_asset"
            ),
            "geometry_regenerated_not_scaled": True,
            "final_landing_entry_xyz_m": integration_path[-1],
            "floor_support_inset_m": 0.005 if args.upper_floor_final_landing else 0.0,
            "final_riser_interface_xy_m": final_riser_interface,
            **getattr(factory, "upper_floor_integration_trace", {}),
            "tread_collision_normalization": tread_collision_normalization,
        },
        "visual_mesh": mesh_metrics,
        "coacd_trace": coacd_trace,
        "official_exported_usd": str(exported_usd),
        "official_exported_metadata": str(exported_metadata),
        "deliverable_usd": str((args.output_dir / "staircase.usd").resolve()),
        "elapsed_seconds": time.time() - started,
    }
    (args.output_dir / "generation_metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    traversal = {
        "kind": args.kind,
        "points_xyz_m": integration_path,
        "upper_floor_is_final_landing": bool(args.upper_floor_final_landing),
        "final_landing_support_provider": (
            "upper_floor" if args.upper_floor_final_landing else "stair_asset"
        ),
        "final_landing_index": len(integration_path) - 1,
        "final_riser_interface_xy_m": metrics["floor_integration"][
            "final_riser_interface_xy_m"
        ],
    }
    (args.output_dir / "traversal_path.json").write_text(
        json.dumps(traversal, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
