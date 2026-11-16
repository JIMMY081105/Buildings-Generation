#!/usr/bin/env python3
"""Apply the task's strict factory semantics and structural source review.

The upstream text screen deliberately has broad recall.  This second pass is
precision-oriented: it separates office and school evidence, rejects dominant
residential use, and requires both industrial objects and factory-like room
geometry before a scene may supply a floor.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from shapely.geometry import box
from shapely.ops import unary_union


MIN_FLOOR_TO_FLOOR_M = 3.20
MIN_TOTAL_ROOM_AREA_M2 = 35.0
MIN_LARGEST_ROOM_AREA_M2 = 20.0
MIN_LARGEST_ROOM_SHORT_SPAN_M = 3.50
MIN_STRONG_INDUSTRIAL_OCCURRENCES = 12
MIN_STRONG_INDUSTRIAL_UNIQUE_ASSETS = 5

STRONG_INDUSTRIAL_TERMS = {
    "assembly line", "loading dock", "loading bay", "machine shop",
    "forklift", "conveyor", "pallet", "workbench", "tool bench",
    "tool chest", "tool trolley", "tool cart", "tool cabinet", "toolbox",
    "pegboard", "electric drill", "power drill", "cordless drill",
    "industrial drill", "caliper", "micrometer", "multimeter",
    "locking plier", "pliers", "screwdriver", "hand tool", "handheld saw",
    "control panel", "electrical panel", "compressor", "generator",
    "impeller", "pipe fitting", "mechanical component", "aluminum extrusion",
    "storage rack", "warehouse rack", "shipping crate", "storage crate",
    "wooden crate", "platform cart", "hand truck", "oil drum", "steel drum",
    "material bin", "parts bin", "storage bin", "metal shelving",
    "packing station", "packing table",
}

STRONG_OFFICE_FRAGMENTS = (
    "officechair", "filingcabinet", "printer", "copier", "cubicle",
    "conference", "receptiondesk",
)
STRONG_SCHOOL_FRAGMENTS = (
    "schooldesk", "studentdesk", "blackboard", "whiteboard", "lectern",
    "podium", "classroom",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_identity(item: dict[str, Any]) -> str:
    return "".join(character for character in (
        str(item.get("object_type", "")) + " "
        + str(item.get("category", ""))
    ).lower() if character.isalnum())


def count_fragment_evidence(items: list[dict[str, Any]], fragments: tuple[str, ...]) -> tuple[int, int]:
    selected = [item for item in items
                if any(fragment in normalized_identity(item) for fragment in fragments)]
    return sum(int(item.get("count", 0)) for item in selected), len(selected)


def strong_industrial(items: list[dict[str, Any]]) -> tuple[int, int, list[dict[str, Any]]]:
    selected = []
    for item in items:
        terms = {str(term).lower() for term in item.get("matched_terms", [])}
        if terms & STRONG_INDUSTRIAL_TERMS:
            selected.append(item)
    return (
        sum(int(item.get("count", 0)) for item in selected),
        len(selected),
        [{"asset_id": item.get("asset_id"), "count": item.get("count"),
          "object_type": item.get("object_type"), "category": item.get("category"),
          "matched_terms": item.get("matched_terms", [])} for item in selected],
    )


def structural_metrics(geometry: dict[str, Any]) -> dict[str, Any]:
    rectangles = [box(*room["rect"]) for room in geometry["rooms"]]
    union = unary_union(rectangles)
    spans = []
    for room, rectangle in zip(geometry["rooms"], rectangles):
        x0, y0, x1, y1 = room["rect"]
        spans.append({
            "room": room["prim"], "area_m2": rectangle.area,
            "short_span_m": min(x1 - x0, y1 - y0),
            "long_span_m": max(x1 - x0, y1 - y0),
        })
    largest = max(spans, key=lambda item: (item["area_m2"], item["short_span_m"]))
    return {
        "floor_to_floor_m": float(geometry["floor_to_floor"]),
        "room_union_area_m2": round(float(union.area), 6),
        "largest_room": {key: round(value, 6) if isinstance(value, float) else value
                         for key, value in largest.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--geometry-cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    screen_path = args.screen.expanduser().resolve()
    cache_path = args.geometry_cache.expanduser().resolve()
    screen = json.loads(screen_path.read_text(encoding="utf-8"))
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    screen_rows = {row["scene"]: row for row in screen["ranked_scenes"]}
    if set(screen_rows) != set(cache["scenes"]):
        raise RuntimeError("strict-screen and geometry-cache scene sets differ")

    rows = []
    for scene in sorted(screen_rows):
        source = screen_rows[scene]
        cached = cache["scenes"][scene]
        industrial_items = list(source.get("evidence", {}).get("industrial", []))
        office_items = list(source.get("evidence", {}).get("office", []))
        school_items = list(source.get("evidence", {}).get("school", []))
        strong_count, strong_unique, strong_items = strong_industrial(industrial_items)
        office_count, office_unique = count_fragment_evidence(
            office_items, STRONG_OFFICE_FRAGMENTS
        )
        school_count, school_unique = count_fragment_evidence(
            school_items, STRONG_SCHOOL_FRAGMENTS
        )
        industrial = int(source.get("occurrences", {}).get("industrial", 0))
        residential = int(source.get("occurrences", {}).get("residential", 0))
        geometry = structural_metrics(cached["geometry"])
        largest = geometry["largest_room"]
        checks = {
            "upstream_strict_screen_pass": source.get("text_screen_pass") is True,
            "source_dependency_gate_pass": (
                cached.get("dependencies", {}).get("dependency_gate_pass") is True
            ),
            "residential_not_dominant": industrial > residential,
            "strong_industrial_occurrences": strong_count >= MIN_STRONG_INDUSTRIAL_OCCURRENCES,
            "strong_industrial_unique_assets": strong_unique >= MIN_STRONG_INDUSTRIAL_UNIQUE_ASSETS,
            "industrial_exceeds_2x_strong_office": industrial >= 2 * office_count,
            "industrial_exceeds_3x_strong_school": industrial >= 3 * school_count,
            "high_clearance": geometry["floor_to_floor_m"] >= MIN_FLOOR_TO_FLOOR_M,
            "factory_scale_total_area": geometry["room_union_area_m2"] >= MIN_TOTAL_ROOM_AREA_M2,
            "large_span_room_area": largest["area_m2"] >= MIN_LARGEST_ROOM_AREA_M2,
            "large_span_room_width": (
                largest["short_span_m"] >= MIN_LARGEST_ROOM_SHORT_SPAN_M
            ),
        }
        passed = all(checks.values())
        if passed:
            label = "factory"
        elif school_count > 0 and school_count >= office_count:
            label = "school_rejected"
        elif office_count > 0:
            label = "office_rejected"
        elif residential >= industrial:
            label = "residential_rejected"
        else:
            label = "nonfactory_rejected"
        rows.append({
            "scene": scene, "decision": "ACCEPT_FACTORY_FLOOR" if passed else "REJECT",
            "separated_scene_label": label,
            "checks": {name: "PASS" if value else "FAIL" for name, value in checks.items()},
            "semantic_metrics": {
                "industrial_occurrences": industrial,
                "residential_occurrences": residential,
                "strong_industrial_occurrences": strong_count,
                "strong_industrial_unique_assets": strong_unique,
                "strong_office_occurrences": office_count,
                "strong_office_unique_assets": office_unique,
                "strong_school_occurrences": school_count,
                "strong_school_unique_assets": school_unique,
                "warehouse_occurrences": int(source.get("occurrences", {}).get("warehouse", 0)),
            },
            "structural_metrics": geometry,
            "strong_industrial_assets": strong_items,
        })

    accepted = [row["scene"] for row in rows if row["decision"] == "ACCEPT_FACTORY_FLOOR"]
    payload = {
        "schema": "Factory10StrictSemanticStructuralSourceReviewV1",
        "status": "PASS" if accepted else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_screen": str(screen_path), "source_screen_sha256": file_sha256(screen_path),
        "geometry_cache": str(cache_path), "geometry_cache_sha256": file_sha256(cache_path),
        "office_and_school_classified_separately": True,
        "thresholds_fixed_before_A4_selection": {
            "minimum_floor_to_floor_m": MIN_FLOOR_TO_FLOOR_M,
            "minimum_total_room_area_m2": MIN_TOTAL_ROOM_AREA_M2,
            "minimum_largest_room_area_m2": MIN_LARGEST_ROOM_AREA_M2,
            "minimum_largest_room_short_span_m": MIN_LARGEST_ROOM_SHORT_SPAN_M,
            "minimum_strong_industrial_occurrences": MIN_STRONG_INDUSTRIAL_OCCURRENCES,
            "minimum_strong_industrial_unique_assets": MIN_STRONG_INDUSTRIAL_UNIQUE_ASSETS,
            "industrial_to_strong_office_ratio_minimum": 2,
            "industrial_to_strong_school_ratio_minimum": 3,
        },
        "scene_count": len(rows), "accepted_count": len(accepted),
        "accepted_factory_floors": accepted, "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite source review: {args.out}")
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "scene_count": len(rows),
                      "accepted_count": len(accepted)}, indent=2))


if __name__ == "__main__":
    main()
