#!/usr/bin/env python3
"""Inventory every official Isaac/USD MolmoSpaces scene subset and decision."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq


FAMILY = {
    "ithor": ("iTHOR", "household-only benchmark; source-level industrial reject"),
    "procthor-10k-train": ("MSProc", "procedural residential; source-level industrial reject"),
    "procthor-10k-val": ("MSProc", "procedural residential; source-level industrial reject"),
    "procthor-10k-test": ("MSProc", "procedural residential; source-level industrial reject"),
    "procthor-objaverse-train": ("MSProcObja", "procedural residential/multi-room; source-level industrial reject"),
    "procthor-objaverse-val": ("MSProcObja", "procedural residential/multi-room; source-level industrial reject"),
    "holodeck-objaverse-train": ("MSMultiType", "101 scene types / 546 room types; primary industrial search"),
    "holodeck-objaverse-val": ("MSMultiType", "101 scene types / 546 room types; primary industrial search"),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-dir", type=Path, required=True)
    parser.add_argument("--train-screen", type=Path, required=True)
    parser.add_argument("--val-screen", type=Path, required=True)
    parser.add_argument("--installed-screen", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    screens = {
        "holodeck-objaverse-train": json.loads(args.train_screen.read_text(encoding="utf-8")),
        "holodeck-objaverse-val": json.loads(args.val_screen.read_text(encoding="utf-8")),
    }
    installed = json.loads(args.installed_screen.read_text(encoding="utf-8"))
    rows = []
    for path in sorted(args.catalog_dir.glob("*.parquet")):
        stem = path.stem
        source, version = stem.rsplit("-", 1)
        table = pq.read_table(path)
        records = table.to_pylist()
        family, role = FAMILY[source]
        item = {
            "source": source,
            "version": version,
            "family": family,
            "official_catalog_path": str(path),
            "official_catalog_sha256": digest(path),
            "official_package_count": len(records),
            "official_compressed_bytes": sum(int(record["size"]) for record in records),
            "industrial_search_decision": role,
        }
        if source in screens:
            screen = screens[source]
            item["industrial_screen"] = {
                "method": "deterministic stratified HTTP-range sample joined to official Objathor annotations",
                "sampled_scene_count": screen["scene_count"],
                "strict_preliminary_pass_count": screen["text_screen_pass_count"],
            }
            if source == "holodeck-objaverse-train":
                item["installed_prefix_screen"] = {
                    "installed_package_count": installed["scene_count"],
                    "strict_pass_count": installed["text_screen_pass_count"],
                    "note": "the installed set contains ceiling variants only in this count",
                }
        rows.append(item)

    result = {
        "contract": "factory10_molmospaces_subset_inventory_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "all official Isaac/USD scene subsets listed by MolmoSpaces downloader",
        "official_catalog_url_root": "https://huggingface.co/datasets/allenai/molmospaces/tree/main/isaac/scenes",
        "molmospaces_repository_commit": "735138876887eb64650742631b8dda841dd264bf",
        "holodeck_repository_commit": "362b8ed948b867b69a72f1f9491f4caa88419bfc",
        "total_official_package_count": sum(row["official_package_count"] for row in rows),
        "total_official_compressed_bytes": sum(row["official_compressed_bytes"] for row in rows),
        "subsets": rows,
        "consistency_note": (
            "MJCF entries are alternate simulator conversions of these scene families; the existing "
            "floor-level pipeline consumes USD, so only the eight official USD catalogs are candidates."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "subsets": len(rows),
        "packages": result["total_official_package_count"],
        "compressed_bytes": result["total_official_compressed_bytes"],
    }, indent=2))


if __name__ == "__main__":
    main()
