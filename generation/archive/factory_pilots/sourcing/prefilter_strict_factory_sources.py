#!/usr/bin/env python3
"""Select scenes requiring full-package/dependency validation without weakening gates."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import strict_factory_source_review as strict


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--geometry-cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite prefilter: {args.out}")
    screen = json.loads(args.screen.read_text(encoding="utf-8"))
    cache = json.loads(args.geometry_cache.read_text(encoding="utf-8"))
    if cache.get("cache_contract_version") != "PREFILTER_ONLY_NOT_A1_A4":
        raise ValueError("input is not the explicit prefilter-only cache contract")
    source_rows = {row["scene"]: row for row in screen["ranked_scenes"]}
    if set(source_rows) != set(cache["scenes"]):
        raise RuntimeError("screen and prefilter cache scene sets differ")

    rows = []
    for scene in sorted(source_rows):
        source = source_rows[scene]
        industrial_items = list(source.get("evidence", {}).get("industrial", []))
        office_items = list(source.get("evidence", {}).get("office", []))
        school_items = list(source.get("evidence", {}).get("school", []))
        strong_count, strong_unique, strong_items = strict.strong_industrial(industrial_items)
        office_count, office_unique = strict.count_fragment_evidence(
            office_items, strict.STRONG_OFFICE_FRAGMENTS
        )
        school_count, school_unique = strict.count_fragment_evidence(
            school_items, strict.STRONG_SCHOOL_FRAGMENTS
        )
        industrial = int(source.get("occurrences", {}).get("industrial", 0))
        residential = int(source.get("occurrences", {}).get("residential", 0))
        geometry = strict.structural_metrics(cache["scenes"][scene]["geometry"])
        largest = geometry["largest_room"]
        checks = {
            "upstream_strict_screen_pass": source.get("text_screen_pass") is True,
            "residential_not_dominant": industrial > residential,
            "strong_industrial_occurrences": (
                strong_count >= strict.MIN_STRONG_INDUSTRIAL_OCCURRENCES
            ),
            "strong_industrial_unique_assets": (
                strong_unique >= strict.MIN_STRONG_INDUSTRIAL_UNIQUE_ASSETS
            ),
            "industrial_exceeds_2x_strong_office": industrial >= 2 * office_count,
            "industrial_exceeds_3x_strong_school": industrial >= 3 * school_count,
            "high_clearance": geometry["floor_to_floor_m"] >= strict.MIN_FLOOR_TO_FLOOR_M,
            "factory_scale_total_area": (
                geometry["room_union_area_m2"] >= strict.MIN_TOTAL_ROOM_AREA_M2
            ),
            "large_span_room_area": (
                largest["area_m2"] >= strict.MIN_LARGEST_ROOM_AREA_M2
            ),
            "large_span_room_width": (
                largest["short_span_m"] >= strict.MIN_LARGEST_ROOM_SHORT_SPAN_M
            ),
        }
        potential = all(checks.values())
        rows.append({
            "scene": scene,
            "decision": (
                "REQUIRES_FULL_PACKAGE_AND_DEPENDENCY_VALIDATION"
                if potential else "REJECT_WITHOUT_FULL_PACKAGE_TRANSFER"
            ),
            "all_non_dependency_strict_checks_pass": potential,
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
            },
            "structural_metrics": geometry,
            "strong_industrial_assets": strong_items,
        })
    potential = [row["scene"] for row in rows if row["all_non_dependency_strict_checks_pass"]]
    output = {
        "schema": "Factory10StrictNonDependencyPrefilterV1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_screen": str(args.screen.resolve()),
        "source_screen_sha256": sha256(args.screen),
        "source_geometry_cache": str(args.geometry_cache.resolve()),
        "source_geometry_cache_sha256": sha256(args.geometry_cache),
        "criteria_unchanged_from_strict_factory_source_review": True,
        "dependency_gate_deferred_not_waived": True,
        "full_package_and_dependency_closure_required_before_acceptance": True,
        "scene_count": len(rows),
        "potential_count": len(potential),
        "potential_scenes": potential,
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "scene_count": len(rows), "potential_count": len(potential)
    }, indent=2))


if __name__ == "__main__":
    main()
