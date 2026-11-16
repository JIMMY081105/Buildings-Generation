#!/usr/bin/env python3
"""Create task-scoped inputs that force A1 geometry scans for every strict factory.

The established geometry-cache builder discovers scene names from the pair file.
These synthetic pair rows are therefore only a complete scene-name carrier; they
do not assert A3 compatibility and are never used as an A3 result.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--pairs-out", type=Path, required=True)
    parser.add_argument("--types-out", type=Path, required=True)
    args = parser.parse_args()

    screen = json.loads(args.screen.read_text(encoding="utf-8"))
    rows = [
        row for row in screen["ranked_scenes"]
        if row["scene"].endswith("_ceiling") and row["text_screen_pass"]
    ]
    rows.sort(key=lambda row: row["scene"])
    if len(rows) < 2:
        raise RuntimeError("need at least two strict factory scenes")

    names = [row["scene"] for row in rows]
    # A ring makes every scene appear and preserves the distinct-scene property.
    pair_rows = [
        {
            "floor1": name,
            "floor2": names[(index + 1) % len(names)],
            "input_role": "scene_name_carrier_only_not_an_A3_decision",
        }
        for index, name in enumerate(names)
    ]
    pairs = {
        "contract": "factory10_geometry_cache_input_v1",
        "purpose": "force complete A1 scans; A3 compatibility is recomputed later",
        "results": pair_rows,
    }
    types = {
        "contract": "factory10_geometry_cache_types_v1",
        "semantic_policy": "strict official-object-annotation factory screen",
        "scenes": {
            row["scene"]: {
                "type": "factory",
                "factory_core_rooms": [
                    room["room"] for room in row["room_evidence"]
                    if room["industrial_core"]
                ],
                "factory_evidence": {
                    "score": row["score"],
                    "occurrences": row["occurrences"],
                    "unique_assets": row["unique_assets"],
                },
            }
            for row in rows
        },
    }
    args.pairs_out.parent.mkdir(parents=True, exist_ok=True)
    args.types_out.parent.mkdir(parents=True, exist_ok=True)
    args.pairs_out.write_text(json.dumps(pairs, indent=2) + "\n", encoding="utf-8")
    args.types_out.write_text(json.dumps(types, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"strict_factory_scenes": len(names), "scenes": names}, indent=2))


if __name__ == "__main__":
    main()
