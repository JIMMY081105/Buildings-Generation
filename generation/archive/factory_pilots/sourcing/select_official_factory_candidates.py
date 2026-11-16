#!/usr/bin/env python3
"""Join strict screening results to immutable official package catalog rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow.parquet as pq


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", action="append", nargs=5, metavar=("SOURCE", "VERSION", "SCREEN", "TYPES", "CATALOG"),
        required=True,
    )
    parser.add_argument("--exact-limit-per-source", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    selected = []
    source_summaries = []
    for source, version, screen_path, types_path, catalog_path in args.input:
        screen = json.loads(Path(screen_path).read_text(encoding="utf-8"))
        types = json.loads(Path(types_path).read_text(encoding="utf-8"))["scenes"]
        catalog_rows = pq.read_table(catalog_path).to_pylist()
        by_package = {row["path"]: row for row in catalog_rows}
        exact = [
            row for row in screen["ranked_scenes"]
            if row["text_screen_pass"]
            and types[row["scene"]]["extraction_kind"] == "exact_scene_metadata"
        ]
        if args.exact_limit_per_source:
            exact = exact[: args.exact_limit_per_source]
        for row in exact:
            package = types[row["scene"]]["source_package"]
            catalog = by_package[package]
            selected.append({
                "source": source,
                "version": version,
                "scene": row["scene"],
                "package": package,
                "shard_id": int(catalog["shard_id"]),
                "offset": int(catalog["offset"]),
                "size": int(catalog["size"]),
                "strict_screen_score": row["score"],
                "occurrences": row["occurrences"],
                "unique_assets": row["unique_assets"],
                "industrial_core_room_count": row["industrial_core_room_count"],
                "room_count": row["room_count"],
                "selection_reason": "strict_screen_pass_with_exact_room_metadata",
            })
        source_summaries.append({
            "source": source,
            "version": version,
            "sampled_scenes": screen["scene_count"],
            "preliminary_screen_pass": screen["text_screen_pass_count"],
            "exact_room_metadata_pass_selected": len(exact),
        })

    result = {
        "contract": "factory10_official_candidate_download_selection_v1",
        "selection_policy": (
            "strict screen pass; exact room-level scene_metadata already recovered; "
            "ranked by unchanged industrial evidence score"
        ),
        "source_summaries": source_summaries,
        "selected_count": len(selected),
        "selected": selected,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "selected_count": len(selected),
        "by_source": {summary["source"]: summary["exact_room_metadata_pass_selected"]
                      for summary in source_summaries},
        "compressed_bytes": sum(row["size"] for row in selected),
    }, indent=2))


if __name__ == "__main__":
    main()
