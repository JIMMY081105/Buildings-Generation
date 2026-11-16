#!/usr/bin/env python3
"""Independently verify an assembled Factory delivery and its SHA256 manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = root / "MANIFEST_SHA256.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    rows = manifest.get("files_excluding_manifest", [])
    if not (
        manifest.get("status") == "PASS"
        and manifest.get("building_count") == 10
        and manifest.get("graph_node_capacity") == 2281
        and manifest.get("file_count_excluding_manifest") == len(rows)
        and manifest.get("artifact_counts") == {
            "expanded_usd_scenes": 10,
            "usdz": 10,
            "graphs": 10,
            "tokens": 10,
        }
    ):
        raise ValueError("Factory manifest header/counts differ")
    expected: dict[str, dict] = {}
    for row in rows:
        relative = str(row.get("relative_path", ""))
        pure = PurePosixPath(relative)
        if (not relative or pure.is_absolute() or ".." in pure.parts
                or relative in expected):
            raise ValueError(f"unsafe or duplicate manifest path: {relative!r}")
        expected[relative] = row
    actual = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if set(actual) != set(expected):
        raise ValueError(
            f"Factory file set differs: missing={sorted(set(expected)-set(actual))}, "
            f"unexpected={sorted(set(actual)-set(expected))}"
        )
    for relative, path in actual.items():
        row = expected[relative]
        if path.stat().st_size != int(row["bytes"]):
            raise ValueError(f"byte count differs: {relative}")
        if sha256(path) != str(row["sha256"]).lower():
            raise ValueError(f"SHA256 differs: {relative}")
    print(json.dumps({
        "status": "PASS",
        "root": str(root),
        "manifest_sha256": sha256(manifest_path),
        "verified_file_count": len(rows),
        "building_count": 10,
        "graph_node_capacity": 2281,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
