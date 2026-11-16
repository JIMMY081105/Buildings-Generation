#!/usr/bin/env python3
"""Factory80 stage 2 criterion B2: cross-storey routes, on body-eroded space.

A cross-storey route is reachable when a body can get from its start to the
stair foot on the lower storey, and from the stair top to its goal on the upper
one.  Both legs are measured on the space a 0.25 m-radius body can occupy, with
the support step limit on edges -- the same machinery as the same-storey test,
for the same reason: the frozen G25 floods raw standable cells and never erodes.

The stair traversal itself is deliberately not claimed here.  Whether a
humanoid can climb it is stage 3's question; this only establishes that the
geometry puts a body-passable approach at both ends.

The snap distance from the recorded stair arrival to the nearest body-passable
cell is reported on its own, because it carries a finding of its own: a landing
that is standable but not body-passable means a 0.25 m-radius body cannot stand
at the stair mouth at all, which no amount of route sampling would reveal.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from factory80_stage2_navigation import (  # noqa: E402
    BODY_RADIUS_M, MAX_SUPPORT_STEP_M, MIN_CONTROL_ISOLATED_AREA_M2, VOXEL_M,
    body_passable, load_support, walk_graph,
)

SCHEMA = "Factory80Stage2CrossStoreyV1"
B2_MIN_REACHABILITY = 0.95


def storey_graph(support: np.ndarray) -> dict[str, Any]:
    passable, radius_cells = body_passable(support)
    graph, index, component, cells = walk_graph(passable, support)
    sizes = np.bincount(component) if len(component) else np.zeros(1, int)
    return {
        "support": support, "passable": passable, "index": index,
        "component": component, "cells": cells,
        "main": int(np.argmax(sizes)) if len(sizes) else -1,
        "radius_cells": radius_cells,
    }


def snap_to_passable(state: dict[str, Any], origin: tuple[float, float],
                     xy_m: tuple[float, float]) -> dict[str, Any]:
    """Nearest body-passable cell to a world xy, with the distance recorded."""
    passable = state["passable"]
    rows, cols = np.nonzero(passable)
    if not rows.size:
        return {"found": False}
    world_x = origin[0] + (rows + 0.5) * VOXEL_M
    world_y = origin[1] + (cols + 0.5) * VOXEL_M
    distance = np.hypot(world_x - xy_m[0], world_y - xy_m[1])
    pick = int(np.argmin(distance))
    node = int(state["index"][rows[pick], cols[pick]])
    return {
        "found": True,
        "cell": [int(rows[pick]), int(cols[pick])],
        "world_xy_m": [float(world_x[pick]), float(world_y[pick])],
        "snap_distance_m": round(float(distance[pick]), 4),
        "node": node,
        "component": int(state["component"][node]),
        "in_main_component": bool(
            int(state["component"][node]) == state["main"]),
    }


def seal_ring(support: np.ndarray, origin: tuple[float, float],
              centre_xy: tuple[float, float], inner_m: float,
              thickness_m: float) -> np.ndarray:
    """Wall off a disc around a point, leaving its interior stranded."""
    altered = support.copy()
    rows = np.arange(altered.shape[0])[:, None]
    cols = np.arange(altered.shape[1])[None, :]
    world_x = origin[0] + (rows + 0.5) * VOXEL_M
    world_y = origin[1] + (cols + 0.5) * VOXEL_M
    radius = np.hypot(world_x - centre_xy[0], world_y - centre_xy[1])
    ring = (radius >= inner_m) & (radius < inner_m + thickness_m)
    altered[ring] = -1
    return altered


def cross_storey(lower_npz: Path, upper_npz: Path, g25: Path, *,
                 samples: int, seed: int,
                 control: bool) -> dict[str, Any]:
    lower_support, _, lower_meta = load_support(lower_npz)
    upper_support, _, upper_meta = load_support(upper_npz)
    lower_origin = tuple(float(v) for v in lower_meta["grid_origin_m"][:2])
    upper_origin = tuple(float(v) for v in upper_meta["grid_origin_m"][:2])

    payload = json.loads(g25.read_text(encoding="utf-8"))
    arrivals = {row["storey"]: row["stair_arrival_xyz_m"]
                for row in payload["storeys"].values()
                if isinstance(row, dict) and "stair_arrival_xyz_m" in row} \
        if isinstance(payload.get("storeys"), dict) else {}
    if not arrivals:
        raise RuntimeError("no stair arrivals found in the G25 report")
    lower_name = sorted(arrivals)[0]
    upper_name = sorted(arrivals)[-1]
    lower_arrival = arrivals[lower_name]
    upper_arrival = arrivals[upper_name]

    lower = storey_graph(lower_support)
    upper = storey_graph(upper_support)
    foot = snap_to_passable(lower, lower_origin,
                            (lower_arrival[0], lower_arrival[1]))
    top = snap_to_passable(upper, upper_origin,
                           (upper_arrival[0], upper_arrival[1]))
    if not (foot["found"] and top["found"]):
        return {"status": "FAIL", "reason": "a storey has no body-passable cell"}

    rng = np.random.default_rng(seed)
    lower_nodes = np.arange(len(lower["cells"]))
    upper_nodes = np.arange(len(upper["cells"]))
    draws = min(samples, len(lower_nodes), len(upper_nodes))
    a_nodes = rng.choice(lower_nodes, size=draws, replace=False)
    b_nodes = rng.choice(upper_nodes, size=draws, replace=False)

    # Reachable only if both legs land in the same component as their stair end.
    lower_ok = lower["component"][a_nodes] == foot["component"]
    upper_ok = upper["component"][b_nodes] == top["component"]
    both = lower_ok & upper_ok
    rate = float(both.mean())

    result = {
        "status": "PASS" if rate + 1.0e-12 >= B2_MIN_REACHABILITY else "FAIL",
        "lower_storey": lower_name,
        "upper_storey": upper_name,
        "floor_to_floor_rise_m": round(
            float(upper_arrival[2]) - float(lower_arrival[2]), 4),
        "stair_foot": foot,
        "stair_top": top,
        "pairs": int(draws),
        "reachable": int(both.sum()),
        "B2_reachability_rate": round(rate, 4),
        "B2_min_rate": B2_MIN_REACHABILITY,
        "lower_leg_rate": round(float(lower_ok.mean()), 4),
        "upper_leg_rate": round(float(upper_ok.mean()), 4),
        "body_radius_m": BODY_RADIUS_M,
        "max_support_step_m": MAX_SUPPORT_STEP_M,
    }

    if control:
        # N2-2: ring the stair foot off.  Everything outside the ring must lose
        # its cross-storey route, so the rate has to collapse.  A ring thinner
        # than twice the body radius would be eroded away and test nothing, so
        # make it 1.0 m.
        altered = seal_ring(lower_support, lower_origin,
                            (lower_arrival[0], lower_arrival[1]),
                            inner_m=3.0, thickness_m=1.0)
        sealed = storey_graph(altered)
        sealed_foot = snap_to_passable(
            sealed, lower_origin, (lower_arrival[0], lower_arrival[1]))
        if not sealed_foot["found"]:
            result["negative_control"] = {
                "control": "N2-2_ring_off_the_stair_foot",
                "status": "INAPPLICABLE_NO_PASSABLE_CELL_AFTER_SEAL",
            }
        else:
            stranded = int(
                (sealed["component"] == sealed_foot["component"]).sum())
            stranded_area = stranded * VOXEL_M ** 2
            # Node ids are positions in each graph's own cell list, so they do
            # not survive a rebuild: the ring deletes cells and every later id
            # shifts.  Re-resolve the sampled starts through their grid cells,
            # which are stable, and treat a start the ring removed outright as
            # simply no longer able to reach the stair.
            shape_y = lower_support.shape[1]
            a_cells = lower["cells"][a_nodes]
            a_rows = (a_cells // shape_y).astype(np.int64)
            a_cols = (a_cells % shape_y).astype(np.int64)
            sealed_nodes = sealed["index"][a_rows, a_cols]
            sealed_ok = np.zeros(len(a_nodes), dtype=bool)
            alive = sealed_nodes >= 0
            sealed_ok[alive] = (
                sealed["component"][sealed_nodes[alive]]
                == sealed_foot["component"])
            sealed_rate = float((sealed_ok & upper_ok).mean())
            collapsed = sealed_rate < B2_MIN_REACHABILITY
            if stranded_area < MIN_CONTROL_ISOLATED_AREA_M2:
                status = "INAPPLICABLE_SEAL_ISOLATED_NOTHING"
            elif collapsed:
                status = "FAIL_AS_REQUIRED"
            else:
                status = "DID_NOT_FAIL_CHECK_IS_INVALID"
            result["negative_control"] = {
                "control": "N2-2_ring_off_the_stair_foot",
                "ring_inner_m": 3.0, "ring_thickness_m": 1.0,
                "stair_foot_pocket_area_m2": round(stranded_area, 4),
                "sealed_B2_rate": round(sealed_rate, 4),
                "baseline_B2_rate": round(rate, 4),
                "status": status,
            }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lower", type=Path, required=True)
    parser.add_argument("--upper", type=Path, required=True)
    parser.add_argument("--g25", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20261012)
    parser.add_argument("--no-control", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = cross_storey(
        args.lower.expanduser().resolve(), args.upper.expanduser().resolve(),
        args.g25.expanduser().resolve(), samples=args.samples, seed=args.seed,
        control=not args.no_control)
    payload = {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "lower": str(args.lower), "upper": str(args.upper),
        "g25": str(args.g25),
        **result,
        "formal_acceptance": "NOT EXECUTED",
        "cannot_support": (
            "This establishes a body-passable approach at both ends of the "
            "stair and a route to each. It does not establish that a humanoid "
            "can climb the stair; the stair traversal itself is stage 3."
        ),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False)
    if args.output is not None and not args.output.exists():
        args.output.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "B2_rate": payload.get("B2_reachability_rate"),
        "lower_leg": payload.get("lower_leg_rate"),
        "upper_leg": payload.get("upper_leg_rate"),
        "rise_m": payload.get("floor_to_floor_rise_m"),
        "foot_snap_m": payload.get("stair_foot", {}).get("snap_distance_m"),
        "top_snap_m": payload.get("stair_top", {}).get("snap_distance_m"),
        "foot_in_main": payload.get("stair_foot", {}).get("in_main_component"),
        "top_in_main": payload.get("stair_top", {}).get("in_main_component"),
        "control": (payload.get("negative_control") or {}).get("status"),
    }, ensure_ascii=False, indent=1))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
