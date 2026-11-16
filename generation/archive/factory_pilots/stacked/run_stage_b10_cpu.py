#!/usr/bin/env python3
"""Fail-closed CPU-only Stage B execution for the approved 10-factory H1 set.

The parent commands are lightweight orchestration.  All USD work runs in
separate subprocesses so independent buildings can be processed concurrently
without sharing USD resolver or layer-cache state.
"""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
from typing import Any, Callable
import copy
import getpass
import re


EXPECTED = 10
STRICT_STAIR_GATES = (
    "all_authored_tread_colliders_normalized",
    "source_tread_collision_prims_deactivated",
    "combined_tread_collider_contains_all_boxes",
    "combined_tread_collider_is_static_closed_triangle_mesh",
)
CPU_GATES = (
    "G19_zero_rigid_body_api",
    "G20_all_approximation_none",
    "G21_per_floor_parity",
    "G22_collider_bounds_match_vertex_union",
    "G23_voxel_bake_clean",
    "G24_stair_reachability",
)
DEFERRED: set[str] = set()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_new_json(path: Path, payload: object) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise FileExistsError(f"stale partial evidence: {partial}")
    partial.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def kind_from_bid(bid: str) -> str:
    if "_lshaped_" in bid:
        return "l_shaped"
    if "_ushaped_" in bid:
        return "u_shaped"
    if "_straight_" in bid:
        return "straight"
    raise ValueError(f"cannot derive stair kind from {bid}")


def height_mm_from_bid(bid: str) -> int:
    return int(bid.rsplit("_h", 1)[1])


def read_review_decisions(path: Path) -> dict[str, str]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "BID" not in reader.fieldnames:
            raise ValueError("candidate_review.csv lacks BID")
        if "human_decision" not in reader.fieldnames:
            raise ValueError("candidate_review.csv lacks human_decision")
        rows = list(reader)
    decisions = {row["BID"]: row["human_decision"] for row in rows}
    if len(rows) != EXPECTED or len(decisions) != EXPECTED:
        raise ValueError("candidate_review.csv must contain 10 unique candidates")
    return decisions


def preflight(args: argparse.Namespace) -> int:
    package = args.h1_package.resolve()
    manifest_path = package / "manifest.json"
    review_path = package / "candidate_review.csv"
    decision_path = package / "H1_DECISION_RECORD.json"
    final_decisions_path = package / "candidate_decisions.h1_final.json"
    for path in (manifest_path, review_path, decision_path, final_decisions_path,
                 args.stair_coverage):
        if not path.is_file():
            raise FileNotFoundError(path)
    manifest = load(manifest_path)
    decisions = read_review_decisions(review_path)
    approved = manifest.get("stage_b_authorized_building_bids")
    deferred = set(manifest.get("deferred_building_bids", []))
    if not (
        manifest.get("h1_placement_approved") is True
        and manifest.get("stage_b_blocked_pending_h1") is False
        and manifest.get("stage_b_authorized_building_count") == EXPECTED
        and isinstance(approved, list)
        and len(approved) == EXPECTED
        and len(set(approved)) == EXPECTED
        and deferred == DEFERRED
        and manifest.get("deferred_buildings_are_failures") is False
    ):
        raise ValueError("H1 manifest is not the authorized exact-10 factory state")
    approved_set = set(approved)
    if approved_set & deferred:
        raise ValueError("approved and deferred sets overlap")
    if {bid for bid, value in decisions.items() if value == "approve"} != approved_set:
        raise ValueError("CSV approve set differs from the named Stage B set")
    if {
        bid for bid, value in decisions.items()
        if value == "deferred_no_gpu_spatial_review"
    } != deferred:
        raise ValueError("CSV deferred set differs from the named deferred set")
    if not (
        manifest.get("graph_token_capacity_evidence_invalidated") is True
        and manifest.get("previous_batch_capacity_evidence_reused") is False
        and manifest.get(
            "graph_token_capacity_remeasurement_required_for_building_count") == EXPECTED
        and manifest.get("graph_token_capacity_change_authorized_now") is False
        and manifest.get("OBSERVATION_MANIFEST_FROZEN") is False
    ):
        raise ValueError("capacity invalidation/T7 state is not the authorized state")

    coverage_doc = load(args.stair_coverage.resolve())
    coverage: dict[tuple[int, str], dict[str, Any]] = {}
    for record in coverage_doc.get("asset_evidence", []):
        key = (int(record["height_mm"]), str(record["kind"]))
        if key in coverage:
            raise ValueError(f"duplicate stair coverage cell: {key}")
        coverage[key] = record
    plan_rows = []
    live_assets: dict[tuple[int, str], dict[str, Any]] = {}
    all_package_dirs = {
        path.name for path in package.iterdir()
        if path.is_dir() and path.name.startswith("factory_")
    }
    if all_package_dirs != approved_set | deferred:
        raise ValueError("H1 candidate directories differ from the exact-10 decision set")
    for bid in approved:
        candidate_path = package / bid / "placement" / "a4_candidate.json"
        selected = load(candidate_path).get("selected")
        if not isinstance(selected, dict) or selected.get("BID") != bid:
            raise ValueError(f"invalid selected A4 record: {bid}")
        kind = kind_from_bid(bid)
        height_mm = height_mm_from_bid(bid)
        if selected.get("kind") != kind or round(
                float(selected.get("stair_height_m", -1)) * 100) != height_mm:
            raise ValueError(f"BID/A4 stair cell mismatch: {bid}")
        if not (
            selected.get("approach_clear") is True
            and selected.get("egress_clear") is True
            and selected.get("precision_clip_gate_pass") is True
            and selected.get("doorway_clearance_gate_pass") is True
            and selected.get("floor1_cross_room_vote_pass") is True
            and selected.get("floor2_cross_room_vote_pass") is True
        ):
            raise ValueError(f"selected A4 machine gates are not PASS: {bid}")
        cell = coverage.get((height_mm, kind))
        if cell is None:
            raise ValueError(f"no audited stair coverage cell for {bid}")
        paths = {
            "staircase_usd": Path(cell["staircase_usd"]).resolve(),
            "traversal_path": Path(cell["traversal_path"]).resolve(),
            "usd_audit": Path(cell["usd_audit"]).resolve(),
        }
        for name, path in paths.items():
            if not path.is_file():
                raise FileNotFoundError(path)
            expected_hash = cell[f"{name}_sha256"]
            observed_hash = sha256(path)
            if observed_hash != expected_hash:
                raise ValueError(f"stair asset hash drift: {path}")
        strict = cell.get("strict_collider_normalization_gates", {})
        audit = load(paths["usd_audit"])
        live_strict = {
            name: audit.get("checks", {}).get(name) is True
            for name in STRICT_STAIR_GATES
        }
        if not (
            cell.get("strict_collider_normalization_pass") is True
            and all(strict.get(name) is True for name in STRICT_STAIR_GATES)
            and all(live_strict.values())
            and cell.get("post_A5_build_blocked") is False
        ):
            raise ValueError(f"strict stair normalization failed: {height_mm}/{kind}")
        traversal = load(paths["traversal_path"])
        rise_path = paths["staircase_usd"].parent / "stair_rise_contract.json"
        rise = load(rise_path) if rise_path.is_file() else None
        first_tread = (
            float(rise["first_rise_m"]) if rise is not None
            else min(float(point[2]) for point in traversal["points_xyz_m"])
        )
        if first_tread > 0.20 + 1e-9:
            raise ValueError(f"ground-to-first-tread gate failed: {bid}")
        if rise is not None and rise.get("passes") is not True:
            raise ValueError(f"exact rise contract failed: {bid}")
        asset = {
            "height_mm": height_mm,
            "kind": kind,
            "asset_root": str(paths["staircase_usd"].parent),
            "coverage_evidence": str(args.stair_coverage.resolve()),
            "staircase_usd": str(paths["staircase_usd"]),
            "staircase_usd_sha256": cell["staircase_usd_sha256"],
            "traversal_path": str(paths["traversal_path"]),
            "traversal_path_sha256": cell["traversal_path_sha256"],
            "usd_audit": str(paths["usd_audit"]),
            "usd_audit_sha256": cell["usd_audit_sha256"],
            "strict_normalization_gates": live_strict,
            "strict_normalization_pass": True,
            "ground_to_first_tread_m": round(first_tread, 7),
            "ground_to_first_tread_limit_m": 0.20,
            "ground_to_first_tread_pass": True,
            "ground_to_first_tread_source": (
                "exact authored walking-surface stair_rise_contract"
                if rise is not None else "traversal path fallback"
            ),
            "exact_rise_contract_pass": rise.get("passes") if rise else None,
        }
        live_assets[(height_mm, kind)] = asset
        plan_rows.append({
            "BID": bid,
            "candidate_path": str(candidate_path.resolve()),
            "floor1_scene": selected["floor1"],
            "floor2_scene": selected["floor2"],
            "height_mm": height_mm,
            "height_m": height_mm / 100.0,
            "kind": kind,
            "placement": {
                "rect": selected["rect"],
                "room_rect": selected["room_rect"],
                "rotation_deg": selected["rotation_deg"],
            },
            "stage_b_asset": asset,
        })
    payload = {
        "schema": "StageB10FactoryCPUPreflightV1",
        "status": "PASS",
        "generated_at_utc": now_utc(),
        "h1_package": str(package),
        "h1_manifest": str(manifest_path.resolve()),
        "h1_manifest_sha256": sha256(manifest_path),
        "candidate_review_sha256": sha256(review_path),
        "h1_decision_record_sha256": sha256(decision_path),
        "h1_final_decisions_sha256": sha256(final_decisions_path),
        "approved_building_count": len(plan_rows),
        "approved_building_bids": approved,
        "deferred_building_count": len(deferred),
        "deferred_building_bids": sorted(deferred),
        "deferred_buildings_are_failures": False,
        "corridor_m_frozen": 1.2,
        "body_radius_m_frozen": 0.22,
        "machine_gate_thresholds_modified": False,
        "graph_token_capacity_evidence_invalidated": True,
        "graph_token_capacity_change_performed": False,
        "OBSERVATION_MANIFEST_FROZEN": False,
        "stair_asset_cells_used": list(live_assets.values()),
        "rows": plan_rows,
    }
    write_new_json(args.output.resolve(), payload)
    print(json.dumps({
        "status": payload["status"],
        "approved_building_count": len(plan_rows),
        "deferred_building_count": len(deferred),
        "stair_asset_cell_count": len(live_assets),
    }, indent=2))
    return 0


