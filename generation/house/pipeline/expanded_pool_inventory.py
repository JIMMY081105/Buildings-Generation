#!/usr/bin/env python3
"""Inventory a MolmoSpaces scene source without installing scene archives.

The output is an auditable, deterministic input to an expanded CPU A1--A5 run.
It intentionally does not assemble buildings, invoke Isaac, or authorize a batch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from molmospaces_resources import R2RemoteStorage, ResourceManager
from molmospaces_resources.constants import REMOTE_MANIFEST_NAME


ARCHIVE_RE = re.compile(r"_(?P<split>train|val|test)_(?P<index>[0-9]+)\.tar\.zst$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="holodeck-objaverse-train")
    parser.add_argument("--version", default="20260128")
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--symlink-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--first", type=int, default=0,
                        help="include the first N numeric archives; zero lists all")
    args = parser.parse_args()
    if args.first < 0:
        raise SystemExit("--first cannot be negative")
    if args.out.exists():
        raise FileExistsError("refusing to overwrite inventory: " + str(args.out))

    manager = ResourceManager(
        remote_storage=R2RemoteStorage("isaac-thor-resources"),
        data_type_to_source_to_version={"scenes": {args.source: args.version}},
        symlink_dir=args.symlink_dir,
        cache_dir=args.cache_dir,
    )
    manager.setup()
    source_cache = manager.cache_path("scenes", args.source)
    manifest_path = source_cache / REMOTE_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not manifest:
        raise RuntimeError("remote scene manifest is empty")

    records = []
    duplicate_indices: dict[int, list[str]] = {}
    for archive, compressed_mib in manifest.items():
        match = ARCHIVE_RE.search(archive)
        if not match:
            continue
        records.append({
            "index": int(match.group("index")),
            "split": match.group("split"),
            "archive": archive,
            "compressed_mib": float(compressed_mib),
        })
    records.sort(key=lambda row: (row["index"], row["archive"]))
    by_index: dict[int, list[str]] = {}
    for record in records:
        by_index.setdefault(record["index"], []).append(record["archive"])
    duplicate_indices = {
        index: archives for index, archives in by_index.items() if len(archives) != 1
    }
    if duplicate_indices:
        raise RuntimeError("scene archive index is not unique: " +
                           json.dumps(duplicate_indices, sort_keys=True))
    if not records:
        raise RuntimeError("no numbered scene archives matched the manifest")

    selected = records[:args.first] if args.first else records
    output = {
        "inventory_contract_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "version": args.version,
        "remote_manifest_path": str(manifest_path),
        "remote_manifest_sha256": sha256(manifest_path),
        "archive_count": len(records),
        "selected_count": len(selected),
        "selected_index_min": selected[0]["index"],
        "selected_index_max": selected[-1]["index"],
        "selected_compressed_mib": sum(row["compressed_mib"] for row in selected),
        "selection_rule": ("first_{}_numeric_indices".format(args.first)
                           if args.first else "all_numeric_indices"),
        "records": selected,
        "automatic_batch_generation_authorized": False,
        "batch_generation_authorized": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "source", "version", "archive_count", "selected_count",
        "selected_compressed_mib", "selection_rule",
        "batch_generation_authorized",
    )}, indent=2))


if __name__ == "__main__":
    main()
