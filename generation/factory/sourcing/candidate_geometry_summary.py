#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def area(rect: list[float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    screen = json.loads(args.screen.read_text(encoding="utf-8"))
    cache = json.loads(args.cache.read_text(encoding="utf-8"))["scenes"]
    for row in screen["ranked_scenes"]:
        if not row["text_screen_pass"]:
            continue
        record = cache.get(row["scene"])
        if not record:
            print(f"{row['scene']}\tMISSING_CACHE\tscore={row['score']}")
            continue
        geometry = record["geometry"]
        areas = [area(room["rect"]) for room in geometry["rooms"]]
        dependency_pass = record["dependencies"].get("dependency_gate_pass") is True
        doorway_pass = geometry["doorway_clearance"].get("pass") is True
        print(
            f"{row['scene']}\tf2f={geometry['floor_to_floor']:.4f}\t"
            f"rooms={len(areas)}\ttotal_rect_area={sum(areas):.1f}\t"
            f"max_room_area={max(areas, default=0):.1f}\t"
            f"door={doorway_pass}\tdeps={dependency_pass}\tscore={row['score']}"
        )


if __name__ == "__main__":
    main()
