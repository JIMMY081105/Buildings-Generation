#!/usr/bin/env python3
"""Install a frozen MolmoSpaces scene inventory and record exact provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from molmospaces_resources import R2RemoteStorage, ResourceManager


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inventory(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    if doc.get("inventory_contract_version") != 1:
        raise ValueError("scene inventory contract is stale")
    if doc.get("automatic_batch_generation_authorized") is not False:
        raise ValueError("automatic batch generation must remain false")
    if doc.get("batch_generation_authorized") is not False:
        raise ValueError("batch generation must remain false")
    records = doc.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("scene inventory is empty")
    if len(records) != int(doc.get("selected_count", -1)):
        raise ValueError("scene inventory selected count mismatch")
    indices = [int(record["index"]) for record in records]
    archives = [str(record["archive"]) for record in records]
    if len(indices) != len(set(indices)):
        raise ValueError("scene inventory contains duplicate indices")
    if len(archives) != len(set(archives)):
        raise ValueError("scene inventory contains duplicate archives")
    for record in records:
        expected = "_train_{}.tar.zst".format(int(record["index"]))
        if not str(record["archive"]).endswith(expected):
            raise ValueError("scene archive/index mismatch: " + str(record))
        if float(record["compressed_mib"]) <= 0:
            raise ValueError("scene archive has invalid compressed size")
    return doc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--symlink-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    inventory = load_inventory(args.inventory)
    if args.preflight_only:
        print(json.dumps({
            "status": "PREFLIGHT_PASS",
            "selected_count": inventory["selected_count"],
            "batch_generation_authorized": False,
        }, indent=2))
        return
    if args.out.exists():
        raise FileExistsError("refusing to overwrite scene install report: " + str(args.out))

    source = inventory["source"]
    version = inventory["version"]
    manager = ResourceManager(
        remote_storage=R2RemoteStorage("isaac-thor-resources"),
        data_type_to_source_to_version={"scenes": {source: version}},
        symlink_dir=args.symlink_dir,
        cache_dir=args.cache_dir,
    )
    manager.setup()
    records = inventory["records"]
    requested_archives = [record["archive"] for record in records]
    manager.install_packages("scenes", {source: requested_archives})

    source_root = args.symlink_dir / "scenes" / source
    installed = []
    rejected = []
    for record in records:
        index = int(record["index"])
        variants = {}
        missing_by_variant = {}
        for suffix in ("", "_ceiling"):
            scene_name = "train_{}{}".format(index, suffix)
            scene_dir = source_root / scene_name
            required = {
                "scene_usda": scene_dir / "scene.usda",
                "scene_metadata": scene_dir / "scene_metadata.json",
                "geometry_usda": scene_dir / "Payload" / "Geometry.usda",
            }
            missing = [name for name, path in required.items()
                       if not path.is_file() or path.stat().st_size == 0]
            if missing:
                missing_by_variant[scene_name] = missing
                continue
            variants[scene_name] = {
                "scene_dir": str(scene_dir),
                "scene_usda_sha256": sha256(required["scene_usda"]),
                "scene_metadata_sha256": sha256(required["scene_metadata"]),
                "geometry_usda_sha256": sha256(required["geometry_usda"]),
            }
        if missing_by_variant:
            rejected.append({
                "index": index,
                "archive": record["archive"],
                "candidate_name": "train_{}_ceiling".format(index),
                "reason": "A1_INCOMPLETE_OFFICIAL_SCENE_ARCHIVE",
                "missing_by_variant": missing_by_variant,
            })
            continue
        installed.append({
            "index": index,
            "archive": record["archive"],
            "compressed_mib": float(record["compressed_mib"]),
            "variants": variants,
        })

    output = {
        "scene_install_contract_version": 1,
        "status": ("PASS_WITH_RETAINED_SOURCE_REJECTIONS"
                   if rejected else "PASS"),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "version": version,
        "inventory_path": str(args.inventory),
        "inventory_sha256": sha256(args.inventory),
        "installed_archive_count": len(installed),
        "installed_variant_count": 2 * len(installed),
        "selected_archive_count": len(records),
        "rejected_archive_count": len(rejected),
        "rejected_archives": rejected,
        "source_root": str(source_root),
        "records": installed,
        "objects_installed": False,
        "object_dependency_audit_required_before_strict_A1_A5": True,
        "automatic_batch_generation_authorized": False,
        "batch_generation_authorized": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "status", "source", "version", "installed_archive_count",
        "installed_variant_count", "objects_installed",
        "batch_generation_authorized",
    )}, indent=2))


if __name__ == "__main__":
    main()
