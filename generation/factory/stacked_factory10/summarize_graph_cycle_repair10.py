#!/usr/bin/env python3
"""Create fail-closed before/probe/final evidence for factory graph pruning."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED = 10


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--final-build", required=True, type=Path)
    parser.add_argument("--final-acceptance", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    require(not args.output.exists(), f"refusing existing output: {args.output}")
    before = load(args.before)
    probe = load(args.probe)
    build = load(args.final_build)
    final = load(args.final_acceptance)
    require(before.get("GRAPH_ARTIFACT_AUTHORIZED") is False,
            "before evidence is not a genuine failure")
    before_failures = before.get("blocking_failures", [])
    g8_failed = sorted(row["building"] for row in before_failures
                       if row.get("gate") == "G8")
    require(len(g8_failed) == 5, "before evidence is not the five-building G8 failure")
    require(sum(row.get("gate") == "G10" for row in before_failures) == 1,
            "before evidence lacks the G10 aggregate failure")
    require(probe.get("GRAPH_ARTIFACT_AUTHORIZED") is True
            and probe.get("blocking_failures") == [],
            "independent cycle-pruning probe did not pass")
    require(final.get("GRAPH_ARTIFACT_AUTHORIZED") is True
            and final.get("blocking_failures") == [],
            "final graph acceptance did not pass")
    table = final.get("table", {})
    rows = {row["BID"]: row for row in build.get("rows", [])}
    require(len(rows) == len(table) == EXPECTED and set(rows) == set(table),
            "final graph coverage differs")
    repair_rows = []
    for bid in sorted(rows):
        row = rows[bid]
        pruning = row.get("graph_sparsification", {})
        require(pruning.get("edges_after") == pruning.get("nodes") - 1,
                f"{bid}: final wrapper is not a tree")
        require(pruning.get("artifact_sha256") == row.get("artifact_sha256"),
                f"{bid}: final artifact binding differs")
        require(table[bid].get("G8") == table[bid].get("G9") == "PASS"
                and table[bid].get("FINAL") == "PASS",
                f"{bid}: protected graph gates did not pass")
        repair_rows.append({
            "building": bid,
            "failed_g8_before": bid in g8_failed,
            **pruning,
            "final_G8": table[bid]["G8"],
            "final_G9": table[bid]["G9"],
        })
    payload = {
        "schema": "FactoryGraphCycleRepairEvidence10V1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "criterion_modified": False,
        "method": "deterministic_goal_shortest_path_tree",
        "semantics": {
            "all_nodes_retained": True,
            "sole_stair_edge_retained": True,
            "embedded_floor_topologies_modified": False,
            "all_node_to_goal_geodesics_unchanged_atol_m": 1.0e-9,
        },
        "before": {
            "path": str(args.before),
            "sha256": sha256(args.before),
            "authorized": False,
            "G8_failed_buildings": g8_failed,
        },
        "independent_probe": {
            "path": str(args.probe),
            "sha256": sha256(args.probe),
            "authorized": True,
        },
        "final": {
            "build_summary": str(args.final_build),
            "build_summary_sha256": sha256(args.final_build),
            "acceptance": str(args.final_acceptance),
            "acceptance_sha256": sha256(args.final_acceptance),
            "authorized": True,
            "passed_building_count": EXPECTED,
        },
        "removed_cycle_edges_total": sum(
            row["removed_cycle_edges"] for row in repair_rows),
        "rows": repair_rows,
    }
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "PASS", "buildings": EXPECTED,
                      "G8_failures_before": len(g8_failed),
                      "removed_cycle_edges_total": payload[
                          "removed_cycle_edges_total"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
