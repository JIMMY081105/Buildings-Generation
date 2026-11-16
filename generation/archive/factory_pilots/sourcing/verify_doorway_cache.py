#!/usr/bin/env python3
"""Verify provenance and completeness of the immutable A4-v2 doorway cache."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--base-cache", type=Path, required=True)
    parser.add_argument("--pairs", type=Path, action="append", required=True)
    parser.add_argument("--scene-root", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite doorway audit: {args.out}")
    cache = load(args.cache)
    pair_names: set[str] = set()
    for path in args.pairs:
        pair_names.update(
            str(row[key])
            for row in load(path).get("results", []) if row.get("usable") is True
            for key in ("floor1", "floor2")
        )
    errors: list[str] = []
    checks = {
        "cache_contract_version_2": cache.get("cache_contract_version") == 2,
        "doorway_contract_version_2": cache.get("doorway_clearance_contract_version") == 2,
        "doorway_complete": cache.get("doorway_clearance_complete") is True,
        "scene_count_matches": cache.get("scene_count") == args.expected_count,
        "scene_names_match_pairs": set(cache.get("scenes", {})) == pair_names,
        "base_cache_sha256_matches": cache.get("source_base_cache_sha256") == sha256(args.base_cache),
    }
    expected_pair_hashes = {str(path.resolve()): sha256(path) for path in args.pairs}
    checks["pair_sha256_matches"] = cache.get("pair_cache_sha256") == expected_pair_hashes
    errors.extend(key for key, passed in checks.items() if not passed)
    passed, failed = 0, 0
    bad_scenes: list[str] = []
    for name, row in sorted(cache.get("scenes", {}).items()):
        doorway = row.get("geometry", {}).get("doorway_clearance", {})
        stage = args.scene_root / name / "scene.usda"
        valid = (
            doorway.get("contract_version") == 2
            and doorway.get("source_scene") == name
            and stage.is_file()
            and doorway.get("source_stage_sha256") == sha256(stage)
            and isinstance(doorway.get("pass"), bool)
        )
        if not valid:
            bad_scenes.append(name)
        if doorway.get("pass") is True:
            passed += 1
        else:
            failed += 1
    if bad_scenes:
        errors.append("invalid_scene_doorway_records")
    report = {
        "schema": "Factory10DoorwayCacheAuditV1",
        "status": "PASS" if not errors else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cache": str(args.cache.resolve()),
        "cache_sha256": sha256(args.cache),
        "scene_count": len(cache.get("scenes", {})),
        "pair_scene_count": len(pair_names),
        "doorway_pass_count": passed,
        "doorway_fail_count": failed,
        "checks": checks,
        "bad_scenes": bad_scenes,
        "errors": errors,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
