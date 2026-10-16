#!/usr/bin/env python3
"""Write an immutable Isaac verdict for one SQZ NVIDIA A40 validation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from batch_execution_contract import CAMERA_VIEWS, REQUIRED_18_GATES
from pipeline_contract import CAMERA_POSE_CONTRACT_SHA256


ACTIVE_A40 = re.compile(r"\|\s*0\s*\|\s*NVIDIA(?:\s+RTX)?\s+A40\s*\|\s*Yes:\s*0\s*\|", re.I)
A40_REQUIRED_18_GATES = tuple(
    "actual_a40_active_device_logged"
    if name == "actual_rtx5090_active_device_logged" else name
    for name in REQUIRED_18_GATES
)
CRITICAL_RUNTIME_PATTERNS = (
    re.compile(r"Invalid PhysX transform detected", re.I),
    re.compile(r"needs to increase PxGpuDynamicsMemoryConfig::foundLostAggregatePairsCapacity", re.I),
    re.compile(r"bounding box dimensions after transformation that exceed", re.I),
)


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


def require_file(path: Path, label: str) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"missing or empty {label}: {path}")
    return path


def write_new(path: Path, value: object) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--console-log", type=Path, required=True)
    parser.add_argument("--validation-returncode", type=int, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--stair-path", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report_path = require_file(args.report, "Isaac report")
    console_path = require_file(args.console_log, "Isaac console log")
    stage_path = require_file(args.stage, "Stage C building")
    build_report_path = require_file(args.build_report, "build report")
    stair_path = require_file(args.stair_path, "traversal path")
    preflight_path = require_file(args.preflight, "Stage D A40 preflight")
    report = load(report_path)
    preflight = load(preflight_path)
    records = {row["BID"]: row for row in preflight.get("buildings", [])}
    if args.building not in records:
        raise ValueError("building is absent from the A40 preflight")
    expected = records[args.building]
    input_binding = {
        "stage": str(stage_path), "stage_sha256": sha256(stage_path),
        "build_report": str(build_report_path),
        "build_report_sha256": sha256(build_report_path),
        "stair_path": str(stair_path), "stair_path_sha256": sha256(stair_path),
    }
    if input_binding != {key: expected[key] for key in input_binding}:
        raise ValueError("live A40 inputs differ from the sealed preflight")

    console = console_path.read_text(encoding="utf-8", errors="replace")
    active_lines = [line for line in console.splitlines() if ACTIVE_A40.search(line)]
    critical_lines = [line for line in console.splitlines()
                      if any(pattern.search(line) for pattern in CRITICAL_RUNTIME_PATTERNS)]
    visible = report.get("renderer_device_evidence", {}).get("nvidia_smi", [])
    exactly_one_visible_a40 = (
        len(visible) == 1 and "A40" in visible[0]
        and not str(visible[0]).startswith("query failed:"))
    active_device_logged = bool(active_lines)

    gates = dict(report.get("acceptance_gates", {}))
    gates["actual_a40_active_device_logged"] = exactly_one_visible_a40 and active_device_logged
    gates["validation_process_exit_zero"] = args.validation_returncode == 0 and not critical_lines
    missing = sorted(set(A40_REQUIRED_18_GATES) - set(gates))
    unexpected = sorted(set(gates) - set(A40_REQUIRED_18_GATES))
    failed_gates = sorted(name for name in A40_REQUIRED_18_GATES if gates.get(name) is not True)

    expected_render_names = {view + suffix for view in CAMERA_VIEWS
                             for suffix in ("_render", "_collision")}
    renders = report.get("renders", {})
    render_failures: list[str] = []
    render_evidence: dict[str, dict[str, Any]] = {}
    if set(renders) != expected_render_names:
        render_failures.append("render key set differs from the 10-frame contract")
    render_root = report_path.parent.parent / "renders"
    for name, item in renders.items():
        image = render_root / str(item.get("file", ""))
        if not image.is_file() or image.stat().st_size <= 0:
            render_failures.append(f"missing or empty render: {name}")
            continue
        render_evidence[name] = {"path": str(image.resolve()),
                                 "bytes": image.stat().st_size,
                                 "sha256": sha256(image)}
        if image.stat().st_size != item.get("bytes"):
            render_failures.append(f"render byte count differs: {name}")

    failures: list[str] = []
    if report.get("machine_status") != "pass":
        failures.append("raw validator machine_status is not pass")
    if report.get("camera_pose_contract_sha256") != CAMERA_POSE_CONTRACT_SHA256:
        failures.append("camera pose contract hash differs")
    if missing:
        failures.append("missing gates: " + ",".join(missing))
    if unexpected:
        failures.append("unexpected gates: " + ",".join(unexpected))
    if failed_gates:
        failures.append("failed gates: " + ",".join(failed_gates))
    if not exactly_one_visible_a40:
        failures.append("exactly one visible NVIDIA A40 was not proved")
    if not active_device_logged:
        failures.append("active Vulkan device 0 was not logged as NVIDIA A40")
    failures.extend(render_failures)

    payload = {
        "schema": "StageBFactory10IsaacA40VerdictV1",
        "status": "PASS" if not failures else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "building": args.building,
        "purpose": "exact-10 factory GPU spatial review plus Stage D machine evidence",
        "preflight": str(preflight_path), "preflight_sha256": sha256(preflight_path),
        "historical_h1_binding": preflight.get("historical_h1_binding"),
        "input_binding": input_binding,
        "isaac_report": str(report_path), "isaac_report_sha256": sha256(report_path),
        "isaac_console_log": str(console_path),
        "isaac_console_log_sha256": sha256(console_path),
        "raw_validator_status": report.get("status"),
        "raw_validator_machine_status": report.get("machine_status"),
        "acceptance_gate_contract": {
            "required_gate_count": len(A40_REQUIRED_18_GATES),
            "present_gate_count": len(gates), "missing_gates": missing,
            "unexpected_gates": unexpected,
        },
        "acceptance_gates": gates,
        "actual_a40_active_device_logged": exactly_one_visible_a40 and active_device_logged,
        "renderer_device_evidence": {
            "exactly_one_visible_a40": exactly_one_visible_a40,
            "active_vulkan_device_logged": active_device_logged,
            "active_device_log_lines": active_lines,
        },
        "runtime_physics_evidence": {
            "critical_log_line_count": len(critical_lines),
            "critical_log_lines": critical_lines,
            "status": "PASS" if not critical_lines else "FAIL",
        },
        "validation_returncode": args.validation_returncode,
        "render_count": len(renders), "render_evidence": render_evidence,
        "cpu_renderer_substitution_used": False,
        "raw_view03_record_preserved_without_reinterpretation": True,
        "h1_was_automatically_advanced_pending_this_gpu_review": True,
        "human_visual_approval_claimed": False,
        "execution": {"host": os.uname().nodename, "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES")},
        "blocking_failures": failures,
    }
    write_new(args.output, payload)
    print(json.dumps({"building": args.building, "status": payload["status"],
                      "failed_gates": failed_gates,
                      "blocking_failures": failures}, indent=2))
    return 0 if payload["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
