#!/usr/bin/env python3
"""Add doorway evidence once per scene so exhaustive A4 can stay CPU-efficient."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pxr import Usd

from doorway_clearance import scan_stage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenes-root", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    document = json.loads(args.input.read_text(encoding="utf-8"))
    if document.get("complete_for_pair_cache") is not True:
        raise ValueError("cannot enrich a partial A1 cache")
    if str(args.scenes_root.resolve()) != document.get("scene_root"):
        raise ValueError("scene root differs from A1 cache provenance")
    rows = document["scenes"]
    for index, (name, record) in enumerate(sorted(rows.items()), 1):
        stage_path = args.scenes_root / name / "scene.usda"
        stage = Usd.Stage.Open(str(stage_path), load=Usd.Stage.LoadAll)
        if stage is None:
            raise RuntimeError(f"could not open {stage_path}")
        record["geometry"]["doorway_clearance"] = scan_stage(stage)
        print(f"doorway cache {index}/{len(rows)} {name}", flush=True)
    document["cache_contract_version"] = 3
    document["doorway_clearance_cached_for_every_scene"] = True
    document["doorway_clearance_scene_count"] = len(rows)
    args.output.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PASS", "scene_count": len(rows),
                      "device": "CPU", "gpu_used": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
