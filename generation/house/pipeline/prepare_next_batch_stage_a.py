#!/usr/bin/env python3
"""Prepare fresh-authorized A1--A3 evidence and the exact stair-cell inventory.

This runner deliberately stops before A4/H1 compilation.  It accepts the new
batch authorization but never treats it as placement approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


KINDS = ("straight", "l_shaped", "u_shaped")
KNOWN_HEIGHT_MM = (270, 300, 320, 350, 360, 420)
STRICT_COLLIDER_GATES = (
    "all_authored_tread_colliders_normalized",
    "source_tread_collision_prims_deactivated",
    "combined_tread_collider_contains_all_boxes",
    "combined_tread_collider_is_static_closed_triangle_mesh",
)


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def run(command: list[str], log: Path, env: dict[str, str]) -> None:
    completed = subprocess.run(command, env=env, text=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    log.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"command exited {completed.returncode}; see {log}")


def asset_paths(height_mm: int, kind: str, batch_root: Path,
                h320_root: Path) -> tuple[Path, Path, Path]:
    root = (h320_root / kind if height_mm == 320 else
            batch_root / f"h{height_mm}" / kind / "tex128")
    paths = (root / "staircase.usd", root / "traversal_path.json",
             root / "usd_audit.json")
    if not all(path.is_file() and path.stat().st_size > 0 for path in paths):
        raise FileNotFoundError(f"missing audited stair asset for h{height_mm} {kind}")
    return paths


def audit_asset(height_mm: int, kind: str, paths: tuple[Path, Path, Path]) -> dict:
    stair, traversal, audit_path = paths
    audit = load(audit_path)
    require(audit.get("passed") is True, f"stair audit is not PASS: {audit_path}")
    require(abs(float(audit.get("target_height_m")) - height_mm / 100.0) <= .001,
            f"stair height mismatch: {audit_path}")
    integration = audit.get("floor_integration", {})
    require(integration.get("upper_floor_is_final_landing") is True,
            f"final landing is absent: {audit_path}")
    checks = audit.get("checks", {})
    gates = {name: checks.get(name) is True for name in STRICT_COLLIDER_GATES}
    return {
        "height_m": height_mm / 100.0,
        "height_mm": height_mm,
        "kind": kind,
        "staircase_usd": str(stair),
        "staircase_usd_sha256": sha256(stair),
        "traversal_path": str(traversal),
        "traversal_path_sha256": sha256(traversal),
        "usd_audit": str(audit_path),
        "usd_audit_sha256": sha256(audit_path),
        "strict_collider_normalization_gates": gates,
        "strict_collider_normalization_pass": all(gates.values()),
        "post_A5_build_blocked": not all(gates.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--stage-dir", default="stage_a_256")
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--scene-install", type=Path, required=True)
    parser.add_argument("--dependency-inventory", type=Path, required=True)
    parser.add_argument("--object-install", type=Path, required=True)
    parser.add_argument("--scenes-root", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--stair-batch-root", type=Path, required=True)
    parser.add_argument("--stair-h320-root", type=Path, required=True)
    args = parser.parse_args()

    authorization = load(args.authorization)
    require(authorization.get("batch_generation_authorized") is True,
            "fresh batch generation authorization is absent")
    require(authorization.get("automatic_batch_generation_authorized") is True,
            "fresh automatic generation authorization is absent")
    require(authorization.get("training_authorized") is False,
            "training must remain unauthorized")
    scene_install = load(args.scene_install)
    dependencies = load(args.dependency_inventory)
    objects = load(args.object_install)
    require(scene_install.get("status") in {
        "PASS", "PASS_WITH_RETAINED_SOURCE_REJECTIONS"},
        "scene installation failed")
    require(dependencies.get("status") == "PASS", "dependency inventory failed")
    require(objects.get("status") == "PASS", "object installation failed")
    require(objects.get("missing_dependency_count") == 0,
            "missing object dependencies must fail closed")
    require(objects.get("replacement_record_count") == 0,
            "unofficial dependency replacements are forbidden")
    ceiling_scenes = sorted(path for path in args.scenes_root.iterdir()
                            if path.is_dir() and path.name.endswith("_ceiling"))
    require(len(ceiling_scenes) == int(scene_install["installed_archive_count"])
            + int(scene_install.get("rejected_archive_count", 0)),
            "scene-root count differs from installed plus retained-rejected scenes")

    stage = args.run_root / args.stage_dir
    require(not stage.exists(), f"refusing to overwrite existing stage: {stage}")
    logs = stage / "logs"
    logs.mkdir(parents=True)
    (stage / "a4").mkdir()
    env = os.environ.copy()
    env["BUILDING_SCENES_ROOT"] = str(args.scenes_root.resolve())
    scripts = args.run_root / "scripts"
    pairs = stage / "scene_pairs.json"
    types = stage / "scene_types.json"
    run([str(args.python), str(scripts / "pair_scenes.py"),
         "--out", str(pairs), "--types-out", str(types),
         "--max-floor2-overhang", "0.12"],
        logs / "A1_A2_A3_pair_scenes.log", env)
    cache = stage / "scene_geometry_cache.json"
    run([str(args.python), str(scripts / "build_scene_geometry_cache.py"),
         "--pairs", str(pairs), "--types", str(types), "--out", str(cache)],
        logs / "A1_scene_geometry_cache.log", env)
    pair_doc = load(pairs)
    cache_doc = load(cache)
    require(cache_doc.get("complete_for_pair_cache") is True,
            "scene geometry cache is incomplete")

    cells = sorted({(int(round(float(row["floor1_f2f"]) * 100)), row["kind"])
                    for row in pair_doc["results"]
                    if row.get("usable") and row.get("pair_gate_passed")})
    evidence = []
    missing = []
    for height_mm, kind in cells:
        if height_mm not in KNOWN_HEIGHT_MM or kind not in KINDS:
            missing.append({"height_mm": height_mm, "height_m": height_mm / 100,
                            "kind": kind,
                            "reason": "NO_EXISTING_EXACT_FINAL_LANDING_STAIR_ASSET"})
            continue
        try:
            evidence.append(audit_asset(
                height_mm, kind,
                asset_paths(height_mm, kind, args.stair_batch_root,
                            args.stair_h320_root)))
        except (FileNotFoundError, ValueError) as error:
            missing.append({"height_mm": height_mm, "height_m": height_mm / 100,
                            "kind": kind, "reason": str(error)})
    coverage = {
        "status": "PASS" if not missing else "INCOMPLETE",
        "asset_evidence": evidence,
        "missing_asset_cells": missing,
        "missing_asset_cell_count": len(missing),
        "strict_collider_ready_cell_count": sum(
            row["strict_collider_normalization_pass"] for row in evidence),
        "strict_collider_blocked_cell_count": sum(
            not row["strict_collider_normalization_pass"] for row in evidence),
        "authorization_source": authorization.get("authorization_source"),
        "batch_generation_authorized": True,
        "automatic_batch_generation_authorized": True,
        "training_authorized": False,
        "h1_placement_approved": False,
        "stage_b_blocked_pending_h1": True,
    }
    (stage / "stair_asset_coverage.json").write_text(
        json.dumps(coverage, indent=2) + "\n", encoding="utf-8")
    report = {
        "status": coverage["status"],
        "device": "CPU",
        "gpu_used": False,
        "scene_count": len(ceiling_scenes),
        "complete_scene_count": int(scene_install["installed_archive_count"]),
        "retained_source_rejection_count": int(
            scene_install.get("rejected_archive_count", 0)),
        "retained_source_rejections": scene_install.get("rejected_archives", []),
        "pair_kind_record_count": len(pair_doc["results"]),
        "usable_pair_kind_record_count": sum(
            row.get("usable") and row.get("pair_gate_passed")
            for row in pair_doc["results"]),
        "height_kind_cell_count": len(cells),
        "searched_asset_cell_count": len(evidence),
        "missing_asset_cell_count": len(missing),
        "h1_placement_approved": False,
        "stage_b_blocked_pending_h1": True,
    }
    (stage / "A1_A3_RUN_REPORT.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if coverage["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
