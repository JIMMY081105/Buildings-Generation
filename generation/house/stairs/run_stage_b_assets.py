#!/usr/bin/env python3
"""Generate the six H1-authorized staircase cells and retain every failure."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from PIL import Image

from stair_rise_contract import measure_stair_rises


STRICT_COLLIDER_GATES = (
    "all_authored_tread_colliders_normalized",
    "source_tread_collision_prims_deactivated",
    "combined_tread_collider_contains_all_boxes",
    "combined_tread_collider_is_static_closed_triangle_mesh",
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def execute(command: list[str], log: Path, env: dict[str, str]) -> None:
    started = time.time()
    completed = subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, check=False,
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(
            "command failed rc={} elapsed={:.1f}s; see {}".format(
                completed.returncode, time.time() - started, log
            )
        )


def normalize_reports(raw: Path) -> None:
    audit_path = raw / "usd_audit.json"
    metrics_path = raw / "generation_metrics.json"
    audit = load(audit_path)
    metrics = load(metrics_path)
    require(Path(audit["usd"]).resolve() == (raw / "staircase.usd").resolve(),
            "audit is bound to another USD")
    audit["usd"] = "staircase.usd"
    for field in ("official_exported_usd", "official_exported_metadata",
                  "deliverable_usd"):
        value = Path(metrics[field]).resolve()
        require(value.is_file(), "generation evidence is absent: " + field)
        metrics[field] = value.relative_to(raw.resolve()).as_posix()
    audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")


def validate_cell(root: Path, height_m: float, height_mm: int, kind: str,
                  frozen: Path, approval: Path, plan: Path) -> dict:
    raw, delivery = root / "raw", root / "tex128"
    audit = load(delivery / "usd_audit.json")
    metrics = load(delivery / "generation_metrics.json")
    traversal = load(delivery / "traversal_path.json")
    rise_contract = load(delivery / "stair_rise_contract.json")
    checks = audit.get("checks", {})
    integration = audit.get("floor_integration", {})
    require(audit.get("passed") is True, "stair USD audit is not PASS")
    require(all(checks.get(name) is True for name in STRICT_COLLIDER_GATES),
            "strict collider-normalization audit failed")
    require(audit.get("kind") == kind, "audit kind mismatch")
    require(abs(float(audit["target_height_m"]) - height_m) <= 0.001,
            "audit height mismatch")
    require(integration.get("upper_floor_is_final_landing") is True,
            "upper floor is not final landing")
    require(integration.get("geometry_regenerated_not_scaled") is True,
            "stair geometry was not regenerated")
    require(traversal.get("upper_floor_is_final_landing") is True,
            "traversal does not terminate on upper floor")
    require(metrics.get("floor_integration") == integration,
            "generation/audit floor-integration evidence differs")
    require(rise_contract.get("status") == "PASS"
            and rise_contract.get("passes") is True,
            "exact walking-surface rise contract failed")
    require(float(rise_contract.get("max_rise_m", float("inf"))) <= 0.20 + 1e-6,
            "walking-surface rise exceeds ProtoMotions support-step limit")
    textures = sorted((delivery / "assets").glob("*.png"))
    require(bool(textures), "128 px texture delivery is empty")
    for texture in textures:
        with Image.open(texture) as image:
            require(image.size == (128, 128),
                    "delivery texture is not 128 px: " + texture.name)
    return {
        "status": "PASS",
        "height_m": height_m,
        "height_mm": height_mm,
        "kind": kind,
        "generator_run_count": 1,
        "geometry_regenerated_not_scaled": True,
        "upper_floor_is_final_landing": True,
        "strict_collider_normalization_gates": {
            name: checks[name] for name in STRICT_COLLIDER_GATES
        },
        "delivery_texture_size_px": 128,
        "delivery_texture_count": len(textures),
        "delivery_texture_sha256": {
            path.name: digest(path) for path in textures
        },
        "staircase_usd_sha256": digest(delivery / "staircase.usd"),
        "traversal_path_sha256": digest(delivery / "traversal_path.json"),
        "usd_audit_sha256": digest(delivery / "usd_audit.json"),
        "stair_rise_contract_sha256": digest(
            delivery / "stair_rise_contract.json"),
        "max_walking_surface_rise_m": rise_contract["max_rise_m"],
        "max_support_step_m": rise_contract["max_support_step_m"],
        "generation_metrics_sha256": digest(
            delivery / "generation_metrics.json"),
        "raw_staircase_usd_sha256": digest(raw / "staircase.usd"),
        "frozen_manifest_sha256": digest(frozen),
        "human_decision_sha256": digest(approval),
        "execution_plan_sha256": digest(plan),
        "batch_generation_authorized": True,
        "automatic_batch_generation_authorized": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-manifest", required=True, type=Path)
    parser.add_argument("--approval", required=True, type=Path)
    parser.add_argument("--execution-plan", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--generator", required=True, type=Path)
    parser.add_argument("--auditor", required=True, type=Path)
    parser.add_argument("--downscaler", required=True, type=Path)
    args = parser.parse_args(argv)

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
            "execution plan does not bind frozen manifest")
    require(plan.get("human_decision_sha256") == digest(approval_path),
            "execution plan does not bind H1 decision")
    for document in (frozen, approval, plan):
        require(document.get("batch_generation_authorized") is True,
                "batch authorization is not true")
        require(document.get("automatic_batch_generation_authorized") is True,
                "automatic batch authorization is not true")

    expected = sorted({
        (int(round(float(item["manifest"]["height_m"]) * 100)),
         item["manifest"]["kind"])
        for item in frozen["authorized_buildings"]
    })
    planned = sorted((int(item["height_mm"]), item["kind"])
                     for item in plan["stage_B_assets"])
    require(expected == planned and len(planned) == 6,
            "Stage-B plan is not the six deduplicated (height, shape) cells")

    output = args.output_root.resolve()
    if (output / "stage_b_summary.json").exists():
        raise FileExistsError("Stage-B summary already exists: " + str(output))
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"PYTHONNOUSERSITE": "1", "HF_HUB_OFFLINE": "1",
                "OMP_NUM_THREADS": "8"})
    results = []
    for height_mm, kind in planned:
        height_m = height_mm / 100.0
        cell_name = f"h{height_mm}_{kind}"
        target = output / f"h{height_mm}" / kind
        partial = output / ".partial" / (cell_name + "_" + uuid.uuid4().hex)
        failed = output / "_failed" / (cell_name + "_" + uuid.uuid4().hex)
        if target.exists():
            raise FileExistsError("refusing to overwrite Stage-B cell: " + str(target))
        partial.mkdir(parents=True)
        started = time.time()
        try:
            raw = partial / "raw"
            raw.mkdir()
            execute([
                str(args.python), str(args.generator), "--kind", kind,
                "--height", str(height_m), "--seed", "101", "--image-res",
                "512", "--upper-floor-final-landing", "--output-dir", str(raw),
            ], partial / "logs" / "generate.log", env)
            execute([
                str(args.python), str(args.auditor), str(raw / "staircase.usd"),
                "--generation-metrics", str(raw / "generation_metrics.json"),
                "--output", str(raw / "usd_audit.json"),
            ], partial / "logs" / "audit.log", env)
            normalize_reports(raw)
            rise_contract = measure_stair_rises(
                raw / "staircase.usd", height_m)
            (raw / "stair_rise_contract.json").write_text(
                json.dumps(rise_contract, indent=2) + "\n", encoding="utf-8")
            require(rise_contract["passes"],
                    "generated stair violates exact support-step contract")

            src_parent = partial / "downscale_src"
            dst_parent = partial / "downscale_dst"
            src_parent.mkdir(); dst_parent.mkdir()
            # Use a real staging copy: Windows test hosts commonly lack the
            # symlink privilege, while the extra copy is small and makes the
            # exact downscaler input independently auditable on every host.
            shutil.copytree(raw, src_parent / kind)
            execute([
                str(args.python), str(args.downscaler), "--src", str(src_parent),
                "--dst", str(dst_parent), "--size", "128",
            ], partial / "logs" / "downscale.log", env)
            shutil.move(str(dst_parent / kind), str(partial / "tex128"))
            shutil.rmtree(src_parent)
            dst_parent.rmdir()
            for name in ("traversal_path.json", "generation_metrics.json",
                         "usd_audit.json", "stair_rise_contract.json"):
                shutil.copy2(raw / name, partial / "tex128" / name)
            pass_doc = validate_cell(
                partial, height_m, height_mm, kind, frozen_path,
                approval_path, plan_path,
            )
            pass_doc["elapsed_seconds"] = round(time.time() - started, 3)
            (partial / "STAGE_B_ASSET_PASS.json").write_text(
                json.dumps(pass_doc, indent=2) + "\n", encoding="utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            partial.rename(target)
            results.append({"cell": cell_name, "status": "PASS",
                            "path": str(target), **pass_doc})
        except Exception as exc:
            failed.parent.mkdir(parents=True, exist_ok=True)
            failure = {
                "cell": cell_name, "status": "FAIL", "error": str(exc),
                "elapsed_seconds": round(time.time() - started, 3),
                "retained_path": str(failed),
            }
            (partial / "STAGE_B_ASSET_FAILURE.json").write_text(
                json.dumps(failure, indent=2) + "\n", encoding="utf-8")
            partial.rename(failed)
            results.append(failure)

    summary = {
        "status": "PASS" if all(row["status"] == "PASS" for row in results)
        else "FAIL",
        "expected_generator_runs": 6,
        "observed_generator_runs": len(results),
        "passed_cells": sum(row["status"] == "PASS" for row in results),
        "failed_cells": sum(row["status"] == "FAIL" for row in results),
        "cells": results,
        "failed_cells_retained": True,
        "frozen_manifest_sha256": digest(frozen_path),
        "human_decision_sha256": digest(approval_path),
        "execution_plan_sha256": digest(plan_path),
        "batch_generation_authorized": True,
        "automatic_batch_generation_authorized": True,
    }
    (output / "stage_b_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
