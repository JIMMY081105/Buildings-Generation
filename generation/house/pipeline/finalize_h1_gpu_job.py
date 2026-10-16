#!/usr/bin/env python3
"""Attach fail-capable Slurm/renderer evidence to one Isaac validation report."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from batch_execution_contract import REQUIRED_18_GATES


ACTIVE_5090 = re.compile(
    r"\|\s*0\s*\|\s*NVIDIA GeForce RTX 5090\s*\|\s*Yes:\s*0\s*\|")

REQUIRED_ACCEPTANCE_GATES = REQUIRED_18_GATES

CRITICAL_RUNTIME_PATTERNS = (
    re.compile(r"Invalid PhysX transform detected", re.IGNORECASE),
    re.compile(
        r"needs to increase PxGpuDynamicsMemoryConfig::"
        r"foundLostAggregatePairsCapacity",
        re.IGNORECASE,
    ),
    re.compile(
        r"bounding box dimensions after transformation that exceed",
        re.IGNORECASE,
    ),
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--console-log", type=Path, required=True)
    ap.add_argument("--validation-returncode", type=int, required=True)
    ap.add_argument("--gpu-ticket", type=Path)
    args = ap.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    console = args.console_log.read_text(encoding="utf-8", errors="replace")
    if args.gpu_ticket is not None:
        raise ValueError(
            "new-batch H1 remediation must not consume an H2 GPU ticket")
    active_lines = [line for line in console.splitlines() if ACTIVE_5090.search(line)]
    critical_runtime_lines = [
        line for line in console.splitlines()
        if any(pattern.search(line) for pattern in CRITICAL_RUNTIME_PATTERNS)
    ]
    visible = report.get("renderer_device_evidence", {}).get("nvidia_smi", [])
    exactly_one_visible_5090 = (
        len(visible) == 1 and "RTX 5090" in visible[0]
        and not str(visible[0]).startswith("query failed:"))
    active_logged = bool(active_lines)
    device_gate = exactly_one_visible_5090 and active_logged

    report.setdefault("renderer_device_evidence", {}).update({
        "exactly_one_visible_rtx5090": exactly_one_visible_5090,
        "active_vulkan_device_logged": active_logged,
        "active_device_log_lines": active_lines,
        "console_log": str(args.console_log),
    })
    report["slurm_execution"] = {
        "job_id": os.environ.get("SLURM_JOB_ID"),
        "node": os.environ.get("SLURMD_NODENAME"),
        "validation_returncode": args.validation_returncode,
        "finalized_utc": datetime.now(timezone.utc).isoformat(),
    }
    report["batch_generation_authorized"] = False
    report["automatic_batch_generation_authorized"] = False
    report["human_gate_required"] = "H1_FULL_BUILDING_REVIEW"
    report["task_specific_remediation_authority"] = (
        "user prompt #20: clear machine blockers and stop at H1")
    report["review_focus"] = "VIEW03_ARRIVAL_EXPERIENCE"
    report["runtime_physics_evidence"] = {
        "critical_log_line_count": len(critical_runtime_lines),
        "critical_log_lines": critical_runtime_lines,
        "status": "pass" if not critical_runtime_lines else "fail",
    }
    gates = report.setdefault("acceptance_gates", {})
    gates["actual_rtx5090_active_device_logged"] = device_gate
    gates["validation_process_exit_zero"] = (
        args.validation_returncode == 0 and not critical_runtime_lines
    )
    present = set(gates)
    required = set(REQUIRED_ACCEPTANCE_GATES)
    missing = sorted(required - present)
    unexpected = sorted(present - required)
    report["acceptance_gate_contract"] = {
        "required_gate_count": len(REQUIRED_ACCEPTANCE_GATES),
        "present_gate_count": len(gates),
        "missing_gates": missing,
        "unexpected_gates": unexpected,
    }
    all_required_true = (
        not missing
        and not unexpected
        and all(gates.get(name) is True for name in REQUIRED_ACCEPTANCE_GATES)
    )
    report["machine_status"] = (
        "pass" if all_required_true and not report.get("error") else "fail"
    )
    if report["machine_status"] == "pass":
        report["status"] = "awaiting_human_review_H1"
    else:
        report["status"] = "fail"
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "machine_status": report["machine_status"],
        "status": report["status"],
        "exactly_one_visible_rtx5090": exactly_one_visible_5090,
        "active_vulkan_device_logged": active_logged,
        "active_device_log_lines": active_lines,
        "critical_runtime_log_line_count": len(critical_runtime_lines),
    }, indent=2))
    if report["machine_status"] != "pass":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
