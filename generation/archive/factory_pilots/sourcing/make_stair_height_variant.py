#!/usr/bin/env python3
"""Create an explicitly audited temporary height variant for A4 search.

The source meshes/colliders and XY footprint are unchanged.  A root-space Z
scale is used only for the A4 feasibility search; production assembly must bake
the transform and re-audit identity scale before release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pxr import Gf, Usd, UsdGeom


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-usd", type=Path, required=True)
    parser.add_argument("--source-path", type=Path, required=True)
    parser.add_argument("--source-height", type=float, required=True)
    parser.add_argument("--target-height", type=float, required=True)
    parser.add_argument("--out-usd", type=Path, required=True)
    parser.add_argument("--out-path", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    factor = args.target_height / args.source_height

    args.out_usd.parent.mkdir(parents=True, exist_ok=True)
    source_stage = Usd.Stage.Open(str(args.source_usd), load=Usd.Stage.LoadAll)
    if source_stage is None or not source_stage.GetPrimAtPath("/Asset").IsValid():
        raise RuntimeError("source stair must open with default /Asset")
    source_stage.Export(str(args.out_usd))
    stage = Usd.Stage.Open(str(args.out_usd), load=Usd.Stage.LoadAll)
    asset = stage.GetPrimAtPath("/Asset")
    UsdGeom.Xformable(asset).AddScaleOp(opSuffix="factory10SearchHeight").Set(
        Gf.Vec3f(1.0, 1.0, factor)
    )
    stage.GetRootLayer().Save()

    path_doc = json.loads(args.source_path.read_text(encoding="utf-8"))
    scaled = dict(path_doc)
    scaled["points_xyz_m"] = [
        [float(point[0]), float(point[1]), float(point[2]) * factor]
        for point in path_doc["points_xyz_m"]
    ]
    scaled["factory10_height_variant"] = {
        "source_height_m": args.source_height,
        "target_height_m": args.target_height,
        "z_factor": factor,
        "purpose": "A4 feasibility search only; production asset must bake and re-audit",
    }
    args.out_path.write_text(json.dumps(scaled, indent=2) + "\n", encoding="utf-8")
    audit = {
        "contract": "factory10_A4_height_variant_v1",
        "source_usd": str(args.source_usd),
        "source_usd_sha256": sha256(args.source_usd),
        "source_path": str(args.source_path),
        "source_path_sha256": sha256(args.source_path),
        "out_usd": str(args.out_usd),
        "out_usd_sha256": sha256(args.out_usd),
        "out_path": str(args.out_path),
        "out_path_sha256": sha256(args.out_path),
        "source_height_m": args.source_height,
        "target_height_m": args.target_height,
        "z_factor": factor,
        "xy_geometry_unchanged": True,
        "maximum_path_height_m": max(point[2] for point in scaled["points_xyz_m"]),
        "production_release_allowed": False,
        "production_release_requirement": "bake root transform; identity-scale and collision re-audit",
    }
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
