#!/usr/bin/env python3
"""Author and audit the human-selected non-blocking locomotion-door policy.

Door assets are structurally identified.  Collision is disabled on the
swinging leaf branches and their attached handle branches.  The doorway frame,
jamb and lintel branches are intentionally left collidable.  Refrigerator,
cabinet and appliance doors are outside a doorway/doorframe object root and
are therefore never selected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from pxr import Sdf, Usd, UsdGeom, UsdPhysics


DOOR_OBJECT_RE = re.compile(
    r"^(?:doorway|doorframe)_[0-9a-f]{32}(?:_|$)", re.IGNORECASE)
LEAF_BRANCH_RE = re.compile(r"doorway_door(?:_|$)", re.IGNORECASE)
HANDLE_BRANCH_RE = re.compile(r"doorway_handle(?:_|$)", re.IGNORECASE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collision_enabled(prim: Usd.Prim) -> bool | None:
    if not prim.HasAPI(UsdPhysics.CollisionAPI):
        return None
    value = UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()
    return value is not False


def door_object_root(prim: Usd.Prim) -> Usd.Prim | None:
    current = prim
    while current and not current.IsPseudoRoot():
        if DOOR_OBJECT_RE.match(current.GetName()):
            return current
        current = current.GetParent()
    return None


def leaf_member_kind(prim: Usd.Prim) -> str | None:
    """Return leaf/handle only beneath a structural doorway object root."""
    root = door_object_root(prim)
    if root is None:
        return None
    current = prim
    while current and current != root:
        name = current.GetName()
        if LEAF_BRANCH_RE.search(name):
            return "leaf"
        if HANDLE_BRANCH_RE.search(name):
            return "attached_handle"
        current = current.GetParent()
    return None


def inventory(stage: Usd.Stage) -> dict[str, Any]:
    selected = []
    structural = []
    unrelated_door_named = []
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        enabled = collision_enabled(prim)
        if enabled is None:
            continue
        root = door_object_root(prim)
        kind = leaf_member_kind(prim)
        row = {
            "prim": str(prim.GetPath()),
            "type": prim.GetTypeName(),
            "collision_enabled": enabled,
            "instance_proxy": prim.IsInstanceProxy(),
        }
        if root is not None:
            row["door_object_root"] = str(root.GetPath())
            row["assembly_part"] = kind or "frame_or_jamb"
            (selected if kind is not None else structural).append(row)
        elif "door" in str(prim.GetPath()).lower():
            unrelated_door_named.append(row)
    roots = sorted({row["door_object_root"] for row in selected + structural})
    return {
        "door_object_root_count": len(roots),
        "door_object_roots": roots,
        "swinging_assembly_collider_count": len(selected),
        "swinging_assembly_collision_enabled_count": sum(
            row["collision_enabled"] for row in selected),
        "leaf_collider_count": sum(
            row["assembly_part"] == "leaf" for row in selected),
        "attached_handle_collider_count": sum(
            row["assembly_part"] == "attached_handle" for row in selected),
        "frame_or_jamb_collider_count": len(structural),
        "frame_or_jamb_collision_enabled_count": sum(
            row["collision_enabled"] for row in structural),
        "selected": selected,
        "frame_or_jamb": structural,
        "unrelated_door_named_collider_count": len(unrelated_door_named),
        "unrelated_door_named": unrelated_door_named,
    }


def disable_swinging_assemblies(stage: Usd.Stage) -> dict[str, Any]:
    """Author false collision opinions in the stage's current edit target."""
    before = inventory(stage)
    paths = [Sdf.Path(row["prim"]) for row in before["selected"]]
    if not paths:
        raise RuntimeError("no swinging doorway collision assemblies found")

    # MolmoSpaces doorway objects are commonly instanceable references.  A
    # child opinion cannot be authored beneath an instance proxy, so deinstance
    # only the containing doorway object in this building/overlay layer.  The
    # referenced source asset and every non-door object remain untouched.
    root_paths = sorted({Sdf.Path(row["door_object_root"])
                         for row in before["selected"]})
    deinstanced = []
    for path in root_paths:
        root = stage.GetPrimAtPath(path)
        if root and root.IsInstance():
            root.SetInstanceable(False)
            deinstanced.append(str(path))

    authored = []
    for path in paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or prim.IsInstanceProxy():
            raise RuntimeError(
                f"door leaf remains an unauthorable instance proxy: {path}")
        api = UsdPhysics.CollisionAPI(prim)
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            raise RuntimeError(f"selected door member lost CollisionAPI: {path}")
        api.CreateCollisionEnabledAttr().Set(False)
        authored.append(str(path))

    after = inventory(stage)
    after_selected = {row["prim"]: row for row in after["selected"]}
    missing = sorted(set(authored).difference(after_selected))
    still_enabled = sorted(
        path for path in authored
        if path in after_selected and after_selected[path]["collision_enabled"])
    frame_disabled = sorted(
        row["prim"] for row in after["frame_or_jamb"]
        if not row["collision_enabled"])
    checks = {
        "every_selected_swinging_member_still_composes": not missing,
        "every_selected_swinging_member_collision_disabled": not still_enabled,
        "every_frame_and_jamb_collision_enabled": not frame_disabled,
        "no_unrelated_door_named_collider_selected": all(
            "refrigerator" not in path.lower() and "cabinet" not in path.lower()
            for path in authored),
    }
    if not all(checks.values()):
        raise RuntimeError("door collision policy verification failed: "
                           + json.dumps(checks, sort_keys=True))
    return {
        "policy": "doors_must_not_block_locomotion",
        "selection": (
            "collision prims beneath doorway_door or doorway_handle branches "
            "of doorway/doorframe asset roots; frame/jamb branches excluded"),
        "deinstanced_door_object_roots": deinstanced,
        "authored_collision_disabled_count": len(authored),
        "authored_collision_disabled_prims": authored,
        "missing_after_authoring": missing,
        "still_enabled_after_authoring": still_enabled,
        "disabled_frame_or_jamb_prims": frame_disabled,
        "before": before,
        "after": after,
        "checks": checks,
        "status": "PASS",
    }


