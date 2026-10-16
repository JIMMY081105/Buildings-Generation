#!/usr/bin/env python3
"""Read-only 0.25 m body-radius erosion audit for the exact factory batch.

This deliberately does not participate in, replace, or modify G25.  It applies
a conservative grid erosion to the same standability masks and reports the
stair-arrival reachable-area delta requested for comparison.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage

from build_multistorey_graphs import _load_scratch
from gate25_storey_navigation import CONFIG, GRID_M, _components
from protomotions.simulator.base_simulator.standability import derive_standability


EXPECTED = 10
RADIUS_M = 0.25


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new(path: Path, value: object) -> None:
    require(not path.exists(), f"refusing existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def conservative_clearance_mask(walkable: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Require a radius-R disc to fit inside the walkable cell union.

    Padding treats the raster boundary as unavailable.  The half-cell addition
    converts center-to-center EDT into a conservative cell-union clearance:
    accepted centers are at least R + half a voxel from an unavailable center.
    """
    padded = np.pad(np.asarray(walkable, dtype=np.bool_), 1, constant_values=False)
    center_distance_m = ndimage.distance_transform_edt(padded)[1:-1, 1:-1] * GRID_M
    threshold_m = RADIUS_M + GRID_M / 2.0
    return walkable & (center_distance_m + 1.0e-12 >= threshold_m), center_distance_m


def audit_storey(occupancy: np.ndarray, grid: Any, g25: dict[str, Any]) -> dict[str, Any]:
    standability = derive_standability(occupancy, grid, CONFIG)
    support = np.asarray(standability.support_z_index)
    walkable = support >= 0
    strict_mask, distances = conservative_clearance_mask(walkable)
    strict_support = np.where(strict_mask, support, -1)
    labels, components = _components(strict_support)

    origin = np.asarray(grid.scene_bounds_min_m, dtype=np.float64)
    arrival = np.asarray(g25["stair_arrival_xyz_m"], dtype=np.float64)
    x_centers = origin[0] + (np.arange(support.shape[0]) + 0.5) * GRID_M
    y_centers = origin[1] + (np.arange(support.shape[1]) + 0.5) * GRID_M
    horizontal_seed = (
        (x_centers[:, None] - arrival[0]) ** 2
        + (y_centers[None, :] - arrival[1]) ** 2
        <= CONFIG.body_radius_m ** 2 + 1.0e-12
    )
    support_top = np.where(
        strict_support >= 0,
        origin[2] + (strict_support + 1) * GRID_M,
        np.nan,
    )
    vertical_seed = (
        np.abs(support_top - arrival[2])
        <= CONFIG.max_support_step_m + GRID_M / 2.0 + 1.0e-12
    )
    seed = strict_mask & horizontal_seed & vertical_seed
    reached_labels = sorted(int(v) for v in np.unique(labels[seed]) if int(v) > 0)
    reachable = np.isin(labels, reached_labels)

    cell_area = GRID_M * GRID_M
    original_reachable = float(g25["area_reachable_from_stair_arrival_m2"])
    strict_reachable = float(np.count_nonzero(reachable)) * cell_area
    strict_walkable = float(np.count_nonzero(strict_mask)) * cell_area
    original_walkable = float(np.count_nonzero(walkable)) * cell_area
    component_areas = sorted(
        (round(len(cells) * cell_area, 4) for cells in components), reverse=True)
    ratio = strict_reachable / original_reachable if original_reachable > 0 else 0.0
    return {
        "status": "MEASURED",
        "grid_m": GRID_M,
        "body_radius_m": RADIUS_M,
        "erosion_rule": (
            "Euclidean EDT of the standable XY mask, outside-grid unavailable; "
            "retain center distance >= body_radius + half_voxel"
        ),
        "conservative_center_distance_threshold_m": RADIUS_M + GRID_M / 2.0,
        "original_walkable_area_m2_recomputed": round(original_walkable, 4),
        "strict_walkable_area_m2": round(strict_walkable, 4),
        "g25_reported_reachable_area_m2": round(original_reachable, 4),
        "strict_reachable_from_same_stair_arrival_m2": round(strict_reachable, 4),
        "strict_to_g25_reachable_ratio": round(ratio, 6),
        "strict_reachable_loss_percent": round((1.0 - ratio) * 100.0, 4),
        "strict_stair_arrival_seed_cell_count": int(np.count_nonzero(seed)),
        "strict_reached_component_labels": reached_labels,
        "strict_component_count": len(components),
        "strict_component_areas_m2_descending": component_areas,
        "minimum_retained_center_clearance_m": (
            round(float(np.min(distances[strict_mask])), 4)
            if np.any(strict_mask) else None
        ),
        "claim": "READ_ONLY_AUDIT_NOT_USED_BY_G25_OR_RELEASE_GATES",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--g25-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    scratch_root = args.scratch_root.expanduser().resolve()
    g25_root = args.g25_root.expanduser().resolve()
    summary_path = g25_root / "G25_SUMMARY.json"
    summary = load(summary_path)
    require(summary.get("schema") == "G25Exact10SummaryV1", "unexpected G25 schema")
    require(summary.get("building_count") == EXPECTED, "G25 is not exact 10")
    bids = sorted(summary.get("approved_building_bids", []))
    require(len(bids) == EXPECTED and len(set(bids)) == EXPECTED,
            "G25 approved set is not 10 unique BIDs")

    rows = []
    for bid in bids:
        report_path = g25_root / f"{bid}.json"
        report = load(report_path)
        require(report.get("building") == bid, f"{bid}: G25 identity differs")
        scratch_path = scratch_root / f"{bid}.storey_bands_scratch.npz"
        extraction = _load_scratch(scratch_path)
        storeys = {}
        for index, name in enumerate(("Floor1", "Floor2")):
            storeys[name] = audit_storey(
                np.asarray(extraction.bands[index].occupancy),
                extraction.bands[index].grid_spec,
                report["storeys"][name],
            )
        rows.append({
            "BID": bid,
            "g25_report": str(report_path),
            "g25_report_sha256": sha256(report_path),
            "scratch": str(scratch_path),
            "scratch_sha256": sha256(scratch_path),
            "storeys": storeys,
        })

    ratios = [row["storeys"][name]["strict_to_g25_reachable_ratio"]
              for row in rows for name in ("Floor1", "Floor2")]
    payload = {
        "schema": "StrictBodyRadiusReachabilityAudit10V1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "READ_ONLY_COMPARISON",
        "building_count": EXPECTED,
        "storey_count": EXPECTED * 2,
        "body_radius_m": RADIUS_M,
        "g25_modified": False,
        "release_gate_modified": False,
        "minimum_strict_to_g25_reachable_ratio": round(min(ratios), 6),
        "median_strict_to_g25_reachable_ratio": round(float(np.median(ratios)), 6),
        "maximum_strict_to_g25_reachable_ratio": round(max(ratios), 6),
        "g25_summary": str(summary_path),
        "g25_summary_sha256": sha256(summary_path),
        "rows": rows,
    }
    write_new(args.output.expanduser().resolve(), payload)
    print(json.dumps({key: payload[key] for key in (
        "status", "building_count", "storey_count",
        "minimum_strict_to_g25_reachable_ratio",
        "median_strict_to_g25_reachable_ratio")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
