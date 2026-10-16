#!/usr/bin/env python3
"""Run fail-retaining G25 plus the new A4 component rule on all ten."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-root", type=Path, required=True)
    ap.add_argument("--stage-summary", type=Path, required=True)
    ap.add_argument("--gate-script", type=Path, required=True)
    ap.add_argument("--python", type=Path, default=Path(sys.executable))
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()
    if args.output_root.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite G25 evidence")
    source = json.loads(args.stage_summary.read_text(encoding="utf-8-sig"))
    bids = [row["BID"] for row in source.get("rows", [])]
    if source.get("status") != "PASS" or len(bids) != 10 or len(set(bids)) != 10:
        raise ValueError("stage summary is not the complete ten-building PASS set")
    args.output_root.mkdir(parents=True)
    rows = []
    for index, bid in enumerate(bids, 1):
        output = args.output_root / f"{bid}.json"
        completed = subprocess.run([
            str(args.python), str(args.gate_script), "--building", bid,
            "--batch-root", str(args.batch_root),
            "--enforce-unreachable-component-limit", "--output", str(output),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (args.output_root / f"{bid}.log").write_text(
            completed.stdout, encoding="utf-8")
        if output.is_file():
            report = json.loads(output.read_text(encoding="utf-8-sig"))
            storeys = report.get("storeys", {})
            trips = sum(value.get("A4_unreachable_component_rule", {}).get(
                "oversized_unreachable_component_count", 0)
                for value in storeys.values())
            row = {
                "BID": bid,
                "status": report.get("status", "FAIL"),
                "returncode": completed.returncode,
                "Floor1_status": storeys.get("Floor1", {}).get("status"),
                "Floor2_status": storeys.get("Floor2", {}).get("status"),
                "unreachable_component_rule_trip_count": trips,
            }
        else:
            row = {"BID": bid, "status": "FAIL",
                   "returncode": completed.returncode,
                   "error": "gate wrote no JSON report"}
        rows.append(row)
        print(f"DONE {index}/10 {bid} {row['status']}", flush=True)
    passed = sum(row["status"] == "PASS" for row in rows)
    payload = {
        "schema": "BatchG25WithA4UnreachableComponentRuleV1",
        "status": "PASS" if passed == 10 else "FAIL",
        "building_count": len(rows),
        "passed_buildings": passed,
        "failed_buildings": len(rows) - passed,
        "failed_candidates_retained": True,
        "unreachable_component_rule_threshold_m2": 4.0,
        "unreachable_component_rule_trip_count": sum(
            row.get("unreachable_component_rule_trip_count", 0) for row in rows),
        "rows": rows,
    }
    args.summary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "status", "building_count", "passed_buildings", "failed_buildings",
        "unreachable_component_rule_trip_count")}, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