def make_overlay(source: Path, output: Path) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError("refusing to overwrite overlay: " + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    source_hash_before = sha256(source)
    layer = Sdf.Layer.CreateNew(str(output))
    relative = os.path.relpath(source, output.parent).replace("\\", "/")
    layer.subLayerPaths = [relative]
    layer.Save()

    stage = Usd.Stage.Open(str(output), Usd.Stage.LoadAll)
    if stage is None:
        raise RuntimeError("could not open composed overlay: " + str(output))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = stage.OverridePrim("/World")
    stage.SetDefaultPrim(world)
    result = disable_swinging_assemblies(stage)
    stage.GetRootLayer().Save()

    reopened = Usd.Stage.Open(str(output), Usd.Stage.LoadAll)
    if reopened is None:
        raise RuntimeError("overlay did not reopen: " + str(output))
    reopened_inventory = inventory(reopened)
    selected_disabled = (
        reopened_inventory["swinging_assembly_collider_count"] > 0
        and reopened_inventory["swinging_assembly_collision_enabled_count"] == 0)
    frame_enabled = (
        reopened_inventory["frame_or_jamb_collider_count"] > 0
        and reopened_inventory["frame_or_jamb_collision_enabled_count"]
        == reopened_inventory["frame_or_jamb_collider_count"])
    source_hash_after = sha256(source)
    checks = {
        "source_usdz_unchanged": source_hash_before == source_hash_after,
        "overlay_reopens": True,
        "root_up_axis_is_Z": str(UsdGeom.GetStageUpAxis(reopened)) == "Z",
        "root_meters_per_unit_is_1": abs(
            float(UsdGeom.GetStageMetersPerUnit(reopened)) - 1.0) < 1e-12,
        "root_default_prim_is_World": (
            reopened.GetDefaultPrim()
            and str(reopened.GetDefaultPrim().GetPath()) == "/World"),
        "sublayer_resolves": len(reopened.GetUsedLayers()) >= 2,
        "every_swinging_member_collision_disabled": selected_disabled,
        "every_frame_and_jamb_collision_enabled": frame_enabled,
    }
    if not all(checks.values()):
        raise RuntimeError("overlay reopen verification failed: "
                           + json.dumps(checks, sort_keys=True))
    return {
        "source": str(source),
        "source_sha256_before": source_hash_before,
        "source_sha256_after": source_hash_after,
        "overlay": str(output),
        "overlay_bytes": output.stat().st_size,
        "relative_sublayer": relative,
        "authoring": result,
        "reopened_inventory": reopened_inventory,
        "checks": checks,
        "status": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("stage", type=Path)
    inspect_parser.add_argument("--output", type=Path)
    overlay_parser = sub.add_parser("overlay-batch")
    overlay_parser.add_argument("--batch-root", type=Path, required=True)
    overlay_parser.add_argument("--output-dir", type=Path, required=True)
    overlay_parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "inspect":
        stage = Usd.Stage.Open(str(args.stage), Usd.Stage.LoadAll)
        if stage is None:
            raise RuntimeError("cannot open stage: " + str(args.stage))
        payload = inventory(stage)
        text = json.dumps(payload, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        print(text, end="")
        return 0

    buildings = sorted(
        path for path in (args.batch_root / "validation").iterdir()
        if path.is_dir())
    if len(buildings) != 10:
        raise RuntimeError(f"expected ten delivered buildings, found {len(buildings)}")
    results = []
    for index, building in enumerate(buildings, 1):
        source = building / "scene" / "building.usdz"
        output = args.output_dir / (building.name + "_doors_open.usda")
        print(f"overlay {index}/{len(buildings)} {building.name}", flush=True)
        results.append(make_overlay(source, output))
    payload = {
        "status": "PASS",
        "policy": "doors_must_not_block_locomotion",
        "batch": args.batch_root.name,
        "building_count": len(results),
        "source_usdz_modified_count": 0,
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "building_count": payload["building_count"],
        "report": str(args.report),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
