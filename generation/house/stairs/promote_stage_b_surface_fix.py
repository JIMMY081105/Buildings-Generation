#!/usr/bin/env python3
"""Promote one authorized Stage-B run after correcting a stale audit predicate.

The source run must have generated all six cells and each cell must have failed
only ``removed_tread_was_at_upper_floor``.  Geometry is copied byte-for-byte,
then re-audited with the current auditor and checked with the exact authored-
surface rise contract.  This is recovery of the original six generator runs,
not a claim that generation ran again.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from run_stage_b_assets import (
    digest,
    execute,
    load,
    normalize_reports,
    require,
    validate_cell,
)
from stair_rise_contract import measure_stair_rises


STALE_FALSE_CHECK = "removed_tread_was_at_upper_floor"


def remap_generation_paths(metrics_path: Path, source_raw: Path,
                           target_raw: Path) -> None:
    metrics = load(metrics_path)
    for field in ("official_exported_usd", "official_exported_metadata",
                  "deliverable_usd"):
        old = str(metrics[field]).replace("\\", "/")
        require("/raw/" in old, "generation path has no /raw/ anchor: " + field)
        relative = Path(old.split("/raw/", 1)[1])
        require(".." not in relative.parts and not relative.is_absolute(),
                "unsafe generation path: " + field)
        source = source_raw / relative
        target = target_raw / relative
        require(source.is_file() and target.is_file(),
                "generation evidence is absent after recovery copy: " + field)
        require(digest(source) == digest(target),
                "recovery copy changed generation evidence: " + field)
        metrics[field] = str(target.resolve())
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--frozen-manifest", required=True, type=Path)
    parser.add_argument("--approval", required=True, type=Path)
    parser.add_argument("--execution-plan", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--auditor", required=True, type=Path)
    parser.add_argument("--downscaler", required=True, type=Path)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    output = args.output_root.resolve()
    source_summary_path = source_root / "stage_b_summary.json"
    source_summary = load(source_summary_path)
    require(source_summary.get("status") == "FAIL", "source Stage B is not FAIL")
    require(source_summary.get("observed_generator_runs") == 6,
            "source did not complete all six authorized generator runs")
    require(source_summary.get("passed_cells") == 0
            and source_summary.get("failed_cells") == 6,
            "source is not the expected stale-audit 0/6 run")
    require(not output.exists(), "refusing to overwrite recovery output")

    frozen_path = args.frozen_manifest.resolve()
    approval_path = args.approval.resolve()
    plan_path = args.execution_plan.resolve()
    frozen, approval, plan = load(frozen_path), load(approval_path), load(plan_path)
    require(frozen.get("status") == "FROZEN_H1_AUTHORIZED_BATCH",
            "frozen manifest status mismatch")
    require(approval.get("status") == "APPROVED", "H1 is not approved")
    require(plan.get("status") == "H1_AUTHORIZED_EXECUTION_PLAN",
            "execution plan is not authorized")
    require(plan.get("frozen_manifest_sha256") == digest(frozen_path),
            "plan/frozen manifest hash mismatch")
    require(plan.get("human_decision_sha256") == digest(approval_path),
            "plan/H1 decision hash mismatch")

    planned = sorted((int(item["height_mm"]), item["kind"])
                     for item in plan["stage_B_assets"])
    require(len(planned) == 6, "Stage-B recovery plan is not six cells")
    output.mkdir(parents=True)
    env = dict(os.environ)
    env.update({"PYTHONNOUSERSITE": "1", "HF_HUB_OFFLINE": "1",
                "OMP_NUM_THREADS": "8"})
    results = []
    for height_mm, kind in planned:
        started = time.time()
        height_m = height_mm / 100.0
        cell_name = f"h{height_mm}_{kind}"
        candidates = sorted((source_root / "_failed").glob(cell_name + "_*"))
        require(len(candidates) == 1,
                f"expected one retained failure for {cell_name}, got {len(candidates)}")
        source = candidates[0]
        source_raw = source / "raw"
        failure_path = source / "STAGE_B_ASSET_FAILURE.json"
        require(failure_path.is_file(), "source failure evidence missing")
        old_audit = load(source_raw / "usd_audit.json")
        false_checks = sorted(
            name for name, passed in old_audit.get("checks", {}).items() if not passed)
        require(false_checks == [STALE_FALSE_CHECK],
                f"{cell_name} had non-stale audit failures: {false_checks}")

        partial = output / ".partial" / (cell_name + "_" + uuid.uuid4().hex)
        raw = partial / "raw"
        logs = partial / "logs"
        raw.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_raw, raw)
        source_hashes = {
            name: digest(source_raw / name)
            for name in ("staircase.usd", "traversal_path.json",
                         "generation_metrics.json")
        }
        for name, expected in source_hashes.items():
            require(digest(raw / name) == expected,
                    "recovery copy changed authored asset: " + name)
        remap_generation_paths(raw / "generation_metrics.json", source_raw, raw)
        (raw / "usd_audit.json").unlink()
        execute([
            str(args.python), str(args.auditor), str(raw / "staircase.usd"),
            "--generation-metrics", str(raw / "generation_metrics.json"),
            "--output", str(raw / "usd_audit.json"),
        ], logs / "reaudit.log", env)
        normalize_reports(raw)
        rise_contract = measure_stair_rises(raw / "staircase.usd", height_m)
        require(rise_contract.get("passes") is True,
                "recovered stair violates exact support-step contract")
        (raw / "stair_rise_contract.json").write_text(
            json.dumps(rise_contract, indent=2) + "\n", encoding="utf-8")

        src_parent, dst_parent = partial / "downscale_src", partial / "downscale_dst"
        src_parent.mkdir(); dst_parent.mkdir()
        shutil.copytree(raw, src_parent / kind)
        execute([
            str(args.python), str(args.downscaler), "--src", str(src_parent),
            "--dst", str(dst_parent), "--size", "128",
        ], logs / "downscale.log", env)
        shutil.move(str(dst_parent / kind), str(partial / "tex128"))
        shutil.rmtree(src_parent)
        dst_parent.rmdir()
        for name in ("traversal_path.json", "generation_metrics.json",
                     "usd_audit.json", "stair_rise_contract.json"):
            shutil.copy2(raw / name, partial / "tex128" / name)

        pass_doc = validate_cell(
            partial, height_m, height_mm, kind, frozen_path, approval_path, plan_path)
        pass_doc.update({
            "recovered_from_authorized_generation": True,
            "source_failed_cell": str(source),
            "source_failure_sha256": digest(failure_path),
            "source_authored_asset_sha256": source_hashes,
            "recovery_reason": "stale audit checked tread underside; walking-surface top is authoritative",
            "elapsed_seconds": round(time.time() - started, 3),
        })
        (partial / "STAGE_B_ASSET_PASS.json").write_text(
            json.dumps(pass_doc, indent=2) + "\n", encoding="utf-8")
        target = output / f"h{height_mm}" / kind
        target.parent.mkdir(parents=True, exist_ok=True)
        partial.rename(target)
        results.append({"cell": cell_name, "status": "PASS",
                        "path": str(target), **pass_doc})

    summary = {
        "status": "PASS",
        "expected_generator_runs": 6,
        "observed_generator_runs": 6,
        "new_generator_runs": 0,
        "reused_authorized_generator_runs": 6,
        "passed_cells": 6,
        "failed_cells": 0,
        "cells": results,
        "source_failed_cells_retained": True,
        "source_failed_stage_b_summary": str(source_summary_path),
        "source_failed_stage_b_summary_sha256": digest(source_summary_path),
        "frozen_manifest_sha256": digest(frozen_path),
        "human_decision_sha256": digest(approval_path),
        "execution_plan_sha256": digest(plan_path),
        "batch_generation_authorized": True,
        "automatic_batch_generation_authorized": True,
    }
    (output / "stage_b_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
