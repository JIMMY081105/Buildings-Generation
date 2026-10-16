#!/usr/bin/env python3
"""Build the exact-54 opened input view and freshly rasterize every USDZ."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from precompute_storey_scratch48 import precompute_one


EXPECTED = 54


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def hardlink_verified(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        os.link(source, target)
    require(target.is_file(), f"hard-link target is not a file: {target}")
    require(
        target.stat().st_dev == source.stat().st_dev
        and target.stat().st_ino == source.stat().st_ino,
        f"not the expected hard link: {target}",
    )
    require(file_sha256(target) == file_sha256(source), f"hard-link differs: {target}")


def exclusive_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def progress_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{os.getpid()}.partial")
    with partial.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r3", required=True, type=Path)
    parser.add_argument("--opened-root", required=True, type=Path)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--scratch-root", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()
    require(1 <= args.workers <= 16, "workers must be in [1,16]")
    r3 = args.r3.resolve()
    opened = args.opened_root.resolve()
    input_root = args.input_root.resolve()
    scratch_root = args.scratch_root.resolve()
    summary = args.summary.resolve()
    manifest_path = input_root / "G25_INPUT_MANIFEST.json"
    require(not manifest_path.exists(), "input manifest already exists")
    require(not summary.exists(), "scratch summary already exists")

    doors_path = opened / "DOOR_OPENING_54.json"
    doors = read_json(doors_path)
    door_rows = {row["building"]: row for row in doors.get("rows", [])}
    require(
        doors.get("status") == "PASS"
        and doors.get("observed_buildings") == EXPECTED
        and len(door_rows) == EXPECTED,
        "door release is not exact-54 PASS",
    )
    bids = sorted(door_rows)
    normalized = {bid.replace("_v001_", "_") for bid in bids}
    require(len(normalized) == EXPECTED, "door release is not normalized-unique exact 54")
    input_root.mkdir(parents=True, exist_ok=True)
    scratch_root.mkdir(parents=True, exist_ok=True)
    input_rows = []
    for bid in bids:
        door = door_rows[bid]
        source_usdz = Path(door["output"])
        door_report = opened / bid / "reports" / "door_opening.json"
        if door["group"] == "A":
            source_report = r3 / "delivery50_r4" / bid / "reports" / "isaac_validation.json"
        else:
            source_report = r3 / "from_f_drive" / "isaac_reports" / f"{bid}.isaac_validation.json"
        require(
            source_usdz.is_file()
            and file_sha256(source_usdz) == door["output_sha256"],
            f"opened geometry binding differs: {bid}",
        )
        require(
            door_report.is_file()
            and read_json(door_report).get("output_sha256") == door["output_sha256"],
            f"door report binding differs: {bid}",
        )
        require(source_report.is_file(), f"missing prior stair-arrival evidence: {bid}")
        target_usdz = input_root / bid / "scene" / "building.usdz"
        target_report = input_root / bid / "reports" / "isaac_validation.json"
        target_door = input_root / bid / "reports" / "door_opening.json"
        hardlink_verified(source_usdz, target_usdz)
        hardlink_verified(source_report, target_report)
        hardlink_verified(door_report, target_door)
        input_rows.append(
            {
                "BID": bid,
                "group": door["group"],
                "geometry_link": str(target_usdz),
                "geometry_sha256": file_sha256(target_usdz),
                "isaac_report": str(target_report),
                "isaac_report_sha256": file_sha256(target_report),
                "isaac_report_usage": (
                    "Floor1 pre-stair arrival sample only; stair geometry and placement "
                    "are unchanged by scoped door Xform deactivation"
                ),
                "door_opening_report": str(target_door),
                "door_opening_report_sha256": file_sha256(target_door),
            }
        )

    started = datetime.now(timezone.utc).isoformat()
    rows: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}

    def publish() -> None:
        progress_json(
            summary.with_suffix(".progress.json"),
            {
                "schema": "OpenedStoreyBandScratchProgress54V1",
                "status": "FAIL" if failures else (
                    "PASS" if len(rows) == EXPECTED else "IN_PROGRESS"
                ),
                "expected_building_count": EXPECTED,
                "completed_building_count": len(rows),
                "failed_building_count": len(failures),
                "started_at_utc": started,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "workers": args.workers,
                "rows": [rows[bid] for bid in sorted(rows)],
                "failures": failures,
            },
        )

    publish()
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(precompute_one, bid, str(input_root), str(scratch_root)): bid
            for bid in bids
        }
        for future in as_completed(futures):
            bid = futures[future]
            try:
                rows[bid] = future.result()
                state = "PASS"
            except Exception as error:
                failures[bid] = f"{type(error).__name__}: {error}"
                state = "FAIL"
            print(
                f"scratch {len(rows) + len(failures)}/{EXPECTED} {bid} {state}",
                flush=True,
            )
            publish()
    require(not failures and len(rows) == EXPECTED, f"fresh scratch failures: {failures}")

    manifest = {
        "schema": "G25InputView54V1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "building_count": EXPECTED,
        "building_bids": bids,
        "normalized_unique_building_count": len(normalized),
        "door_opening_manifest": str(doors_path),
        "door_opening_manifest_sha256": file_sha256(doors_path),
        "all_scratch_freshly_computed_for_opened_geometry": True,
        "historical_scratch_reused": False,
        "rows": input_rows,
    }
    exclusive_json(manifest_path, manifest)
    payload = {
        "schema": "OpenedStoreyBandScratchPrecompute54V1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_building_count": EXPECTED,
        "completed_building_count": EXPECTED,
        "fresh_opened_geometry_count": EXPECTED,
        "historical_scratch_reused_count": 0,
        "input_manifest": str(manifest_path),
        "input_manifest_sha256": file_sha256(manifest_path),
        "scratch_root": str(scratch_root),
        "rows": [rows[bid] for bid in sorted(rows)],
    }
    exclusive_json(summary, payload)
    print(
        json.dumps(
            {
                "status": "PASS",
                "building_count": EXPECTED,
                "input_manifest": str(manifest_path),
                "summary": str(summary),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
