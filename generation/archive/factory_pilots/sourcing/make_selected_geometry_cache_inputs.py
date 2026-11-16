#!/usr/bin/env python3
"""Create scene-name-carrier pairs and authoritative factory types for a selection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--pairs-out", type=Path, required=True)
    parser.add_argument("--types-out", type=Path, required=True)
    args = parser.parse_args()
    selected = json.loads(args.selection.read_text(encoding="utf-8"))["selected"]
    if not selected:
        raise RuntimeError("selection contains no scenes")
    selected = sorted(selected, key=lambda row: row["scene"])
    names = [row["scene"] for row in selected]
    pairs = {
        "contract": "factory10_selected_geometry_cache_input_v1",
        "purpose": "scene-name carrier only; A3 shape compatibility is recomputed",
        "results": [
            {
                "floor1": name,
                "floor2": names[(index + 1) % len(names)],
                "input_role": "scene_name_carrier_only_not_an_A3_decision",
            }
            for index, name in enumerate(names)
        ],
    }
    types = {
        "contract": "factory10_selected_scene_types_v1",
        "semantic_policy": "strict screen pass with exact room-level metadata",
        "scenes": {
            row["scene"]: {
                "type": "factory",
                "source": row["source"],
                "source_version": row["version"],
                "source_package": row["package"],
                "factory_core_room_pass": row["industrial_core_room_count"] >= 1,
                "factory_evidence": {
                    "strict_screen_score": row["strict_screen_score"],
                    "occurrences": row["occurrences"],
                    "unique_assets": row["unique_assets"],
                    "industrial_core_room_count": row["industrial_core_room_count"],
                    "room_count": row["room_count"],
                },
            }
            for row in selected
        },
    }
    args.pairs_out.parent.mkdir(parents=True, exist_ok=True)
    args.types_out.parent.mkdir(parents=True, exist_ok=True)
    args.pairs_out.write_text(json.dumps(pairs, indent=2) + "\n", encoding="utf-8")
    args.types_out.write_text(json.dumps(types, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected_scenes": len(names)}))


if __name__ == "__main__":
    main()