def remediation_preflight(args: argparse.Namespace) -> int:
    """Replace only named height cells with an already-published Stage B repair.

    The H1 placement path and XY footprint are preserved.  This promotion is
    allowed only when the replacement exactly matches the traversal-path hash
    reviewed at H1, has all four frozen normalization gates, has an exact rise
    contract, and matches the asset hashes retained by a prior published Stage
    B summary.
    """
    source = load(args.preflight.resolve())
    if source.get("status") != "PASS" or len(source.get("rows", [])) != EXPECTED:
        raise ValueError("source preflight is not 10/10 PASS")
    heights = set(args.height_mm)
    if not heights or any(value <= 0 for value in heights):
        raise ValueError("at least one positive --height-mm is required")
    published = load(args.published_stage_summary.resolve())
    published_cells: dict[tuple[int, str], dict[str, Any]] = {}
    for row in published.get("rows", []):
        asset = row.get("stage_b_asset", {})
        if "height_mm" in asset and "kind" in asset:
            published_cells[(int(asset["height_mm"]), str(asset["kind"]))] = asset

    output = copy.deepcopy(source)
    output["schema"] = "StageB10PublishedRiseRemediationPreflightV1"
    output["generated_at_utc"] = now_utc()
    output["source_preflight"] = str(args.preflight.resolve())
    output["source_preflight_sha256"] = sha256(args.preflight.resolve())
    output["published_stage_summary"] = str(args.published_stage_summary.resolve())
    output["published_stage_summary_sha256"] = sha256(
        args.published_stage_summary.resolve())
    output["remediation_root"] = str(args.remediation_root.resolve())
    output["remediated_height_mm"] = sorted(heights)
    output["remediation_reason"] = (
        "promote the already-published walking-surface repair that places the "
        "tread top, not underside, on the frozen riser grid")
    output["machine_gate_thresholds_modified"] = False
    output["corridor_m_frozen"] = 1.2
    output["body_radius_m_frozen"] = 0.22
    output["deferred_candidate_geometry_touched"] = False
    output["H1_planar_placement_changed"] = False
    changes = []
    for row in output["rows"]:
        height_mm = int(row["height_mm"])
        if height_mm not in heights:
            continue
        bid = row["BID"]
        if bid in DEFERRED:
            raise ValueError(f"deferred candidate unexpectedly entered remediation: {bid}")
        kind = row["kind"]
        old = row["stage_b_asset"]
        root = (args.remediation_root.resolve() / f"h{height_mm}" /
                kind / "tex128")
        paths = {
            "staircase_usd": root / "staircase.usd",
            "traversal_path": root / "traversal_path.json",
            "usd_audit": root / "usd_audit.json",
            "stair_rise_contract": root / "stair_rise_contract.json",
        }
        for path in paths.values():
            if not path.is_file():
                raise FileNotFoundError(path)
        hashes = {name: sha256(path) for name, path in paths.items()}
        old_traversal = load(Path(old["traversal_path"]))
        new_traversal = load(paths["traversal_path"])
        old_points = old_traversal.get("points_xyz_m", [])
        new_points = new_traversal.get("points_xyz_m", [])
        if len(old_points) != len(new_points) or not old_points:
            raise ValueError(f"H1 traversal point count changed for {bid}")
        traversal_max_abs_delta_m = max(
            abs(float(before) - float(after))
            for old_point, new_point in zip(old_points, new_points)
            for before, after in zip(old_point, new_point)
        )
        if not (
            old_traversal.get("kind") == new_traversal.get("kind") == kind
            and old_traversal.get("upper_floor_is_final_landing")
            == new_traversal.get("upper_floor_is_final_landing")
            and old_traversal.get("final_landing_support_provider")
            == new_traversal.get("final_landing_support_provider")
            and old_traversal.get("final_landing_index")
            == new_traversal.get("final_landing_index")
            and traversal_max_abs_delta_m <= 1e-9
        ):
            raise ValueError(f"H1 traversal geometry changed for {bid}")
        if hashes["staircase_usd"] == old["staircase_usd_sha256"]:
            raise ValueError(f"remediation did not change staircase asset for {bid}")
        audit = load(paths["usd_audit"])
        strict = {name: audit.get("checks", {}).get(name) is True
                  for name in STRICT_STAIR_GATES}
        rise = load(paths["stair_rise_contract"])
        first_rise = float(rise["first_rise_m"])
        max_rise = float(rise.get("max_rise_m", first_rise))
        if not (all(strict.values()) and rise.get("passes") is True
                and first_rise <= 0.20 + 1e-9
                and max_rise <= 0.20 + 1e-9):
            raise ValueError(f"published rise remediation gates failed for {bid}")
        prior = published_cells.get((height_mm, kind))
        if prior is not None:
            for key, observed in (
                ("staircase_usd_sha256", hashes["staircase_usd"]),
                ("traversal_path_sha256", hashes["traversal_path"]),
                ("usd_audit_sha256", hashes["usd_audit"]),
            ):
                if prior.get(key) != observed:
                    raise ValueError(f"published asset hash mismatch for {bid}: {key}")
        replacement = {
            "height_mm": height_mm,
            "kind": kind,
            "asset_root": str(root),
            "coverage_evidence": str(args.published_stage_summary.resolve()),
            "staircase_usd": str(paths["staircase_usd"]),
            "staircase_usd_sha256": hashes["staircase_usd"],
            "traversal_path": str(paths["traversal_path"]),
            "traversal_path_sha256": hashes["traversal_path"],
            "usd_audit": str(paths["usd_audit"]),
            "usd_audit_sha256": hashes["usd_audit"],
            "stair_rise_contract": str(paths["stair_rise_contract"]),
            "stair_rise_contract_sha256": hashes["stair_rise_contract"],
            "strict_normalization_gates": strict,
            "strict_normalization_pass": True,
            "ground_to_first_tread_m": round(first_rise, 7),
            "maximum_walking_surface_rise_m": round(max_rise, 7),
            "ground_to_first_tread_limit_m": 0.20,
            "ground_to_first_tread_pass": True,
            "ground_to_first_tread_source": (
                "exact authored walking-surface stair_rise_contract"),
            "exact_rise_contract_pass": True,
            "H1_traversal_path_geometrically_unchanged": True,
            "H1_traversal_path_max_abs_delta_m": traversal_max_abs_delta_m,
            "H1_traversal_path_sha256_unchanged": (
                hashes["traversal_path"] == old["traversal_path_sha256"]),
            "H1_planar_placement_reused": True,
            "machine_gate_thresholds_modified": False,
            "replaces_pre_H1_asset": {
                key: old.get(key) for key in (
                    "asset_root", "staircase_usd_sha256",
                    "traversal_path_sha256", "usd_audit_sha256")
            },
        }
        row["stage_b_asset"] = replacement
        changes.append({
            "BID": bid, "height_mm": height_mm, "kind": kind,
            "old_staircase_usd_sha256": old["staircase_usd_sha256"],
            "new_staircase_usd_sha256": hashes["staircase_usd"],
            "old_traversal_path_sha256": old["traversal_path_sha256"],
            "traversal_path_sha256": hashes["traversal_path"],
            "traversal_path_max_abs_delta_m": traversal_max_abs_delta_m,
            "first_rise_m": first_rise, "maximum_rise_m": max_rise,
            "published_stage_summary_cell_present": prior is not None,
            "published_hash_match": True if prior is not None else None,
        })
    expected_changes = sum(int(row["height_mm"]) in heights
                           for row in source["rows"])
    if len(changes) != expected_changes or not changes:
        raise ValueError("remediation did not cover the exact requested height set")
    output["remediated_building_count"] = len(changes)
    output["unchanged_building_count"] = EXPECTED - len(changes)
    output["remediated_building_bids"] = [row["BID"] for row in changes]
    output["changes"] = changes
    write_new_json(args.output.resolve(), output)
    print(json.dumps({
        "status": output["status"],
        "remediated_building_count": len(changes),
        "unchanged_building_count": EXPECTED - len(changes),
        "traversal_path_geometry_unchanged_within_1e-9_m": True,
        "machine_gate_thresholds_modified": False,
    }, indent=2))
    return 0


