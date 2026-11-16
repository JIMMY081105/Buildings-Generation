#!/usr/bin/env python3
"""Conservatively recheck strict industrial identity after navigation repairs."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


EXPECTED = 10
BID_RE = re.compile(r"^factory_((?:train|val)\d+)_((?:train|val)\d+)_")
ASSET_ID_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", re.I)
PROTECTED_RE = re.compile(
    r"^(?:doorway_|doorframe_|parc_storage_crate_)|"
    r"(?:^|_)(?:fridge|refrigerator|microwave|oven|dishwasher|"
    r"washing_?machine|dryer)(?:_|$)", re.I
)


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def scene_name(token: str) -> str:
    match = re.fullmatch(r"(train|val)(\d+)", token)
    if not match:
        raise ValueError(token)
    return f"{match.group(1)}_{match.group(2)}_ceiling"


def evidence_asset_ids(scene: dict, category: str) -> set[str]:
    return {
        str(row["asset_id"]).lower()
        for row in scene.get("evidence", {}).get(category, [])
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict-review", required=True, type=Path)
    parser.add_argument("--screen", required=True, type=Path)
    parser.add_argument("--repair-summary", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    require(not output.exists(), f"refusing existing output: {output}")
    review_path = args.strict_review.resolve()
    screen_path = args.screen.resolve()
    review = load(review_path)
    screen = load(screen_path)
    review_rows = {row["scene"]: row for row in review.get("rows", [])}
    screen_rows = {row["scene"]: row for row in screen.get("ranked_scenes", [])}
    require(review.get("status") == "PASS" and review.get("accepted_count") == 157,
            "strict review differs")
    require(len(review_rows) == 184 and len(screen_rows) == 184,
            "source review/screen coverage differs")

    histories: dict[str, list[dict]] = {}
    summary_bindings = []
    for raw in args.repair_summary:
        path = raw.resolve()
        document = load(path)
        iteration = int(document.get("iteration", -1))
        require(document.get("status") == "PASS"
                and document.get("building_count") == EXPECTED
                and document.get("protected_gate_modified") is False
                and document.get("formal_threshold_modified") is False,
                f"repair summary differs: {path}")
        summary_bindings.append({"iteration": iteration, "path": str(path),
                                 "sha256": sha256(path)})
        for row in document.get("rows", []):
            bid = row["BID"]
            if row.get("carried_forward") is True:
                continue
            report_path = Path(row["report"]).resolve()
            require(report_path.is_file() and sha256(report_path) == row["report_sha256"],
                    f"repair report binding differs: {bid}")
            report = load(report_path)
            require(report.get("status") == "PASS"
                    and report.get("building") == bid
                    and report.get("output_sha256") == row["geometry_sha256"],
                    f"repair detail differs: {bid}")
            histories.setdefault(bid, []).append({"iteration": iteration,
                                                  "report": report,
                                                  "path": report_path})
    require(len(histories) == EXPECTED, "repair histories are not exact 10")

    rows = []
    all_bids = sorted(histories)
    for bid in all_bids:
        match = BID_RE.match(bid)
        require(match is not None, f"cannot parse BID source floors: {bid}")
        floor_scenes = {"Floor1": scene_name(match.group(1)),
                        "Floor2": scene_name(match.group(2))}
        removed_by_storey: dict[str, list[str]] = {"Floor1": [], "Floor2": []}
        report_bindings = []
        for item in sorted(histories[bid], key=lambda value: value["iteration"]):
            report = item["report"]
            report_bindings.append({"iteration": item["iteration"],
                                    "path": str(item["path"]),
                                    "sha256": sha256(item["path"])})
            for root in report.get("deactivated_roots", []):
                parts = str(root).split("/")
                if len(parts) > 4 and parts[2] in removed_by_storey:
                    removed_by_storey[parts[2]].append(parts[4])

        floor_rows = []
        for storey in ("Floor1", "Floor2"):
            scene_name_value = floor_scenes[storey]
            strict = review_rows[scene_name_value]
            screen_row = screen_rows[scene_name_value]
            require(strict.get("decision") == "ACCEPT_FACTORY_FLOOR",
                    f"source was not strict accepted: {scene_name_value}")
            removed_roots = removed_by_storey[storey]
            protected = sorted(root for root in removed_roots if PROTECTED_RE.search(root))
            require(not protected, f"protected root removed: {bid}/{storey}/{protected}")
            removed_asset_ids = []
            for root in removed_roots:
                found = ASSET_ID_RE.search(root)
                if found:
                    removed_asset_ids.append(found.group(1).lower())
            removed_counts = Counter(removed_asset_ids)
            industrial_ids = evidence_asset_ids(screen_row, "industrial")
            strong_rows = strict.get("strong_industrial_assets", [])
            strong_counts = {
                str(row["asset_id"]).lower(): int(row["count"])
                for row in strong_rows
            }
            industrial_removed = sum(
                count for asset_id, count in removed_counts.items()
                if asset_id in industrial_ids
            )
            strong_removed = sum(
                min(count, strong_counts.get(asset_id, 0))
                for asset_id, count in removed_counts.items()
            )
            semantic = strict["semantic_metrics"]
            industrial_after_lower = int(semantic["industrial_occurrences"]) - industrial_removed
            strong_after = int(semantic["strong_industrial_occurrences"]) - strong_removed
            strong_unique_after = sum(
                removed_counts.get(asset_id, 0) < original_count
                for asset_id, original_count in strong_counts.items()
            )
            residential_upper = int(semantic["residential_occurrences"])
            office_upper = int(semantic["strong_office_occurrences"])
            school_upper = int(semantic["strong_school_occurrences"])
            checks = {
                "no_protected_root_removed": not protected,
                "conservative_industrial_still_exceeds_original_residential": (
                    industrial_after_lower > residential_upper
                ),
                "strong_industrial_occurrences_at_least_12": strong_after >= 12,
                "strong_industrial_unique_assets_at_least_5": strong_unique_after >= 5,
                "strong_industrial_exceeds_2x_original_office": (
                    strong_after >= 2 * office_upper
                ),
                "strong_industrial_exceeds_3x_original_school": (
                    strong_after >= 3 * school_upper
                ),
            }
            floor_rows.append({
                "storey": storey,
                "source_scene": scene_name_value,
                "removed_object_root_count": len(removed_roots),
                "removed_object_roots": sorted(removed_roots),
                "removed_asset_id_counts": dict(sorted(removed_counts.items())),
                "protected_roots_removed": protected,
                "conservative_metrics_after_repair": {
                    "industrial_occurrences_lower_bound": industrial_after_lower,
                    "residential_occurrences_upper_bound": residential_upper,
                    "strong_industrial_occurrences": strong_after,
                    "strong_industrial_unique_assets": strong_unique_after,
                    "strong_office_occurrences_upper_bound": office_upper,
                    "strong_school_occurrences_upper_bound": school_upper,
                    "industrial_asset_instances_removed": industrial_removed,
                    "strong_industrial_asset_instances_removed": strong_removed,
                },
                "checks": checks,
                "status": "PASS" if all(checks.values()) else "FAIL",
            })
        rows.append({"BID": bid,
                     "status": "PASS" if all(row["status"] == "PASS"
                                               for row in floor_rows) else "FAIL",
                     "repair_reports": report_bindings,
                     "floors": floor_rows})
    status = "PASS" if len(rows) == EXPECTED and all(
        row["status"] == "PASS" for row in rows) else "FAIL"
    payload = {
        "schema": "Factory10PostRepairIndustrialIdentityAuditV1",
        "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "building_count": len(rows),
        "method": (
            "conservative: subtract removed asset instances from industrial and "
            "strong-industrial counts, but do not reduce residential/office/school "
            "comparators; structural metrics are unchanged or relaxed by openings"
        ),
        "thresholds_modified": False,
        "protected_gate_modified": False,
        "strict_review": str(review_path),
        "strict_review_sha256": sha256(review_path),
        "screen": str(screen_path),
        "screen_sha256": sha256(screen_path),
        "repair_summaries": sorted(summary_bindings, key=lambda row: row["iteration"]),
        "rows": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "buildings": len(rows),
                      "failed_buildings": sum(row["status"] != "PASS" for row in rows)}))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
