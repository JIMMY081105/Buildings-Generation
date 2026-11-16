#!/usr/bin/env python3
"""Add frozen A4-v2 doorway evidence to a task-local scene cache.

The expensive per-scene results are checkpointed as immutable JSON files.  A
completed cache is written only after every scene referenced by the supplied
pair catalogs has a contract-v2 result.  Source cache and source USD files are
never modified.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def pair_scene_names(paths: list[Path]) -> set[str]:
    names: set[str] = set()
    for path in paths:
        document = load(path)
        for row in document.get("results", []):
            if row.get("usable") is True:
                names.update((str(row["floor1"]), str(row["floor2"])))
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-cache", type=Path, required=True)
    parser.add_argument("--pairs", type=Path, action="append", required=True)
    parser.add_argument("--scene-root", type=Path, required=True)
    parser.add_argument("--door-scripts", type=Path, required=True)
    parser.add_argument("--checkpoints", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--scan-only", action="store_true")
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("shard index must be in [0, shard count)")

    sys.path.insert(0, str(args.door_scripts.resolve()))
    from doorway_clearance import scan_stage  # noqa: PLC0415
    from pxr import Usd  # noqa: PLC0415

    base = load(args.base_cache)
    required_all = pair_scene_names(args.pairs)
    missing = sorted(required_all.difference(base.get("scenes", {})))
    if missing:
        raise ValueError(f"pair scenes missing from base cache: {missing}")
    args.checkpoints.mkdir(parents=True, exist_ok=True)

    required = [name for index, name in enumerate(sorted(required_all))
                if index % args.shard_count == args.shard_index]
    for index, name in enumerate(required, 1):
        checkpoint = args.checkpoints / f"{name}.json"
        stage_path = args.scene_root / name / "scene.usda"
        stage_hash = sha256(stage_path)
        if checkpoint.exists():
            result = load(checkpoint)
            if result.get("contract_version") != 2:
                raise ValueError(f"stale doorway checkpoint: {checkpoint}")
            if result.get("source_scene") != name:
                raise ValueError(f"doorway checkpoint scene mismatch: {checkpoint}")
            if result.get("source_stage_sha256") != stage_hash:
                raise ValueError(f"doorway checkpoint source hash mismatch: {checkpoint}")
            print(f"reused {index}/{len(required)} {name}", flush=True)
            continue
        stage = Usd.Stage.Open(str(stage_path), load=Usd.Stage.LoadAll)
        if stage is None:
            raise RuntimeError(f"could not open source stage: {stage_path}")
        result = scan_stage(stage)
        if result.get("contract_version") != 2:
            raise RuntimeError(f"doorway scan returned stale contract for {name}")
        result["source_scene"] = name
        result["source_stage"] = str(stage_path.resolve())
        result["source_stage_sha256"] = stage_hash
        atomic_json(checkpoint, result)
        print(
            f"scanned {index}/{len(required)} {name} "
            f"doors={result['doorway_count']} failed={result['failed_doorway_count']} "
            f"pass={result['pass']}",
            flush=True,
        )

    if args.scan_only:
        print(json.dumps({
            "status": "PASS",
            "mode": "scan_only",
            "shard_count": args.shard_count,
            "shard_index": args.shard_index,
            "scene_count": len(required),
        }, indent=2))
        return 0
    absent = [name for name in sorted(required_all)
              if not (args.checkpoints / f"{name}.json").exists()]
    if absent:
        raise RuntimeError(f"doorway checkpoints are incomplete: {absent}")
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite completed cache: {args.out}")
    scenes = {}
    for name in sorted(required_all):
        record = base["scenes"][name]
        geometry = dict(record["geometry"])
        geometry["doorway_clearance"] = load(args.checkpoints / f"{name}.json")
        scenes[name] = {**record, "geometry": geometry}
    output = {
        **{key: value for key, value in base.items() if key != "scenes"},
        "cache_contract_version": 2,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scene_count": len(scenes),
        "complete_for_pair_cache": True,
        "doorway_clearance_contract_version": 2,
        "doorway_clearance_complete": True,
        "source_base_cache_path": str(args.base_cache.resolve()),
        "source_base_cache_sha256": sha256(args.base_cache),
        "pair_cache_paths": [str(path.resolve()) for path in args.pairs],
        "pair_cache_sha256": {str(path.resolve()): sha256(path) for path in args.pairs},
        "scenes": scenes,
    }
    atomic_json(args.out, output)
    print(json.dumps({
        "status": "PASS",
        "scene_count": len(scenes),
        "doorway_pass_count": sum(
            row["geometry"]["doorway_clearance"]["pass"] is True
            for row in scenes.values()),
        "doorway_fail_count": sum(
            row["geometry"]["doorway_clearance"]["pass"] is not True
            for row in scenes.values()),
        "out": str(args.out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
