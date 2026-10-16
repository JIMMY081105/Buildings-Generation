#!/usr/bin/env python3
"""Audit collider and door-policy facts from the packaged USDZ files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pxr import Usd, UsdPhysics

from door_collision_policy import inventory


def enabled(prim: Usd.Prim) -> bool:
    if not prim.HasAPI(UsdPhysics.CollisionAPI):
        return False
    return UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    buildings = sorted(path for path in args.root.iterdir() if path.is_dir())
    if len(buildings) != 10:
        raise ValueError(f"expected ten buildings, found {len(buildings)}")
    rows = []
    for building in buildings:
        package = building / "scene" / "building.usdz"
        stage = Usd.Stage.Open(str(package), Usd.Stage.LoadAll)
        if stage is None:
            raise ValueError(f"cannot open {package}")
        counts = {"Floor1": 0, "Floor2": 0, "Stair": 0, "Cut": 0, "other": 0}
        rigid = []
        enabled_total = 0
        for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
            path = str(prim.GetPath())
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                rigid.append(path)
            if not enabled(prim):
                continue
            enabled_total += 1
            bucket = "other"
            for candidate in ("Floor1", "Floor2", "Stair", "Cut"):
                if path.startswith(f"/World/{candidate}/"):
                    bucket = candidate
                    break
            counts[bucket] += 1
        doors = inventory(stage)
        checks = {
            "package_opens": True,
            "no_rigid_body_api": not rigid,
            "has_floor1_colliders": counts["Floor1"] > 1,
            "has_floor2_colliders": counts["Floor2"] > 1,
            "has_swinging_members": doors[
                "swinging_assembly_collider_count"] > 0,
            "all_swinging_members_disabled": doors[
                "swinging_assembly_collision_enabled_count"] == 0,
            "all_frames_and_jambs_enabled": (
                doors["frame_or_jamb_collider_count"] > 0
                and doors["frame_or_jamb_collision_enabled_count"]
                == doors["frame_or_jamb_collider_count"]),
        }
        rows.append({
            "BID": building.name,
            "status": "PASS" if all(checks.values()) else "FAIL",
            "enabled_collider_count": enabled_total,
            "enabled_collider_count_by_root": counts,
            "rigid_body_api_count": len(rigid),
            "door_object_root_count": doors["door_object_root_count"],
            "swinging_assembly_collider_count": doors[
                "swinging_assembly_collider_count"],
            "swinging_assembly_enabled_count": doors[
                "swinging_assembly_collision_enabled_count"],
            "frame_or_jamb_collider_count": doors[
                "frame_or_jamb_collider_count"],
            "frame_or_jamb_enabled_count": doors[
                "frame_or_jamb_collision_enabled_count"],
            "checks": checks,
        })
    passed = sum(row["status"] == "PASS" for row in rows)
    payload = {
        "schema": "PackagedGeometryDoorPolicyAuditV1",
        "status": "PASS" if passed == len(rows) else "FAIL",
        "building_count": len(rows),
        "passed_buildings": passed,
        "failed_buildings": len(rows) - passed,
        "measurement_source": "published self-contained building.usdz",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "status", "building_count", "passed_buildings", "failed_buildings")},
        indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
