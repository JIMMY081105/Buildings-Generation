#!/usr/bin/env python3
"""Inspect or recover the staircase subtree from a flattened accepted building."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom


def world_bbox(stage: Usd.Stage, prim: Usd.Prim) -> list[float]:
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
    )
    value = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    mn, mx = value.GetMin(), value.GetMax()
    return [float(mn[0]), float(mn[1]), float(mn[2]),
            float(mx[0]), float(mx[1]), float(mx[2])]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("building", type=Path)
    parser.add_argument("--source-prim")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()

    stage = Usd.Stage.Open(str(args.building), load=Usd.Stage.LoadAll)
    if stage is None:
        raise RuntimeError(f"could not open {args.building}")
    matches = [prim for prim in stage.Traverse()
               if "stair" in str(prim.GetPath()).lower()]
    rows = [{
        "path": str(prim.GetPath()), "type": prim.GetTypeName(),
        "active": prim.IsActive(), "bbox": world_bbox(stage, prim),
    } for prim in matches]
    print(json.dumps(rows[:100], indent=2))
    if not args.out:
        return
    if not args.source_prim:
        raise ValueError("--source-prim is required with --out")
    source = stage.GetPrimAtPath(args.source_prim)
    if not source.IsValid():
        raise ValueError(f"missing source prim {args.source_prim}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    destination = Usd.Stage.CreateNew(str(args.out))
    asset = UsdGeom.Xform.Define(destination, "/Asset").GetPrim()
    destination.SetDefaultPrim(asset)
    Sdf.CopySpec(stage.GetRootLayer(), source.GetPath(), destination.GetRootLayer(), Sdf.Path("/Asset"))
    asset = destination.GetPrimAtPath("/Asset")
    xformable = UsdGeom.Xformable(asset)
    original_ops = [str(op.GetOpName()) for op in xformable.GetOrderedXformOps()]
    xformable.ClearXformOpOrder()
    destination.GetRootLayer().Save()
    reopened = Usd.Stage.Open(str(args.out), load=Usd.Stage.LoadAll)
    recovered = reopened.GetPrimAtPath("/Asset")
    meshes = [prim for prim in Usd.PrimRange(recovered) if prim.IsA(UsdGeom.Mesh)]
    audit = {
        "contract": "recovered_stair_asset_v1",
        "source_building": str(args.building),
        "source_prim": args.source_prim,
        "source_world_bbox": world_bbox(stage, source),
        "removed_outer_xform_ops": original_ops,
        "recovered_asset": str(args.out),
        "recovered_local_bbox": world_bbox(reopened, recovered),
        "mesh_count": len(meshes),
        "active_mesh_count": sum(prim.IsActive() for prim in meshes),
        "default_prim": str(reopened.GetDefaultPrim().GetPath()),
    }
    if args.audit:
        args.audit.parent.mkdir(parents=True, exist_ok=True)
        args.audit.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
