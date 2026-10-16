#!/usr/bin/env python3
"""Extract exact object dependencies from installed expanded-pool USD scenes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pxr import Sdf


OBJECT_PATH_RE = re.compile(r"(?:^|/)objects/(?P<source>thor|objaverse)/(?P<relative>.+)$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify_object_path(path: str) -> dict:
    normalized = path.replace("\\", "/")
    match = OBJECT_PATH_RE.search(normalized)
    if not match:
        raise ValueError("unclassified object dependency path: " + path)
    relative = match.group("relative")
    if not relative or relative.startswith("../") or "/../" in relative:
        raise ValueError("unsafe object dependency relative path: " + path)
    return {"source": match.group("source"), "relative_path": relative}


def layer_dependencies(root_layer_path: Path) -> dict:
    pending = [root_layer_path]
    visited: set[str] = set()
    local_layers: list[dict] = []
    object_paths: dict[tuple[str, str], dict] = {}
    unresolved_local_layers: list[str] = []
    while pending:
        requested = pending.pop()
        key = str(requested)
        if key in visited:
            continue
        visited.add(key)
        layer = Sdf.Layer.FindOrOpen(key)
        if layer is None:
            unresolved_local_layers.append(key)
            continue
        real_path = Path(layer.realPath or key)
        local_layers.append({
            "path": str(real_path),
            "sha256": sha256(real_path),
            "bytes": real_path.stat().st_size,
        })
        references = list(layer.externalReferences) + list(layer.subLayerPaths)
        for reference in references:
            absolute = layer.ComputeAbsolutePath(reference)
            normalized = absolute.replace("\\", "/")
            if "/objects/" in normalized:
                classified = classify_object_path(normalized)
                object_paths[(classified["source"], classified["relative_path"])] = {
                    **classified,
                    "resolved_path": absolute,
                    "present_before_install": Path(absolute).is_file(),
                }
            else:
                pending.append(Path(absolute))
    return {
        "local_layers": sorted(local_layers, key=lambda row: row["path"]),
        "object_dependencies": sorted(
            object_paths.values(), key=lambda row: (row["source"], row["relative_path"])),
        "unresolved_local_layers": sorted(set(unresolved_local_layers)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene-install", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        assert classify_object_path("/x/objects/thor/Chair_1/Chair.usda") == {
            "source": "thor", "relative_path": "Chair_1/Chair.usda"}
        try:
            classify_object_path("/x/objects/unapproved/Chair.usda")
        except ValueError:
            print(json.dumps({"positive_control": "PASS",
                              "negative_control": "FAIL_AS_EXPECTED"}, indent=2))
            return
        raise AssertionError("unclassified object-source negative control passed")
    if args.out.exists():
        raise FileExistsError("refusing to overwrite dependency inventory: " + str(args.out))
    install = json.loads(args.scene_install.read_text(encoding="utf-8-sig"))
    if install.get("status") not in {
            "PASS", "PASS_WITH_RETAINED_SOURCE_REJECTIONS"} or install.get(
                "scene_install_contract_version") != 1:
        raise ValueError("scene installation report is not PASS/current")
    if install.get("batch_generation_authorized") is not False:
        raise ValueError("scene installation unexpectedly authorized a batch")

    scene_records = []
    all_objects: dict[tuple[str, str], dict] = {}
    for record in install["records"]:
        scene_name = "train_{}_ceiling".format(int(record["index"]))
        scene_dir = Path(record["variants"][scene_name]["scene_dir"])
        dependencies = layer_dependencies(scene_dir / "scene.usda")
        if dependencies["unresolved_local_layers"]:
            raise FileNotFoundError(
                "scene-local dependency is missing for {}: {}".format(
                    scene_name, dependencies["unresolved_local_layers"]))
        for item in dependencies["object_dependencies"]:
            all_objects[(item["source"], item["relative_path"])] = item
        scene_records.append({
            "scene": scene_name,
            "scene_dir": str(scene_dir),
            "local_layer_count": len(dependencies["local_layers"]),
            "object_dependency_count": len(dependencies["object_dependencies"]),
            **dependencies,
        })

    if not all_objects:
        raise RuntimeError("expanded scenes unexpectedly contain no object dependencies")
    unique_objects = sorted(all_objects.values(),
                            key=lambda row: (row["source"], row["relative_path"]))
    output = {
        "dependency_inventory_contract_version": 1,
        "status": "PASS",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scene_install_path": str(args.scene_install),
        "scene_install_sha256": sha256(args.scene_install),
        "scene_count": len(scene_records),
        "unique_object_dependency_count": len(unique_objects),
        "present_before_install_count": sum(
            bool(item["present_before_install"]) for item in unique_objects),
        "missing_before_install_count": sum(
            not bool(item["present_before_install"]) for item in unique_objects),
        "object_sources": sorted({item["source"] for item in unique_objects}),
        "unique_object_dependencies": unique_objects,
        "scenes": scene_records,
        "automatic_batch_generation_authorized": False,
        "batch_generation_authorized": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "status", "scene_count", "unique_object_dependency_count",
        "present_before_install_count", "missing_before_install_count",
        "object_sources", "batch_generation_authorized",
    )}, indent=2))


if __name__ == "__main__":
    main()
