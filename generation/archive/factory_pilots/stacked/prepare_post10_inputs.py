#!/usr/bin/env python3
"""Prepare exact-10 G25 inputs and recompute all 0.05 m storey scratch."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

from build_multistorey_graphs import _load_scratch
from precompute_storey_scratch48 import precompute_one


EXPECTED = 10
FINAL_H1_STATUS = "H1_APPROVED_10_AFTER_SQZ_A40_REVIEW"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h1-package", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    parser.add_argument("--stage-d-summary", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--geometry-modified-after-stage-d", action="store_true",
        help=("record an evidence-bound repair iteration; the copied Isaac report "
              "is used only for stair arrival and final Stage D must be rerun"),
    )
    args = parser.parse_args()
    require(1 <= args.workers <= 4, "workers must be in [1,4]")
    h1_path = args.h1_package.expanduser().resolve() / "manifest.json"
    delivery = args.delivery_root.expanduser().resolve()
    stage_d_path = args.stage_d_summary.expanduser().resolve()
    input_root = args.input_root.expanduser().resolve()
    scratch_root = args.scratch_root.expanduser().resolve()
    summary_path = args.summary.expanduser().resolve()
    require(not input_root.exists() and not scratch_root.exists(),
            "refusing to overwrite input or scratch root")
    require(not summary_path.exists(), "scratch summary already exists")
    h1 = load(h1_path)
    approved = sorted(h1.get("approved_building_bids", []))
    require(h1.get("status") == FINAL_H1_STATUS
            and len(approved) == EXPECTED and len(set(approved)) == EXPECTED,
            "H1 is not final exact-10 A40 approved")
    require(h1.get("deferred_building_bids") == [], "H1 contains deferrals")
    require(h1.get("graph_token_capacity_evidence_invalidated") is True
            and h1.get("previous_batch_capacity_evidence_reused") is False,
            "capacity invalidation/reuse boundary differs")
    require(h1.get("OBSERVATION_MANIFEST_FROZEN") is False,
            "observation manifest is already frozen")
    stage_d = load(stage_d_path)
    require(stage_d.get("status") == "PASS"
            and stage_d.get("building_count") == EXPECTED
            and stage_d.get("passed_buildings") == EXPECTED
            and stage_d.get("failed_buildings") == 0,
            "Stage D is not exact-10 PASS")
    stage_rows = {row["BID"]: row for row in stage_d.get("rows", [])}
    require(set(stage_rows) == set(approved), "Stage D set differs")
    delivery_summary_path = delivery / "stage_c_summary.json"
    delivery_summary = load(delivery_summary_path)
    require(delivery_summary.get("status") == "PASS"
            and delivery_summary.get("building_count") == EXPECTED
            and delivery_summary.get("pass_count") == EXPECTED
            and delivery_summary.get("fail_count") == 0
            and {row["BID"] for row in delivery_summary.get("rows", [])} == set(approved),
            "delivery Stage C summary differs")

    input_root.mkdir(parents=True)
    scratch_root.mkdir(parents=True)
    link_or_copy(delivery_summary_path, input_root / "stage_c_summary.json")
    input_rows = []
    for bid in approved:
        source_usdz = delivery / bid / "scene" / "building.usdz"
        source_report = Path(stage_rows[bid]["isaac_report"])
        require(source_usdz.is_file() and source_usdz.stat().st_size > 0,
                f"{bid}: delivery USDZ absent")
        require(source_report.is_file()
                and sha256(source_report) == stage_rows[bid]["isaac_report_sha256"],
                f"{bid}: Stage D report binding differs")
        target_usdz = input_root / bid / "scene" / "building.usdz"
        target_report = input_root / bid / "reports" / "isaac_validation.json"
        target_usdz.parent.mkdir(parents=True)
        target_report.parent.mkdir(parents=True)
        link_or_copy(source_usdz, target_usdz)
        link_or_copy(source_report, target_report)
        require(sha256(target_usdz) == sha256(source_usdz), f"{bid}: USDZ differs")
        require(sha256(target_report) == sha256(source_report), f"{bid}: report differs")
        input_rows.append({
            "BID": bid,
            "geometry_link": str(target_usdz),
            "geometry_sha256": sha256(target_usdz),
            "isaac_report": str(target_report),
            "isaac_report_sha256": sha256(target_report),
            "stage_d_device": stage_rows[bid].get("device"),
        })
    manifest = {
        "schema": "G25InputView10V1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "building_count": EXPECTED,
        "approved_building_bids": approved,
        "deferred_building_bids": [],
        "h1_manifest": str(h1_path), "h1_manifest_sha256": sha256(h1_path),
        "stage_d_summary": str(stage_d_path), "stage_d_summary_sha256": sha256(stage_d_path),
        "delivery_stage_c_summary": str(delivery_summary_path),
        "delivery_stage_c_summary_sha256": sha256(delivery_summary_path),
        "geometry_modified": bool(args.geometry_modified_after_stage_d),
        "isaac_reports_modified": False,
        "isaac_reports_used_for_stair_arrival_only": bool(
            args.geometry_modified_after_stage_d),
        "final_stage_d_rerun_required": bool(args.geometry_modified_after_stage_d),
        "rows": input_rows,
    }
    input_manifest_path = input_root / "G25_INPUT_MANIFEST.json"
    write_new(input_manifest_path, manifest)

    rows = []
    failures = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(precompute_one, bid, str(input_root),
                                   str(scratch_root)): bid for bid in approved}
        done = 0
        for future in as_completed(futures):
            bid = futures[future]
            done += 1
            try:
                row = future.result()
                require(row.get("status") == "PASS", f"{bid}: scratch failed")
                scratch = Path(row["scratch"])
                _load_scratch(scratch)
                rows.append(row)
                print(f"scratch {done}/{EXPECTED} {bid} PASS", flush=True)
            except Exception as error:
                failures.append({"BID": bid,
                                 "error": f"{type(error).__name__}: {error}"})
                print(f"scratch {done}/{EXPECTED} {bid} FAIL {error}", flush=True)
    rows.sort(key=lambda row: row["BID"])
    payload = {
        "schema": "StoreyBandScratchPrecompute10V1",
        "status": "PASS" if len(rows) == EXPECTED and not failures else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_building_count": EXPECTED,
        "completed_building_count": len(rows),
        "failed_building_count": len(failures),
        "reused_historical_count": 0,
        "computed_current_geometry_count": len(rows),
        "voxel_size_m": 0.05,
        "floor_band_rule": "density peaks above 2 m; descend to 35 percent of peak",
        "input_manifest": str(input_manifest_path),
        "input_manifest_sha256": sha256(input_manifest_path),
        "scratch_root": str(scratch_root),
        "rows": rows, "failures": failures,
        "OBSERVATION_MANIFEST_FROZEN": False,
        "training_authorized": False,
    }
    write_new(summary_path, payload)
    print(json.dumps({"status": payload["status"],
                      "completed": len(rows), "failed": len(failures)}, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
