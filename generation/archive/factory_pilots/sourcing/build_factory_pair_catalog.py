#!/usr/bin/env python3
"""Build a factory-only A3 pair catalog from the frozen A1 geometry cache.

The spatial rules are the established floor-level rules: directed distinct-scene
pairs, footprint IoU >= 0.60, and unsupported upper-floor area <= 0.12.  Factory
semantics come from the separately auditable official-annotation screen; this
adapter never re-labels a scene as home or school.
"""

from __future__ import annotations

import argparse
from itertools import permutations
import json
from pathlib import Path

from shapely.geometry import box
from shapely.ops import unary_union


STAIRS = {"straight": (1.62, 5.22), "l_shaped": (4.32, 4.08), "u_shaped": (2.99, 4.19)}
MIN_IOU = 0.60
MAX_FLOOR2_OVERHANG = 0.12
MARGIN = 0.25


def room_union(geometry: dict):
    return unary_union([box(*room["rect"]) for room in geometry["rooms"]])


def wall_union(geometry: dict):
    walls = [box(*rect) for rect in geometry.get("walls", [])]
    return unary_union(walls) if walls else None


def free_spot(geometry: dict, fx: float, fy: float):
    need_x, need_y = fx + 2 * MARGIN, fy + 2 * MARGIN
    obstacles = [box(*rect) for rect in geometry.get("obstacles", [])]
    rooms = sorted(
        geometry["rooms"],
        key=lambda room: -box(*room["rect"]).area,
    )
    for room in rooms:
        rx0, ry0, rx1, ry1 = room["rect"]
        if rx1 - rx0 < need_x or ry1 - ry0 < need_y:
            continue
        i = 0
        while rx0 + MARGIN + i * 0.25 + fx <= rx1 - MARGIN:
            j = 0
            while ry0 + MARGIN + j * 0.25 + fy <= ry1 - MARGIN:
                x0 = rx0 + MARGIN + i * 0.25
                y0 = ry0 + MARGIN + j * 0.25
                candidate = box(x0, y0, x0 + fx, y0 + fy)
                if not any(candidate.intersects(obstacle) for obstacle in obstacles):
                    return {"room": room["prim"], "rect": list(candidate.bounds),
                            "room_rect": list(room["rect"])}
                j += 1
            i += 1
    return None


def arrival_ok(geometry: dict, rect: list[float]) -> dict:
    polygon = box(*rect)
    rooms = [(room["prim"], box(*room["rect"])) for room in geometry["rooms"]]
    inside = next((name for name, room in rooms if room.contains(polygon.buffer(-1e-6))), None)
    rooms_union = unary_union([room for _, room in rooms])
    walls = wall_union(geometry)
    coverage = polygon.intersection(rooms_union).area / polygon.area
    wall_hit = walls is not None and polygon.intersection(walls).area > 0.02
    furniture = sum(polygon.intersects(box(*rect)) for rect in geometry.get("obstacles", []))
    return {
        "inside_single_room": inside,
        "room_coverage": round(coverage, 4),
        "intersects_wall": bool(wall_hit),
        "furniture_overlaps": int(furniture),
        "ok": bool(inside is not None and not wall_hit and furniture == 0),
    }


