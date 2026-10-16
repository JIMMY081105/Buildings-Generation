#!/usr/bin/env python3
"""Install exact official object dependencies for an expanded scene pool."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from molmospaces_resources import R2RemoteStorage, ResourceManager


OBJECT_VERSIONS = {"thor": "20260128", "objaverse": "20260128"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--symlink-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError("refusing to overwrite object install report: " + str(args.out))
    dependencies = json.loads(args.dependencies.read_text(encoding="utf-8-sig"))
    if dependencies.get("status") != "PASS" or dependencies.get(
            "dependency_inventory_contract_version") != 1:
        raise ValueError("object dependency inventory is not PASS/current")
    if dependencies.get("batch_generation_authorized") is not False:
        raise ValueError("object dependency inventory unexpectedly authorized a batch")

    manager = ResourceManager(
        remote_storage=R2RemoteStorage("isaac-thor-resources"),
        data_type_to_source_to_version={"objects": OBJECT_VERSIONS},
        symlink_dir=args.symlink_dir,
        cache_dir=args.cache_dir,
    )
    required_manager_methods = ("find_archives", "install_packages")
    missing_manager_methods = [
        name for name in required_manager_methods if not callable(getattr(manager, name, None))
    ]
    if missing_manager_methods:
        raise RuntimeError(
            "resource manager API mismatch; missing methods: "
            + ", ".join(missing_manager_methods)
        )
    if args.preflight_only:
        print(json.dumps({
            "status": "PREFLIGHT_PASS",
            "dependency_count": len(dependencies["unique_object_dependencies"]),
            "object_sources": sorted({
                item["source"] for item in dependencies["unique_object_dependencies"]
            }),
            "resource_manager_methods": list(required_manager_methods),
            "batch_generation_authorized": False,
        }, indent=2))
        return
    manager.setup()
    relative_paths: dict[str, list[Path]] = defaultdict(list)
    for item in dependencies["unique_object_dependencies"]:
        source = item["source"]
        if source not in OBJECT_VERSIONS:
            raise ValueError("unapproved object source: " + source)
        relative_paths[source].append(Path(item["relative_path"]))

    source_to_archives = {}
    for source, paths in sorted(relative_paths.items()):
        archives = manager.find_archives("objects", source, paths)
        if not archives:
            raise RuntimeError("no official archives found for object source " + source)
        source_to_archives[source] = sorted(set(archives))
    manager.install_packages("objects", source_to_archives)

    installed = []
    missing = []
    for item in dependencies["unique_object_dependencies"]:
        path = args.symlink_dir / "objects" / item["source"] / item["relative_path"]
        record = {
            "source": item["source"],
            "version": OBJECT_VERSIONS[item["source"]],
            "relative_path": item["relative_path"],
            "installed_path": str(path),
            "present": path.is_file() and path.stat().st_size > 0,
        }
        if record["present"]:
            record["bytes"] = path.stat().st_size
            record["sha256"] = sha256(path)
            installed.append(record)
        else:
            missing.append(record)
    if missing:
        raise FileNotFoundError(
            "official object installation left {} unresolved dependencies".format(len(missing)))

    output = {
        "object_install_contract_version": 1,
        "status": "PASS",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "dependency_inventory_path": str(args.dependencies),
        "dependency_inventory_sha256": sha256(args.dependencies),
        "object_versions": OBJECT_VERSIONS,
        "archive_counts": {source: len(archives)
                           for source, archives in source_to_archives.items()},
        "archives": source_to_archives,
        "installed_dependency_count": len(installed),
        "missing_dependency_count": 0,
        "installation_kind": "official_same_asset_id_and_path_not_replacement",
        "resource_manager_api": {
            "archive_lookup": "find_archives",
            "package_install": "install_packages",
        },
        "replacement_record_count": 0,
        "records": installed,
        "automatic_batch_generation_authorized": False,
        "batch_generation_authorized": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "status", "archive_counts", "installed_dependency_count",
        "missing_dependency_count", "installation_kind",
        "batch_generation_authorized",
    )}, indent=2))


if __name__ == "__main__":
    main()
