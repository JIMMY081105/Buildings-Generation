#!/usr/bin/env python3
"""Build the final exact-10 source decision from the complete 184-floor review."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


EXPECTED = 10
BID_RE = re.compile(r"^factory_((?:train|val)\d+)_((?:train|val)\d+)_")


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def scene_name(token: str) -> str:
    match = re.fullmatch(r"(train|val)(\d+)", token)
    if not match:
        raise ValueError(f"unexpected source token: {token}")
    return f"{match.group(1)}_{match.group(2)}_ceiling"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--strict-review", required=True, type=Path)
    parser.add_argument("--variant-selection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inventory_path = args.inventory.resolve()
    review_path = args.strict_review.resolve()
    variant_path = args.variant_selection.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    inventory = load(inventory_path)
    review = load(review_path)
    variants = load(variant_path)
    if not (
        inventory.get("contract") == "factory10_molmospaces_subset_inventory_v1"
        and len(inventory.get("subsets", [])) == 8
        and inventory.get("total_official_package_count") == 231467
        and review.get("status") == "PASS"
        and review.get("scene_count") == 184
        and review.get("accepted_count") == 157
        and variants.get("status") == "PASS"
        and variants.get("selected_count") == EXPECTED
        and variants.get("machine_gate_thresholds_modified") is False
    ):
        raise ValueError("inventory/review/selection contract differs")
    review_rows = {row["scene"]: row for row in review["rows"]}
    selected = []
    unique_scenes = set()
    seen_bids = set()
    for variant in sorted(variants["variants"], key=lambda row: row["BID"]):
        bid = variant["BID"]
        match = BID_RE.match(bid)
        if not match or bid in seen_bids:
            raise ValueError(f"invalid or duplicate BID: {bid}")
        seen_bids.add(bid)
        floor_scenes = [scene_name(match.group(1)), scene_name(match.group(2))]
        floors = []
        for storey, scene in zip(("Floor1", "Floor2"), floor_scenes):
            row = review_rows.get(scene)
            if not row or row.get("decision") != "ACCEPT_FACTORY_FLOOR":
                raise ValueError(f"selected source is not strict accepted: {bid}/{scene}")
            if set(row.get("checks", {}).values()) != {"PASS"}:
                raise ValueError(f"selected source has a failed strict check: {scene}")
            unique_scenes.add(scene)
            floors.append({
                "storey": storey,
                "scene": scene,
                "decision": row["decision"],
                "checks": row["checks"],
                "semantic_metrics": row["semantic_metrics"],
                "structural_metrics": row["structural_metrics"],
                "strong_industrial_assets": row["strong_industrial_assets"],
            })
        selected.append({
            "BID": bid,
            "source_base_bid": variant["source_base_bid"],
            "placement_variant_id": variant["placement_variant_id"],
            "placement": {
                "center_xy_m": variant["center_xy_m"],
                "rect": variant["rect"],
                "rotation_deg": variant["rotation_deg"],
            },
            "floors": floors,
        })
    if len(selected) != EXPECTED or len(unique_scenes) != 9:
        raise ValueError("selected exact-10 or unique source-floor count differs")
    payload = {
        "schema": "Factory10FinalSceneSourceDecisionV1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "all eight official MolmoSpaces Isaac/USD scene subsets",
        "all_available_usd_subsets_inventoried": True,
        "official_package_count": inventory["total_official_package_count"],
        "official_compressed_bytes": inventory["total_official_compressed_bytes"],
        "catalog_subset_count": len(inventory["subsets"]),
        "subset_decisions": inventory["subsets"],
        "search_execution": {
            "primary_train_sampled_scenes": 8192,
            "primary_val_sampled_scenes": 4096,
            "exact_candidate_floor_count": review["scene_count"],
            "strict_accepted_floor_count": review["accepted_count"],
            "strict_rejected_floor_count": review["scene_count"] - review["accepted_count"],
            "office_and_school_classified_separately": review.get(
                "office_and_school_classified_separately"
            ),
            "thresholds_fixed_before_A4_selection": review[
                "thresholds_fixed_before_A4_selection"
            ],
        },
        "selection": {
            "building_count": len(selected),
            "unique_source_floor_count": len(unique_scenes),
            "unique_source_floors": sorted(unique_scenes),
            "machine_gate_thresholds_modified": False,
            "buildings": selected,
        },
        "evidence_bindings": {
            "subset_inventory": str(inventory_path),
            "subset_inventory_sha256": sha256(inventory_path),
            "strict_source_review_combined184": str(review_path),
            "strict_source_review_combined184_sha256": sha256(review_path),
            "variant_selection": str(variant_path),
            "variant_selection_sha256": sha256(variant_path),
        },
        "supersedes": (
            "all earlier partial source-decision files, including the round-1 "
            "49-strict-floor decision"
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "buildings": len(selected),
                      "accepted_floors": review["accepted_count"],
                      "unique_selected_floors": len(unique_scenes)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
