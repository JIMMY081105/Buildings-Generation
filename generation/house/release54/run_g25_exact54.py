#!/usr/bin/env python3
"""Run the frozen G25 implementation on the exact-54 opened input view."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path

from build_multistorey_graphs import _load_scratch
from run_g25_exact48 import exclusive_json, file_sha256, read_json, require, run_fixture, worker


EXPECTED = 54


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--scratch-root", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    require(1 <= args.workers <= 16, "workers must be in [1,16]")
    input_root = args.input_root.resolve()
    manifest_path = input_root / "G25_INPUT_MANIFEST.json"
    manifest = read_json(manifest_path)
    bids = sorted(manifest.get("building_bids", []))
    require(
        manifest.get("schema") == "G25InputView54V1"
        and manifest.get("status") == "PASS"
        and manifest.get("building_count") == EXPECTED
        and len(bids) == len(set(bids)) == EXPECTED,
        "G25 input manifest is not exact-54 PASS",
    )
    require(
        len({bid.replace("_v001_", "_") for bid in bids}) == EXPECTED,
        "G25 input is not normalized-unique exact 54",
    )
    input_rows = {row["BID"]: row for row in manifest.get("rows", [])}
    require(set(input_rows) == set(bids), "G25 input rows differ from exact set")
    scratch_root = args.scratch_root.resolve()
    require(scratch_root.is_dir(), "scratch root absent")
    for bid, row in input_rows.items():
        geometry = Path(row["geometry_link"])
        report = Path(row["isaac_report"])
        door = Path(row["door_opening_report"])
        require(
            geometry.is_file() and file_sha256(geometry) == row["geometry_sha256"],
            f"{bid}: geometry binding differs",
        )
        require(
            report.is_file() and file_sha256(report) == row["isaac_report_sha256"],
            f"{bid}: stair-arrival report binding differs",
        )
        require(
            door.is_file() and file_sha256(door) == row["door_opening_report_sha256"],
            f"{bid}: door report binding differs",
        )
        scratch = scratch_root / f"{bid}.storey_bands_scratch.npz"
        binding_path = scratch.with_suffix(scratch.suffix + ".binding.json")
        require(scratch.is_file() and scratch.stat().st_size > 0, f"{bid}: scratch absent")
        binding = read_json(binding_path)
        require(
            binding
            == {
                "schema": "StoreyBandScratchBindingV1",
                "building": bid,
                "source_usdz": str(geometry.resolve()),
                "source_usdz_sha256": row["geometry_sha256"],
                "scratch": str(scratch.resolve()),
                "scratch_sha256": file_sha256(scratch),
            },
            f"{bid}: scratch binding differs",
        )
        _load_scratch(scratch)

    output_root = args.output_root.resolve()
    require(not output_root.exists(), f"G25 output exists: {output_root}")
    output_root.mkdir(parents=True)
    results = {}
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(worker, bid, str(input_root), str(scratch_root)): bid
            for bid in bids
        }
        done = 0
        for future in as_completed(futures):
            bid = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {
                    "BID": bid,
                    "report": {
                        "schema": "G25DeliveredBuildingV1",
                        "building": bid,
                        "status": "FAIL",
                        "exception": f"{type(error).__name__}: {error}",
                    },
                }
            results[bid] = result["report"]
            done += 1
            print(
                f"G25 {done}/{EXPECTED} {bid} {result['report'].get('status', 'FAIL')}",
                flush=True,
            )

    rows = []
    for bid in bids:
        report = results[bid]
        path = output_root / f"{bid}.json"
        exclusive_json(path, report)
        rows.append(
            {
                "BID": bid,
                "status": report.get("status", "FAIL"),
                "report": str(path),
                "report_sha256": file_sha256(path),
                "Floor1_status": report.get("storeys", {}).get("Floor1", {}).get("status"),
                "Floor2_status": report.get("storeys", {}).get("Floor2", {}).get("status"),
                "exception": report.get("exception"),
            }
        )
    try:
        sealed = run_fixture("sealed_closet")
    except Exception as error:
        sealed = {
            "fixture_expectation_met": False,
            "observed_status": "ERROR",
            "exception": f"{type(error).__name__}: {error}",
        }
    try:
        open_door = run_fixture("open_door_control")
    except Exception as error:
        open_door = {
            "fixture_expectation_met": False,
            "observed_status": "ERROR",
            "exception": f"{type(error).__name__}: {error}",
        }
    controls_pass = (
        sealed.get("fixture_expectation_met") is True
        and sealed.get("observed_status") == "FAIL"
        and open_door.get("fixture_expectation_met") is True
        and open_door.get("observed_status") == "PASS"
    )
    passed = sum(row["status"] == "PASS" for row in rows)
    failed = EXPECTED - passed
    status = "PASS" if passed == EXPECTED and controls_pass else "FAIL"
    summary = {
        "schema": "G25Exact54SummaryV1",
        "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "building_count": EXPECTED,
        "passed_buildings": passed,
        "failed_buildings": failed,
        "coverage_status": "PASS",
        "terminal_coverage_complete": True,
        "building_bids": bids,
        "execution_device": "CPU",
        "rasterization_source": "VERIFIED_FRESH_STOREY_BAND_SCRATCH",
        "scratch_root": str(scratch_root),
        "worker_count": args.workers,
        "unreachable_component_limit_enforced": True,
        "input_manifest": str(manifest_path),
        "input_manifest_sha256": file_sha256(manifest_path),
        "negative_controls": {
            "sealed_closet_expected_fail": "PASS"
            if (
                sealed.get("fixture_expectation_met") is True
                and sealed.get("observed_status") == "FAIL"
            )
            else "FAIL",
            "open_door_expected_pass": "PASS"
            if (
                open_door.get("fixture_expectation_met") is True
                and open_door.get("observed_status") == "PASS"
            )
            else "FAIL",
        },
        "rows": rows,
    }
    exclusive_json(output_root / "G25_SUMMARY.json", summary)
    print(
        json.dumps(
            {
                "status": status,
                "building_count": EXPECTED,
                "passed_buildings": passed,
                "failed_buildings": failed,
                "controls_pass": controls_pass,
                "output": str(output_root),
            }
        )
    )
    # Building failures are recorded and retained; control failure is executable failure.
    return 0 if controls_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
