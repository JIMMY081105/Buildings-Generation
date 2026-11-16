#!/usr/bin/env python3
"""Write the fail-closed final release verdict for the exact 10-building batch."""
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


def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h1-package", type=Path, required=True)
    parser.add_argument("--stage-d", type=Path, required=True)
    parser.add_argument("--g25", type=Path, required=True)
    parser.add_argument("--graph-build", type=Path, required=True)
    parser.add_argument("--graph-acceptance", type=Path, required=True)
    parser.add_argument("--capacity", type=Path, required=True)
    parser.add_argument("--tensor-release-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chain-status", type=Path, required=True)
    args = parser.parse_args()
    h1_path = args.h1_package.expanduser().resolve() / "manifest.json"
    paths = {
        "h1": h1_path, "stage_d": args.stage_d.expanduser().resolve(),
        "g25": args.g25.expanduser().resolve(),
        "graph_build": args.graph_build.expanduser().resolve(),
        "graph_acceptance": args.graph_acceptance.expanduser().resolve(),
        "capacity": args.capacity.expanduser().resolve(),
    }
    for name, path in paths.items():
        require(path.is_file() and path.stat().st_size > 0, f"missing {name}: {path}")
    h1 = load(paths["h1"]); stage_d = load(paths["stage_d"]); g25 = load(paths["g25"])
    graph_build = load(paths["graph_build"]); graph = load(paths["graph_acceptance"])
    capacity = load(paths["capacity"])
    require(h1.get("approved_building_count") == EXPECTED
            and len(set(h1.get("approved_building_bids", []))) == EXPECTED,
            "H1 coverage differs")
    require(stage_d.get("building_count") == EXPECTED
            and stage_d.get("passed_buildings") + stage_d.get("failed_buildings") == EXPECTED,
            "Stage D terminal coverage differs")
    require(g25.get("building_count") == EXPECTED
            and g25.get("passed_buildings") + g25.get("failed_buildings") == EXPECTED
            and g25.get("terminal_coverage_complete") is True,
            "G25 terminal coverage differs")
    require(graph_build.get("terminal_building_count") == EXPECTED
            and graph_build.get("built_building_count") == EXPECTED
            and graph_build.get("capacity_measured_building_count") == EXPECTED,
            "graph build terminal/capacity coverage differs")
    require(graph.get("expected_building_count") == EXPECTED
            and len(graph.get("table", {})) == EXPECTED,
            "graph acceptance coverage differs")
    require(capacity.get("status") == "PASS"
            and capacity.get("evidence_building_count") == EXPECTED,
            "capacity remeasurement is incomplete")
    tensor_dir = args.tensor_release_dir.expanduser().resolve()
    tensor_files = sorted(tensor_dir.rglob("*.pt")) if tensor_dir.exists() else []
    graph_authorized = graph.get("GRAPH_ARTIFACT_AUTHORIZED") is True
    g25_authorized = g25.get("status") == "PASS" and g25.get("passed_buildings") == EXPECTED
    stage_d_authorized = stage_d.get("status") == "PASS" and stage_d.get("passed_buildings") == EXPECTED
    prerequisites_pass = stage_d_authorized and g25_authorized and graph_authorized
    if not prerequisites_pass:
        require(not tensor_files, "tensor files exist despite failed release prerequisites")
    release_authorized = prerequisites_pass and bool(tensor_files)
    if prerequisites_pass and not tensor_files:
        reason = "all upstream gates pass but tensor release has not been produced"
    elif not stage_d_authorized:
        reason = "Stage D failed"
    elif not g25_authorized:
        reason = "frozen G25 quality gates failed"
    elif not graph_authorized:
        reason = "frozen graph quality gates failed"
    else:
        reason = "authorized"
    graph_passed = sum(row.get("FINAL") == "PASS" for row in graph.get("table", {}).values())
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema": "OneShotAcceptance10FailClosedV1",
        "status": "PASS" if release_authorized else "FAIL",
        "generated_at_utc": now,
        "execution_coverage_complete": True,
        "stage_d_terminal_building_count": EXPECTED,
        "stage_d_passed_buildings": stage_d.get("passed_buildings"),
        "stage_d_failed_buildings": stage_d.get("failed_buildings"),
        "g25_terminal_building_count": EXPECTED,
        "g25_passed_buildings": g25.get("passed_buildings"),
        "g25_failed_buildings": g25.get("failed_buildings"),
        "graph_terminal_building_count": EXPECTED,
        "graph_passed_buildings": graph_passed,
        "graph_failed_buildings": EXPECTED - graph_passed,
        "graph_blocking_failure_count": len(graph.get("blocking_failures", [])),
        "capacity_evidence_status": capacity.get("status"),
        "capacity_evidence_building_count": capacity.get("evidence_building_count"),
        "recommended_graph_node_capacity": capacity.get("remeasurement", {}).get("recommended_graph_node_capacity"),
        "graph_artifact_authorized": graph_authorized,
        "TRANSFORMER_TENSOR_RELEASE_AUTHORIZED": release_authorized,
        "tensor_file_count": len(tensor_files),
        "tensor_files_published": [str(path) for path in tensor_files],
        "reason": reason,
        "capacity_change_performed": False,
        "OBSERVATION_MANIFEST_FROZEN": False,
        "training_authorized": False,
        "evidence": {name: {"path": str(path), "sha256": sha256(path)}
                     for name, path in paths.items()},
    }
    output = args.output.expanduser().resolve()
    write_new(output, payload)
    chain = {
        "schema": "PostStageD10CPUChainFinalV1",
        "status": "COMPLETE" if release_authorized else "COMPLETE_WITH_QUALITY_FAILURES",
        "generated_at_utc": now, "coverage_complete": True,
        "capacity_remeasurement_complete": True,
        "tensor_release_authorized": release_authorized,
        "tensor_file_count": len(tensor_files),
        "OBSERVATION_MANIFEST_FROZEN": False,
        "training_authorized": False,
        "oneshot": str(output), "oneshot_sha256": sha256(output),
    }
    write_new(args.chain_status.expanduser().resolve(), chain)
    print(json.dumps({"status": chain["status"],
                      "release_authorized": release_authorized,
                      "reason": reason, "tensor_file_count": len(tensor_files)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
