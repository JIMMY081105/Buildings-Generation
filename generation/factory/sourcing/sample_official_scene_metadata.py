#!/usr/bin/env python3
"""Deterministically sample official MolmoSpaces scene packages by HTTP range.

Each package is a zstd-compressed tar member inside a large uncompressed shard.
The converter writes ``scene_metadata.json`` first, so a small byte range is
enough to recover auditable room/object counts without downloading the USD and
textures.  Every response range and failure is recorded; partial JSONL output is
resume-safe.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import re
from pathlib import Path
import tarfile
import threading
import time

import pyarrow.parquet as pq
import requests
import zstandard as zstd


BASE_URL = "https://huggingface.co/datasets/allenai/molmospaces/resolve/main/isaac/scenes"
PREFIX_SIZES = (
    128 * 1024,
    512 * 1024,
    2 * 1024 * 1024,
    8 * 1024 * 1024,
    32 * 1024 * 1024,
)
COARSE_PREFIX_SIZES = PREFIX_SIZES[:4]
_thread_local = threading.local()


def numeric_id(path: str) -> int:
    match = re.search(r"_(?:train|val|test)_(\d+)\.tar\.zst$", path)
    if not match:
        raise ValueError(f"cannot parse scene id: {path}")
    return int(match.group(1))


def session() -> requests.Session:
    value = getattr(_thread_local, "session", None)
    if value is None:
        value = requests.Session()
        value.headers.update({"User-Agent": "factory10-auditable-metadata-screen/1.0"})
        _thread_local.session = value
    return value


def extract_first_scene_metadata(payload: bytes) -> tuple[str, dict]:
    raw = zstd.ZstdDecompressor().decompressobj().decompress(payload)
    archive = tarfile.open(fileobj=io.BytesIO(raw), mode="r|")
    for member in archive:
        if member.isfile() and member.name.endswith("/scene_metadata.json"):
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError("metadata member could not be opened")
            data = stream.read()
            if len(data) != member.size:
                raise EOFError(f"metadata truncated: {len(data)}/{member.size}")
            scene_name = member.name.split("/", 1)[0]
            return scene_name, json.loads(data)
    raise LookupError("scene_metadata.json was not found in prefix")


def compact(scene_name: str, metadata: dict) -> dict:
    room_counts: dict[str, Counter[str]] = defaultdict(Counter)
    object_count = 0
    for record in metadata.get("objects", {}).values():
        if not isinstance(record, dict):
            continue
        asset_id = str(record.get("asset_id") or "")
        if not asset_id:
            continue
        room_id = record.get("room_id")
        room_name = "unassigned" if room_id is None else f"room_{room_id}_visual_0"
        room_counts[room_name][asset_id] += 1
        object_count += 1
    return {
        "scene": scene_name if scene_name.endswith("_ceiling") else scene_name + "_ceiling",
        "asset_count": object_count,
        "room_distribution": {
            room: {"asset_count": sum(counts.values()), "asset_id_counts": dict(counts)}
            for room, counts in sorted(room_counts.items())
        },
    }


def compact_geometry(scene_name: str, geometry_usda: bytes) -> dict:
    text = geometry_usda.decode("utf-8", errors="ignore")
    pattern = re.compile(
        r'^\s*def\s+Xform\s+"obja_([0-9a-f]{32})_\d+_0_0"', re.MULTILINE
    )
    counts = Counter(pattern.findall(text))
    normalized = scene_name if scene_name.endswith("_ceiling") else scene_name + "_ceiling"
    return {
        "scene": normalized,
        "asset_count": sum(counts.values()),
        "room_distribution": {
            "room_aggregate_preliminary": {
                "asset_count": sum(counts.values()),
                "asset_id_counts": dict(counts),
            }
        },
    }


def extract_prefix_record(payload: bytes, mode: str) -> tuple[str, dict]:
    raw = zstd.ZstdDecompressor().decompressobj().decompress(payload)
    archive = tarfile.open(fileobj=io.BytesIO(raw), mode="r|")
    for member in archive:
        if not member.isfile():
            continue
        if member.name.endswith("/scene_metadata.json"):
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError("metadata member could not be opened")
            data = stream.read()
            if len(data) != member.size:
                raise EOFError(f"metadata truncated: {len(data)}/{member.size}")
            scene_name = member.name.split("/", 1)[0]
            return "exact_scene_metadata", compact(scene_name, json.loads(data))
        if mode == "coarse" and member.name.endswith("/Payload/Geometry.usda"):
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError("Geometry.usda member could not be opened")
            data = stream.read()
            if len(data) != member.size:
                raise EOFError(f"Geometry.usda truncated: {len(data)}/{member.size}")
            scene_name = member.name.split("/", 1)[0]
            return "coarse_geometry_asset_roots", compact_geometry(scene_name, data)
    target = "scene metadata or geometry" if mode == "coarse" else "scene metadata"
    raise LookupError(f"{target} was not found in prefix")


def fetch(row: dict, source: str, version: str, mode: str) -> dict:
    shard_id = int(row["shard_id"])
    offset = int(row["offset"])
    package_size = int(row["size"])
    url = f"{BASE_URL}/{source}/{version}/shards/{shard_id:05d}.tar"
    errors = []
    prefix_sizes = COARSE_PREFIX_SIZES if mode == "coarse" else PREFIX_SIZES
    for prefix_size in prefix_sizes:
        requested = min(prefix_size, package_size)
        end = offset + requested - 1
        response = None
        response_payload = None
        content_range = ""
        for attempt in range(1, 4):
            started = time.monotonic()
            try:
                response = session().get(
                    url,
                    headers={"Range": f"bytes={offset}-{end}"},
                    timeout=(15, 60),
                )
                response.raise_for_status()
                expected_range = f"bytes {offset}-{end}/"
                content_range = response.headers.get("Content-Range", "")
                if response.status_code != 206 or not content_range.startswith(expected_range):
                    raise RuntimeError(
                        f"range contract failed: status={response.status_code} "
                        f"content_range={content_range!r}"
                    )
                response_payload = response.content
                break
            except Exception as exc:  # recorded and retried deterministically
                errors.append({
                    "prefix_size": requested,
                    "attempt": attempt,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                time.sleep(min(attempt, 2))
        if response_payload is None:
            continue
        try:
            extraction_kind, scene_record = extract_prefix_record(response_payload, mode)
            return {
                "ok": True,
                "source": source,
                "version": version,
                "package": row["path"],
                "scene_id": numeric_id(row["path"]),
                "shard_id": shard_id,
                "offset": offset,
                "package_size": package_size,
                "prefix_bytes": len(response_payload),
                "content_range": content_range,
                "attempt": attempt,
                "elapsed_s": round(time.monotonic() - started, 3),
                "extraction_kind": extraction_kind,
                "scene_record": scene_record,
            }
        except (EOFError, LookupError, tarfile.ReadError, zstd.ZstdError, json.JSONDecodeError) as exc:
            # A valid but short prefix is deterministic; move to the next size
            # without repeating the same range three times.
            errors.append({
                "prefix_size": requested,
                "attempt": attempt,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return {
        "ok": False,
        "source": source,
        "version": version,
        "package": row["path"],
        "scene_id": numeric_id(row["path"]),
        "shard_id": shard_id,
        "offset": offset,
        "package_size": package_size,
        "errors": errors,
    }


def stratified(rows: list[dict], count: int, exclude_below: int) -> list[dict]:
    eligible = [row for row in rows if numeric_id(row["path"]) >= exclude_below]
    eligible.sort(key=lambda row: numeric_id(row["path"]))
    if count <= 0 or count >= len(eligible):
        return eligible
    # One reproducible representative from the centre of every equal-width bin.
    positions = [min(len(eligible) - 1, int((index + 0.5) * len(eligible) / count))
                 for index in range(count)]
    return [eligible[position] for position in positions]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--exclude-below", type=int, default=0)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--mode", choices=("coarse", "metadata"), default="coarse")
    parser.add_argument("--jsonl", type=Path, required=True)
    parser.add_argument("--scene-types-out", type=Path, required=True)
    args = parser.parse_args()

    rows = pq.read_table(args.catalog).to_pylist()
    selected = stratified(rows, args.count, args.exclude_below)
    args.jsonl.parent.mkdir(parents=True, exist_ok=True)
    completed: dict[str, dict] = {}
    if args.jsonl.exists():
        for line in args.jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                completed[record["package"]] = record
    pending = [
        row for row in selected
        if row["path"] not in completed or completed[row["path"]].get("ok") is not True
    ]
    print(json.dumps({
        "source": args.source,
        "catalog_rows": len(rows),
        "selected": len(selected),
        "resumed": len(selected) - len(pending),
        "pending": len(pending),
        "workers": args.workers,
    }), flush=True)

    lock = threading.Lock()
    done = 0
    with args.jsonl.open("a", encoding="utf-8") as output:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(fetch, row, args.source, args.version, args.mode): row
                for row in pending
            }
            for future in as_completed(futures):
                record = future.result()
                with lock:
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
                    output.flush()
                    completed[record["package"]] = record
                    done += 1
                    if done % 100 == 0 or done == len(pending):
                        ok = sum(item.get("ok") is True for item in completed.values()
                                 if item["package"] in {row["path"] for row in selected})
                        print(json.dumps({"new_done": done, "selected_ok": ok}), flush=True)

    selected_records = [completed[row["path"]] for row in selected]
    failures = [record for record in selected_records if not record.get("ok")]
    if failures:
        raise RuntimeError(f"metadata sampling incomplete: {len(failures)} failures")
    scenes = {
        record["scene_record"]["scene"]: {
            "type": "unclassified_official_catalog_sample",
            "asset_count": record["scene_record"]["asset_count"],
            "room_distribution": record["scene_record"]["room_distribution"],
            "source_package": record["package"],
            "source_subset": args.source,
            "source_version": args.version,
            "extraction_kind": record["extraction_kind"],
        }
        for record in selected_records
    }
    result = {
        "contract": "factory10_official_metadata_range_sample_v1",
        "source": args.source,
        "version": args.version,
        "official_catalog": str(args.catalog),
        "official_catalog_rows": len(rows),
        "selection": {
            "method": "equal-width deterministic stratified sample by numeric scene id",
            "count": len(selected),
            "exclude_numeric_ids_below": args.exclude_below,
        },
        "extraction_mode": args.mode,
        "http_range": {
            "prefix_sizes_bytes": list(
                COARSE_PREFIX_SIZES if args.mode == "coarse" else PREFIX_SIZES
            ),
            "responses_verified_206_and_exact_start": True,
            "downloaded_bytes": sum(record["prefix_bytes"] for record in selected_records),
        },
        "scene_count": len(scenes),
        "scenes": scenes,
    }
    args.scene_types_out.parent.mkdir(parents=True, exist_ok=True)
    args.scene_types_out.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": "PASS",
        "scene_count": len(scenes),
        "downloaded_bytes": result["http_range"]["downloaded_bytes"],
    }), flush=True)


if __name__ == "__main__":
    main()
