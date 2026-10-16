#!/usr/bin/env python3
"""Run an authorized expanded-pool CPU A1--A5 workflow, never post-A5 work."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


KINDS = ("straight", "l_shaped", "u_shaped")
KNOWN_HEIGHT_MM = {270, 300, 320, 350, 360, 420}
STRICT_COLLIDER_GATES = (
    "all_authored_tread_colliders_normalized",
    "source_tread_collision_prims_deactivated",
    "combined_tread_collider_contains_all_boxes",
    "combined_tread_collider_is_static_closed_triangle_mesh",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object: " + str(path))
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def run(command: list[str], log: Path, env: dict[str, str]) -> None:
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, env=env)
    log.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError("command failed ({}); see {}: {}".format(
            completed.returncode, log, command))


def asset_paths(height_mm: int, kind: str, batch_root: Path,
                h320_root: Path) -> tuple[Path, Path, Path] | None:
    if height_mm not in KNOWN_HEIGHT_MM:
        return None
    root = (h320_root / kind if height_mm == 320
            else batch_root / ("h{}".format(height_mm)) / kind / "tex128")
    stair = root / "staircase.usd"
    traversal = root / "traversal_path.json"
    audit = root / "usd_audit.json"
    if not all(path.is_file() and path.stat().st_size > 0
               for path in (stair, traversal, audit)):
        return None
    return stair, traversal, audit


def validate_asset(height_m: float, kind: str, paths: tuple[Path, Path, Path]) -> dict:
    stair, traversal, audit_path = paths
    audit = load(audit_path)
    require(audit.get("passed") is True, "stair USD audit is not PASS")
    require(abs(float(audit.get("target_height_m")) - height_m) <= 0.001,
            "stair audit target height mismatch")
    integration = audit.get("floor_integration", {})
    require(integration.get("upper_floor_is_final_landing") is True,
            "stair audit lacks upper-floor final landing")
    require(integration.get("final_landing_support_provider") == "upper_floor",
            "stair audit final landing provider mismatch")
    checks = audit.get("checks", {})
    strict_collider_gates = {
        name: checks.get(name) is True for name in STRICT_COLLIDER_GATES
    }
    return {
        "height_m": height_m,
        "height_mm": int(round(height_m * 100)),
        "kind": kind,
        "staircase_usd": str(stair),
        "staircase_usd_sha256": sha256(stair),
        "traversal_path": str(traversal),
        "traversal_path_sha256": sha256(traversal),
        "usd_audit": str(audit_path),
        "usd_audit_sha256": sha256(audit_path),
        "strict_collider_normalization_gates": strict_collider_gates,
        "strict_collider_normalization_pass": all(strict_collider_gates.values()),
        "post_A5_build_blocked": not all(strict_collider_gates.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--scene-install", type=Path, required=True)
    parser.add_argument("--dependency-inventory", type=Path, required=True)
    parser.add_argument("--object-install", type=Path, required=True)
    parser.add_argument("--scenes-root", type=Path, required=True)
    parser.add_argument("--approved-building", type=Path, required=True)
    parser.add_argument("--scripts-root", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--stair-batch-root", type=Path, required=True)
    parser.add_argument("--stair-h320-root", type=Path, required=True)
    parser.add_argument("--target-count", type=int, default=30)
    parser.add_argument("--resume-a5-from-existing-a4", action="store_true",
                        help="validate and reuse an existing complete A1--A4 stage")
    args = parser.parse_args()
    if args.target_count < 1:
        raise SystemExit("--target-count must be positive")

    authorization = load(args.authorization)
    require(authorization.get("scope") ==
            "expanded_official_scene_pool_CPU_A1_through_A5_only",
            "authorization scope mismatch")
    for field in ("building_assembly_authorized", "isaac_validation_authorized",
                  "gpu_use_authorized", "gpu_queue_mutation_authorized",
                  "automatic_batch_generation_authorized",
                  "batch_generation_authorized"):
        require(authorization.get(field) is False,
                "authorization must keep {}=false".format(field))
    inventory = load(args.inventory)
    scene_install = load(args.scene_install)
    dependency_inventory = load(args.dependency_inventory)
    object_install = load(args.object_install)
    require(inventory.get("batch_generation_authorized") is False,
            "inventory unexpectedly authorizes a batch")
    require(scene_install.get("status") == "PASS",
            "scene installation did not pass")
    require(dependency_inventory.get("status") == "PASS",
            "dependency extraction did not pass")
    require(object_install.get("status") == "PASS",
            "official object installation did not pass")
    require(object_install.get("missing_dependency_count") == 0,
            "missing object dependency must fail A1--A5")
    require(object_install.get("replacement_record_count") == 0,
            "expanded pool must use official paths, not replacements")
    require(object_install.get("batch_generation_authorized") is False,
            "object installation unexpectedly authorizes a batch")
    require(args.scenes_root.is_dir(), "expanded scene root is absent")
    ceiling_scenes = sorted(path.name for path in args.scenes_root.iterdir()
                            if path.is_dir() and path.name.endswith("_ceiling"))
    require(len(ceiling_scenes) == scene_install["installed_archive_count"],
            "expanded scene-root count differs from frozen install")
    for name in ceiling_scenes:
        require((args.scenes_root / name / "scene.usda").is_file(),
                "expanded scene is missing scene.usda: " + name)

    required_scripts = (
        "pair_scenes.py", "pick_placement.py", "pipeline_contract.py",
        "build_scene_geometry_cache.py", "make_floorplans.py",
        "dependency_policy.py", "prepare_manifest.py", "compile_a5_batch.py",
    )
    for name in required_scripts:
        require((args.scripts_root / name).is_file(), "missing script: " + name)
    require(args.python.is_file(), "CPU USD Python is absent")

    final = args.run_root / "stage_a"
    if final.exists() and not args.resume_a5_from_existing_a4:
        raise FileExistsError("refusing to overwrite existing Stage-A output")
    if args.resume_a5_from_existing_a4:
        require(final.is_dir(), "resume requested but Stage-A output is absent")
    else:
        final.mkdir(parents=True)
    logs = final / "logs"
    a4 = final / "a4"
    if args.resume_a5_from_existing_a4:
        require(logs.is_dir() and a4.is_dir(),
                "resume requested but Stage-A A4/log directories are absent")
    else:
        logs.mkdir()
        a4.mkdir()
    env = os.environ.copy()
    env["BUILDING_SCENES_ROOT"] = str(args.scenes_root.resolve())

    pairs = final / "scene_pairs.json"
    types = final / "scene_types.json"
    if not args.resume_a5_from_existing_a4:
        run([
            str(args.python), str(args.scripts_root / "pair_scenes.py"),
            "--out", str(pairs), "--types-out", str(types),
            "--max-floor2-overhang", "0.12",
        ], logs / "A1_A2_A3_pair_scenes.log", env)
    else:
        require(pairs.is_file() and types.is_file(),
                "resume requested but A2/A3 outputs are absent")
    pair_doc, type_doc = load(pairs), load(types)
    expected_root = str(args.scenes_root.resolve())
    require(pair_doc.get("scene_root") == expected_root,
            "A3 pair cache recorded the wrong scene root")
    require(type_doc.get("scene_root") == expected_root,
            "A2 type table recorded the wrong scene root")
    rules = type_doc.get("rules", {})
    require(rules.get("minimum_type_bearing_spatial_rooms") == 2,
            "cross-room type-vote rule changed")
    require(float(pair_doc.get("max_floor2_overhang")) == 0.12,
            "Floor-2 overhang threshold changed")

    scene_cache = final / "scene_geometry_cache.json"
    if not args.resume_a5_from_existing_a4:
        run([
            str(args.python), str(args.scripts_root / "build_scene_geometry_cache.py"),
            "--pairs", str(pairs), "--types", str(types), "--out", str(scene_cache),
        ], logs / "A1_scene_geometry_cache.log", env)
    else:
        require(scene_cache.is_file(),
                "resume requested but A1 scene geometry cache is absent")
    cache = load(scene_cache)
    require(cache.get("complete_for_pair_cache") is True,
            "A1 geometry/dependency cache is partial")
    require(cache.get("scene_root") == expected_root,
            "A1 cache recorded the wrong scene root")
    for scene, record in cache["scenes"].items():
        dependencies = record["dependencies"]
        require(dependencies.get("all_missing_dependencies_classified") is True,
                "unclassified dependency in scene " + scene)

    cells: dict[tuple[float, str], int] = {}
    for record in pair_doc["results"]:
        if record.get("pair_gate_passed") and record.get("usable"):
            key = (float(record["floor1_f2f"]), record["kind"])
            cells[key] = cells.get(key, 0) + 1
    searches: list[tuple[float, str, Path]] = []
    asset_evidence = []
    missing_asset_cells = []
    for height_m, kind in sorted(cells, key=lambda cell: (cell[0], cell[1])):
        height_mm = int(round(height_m * 100))
        exact_height = abs(height_m - height_mm / 100.0) <= 0.001
        paths = (asset_paths(height_mm, kind, args.stair_batch_root,
                             args.stair_h320_root) if exact_height else None)
        if paths is None:
            missing_asset_cells.append({
                "height_m": height_m, "height_mm": height_mm, "kind": kind,
                "eligible_pair_count": cells[(height_m, kind)],
                "reason": "NO_EXISTING_EXACT_FINAL_LANDING_STAIR_ASSET",
            })
            continue
        asset_evidence.append(validate_asset(height_m, kind, paths))
        stair, traversal, _audit = paths
        output = a4 / "placement_h{}_{}_grid001.json".format(height_mm, kind)
        if not args.resume_a5_from_existing_a4:
            run([
                str(args.python), str(args.scripts_root / "pick_placement.py"),
                "--pairs", str(pairs), "--stair", str(stair),
                "--stair-path", str(traversal), "--kind", kind,
                "--grid-step", "0.01", "--max-pairs", "60",
                "--max-candidates", "60", "--max-alternatives-per-pair", "250",
                "--out", str(output),
            ], logs / ("A4_h{}_{}.log".format(height_mm, kind)), env)
        else:
            require(output.is_file() and output.stat().st_size > 0,
                    "resume requested but A4 output is absent: " + str(output))
        placement = load(output)
        require(placement.get("placement_contract_version") == 3,
                "A4 placement contract is stale")
        require(placement.get("doorway_clearance_contract_version") == 2,
                "A4 doorway-clearance contract is absent")
        require(placement.get("corridor_obstacle_source") ==
                "all_obstacle_boxes_walk_prism",
                "A4 omitted visual-only furniture")
        require(float(placement.get("corridor_m")) == 1.2,
                "A4 changed the corridor")
        require(float(placement.get("corridor_body_radius_m")) == 0.22,
                "A4 changed the body radius")
        require(float(placement.get("grid_step_m")) <= 0.01,
                "A4 grid is too coarse")
        require(placement.get("rotations_tested_deg") == [0, 90, 180, 270],
                "A4 did not test all quarter turns")
        for candidate in placement["candidates"]:
            require(candidate.get("approach_clear") is True
                    and candidate.get("egress_clear") is True,
                    "A4 candidate violated the 1.2 m corridor")
            require(candidate.get("precision_clip_gate_pass") is True,
                    "A4 candidate failed precise clipping")
            require(candidate.get("doorway_clearance_gate_pass") is True,
                    "A4 candidate failed doorway clearance")
        searches.append((height_m, kind, output))
    (final / "stair_asset_coverage.json").write_text(json.dumps({
        "status": "PASS" if not missing_asset_cells else "INCOMPLETE",
        "existing_asset_cells_searched": len(searches),
        "missing_asset_cell_count": len(missing_asset_cells),
        "asset_evidence": asset_evidence,
        "strict_collider_ready_cell_count": sum(
            record["strict_collider_normalization_pass"] for record in asset_evidence),
        "strict_collider_blocked_cell_count": sum(
            not record["strict_collider_normalization_pass"] for record in asset_evidence),
        "missing_asset_cells": missing_asset_cells,
        "automatic_batch_generation_authorized": False,
        "batch_generation_authorized": False,
    }, indent=2) + "\n", encoding="utf-8")

    package = final / "package"
    require(not package.exists(), "refusing to overwrite an existing A5 package")
    command = [
        str(args.python), str(args.scripts_root / "compile_a5_batch.py"),
        "--pairs", str(pairs), "--types", str(types),
        "--approved-building", str(args.approved_building),
        "--target-count", str(args.target_count), "--out-dir", str(package),
        "--floorplan-script", str(args.scripts_root / "make_floorplans.py"),
        "--python", str(args.python), "--scene-cache", str(scene_cache),
        "--stair-asset-coverage", str(final / "stair_asset_coverage.json"),
    ]
    for height_m, kind, output in searches:
        command.extend(["--search", str(height_m), kind, str(output)])
    run(command, logs / "A5_compile.log", env)
    manifest = load(package / "manifest.json")
    require(manifest.get("batch_generation_authorized") is False,
            "A5 manifest unexpectedly authorized a batch")
    require(manifest.get("prior_approved_building_count") == 1,
            "current accepted single building was not carried into A5")
    require(manifest.get("remaining_target_count") == args.target_count - 1,
            "A5 remaining target count is wrong")

    provenance_dir = package / "expanded_pool_provenance"
    provenance_dir.mkdir()
    for source_path, name in (
        (args.authorization, "AUTHORIZATION.json"),
        (args.inventory, "scene_inventory.json"),
        (args.scene_install, "scene_install.json"),
        (args.dependency_inventory, "dependency_inventory.json"),
        (args.object_install, "object_install.json"),
    ):
        shutil.copy2(source_path, provenance_dir / name)
    script_hashes = {name: sha256(args.scripts_root / name)
                     for name in required_scripts}
    files = []
    for path in sorted(package.rglob("*")):
        if path.is_file():
            files.append({
                "path": path.relative_to(package).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            })
    proof = {
        "package_contract_version": 1,
        "status": "PASS",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "expanded_official_scene_pool_CPU_A1_through_A5_only",
        "authorization_sha256": sha256(args.authorization),
        "source": inventory["source"],
        "source_version": inventory["version"],
        "selected_scene_count": inventory["selected_count"],
        "a5_status": manifest["status"],
        "prior_approved_building_count": manifest["prior_approved_building_count"],
        "new_review_candidate_count": manifest["new_candidate_count"],
        "dependency_eligible_candidate_count": manifest[
            "dependency_eligible_candidate_count"],
        "missing_asset_cell_count": len(missing_asset_cells),
        "strict_collider_blocked_cell_count": sum(
            not record["strict_collider_normalization_pass"]
            for record in asset_evidence),
        "strict_collider_blocked_candidate_count": manifest[
            "strict_collider_blocked_candidate_count"],
        "script_sha256": script_hashes,
        "files": files,
        "building_assembly_ran": False,
        "isaac_validation_ran": False,
        "gpu_used": False,
        "gpu_queue_mutated": False,
        "automatic_batch_generation_authorized": False,
        "batch_generation_authorized": False,
    }
    (package / "A5_EXPANDED_PACKAGE_PASS.json").write_text(
        json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    run_report = {
        "status": "PASS",
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "scene_count": len(ceiling_scenes),
        "type_counts": type_doc["counts"],
        "pair_kind_count": pair_doc["candidates"],
        "height_kind_cell_count": len(cells),
        "searched_cell_count": len(searches),
        "missing_asset_cell_count": len(missing_asset_cells),
        "strict_collider_blocked_cell_count": sum(
            not record["strict_collider_normalization_pass"]
            for record in asset_evidence),
        "strict_collider_blocked_candidate_count": manifest[
            "strict_collider_blocked_candidate_count"],
        "strict_candidate_count": manifest["strict_candidate_count"],
        "dependency_eligible_candidate_count": manifest[
            "dependency_eligible_candidate_count"],
        "a5_status": manifest["status"],
        "batch_generation_authorized": False,
    }
    (final / "RUN_REPORT.json").write_text(
        json.dumps(run_report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(run_report, indent=2))


if __name__ == "__main__":
    main()
