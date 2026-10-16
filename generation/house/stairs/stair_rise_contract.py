#!/usr/bin/env python3
"""Exact walking-surface rise contract for generated staircase USD assets.

The criterion is evaluated from authored tread facets, not traversal metadata:
ground -> first tread -> every subsequent tread -> upper slab must stay within
ProtoMotions' 0.20 m maximum support-step contract.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from pxr import Gf, Usd, UsdGeom


MAX_SUPPORT_STEP_M = 0.20
GROUND_Z_M = 0.0
TOLERANCE_M = 1e-6
TREAD_PRIM_NAME = "integrated_tread_collision"


def _world_mesh(stage: Any, prim: Any) -> tuple[np.ndarray, np.ndarray]:
    mesh = UsdGeom.Mesh(prim)
    points = mesh.GetPointsAttr().Get() or []
    counts = mesh.GetFaceVertexCountsAttr().Get() or []
    indices = mesh.GetFaceVertexIndicesAttr().Get() or []
    if not points or not counts or not indices:
        raise ValueError("tread collider has empty mesh topology")
    matrix = UsdGeom.XformCache(
        Usd.TimeCode.Default()).GetLocalToWorldTransform(prim)
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage))
    vertices = np.asarray([
        [float(value) * meters_per_unit
         for value in matrix.Transform(Gf.Vec3d(*point))]
        for point in points
    ], dtype=np.float64)
    triangles: list[tuple[int, int, int]] = []
    cursor = 0
    for count in counts:
        face = indices[cursor:cursor + int(count)]
        for offset in range(1, len(face) - 1):
            triangles.append((int(face[0]), int(face[offset]),
                              int(face[offset + 1])))
        cursor += int(count)
    if not triangles:
        raise ValueError("tread collider has no triangles")
    return vertices, np.asarray(triangles, dtype=np.int64)


def _tread_prim(stage: Any) -> Any:
    matches = [
        prim for prim in stage.Traverse()
        if prim.IsA(UsdGeom.Mesh) and prim.GetName() == TREAD_PRIM_NAME
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one active {TREAD_PRIM_NAME}, found {len(matches)}")
    return matches[0]


def measure_stair_rises(
        usd_path: Path,
        upper_slab_top_m: float,
        *,
        ground_z_m: float = GROUND_Z_M,
        max_support_step_m: float = MAX_SUPPORT_STEP_M,
        tolerance_m: float = TOLERANCE_M,
) -> dict[str, Any]:
    """Measure all support-height transitions from exact authored facets."""
    usd_path = usd_path.resolve()
    stage = Usd.Stage.Open(str(usd_path), Usd.Stage.LoadAll)
    if stage is None:
        raise ValueError("could not open staircase USD: " + str(usd_path))
    vertices, faces = _world_mesh(stage, _tread_prim(stage))
    triangles = vertices[faces]
    normals = np.cross(
        triangles[:, 1] - triangles[:, 0],
        triangles[:, 2] - triangles[:, 0],
    )
    lengths = np.linalg.norm(normals, axis=1)
    vertical = np.zeros(len(normals), dtype=np.float64)
    nonzero = lengths > 0
    vertical[nonzero] = normals[nonzero, 2] / lengths[nonzero]
    horizontal = triangles[np.abs(vertical) >= 0.999]
    levels = np.unique(np.round(horizontal[:, :, 2].mean(axis=1), 7))
    if len(levels) < 2 or len(levels) % 2:
        raise ValueError(
            f"unexpected tread horizontal-facet level count: {len(levels)}")
    bottoms, tops = levels[0::2], levels[1::2]
    if len(tops) < 2:
        raise ValueError("fewer than two authored walking surfaces")
    plate_thicknesses = tops - bottoms
    inner = np.diff(tops)
    first = float(tops[0]) - float(ground_z_m)
    last = float(upper_slab_top_m) - float(tops[-1])
    rises = np.concatenate(([first], inner, [last])).astype(np.float64)
    finite = bool(np.all(np.isfinite(rises)) and np.all(rises > 0.0))
    over = rises > float(max_support_step_m) + float(tolerance_m)
    passed = bool(finite and not np.any(over))
    return {
        "status": "PASS" if passed else "FAIL",
        "criterion": (
            "ground -> first tread -> tread tops -> upper slab; every exact "
            "authored support rise <= max_support_step_m"
        ),
        "usd": str(usd_path),
        "ground_z_m": float(ground_z_m),
        "upper_slab_top_m": float(upper_slab_top_m),
        "tread_count": int(len(tops)),
        "plate_thickness_m": round(float(np.median(plate_thicknesses)), 7),
        "first_rise_m": round(first, 7),
        "median_inner_riser_m": round(float(np.median(inner)), 7),
        "max_inner_riser_m": round(float(np.max(inner)), 7),
        "last_rise_m": round(last, 7),
        "max_rise_m": round(float(np.max(rises)), 7),
        "min_rise_m": round(float(np.min(rises)), 7),
        "max_support_step_m": float(max_support_step_m),
        "tolerance_m": float(tolerance_m),
        "rises_over_limit": int(np.count_nonzero(over)),
        "rises_m": [round(float(value), 7) for value in rises],
        "finite_positive_rises": finite,
        "passes": passed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("usd", type=Path)
    parser.add_argument("--upper-slab-top-m", type=float, required=True)
    parser.add_argument("--ground-z-m", type=float, default=GROUND_Z_M)
    parser.add_argument("--max-support-step-m", type=float,
                        default=MAX_SUPPORT_STEP_M)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = measure_stair_rises(
        args.usd, args.upper_slab_top_m, ground_z_m=args.ground_z_m,
        max_support_step_m=args.max_support_step_m,
    )
    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        if args.output.exists():
            raise FileExistsError("refusing to overwrite: " + str(args.output))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
