#!/usr/bin/env python3
"""Check every scene object dependency against an installed USD object root."""

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--objects-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source_path = args.dependencies.expanduser().resolve()
    root = args.objects_root.expanduser().resolve()
    document = json.loads(source_path.read_text(encoding="utf-8"))
    rows = []
    for dependency in document["dependencies"]:
        directory = root / dependency["source"] / dependency["directory"]
        expected = directory / f"{dependency['directory']}.usda"
        present = expected.is_file() and expected.stat().st_size > 0
        rows.append({
            **dependency,
            "expected_usda": str(expected),
            "present": present,
            "size": expected.stat().st_size if present else None,
        })
    present_counts = Counter(row["source"] for row in rows if row["present"])
    missing_counts = Counter(row["source"] for row in rows if not row["present"])
    payload = {
        "schema": "Factory10InstalledObjectDependencyAuditV1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dependency_manifest": str(source_path),
        "dependency_manifest_sha256": sha256(source_path),
        "objects_root_requested": str(args.objects_root),
        "objects_root_resolved": str(root),
        "dependency_count": len(rows),
        "present_count": sum(row["present"] for row in rows),
        "missing_count": sum(not row["present"] for row in rows),
        "present_count_by_source": dict(sorted(present_counts.items())),
        "missing_count_by_source": dict(sorted(missing_counts.items())),
        "status": "PASS" if all(row["present"] for row in rows) else "FAIL",
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite audit: {args.out}")
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "status", "dependency_count", "present_count", "missing_count",
        "present_count_by_source", "missing_count_by_source")}, indent=2))


if __name__ == "__main__":
    main()
