#!/usr/bin/env python3
"""Read official scene packages and inventory their external USD object dependencies."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import tarfile

import zstandard


REFERENCE = re.compile(
    rb"(?:\.\./)+objects/(objaverse|thor)/([^/@]+)/(?:[^/@]+\.usd[ac])"
)


def geometry_members(package: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    with package.open("rb") as raw:
        with zstandard.ZstdDecompressor().stream_reader(raw) as decoded:
            with tarfile.open(fileobj=decoded, mode="r|") as archive:
                for member in archive:
                    if member.isfile() and member.name.endswith("/Payload/Geometry.usda"):
                        stream = archive.extractfile(member)
                        if stream is None:
                            raise RuntimeError(f"could not read {member.name} from {package}")
                        result[member.name] = stream.read()
    if not result:
        raise RuntimeError(f"no Geometry.usda member in {package}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-jsonl", type=Path, required=True)
    parser.add_argument("--selection", type=Path,
                        help="optional exact subset of verified packages to inventory")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    latest: dict[str, dict] = {}
    for line in args.download_jsonl.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("status") == "PASS":
                latest[row["package"]] = row
    if not latest:
        raise RuntimeError("no verified scene packages in download log")
    if args.selection:
        selected = json.loads(args.selection.read_text(encoding="utf-8"))["selected"]
        wanted = {row["package"] for row in selected}
        if len(wanted) != len(selected):
            raise RuntimeError("selection has duplicate packages")
        missing = sorted(wanted.difference(latest))
        if missing:
            raise RuntimeError(f"selected packages lack PASS records: {missing}")
        latest = {package: latest[package] for package in wanted}

    asset_to_scenes: dict[tuple[str, str], set[str]] = defaultdict(set)
    scene_rows = []
    for index, row in enumerate(sorted(latest.values(), key=lambda item: item["scene"]), 1):
        package = Path(row["package_path"])
        if not package.is_file() or package.stat().st_size != int(row["size"]):
            raise RuntimeError(f"verified scene package is absent or changed: {package}")
        members = geometry_members(package)
        assets: set[tuple[str, str]] = set()
        for payload in members.values():
            assets.update(
                (source.decode("ascii"), directory.decode("utf-8"))
                for source, directory in REFERENCE.findall(payload)
            )
        for asset in assets:
            asset_to_scenes[asset].add(row["scene"])
        counts = Counter(source for source, _ in assets)
        scene_rows.append({
            "scene": row["scene"],
            "scene_package": row["package"],
            "geometry_member_count": len(members),
            "object_dependency_count": len(assets),
            "object_dependency_count_by_source": dict(sorted(counts.items())),
            "object_dependencies": [
                {"source": source, "directory": directory}
                for source, directory in sorted(assets)
            ],
        })
        print(json.dumps({"done": index, "total": len(latest), "scene": row["scene"],
                          "dependencies": len(assets)}), flush=True)

    dependencies = [
        {"source": source, "directory": directory, "scenes": sorted(scenes)}
        for (source, directory), scenes in sorted(asset_to_scenes.items())
    ]
    totals = Counter(row["source"] for row in dependencies)
    payload = {
        "schema": "Factory10OfficialSceneObjectDependenciesV1",
        "scene_package_count": len(latest),
        "unique_object_dependency_count": len(dependencies),
        "unique_object_dependency_count_by_source": dict(sorted(totals.items())),
        "scenes": scene_rows,
        "dependencies": dependencies,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "scene_package_count", "unique_object_dependency_count",
        "unique_object_dependency_count_by_source")}, indent=2))


if __name__ == "__main__":
    main()
