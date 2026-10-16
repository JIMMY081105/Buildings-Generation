#!/usr/bin/env python3
"""Run frozen G25 navigation on the exact 10-building approved input view."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path

from build_multistorey_graphs import _load_scratch
from run_g25_exact48 import exclusive_json, file_sha256, read_json, require, run_fixture, worker


EXPECTED = 10


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h1-package", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    require(args.workers > 0, "workers must be positive")
    h1_path = args.h1_package.expanduser().resolve() / "manifest.json"
    h1 = read_json(h1_path)
    approved = sorted(h1.get("approved_building_bids", []))
    require(h1.get("status") == "H1_APPROVED_10_AFTER_SQZ_A40_REVIEW"
            and len(approved) == EXPECTED and len(set(approved)) == EXPECTED,
            "H1 approved set is not exact 10")
    require(h1.get("deferred_building_count") == 0
            and h1.get("deferred_building_bids") == [],
            "H1 still has deferred candidates")
    require(h1.get("h1_placement_approved") is True
            and h1.get("stage_b_blocked_pending_h1") is False,
            "H1 does not authorize Stage B")
    require(h1.get("OBSERVATION_MANIFEST_FROZEN") is False, "T7 is frozen")

    input_root = args.input_root.expanduser().resolve()
    input_manifest_path = input_root / "G25_INPUT_MANIFEST.json"
    input_manifest = read_json(input_manifest_path)
    require(input_manifest.get("schema") == "G25InputView10V1"
            and input_manifest.get("status") == "PASS"
            and input_manifest.get("building_count") == EXPECTED,
            "G25 input manifest is not exact-10 PASS")
    require(set(input_manifest.get("approved_building_bids", [])) == set(approved)
            and input_manifest.get("deferred_building_bids") == [],
            "G25 input approved set differs")
    input_rows = {row["BID"]: row for row in input_manifest.get("rows", [])}
    require(set(input_rows) == set(approved), "G25 input rows differ")

    scratch_root = args.scratch_root.expanduser().resolve()
    require(scratch_root.is_dir(), "scratch root absent")
    for bid, row in input_rows.items():
        geometry = Path(row["geometry_link"])
        report = Path(row["isaac_report"])
        require(geometry.is_file() and file_sha256(geometry) == row["geometry_sha256"],
                f"{bid}: geometry binding differs")
        require(report.is_file() and file_sha256(report) == row["isaac_report_sha256"],
                f"{bid}: Isaac binding differs")
        scratch = scratch_root / f"{bid}.storey_bands_scratch.npz"
        binding_path = scratch.with_suffix(scratch.suffix + ".binding.json")
        require(scratch.is_file() and scratch.stat().st_size > 0, f"{bid}: scratch absent")
        binding = read_json(binding_path)
        require(binding == {
            "schema": "StoreyBandScratchBindingV1", "building": bid,
            "source_usdz": str(geometry.resolve()),
            "source_usdz_sha256": row["geometry_sha256"],
            "scratch": str(scratch.resolve()), "scratch_sha256": file_sha256(scratch),
        }, f"{bid}: scratch binding differs")
        _load_scratch(scratch)

    output_root = args.output_root.expanduser().resolve()
    require(not output_root.exists(), f"G25 output exists: {output_root}")
    output_root.mkdir(parents=True)
    results = {}
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        # The frozen exact-48 worker accepts only (bid, input_root) and invokes the
        # frozen G25 implementation, which independently rasterizes the bound final
        # USDZ at 0.05 m.  Scratch is integrity-checked above and is consumed by the
        # downstream strict-radius, PARC, collision and graph audits; passing it to
        # the frozen worker was a wrapper-only signature error.
        futures = {executor.submit(worker, bid, str(input_root)): bid
                   for bid in approved}
        done = 0
        for future in as_completed(futures):
            bid = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {"BID": bid, "report": {"schema": "G25DeliveredBuildingV1",
                          "building": bid, "status": "FAIL",
                          "exception": f"{type(error).__name__}: {error}"}}
            results[result["BID"]] = result["report"]
            done += 1
            print(f"G25 {done}/{EXPECTED} {bid} {result['report'].get('status', 'FAIL')}", flush=True)

    rows = []
    for bid in approved:
        report = results[bid]
        path = output_root / f"{bid}.json"
        exclusive_json(path, report)
        rows.append({"BID": bid, "status": report.get("status", "FAIL"),
                     "report": str(path), "report_sha256": file_sha256(path),
                     "Floor1_status": report.get("storeys", {}).get("Floor1", {}).get("status"),
                     "Floor2_status": report.get("storeys", {}).get("Floor2", {}).get("status")})
    try:
        sealed = run_fixture("sealed_closet")
    except Exception as error:
        sealed = {"fixture_expectation_met": False, "observed_status": "ERROR",
                  "exception": f"{type(error).__name__}: {error}"}
    try:
        open_door = run_fixture("open_door_control")
    except Exception as error:
        open_door = {"fixture_expectation_met": False, "observed_status": "ERROR",
                     "exception": f"{type(error).__name__}: {error}"}
    controls_pass = (sealed.get("fixture_expectation_met") is True
                     and sealed.get("observed_status") == "FAIL"
                     and open_door.get("fixture_expectation_met") is True
                     and open_door.get("observed_status") == "PASS")
    passed = sum(row["status"] == "PASS" for row in rows)
    failed = EXPECTED - passed
    status = "PASS" if passed == EXPECTED and controls_pass else "FAIL"
    summary = {
        "schema": "G25Exact10SummaryV1", "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "building_count": EXPECTED, "passed_buildings": passed,
        "failed_buildings": failed, "quality_status": status,
        "coverage_status": "PASS", "terminal_coverage_complete": True,
        "approved_building_bids": approved, "deferred_building_bids": [],
        "execution_device": "CPU",
        "rasterization_source": "FROZEN_G25_DIRECT_FINAL_USDZ_RASTER_0P05M",
        "matching_storey_band_scratch_verified": True,
        "scratch_root": str(scratch_root), "worker_count": args.workers,
        "unreachable_component_limit_enforced": True,
        "input_manifest": str(input_manifest_path),
        "input_manifest_sha256": file_sha256(input_manifest_path),
        "negative_controls": {
            "sealed_closet_expected_fail": "PASS" if (sealed.get("fixture_expectation_met") is True
                                                         and sealed.get("observed_status") == "FAIL") else "FAIL",
            "open_door_expected_pass": "PASS" if (open_door.get("fixture_expectation_met") is True
                                                     and open_door.get("observed_status") == "PASS") else "FAIL",
        },
        "OBSERVATION_MANIFEST_FROZEN": False, "rows": rows,
    }
    exclusive_json(output_root / "G25_SUMMARY.json", summary)
    print(json.dumps({"status": status, "building_count": EXPECTED,
                      "passed_buildings": passed, "failed_buildings": failed,
                      "output": str(output_root)}))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
