#!/usr/bin/env python3
"""Resolve missing USD object dependencies to exact official package ranges."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--objaverse-catalog", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    missing = [row for row in audit["rows"] if not row["present"]]
    unsupported = [row for row in missing if row["source"] != "objaverse"]
    if unsupported:
        raise RuntimeError(f"missing non-Objaverse assets require separate resolution: {unsupported[:3]}")

    catalog_rows = pq.read_table(args.objaverse_catalog).to_pylist()
    by_directory = {
        row["path"][len("objaverse_"):-len(".tar.zst")]: row
        for row in catalog_rows
        if row["path"].startswith("objaverse_") and row["path"].endswith(".tar.zst")
    }
    selected = []
    unresolved = []
    for dependency in missing:
        row = by_directory.get(dependency["directory"])
        if row is None:
            unresolved.append(dependency)
            continue
        selected.append({
            "source": "objaverse",
            "version": "20260128",
            "directory": dependency["directory"],
            "package": row["path"],
            "shard_id": int(row["shard_id"]),
            "offset": int(row["offset"]),
            "size": int(row["size"]),
            "required_by_scenes": dependency["scenes"],
        })
    if unresolved:
        raise RuntimeError(f"unresolved official object packages: {unresolved[:5]}")
    selected.sort(key=lambda row: row["directory"])
    payload = {
        "schema": "Factory10MissingOfficialObjectPackageSelectionV1",
        "policy": "exact dependencies absent from the existing official object root only",
        "audit": str(args.audit.resolve()),
        "audit_sha256": sha256(args.audit),
        "objaverse_catalog": str(args.objaverse_catalog.resolve()),
        "objaverse_catalog_sha256": sha256(args.objaverse_catalog),
        "catalog_row_count": len(catalog_rows),
        "selected_count": len(selected),
        "total_compressed_bytes": sum(row["size"] for row in selected),
        "selected": selected,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "catalog_row_count", "selected_count", "total_compressed_bytes")}, indent=2))


if __name__ == "__main__":
    main()