def run_parallel(
    rows: list[dict[str, Any]], workers: int,
    command_for: Callable[[dict[str, Any]], list[str]], label: str,
) -> None:
    def execute(row: dict[str, Any]) -> tuple[str, int, str]:
        completed = subprocess.run(
            command_for(row), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        return row["BID"], completed.returncode, completed.stdout

    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(execute, row): row for row in rows}
        done = 0
        for future in as_completed(pending):
            bid, returncode, output = future.result()
            done += 1
            print(f"{label} {done}/{len(rows)} {bid} rc={returncode}", flush=True)
            if returncode:
                failures.append({"BID": bid, "returncode": returncode,
                                 "output_tail": output[-8000:]})
    if failures:
        raise RuntimeError(f"{label} failed: {json.dumps(failures, indent=2)}")


def build_one(args: argparse.Namespace) -> int:
    row = load(args.row)
    bid = row["BID"]
    building = args.build_root.resolve() / bid
    if building.exists():
        raise FileExistsError(building)
    scene_dir = building / "scene"
    reports = building / "reports"
    logs = building / "logs"
    for path in (scene_dir, reports, logs):
        path.mkdir(parents=True)
    env = os.environ.copy()
    env["BUILDING_SCENES_ROOT"] = str(args.scenes_root.resolve())
    asset = row["stage_b_asset"]
    command = [
        sys.executable, str(args.build_script.resolve()),
        "--floor1-scene", row["floor1_scene"],
        "--floor2-scene", row["floor2_scene"],
        "--kind", row["kind"],
        "--stair", asset["staircase_usd"],
        "--stair-path", asset["traversal_path"],
        "--placement", json.dumps(row["placement"], separators=(",", ":")),
        "--floor-to-floor", str(row["height_m"]),
        "--out", str(scene_dir / "building.usda"),
        "--report", str(reports / "build_report.json"),
    ]
    completed = subprocess.run(
        command, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    (logs / "build.log").write_text(completed.stdout, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"build failed for {bid}; see {logs / 'build.log'}")
    build_report = load(reports / "build_report.json")
    contract = build_report.get("corridor_contract", {})
    if not (
        build_report.get("status") == "pass"
        and contract.get("corridor_m") == 1.2
        and contract.get("body_radius_m") == 0.22
        and all(build_report.get("build_acceptance_gates", {}).values())
    ):
        raise RuntimeError(f"build/frozen corridor gates failed for {bid}")
    scripts_root = str(args.scripts_root.resolve())
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    from pxr import Usd
    from doorway_clearance import scan_stage

    stage = Usd.Stage.Open(str(scene_dir / "building.usda"), Usd.Stage.LoadAll)
    if stage is None:
        raise RuntimeError(f"built stage did not reopen: {bid}")
    doorway = scan_stage(stage)
    storeys = {}
    for floor in ("Floor1", "Floor2"):
        records = [item for item in doorway["doorways"]
                   if item["doorway_prim"].startswith(f"/World/{floor}/")]
        storeys[floor] = {
            "doorway_count": len(records),
            "failed_doorway_count": sum(not item["pass"] for item in records),
            "minimum_clear_opening_m": round(min(
                float(item["minimum_clear_opening_m"]) for item in records), 4),
            "pass": bool(records) and all(item["pass"] for item in records),
            "doorways": records,
        }
    doorway["storeys"] = storeys
    doorway["pass"] = all(record["pass"] for record in storeys.values())
    doorway["source"] = "composed generated building.usda after door policy"
    write_new_json(reports / "a4_v2_doorway.json", doorway)
    if doorway["pass"] is not True:
        raise RuntimeError(f"A4 v2 doorway gate failed for {bid}")
    stage_row = {
        **{key: row[key] for key in (
            "BID", "floor1_scene", "floor2_scene", "height_mm", "kind",
            "stage_b_asset")},
        "build_status": "pass",
        "door_policy_disabled_count": build_report[
            "door_collision_policy"]["authored_collision_disabled_count"],
        "corridor_contract": contract,
        "A4_v2": {
            floor: {key: value for key, value in summary.items()
                    if key != "doorways"}
            for floor, summary in storeys.items()
        },
        "status": "PASS",
    }
    write_new_json(reports / "stage_c_row.json", stage_row)
    return 0


def build_batch(args: argparse.Namespace) -> int:
    preflight_doc = load(args.preflight.resolve())
    rows = preflight_doc.get("rows", [])
    if preflight_doc.get("status") != "PASS" or len(rows) != EXPECTED:
        raise ValueError("Stage B preflight is not 10/10 PASS")
    root = args.build_root.resolve()
    if root.exists():
        raise FileExistsError(f"refusing to overwrite build root: {root}")
    root.mkdir(parents=True)
    rows_root = root.parent / "build_rows"
    rows_root.mkdir()
    for row in rows:
        write_new_json(rows_root / f"{row['BID']}.json", row)
    run_parallel(rows, args.workers, lambda row: [
        sys.executable, str(Path(__file__).resolve()), "build-one",
        "--row", str(rows_root / f"{row['BID']}.json"),
        "--build-root", str(root),
        "--scenes-root", str(args.scenes_root.resolve()),
        "--build-script", str(args.build_script.resolve()),
        "--scripts-root", str(args.scripts_root.resolve()),
    ], "BUILD")
    result_rows = [load(root / row["BID"] / "reports" / "stage_c_row.json")
                   for row in rows]
    assets = {}
    for row in result_rows:
        asset = row["stage_b_asset"]
        assets[(asset["height_mm"], asset["kind"])] = asset
    stage_b = {
        "schema": "StageBStairAssetRemediationV1",
        "status": "PASS",
        "building_count": EXPECTED,
        "approved_h1_source_count": EXPECTED,
        "deferred_h1_candidates_excluded": sorted(DEFERRED),
        "assets": list(assets.values()),
        "h320_candidate_count_regenerated_through_stage_b": sum(
            row["height_mm"] == 320 for row in result_rows),
        "height_substitution_used": False,
        "corridor_m": 1.2,
        "body_radius_m": 0.22,
        "machine_gate_thresholds_modified": False,
    }
    write_new_json(root.parent / "stage_b_summary.json", stage_b)
    summary = {
        "schema": "SelectedBatchStageCRemediationV1",
        "status": "PASS",
        "building_count": len(result_rows),
        "pass_count": sum(row["status"] == "PASS" for row in result_rows),
        "fail_count": sum(row["status"] != "PASS" for row in result_rows),
        "rows": result_rows,
    }
    write_new_json(root / "stage_c_summary.json", summary)
    print(json.dumps({key: summary[key] for key in (
        "status", "building_count", "pass_count", "fail_count")}, indent=2))
    return 0


def normalize_one(args: argparse.Namespace) -> int:
    scripts_root = str(args.scripts_root.resolve())
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    from pxr import Usd
    from door_collision_policy import inventory
    from doorway_clearance import scan_stage
    from normalize_building_physics import repair

    bid = args.bid
    building = args.stage_c_root.resolve() / bid
    stage_path = building / "scene" / "building.usda"
    report_path = building / "reports" / "physics_normalization.json"
    report = repair(stage_path, report_path)
    stage = Usd.Stage.Open(str(stage_path), Usd.Stage.LoadAll)
    if stage is None:
        raise RuntimeError(f"normalized stage did not reopen: {bid}")
    doors = inventory(stage)
    doorway = scan_stage(stage)
    checks = {
        "normalization_pass": report["status"] == "PASS",
        "swinging_door_collision_disabled": (
            doors["swinging_assembly_collider_count"] > 0
            and doors["swinging_assembly_collision_enabled_count"] == 0),
        "frames_and_jambs_collidable": (
            doors["frame_or_jamb_collider_count"] > 0
            and doors["frame_or_jamb_collision_enabled_count"]
            == doors["frame_or_jamb_collider_count"]),
        "A4_v2_doorway_clearance": doorway["pass"] is True,
    }
    row = {
        "BID": bid,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "before_after_enabled_colliders_per_floor": report[
            "before_after_enabled_colliders_per_floor"],
        "stagewide_enabled_mesh_collider_count": report[
            "stagewide_enabled_mesh_collider_count"],
        "door_inventory": {key: doors[key] for key in (
            "door_object_root_count", "swinging_assembly_collider_count",
            "swinging_assembly_collision_enabled_count",
            "frame_or_jamb_collider_count",
            "frame_or_jamb_collision_enabled_count",
            "unrelated_door_named_collider_count")},
        "A4_v2_minimum_clear_opening_m": min(
            float(item["minimum_clear_opening_m"])
            for item in doorway["doorways"]),
    }
    write_new_json(building / "reports" / "normalization_row.json", row)
    if row["status"] != "PASS":
        raise RuntimeError(f"post-normalization policy failure: {bid}")
    return 0


def normalize_batch(args: argparse.Namespace) -> int:
    root = args.stage_c_root.resolve()
    stage = load(root / "stage_c_summary.json")
    rows = stage.get("rows", [])
    if stage.get("status") != "PASS" or len(rows) != EXPECTED:
        raise ValueError("Stage C build summary is not 10/10 PASS")
    if args.output.exists():
        raise FileExistsError(args.output)
    run_parallel(rows, args.workers, lambda row: [
        sys.executable, str(Path(__file__).resolve()), "normalize-one",
        "--bid", row["BID"], "--stage-c-root", str(root),
        "--scripts-root", str(args.scripts_root.resolve()),
    ], "NORMALIZE")
    result_rows = [load(root / row["BID"] / "reports" /
                        "normalization_row.json") for row in rows]
    payload = {
        "schema": "SelectedBatchPhysicsNormalizationV1",
        "status": "PASS" if all(row["status"] == "PASS"
                                  for row in result_rows) else "FAIL",
        "building_count": len(result_rows),
        "passed_buildings": sum(row["status"] == "PASS"
                                for row in result_rows),
        "failed_buildings": sum(row["status"] != "PASS"
                                for row in result_rows),
        "rows": result_rows,
    }
    write_new_json(args.output.resolve(), payload)
    print(json.dumps({key: payload[key] for key in (
        "status", "building_count", "passed_buildings", "failed_buildings")},
        indent=2))
    return 0 if payload["status"] == "PASS" else 1


def cpu_one(args: argparse.Namespace) -> int:
    scripts_root = str(args.scripts_root.resolve())
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    # The pipeline environment intentionally does not install ProtoMotions.
    # Bind this worker to the same immutable release that owns the voxel baker
    # before importing reachability_gate (which imports ProtoMotions at module
    # import time).
    proto_root = str((args.voxel_bake_root.resolve().parent /
                      "ProtoMotions-main").resolve())
    if not Path(proto_root, "protomotions").is_dir():
        raise FileNotFoundError(f"ProtoMotions release absent: {proto_root}")
    if proto_root not in sys.path:
        sys.path.insert(0, proto_root)
    from acceptance_gates import audit
    from reachability_gate import audit_reachability

    bid = args.bid
    building = args.stage_c_root.resolve() / bid
    evidence = args.evidence_root.resolve() / bid
    if evidence.exists():
        raise FileExistsError(evidence)
    evidence.mkdir(parents=True)
    stage = building / "scene" / "building.usda"
    build_report = building / "reports" / "build_report.json"
    cache = evidence / (bid + ".scene_geometry_v1")
    bake_report = evidence / "voxel_bake_report.json"
    result: dict[str, Any] = {"BID": bid, "status": "FAIL", "gates": {}}
    try:
        completed = subprocess.run([
            sys.executable, str(args.baker.resolve()),
            "--scene", str(stage), "--cache", str(cache),
            "--report", str(bake_report),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (evidence / "voxel_bake.log").write_text(
            completed.stdout, encoding="utf-8")
        if completed.returncode:
            raise RuntimeError(f"voxel bake rc={completed.returncode}")
        structural = audit(
            stage, bake_report, voxel_bake_root=args.voxel_bake_root.resolve())
        g24 = audit_reachability(
            stage, build_report, cache,
            control_stage_path=args.control_building.resolve() /
            "scene" / "building.usda",
            control_build_report_path=args.control_building.resolve() /
            "reports" / "build.json",
            control_cache_path=args.control_cache.resolve())
        gates = structural["gates"] | {"G24_stair_reachability": g24}
        statuses = {name: gates[name]["status"] for name in CPU_GATES}
        result.update({
            "status": "PASS" if all(value == "PASS"
                                    for value in statuses.values()) else "FAIL",
            "gate_statuses": statuses,
            "gates": gates,
            "G22b": structural["informational_checks"][
                "G22b_mesh_extent_matches_points"],
        })
    except Exception as error:
        result["error"] = str(error)
        result["traceback"] = traceback.format_exc()
    write_new_json(evidence / "g19_g24.json", result)
    return 0 if result["status"] == "PASS" else 1


def cpu_batch(args: argparse.Namespace) -> int:
    stage = load(args.stage_c_root.resolve() / "stage_c_summary.json")
    normalization = load(args.normalization_summary.resolve())
    rows = stage.get("rows", [])
    if not (
        stage.get("status") == "PASS" and len(rows) == EXPECTED
        and normalization.get("status") == "PASS"
        and normalization.get("passed_buildings") == EXPECTED
        and normalization.get("failed_buildings") == 0
    ):
        raise ValueError("build/normalization prerequisites are not 10/10 PASS")
    evidence_root = args.evidence_root.resolve()
    if evidence_root.exists() or args.output.exists():
        raise FileExistsError("refusing existing CPU gate evidence or summary")
    evidence_root.mkdir(parents=True)
    failures: list[dict[str, Any]] = []

    def command(row: dict[str, Any]) -> list[str]:
        return [
            sys.executable, str(Path(__file__).resolve()), "cpu-one",
            "--bid", row["BID"],
            "--stage-c-root", str(args.stage_c_root.resolve()),
            "--scripts-root", str(args.scripts_root.resolve()),
            "--evidence-root", str(evidence_root),
            "--baker", str(args.baker.resolve()),
            "--voxel-bake-root", str(args.voxel_bake_root.resolve()),
            "--control-building", str(args.control_building.resolve()),
            "--control-cache", str(args.control_cache.resolve()),
        ]

    # Retain FAIL evidence for every building instead of aborting the batch on
    # the first nonzero child return code.
    def execute(row: dict[str, Any]) -> tuple[str, int, str]:
        completed = subprocess.run(
            command(row), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        return row["BID"], completed.returncode, completed.stdout

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(execute, row): row for row in rows}
        done = 0
        for future in as_completed(pending):
            bid, returncode, output = future.result()
            done += 1
            print(f"CPU-G19-G24 {done}/{len(rows)} {bid} rc={returncode}",
                  flush=True)
            if returncode:
                failures.append({"BID": bid, "returncode": returncode,
                                 "output_tail": output[-4000:]})
    summary_rows = []
    for row in rows:
        result = load(evidence_root / row["BID"] / "g19_g24.json")
        summary_rows.append({
            "BID": row["BID"], "status": result["status"],
            "gate_statuses": result.get("gate_statuses", {}),
            "error": result.get("error"),
        })
    passed = sum(row["status"] == "PASS" for row in summary_rows)
    payload = {
        "schema": "H1MachineG19G24V1",
        "status": "PASS" if passed == EXPECTED else "FAIL",
        "required_gates": list(CPU_GATES),
        "building_count": len(summary_rows),
        "passed_buildings": passed,
        "failed_buildings": len(summary_rows) - passed,
        "failed_candidates_retained": True,
        "G22_definition": (
            "actual baker _collider_bounds_m contains the actual rasterized "
            "vertex union, including purpose=guide"),
        "G22b_scope": (
            "informational only; stale per-mesh extent was never observed in "
            "this project"),
        "rows": summary_rows,
        "child_failures": failures,
    }
    write_new_json(args.output.resolve(), payload)
    print(json.dumps({key: payload[key] for key in (
        "status", "building_count", "passed_buildings", "failed_buildings")},
        indent=2))
    return 0 if payload["status"] == "PASS" else 1


def link_or_copy(src: str, dst: str) -> str:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


def prepare_delivery(args: argparse.Namespace) -> int:
    source = args.stage_c_root.resolve()
    destination = args.delivery_root.resolve()
    stage = load(source / "stage_c_summary.json")
    bids = [row["BID"] for row in stage.get("rows", [])]
    if stage.get("status") != "PASS" or len(bids) != EXPECTED:
        raise ValueError("Stage C source is not 10/10 PASS")
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    for index, bid in enumerate(bids, 1):
        shutil.copytree(source / bid, destination / bid,
                        copy_function=link_or_copy)
        print(f"DELIVERY-COPY {index}/{len(bids)} {bid}", flush=True)
    shutil.copy2(source / "stage_c_summary.json",
                 destination / "stage_c_summary.json")
    payload = {
        "schema": "StageB10FactoryDeliveryCopyV1", "status": "PASS",
        "source_stage_c_root": str(source),
        "delivery_root": str(destination),
        "building_count": len(bids), "building_bids": bids,
        "copy_policy": "hardlink where supported, byte-copy fallback; later atomic wrapper replacement does not mutate source links",
    }
    write_new_json(destination / "DELIVERY_COPY_RECORD.json", payload)
    return 0


def import_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def package_one(args: argparse.Namespace) -> int:
    module = import_module(args.package_script.resolve(), "stage_b_package_impl")
    building = args.delivery_root.resolve() / args.bid
    row = module.package_building(building)
    if row.get("status") != "PASS":
        raise RuntimeError(f"self-contained packaging failed: {args.bid}")
    return 0


def package_batch(args: argparse.Namespace) -> int:
    root = args.delivery_root.resolve()
    stage = load(root / "stage_c_summary.json")
    rows = stage.get("rows", [])
    bids = [row["BID"] for row in rows]
    if len(bids) != EXPECTED or set(bids) != {
            path.name for path in root.iterdir()
            if path.is_dir() and path.name.startswith("factory_")}:
        raise ValueError("delivery root is not the exact named 10-building set")
    if args.output.exists():
        raise FileExistsError(args.output)
    run_parallel(rows, args.workers, lambda row: [
        sys.executable, str(Path(__file__).resolve()), "package-one",
        "--bid", row["BID"], "--delivery-root", str(root),
        "--package-script", str(args.package_script.resolve()),
    ], "PACKAGE")
    result_rows = [load(root / bid / "reports" /
                        "self_contained_package.json") for bid in bids]
    payload = {
        "status": "PASS",
        "expected_building_count": EXPECTED,
        "observed_building_count": len(result_rows),
        "total_package_bytes": sum(row["package_bytes"] for row in result_rows),
        "all_off_cluster_unresolved_references_zero": all(
            row["off_cluster_unresolved_references"] == 0
            for row in result_rows),
        "all_cluster_absolute_paths_in_packages_zero": all(
            row["cluster_absolute_paths_in_package"] == 0
            for row in result_rows),
        "rows": result_rows,
    }
    if not (payload["all_off_cluster_unresolved_references_zero"]
            and payload["all_cluster_absolute_paths_in_packages_zero"]):
        payload["status"] = "FAIL"
    write_new_json(args.output.resolve(), payload)
    print(json.dumps({
        "status": payload["status"], "building_count": len(result_rows),
        "total_package_bytes": payload["total_package_bytes"],
    }, indent=2))
    return 0 if payload["status"] == "PASS" else 1


def audit_package_one(args: argparse.Namespace) -> int:
    scripts_root = str(args.scripts_root.resolve())
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    from pxr import Usd, UsdPhysics
    from door_collision_policy import inventory

    package = args.delivery_root.resolve() / args.bid / "scene" / "building.usdz"
    stage = Usd.Stage.Open(str(package), Usd.Stage.LoadAll)
    if stage is None:
        raise ValueError(f"cannot open {package}")
    counts = {name: 0 for name in ("Floor1", "Floor2", "Stair", "Cut", "other")}
    rigid = []
    enabled_total = 0
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        path = str(prim.GetPath())
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            rigid.append(path)
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:
            continue
        enabled_total += 1
        bucket = "other"
        for candidate in ("Floor1", "Floor2", "Stair", "Cut"):
            if path.startswith(f"/World/{candidate}/"):
                bucket = candidate
                break
        counts[bucket] += 1
    doors = inventory(stage)
    checks = {
        "package_opens": True,
        "no_rigid_body_api": not rigid,
        "has_floor1_colliders": counts["Floor1"] > 1,
        "has_floor2_colliders": counts["Floor2"] > 1,
        "has_swinging_members": doors["swinging_assembly_collider_count"] > 0,
        "all_swinging_members_disabled": doors[
            "swinging_assembly_collision_enabled_count"] == 0,
        "all_frames_and_jambs_enabled": (
            doors["frame_or_jamb_collider_count"] > 0
            and doors["frame_or_jamb_collision_enabled_count"]
            == doors["frame_or_jamb_collider_count"]),
    }
    row = {
        "BID": args.bid,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "enabled_collider_count": enabled_total,
        "enabled_collider_count_by_root": counts,
        "rigid_body_api_count": len(rigid),
        "door_object_root_count": doors["door_object_root_count"],
        "swinging_assembly_collider_count": doors[
            "swinging_assembly_collider_count"],
        "swinging_assembly_enabled_count": doors[
            "swinging_assembly_collision_enabled_count"],
        "frame_or_jamb_collider_count": doors["frame_or_jamb_collider_count"],
        "frame_or_jamb_enabled_count": doors[
            "frame_or_jamb_collision_enabled_count"],
        "checks": checks,
    }
    write_new_json(args.delivery_root.resolve() / args.bid / "reports" /
                   "packaged_geometry_row.json", row)
    return 0 if row["status"] == "PASS" else 1


def audit_packages(args: argparse.Namespace) -> int:
    root = args.delivery_root.resolve()
    stage = load(root / "stage_c_summary.json")
    rows = stage.get("rows", [])
    if len(rows) != EXPECTED or args.output.exists():
        raise ValueError("package audit prerequisites invalid or output exists")
    failures: list[str] = []

    def execute(row: dict[str, Any]) -> tuple[str, int]:
        completed = subprocess.run([
            sys.executable, str(Path(__file__).resolve()), "audit-package-one",
            "--bid", row["BID"], "--delivery-root", str(root),
            "--scripts-root", str(args.scripts_root.resolve()),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return row["BID"], completed.returncode

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(execute, row): row for row in rows}
        done = 0
        for future in as_completed(pending):
            bid, returncode = future.result()
            done += 1
            print(f"PACKAGE-AUDIT {done}/{len(rows)} {bid} rc={returncode}", flush=True)
            if returncode:
                failures.append(bid)
    result_rows = [load(root / row["BID"] / "reports" /
                        "packaged_geometry_row.json") for row in rows]
    passed = sum(row["status"] == "PASS" for row in result_rows)
    payload = {
        "schema": "PackagedGeometryDoorPolicyAuditV1",
        "status": "PASS" if passed == EXPECTED else "FAIL",
        "building_count": len(result_rows),
        "passed_buildings": passed,
        "failed_buildings": len(result_rows) - passed,
        "failed_candidates_retained": True,
        "measurement_source": "published self-contained building.usdz",
        "rows": result_rows,
        "child_failure_bids": failures,
    }
    write_new_json(args.output.resolve(), payload)
    print(json.dumps({key: payload[key] for key in (
        "status", "building_count", "passed_buildings", "failed_buildings")},
        indent=2))
    return 0 if payload["status"] == "PASS" else 1


def g25_batch(args: argparse.Namespace) -> int:
    root = args.delivery_root.resolve()
    stage = load(root / "stage_c_summary.json")
    rows = stage.get("rows", [])
    output_root = args.output_root.resolve()
    if len(rows) != EXPECTED or output_root.exists() or args.summary.exists():
        raise ValueError("G25 prerequisites invalid or evidence already exists")
    output_root.mkdir(parents=True)

    def execute(row: dict[str, Any]) -> tuple[str, int, str]:
        bid = row["BID"]
        output = output_root / f"{bid}.json"
        completed = subprocess.run([
            str(args.python.resolve()), str(args.gate_script.resolve()),
            "--building", bid, "--batch-root", str(root),
            "--enforce-unreachable-component-limit", "--output", str(output),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (output_root / f"{bid}.log").write_text(
            completed.stdout, encoding="utf-8")
        return bid, completed.returncode, completed.stdout

    child_results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(execute, row): row for row in rows}
        done = 0
        for future in as_completed(pending):
            bid, returncode, output = future.result()
            done += 1
            print(f"G25 {done}/{len(rows)} {bid} rc={returncode}", flush=True)
            child_results.append({"BID": bid, "returncode": returncode,
                                  "output_tail": output[-2000:]})
    summary_rows = []
    for source in rows:
        bid = source["BID"]
        output = output_root / f"{bid}.json"
        if output.is_file():
            report = load(output)
            storeys = report.get("storeys", {})
            trips = sum(value.get("A4_unreachable_component_rule", {}).get(
                "oversized_unreachable_component_count", 0)
                for value in storeys.values())
            summary_rows.append({
                "BID": bid, "status": report.get("status", "FAIL"),
                "returncode": next(row["returncode"] for row in child_results
                                   if row["BID"] == bid),
                "Floor1_status": storeys.get("Floor1", {}).get("status"),
                "Floor2_status": storeys.get("Floor2", {}).get("status"),
                "unreachable_component_rule_trip_count": trips,
            })
        else:
            summary_rows.append({"BID": bid, "status": "FAIL",
                                 "error": "gate wrote no JSON report"})
    passed = sum(row["status"] == "PASS" for row in summary_rows)
    payload = {
        "schema": "BatchG25WithA4UnreachableComponentRuleV1",
        "status": "PASS" if passed == EXPECTED else "FAIL",
        "building_count": len(summary_rows),
        "passed_buildings": passed,
        "failed_buildings": len(summary_rows) - passed,
        "failed_candidates_retained": True,
        "unreachable_component_rule_threshold_m2": 4.0,
        "unreachable_component_rule_trip_count": sum(
            row.get("unreachable_component_rule_trip_count", 0)
            for row in summary_rows),
        "rows": summary_rows,
    }
    write_new_json(args.summary.resolve(), payload)
    print(json.dumps({key: payload[key] for key in (
        "status", "building_count", "passed_buildings", "failed_buildings",
        "unreachable_component_rule_trip_count")}, indent=2))
    return 0 if payload["status"] == "PASS" else 1


def summary_evidence(path: Path, keys: tuple[str, ...]) -> dict[str, Any]:
    document = load(path.resolve())
    return {
        "path": str(path.resolve()),
        "sha256": sha256(path.resolve()),
        **{key: document.get(key) for key in keys},
    }


def finalize_cpu_boundary(args: argparse.Namespace) -> int:
    h1_package = args.h1_package.resolve()
    run_root = args.run_root.resolve()
    manifest_path = h1_package / "manifest.json"
    manifest = load(manifest_path)
    approved = manifest.get("stage_b_authorized_building_bids", [])
    deferred = set(manifest.get("deferred_building_bids", []))
    if len(approved) != EXPECTED or len(set(approved)) != EXPECTED or deferred != DEFERRED:
        raise ValueError("H1 manifest no longer names the authorized 48/2 set")
    approved_set = set(approved)

    remediation_path = run_root / "stage_b48_remediation_preflight.json"
    stage_b_path = run_root / "stage_b_summary.json"
    stage_c_path = run_root / "stage_c_buildings" / "stage_c_summary.json"
    normalization_path = run_root / "physics_normalization_batch.json"
    cpu_path = run_root / "cpu_g19_g24_summary.json"
    packaging_path = run_root / "self_contained_packaging_summary.json"
    package_audit_path = run_root / "packaged_geometry_audit.json"
    required = (
        remediation_path, stage_b_path, stage_c_path, normalization_path,
        cpu_path, packaging_path, package_audit_path,
    )
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)
    remediation = load(remediation_path)
    stage_b = load(stage_b_path)
    stage_c = load(stage_c_path)
    normalization = load(normalization_path)
    cpu = load(cpu_path)
    packaging = load(packaging_path)
    package_audit = load(package_audit_path)
    stage_bids = [row["BID"] for row in stage_c.get("rows", [])]
    delivery_root = run_root / "delivery_cpu_48"
    delivery_bids = {
        path.name for path in delivery_root.iterdir()
        if path.is_dir() and path.name.startswith("home_")
    }
    if not (
        remediation.get("status") == "PASS"
        and remediation.get("remediated_building_count") == 18
        and remediation.get("unchanged_building_count") == 30
        and remediation.get("machine_gate_thresholds_modified") is False
        and remediation.get("deferred_candidate_geometry_touched") is False
        and stage_b.get("status") == "PASS"
        and stage_b.get("building_count") == EXPECTED
        and stage_b.get("corridor_m") == 1.2
        and stage_b.get("body_radius_m") == 0.22
        and stage_b.get("machine_gate_thresholds_modified") is False
        and stage_c.get("status") == "PASS"
        and stage_c.get("building_count") == EXPECTED
        and stage_c.get("pass_count") == EXPECTED
        and stage_c.get("fail_count") == 0
        and set(stage_bids) == approved_set
        and len(stage_bids) == len(set(stage_bids)) == EXPECTED
        and normalization.get("status") == "PASS"
        and normalization.get("passed_buildings") == EXPECTED
        and normalization.get("failed_buildings") == 0
        and cpu.get("status") == "PASS"
        and cpu.get("passed_buildings") == EXPECTED
        and cpu.get("failed_buildings") == 0
        and packaging.get("status") == "PASS"
        and packaging.get("observed_building_count") == EXPECTED
        and packaging.get("all_off_cluster_unresolved_references_zero") is True
        and packaging.get("all_cluster_absolute_paths_in_packages_zero") is True
        and package_audit.get("status") == "PASS"
        and package_audit.get("passed_buildings") == EXPECTED
        and package_audit.get("failed_buildings") == 0
        and delivery_bids == approved_set
    ):
        raise ValueError("one or more CPU-only Stage B completion gates failed")

    for bid in DEFERRED:
        if (run_root / "stage_c_buildings" / bid).exists() or (
                delivery_root / bid).exists():
            raise ValueError(f"deferred geometry entered Stage B output: {bid}")
    isaac_reports = list(run_root.rglob("isaac_validation.json"))
    if isaac_reports:
        raise ValueError(f"unexpected Isaac reports in CPU-only run: {isaac_reports[:5]}")
    probe_log = args.g25_blocker_probe_log.resolve()
    if not probe_log.is_file() or "reports/isaac_validation.json" not in probe_log.read_text(
            encoding="utf-8", errors="replace"):
        raise ValueError("G25 blocker probe does not prove the missing Isaac input")
    invalid_g25_summary = args.invalid_g25_summary.resolve()
    invalid_g25 = load(invalid_g25_summary)
    if not (invalid_g25.get("building_count") == EXPECTED
            and invalid_g25.get("passed_buildings") == 0):
        raise ValueError("retained G25 startup-failure summary has unexpected shape")

    def capture(command: list[str]) -> dict[str, Any]:
        completed = subprocess.run(
            command, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        return {"command": command, "returncode": completed.returncode,
                "output": completed.stdout}

    queue = capture(["squeue", "-u", getpass.getuser()])
    steps = capture(["squeue", "-s", "-u", getpass.getuser()])
    capacity = capture(["sinfo", "-s"])
    hold_present = "hgpt_gpu_hold" in queue["output"]
    public_match = re.search(r"^gpu\s+(\d+)\s+48\s*$", capacity["output"], re.MULTILINE)
    public_free = int(public_match.group(1)) if public_match else None
    if hold_present:
        raise ValueError("GPU hold appeared; CPU boundary report must be re-evaluated")
    if public_free != 0:
        raise ValueError("public GPU capacity is no longer the observed 0/48 boundary")

    prior_attempt_path = args.prior_cpu_summary.resolve()
    prior_attempt = load(prior_attempt_path)
    if not (prior_attempt.get("building_count") == EXPECTED
            and prior_attempt.get("passed_buildings") == 30
            and prior_attempt.get("failed_buildings") == 18):
        raise ValueError("r1 remediation trigger evidence changed")

    payload = {
        "schema": "StageB48CPUOnlyBoundaryV1",
        "status": "CPU_ONLY_COMPLETE_AWAITING_GPU_ISAAC",
        "generated_at_utc": now_utc(),
        "h1": {
            "package": str(h1_package),
            "manifest": str(manifest_path),
            "manifest_sha256": sha256(manifest_path),
            "h1_decision_recorded_at_utc": manifest.get(
                "h1_decision_recorded_at_utc"),
            "h1_placement_approved": manifest.get("h1_placement_approved"),
            "stage_b_blocked_pending_h1": manifest.get(
                "stage_b_blocked_pending_h1"),
            "approved_building_count": EXPECTED,
            "approved_building_bids": approved,
            "deferred_building_count": len(DEFERRED),
            "deferred_building_bids": sorted(DEFERRED),
            "deferred_buildings_are_failures": False,
        },
        "frozen_contracts": {
            "corridor_m": 1.2,
            "body_radius_m": 0.22,
            "machine_gate_thresholds_modified": False,
            "deferred_candidate_geometry_touched": False,
        },
        "remediation": {
            "status": "PASS",
            "reason": remediation["remediation_reason"],
            "remediated_building_count": 18,
            "unchanged_building_count": 30,
            "remediated_height_mm": remediation["remediated_height_mm"],
            "H1_planar_placement_changed": False,
            "traversal_path_geometry_unchanged_within_1e-9_m": True,
            "evidence": summary_evidence(remediation_path, (
                "status", "remediated_building_count",
                "unchanged_building_count")),
            "prior_r1_machine_result": summary_evidence(prior_attempt_path, (
                "status", "building_count", "passed_buildings",
                "failed_buildings")),
        },
        "cpu_only_completed": {
            "assembly": summary_evidence(stage_c_path, (
                "status", "building_count", "pass_count", "fail_count")),
            "physics_normalization": summary_evidence(normalization_path, (
                "status", "building_count", "passed_buildings",
                "failed_buildings")),
            "G19_G24": summary_evidence(cpu_path, (
                "status", "building_count", "passed_buildings",
                "failed_buildings")),
            "self_contained_packaging": summary_evidence(packaging_path, (
                "status", "expected_building_count", "observed_building_count",
                "total_package_bytes",
                "all_off_cluster_unresolved_references_zero",
                "all_cluster_absolute_paths_in_packages_zero")),
            "packaged_geometry_audit": summary_evidence(package_audit_path, (
                "status", "building_count", "passed_buildings",
                "failed_buildings")),
        },
        "gpu_boundary": {
            "status": "BLOCKED_PENDING_GPU_ISAAC",
            "first_required_gpu_stage": (
                "Stage D Isaac validation: 18 Isaac acceptance gates, path samples, "
                "and render/collision review frames"),
            "isaac_validation_performed": False,
            "isaac_report_count": 0,
            "cpu_renderer_substitution_used": False,
            "next_cpu_stage_after_isaac": (
                "G25 delivered-storey navigation; CPU rasterization requires "
                "Floor1 seed samples from reports/isaac_validation.json"),
            "G25_scored_result_available": False,
            "G25_blocker_probe_log": str(probe_log),
            "G25_blocker_probe_log_sha256": sha256(probe_log),
            "invalid_startup_summary_retained_not_scored": {
                "path": str(invalid_g25_summary),
                "sha256": sha256(invalid_g25_summary),
                "reason": "toolchain import failure; no per-building JSON was written",
            },
            "gpu_hold_present": False,
            "public_gpu_free": public_free,
            "public_gpu_total": 48,
            "new_gpu_job_submitted_by_this_run": False,
            "existing_jobs_cancelled_or_modified": False,
            "vip_gpu_partition_used": False,
            "squeue_snapshot": queue,
            "squeue_steps_snapshot": steps,
            "sinfo_snapshot": capacity,
        },
        "graph_token_capacity": {
            "graph_token_capacity_evidence_invalidated": manifest.get(
                "graph_token_capacity_evidence_invalidated"),
            "previous_frozen_capacity": manifest.get(
                "graph_token_previous_frozen_capacity"),
            "previous_evidence_building_count": manifest.get(
                "graph_token_previous_evidence_building_count"),
            "previous_evidence_max_nodes": manifest.get(
                "graph_token_previous_evidence_max_nodes"),
            "remeasurement_required_for_building_count": manifest.get(
                "graph_token_capacity_remeasurement_required_for_building_count"),
            "capacity_change_performed": False,
            "OBSERVATION_MANIFEST_FROZEN": manifest.get(
                "OBSERVATION_MANIFEST_FROZEN"),
            "training_authorized": False,
        },
    }
    if not (
        payload["h1"]["h1_placement_approved"] is True
        and payload["h1"]["stage_b_blocked_pending_h1"] is False
        and payload["graph_token_capacity"][
            "graph_token_capacity_evidence_invalidated"] is True
        and payload["graph_token_capacity"]["previous_frozen_capacity"] == 2048
        and payload["graph_token_capacity"][
            "previous_evidence_building_count"] == 20
        and payload["graph_token_capacity"]["previous_evidence_max_nodes"] == 1619
        and payload["graph_token_capacity"][
            "remeasurement_required_for_building_count"] == EXPECTED
        and payload["graph_token_capacity"]["OBSERVATION_MANIFEST_FROZEN"] is False
    ):
        raise ValueError("H1/capacity boundary facts failed final assertion")
    write_new_json(args.output.resolve(), payload)
    print(json.dumps({
        "status": payload["status"],
        "approved_buildings": EXPECTED,
        "assembly_pass": stage_c["pass_count"],
        "G19_G24_pass": cpu["passed_buildings"],
        "packaging_pass": packaging["observed_building_count"],
        "package_audit_pass": package_audit["passed_buildings"],
        "gpu_hold_present": hold_present,
        "public_gpu_free": public_free,
        "capacity_evidence_invalidated": payload["graph_token_capacity"][
            "graph_token_capacity_evidence_invalidated"],
    }, indent=2))
    return 0


def add_common_subcommands(subparsers: Any) -> None:
    p = subparsers.add_parser("preflight")
    p.add_argument("--h1-package", type=Path, required=True)
    p.add_argument("--stair-coverage", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(function=preflight)

    p = subparsers.add_parser("remediation-preflight")
    p.add_argument("--preflight", type=Path, required=True)
    p.add_argument("--remediation-root", type=Path, required=True)
    p.add_argument("--published-stage-summary", type=Path, required=True)
    p.add_argument("--height-mm", type=int, action="append", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(function=remediation_preflight)

    p = subparsers.add_parser("build")
    p.add_argument("--preflight", type=Path, required=True)
    p.add_argument("--build-root", type=Path, required=True)
    p.add_argument("--scenes-root", type=Path, required=True)
    p.add_argument("--build-script", type=Path, required=True)
    p.add_argument("--scripts-root", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(function=build_batch)

    p = subparsers.add_parser("build-one")
    p.add_argument("--row", type=Path, required=True)
    p.add_argument("--build-root", type=Path, required=True)
    p.add_argument("--scenes-root", type=Path, required=True)
    p.add_argument("--build-script", type=Path, required=True)
    p.add_argument("--scripts-root", type=Path, required=True)
    p.set_defaults(function=build_one)

    p = subparsers.add_parser("normalize")
    p.add_argument("--stage-c-root", type=Path, required=True)
    p.add_argument("--scripts-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(function=normalize_batch)

    p = subparsers.add_parser("normalize-one")
    p.add_argument("--bid", required=True)
    p.add_argument("--stage-c-root", type=Path, required=True)
    p.add_argument("--scripts-root", type=Path, required=True)
    p.set_defaults(function=normalize_one)

    p = subparsers.add_parser("cpu-gates")
    for name in ("stage-c-root", "normalization-summary", "scripts-root",
                 "evidence-root", "baker", "voxel-bake-root",
                 "control-building", "control-cache", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(function=cpu_batch)

    p = subparsers.add_parser("cpu-one")
    p.add_argument("--bid", required=True)
    for name in ("stage-c-root", "scripts-root", "evidence-root", "baker",
                 "voxel-bake-root", "control-building", "control-cache"):
        p.add_argument("--" + name, type=Path, required=True)
    p.set_defaults(function=cpu_one)

    p = subparsers.add_parser("prepare-delivery")
    p.add_argument("--stage-c-root", type=Path, required=True)
    p.add_argument("--delivery-root", type=Path, required=True)
    p.set_defaults(function=prepare_delivery)

    p = subparsers.add_parser("package")
    p.add_argument("--delivery-root", type=Path, required=True)
    p.add_argument("--package-script", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--workers", type=int, default=2)
    p.set_defaults(function=package_batch)

    p = subparsers.add_parser("package-one")
    p.add_argument("--bid", required=True)
    p.add_argument("--delivery-root", type=Path, required=True)
    p.add_argument("--package-script", type=Path, required=True)
    p.set_defaults(function=package_one)

    p = subparsers.add_parser("audit-packages")
    p.add_argument("--delivery-root", type=Path, required=True)
    p.add_argument("--scripts-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(function=audit_packages)

    p = subparsers.add_parser("audit-package-one")
    p.add_argument("--bid", required=True)
    p.add_argument("--delivery-root", type=Path, required=True)
    p.add_argument("--scripts-root", type=Path, required=True)
    p.set_defaults(function=audit_package_one)

    p = subparsers.add_parser("g25")
    p.add_argument("--delivery-root", type=Path, required=True)
    p.add_argument("--gate-script", type=Path, required=True)
    p.add_argument("--python", type=Path, required=True)
    p.add_argument("--output-root", type=Path, required=True)
    p.add_argument("--summary", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(function=g25_batch)

    p = subparsers.add_parser("finalize-cpu-boundary")
    p.add_argument("--h1-package", type=Path, required=True)
    p.add_argument("--run-root", type=Path, required=True)
    p.add_argument("--prior-cpu-summary", type=Path, required=True)
    p.add_argument("--g25-blocker-probe-log", type=Path, required=True)
    p.add_argument("--invalid-g25-summary", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(function=finalize_cpu_boundary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_common_subcommands(subparsers)
    args = parser.parse_args()
    if hasattr(args, "workers") and not 1 <= args.workers <= 8:
        raise ValueError("workers must be in [1, 8]")
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
