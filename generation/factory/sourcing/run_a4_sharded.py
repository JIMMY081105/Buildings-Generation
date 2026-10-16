#!/usr/bin/env python3
"""Run unchanged formal A4 placement checks in parallel and losslessly merge them."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


KINDS = ("straight", "l_shaped", "u_shaped")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def split_contiguous(rows: list[dict], count: int) -> list[list[dict]]:
    count = min(count, len(rows))
    quotient, remainder = divmod(len(rows), count)
    chunks, offset = [], 0
    for index in range(count):
        size = quotient + (1 if index < remainder else 0)
        chunks.append(rows[offset:offset + size])
        offset += size
    if offset != len(rows) or any(not chunk for chunk in chunks):
        raise RuntimeError("invalid contiguous shard split")
    return chunks


def run_one(command: list[str], log: Path) -> dict:
    with log.open("wb") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
    return {"returncode": result.returncode, "command": command, "log": str(log)}


def pair_key(row: dict) -> tuple[str, str]:
    return str(row["floor1"]), str(row["floor2"])


def merge_kind(
    kind: str,
    rows: list[dict],
    shard_outputs: list[Path],
    final_path: Path,
    pair_path: Path,
) -> dict:
    rank = {pair_key(row): index for index, row in enumerate(rows)}
    if len(rank) != len(rows):
        raise RuntimeError(f"duplicate input floor pair for kind {kind}")
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in shard_outputs]
    identity_keys = (
        "placement_contract_version", "corridor_obstacle_source",
        "doorway_clearance_contract_version", "doorway_clearance_rule",
        "stair_unrotated_size", "stair_height_m", "stair_visual_height_m",
        "height_tolerance_m", "rotations_tested_deg", "search_exhaustive",
        "corridor_m", "corridor_body_radius_m", "corridor_reserved_length_m",
        "grid_step_m", "selection_policy", "precision_clip_gate",
    )
    first = documents[0]
    for path, document in zip(shard_outputs, documents):
        if any(document.get(key) != first.get(key) for key in identity_keys):
            raise RuntimeError(f"A4 metadata mismatch in shard: {path}")
        if document.get("placement_contract_version") != 3:
            raise RuntimeError(f"stale placement contract in shard: {path}")
        if document.get("doorway_clearance_contract_version") != 2:
            raise RuntimeError(f"stale doorway contract in shard: {path}")
        if document.get("grid_step_m") != 0.01 or document.get("search_exhaustive") is not True:
            raise RuntimeError(f"non-formal A4 search settings in shard: {path}")

    doorway = [row for doc in documents for row in doc["doorway_clearance_rejections"]]
    strict = [row for doc in documents for row in doc["strict_placement_rejections"]]
    candidates = [row for doc in documents for row in doc["candidates"]]
    outcome_rows = doorway + strict + candidates
    outcome_keys = [pair_key(row) for row in outcome_rows]
    if len(outcome_keys) != len(set(outcome_keys)):
        raise RuntimeError(f"duplicate A4 outcomes while merging kind {kind}")
    if set(outcome_keys) != set(rank):
        missing = sorted(set(rank).difference(outcome_keys))
        extra = sorted(set(outcome_keys).difference(rank))
        raise RuntimeError(f"A4 outcome coverage mismatch for {kind}: missing={missing}, extra={extra}")
    if any(doc["pairs_evaluated_count"] != doc["pairs_height_and_kind_eligible"]
           for doc in documents):
        raise RuntimeError(f"incomplete shard evaluation for kind {kind}")

    doorway.sort(key=lambda row: rank[pair_key(row)])
    strict.sort(key=lambda row: rank[pair_key(row)])
    candidates.sort(key=lambda row: rank[pair_key(row)])
    precision = [row for doc in documents for row in doc["precision_clip_rejections"]]
    precision.sort(key=lambda row: rank.get(pair_key(row), len(rank)))
    precision = precision[:100]
    merged = dict(first)
    merged.update({
        "doorway_clearance_rejection_count": len(doorway),
        "doorway_clearance_rejections": doorway,
        "strict_placement_rejection_count": len(strict),
        "strict_placement_rejections": strict,
        "pairs_height_and_kind_eligible": len(rows),
        "pairs_evaluated_count": len(rows),
        "precision_clip_rejection_count": len(precision),
        "precision_clip_rejections": precision,
        "candidates": candidates,
        "parallelization": {
            "contract": "contiguous_pair_shards_merged_without_gate_changes_v1",
            "source_pair_catalog": str(pair_path.resolve()),
            "source_pair_catalog_sha256": sha256(pair_path),
            "shard_count": len(shard_outputs),
            "shard_outputs": [
                {"path": str(path.resolve()), "sha256": sha256(path)}
                for path in shard_outputs
            ],
            "outcome_coverage_exact": True,
            "original_pair_order_restored": True,
        },
    })
    atomic_json(final_path, merged)
    return {
        "kind": kind,
        "eligible": len(rows),
        "evaluated": len(rows),
        "door_rejected": len(doorway),
        "placement_rejected": len(strict),
        "candidates": len(candidates),
        "grid_step_m": merged["grid_step_m"],
        "search_exhaustive": merged["search_exhaustive"],
        "output": str(final_path),
        "output_sha256": sha256(final_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--height-mm", type=int, choices=(320, 350, 360), required=True)
    parser.add_argument("--shards-per-kind", type=int, default=12)
    parser.add_argument("--max-workers", type=int, default=0,
                        help="maximum concurrent picker processes; zero launches every shard")
    parser.add_argument("--resume", action="store_true",
                        help="reuse valid completed shard outputs and run only missing shards")
    parser.add_argument("--attempt-label", default="initial")
    parser.add_argument("--root", type=Path,
                        default=Path("/cephfs/shared/yixing/outputs/factory10"))
    parser.add_argument("--picker", type=Path, required=True)
    args = parser.parse_args()
    if args.shards_per_kind < 1:
        raise SystemExit("--shards-per-kind must be positive")
    root, height = args.root, args.height_mm
    pair_path = root / "work" / "pair_catalog_combined184_strict" / f"pairs_h{height}.json"
    cache = root / "work" / "official_scenes" / "cache_combined184_strict_doorway_v2.json"
    final_dir = root / "work" / "a4_combined184_exact" / "a4"
    shard_root = root / "work" / "a4_combined184_exact" / "shards" / f"h{height}"
    log_root = root / "logs" / "a4_combined184_exact" / f"h{height}_shards"
    suffix = "" if args.attempt_label == "initial" else f"_{args.attempt_label}"
    run_report = root / "evidence" / f"A4_EXACT_H{height}_SHARDED_RUN{suffix}.json"
    if args.max_workers < 0:
        raise SystemExit("--max-workers cannot be negative")
    if run_report.exists():
        raise FileExistsError(f"refusing to overwrite A4 run report: {run_report}")
    if not args.resume and (shard_root.exists() or log_root.exists()):
        raise FileExistsError(f"refusing to overwrite prior sharded A4 run for h{height}")
    if args.resume and (not shard_root.is_dir() or not log_root.is_dir()):
        raise FileNotFoundError(f"resume roots are incomplete for h{height}")
    final_dir.mkdir(parents=True, exist_ok=True)
    shard_root.mkdir(parents=True, exist_ok=args.resume)
    log_root.mkdir(parents=True, exist_ok=args.resume)

    pair_doc = json.loads(pair_path.read_text(encoding="utf-8"))
    all_rows: dict[str, list[dict]] = {}
    tasks: list[dict] = []
    reused_tasks: list[dict] = []
    expected_task_count = 0
    for kind in KINDS:
        rows = [row for row in pair_doc["results"]
                if row.get("usable") is True and row.get("kind") == kind]
        if not rows:
            raise RuntimeError(f"no eligible pair rows for {kind} h{height}")
        all_rows[kind] = rows
        final_path = final_dir / f"placement_h{height}_{kind}_grid001.json"
        if final_path.exists():
            raise FileExistsError(f"refusing to overwrite final A4 output: {final_path}")
        kind_root = shard_root / kind
        kind_root.mkdir(exist_ok=args.resume)
        for shard_index, chunk in enumerate(split_contiguous(rows, args.shards_per_kind)):
            expected_task_count += 1
            shard_doc = kind_root / f"pairs_{shard_index:02d}.json"
            shard_out = kind_root / f"placement_{shard_index:02d}.json"
            log = log_root / f"{kind}_{shard_index:02d}{suffix}.log"
            expected_shard = {
                "contract": "factory10_A4_contiguous_pair_shard_v1",
                "source_pair_catalog": str(pair_path.resolve()),
                "source_pair_catalog_sha256": sha256(pair_path),
                "height_mm": height,
                "kind": kind,
                "shard_index": shard_index,
                "shard_count": min(args.shards_per_kind, len(rows)),
                "results": chunk,
            }
            if shard_doc.exists():
                if not args.resume or json.loads(shard_doc.read_text(encoding="utf-8")) != expected_shard:
                    raise RuntimeError(f"existing shard document mismatch: {shard_doc}")
            else:
                atomic_json(shard_doc, expected_shard)
            command = [
                sys.executable, str(args.picker),
                "--pairs", str(shard_doc),
                "--scene-cache", str(cache),
                "--stair", str(root / "assets" / "stairs" / f"h{height}" / kind / "tex128" / "staircase.usd"),
                "--stair-path", str(root / "assets" / "stairs" / f"h{height}" / kind / "tex128" / "traversal_path.json"),
                "--kind", kind,
                "--out", str(shard_out),
                "--max-pairs", str(len(chunk)),
                "--max-candidates", str(len(chunk)),
                "--grid-step", "0.01",
            ]
            task = {
                "kind": kind, "index": shard_index, "rows": len(chunk),
                "pair_doc": shard_doc, "output": shard_out, "log": log,
                "command": command,
            }
            if shard_out.exists():
                if not args.resume:
                    raise FileExistsError(f"unexpected pre-existing shard output: {shard_out}")
                document = json.loads(shard_out.read_text(encoding="utf-8"))
                outcomes = (document.get("doorway_clearance_rejections", [])
                            + document.get("strict_placement_rejections", [])
                            + document.get("candidates", []))
                expected_keys = {pair_key(row) for row in chunk}
                actual_keys = [pair_key(row) for row in outcomes]
                if (document.get("placement_contract_version") != 3
                        or document.get("doorway_clearance_contract_version") != 2
                        or document.get("grid_step_m") != 0.01
                        or document.get("search_exhaustive") is not True
                        or len(actual_keys) != len(set(actual_keys))
                        or set(actual_keys) != expected_keys
                        or document.get("pairs_evaluated_count") != len(chunk)):
                    raise RuntimeError(f"invalid completed shard output: {shard_out}")
                reused_tasks.append(task)
            else:
                tasks.append(task)

    failures: list[dict] = []
    completed = 0
    worker_count = min(args.max_workers or len(tasks), len(tasks)) if tasks else 0
    with ThreadPoolExecutor(max_workers=worker_count or 1) as pool:
        futures = {pool.submit(run_one, task["command"], task["log"]): task
                   for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            result = future.result()
            completed += 1
            if result["returncode"] != 0 or not task["output"].is_file():
                failures.append({**task, **result})
            print(json.dumps({
                "completed_shards": completed, "total_shards": len(tasks),
                "kind": task["kind"], "shard": task["index"],
                "rows": task["rows"], "returncode": result["returncode"],
            }), flush=True)
    if failures:
        atomic_json(run_report, {
            "schema": "Factory10A4ShardedRunV1", "status": "FAIL",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "height_mm": height, "attempt_label": args.attempt_label,
            "resumed": args.resume, "reused_shards": len(reused_tasks),
            "scheduled_shards": len(tasks), "max_workers": worker_count,
            "failures": [{key: str(value) for key, value in row.items()
                          if key not in {"command"}} for row in failures],
        })
        raise RuntimeError(f"{len(failures)} A4 shards failed")

    summaries = []
    for kind in KINDS:
        outputs = sorted((shard_root / kind).glob("placement_*.json"))
        summaries.append(merge_kind(
            kind, all_rows[kind], outputs,
            final_dir / f"placement_h{height}_{kind}_grid001.json", pair_path,
        ))
    report = {
        "schema": "Factory10A4ShardedRunV1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "height_mm": height,
        "formal_picker": str(args.picker.resolve()),
        "formal_picker_sha256": sha256(args.picker),
        "scene_cache": str(cache.resolve()),
        "scene_cache_sha256": sha256(cache),
        "source_pair_catalog": str(pair_path.resolve()),
        "source_pair_catalog_sha256": sha256(pair_path),
        "shards_per_kind": args.shards_per_kind,
        "total_shards": expected_task_count,
        "attempt_label": args.attempt_label,
        "resumed": args.resume,
        "reused_shards": len(reused_tasks),
        "scheduled_shards": len(tasks),
        "max_workers": worker_count,
        "gate_changes": False,
        "summaries": summaries,
    }
    atomic_json(run_report, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
