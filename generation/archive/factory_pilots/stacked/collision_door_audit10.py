#!/usr/bin/env python3
"""Read-only, exact-10 factory collision, cavity, and door inventory."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable

import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics
import trimesh


EXPECTED_TOTAL = 10
GRID_M = 0.05
SEALED_THRESHOLD = 0.99
MEDIAN_WARNING_THRESHOLD = 0.95
DOOR_ROOT_RE = re.compile(
    r"^/World/(Floor\d+)/Geometry/((?:doorway|doorframe)_[^/]+)$", re.I
)
FURNITURE_RE = re.compile(
    r"(/World/Floor\d/Geometry/[^/]*(?:table|desk|chair|sofa|stool|bench|crate|"
    r"pallet|platform|rack|shelf|cabinet|cart|machine|equipment)[^/]*)/",
    re.I,
)
APPLIANCE_RE = re.compile(
    r"fridge|refrigerator|microwave|oven|dishwasher|washing.?machine|dryer", re.I
)
WALL_REPAIR_ROOT_RE = re.compile(
    r"^(wall_.+?)_(visual|collision)(?:_\d+)?$", re.I
)
LEAF_NAME_RE = re.compile(r"_(?:doorway|doorframe)_door(?:_|$)", re.I)
HANDLE_NAME_RE = re.compile(r"_(?:doorway|doorframe)_handle(?:_|$)", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + f".partial.{os.getpid()}")
    with partial.open("xb") as stream:
        stream.write(canonical_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def is_below(path: str, roots: Iterable[str]) -> bool:
    return any(path == root or path.startswith(root + "/") for root in roots)


def effectively_active(prim: Usd.Prim) -> bool:
    current = prim
    while current and not current.IsPseudoRoot():
        if not current.IsActive():
            return False
        current = current.GetParent()
    return True


def all_stage_prims(stage: Usd.Stage) -> list[Usd.Prim]:
    return list(stage.TraverseAll())


def active_stage_prims(stage: Usd.Stage) -> list[Usd.Prim]:
    return list(stage.Traverse(Usd.TraverseInstanceProxies()))


def discover_door_scope(stage: Usd.Stage) -> dict[str, Any]:
    all_prims = all_stage_prims(stage)
    door_roots = sorted(
        str(prim.GetPath())
        for prim in all_prims
        if DOOR_ROOT_RE.match(str(prim.GetPath()))
    )
    leaf_roots: set[str] = set()
    handle_roots: set[str] = set()
    revolute_joint_paths: list[str] = []

    for prim in all_prims:
        path = str(prim.GetPath())
        if not is_below(path, door_roots):
            continue
        name = prim.GetName()
        if prim.IsA(UsdGeom.Xform):
            if HANDLE_NAME_RE.search(name):
                handle_roots.add(path)
            elif LEAF_NAME_RE.search(name):
                leaf_roots.add(path)
        if prim.IsA(UsdPhysics.RevoluteJoint):
            revolute_joint_paths.append(path)
            parent = prim.GetParent()
            parent_path = str(parent.GetPath())
            parent_name = parent.GetName()
            if parent.IsA(UsdGeom.Xform):
                if "handle" in parent_name.lower():
                    handle_roots.add(parent_path)
                elif "door" in parent_name.lower():
                    leaf_roots.add(parent_path)

    # Keep only the highest selected Xform.  Deactivating it covers descendants.
    def topmost(paths: set[str]) -> list[str]:
        ordered = sorted(paths, key=lambda item: (item.count("/"), item))
        result: list[str] = []
        for path in ordered:
            if not is_below(path, result):
                result.append(path)
        return sorted(result)

    leaf = topmost(leaf_roots)
    handles = topmost(handle_roots)
    targets = sorted(set(leaf + handles))
    if any(not is_below(path, door_roots) for path in targets):
        raise ValueError("door target escaped doorway/doorframe roots")
    if any(APPLIANCE_RE.search(path) for path in targets):
        raise ValueError("appliance-like path entered door target scope")

    active = active_stage_prims(stage)
    door_mesh_paths: list[str] = []
    door_visual_mesh_paths: list[str] = []
    enabled_target_colliders: list[str] = []
    frame_or_jamb_enabled_colliders: list[str] = []
    for prim in active:
        if not prim.IsA(UsdGeom.Mesh):
            continue
        path = str(prim.GetPath())
        if not is_below(path, door_roots):
            continue
        door_mesh_paths.append(path)
        has_collision = prim.HasAPI(UsdPhysics.CollisionAPI)
        enabled = (
            has_collision
            and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False
        )
        if is_below(path, targets):
            if not has_collision:
                door_visual_mesh_paths.append(path)
            if enabled:
                enabled_target_colliders.append(path)
        elif enabled:
            frame_or_jamb_enabled_colliders.append(path)

    unclassified_joints = [
        path
        for path in revolute_joint_paths
        if not is_below(str(stage.GetPrimAtPath(path).GetParent().GetPath()), targets)
    ]

    appliance_door_like: list[str] = []
    for prim in all_prims:
        path = str(prim.GetPath())
        if not APPLIANCE_RE.search(path) or "door" not in prim.GetName().lower():
            continue
        if not is_below(path, door_roots):
            appliance_door_like.append(path)

    object_rows = []
    for root in door_roots:
        object_rows.append(
            {
                "root": root,
                "leaf_xforms": [path for path in leaf if is_below(path, [root])],
                "handle_xforms": [path for path in handles if is_below(path, [root])],
                "enabled_target_colliders": [
                    path for path in enabled_target_colliders if is_below(path, [root])
                ],
                "frame_or_jamb_enabled_colliders": [
                    path
                    for path in frame_or_jamb_enabled_colliders
                    if is_below(path, [root])
                ],
            }
        )

    return {
        "scope_contract": (
            "only Xform door leaves and handles under /World/FloorN/Geometry/"
            "doorway_* or doorframe_*; appliance objects are excluded"
        ),
        "door_object_root_count": len(door_roots),
        "door_object_roots": door_roots,
        "door_leaf_xform_count": len(leaf),
        "door_leaf_xforms": leaf,
        "door_handle_xform_count": len(handles),
        "door_handle_xforms": handles,
        "door_mesh_count": len(door_mesh_paths),
        "door_visual_mesh_count": len(door_visual_mesh_paths),
        "door_visual_meshes": sorted(door_visual_mesh_paths),
        "enabled_target_collider_count": len(enabled_target_colliders),
        "enabled_target_colliders": sorted(enabled_target_colliders),
        "frame_or_jamb_enabled_collider_count": len(frame_or_jamb_enabled_colliders),
        "frame_or_jamb_enabled_colliders": sorted(frame_or_jamb_enabled_colliders),
        "revolute_joint_count": len(revolute_joint_paths),
        "revolute_joints": sorted(revolute_joint_paths),
        "unclassified_revolute_joint_count": len(unclassified_joints),
        "unclassified_revolute_joints": sorted(unclassified_joints),
        "appliance_door_like_prim_count_excluded": len(appliance_door_like),
        "appliance_door_like_prims_excluded": sorted(appliance_door_like),
        "objects": object_rows,
    }


def collision_inventory(stage: Usd.Stage) -> dict[str, Any]:
    enabled_meshes: list[str] = []
    distribution: Counter[str] = Counter()
    exceptional: list[dict[str, str]] = []
    rigid: list[str] = []
    for prim in active_stage_prims(stage):
        path = str(prim.GetPath())
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            rigid.append(path)
        if not prim.IsA(UsdGeom.Mesh) or not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:
            continue
        enabled_meshes.append(path)
        attr = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr()
        approximation = str(attr.Get() or "none")
        distribution[approximation] += 1
        if approximation != "none":
            exceptional.append({"path": path, "approximation": approximation})
    return {
        "enabled_mesh_collider_count": len(enabled_meshes),
        "approximation_distribution": dict(sorted(distribution.items())),
        "non_none_approximation_count": len(exceptional),
        "non_none_approximation_colliders": exceptional,
        "rigid_body_count": len(rigid),
        "rigid_body_prims": sorted(rigid),
    }


def extract_furniture_geometry(stage: Usd.Stage) -> dict[str, list[dict[str, Any]]]:
    # Import from the sealed runtime, selected by PYTHONPATH in the launcher.
    from protomotions.simulator.isaaclab.utils.scene_geometry_baking import (
        _authored_mesh_geometry_m,
    )

    metres = float(UsdGeom.GetStageMetersPerUnit(stage))
    xforms = UsdGeom.XformCache(Usd.TimeCode.Default())
    instances: dict[str, list[dict[str, Any]]] = {}
    for prim in active_stage_prims(stage):
        if not prim.IsA(UsdGeom.Mesh) or not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:
            continue
        path = str(prim.GetPath())
        match = FURNITURE_RE.match(path)
        if not match:
            continue
        vertices, faces = _authored_mesh_geometry_m(stage, prim, metres, xforms)
        instances.setdefault(match.group(1), []).append(
            {"path": path, "vertices": vertices, "faces": faces}
        )
    return instances


def cavity_from_scratch(
    stage: Usd.Stage, scratch: Path, binding: Path, source_sha: str
) -> dict[str, Any]:
    binding_doc = json.loads(binding.read_text(encoding="utf-8"))
    if binding_doc.get("source_usdz_sha256") != source_sha:
        raise ValueError(
            "scratch binding source SHA mismatch: "
            f"{binding_doc.get('source_usdz_sha256')} != {source_sha}"
        )
    scratch_sha = file_sha256(scratch)
    if binding_doc.get("scratch_sha256") != scratch_sha:
        raise ValueError("scratch SHA mismatch")
    with np.load(scratch, allow_pickle=False) as data:
        occupancy = np.asarray(data["full_occupancy"], dtype=np.bool_)
        metadata = json.loads(bytes(data["metadata_json_utf8"]).decode("utf-8"))
    origin = np.asarray(
        metadata["full_grid"]["scene_bounds_min_m"], dtype=np.float64
    )
    if occupancy.shape != tuple(metadata["full_grid"]["fine_shape"]):
        raise ValueError("scratch occupancy shape disagrees with metadata")

    instance_rows: list[dict[str, Any]] = []
    for instance_path, meshes in extract_furniture_geometry(stage).items():
        vertices_parts = []
        face_parts = []
        offset = 0
        for mesh in meshes:
            vertices_parts.append(mesh["vertices"])
            face_parts.append(mesh["faces"] + offset)
            offset += len(mesh["vertices"])
        try:
            hull = trimesh.Trimesh(
                vertices=np.concatenate(vertices_parts),
                faces=np.concatenate(face_parts),
                process=False,
            ).convex_hull
            points = np.asarray(
                hull.voxelized(GRID_M, method="subdivide").fill().points,
                dtype=np.float64,
            )
        except Exception as error:
            instance_rows.append(
                {
                    "instance_path": instance_path,
                    "collision_mesh_count": len(meshes),
                    "measurement_error": repr(error),
                }
            )
            continue
        indices = np.floor((points - origin) / GRID_M).astype(np.int64)
        inside = np.all(
            (indices >= 0) & (indices < np.asarray(occupancy.shape)), axis=1
        )
        indices = np.unique(indices[inside], axis=0)
        if len(indices) < 8:
            instance_rows.append(
                {
                    "instance_path": instance_path,
                    "collision_mesh_count": len(meshes),
                    "measurement_error": "fewer than 8 in-bounds hull voxels",
                }
            )
            continue
        ratio = float(
            occupancy[indices[:, 0], indices[:, 1], indices[:, 2]].mean()
        )
        instance_rows.append(
            {
                "instance_path": instance_path,
                "collision_mesh_count": len(meshes),
                "hull_voxel_count": len(indices),
                "hull_fill_ratio": ratio,
                "high_hull_fill_suspect": ratio >= MEDIAN_WARNING_THRESHOLD,
                "suspected_sealed_cavity": ratio >= SEALED_THRESHOLD,
            }
        )

    measured = [row for row in instance_rows if "hull_fill_ratio" in row]
    if not measured:
        raise ValueError("no furniture hull could be measured")
    values = np.asarray([row["hull_fill_ratio"] for row in measured])
    rows = sorted(
        instance_rows,
        key=lambda row: (-row.get("hull_fill_ratio", -math.inf), row["instance_path"]),
    )
    return {
        "evidence_mode": "fresh_measurement_on_exact_sha_bound_existing_occupancy",
        "grid_m": GRID_M,
        "sealed_threshold": SEALED_THRESHOLD,
        "median_warning_threshold": MEDIAN_WARNING_THRESHOLD,
        "scratch": str(scratch),
        "scratch_sha256": scratch_sha,
        "scratch_binding": str(binding),
        "furniture_instances_measured": len(measured),
        "median_hull_fill_ratio": float(np.median(values)),
        "suspected_sealed_cavity_count": int((values >= SEALED_THRESHOLD).sum()),
        "suspected_sealed_cavities": [
            row for row in rows if row.get("suspected_sealed_cavity")
        ],
        "instances": rows,
    }


def cavity_from_legacy(
    legacy: dict[str, Any], bid: str, source_sha: str, legacy_path: Path
) -> dict[str, Any]:
    row = legacy.get("table", {}).get(bid)
    if not isinstance(row, dict):
        raise ValueError(f"legacy audit has no row for {bid}")
    if row.get("source_usdz_sha256") != source_sha:
        raise ValueError("legacy cavity evidence source SHA mismatch")
    furniture = row["visual_collision"]["furniture"]
    instances = []
    for old in furniture["instances"]:
        item = dict(old)
        item["high_hull_fill_suspect"] = (
            float(item["hull_fill_ratio"]) >= MEDIAN_WARNING_THRESHOLD)
        item["suspected_sealed_cavity"] = (
            float(item["hull_fill_ratio"]) >= SEALED_THRESHOLD
        )
        instances.append(item)
    instances.sort(key=lambda item: (-item["hull_fill_ratio"], item["instance_path"]))
    return {
        "evidence_mode": "exact_sha_bound_prior_voxel_measurement_no_render_reuse",
        "grid_m": GRID_M,
        "sealed_threshold": SEALED_THRESHOLD,
        "median_warning_threshold": MEDIAN_WARNING_THRESHOLD,
        "legacy_audit": str(legacy_path),
        "legacy_audit_sha256": file_sha256(legacy_path),
        "furniture_instances_measured": furniture["furniture_instances_measured"],
        "median_hull_fill_ratio": furniture["median_hull_fill_ratio"],
        "suspected_sealed_cavity_count": sum(
            bool(item["suspected_sealed_cavity"]) for item in instances
        ),
        "suspected_sealed_cavities": [
            item for item in instances if item["suspected_sealed_cavity"]
        ],
        "instances": instances,
    }


def collect_sources(delivery_root: Path) -> list[dict[str, Any]]:
    paths = sorted(delivery_root.glob("*/scene/building.usdz"))
    rows = [{"group": "Factory", "bid": path.parent.parent.name, "source": path}
            for path in paths]
    if len(rows) != EXPECTED_TOTAL or len({row["bid"] for row in rows}) != EXPECTED_TOTAL:
        raise ValueError("source inventory is not exactly 10 unique factory buildings")
    return rows


def load_repair_histories(
    summary_paths: list[Path],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    histories: dict[str, list[dict[str, Any]]] = {}
    bindings = []
    seen_iterations: set[int] = set()
    for raw_path in summary_paths:
        path = raw_path.resolve()
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        iteration = int(document.get("iteration", -1))
        if (
            document.get("schema")
            != "Factory10EvidenceBoundNavigationRepairSummaryV1"
            or document.get("status") != "PASS"
            or document.get("building_count") != EXPECTED_TOTAL
            or document.get("protected_gate_modified") is not False
            or document.get("formal_threshold_modified") is not False
            or len(document.get("rows", [])) != EXPECTED_TOTAL
            or iteration < 1
            or iteration in seen_iterations
        ):
            raise ValueError(f"invalid or duplicate exact-10 repair summary: {path}")
        seen_iterations.add(iteration)
        bindings.append(
            {
                "iteration": iteration,
                "path": str(path),
                "sha256": file_sha256(path),
            }
        )
        for row in document["rows"]:
            bid = row.get("BID")
            report_path = Path(row["report"]).resolve()
            if (
                row.get("status") != "PASS"
                or not report_path.is_file()
                or file_sha256(report_path) != row.get("report_sha256")
            ):
                raise ValueError(f"repair row/report binding differs: {bid}")
            report = json.loads(report_path.read_text(encoding="utf-8-sig"))
            if (
                report.get("schema") != "Factory10ResidualNavigationRepairV1"
                or report.get("status") != "PASS"
                or report.get("building") != bid
                or report.get("output_sha256") != row.get("geometry_sha256")
                or report.get("protected_gate_modified") is not False
                or report.get("formal_threshold_modified") is not False
            ):
                raise ValueError(f"invalid detailed repair evidence: {bid}")
            if row.get("carried_forward") is True:
                histories.setdefault(str(bid), [])
                continue
            histories.setdefault(str(bid), []).append(
                {
                    "iteration": iteration,
                    "summary_row": row,
                    "report_path": report_path,
                    "report_sha256": file_sha256(report_path),
                    "report": report,
                }
            )
    if len(histories) != EXPECTED_TOTAL:
        raise ValueError("repair summaries do not cover exact-10 buildings")
    for rows in histories.values():
        rows.sort(key=lambda item: item["iteration"])
    bindings.sort(key=lambda item: item["iteration"])
    return histories, bindings


def validate_repair_history(
    bid: str, source_sha: str, history: list[dict[str, Any]]
) -> dict[str, Any]:
    if not history:
        raise ValueError(f"repair evidence absent: {bid}")
    previous_output: str | None = None
    deactivated_roots: list[str] = []
    wall_groups: list[dict[str, Any]] = []
    report_rows = []
    for item in history:
        report = item["report"]
        if previous_output is not None and report.get("input_sha256") != previous_output:
            raise ValueError(f"repair iteration hash chain differs: {bid}")
        previous_output = report["output_sha256"]
        roots = [str(path) for path in report.get("deactivated_roots", [])]
        protected = []
        for path in roots:
            name = path.rsplit("/", 1)[-1]
            if name.lower().startswith(
                ("doorway_", "doorframe_", "parc_storage_crate_")
            ) or APPLIANCE_RE.search(name):
                protected.append(path)
        if protected:
            raise ValueError(f"protected door/appliance roots were deactivated: {protected}")

        grouped_wall_roots: set[str] = set()
        for row in report.get("component_and_wall_evidence", []):
            if "logical_wall_stem" not in row:
                continue
            siblings = [str(path) for path in row.get("deactivated_sibling_roots", [])]
            matches = [
                WALL_REPAIR_ROOT_RE.match(path.rsplit("/", 1)[-1])
                for path in siblings
            ]
            roles = {match.group(2).lower() for match in matches if match}
            stems = {match.group(1).lower() for match in matches if match}
            expected_stem = str(row["logical_wall_stem"]).lower()
            if (len(siblings) < 2 or any(match is None for match in matches)
                    or roles != {"visual", "collision"}
                    or stems != {expected_stem}):
                raise ValueError(f"wall repair lacks a complete visual/collision pair: {bid}")
            grouped_wall_roots.update(siblings)
            wall_groups.append(
                {
                    "iteration": item["iteration"],
                    "storey": row.get("storey"),
                    "logical_wall_stem": row["logical_wall_stem"],
                    "deactivated_sibling_roots": siblings,
                }
            )
        actual_wall_roots = {
            path for path in roots
            if WALL_REPAIR_ROOT_RE.match(path.rsplit("/", 1)[-1])
        }
        if actual_wall_roots != grouped_wall_roots:
            raise ValueError(f"wall repair roots are not exactly paired by evidence: {bid}")
        checks = report.get("checks", {})
        if not (
            checks.get("input_bound_to_minimum_cut_evidence") is True
            and checks.get("only_evidence_bound_oversized_components_selected") is True
            and checks.get("both_visual_and_collision_wall_siblings_removed") is True
            and checks.get("unrelated_object_roots_retained") is True
        ):
            raise ValueError(f"repair scope checks are incomplete: {bid}")
        deactivated_roots.extend(roots)
        report_rows.append(
            {
                "iteration": item["iteration"],
                "report": str(item["report_path"]),
                "report_sha256": item["report_sha256"],
                "input_sha256": report["input_sha256"],
                "output_sha256": report["output_sha256"],
                "deactivated_roots": roots,
                "authored_support_repairs": report.get("authored_support_repairs", []),
            }
        )
    if previous_output != source_sha:
        raise ValueError(f"final audited USDZ is not bound to last repair: {bid}")
    return {
        "status": "PASS",
        "iteration_count": len(history),
        "final_output_sha256": previous_output,
        "protected_door_or_appliance_roots_deactivated": [],
        "deactivated_root_count": len(deactivated_roots),
        "deactivated_roots": deactivated_roots,
        "wall_repair_group_count": len(wall_groups),
        "wall_repair_groups": wall_groups,
        "all_wall_repairs_are_complete_visual_collision_pairs": True,
        "reports": report_rows,
    }


def audit_one(
    item: dict[str, Any],
    scratch_root: Path,
    normalization_rows: dict[str, dict[str, Any]],
    g25_root: Path,
    repair_histories: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    started = time.monotonic()
    bid = item["bid"]
    source: Path = item["source"]
    source_sha = file_sha256(source)
    repair = validate_repair_history(bid, source_sha, repair_histories.get(bid, []))
    stage = Usd.Stage.Open(str(source), load=Usd.Stage.LoadAll)
    if stage is None or not stage.GetPrimAtPath("/World").IsValid():
        raise ValueError(f"could not open {source} with /World")
    collision = collision_inventory(stage)
    doors = discover_door_scope(stage)
    scratch = scratch_root / f"{bid}.storey_bands_scratch.npz"
    cavity = cavity_from_scratch(
        stage, scratch, Path(str(scratch) + ".binding.json"), source_sha
    )
    normalization = normalization_rows.get(bid)
    if not isinstance(normalization, dict):
        raise ValueError(f"normalization summary has no row for {bid}")
    g25_path = g25_root / f"{bid}.json"
    g25 = json.loads(g25_path.read_text(encoding="utf-8"))
    if g25.get("building") != bid:
        raise ValueError(f"G25 identity differs for {bid}")
    checks = {
        "stage_opens_with_world": True,
        "all_enabled_approximations_none": collision[
            "non_none_approximation_count"
        ]
        == 0,
        "zero_rigid_bodies": collision["rigid_body_count"] == 0,
        "door_roots_present": doors["door_object_root_count"] > 0,
        "door_leaf_xforms_present": doors["door_leaf_xform_count"] > 0,
        "door_visual_meshes_remain_present": doors["door_visual_mesh_count"] > 0,
        "all_target_leaf_and_handle_colliders_disabled": doors[
            "enabled_target_collider_count"] == 0,
        "frame_or_jamb_colliders_remain_enabled": doors[
            "frame_or_jamb_enabled_collider_count"] > 0,
        "all_revolute_joints_classified": doors[
            "unclassified_revolute_joint_count"
        ]
        == 0,
        "pre_repair_normalization_and_exact_doorway_clearance_lineage_pass": (
            normalization.get("status") == "PASS"
            and normalization.get("checks", {}).get("A4_v2_doorway_clearance") is True),
        "repair_evidence_chain_bound_to_final_geometry": repair["status"] == "PASS",
        "no_protected_door_or_appliance_root_deactivated": not repair[
            "protected_door_or_appliance_roots_deactivated"
        ],
        "wall_repairs_remove_complete_visual_collision_sibling_pairs": repair[
            "all_wall_repairs_are_complete_visual_collision_pairs"
        ],
        "every_remaining_enabled_collider_included_in_frozen_G25_pass": (
            g25.get("status") == "PASS"
            and g25.get("storeys", {}).get("Floor1", {}).get("status") == "PASS"
            and g25.get("storeys", {}).get("Floor2", {}).get("status") == "PASS"),
        "cavity_measurement_present": cavity["furniture_instances_measured"] > 0,
    }
    return {
        "schema": "CollisionDoorAuditOneV1",
        "created_at_utc": utc_now(),
        "status": "PASS" if all(checks.values()) else "FAIL",
        "building": bid,
        "group": item["group"],
        "source_usdz": str(source),
        "source_usdz_bytes": source.stat().st_size,
        "source_usdz_sha256": source_sha,
        "read_only_audit": True,
        "visual_rendering_performed": False,
        "collision": collision,
        "doors": doors,
        "normalization_row": normalization,
        "normalization_role": "pre-repair lineage; live final door/collision state audited above",
        "repair_evidence": repair,
        "g25_report": str(g25_path),
        "g25_report_sha256": file_sha256(g25_path),
        "wall_collider_policy": (
            "no collision-only or invisible wall omission is allowed; each repaired wall "
            "is a minimum-cut-bound complete visual+collision sibling pair, and frozen "
            "G25 includes every collider still enabled in the final composed stage"),
        "cavity_signal": cavity,
        "checks": checks,
        "elapsed_seconds": time.monotonic() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery-root", required=True, type=Path)
    parser.add_argument("--scratch-root", required=True, type=Path)
    parser.add_argument("--normalization-summary", required=True, type=Path)
    parser.add_argument("--g25-root", required=True, type=Path)
    parser.add_argument("--repair-summary", required=True, action="append", type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    delivery_root = args.delivery_root.resolve()
    scratch_root = args.scratch_root.resolve()
    normalization_path = args.normalization_summary.resolve()
    normalization_doc = json.loads(normalization_path.read_text(encoding="utf-8"))
    normalization_rows = {row["BID"]: row for row in normalization_doc.get("rows", [])}
    if (normalization_doc.get("status") != "PASS"
            or len(normalization_rows) != EXPECTED_TOTAL):
        raise ValueError("normalization summary is not exact-10 PASS")
    g25_root = args.g25_root.resolve()
    repair_histories, repair_summary_bindings = load_repair_histories(
        args.repair_summary
    )
    output_root = args.output_root.resolve()
    sources = collect_sources(delivery_root)
    by_building = output_root / "by_building"
    by_building.mkdir(parents=True, exist_ok=True)
    rows = []
    errors = []
    for index, item in enumerate(sources, 1):
        path = by_building / f"{item['bid']}.json"
        if args.resume and path.is_file():
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("building") != item["bid"]:
                raise ValueError(f"resume row identity mismatch: {path}")
            rows.append(row)
            print(f"AUDIT {index}/{EXPECTED_TOTAL} RESUME {item['bid']}", flush=True)
            continue
        try:
            row = audit_one(
                item, scratch_root, normalization_rows, g25_root, repair_histories
            )
            atomic_json(path, row)
            rows.append(row)
            print(
                f"AUDIT {index}/{EXPECTED_TOTAL} {row['status']} {item['bid']} "
                f"colliders={row['collision']['enabled_mesh_collider_count']} "
                f"sealed={row['cavity_signal']['suspected_sealed_cavity_count']} "
                f"seconds={row['elapsed_seconds']:.1f}",
                flush=True,
            )
        except Exception as error:
            errors.append({"building": item["bid"], "error": repr(error)})
            print(
                f"AUDIT {index}/{EXPECTED_TOTAL} ERROR {item['bid']} {error!r}",
                file=sys.stderr,
                flush=True,
            )
        atomic_json(
            output_root / "COLLISION_AUDIT.progress.json",
            {
                "schema": "CollisionAudit54ProgressV1",
                "updated_at_utc": utc_now(),
                "expected": EXPECTED_TOTAL,
                "completed": len(rows),
                "errors": errors,
            },
        )

    approximation_exceptions = [
        {
            "building": row["building"],
            "colliders": row["collision"]["non_none_approximation_colliders"],
        }
        for row in rows
        if row["collision"]["non_none_approximation_count"]
    ]
    rigid_exceptions = [
        {
            "building": row["building"],
            "prims": row["collision"]["rigid_body_prims"],
        }
        for row in rows
        if row["collision"]["rigid_body_count"]
    ]
    sealed = [
        {
            "building": row["building"],
            "instances": row["cavity_signal"]["suspected_sealed_cavities"],
        }
        for row in rows
        if row["cavity_signal"]["suspected_sealed_cavity_count"]
    ]
    high_hull_fill = [
        {
            "building": row["building"],
            "instances": [item for item in row["cavity_signal"]["instances"]
                          if item.get("hull_fill_ratio", -1.0)
                          >= MEDIAN_WARNING_THRESHOLD],
        }
        for row in rows
        if any(item.get("hull_fill_ratio", -1.0) >= MEDIAN_WARNING_THRESHOLD
               for item in row["cavity_signal"]["instances"])
    ]
    aggregate = {
        "schema": "CollisionAuditFactory10V2",
        "created_at_utc": utc_now(),
        "status": (
            "PASS" if (len(rows) == EXPECTED_TOTAL and not errors
                       and all(row.get("status") == "PASS" for row in rows))
            else "FAIL"
        ),
        "scope": {
            "expected_buildings": EXPECTED_TOTAL,
            "observed_buildings": len(rows),
            "factory": sum(row["group"] == "Factory" for row in rows),
            "unique_buildings": len({row["building"] for row in rows}),
        },
        "method": {
            "usd_inventory": "fresh composed-stage traversal for every USDZ",
            "cavity_signal": (
                "5 cm enabled-collider occupancy inside each furniture convex hull; "
                "ratio >= 0.99 is suspected sealed cavity"
            ),
            "rendering": "none",
            "audit_modified_geometry": False,
            "audited_geometry_is_post_repair": True,
            "wall_colliders_ignored": False,
            "repair_summaries": repair_summary_bindings,
            "normalization_summary": str(normalization_path),
            "normalization_summary_sha256": file_sha256(normalization_path),
        },
        "summary": {
            "total_enabled_mesh_colliders": sum(
                row["collision"]["enabled_mesh_collider_count"] for row in rows
            ),
            "non_none_approximation_building_count": len(approximation_exceptions),
            "rigid_body_building_count": len(rigid_exceptions),
            "suspected_sealed_cavity_building_count": len(sealed),
            "suspected_sealed_cavity_instance_count": sum(
                len(item["instances"]) for item in sealed
            ),
            "high_hull_fill_threshold": MEDIAN_WARNING_THRESHOLD,
            "high_hull_fill_building_count": len(high_hull_fill),
            "high_hull_fill_instance_count": sum(
                len(item["instances"]) for item in high_hull_fill),
        },
        "approximation_exceptions": approximation_exceptions,
        "rigid_body_exceptions": rigid_exceptions,
        "suspected_sealed_cavities": sealed,
        "high_hull_fill_suspects": high_hull_fill,
        "errors": errors,
        "rows": rows,
    }
    atomic_json(output_root / "COLLISION_AUDIT.json", aggregate)
    print(
        json.dumps(
            {
                "status": aggregate["status"],
                "buildings": len(rows),
                **aggregate["summary"],
            },
            indent=2,
        ),
        flush=True,
    )
    return 0 if aggregate["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
