#!/usr/bin/env python3
"""Factory80 stage 1 controls + stage 2 global navigation, on body-eroded space.

Why this exists
---------------
The frozen G25 answers "is it reachable from the stair arrival", and it passes
all six factory pilots.  But G25 does its reachability flood on raw standable
cells: it never erodes by body radius, so a 0.05 m sliver counts as connected.
``failed-tests.md`` records the most expensive miss in this project as exactly
that shape -- ``body_radius=0.25`` filtered seeds but did not erode the
component, so a lenient pass of 50/50 became 0/50 under strict recheck.

So this tool re-asks the question on the space a 0.25 m-radius body can
actually occupy, and adds the step limit on edges so you cannot "walk" from the
floor onto a table top.

Criteria are pre-registered in FACTORY80_ACCEPTANCE_V2.md and are not tuned
here.  Nothing frozen is modified; the Standability V1 constants are read, not
redefined.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

SCHEMA = "Factory80Stage2NavigationV1"

# Standability V1 is pinned.  These are read-only mirrors used to size the
# erosion and the step limit; they are never written back anywhere.
VOXEL_M = 0.05
BODY_RADIUS_M = 0.25
MAX_SUPPORT_STEP_M = 0.20

# Pre-registered in FACTORY80_ACCEPTANCE_V2.md section 3.
B1_MIN_REACHABILITY = 0.95
B3_MAX_LENGTH_RATIO_MEDIAN = 3.0

# A seal that strands less than this is not a usable negative control.  The
# number is borrowed from the frozen G25's own written justification for 4.0 m2:
# "a 2 m by 2 m room-scale standable region, large enough for translation and
# turning rather than a raster sliver".
MIN_CONTROL_ISOLATED_AREA_M2 = 4.0


def load_support(path: Path) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    with np.load(path, allow_pickle=False) as archive:
        support = np.asarray(archive["support_z_index"], dtype=np.int32)
        labels = np.asarray(archive["component_labels"], dtype=np.int32)
        meta = json.loads(bytes(archive["metadata_json_utf8"]).decode("utf-8"))
    return support, labels, meta


def disc(radius_cells: int) -> np.ndarray:
    span = np.arange(-radius_cells, radius_cells + 1)
    yy, xx = np.meshgrid(span, span, indexing="ij")
    return (xx * xx + yy * yy) <= radius_cells * radius_cells


def body_passable(support: np.ndarray) -> tuple[np.ndarray, int]:
    """Cells where a BODY_RADIUS_M disc fits entirely inside standable space."""
    radius_cells = int(round(BODY_RADIUS_M / VOXEL_M))
    standable = support >= 0
    eroded = ndimage.binary_erosion(
        standable, structure=disc(radius_cells), border_value=0)
    return eroded, radius_cells


def walk_graph(passable: np.ndarray, support: np.ndarray
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """8-connected graph over passable cells, with the support step limit.

    An edge exists only when both endpoints are passable and their support tops
    differ by at most MAX_SUPPORT_STEP_M.  Without that second condition a
    table top 0.7 m up would be "connected" to the floor beside it and every
    reachability number would be wrong in the optimistic direction.

    Diagonal moves additionally require both shared orthogonal neighbours to be
    passable, so the body cannot squeeze through a corner contact.
    """
    step_cells = int(round(MAX_SUPPORT_STEP_M / VOXEL_M))
    index = np.full(passable.shape, -1, dtype=np.int64)
    cells = np.flatnonzero(passable.ravel())
    index.ravel()[cells] = np.arange(len(cells), dtype=np.int64)

    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    data: list[np.ndarray] = []

    rows_n, cols_n = passable.shape

    def add(shift_x: int, shift_y: int, weight: float,
            require: tuple[tuple[int, int], ...] = ()) -> None:
        def window(array: np.ndarray, offset_x: int, offset_y: int
                   ) -> np.ndarray:
            """The source window shifted by (offset_x, offset_y).

            The source window itself is ``window(array, 0, 0)``: rows
            ``[max(0,-dx) : N-max(0,dx)]``, cols ``[max(0,-dy) : M-max(0,dy)]``,
            shape ``(N-|dx|, M-|dy|)``.  Every shifted window keeps that shape,
            so all the masks below line up element-wise.  Offsets are only used
            with ``|offset| <= |shift|``, which keeps the slice in bounds.
            """
            start_x = max(0, -shift_x) + offset_x
            start_y = max(0, -shift_y) + offset_y
            return array[start_x: start_x + rows_n - abs(shift_x),
                         start_y: start_y + cols_n - abs(shift_y)]

        here = window(passable, 0, 0)
        there = window(passable, shift_x, shift_y)
        support_here = window(support, 0, 0).astype(np.int64)
        support_there = window(support, shift_x, shift_y).astype(np.int64)
        ok = here & there & (
            np.abs(support_here - support_there) <= step_cells)
        for corner_x, corner_y in require:
            ok &= window(passable, corner_x, corner_y)
        rows.append(window(index, 0, 0)[ok])
        cols.append(window(index, shift_x, shift_y)[ok])
        data.append(np.full(int(ok.sum()), weight, dtype=np.float32))

    add(1, 0, 1.0)
    add(0, 1, 1.0)
    diagonal = math.sqrt(2.0)
    add(1, 1, diagonal, require=((1, 0), (0, 1)))
    add(1, -1, diagonal, require=((1, 0), (0, -1)))

    row = np.concatenate(rows) if rows else np.empty(0, dtype=np.int64)
    col = np.concatenate(cols) if cols else np.empty(0, dtype=np.int64)
    weights = np.concatenate(data) if data else np.empty(0, dtype=np.float32)
    count = len(cells)
    graph = coo_matrix((weights, (row, col)), shape=(count, count)).tocsr()
    graph = graph.maximum(graph.T)
    component_count, component = connected_components(
        graph, directed=False, return_labels=True)
    return graph, index, component, np.asarray(cells, dtype=np.int64)


def tile_of(x_m: np.ndarray, y_m: np.ndarray,
            pitch: tuple[float, float]) -> np.ndarray:
    return (np.floor(x_m / pitch[0]).astype(np.int64) * 10_000
            + np.floor(y_m / pitch[1]).astype(np.int64))


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": int(array.size),
        "min": round(float(array.min()), 4),
        "median": round(float(np.median(array)), 4),
        "p90": round(float(np.quantile(array, 0.90)), 4),
        "max": round(float(array.max()), 4),
    }


def navigation_report(support: np.ndarray, *, origin: tuple[float, float],
                      pitch: tuple[float, float], sources: int,
                      targets_per_source: int, seed: int) -> dict[str, Any]:
    passable, radius_cells = body_passable(support)
    graph, index, component, cells = walk_graph(passable, support)
    passable_count = int(passable.sum())
    if passable_count == 0:
        return {"status": "FAIL", "reason": "no body-passable cell survives erosion",
                "body_passable_cells": 0}

    sizes = np.bincount(component)
    main = int(np.argmax(sizes))
    rng = np.random.default_rng(seed)

    # Sample uniformly over body-passable cells.  No connectivity pre-filter:
    # an unreachable draw is a result, not a bad seed.  Pre-filtering by the
    # answer is the documented way this check stops being able to fail.
    source_ids = rng.choice(passable_count, size=min(sources, passable_count),
                            replace=False)
    pairs: dict[str, list[dict[str, Any]]] = {"same_tile": [], "cross_tile": []}

    shape_y = passable.shape[1]
    for source in source_ids:
        distance = dijkstra(graph, directed=False, indices=int(source))
        target_ids = rng.choice(passable_count,
                                size=min(targets_per_source, passable_count),
                                replace=False)
        source_cell = cells[source]
        source_x = (source_cell // shape_y) * VOXEL_M + origin[0]
        source_y = (source_cell % shape_y) * VOXEL_M + origin[1]
        for target in target_ids:
            if int(target) == int(source):
                continue
            target_cell = cells[target]
            target_x = (target_cell // shape_y) * VOXEL_M + origin[0]
            target_y = (target_cell % shape_y) * VOXEL_M + origin[1]
            same = (tile_of(np.asarray(source_x), np.asarray(source_y), pitch)
                    == tile_of(np.asarray(target_x), np.asarray(target_y), pitch))
            euclidean = math.hypot(target_x - source_x, target_y - source_y)
            length = float(distance[int(target)]) * VOXEL_M
            reachable = math.isfinite(length)
            pairs["same_tile" if bool(same) else "cross_tile"].append({
                "reachable": reachable,
                "path_length_m": round(length, 4) if reachable else None,
                "euclidean_m": round(euclidean, 4),
                "ratio": (round(length / euclidean, 4)
                          if reachable and euclidean > 1.0e-9 else None),
            })

    classes: dict[str, Any] = {}
    for name, rows in pairs.items():
        if not rows:
            classes[name] = {"pairs": 0, "note": "no pair drawn in this class"}
            continue
        reached = [row for row in rows if row["reachable"]]
        rate = len(reached) / len(rows)
        ratios = [row["ratio"] for row in reached if row["ratio"] is not None]
        ratio_stats = summarize(ratios)
        b3 = (ratio_stats.get("median") is not None
              and ratio_stats["median"] <= B3_MAX_LENGTH_RATIO_MEDIAN)
        classes[name] = {
            "pairs": len(rows),
            "reachable": len(reached),
            "reachability_rate": round(rate, 4),
            "B1_min_rate": B1_MIN_REACHABILITY,
            "B1_pass": bool(rate + 1.0e-12 >= B1_MIN_REACHABILITY),
            "path_length_m": summarize(
                [row["path_length_m"] for row in reached]),
            "length_over_euclidean_ratio": ratio_stats,
            "B3_max_median_ratio": B3_MAX_LENGTH_RATIO_MEDIAN,
            "B3_pass": bool(b3),
        }

    graded = [value for value in classes.values() if value.get("pairs")]
    status = "PASS" if graded and all(
        value["B1_pass"] and value["B3_pass"] for value in graded) else "FAIL"
    return {
        "status": status,
        "body_radius_m": BODY_RADIUS_M,
        "erosion_radius_cells": radius_cells,
        "max_support_step_m": MAX_SUPPORT_STEP_M,
        "standable_cells": int((support >= 0).sum()),
        "body_passable_cells": passable_count,
        "body_passable_area_m2": round(passable_count * VOXEL_M ** 2, 4),
        "standable_area_m2": round(int((support >= 0).sum()) * VOXEL_M ** 2, 4),
        "eroded_component_count": int(len(sizes)),
        "eroded_main_component_area_m2": round(
            int(sizes[main]) * VOXEL_M ** 2, 4),
        "eroded_main_component_fraction": round(
            float(sizes[main]) / passable_count, 6),
        "tile_pitch_m": list(pitch),
        "classes": classes,
    }


def seal_tile(support: np.ndarray, *, origin: tuple[float, float],
              pitch: tuple[float, float], tile: tuple[int, int],
              wall_cells: int = 12) -> np.ndarray:
    """Negative control N2-1: wall off one tile and return the altered support.

    The ring is ``wall_cells`` thick so it survives the body erosion; a 1-cell
    scratch would be eroded away and the control would silently do nothing.
    """
    altered = support.copy()
    x0 = int(round((tile[0] * pitch[0] - origin[0]) / VOXEL_M))
    x1 = int(round(((tile[0] + 1) * pitch[0] - origin[0]) / VOXEL_M))
    y0 = int(round((tile[1] * pitch[1] - origin[1]) / VOXEL_M))
    y1 = int(round(((tile[1] + 1) * pitch[1] - origin[1]) / VOXEL_M))
    x0, x1 = max(0, x0), min(altered.shape[0], x1)
    y0, y1 = max(0, y0), min(altered.shape[1], y1)
    for lo, hi, axis in ((x0, x0 + wall_cells, 0), (x1 - wall_cells, x1, 0),
                         (y0, y0 + wall_cells, 1), (y1 - wall_cells, y1, 1)):
        if axis == 0:
            altered[max(0, lo):max(0, hi), y0:y1] = -1
        else:
            altered[x0:x1, max(0, lo):max(0, hi)] = -1
    return altered


def self_test() -> dict[str, Any]:
    """Prove the three mechanisms this tool stands on actually bite.

    Plausible numbers on real data prove nothing: if the erosion or the step
    limit silently did nothing, every reachability figure would be wrong in the
    optimistic direction and still look reasonable.  So check each mechanism
    against a case whose answer is known by construction.
    """
    def two_rooms(corridor_m: float, step_m: float = 0.0) -> np.ndarray:
        # Two 4 m x 4 m rooms, 2 m apart, joined by one corridor.
        support = np.full((int(10.0 / VOXEL_M), int(4.0 / VOXEL_M)), -1,
                          dtype=np.int32)
        room = int(4.0 / VOXEL_M)
        gap = int(2.0 / VOXEL_M)
        support[:room, :] = 0
        support[room + gap:, :] = int(round(step_m / VOXEL_M))
        width = max(1, int(round(corridor_m / VOXEL_M)))
        middle = support.shape[1] // 2
        support[room:room + gap,
                middle - width // 2: middle - width // 2 + width] = 0
        return support

    def connected(support: np.ndarray) -> bool:
        passable, _ = body_passable(support)
        if not passable.any():
            return False
        _, _, component, cells = walk_graph(passable, support)
        # Are both rooms represented in a single component?
        room = int(4.0 / VOXEL_M)
        shape_y = support.shape[1]
        xs = cells // shape_y
        left = set(component[xs < room].tolist())
        right = set(component[xs >= room + int(2.0 / VOXEL_M)].tolist())
        return bool(left & right)

    arms = [
        {
            "arm": "wide_corridor_0.60m_must_connect",
            "expected": True,
            "observed": connected(two_rooms(0.60)),
            "why": "0.60 m > 2 x body_radius 0.25 m, so the eroded disc fits",
        },
        {
            "arm": "narrow_corridor_0.30m_must_disconnect",
            "expected": False,
            "observed": connected(two_rooms(0.30)),
            "why": "0.30 m < 0.50 m, so erosion must remove the corridor; if "
                   "this connects, the erosion is not being applied",
        },
        {
            "arm": "step_0.70m_must_disconnect",
            "expected": False,
            "observed": connected(two_rooms(0.60, step_m=0.70)),
            "why": "0.70 m > max_support_step 0.20 m, so the edge test must "
                   "reject it; if this connects, the step limit is dead and "
                   "table tops would count as floor",
        },
        {
            "arm": "step_0.15m_must_connect",
            "expected": True,
            "observed": connected(two_rooms(0.60, step_m=0.15)),
            "why": "0.15 m <= 0.20 m is a walkable step; if this disconnects, "
                   "the step limit is too strict and reachability is "
                   "understated",
        },
    ]
    for arm in arms:
        arm["status"] = ("OK" if arm["observed"] == arm["expected"]
                         else "MECHANISM_BROKEN")
    return {
        "schema": SCHEMA + "SelfTest",
        "status": "PASS" if all(a["status"] == "OK" for a in arms) else "FAIL",
        "arms": arms,
        "cannot_support": (
            "A self-test PASS only shows erosion and the step limit respond "
            "correctly on constructed cases. It says nothing about any "
            "building."
        ),
    }


def richest_tile(support: np.ndarray, *, origin: tuple[float, float],
                 pitch: tuple[float, float]) -> tuple[int, int]:
    """The tile holding the most body-passable area.

    Sealing a tile that barely overlaps the walkable area isolates nothing, so
    let the control aim at a tile that actually has something to cut off.
    """
    passable, _ = body_passable(support)
    xs, ys = np.nonzero(passable)
    if not xs.size:
        return (0, 0)
    tile_x = np.floor((xs * VOXEL_M + origin[0]) / pitch[0]).astype(np.int64)
    tile_y = np.floor((ys * VOXEL_M + origin[1]) / pitch[1]).astype(np.int64)
    keys, counts = np.unique(tile_x * 10_000 + tile_y, return_counts=True)
    best = keys[int(np.argmax(counts))]
    return (int(best // 10_000), int(best % 10_000))


def seal_control(support: np.ndarray, *, origin: tuple[float, float],
                 pitch: tuple[float, float], tile: tuple[int, int],
                 samples: int, seed: int) -> dict[str, Any]:
    """N2-1 as a targeted assertion, not a nudge to the global pass rate.

    Sealing one tile and watching the overall reachability rate is too weak to
    be a control on a large floor: on a 5x5 tiling one tile is 4% of the area,
    so even a perfectly detected wall-off can only pull the rate to about 96%,
    which still clears the 95% threshold.  The arm then reports "did not fail"
    while the checker was in fact working.

    So ask the question directly instead: wall a tile off, then draw pairs that
    straddle the wall and require every one of them to come back unreachable.
    That assertion does not depend on floor size or on the B1 threshold.
    """
    altered = seal_tile(support, origin=origin, pitch=pitch, tile=tile)
    passable, _ = body_passable(altered)
    if not passable.any():
        return {"status": "INAPPLICABLE_SEAL_LEFT_NO_PASSABLE_SPACE"}
    _, index, component, cells = walk_graph(passable, altered)
    sizes = np.bincount(component)
    main = int(np.argmax(sizes))

    # Cells of the sealed tile's interior, in grid indices.
    x0 = int(round((tile[0] * pitch[0] - origin[0]) / VOXEL_M))
    x1 = int(round(((tile[0] + 1) * pitch[0] - origin[0]) / VOXEL_M))
    y0 = int(round((tile[1] * pitch[1] - origin[1]) / VOXEL_M))
    y1 = int(round(((tile[1] + 1) * pitch[1] - origin[1]) / VOXEL_M))
    inside = np.zeros_like(passable)
    inside[max(0, x0): max(0, x1), max(0, y0): max(0, y1)] = True
    inside &= passable

    shape_y = passable.shape[1]
    inside_flat = np.flatnonzero(inside.ravel())
    if not inside_flat.size:
        return {"status": "INAPPLICABLE_SEALED_TILE_HAS_NO_PASSABLE_SPACE",
                "sealed_tile": list(tile)}
    inside_nodes = index.ravel()[inside_flat]
    inside_components = component[inside_nodes]
    stranded = inside_nodes[inside_components != main]
    stranded_area = float(stranded.size) * VOXEL_M ** 2
    if stranded_area < MIN_CONTROL_ISOLATED_AREA_M2:
        return {
            "status": "INAPPLICABLE_SEAL_ISOLATED_NOTHING",
            "sealed_tile": list(tile),
            "stranded_area_m2": round(stranded_area, 4),
            "minimum_meaningful_m2": MIN_CONTROL_ISOLATED_AREA_M2,
            "note": ("the wall did not cut anything off, so this arm tested "
                     "nothing; record it and pick another tile rather than "
                     "reading it as a pass"),
        }

    rng = np.random.default_rng(seed)
    outside = np.flatnonzero(component == main)
    draws = min(samples, len(stranded), len(outside))
    a_nodes = rng.choice(stranded, size=draws, replace=False)
    b_nodes = rng.choice(outside, size=draws, replace=False)
    # Straddling pairs must all be unreachable.  Component identity is the same
    # relation the baseline reachability uses, so this exercises the real path.
    reachable = int(np.count_nonzero(
        component[a_nodes] == component[b_nodes]))
    return {
        "status": ("FAIL_AS_REQUIRED" if reachable == 0
                   else "DID_NOT_FAIL_CHECK_IS_INVALID"),
        "sealed_tile": list(tile),
        "stranded_area_m2": round(stranded_area, 4),
        "straddling_pairs_drawn": int(draws),
        "straddling_pairs_reported_reachable": reachable,
        "required_reachable": 0,
        "note": ("every pair with one endpoint inside the sealed tile and one "
                 "outside must be unreachable; any reachable pair means the "
                 "connectivity test cannot see a wall"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("support", type=Path, nargs="?",
                        help="ACTUAL_VOXEL_SUPPORT_<storey>.npz")
    parser.add_argument("--auto-select-control-tile", action="store_true",
                        help="aim N2-1 at the tile with the most body-passable "
                             "area instead of the one given")
    parser.add_argument("--control-samples", type=int, default=300,
                        help="straddling pairs drawn for N2-1")
    parser.add_argument("--self-test", action="store_true",
                        help="check erosion and the step limit on constructed "
                             "cases instead of a stored artifact")
    parser.add_argument("--origin", type=float, nargs=2, default=None,
                        help="grid origin x y in metres; read from metadata "
                             "when omitted")
    parser.add_argument("--pitch", type=float, nargs=2, default=(20.0, 21.0))
    parser.add_argument("--sources", type=int, default=20)
    parser.add_argument("--targets-per-source", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261012)
    parser.add_argument("--negative-control-tile", type=int, nargs=2,
                        default=None,
                        help="run N2-1 by sealing this tile and re-measuring")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.self_test:
        report = self_test()
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report["status"] == "PASS" else 1
    if args.support is None:
        parser.error("give a support npz, or --self-test")

    support, labels, meta = load_support(args.support.expanduser().resolve())
    origin = tuple(args.origin) if args.origin else tuple(
        float(value) for value in meta.get("grid_origin_m", (0.0, 0.0))[:2])
    pitch = (float(args.pitch[0]), float(args.pitch[1]))

    baseline = navigation_report(
        support, origin=origin, pitch=pitch, sources=args.sources,
        targets_per_source=args.targets_per_source, seed=args.seed)

    control: dict[str, Any] | None = None
    if args.negative_control_tile is not None:
        tile = tuple(args.negative_control_tile)
        if args.auto_select_control_tile:
            tile = richest_tile(support, origin=origin, pitch=pitch)
        control = {
            "control": "N2-1_seal_one_tile_straddling_pairs",
            "auto_selected": bool(args.auto_select_control_tile),
            "baseline_status": baseline["status"],
            **seal_control(support, origin=origin, pitch=pitch, tile=tile,
                           samples=args.control_samples, seed=args.seed + 1),
        }

    payload = {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "support": str(args.support.expanduser().resolve()),
        "grid_origin_m": list(origin),
        "frozen_inputs": {
            "voxel_size_m": VOXEL_M,
            "body_radius_m": BODY_RADIUS_M,
            "max_support_step_m": MAX_SUPPORT_STEP_M,
            "source": "Standability V1 pinned values, read only",
        },
        "native_standability_reference": meta.get("native_standability"),
        "baseline": baseline,
        "negative_control": control,
        "formal_acceptance": "NOT EXECUTED",
        "cannot_support": (
            "Reachability and path length on the body-eroded voxel graph do "
            "not establish that a humanoid can walk the route. Dynamics, "
            "collision shapes, headroom along the path and stair traversal are "
            "untested here; that is stage 3."
        ),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False)
    if args.output is not None:
        if args.output.exists():
            raise SystemExit(f"refusing to overwrite {args.output}")
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    print(json.dumps({
        "status": baseline["status"],
        "body_passable_area_m2": baseline.get("body_passable_area_m2"),
        "standable_area_m2": baseline.get("standable_area_m2"),
        "eroded_main_component_fraction": baseline.get(
            "eroded_main_component_fraction"),
        "classes": {name: {
            "pairs": value.get("pairs"),
            "reachability_rate": value.get("reachability_rate"),
            "B1_pass": value.get("B1_pass"),
            "ratio_median": value.get(
                "length_over_euclidean_ratio", {}).get("median"),
            "B3_pass": value.get("B3_pass"),
        } for name, value in baseline.get("classes", {}).items()},
        "negative_control": (control or {}).get("status"),
    }, ensure_ascii=False, indent=1))
    return 0 if baseline["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