def compact_evidence(row: dict) -> dict:
    return {
        "factory_text_screen_pass": row["text_screen_pass"],
        "score": row["score"],
        "occurrences": row["occurrences"],
        "unique_assets": row["unique_assets"],
        "factory_core_rooms": [
            room["room"] for room in row["room_evidence"] if room["industrial_core"]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--geometry-cache", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    screen = json.loads(args.screen.read_text(encoding="utf-8"))
    cache_doc = json.loads(args.geometry_cache.read_text(encoding="utf-8"))
    cache = cache_doc["scenes"]
    decisions = []
    accepted: dict[str, dict] = {}
    for row in screen["ranked_scenes"]:
        if not row["scene"].endswith("_ceiling"):
            continue
        reasons = []
        if not row["text_screen_pass"]:
            reasons.append("FACTORY_TEXT_SCREEN_FAIL")
        cached = cache.get(row["scene"])
        if cached is None:
            reasons.append("A1_GEOMETRY_CACHE_MISSING")
        elif cached.get("dependencies", {}).get("dependency_gate_pass") is not True:
            reasons.append("SOURCE_DEPENDENCY_GATE_FAIL")
        decision = "ACCEPT_FOR_A3_SHAPE_PAIRING" if not reasons else "REJECT"
        decisions.append({
            "scene": row["scene"], "decision": decision, "reasons": reasons,
            "factory_evidence": compact_evidence(row),
            "geometry_cache_present": cached is not None,
            "doorway_clearance_pass": (
                cached.get("geometry", {}).get("doorway_clearance", {}).get("pass")
                if cached else None
            ),
            "note": "Doorway clearance is retained for A4 rejection, not prefiltered in A3.",
        })
        if not reasons:
            accepted[row["scene"]] = {"screen": row, "cache": cached}

    results = []
    overhang_rejections = []
    iou_rejections = 0
    for lower_name, upper_name in permutations(sorted(accepted), 2):
        lower = accepted[lower_name]["cache"]["geometry"]
        upper = accepted[upper_name]["cache"]["geometry"]
        lower_union, upper_union = room_union(lower), room_union(upper)
        intersection = lower_union.intersection(upper_union).area
        union = lower_union.union(upper_union).area
        iou = intersection / union if union > 0 else 0.0
        if iou < MIN_IOU:
            iou_rejections += 1
            continue
        overhang_area = upper_union.difference(lower_union).area
        overhang_ratio = overhang_area / upper_union.area if upper_union.area else 1.0
        if overhang_ratio > MAX_FLOOR2_OVERHANG:
            overhang_rejections.append({
                "floor1": lower_name, "floor2": upper_name,
                "footprint_iou": round(iou, 4),
                "floor2_overhang_area_m2": round(overhang_area, 4),
                "floor2_overhang_ratio": round(overhang_ratio, 6),
                "threshold": MAX_FLOOR2_OVERHANG,
                "reason": "floor2_overhang_exceeds_threshold",
            })
            continue
        lower_row = accepted[lower_name]["screen"]
        upper_row = accepted[upper_name]["screen"]
        lower_core = compact_evidence(lower_row)["factory_core_rooms"]
        upper_core = compact_evidence(upper_row)["factory_core_rooms"]
        for kind, (fx, fy) in STAIRS.items():
            spot = free_spot(lower, fx, fy)
            arrival = arrival_ok(upper, spot["rect"]) if spot else None
            results.append({
                "floor1": lower_name, "floor2": upper_name, "kind": kind,
                "scene_type": "factory",
                "floor1_type_votes": {"factory": lower_row["score"], "home": 0, "school": 0},
                "floor2_type_votes": {"factory": upper_row["score"], "home": 0, "school": 0},
                "floor1_type_bearing_rooms": lower_core,
                "floor2_type_bearing_rooms": upper_core,
                "floor1_cross_room_vote_pass": len(lower_core) >= 1,
                "floor2_cross_room_vote_pass": len(upper_core) >= 1,
                "factory_semantic_rule": "at_least_one_industrial_core_room_per_floor",
                "floor1_factory_evidence": compact_evidence(lower_row),
                "floor2_factory_evidence": compact_evidence(upper_row),
                "footprint_iou": round(iou, 4),
                "floor2_overhang_area_m2": round(overhang_area, 4),
                "floor2_overhang_ratio": round(overhang_ratio, 6),
                "floor2_overhang_pass": True,
                "floor1_f2f": round(float(lower["floor_to_floor"]), 4),
                "floor2_f2f": round(float(upper["floor_to_floor"]), 4),
                "initial_zero_degree_probe": {
                    "placement": spot,
                    "arrival": arrival,
                    "passed": bool(arrival and arrival["ok"]),
                    "decision_role": "diagnostic_only_not_an_A3_gate",
                },
                "pair_gate_passed": True,
                "usable": True,
            })

    results.sort(key=lambda row: (-row["footprint_iou"], row["floor1"], row["floor2"], row["kind"]))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pair_doc = {
        "contract": "factory10_A3_floor_level_pair_catalog_v1",
        "source_screen": str(args.screen),
        "source_geometry_cache": str(args.geometry_cache),
        "rules": {
            "distinct_floor_scenes": True,
            "minimum_footprint_iou": MIN_IOU,
            "maximum_floor2_overhang_ratio": MAX_FLOOR2_OVERHANG,
            "factory_semantics": "strict official-annotation screen and >=1 industrial core room per floor",
            "initial_zero_degree_probe": "diagnostic_only; A4 tests all rotations and exact triangles",
        },
        "factory_scenes_A3_eligible": len(accepted),
        "directed_pairs_iou_rejected": iou_rejections,
        "directed_pairs_overhang_rejected": len(overhang_rejections),
        "overhang_rejections": overhang_rejections,
        "candidates": len(results),
        "usable": len(results),
        "results": results,
    }
    (args.out_dir / "scene_pairs_factory.json").write_text(
        json.dumps(pair_doc, indent=2) + "\n", encoding="utf-8"
    )
    for height_mm in sorted({int(round(row["floor1_f2f"] * 100)) for row in results}):
        grouped = [row for row in results if int(round(row["floor1_f2f"] * 100)) == height_mm]
        (args.out_dir / f"pairs_h{height_mm}.json").write_text(
            json.dumps({
                "height_m": height_mm / 100.0,
                "candidates": len(grouped), "usable": len(grouped), "results": grouped,
            }, indent=2) + "\n", encoding="utf-8"
        )
    (args.out_dir / "SOURCE_DECISIONS.json").write_text(
        json.dumps({
            "contract": "factory10_source_decisions_v1",
            "rules": screen["rules"],
            "accepted_for_A3": sorted(accepted),
            "accepted_count": len(accepted),
            "decisions": decisions,
        }, indent=2) + "\n", encoding="utf-8"
    )
    types_doc = {
        "contract": "factory10_scene_types_v1",
        "scene_root": cache_doc.get("scene_root"),
        "semantic_policy": "factory is independent of home, school, and office labels",
        "counts": {"factory": len(accepted)},
        "scenes": {
            name: {
                "type": "factory",
                "factory_core_room_pass": True,
                "factory_core_rooms": compact_evidence(record["screen"])["factory_core_rooms"],
                "factory_evidence": compact_evidence(record["screen"]),
                "automatic_residential_classifier_type": record["screen"]["automatic_residential_classifier_type"],
            }
            for name, record in sorted(accepted.items())
        },
    }
    (args.out_dir / "scene_types_factory.json").write_text(
        json.dumps(types_doc, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "factory_scenes_A3_eligible": len(accepted),
        "pair_candidates": len(results),
        "height_counts": {
            str(height): sum(int(round(row["floor1_f2f"] * 100)) == height for row in results)
            for height in sorted({int(round(row["floor1_f2f"] * 100)) for row in results})
        },
    }, indent=2))


if __name__ == "__main__":
    main()
