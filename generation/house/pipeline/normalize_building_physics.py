#!/usr/bin/env python3
"""Author complete static exact floor-object colliders without aggregation.

The previous implementation collapsed each storey to one aggregate made only
from already-enabled colliders. It therefore preserved an empty collision scene
and made G21 pass as 1 == 1. This repair flattens the composed building so every
object mesh is independently authorable, removes the two legacy aggregates, and
applies the mesh-derived floor collider contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from pxr import Usd, UsdPhysics

from floor_collider_contract import (
    FLOOR_ROOTS,
    LEGACY_AGGREGATE_NAME,
    approximation,
    enabled_mesh_collider,
    expected_mesh_prims,
    floor_profile,
)


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def profile_summary(profile: dict) -> dict:
    return {key: value for key, value in profile.items() if key != "object_records"}


def write_atomic(path: Path, document: dict) -> None:
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise FileExistsError(partial)
    partial.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def repair(stage_path: Path, report_path: Path) -> dict:
    stage_path, report_path = stage_path.resolve(), report_path.resolve()
    if not stage_path.is_file():
        raise FileNotFoundError(stage_path)
    if report_path.exists():
        raise FileExistsError("refusing to overwrite normalization report: " + str(report_path))
    partial_stage = stage_path.with_name(stage_path.stem + ".collider_repair.partial.usda")
    if partial_stage.exists():
        raise FileExistsError("stale collider-repair partial: " + str(partial_stage))

    before_hash = sha(stage_path)
    source = Usd.Stage.Open(str(stage_path), load=Usd.Stage.LoadAll)
    if source is None or not source.GetPrimAtPath("/World").IsValid():
        raise ValueError("could not compose source building: " + str(stage_path))
    before_profiles = {
        root: profile_summary(floor_profile(source, root)) for root in FLOOR_ROOTS
    }

    flattened = source.Flatten()
    flattened.documentation = ""
    if flattened.Export(str(partial_stage)) is not True:
        raise RuntimeError("failed to export flattened collider-repair stage")
    repaired = Usd.Stage.Open(str(partial_stage), load=Usd.Stage.LoadAll)
    if repaired is None or not repaired.GetPrimAtPath("/World").IsValid():
        raise ValueError("flattened collider-repair stage does not compose /World")
    instances = [str(prim.GetPath()) for prim in repaired.Traverse() if prim.IsInstance()]
    if instances:
        raise ValueError("flattening retained uneditable instances: " + str(instances[:20]))
    repaired.SetEditTarget(repaired.GetRootLayer())

    legacy_aggregates_removed = []
    for root in FLOOR_ROOTS:
        aggregate_path = root + "/" + LEGACY_AGGREGATE_NAME
        if repaired.GetPrimAtPath(aggregate_path).IsValid():
            if not repaired.RemovePrim(aggregate_path):
                raise RuntimeError("could not remove legacy aggregate: " + aggregate_path)
            legacy_aggregates_removed.append(aggregate_path)

    rigid_removed = []
    for prim in list(repaired.Traverse()):
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            if not prim.RemoveAPI(UsdPhysics.RigidBodyAPI):
                raise RuntimeError("could not remove RigidBodyAPI: " + str(prim.GetPath()))
            rigid_removed.append(str(prim.GetPath()))

    authored = []
    disabled_unexpected = []
    expected_by_floor = {}
    for root in FLOOR_ROOTS:
        expected, _ = expected_mesh_prims(repaired, root)
        expected_paths = {str(prim.GetPath()) for prim in expected}
        if not expected_paths:
            raise ValueError("floor has no mesh-derived collider expectation: " + root)
        expected_by_floor[root] = len(expected_paths)

        # A floor collider not selected by the per-object contract is stale or
        # redundant (notably the old aggregate/plane strategy) and must not let
        # coverage appear healthier than it is.
        for prim in list(repaired.Traverse()):
            path = str(prim.GetPath())
            if (path == root or path.startswith(root + "/")) and enabled_mesh_collider(prim):
                if path not in expected_paths:
                    UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr().Set(False)
                    disabled_unexpected.append(path)

        for prim in expected:
            if prim.IsInstanceProxy():
                raise ValueError("cannot author a collider on instance proxy: " + str(prim.GetPath()))
            collision = UsdPhysics.CollisionAPI.Apply(prim)
            collision.CreateCollisionEnabledAttr().Set(True)
            UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr().Set(
                UsdPhysics.Tokens.none)
            authored.append(str(prim.GetPath()))

    # Stair and Cut meshes remain outside FloorN. They are preserved, made
    # static above, and normalized to the same exact-triangle policy.
    stagewide_approximation_to_none = []
    for prim in repaired.Traverse():
        if not enabled_mesh_collider(prim):
            continue
        if approximation(prim) != "none":
            UsdPhysics.MeshCollisionAPI.Apply(
                prim).CreateApproximationAttr().Set(UsdPhysics.Tokens.none)
            stagewide_approximation_to_none.append(str(prim.GetPath()))

    repaired.GetRootLayer().Save()
    check = Usd.Stage.Open(str(partial_stage), load=Usd.Stage.LoadAll)
    after_profiles = {
        root: profile_summary(floor_profile(check, root)) for root in FLOOR_ROOTS
    }
    remaining_rigid = [
        str(prim.GetPath()) for prim in check.Traverse()
        if prim.HasAPI(UsdPhysics.RigidBodyAPI)
    ]
    enabled = [prim for prim in check.Traverse() if enabled_mesh_collider(prim)]
    approximation_mix = Counter(approximation(prim) for prim in enabled)
    checks = {
        "flattened_stage_has_no_instances": not instances,
        "both_floors_have_absolute_expectations": all(
            expected_by_floor[root] > 0 for root in FLOOR_ROOTS),
        "both_floor_coverage_contracts_pass": all(
            profile["status"] == "PASS" for profile in after_profiles.values()),
        "every_floor_object_mesh_expectation_enabled": all(
            profile["enabled_expected_mesh_collider_count"]
            == profile["expected_mesh_collider_count"]
            for profile in after_profiles.values()),
        "stagewide_zero_rigid_body_api": not remaining_rigid,
        "stagewide_all_enabled_approximation_none": bool(enabled)
        and set(approximation_mix) == {"none"},
        "legacy_aggregate_absent": all(
            not check.GetPrimAtPath(root + "/" + LEGACY_AGGREGATE_NAME).IsValid()
            for root in FLOOR_ROOTS),
    }
    document = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "stage": str(stage_path),
        "stage_sha256_before": before_hash,
        "method": (
            "flatten composed stage; per FloorN/Geometry object author all guide "
            "meshes, else all default/render meshes; static approximation=none; "
            "no per-floor aggregate"
        ),
        "floor_roots": list(FLOOR_ROOTS),
        "before_floor_profiles": before_profiles,
        "after_floor_profiles": after_profiles,
        # Compatibility key consumed by Stage-C manifest/ticket construction.
        "post_normalization_floor_profiles": after_profiles,
        "before_after_enabled_colliders_per_floor": {
            root: {
                "before": before_profiles[root]["total_enabled_mesh_collider_count"],
                "after": after_profiles[root]["total_enabled_mesh_collider_count"],
                "absolute_expected_after": after_profiles[root][
                    "expected_mesh_collider_count"],
            }
            for root in FLOOR_ROOTS
        },
        "legacy_aggregates_removed": legacy_aggregates_removed,
        "unexpected_floor_colliders_disabled": disabled_unexpected,
        "rigid_body_api_removed_count": len(rigid_removed),
        "authored_expected_mesh_collider_count": len(authored),
        "stagewide_approximation_to_none_count": len(stagewide_approximation_to_none),
        "stagewide_enabled_mesh_collider_count": len(enabled),
        "stagewide_enabled_approximation_mix": dict(sorted(approximation_mix.items())),
        "checks": checks,
    }
    if document["status"] != "PASS":
        raise ValueError("collider repair failed its post-authoring checks")
    partial_stage.replace(stage_path)
    document["stage_sha256_after"] = sha(stage_path)
    document["stage_changed"] = document["stage_sha256_after"] != before_hash
    if not document["stage_changed"]:
        raise AssertionError("collider repair did not change the stage")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(report_path, document)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    result = repair(args.stage, args.report)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
