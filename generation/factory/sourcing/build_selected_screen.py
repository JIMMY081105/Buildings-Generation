#!/usr/bin/env python3
"""Build a compact exact-room strict-screen document for selected scenes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--screen", action="append", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    selected = {row["scene"] for row in selection["selected"]}
    rows_by_scene = {}
    rules = None
    metadata_asset_count = None
    for path in args.screen:
        document = json.loads(path.read_text(encoding="utf-8"))
        if rules is not None and document["rules"] != rules:
            raise RuntimeError(f"screen rules differ across inputs: {path}")
        rules = rules or document["rules"]
        if (metadata_asset_count is not None
                and document["metadata_asset_count"] != metadata_asset_count):
            raise RuntimeError(f"metadata asset counts differ across inputs: {path}")
        metadata_asset_count = metadata_asset_count or document["metadata_asset_count"]
        for row in document["ranked_scenes"]:
            if row["scene"] not in selected:
                continue
            previous = rows_by_scene.get(row["scene"])
            if previous is not None and previous != row:
                raise RuntimeError(
                    f"conflicting duplicate screen evidence: {row['scene']}"
                )
            rows_by_scene[row["scene"]] = row
    rows = list(rows_by_scene.values())
    found = set(rows_by_scene)
    if found != selected:
        raise RuntimeError(f"selected-screen mismatch: missing={sorted(selected - found)}")
    if not all(row["text_screen_pass"] and row["industrial_core_room_count"] >= 1 for row in rows):
        raise RuntimeError("selected screen contains a non-passing scene")
    rows.sort(key=lambda row: (-row["score"], row["scene"]))
    result = {
        "schema_version": 1,
        "purpose": "exact room-level strict screen for official download selection",
        "rules": rules,
        "scene_count": len(rows),
        "text_screen_pass_count": len(rows),
        "metadata_asset_count": metadata_asset_count,
        "missing_annotation_asset_count": 0,
        "missing_annotation_assets": [],
        "ranked_scenes": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected_screen_scenes": len(rows)}))


if __name__ == "__main__":
    main()
