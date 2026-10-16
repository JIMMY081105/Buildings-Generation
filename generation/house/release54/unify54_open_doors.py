#!/usr/bin/env python3
"""Open exact-54 doors by deactivating complete door-leaf/handle Xforms.

This is the authorised fallback (b): visual and collision geometry below each
door leaf/handle disappears, equivalent to the open panel being folded fully
against the wall.  Selection is structurally confined to doorway_*/doorframe_*
object roots; appliance doors cannot enter the target set.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

from pxr import Usd, UsdGeom, UsdPhysics, UsdUtils

from unify54_audit import (
    EXPECTED_TOTAL,
    active_stage_prims,
    atomic_json,
    collect_sources,
    collision_inventory,
    discover_door_scope,
    effectively_active,
    file_sha256,
    is_below,
    utc_now,
)


def active_target_geometry(stage: Usd.Stage, targets: list[str]) -> dict[str, Any]:
    all_meshes = []
    visual_meshes = []
    enabled_colliders = []
    for prim in active_stage_prims(stage):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        path = str(prim.GetPath())
        if not is_below(path, targets):
            continue
        all_meshes.append(path)
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False:
                enabled_colliders.append(path)
        else:
            visual_meshes.append(path)
    return {
        "active_mesh_count": len(all_meshes),
        "active_meshes": sorted(all_meshes),
        "active_visual_mesh_count": len(visual_meshes),
        "active_visual_meshes": sorted(visual_meshes),
        "enabled_mesh_collider_count": len(enabled_colliders),
        "enabled_mesh_colliders": sorted(enabled_colliders),
    }


def appliance_state(stage: Usd.Stage, paths: list[str]) -> dict[str, Any]:
    rows = []
    for path in paths:
        prim = stage.GetPrimAtPath(path)
        rows.append(
            {
                "path": path,
                "valid": prim.IsValid(),
                "effectively_active": effectively_active(prim) if prim.IsValid() else False,
                "type": prim.GetTypeName() if prim.IsValid() else None,
            }
        )
    return {"count": len(rows), "rows": rows}


def open_extracted(root_layer: Path) -> dict[str, Any]:
    stage = Usd.Stage.Open(str(root_layer), load=Usd.Stage.LoadAll)
    if stage is None or not stage.GetPrimAtPath("/World").IsValid():
        raise ValueError(f"could not open extracted root {root_layer}")
    before_collision = collision_inventory(stage)
    before_doors = discover_door_scope(stage)
    if before_doors["door_object_root_count"] == 0:
        raise ValueError("no doorway_/doorframe_ object roots")
    if before_doors["door_leaf_xform_count"] == 0:
        raise ValueError("no door-leaf Xforms")
    if before_doors["unclassified_revolute_joint_count"]:
        raise ValueError("one or more door-scope revolute joints are unclassified")
    targets = sorted(
        set(before_doors["door_leaf_xforms"] + before_doors["door_handle_xforms"])
    )
    if not targets:
        raise ValueError("empty door target set")
    if any(not is_below(path, before_doors["door_object_roots"]) for path in targets):
        raise ValueError("target escaped door object roots")
    before_target = active_target_geometry(stage, targets)
    before_appliance = appliance_state(
        stage, before_doors["appliance_door_like_prims_excluded"]
    )
    target_states_before = {}
    newly_deactivated = []
    already_inactive = []
    for path in targets:
        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid() or not prim.IsA(UsdGeom.Xform):
            raise ValueError(f"door target is not a valid Xform: {path}")
        active = effectively_active(prim)
        target_states_before[path] = active
        if active:
            if not prim.SetActive(False):
                raise RuntimeError(f"failed to deactivate {path}")
            newly_deactivated.append(path)
        else:
            already_inactive.append(path)
    if not stage.GetRootLayer().Save():
        raise RuntimeError(f"failed to save {root_layer}")

    check = Usd.Stage.Open(str(root_layer), load=Usd.Stage.LoadAll)
    if check is None or not check.GetPrimAtPath("/World").IsValid():
        raise ValueError("edited extracted stage does not reopen")
    still_active = []
    for path in targets:
        prim = check.GetPrimAtPath(path)
        if not prim.IsValid() or effectively_active(prim):
            still_active.append(path)
    after_target = active_target_geometry(check, targets)
    after_collision = collision_inventory(check)
    after_doors = discover_door_scope(check)
    after_appliance = appliance_state(
        check, before_doors["appliance_door_like_prims_excluded"]
    )
    before_frames = before_doors["frame_or_jamb_enabled_colliders"]
    after_frames = after_doors["frame_or_jamb_enabled_colliders"]
    expected_after = (
        before_collision["enabled_mesh_collider_count"]
        - before_target["enabled_mesh_collider_count"]
    )
    checks = {
        "all_selected_xforms_inactive": not still_active,
        "zero_active_target_meshes": after_target["active_mesh_count"] == 0,
        "zero_active_target_visual_meshes": after_target["active_visual_mesh_count"] == 0,
        "zero_enabled_target_colliders": after_target["enabled_mesh_collider_count"] == 0,
        "collider_count_delta_exact": after_collision["enabled_mesh_collider_count"]
        == expected_after,
        "frame_and_jamb_enabled_paths_unchanged": before_frames == after_frames,
        "appliance_door_like_prims_unchanged": before_appliance == after_appliance,
        "all_enabled_approximations_none_after": after_collision[
            "non_none_approximation_count"
        ]
        == 0,
        "zero_rigid_bodies_after": after_collision["rigid_body_count"] == 0,
    }
    if not all(checks.values()):
        raise ValueError("door-opening postconditions failed: " + repr(checks))
    return {
        "method": "fallback_b_deactivate_complete_door_leaf_and_handle_xforms",
        "method_rationale": (
            "revolute joints are authored, but delivered stages are static with zero "
            "rigid bodies; deactivating the complete leaf/handle Xforms gives a "
            "deterministic visual-and-collision open state"
        ),
        "scope_contract": before_doors["scope_contract"],
        "door_object_root_count": before_doors["door_object_root_count"],
        "target_xform_count": len(targets),
        "target_xforms": targets,
        "newly_deactivated_xform_count": len(newly_deactivated),
        "newly_deactivated_xforms": newly_deactivated,
        "already_inactive_xform_count": len(already_inactive),
        "already_inactive_xforms": already_inactive,
        "target_effective_state_before": target_states_before,
        "enabled_mesh_collider_count_before": before_collision[
            "enabled_mesh_collider_count"
        ],
        "enabled_mesh_collider_count_after": after_collision[
            "enabled_mesh_collider_count"
        ],
        "enabled_mesh_collider_delta": after_collision[
            "enabled_mesh_collider_count"
        ]
        - before_collision["enabled_mesh_collider_count"],
        "target_geometry_before": before_target,
        "target_geometry_after": after_target,
        "frame_or_jamb_enabled_collider_count": len(before_frames),
        "frame_or_jamb_enabled_colliders": before_frames,
        "appliance_door_like_prims_excluded": before_appliance,
        "checks": checks,
    }


def process_one(
    item: dict[str, Any], output_root: Path, runtime_tools: Path
) -> dict[str, Any]:
    sys.path.insert(0, str(runtime_tools))
    from apply_residual_navigation_repair_iterative_v4 import (
        _payload_manifest_from_files,
        _safe_usdz_member_names,
        _verify_aligned_usdz,
        _visual_geometry_hash,
        _write_aligned_usdz,
    )

    started = time.monotonic()
    bid = item["bid"]
    source: Path = item["source"]
    destination = output_root / bid / "scene" / "building.usdz"
    report_path = output_root / bid / "reports" / "door_opening.json"
    if destination.exists() or report_path.exists():
        raise FileExistsError(f"refusing existing output for {bid}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    source_sha = file_sha256(source)
    source_stage = Usd.Stage.Open(str(source), load=Usd.Stage.LoadAll)
    if source_stage is None:
        raise ValueError(f"could not open source {source}")
    visual_before = _visual_geometry_hash(source_stage)
    source_members = _safe_usdz_member_names(source)

    with tempfile.TemporaryDirectory(
        prefix=f".{bid}.open_doors_", dir=str(destination.parent)
    ) as temp_name:
        temp = Path(temp_name)
        extracted = temp / "extracted"
        if not UsdUtils.ExtractUsdzPackage(
            str(source), str(extracted), verbose=False, force=False
        ):
            raise RuntimeError(f"USDZ extraction failed: {source}")
        non_root_manifest = _payload_manifest_from_files(
            extracted, source_members, exclude_root=True
        )
        changes = open_extracted(extracted / "building.usda")
        partial = temp / "opened.partial.usdz"
        _write_aligned_usdz(extracted, partial, source_members)
        archive_checks = _verify_aligned_usdz(
            partial, source_members, non_root_manifest
        )
        os.replace(partial, destination)

    packaged = Usd.Stage.Open(str(destination), load=Usd.Stage.LoadAll)
    if packaged is None or not packaged.GetPrimAtPath("/World").IsValid():
        raise ValueError("packaged output does not reopen with /World")
    packaged_scope = discover_door_scope(packaged)
    targets = changes["target_xforms"]
    packaged_target = active_target_geometry(packaged, targets)
    packaged_appliance = appliance_state(
        packaged,
        [
            row["path"]
            for row in changes["appliance_door_like_prims_excluded"]["rows"]
        ],
    )
    packaged_still_active = [
        path
        for path in targets
        if (
            not packaged.GetPrimAtPath(path).IsValid()
            or effectively_active(packaged.GetPrimAtPath(path))
        )
    ]
    visual_after = _visual_geometry_hash(packaged)
    package_checks = {
        "package_reopens_with_world": True,
        "all_target_xforms_inactive": not packaged_still_active,
        "zero_active_target_meshes": packaged_target["active_mesh_count"] == 0,
        "appliance_exclusion_retained": packaged_appliance
        == changes["appliance_door_like_prims_excluded"],
        "visual_geometry_changed_or_was_already_open": (
            visual_after != visual_before
            or changes["newly_deactivated_xform_count"] == 0
        ),
    }
    if not all(package_checks.values()):
        raise ValueError("packaged door-opening checks failed: " + repr(package_checks))
    report = {
        "schema": "Exact54VisualCollisionDoorOpeningV1",
        "created_at_utc": utc_now(),
        "status": "PASS",
        "building": bid,
        "group": item["group"],
        "input": str(source),
        "input_bytes": source.stat().st_size,
        "input_sha256": source_sha,
        "output": str(destination),
        "output_bytes": destination.stat().st_size,
        "output_sha256": file_sha256(destination),
        "visual_geometry_sha256_before": visual_before,
        "visual_geometry_sha256_after": visual_after,
        "visual_rendering_performed": False,
        "protected_gate_modified": False,
        "formal_threshold_modified": False,
        "archive_integrity": archive_checks,
        "package_checks": package_checks,
        "elapsed_seconds": time.monotonic() - started,
        **changes,
    }
    atomic_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r3", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--runtime-tools", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--pilot-bid")
    args = parser.parse_args()
    r3 = args.r3.resolve()
    output_root = args.output_root.resolve()
    runtime_tools = args.runtime_tools.resolve()
    sources = collect_sources(r3)
    audit = json.loads(
        (r3 / "unify54" / "audit" / "COLLISION_AUDIT_54.json").read_text(
            encoding="utf-8"
        )
    )
    if audit.get("status") != "PASS" or audit.get("scope", {}).get(
        "observed_buildings"
    ) != EXPECTED_TOTAL:
        raise ValueError("exact-54 read-only audit must pass before geometry edits")
    audit_sha = file_sha256(
        r3 / "unify54" / "audit" / "COLLISION_AUDIT_54.json"
    )
    if args.pilot_bid:
        matches = [item for item in sources if item["bid"] == args.pilot_bid]
        if len(matches) != 1:
            raise ValueError(f"pilot BID is not in the exact-54 set: {args.pilot_bid}")
        row = process_one(matches[0], output_root, runtime_tools)
        print(
            json.dumps(
                {
                    "status": row["status"],
                    "building": row["building"],
                    "target_xforms": row["target_xform_count"],
                    "colliders_before": row["enabled_mesh_collider_count_before"],
                    "colliders_after": row["enabled_mesh_collider_count_after"],
                    "output_sha256": row["output_sha256"],
                },
                indent=2,
            )
        )
        return 0
    rows = []
    errors = []
    output_root.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(sources, 1):
        report_path = output_root / item["bid"] / "reports" / "door_opening.json"
        if args.resume and report_path.is_file():
            row = json.loads(report_path.read_text(encoding="utf-8"))
            if row.get("building") != item["bid"] or row.get("status") != "PASS":
                raise ValueError(f"invalid resume report: {report_path}")
            if file_sha256(Path(row["output"])) != row["output_sha256"]:
                raise ValueError(f"resume output SHA mismatch: {item['bid']}")
            rows.append(row)
            print(f"OPEN {index}/{EXPECTED_TOTAL} RESUME {item['bid']}", flush=True)
            continue
        try:
            row = process_one(item, output_root, runtime_tools)
            rows.append(row)
            print(
                f"OPEN {index}/{EXPECTED_TOTAL} PASS {item['bid']} "
                f"xforms={row['target_xform_count']} "
                f"colliders={row['enabled_mesh_collider_count_before']}->"
                f"{row['enabled_mesh_collider_count_after']} "
                f"seconds={row['elapsed_seconds']:.1f}",
                flush=True,
            )
        except Exception as error:
            errors.append({"building": item["bid"], "error": repr(error)})
            print(
                f"OPEN {index}/{EXPECTED_TOTAL} ERROR {item['bid']} {error!r}",
                file=sys.stderr,
                flush=True,
            )
            break
        atomic_json(
            output_root / "DOOR_OPENING_54.progress.json",
            {
                "schema": "DoorOpening54ProgressV1",
                "updated_at_utc": utc_now(),
                "expected": EXPECTED_TOTAL,
                "completed": len(rows),
                "errors": errors,
            },
        )

    aggregate = {
        "schema": "DoorOpening54V1",
        "created_at_utc": utc_now(),
        "status": "PASS" if len(rows) == EXPECTED_TOTAL and not errors else "FAIL",
        "expected_buildings": EXPECTED_TOTAL,
        "observed_buildings": len(rows),
        "group_a": sum(row["group"] == "A" for row in rows),
        "group_b": sum(row["group"] == "B" for row in rows),
        "read_only_audit": str(
            r3 / "unify54" / "audit" / "COLLISION_AUDIT_54.json"
        ),
        "read_only_audit_sha256": audit_sha,
        "method": "fallback_b_deactivate_complete_door_leaf_and_handle_xforms",
        "scope": (
            "door leaves and handles under doorway_*/doorframe_* only; "
            "refrigerator, microwave, oven and other appliance doors excluded"
        ),
        "visual_rendering_performed": False,
        "total_target_xforms": sum(row["target_xform_count"] for row in rows),
        "total_newly_deactivated_xforms": sum(
            row["newly_deactivated_xform_count"] for row in rows
        ),
        "total_enabled_colliders_before": sum(
            row["enabled_mesh_collider_count_before"] for row in rows
        ),
        "total_enabled_colliders_after": sum(
            row["enabled_mesh_collider_count_after"] for row in rows
        ),
        "errors": errors,
        "rows": rows,
    }
    atomic_json(output_root / "DOOR_OPENING_54.json", aggregate)
    print(
        json.dumps(
            {
                "status": aggregate["status"],
                "buildings": len(rows),
                "target_xforms": aggregate["total_target_xforms"],
                "colliders_before": aggregate["total_enabled_colliders_before"],
                "colliders_after": aggregate["total_enabled_colliders_after"],
            },
            indent=2,
        ),
        flush=True,
    )
    return 0 if aggregate["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
