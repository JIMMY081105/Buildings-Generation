#!/usr/bin/env python3
"""Repair wall-collider selection and stair semantic hierarchy in one USDZ."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import tempfile
from typing import Any

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils


WALL_ROLE_RE = re.compile(
    r"^(?P<stem>wall_[0-9]+_[0-9]+)_(?P<role>visual|collision)_(?P<index>[0-9]+)$"
)
GENERIC_STAIR_COLLIDER_RE = re.compile(r"^geom_[0-9]+_col[0-9]+$")
STAIR_ROOT = Sdf.Path("/World/Stair")
STAIR_COLLISION = Sdf.Path("/World/Stair/world/collision")
OBSTACLE_ROOT = Sdf.Path("/World/StairObstacles")
OBSTACLE_WORLD = Sdf.Path("/World/StairObstacles/world")
OBSTACLE_COLLISION = Sdf.Path("/World/StairObstacles/world/collision")
INTEGRATED_TREAD = "integrated_tread_collision"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _enabled(prim: Usd.Prim) -> bool:
    return bool(
        prim.IsA(UsdGeom.Mesh)
        and prim.HasAPI(UsdPhysics.CollisionAPI)
        and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False
    )


def _array_hash(digest: Any, value: Any, dtype: Any) -> None:
    array = np.ascontiguousarray(np.asarray(value, dtype=dtype))
    digest.update(str(array.shape).encode("ascii"))
    digest.update(array.tobytes())


def _visual_geometry_hash(stage: Usd.Stage) -> str:
    """Hash active visible default/render mesh topology and world placement."""
    digest = hashlib.sha256()
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        imageable = UsdGeom.Imageable(prim)
        purpose = str(imageable.ComputePurpose() or UsdGeom.Tokens.default_)
        if purpose == "guide" or imageable.ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        if not points or not counts or not indices:
            continue
        digest.update(str(prim.GetPath()).encode("utf-8"))
        digest.update(purpose.encode("utf-8"))
        _array_hash(digest, points, np.float64)
        _array_hash(digest, counts, np.int64)
        _array_hash(digest, indices, np.int64)
        _array_hash(digest, cache.GetLocalToWorldTransform(prim), np.float64)
    return digest.hexdigest()


def _world_mesh_hashes(
    stage: Usd.Stage, prims: list[Usd.Prim]
) -> dict[str, str]:
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    result: dict[str, str] = {}
    for prim in prims:
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        if not points or not counts or not indices:
            raise ValueError(f"empty enabled stair collider: {prim.GetPath()}")
        transform = cache.GetLocalToWorldTransform(prim)
        world = [transform.Transform(Gf.Vec3d(*point)) for point in points]
        digest = hashlib.sha256()
        _array_hash(digest, world, np.float64)
        _array_hash(digest, counts, np.int64)
        _array_hash(digest, indices, np.int64)
        name = prim.GetName()
        if name in result:
            raise ValueError(f"duplicate stair collider name: {name}")
        result[name] = digest.hexdigest()
    return dict(sorted(result.items()))


def _copy_authored_properties(
    stage: Usd.Stage,
    source: Usd.Prim,
    target: Usd.Prim,
    names: set[str] | None = None,
    prefixes: tuple[str, ...] = (),
) -> list[str]:
    """Copy selected property specs without collapsing xform-op semantics."""
    layer = stage.GetRootLayer()
    copied = []
    for prop in source.GetAuthoredProperties():
        name = prop.GetName()
        if not ((names is not None and name in names) or name.startswith(prefixes)):
            continue
        destination = target.GetPath().AppendProperty(name)
        if not Sdf.CopySpec(layer, prop.GetPath(), layer, destination):
            raise RuntimeError(
                f"failed to copy authored property {prop.GetPath()} -> {destination}"
            )
        copied.append(name)
    return sorted(copied)


def _clone_xform_specs(
    stage: Usd.Stage, source: Usd.Prim, destination: Sdf.Path
) -> tuple[Usd.Prim, list[str]]:
    target = UsdGeom.Xform.Define(stage, destination).GetPrim()
    copied = _copy_authored_properties(
        stage,
        source,
        target,
        names={"xformOpOrder"},
        prefixes=("xformOp:",),
    )
    source_names = sorted(
        prop.GetName()
        for prop in source.GetAuthoredProperties()
        if prop.GetName() == "xformOpOrder" or prop.GetName().startswith("xformOp:")
    )
    if copied != source_names:
        raise ValueError(
            f"xform property copy incomplete for {source.GetPath()}: "
            f"source={source_names}, copied={copied}"
        )
    return target, copied


def _repair_wall_colliders(
    stage: Usd.Stage, allowed_visual_paths: set[str]
) -> dict[str, Any]:
    disabled = []
    group_rows = []
    for storey in ("Floor1", "Floor2"):
        geometry = stage.GetPrimAtPath(f"/World/{storey}/Geometry")
        if not geometry or not geometry.IsValid():
            raise ValueError(f"missing /World/{storey}/Geometry")
        groups: dict[str, dict[str, list[Usd.Prim]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for child in geometry.GetChildren():
            match = WALL_ROLE_RE.match(child.GetName())
            if match and child.IsA(UsdGeom.Mesh):
                groups[match.group("stem")][match.group("role")].append(child)
        for stem, roles in sorted(groups.items()):
            guides = [prim for prim in roles.get("collision", []) if _enabled(prim)]
            visuals = [
                prim for prim in roles.get("visual", [])
                if _enabled(prim) and str(prim.GetPath()) in allowed_visual_paths
            ]
            if not guides or not visuals:
                continue
            if not all(
                str(UsdGeom.Imageable(prim).ComputePurpose()) == "guide"
                for prim in guides
            ):
                raise ValueError(f"same-stem collision siblings are not guide: {stem}")
            for prim in visuals:
                UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr().Set(False)
                disabled.append(str(prim.GetPath()))
            group_rows.append({
                "storey": storey,
                "logical_wall_stem": stem,
                "disabled_visual_fallback_colliders": sorted(
                    str(prim.GetPath()) for prim in visuals
                ),
                "retained_guide_colliders": sorted(
                    str(prim.GetPath()) for prim in guides
                ),
            })
    if not disabled:
        raise ValueError("no shadowing flat visual wall colliders were found")
    if set(disabled) != allowed_visual_paths:
        missing = sorted(allowed_visual_paths - set(disabled))
        unexpected = sorted(set(disabled) - allowed_visual_paths)
        raise ValueError(
            f"repair does not exactly match diagnosed wall paths; "
            f"missing={missing}, unexpected={unexpected}"
        )
    return {
        "logical_wall_group_count": len(group_rows),
        "disabled_visual_fallback_collider_count": len(disabled),
        "disabled_visual_fallback_colliders": sorted(disabled),
        "groups": group_rows,
    }


def _repair_stair_hierarchy(stage: Usd.Stage) -> dict[str, Any]:
    if stage.GetPrimAtPath(OBSTACLE_ROOT).IsValid():
        raise ValueError(f"destination already exists: {OBSTACLE_ROOT}")
    stair = stage.GetPrimAtPath(STAIR_ROOT)
    stair_world = stage.GetPrimAtPath(STAIR_ROOT.AppendChild("world"))
    collision = stage.GetPrimAtPath(STAIR_COLLISION)
    if not stair.IsValid() or not stair_world.IsValid() or not collision.IsValid():
        raise ValueError("missing canonical /World/Stair/world/collision hierarchy")
    enabled_children = [prim for prim in collision.GetChildren() if _enabled(prim)]
    integrated = [prim for prim in enabled_children if prim.GetName() == INTEGRATED_TREAD]
    generic = [prim for prim in enabled_children if prim.GetName() != INTEGRATED_TREAD]
    if len(integrated) != 1:
        raise ValueError(
            f"expected exactly one integrated tread collider, found {len(integrated)}"
        )
    unexpected = [str(prim.GetPath()) for prim in generic
                  if not GENERIC_STAIR_COLLIDER_RE.match(prim.GetName())]
    if unexpected:
        raise ValueError(f"unexpected generic stair collider names: {unexpected}")
    if not generic:
        raise ValueError("no generic stair obstacle colliders found")
    before_hashes = _world_mesh_hashes(stage, generic)

    obstacle, obstacle_xform_properties = _clone_xform_specs(
        stage, stair, OBSTACLE_ROOT
    )
    _, obstacle_world_xform_properties = _clone_xform_specs(
        stage, stair_world, OBSTACLE_WORLD
    )
    obstacle.SetCustomDataByKey(
        "navigationRole", "physical_stair_obstacles_not_arrival_support"
    )

    mapping = {
        str(prim.GetPath()): str(OBSTACLE_COLLISION.AppendChild(prim.GetName()))
        for prim in generic
    }
    integrated_before_path = integrated[0].GetPath()

    # This USD build does not reliably retain a queue of many sibling moves.
    # Move the containing scope in one edit, then move only the integrated tread
    # back in a second independent edit.
    editor = Usd.NamespaceEditor(stage)
    editor.MovePrimAtPath(STAIR_COLLISION, OBSTACLE_COLLISION)
    can_apply = editor.CanApplyEdits()
    if not can_apply:
        raise ValueError(f"cannot move stair collision scope: {can_apply}")
    if not editor.ApplyEdits():
        raise RuntimeError("NamespaceEditor failed to move stair collision scope")

    moved_scope = stage.GetPrimAtPath(OBSTACLE_COLLISION)
    if not moved_scope.IsValid():
        raise ValueError("moved stair collision scope is missing")
    replacement_scope = UsdGeom.Scope.Define(stage, STAIR_COLLISION).GetPrim()
    copied_scope_properties = _copy_authored_properties(
        stage,
        moved_scope,
        replacement_scope,
        names={"purpose", "visibility"},
    )
    if UsdGeom.Imageable(replacement_scope).ComputeVisibility() != UsdGeom.Tokens.invisible:
        raise ValueError("replacement semantic stair collision scope is not invisible")

    integrated_moved_path = OBSTACLE_COLLISION.AppendChild(INTEGRATED_TREAD)
    integrated_restored_path = STAIR_COLLISION.AppendChild(INTEGRATED_TREAD)
    if not stage.GetPrimAtPath(integrated_moved_path).IsValid():
        raise ValueError("integrated tread did not move with collision scope")
    editor = Usd.NamespaceEditor(stage)
    editor.MovePrimAtPath(integrated_moved_path, integrated_restored_path)
    can_apply = editor.CanApplyEdits()
    if not can_apply:
        raise ValueError(f"cannot restore integrated tread prim: {can_apply}")
    if not editor.ApplyEdits():
        raise RuntimeError("NamespaceEditor failed to restore integrated tread prim")
    if integrated_restored_path != integrated_before_path:
        raise AssertionError("integrated tread canonical path changed")

    moved = [stage.GetPrimAtPath(Sdf.Path(path)) for path in mapping.values()]
    if not all(prim.IsValid() and _enabled(prim) for prim in moved):
        raise ValueError("moved stair obstacle collision was not preserved")
    after_hashes = _world_mesh_hashes(stage, moved)
    if before_hashes != after_hashes:
        raise ValueError("stair obstacle world geometry changed during reparenting")
    remaining = [
        prim for prim in stage.Traverse()
        if _enabled(prim)
        and (str(prim.GetPath()) == "/World/Stair"
             or str(prim.GetPath()).startswith("/World/Stair/"))
    ]
    if [prim.GetName() for prim in remaining] != [INTEGRATED_TREAD]:
        raise ValueError(
            "semantic stair root must retain only integrated tread collision; got "
            + repr([str(prim.GetPath()) for prim in remaining])
        )
    return {
        "moved_generic_obstacle_collider_count": len(mapping),
        "path_mapping": mapping,
        "world_geometry_sha256_before_by_name": before_hashes,
        "world_geometry_sha256_after_by_name": after_hashes,
        "world_geometry_preserved": True,
        "obstacle_root_xform_properties_copied": obstacle_xform_properties,
        "obstacle_world_xform_properties_copied": obstacle_world_xform_properties,
        "replacement_scope_properties_copied": copied_scope_properties,
        "integrated_tread_path_before": str(integrated_before_path),
        "integrated_tread_path_after": str(integrated_restored_path),
        "semantic_stair_enabled_colliders_after": [
            str(prim.GetPath()) for prim in remaining
        ],
        "physical_obstacle_collisions_preserved": True,
    }


def _enabled_collider_count(stage: Usd.Stage) -> int:
    return sum(_enabled(prim) for prim in stage.Traverse())


def _repair_root(root: Path, allowed_visual_paths: set[str]) -> dict[str, Any]:
    stage = Usd.Stage.Open(str(root), load=Usd.Stage.LoadAll)
    if stage is None or not stage.GetPrimAtPath("/World").IsValid():
        raise ValueError(f"could not open extracted root layer: {root}")
    before_visual = _visual_geometry_hash(stage)
    before_enabled = _enabled_collider_count(stage)
    wall = _repair_wall_colliders(stage, allowed_visual_paths)
    stair = _repair_stair_hierarchy(stage)
    if not stage.GetRootLayer().Save():
        raise RuntimeError(f"failed to save repaired layer: {root}")

    check = Usd.Stage.Open(str(root), load=Usd.Stage.LoadAll)
    if check is None:
        raise ValueError("repaired extracted root did not reopen")
    after_visual = _visual_geometry_hash(check)
    after_enabled = _enabled_collider_count(check)
    if before_visual != after_visual:
        raise ValueError("visual geometry changed during collision-only repair")
    expected_enabled = before_enabled - wall[
        "disabled_visual_fallback_collider_count"
    ]
    if after_enabled != expected_enabled:
        raise ValueError(
            f"enabled collider count {after_enabled} != expected {expected_enabled}"
        )
    remaining_targets = [
        path
        for path in sorted(allowed_visual_paths)
        if not check.GetPrimAtPath(path).IsValid() or _enabled(check.GetPrimAtPath(path))
    ]
    if remaining_targets:
        raise ValueError(
            "an evidence-bound shadowing visual wall collider remains enabled: "
            + repr(remaining_targets)
        )
    lost_guides = [
        path
        for group in wall["groups"]
        for path in group["retained_guide_colliders"]
        if not check.GetPrimAtPath(path).IsValid() or not _enabled(check.GetPrimAtPath(path))
    ]
    if lost_guides:
        raise ValueError("an intended guide wall collider was lost: " + repr(lost_guides))
    return {
        "visual_geometry_sha256_before": before_visual,
        "visual_geometry_sha256_after": after_visual,
        "visual_geometry_modified": False,
        "enabled_mesh_collider_count_before": before_enabled,
        "enabled_mesh_collider_count_after": after_enabled,
        "wall_repair": wall,
        "stair_hierarchy_repair": stair,
    }


def _exclusive_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _safe_usdz_member_names(source: Path) -> list[str]:
    archive = Sdf.ZipFile.Open(str(source))
    if not archive:
        raise ValueError(f"could not open USDZ archive: {source}")
    names = list(archive.GetFileNames())
    if not names or names[0] != "building.usda" or len(names) != len(set(names)):
        raise ValueError("USDZ must have one unique building.usda first member")
    for name in names:
        member = PurePosixPath(name)
        if member.is_absolute() or ".." in member.parts or not name:
            raise ValueError(f"unsafe USDZ archive member: {name!r}")
    return names


def _payload_manifest_from_files(
    root: Path, names: list[str], *, exclude_root: bool
) -> dict[str, str]:
    result = {}
    for name in names:
        if exclude_root and name == "building.usda":
            continue
        path = root.joinpath(*PurePosixPath(name).parts)
        if not path.is_file():
            raise ValueError(f"extracted USDZ member is missing: {name}")
        result[name] = _sha256(path)
    return result


def _manifest_digest(manifest: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(manifest.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _write_aligned_usdz(
    extracted: Path, destination: Path, member_names: list[str]
) -> None:
    root = extracted / "building.usda"
    ordered = ["building.usda"] + sorted(
        name for name in member_names if name != "building.usda"
    )
    with Sdf.ZipFileWriter.CreateNew(str(destination)) as writer:
        for name in ordered:
            path = extracted.joinpath(*PurePosixPath(name).parts)
            added = writer.AddFile(str(path), name)
            if added != name:
                raise RuntimeError(f"USDZ writer AddFile({name!r}) returned {added!r}")


def _verify_aligned_usdz(
    package: Path,
    expected_names: list[str],
    expected_non_root_payloads: dict[str, str],
) -> dict[str, Any]:
    archive = Sdf.ZipFile.Open(str(package))
    if not archive:
        raise ValueError(f"could not reopen repaired USDZ archive: {package}")
    names = list(archive.GetFileNames())
    if not names or names[0] != "building.usda":
        raise ValueError("repaired USDZ root layer is not first")
    if set(names) != set(expected_names) or len(names) != len(expected_names):
        raise ValueError("repaired USDZ member-name set differs from source")
    output_non_root_payloads = {}
    for name in names:
        info = archive.GetFileInfo(name)
        if (
            info.compressionMethod != 0
            or info.encrypted
            or info.size != info.uncompressedSize
            or info.dataOffset % 64 != 0
        ):
            raise ValueError(f"invalid USDZ alignment/storage for member {name}")
        if name != "building.usda":
            output_non_root_payloads[name] = hashlib.sha256(
                archive.GetFile(name)
            ).hexdigest()
    if output_non_root_payloads != expected_non_root_payloads:
        changed = sorted(
            name
            for name in set(output_non_root_payloads) | set(expected_non_root_payloads)
            if output_non_root_payloads.get(name) != expected_non_root_payloads.get(name)
        )
        raise ValueError(f"non-root USDZ payloads changed: {changed}")
    return {
        "member_count": len(names),
        "root_layer_first": True,
        "all_members_uncompressed_and_aligned_64_bytes": True,
        "non_root_member_count": len(output_non_root_payloads),
        "non_root_payload_manifest_sha256": _manifest_digest(
            output_non_root_payloads
        ),
        "non_root_payloads_unchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--building", required=True)
    parser.add_argument("--root-cause", required=True, type=Path)
    parser.add_argument("--stage-d-report", type=Path)
    args = parser.parse_args()
    source = args.input.expanduser().resolve()
    output = args.output.expanduser().resolve()
    report = args.report.expanduser().resolve()
    root_cause = args.root_cause.expanduser().resolve()
    if not source.is_file() or not root_cause.is_file():
        raise FileNotFoundError(source if not source.is_file() else root_cause)
    if output.exists() or report.exists():
        raise FileExistsError(f"refusing existing output/report: {output}, {report}")
    root_cause_document = json.loads(root_cause.read_text(encoding="utf-8"))
    shadow_record = root_cause_document["evidence"][
        "005_flat_sibling_collider_shadowing_exact50.json"
    ]
    shadow_path = Path(shadow_record["path"])
    if _sha256(shadow_path) != shadow_record["sha256"]:
        raise ValueError("root-cause wall-shadowing evidence hash differs")
    shadow_document = json.loads(shadow_path.read_text(encoding="utf-8"))
    building_evidence = next(
        (row for row in shadow_document["buildings"]
         if row["building"] == args.building),
        None,
    )
    if building_evidence is None:
        raise ValueError(f"building absent from exact50 root-cause evidence: {args.building}")
    source_hash = _sha256(source)
    if (
        source != Path(building_evidence["source_usdz"]).resolve()
        or source_hash != building_evidence["source_usdz_sha256"]
    ):
        raise ValueError(
            "input USDZ does not exactly match the diagnosed source binding"
        )
    allowed_visual_paths = {
        path
        for floor in building_evidence["storeys"].values()
        for group in floor["groups"]
        for path in group["enabled_continuous_visual_collider_paths"]
    }
    if not allowed_visual_paths:
        raise ValueError("root-cause evidence authorizes no wall repair paths")
    source_members = _safe_usdz_member_names(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".navigation_repair_", dir=str(output.parent)
    ) as directory:
        staging = Path(directory)
        extracted = staging / "extracted"
        if not UsdUtils.ExtractUsdzPackage(
            str(source), str(extracted), verbose=False, force=False
        ):
            raise RuntimeError(f"USDZ extraction failed: {source}")
        source_non_root_payloads = _payload_manifest_from_files(
            extracted, source_members, exclude_root=True
        )
        root = extracted / "building.usda"
        if not root.is_file():
            raise ValueError(f"{source} does not contain building.usda")
        changes = _repair_root(root, allowed_visual_paths)
        partial = staging / "repaired.partial.usdz"
        _write_aligned_usdz(extracted, partial, source_members)
        archive_checks = _verify_aligned_usdz(
            partial, source_members, source_non_root_payloads
        )
        partial_stage = Usd.Stage.Open(str(partial), load=Usd.Stage.LoadAll)
        if (
            partial_stage is None
            or not partial_stage.GetPrimAtPath("/World").IsValid()
            or partial_stage.GetDefaultPrim().GetPath() != Sdf.Path("/World")
        ):
            raise ValueError("repaired partial USDZ does not reopen at default /World")
        os.replace(partial, output)

    packaged = Usd.Stage.Open(str(output), load=Usd.Stage.LoadAll)
    if packaged is None or not packaged.GetPrimAtPath("/World").IsValid():
        raise ValueError("repaired USDZ does not reopen with /World")
    package_visual_hash = _visual_geometry_hash(packaged)
    if package_visual_hash != changes["visual_geometry_sha256_before"]:
        raise ValueError("packaging changed visual geometry")
    payload = {
        "schema": "Exact50NavigationColliderRepairV1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "building": args.building,
        "input": str(source),
        "input_sha256": source_hash,
        "output": str(output),
        "output_sha256": _sha256(output),
        "output_bytes": output.stat().st_size,
        "root_cause": str(root_cause),
        "root_cause_sha256": _sha256(root_cause),
        "wall_shadowing_evidence": str(shadow_path.resolve()),
        "wall_shadowing_evidence_sha256": _sha256(shadow_path),
        "diagnosed_wall_visual_path_count": len(allowed_visual_paths),
        "protected_gate_modified": False,
        "formal_threshold_modified": False,
        "repair_scope": "collision API selection and semantic hierarchy only",
        "lineage_classification": "post-Stage-D derivative collider-selection repair",
        "stage_d_report": (
            {"path": str(args.stage_d_report.resolve()),
             "sha256": _sha256(args.stage_d_report)}
            if args.stage_d_report is not None else None
        ),
        **changes,
        "archive_integrity": archive_checks,
        "packaged_visual_geometry_sha256": package_visual_hash,
        "checks": {
            "visual_geometry_unchanged": True,
            "guide_wall_aperture_colliders_retained": True,
            "shadowing_visual_wall_colliders_disabled": True,
            "stair_obstacle_collision_world_geometry_preserved": True,
            "semantic_stair_root_contains_only_integrated_tread": True,
            "non_root_archive_payloads_unchanged": True,
            "usdz_members_uncompressed_and_aligned_64_bytes": True,
            "output_reopens_with_world": True,
        },
        "status": "PASS",
    }
    _exclusive_json(report, payload)
    print(json.dumps({
        "status": payload["status"],
        "building": args.building,
        "output": str(output),
        "output_sha256": payload["output_sha256"],
        "report": str(report),
        "report_sha256": _sha256(report),
        "disabled_visual_wall_colliders": changes["wall_repair"][
            "disabled_visual_fallback_collider_count"],
        "moved_stair_obstacle_colliders": changes["stair_hierarchy_repair"][
            "moved_generic_obstacle_collider_count"],
    }, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
