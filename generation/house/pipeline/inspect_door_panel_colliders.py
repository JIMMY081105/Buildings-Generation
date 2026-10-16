#!/usr/bin/env python3
"""Inventory collision-enabled geometry belonging to doorway asset roots.

This is deliberately path-structural rather than a tall/thin heuristic: every
collider below a ``doorway_<asset-id>_*`` root belongs to the door-panel set.
The leaf diagnostic is a second, narrower geometric census and never defines
which colliders are removed by a door counterfactual.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from pxr import Usd, UsdGeom, UsdPhysics


DOORWAY_ROOT_RE = re.compile(r"^doorway_[0-9a-f]{32}_", re.IGNORECASE)
PURPOSES = [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.guide]


def doorway_root(prim: Usd.Prim) -> Usd.Prim | None:
    current = prim
    while current and not current.IsPseudoRoot():
        if DOORWAY_ROOT_RE.match(current.GetName()):
            return current
        current = current.GetParent()
    return None


def collision_state(prim: Usd.Prim) -> tuple[bool, bool]:
    applied = prim.HasAPI(UsdPhysics.CollisionAPI)
    if not applied:
        return False, False
    value = UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()
    return True, value is not False


def bounds(cache: UsdGeom.BBoxCache, prim: Usd.Prim) -> list[float] | None:
    value = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    if value.IsEmpty():
        return None
    lo, hi = value.GetMin(), value.GetMax()
    return [float(lo[i]) for i in range(3)] + [float(hi[i]) for i in range(3)]


def inspect(stage_path: Path) -> dict:
    stage = Usd.Stage.Open(str(stage_path))
    if stage is None:
        raise ValueError(f"cannot open stage: {stage_path}")
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), PURPOSES)
    rows = []
    leaf_rows = []
    all_prim_collision_rows = []
    door_named_rows = []
    door_named_leaf_rows = []
    door_path_rows = []
    door_path_leaf_rows = []
    full_panel_rows = []
    full_panel_leaf_rows = []
    predicate = Usd.TraverseInstanceProxies()
    for prim in stage.Traverse(predicate):
        path_parts = str(prim.GetPath()).split("/")
        object_root_name = (path_parts[4] if len(path_parts) > 4
                            and path_parts[1] == "World"
                            and path_parts[2] in ("Floor1", "Floor2")
                            and path_parts[3] == "Geometry" else "")
        applied, enabled = collision_state(prim)
        object_root_is_door = object_root_name.lower().startswith(
            ("doorway_", "doorframe_"))
        full_panel_member = (
            object_root_is_door
            or ("door" in str(prim.GetPath()).lower()
                and "meshcollider" in prim.GetName().lower())
        )
        if prim.IsA(UsdGeom.Mesh) and applied and full_panel_member:
            value = bounds(cache, prim)
            if value is not None:
                extent = [value[i + 3] - value[i] for i in range(3)]
                panel_row = {
                    "prim": str(prim.GetPath()),
                    "object_root": object_root_name,
                    "enabled": enabled,
                    "bounds_m": value,
                    "extent_m": extent,
                }
                full_panel_rows.append(panel_row)
                name = prim.GetName().lower()
                if (enabled and extent[2] > 1.5
                        and min(extent[0], extent[1]) < 0.15
                        and "door" in name and "handle" not in name):
                    full_panel_leaf_rows.append(panel_row)
        if (prim.IsA(UsdGeom.Mesh) and applied
                and "door" in object_root_name.lower()):
            value = bounds(cache, prim)
            if value is not None:
                extent = [value[i + 3] - value[i] for i in range(3)]
                named_row = {
                    "prim": str(prim.GetPath()),
                    "object_root": object_root_name,
                    "enabled": enabled,
                    "bounds_m": value,
                    "extent_m": extent,
                }
                door_named_rows.append(named_row)
                name = prim.GetName().lower()
                if (enabled and extent[2] > 1.5
                        and min(extent[0], extent[1]) < 0.15
                        and "door" in name and "handle" not in name):
                    door_named_leaf_rows.append(named_row)
        if prim.IsA(UsdGeom.Mesh) and applied and "door" in str(prim.GetPath()).lower():
            value = bounds(cache, prim)
            if value is not None:
                extent = [value[i + 3] - value[i] for i in range(3)]
                path_row = {
                    "prim": str(prim.GetPath()),
                    "object_root": object_root_name,
                    "enabled": enabled,
                    "bounds_m": value,
                    "extent_m": extent,
                }
                door_path_rows.append(path_row)
                name = prim.GetName().lower()
                if (enabled and extent[2] > 1.5
                        and min(extent[0], extent[1]) < 0.15
                        and "door" in name and "handle" not in name):
                    door_path_leaf_rows.append(path_row)
        root = doorway_root(prim)
        if root is None:
            continue
        if applied:
            all_prim_collision_rows.append({
                "prim": str(prim.GetPath()), "enabled": enabled,
                "type": prim.GetTypeName(),
            })
        if not prim.IsA(UsdGeom.Mesh) or not applied:
            continue
        value = bounds(cache, prim)
        if value is None:
            continue
        extent = [value[i + 3] - value[i] for i in range(3)]
        row = {
            "prim": str(prim.GetPath()),
            "doorway_root": str(root.GetPath()),
            "enabled": enabled,
            "bounds_m": value,
            "extent_m": extent,
        }
        rows.append(row)
        name = prim.GetName().lower()
        # The geometric filter is reported only as a diagnostic.  Requiring a
        # door-like leaf name prevents jambs/frames from being mislabelled.
        if (enabled and extent[2] > 1.5 and min(extent[0], extent[1]) < 0.15
                and "door" in name and "handle" not in name):
            leaf_rows.append(row)
    roots = sorted({row["doorway_root"] for row in rows})
    return {
        "stage": str(stage_path),
        "doorway_root_count": len(roots),
        "door_panel_collider_mesh_count": len(rows),
        "door_panel_collision_enabled_mesh_count": sum(r["enabled"] for r in rows),
        "door_panel_collision_disabled_mesh_count": sum(not r["enabled"] for r in rows),
        "door_panel_collision_api_prim_count_all_types": len(all_prim_collision_rows),
        "tall_thin_closed_leaf_count": len(leaf_rows),
        "tall_thin_closed_leaves": leaf_rows,
        "door_panel_colliders": rows,
        "door_named_object_collider_mesh_count": len(door_named_rows),
        "door_named_object_collision_enabled_mesh_count": sum(
            r["enabled"] for r in door_named_rows),
        "door_named_tall_thin_closed_leaf_count": len(door_named_leaf_rows),
        "door_named_object_roots": sorted({r["object_root"] for r in door_named_rows}),
        "door_named_colliders": door_named_rows,
        "door_path_collider_mesh_count": len(door_path_rows),
        "door_path_collision_enabled_mesh_count": sum(
            r["enabled"] for r in door_path_rows),
        "door_path_tall_thin_closed_leaf_count": len(door_path_leaf_rows),
        "door_path_object_roots": sorted({r["object_root"] for r in door_path_rows}),
        "door_path_colliders": door_path_rows,
        "full_door_panel_collider_count": len(full_panel_rows),
        "full_door_panel_collision_enabled_count": sum(
            r["enabled"] for r in full_panel_rows),
        "full_door_panel_tall_thin_closed_leaf_count": len(full_panel_leaf_rows),
        "full_door_panel_object_roots": sorted(
            {r["object_root"] for r in full_panel_rows}),
        "full_door_panel_colliders": full_panel_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stages", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = [inspect(path) for path in args.stages]
    payload = {"contract_version": 1, "stages": results}
    text = json.dumps(payload, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
