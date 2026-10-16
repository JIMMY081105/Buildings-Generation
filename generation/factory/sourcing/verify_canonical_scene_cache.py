#!/usr/bin/env python3
"""Hard-gate a complete canonical factory scene geometry/dependency cache."""

from __future__ import annotations

import argparse
from collections import Counter
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--types", type=Path, required=True)
    parser.add_argument("--expected-scene-root", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite cache audit: {args.out}")
    cache = json.loads(args.cache.read_text(encoding="utf-8"))
    pairs = json.loads(args.pairs.read_text(encoding="utf-8-sig"))
    expected_names = sorted({row[key] for row in pairs["results"]
                             for key in ("floor1", "floor2")})
    actual_names = sorted(cache.get("scenes", {}))
    errors: list[str] = []
    checks = {
        "cache_contract_version_2": cache.get("cache_contract_version") == 2,
        "complete_for_pair_cache": cache.get("complete_for_pair_cache") is True,
        "scene_count_matches_expected": cache.get("scene_count") == args.expected_count,
        "scene_names_match_pairs": actual_names == expected_names,
        "pair_sha256_matches": cache.get("pair_cache_sha256") == sha256(args.pairs),
        "type_sha256_matches": cache.get("type_cache_sha256") == sha256(args.types),
        "scene_root_matches": (
            str(Path(cache.get("scene_root", "")).resolve())
            == str(args.expected_scene_root.resolve())
        ),
    }
    errors.extend(name for name, passed in checks.items() if not passed)
    dependency_statuses: Counter[str] = Counter()
    missing_assets = 0
    noncritical_missing = 0
    critical_missing = 0
    dependency_fail_scenes: list[str] = []
    malformed_geometry: list[str] = []
    required_geometry = {
        "scene", "floor_to_floor", "rooms", "walls", "obstacles",
        "wall_boxes", "obstacle_boxes",
    }
    for name, record in sorted(cache.get("scenes", {}).items()):
        geometry = record.get("geometry", {})
        if set(geometry) != required_geometry or geometry.get("scene") != name:
            malformed_geometry.append(name)
        dependency = record.get("dependencies", {})
        dependency_statuses[str(dependency.get("status"))] += 1
        missing_assets += int(dependency.get("missing_asset_count", 0))
        noncritical_missing += int(dependency.get("noncritical_missing_dependency_count", 0))
        critical_missing += int(dependency.get("critical_missing_dependency_count", 0))
        if (dependency.get("dependency_gate_pass") is not True
                or dependency.get("critical_missing_dependency_count") != 0
                or dependency.get("all_missing_dependencies_classified") is not True):
            dependency_fail_scenes.append(name)
    if malformed_geometry:
        errors.append("malformed_geometry")
    if dependency_fail_scenes:
        errors.append("dependency_gate_failures")
    report = {
        "schema": "Factory10CanonicalSceneCacheAuditV1",
        "status": "PASS" if not errors else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cache": str(args.cache.resolve()),
        "cache_sha256": sha256(args.cache),
        "scene_count": len(actual_names),
        "expected_scene_count": args.expected_count,
        "checks": checks,
        "dependency_status_counts": dict(sorted(dependency_statuses.items())),
        "missing_asset_count_total": missing_assets,
        "noncritical_missing_dependency_count_total": noncritical_missing,
        "critical_missing_dependency_count_total": critical_missing,
        "dependency_fail_scenes": dependency_fail_scenes,
        "malformed_geometry_scenes": malformed_geometry,
        "errors": errors,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
