#!/usr/bin/env python3
"""Seal MolmoSpaces subset inventory and strict factory-source decisions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--strict-review", type=Path, required=True)
    parser.add_argument("--object-audit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    paths = {name: path.expanduser().resolve() for name, path in {
        "inventory": args.inventory, "selection": args.selection,
        "strict_review": args.strict_review, "object_audit": args.object_audit,
    }.items()}
    inventory, selection, review, objects = (load(paths[name]) for name in paths)
    if inventory.get("total_official_package_count") != 231467:
        raise ValueError("official MolmoSpaces package inventory differs")
    if len(inventory.get("subsets", [])) != 8:
        raise ValueError("official Isaac/USD subset count differs")
    if selection.get("selected_count") != len(selection.get("selected", [])):
        raise ValueError("selected official package count differs")
    if review.get("scene_count") != selection.get("selected_count"):
        raise ValueError("strict review does not cover every downloaded scene")
    if objects.get("status") != "PASS" or objects.get("missing_count") != 0:
        raise ValueError("object dependency closure is not PASS")
    selected_by_source = {}
    for row in selection["selected"]:
        selected_by_source[row["source"]] = selected_by_source.get(row["source"], 0) + 1
    accepted = set(review["accepted_factory_floors"])
    accepted_by_source = {}
    for row in selection["selected"]:
        if row["scene"] in accepted:
            accepted_by_source[row["source"]] = accepted_by_source.get(row["source"], 0) + 1
    payload = {
        "schema": "Factory10SceneSourceDecisionV1", "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "all eight official MolmoSpaces Isaac/USD scene subsets",
        "official_package_count_inventoried": inventory["total_official_package_count"],
        "official_subset_count": len(inventory["subsets"]),
        "source_level_policy": {
            "MSMultiType": "primary industrial search; train and validation both screened",
            "MSProc": "procedural residential; rejected at family level",
            "MSProcObja": "procedural residential/multi-room; rejected at family level",
            "iTHOR": "household benchmark; rejected at family level",
            "MJCF": "alternate conversion, not a separate scene population for the USD pipeline",
        },
        "positive_terms": [
            "warehouse", "factory", "workshop", "plant", "industrial", "storage",
            "loading dock", "assembly", "machine shop", "garage",
        ],
        "negative_dominance_terms": [
            "bedroom", "bathroom", "kitchen", "nursery", "living room",
        ],
        "office_and_school_classified_separately": True,
        "subset_inventory": inventory["subsets"],
        "downloaded_exact_room_candidate_count": selection["selected_count"],
        "downloaded_by_source": dict(sorted(selected_by_source.items())),
        "strict_factory_floor_source_count": review["accepted_count"],
        "strict_factory_floors_by_source": dict(sorted(accepted_by_source.items())),
        "strict_factory_floors": sorted(accepted),
        "strict_review_thresholds": review["thresholds_fixed_before_A4_selection"],
        "strict_review_rows": review["rows"],
        "official_object_dependency_closure": {
            "dependency_count": objects["dependency_count"],
            "present_count": objects["present_count"],
            "missing_count": objects["missing_count"],
            "present_count_by_source": objects["present_count_by_source"],
        },
        "evidence": {
            name: {"path": str(path), "sha256": sha256(path)}
            for name, path in paths.items()
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite decision: {args.out}")
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "official_subset_count": payload["official_subset_count"],
        "downloaded_exact_room_candidate_count": selection["selected_count"],
        "strict_factory_floor_source_count": review["accepted_count"],
    }, indent=2))


if __name__ == "__main__":
    main()
