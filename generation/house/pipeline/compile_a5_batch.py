#!/usr/bin/env python3
"""Compile strict A4 results into a fail-closed, CPU-only A5 review package.

This tool never authorizes or starts generation.  It records which
height/kind cells were searched under the current placement contract, which
strict candidates remain after excluding already-approved buildings, and what
coverage is still missing before a batch can be submitted for human approval.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import subprocess
import sys
import hashlib
from collections import Counter
from pathlib import Path

from PIL import Image

from prepare_manifest import MANIFEST_FIELDS, bid_for, evidence_text, validate


KINDS = ("straight", "l_shaped", "u_shaped")
DEPENDENCY_CATEGORIES = {
    "building_structure", "furniture_visibility", "collision",
    "noncritical_metadata", "unclassified_unknown",
}
REQUIRED_ROTATIONS = {0, 90, 180, 270}
# Searches in the new batch are exhaustive.  This ceiling is only a defensive
# guard against a malformed pair cache, not a sampling limit.
MAX_PAIRS_PER_SEARCH = 100_000
MAX_GRID_STEP_M = 0.01
CORRIDOR_M = 1.2
BODY_RADIUS_M = 0.22
STRICT_COLLIDER_GATES = (
    "all_authored_tread_colliders_normalized",
    "source_tread_collision_prims_deactivated",
    "combined_tread_collider_contains_all_boxes",
    "combined_tread_collider_is_static_closed_triangle_mesh",
)
FLOORPLAN_WIDTH = 1200
FLOORPLAN_HEIGHT = 900


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_scene_cache(cache_path: Path, pairs_path: Path, types_path: Path,
                         buildings: list[dict]) -> None:
    cache = load(cache_path)
    pairs = load(pairs_path)
    types = load(types_path)
    if cache.get("cache_contract_version") not in (2, 3):
        raise ValueError("A1 geometry cache contract is stale")
    if cache.get("complete_for_pair_cache") is not True:
        raise ValueError("A1 geometry cache is partial")
    if cache.get("pair_cache_sha256") != file_sha256(pairs_path):
        raise ValueError("A1 geometry cache belongs to a different pair cache")
    if cache.get("type_cache_sha256") != file_sha256(types_path):
        raise ValueError("A1 geometry cache belongs to a different type table")
    roots = {str(record.get("scene_root", "")) for record in (cache, pairs, types)}
    if "" in roots or len(roots) != 1:
        raise ValueError("A1/A2/A3 scene roots are absent or inconsistent")
    needed = {record[floor] for record in buildings
              for floor in ("floor1", "floor2")}
    missing = sorted(needed.difference(cache.get("scenes", {})))
    if missing:
        raise ValueError("A1 geometry cache is missing candidate scenes: "
                         + ", ".join(missing))


def height_key(value: float) -> str:
    return "{:.3f}".format(float(value))


def pair_key(record: dict) -> tuple[str, str, str]:
    return record["floor1"], record["floor2"], record["kind"]


def strict_pair_inventory(pair_doc: dict) -> list[dict]:
    unique: dict[tuple[str, str, str], dict] = {}
    for record in pair_doc["results"]:
        if not record.get("usable") or not record.get("pair_gate_passed"):
            continue
        if record.get("kind") not in KINDS:
            raise ValueError("unsupported pair kind: {}".format(record.get("kind")))
        key = pair_key(record)
        if key in unique:
            raise ValueError("duplicate usable pair/kind record: {}".format(key))
        unique[key] = record
    return list(unique.values())


def validate_search_document(doc: dict, height: float, kind: str,
                             expected: list[dict], types: dict) -> list[dict]:
    if kind not in KINDS:
        raise ValueError("unsupported placement kind: " + kind)
    if doc.get("placement_contract_version") != 3:
        raise ValueError("{} {} placement contract is stale".format(height, kind))
    if doc.get("doorway_clearance_contract_version") != 2:
        raise ValueError("{} {} doorway-clearance contract is absent".format(height, kind))
    if "0.60 m" not in str(doc.get("doorway_clearance_rule", "")):
        raise ValueError("{} {} doorway-clearance threshold is absent".format(height, kind))
    if doc.get("corridor_obstacle_source") != "all_obstacle_boxes_walk_prism":
        raise ValueError("{} {} omitted visual-only obstacles".format(height, kind))
    if abs(float(doc.get("corridor_m", -1)) - CORRIDOR_M) > 1e-9:
        raise ValueError("{} {} corridor is not 1.2 m".format(height, kind))
    if abs(float(doc.get("corridor_body_radius_m", -1)) - BODY_RADIUS_M) > 1e-9:
        raise ValueError("{} {} body radius is not 0.22 m".format(height, kind))
    if float(doc.get("grid_step_m", 999)) > MAX_GRID_STEP_M:
        raise ValueError("{} {} grid is coarser than 0.01 m".format(height, kind))
    if float(doc.get("height_tolerance_m", 999)) > 0.001:
        raise ValueError("{} {} height tolerance exceeds 1 mm".format(height, kind))
    if set(int(v) for v in doc.get("rotations_tested_deg", [])) != REQUIRED_ROTATIONS:
        raise ValueError("{} {} did not test all quarter turns".format(height, kind))
    if abs(float(doc.get("stair_height_m", -999)) - float(height)) > 0.001:
        raise ValueError("{} {} stair height metadata mismatch".format(height, kind))
    searched_pair_count = int(doc.get("pairs_height_and_kind_eligible", -1))
    expected_searched_pair_count = min(len(expected), MAX_PAIRS_PER_SEARCH)
    if searched_pair_count != expected_searched_pair_count:
        raise ValueError(
            "{} {} claims {} searched pairs; expected {} from {} eligible "
            "pairs with max-pairs {}".format(
                height, kind, searched_pair_count, expected_searched_pair_count,
                len(expected), MAX_PAIRS_PER_SEARCH))
    if doc.get("search_exhaustive") is not True:
        raise ValueError("{} {} A4 search is not exhaustive".format(height, kind))
    if int(doc.get("pairs_evaluated_count", -1)) != searched_pair_count:
        raise ValueError("{} {} A4 outcome accounting is incomplete".format(
            height, kind))

    expected_keys = {pair_key(record) for record in expected}
    candidates = doc.get("candidates", [])
    seen = set()
    for candidate in candidates:
        key = pair_key(candidate)
        if key not in expected_keys:
            raise ValueError("placement candidate not in gated pair inventory: {}".format(key))
        if key in seen:
            raise ValueError("duplicate placement candidate: {}".format(key))
        seen.add(key)
        if candidate.get("kind") != kind:
            raise ValueError("placement candidate kind mismatch")
        if abs(float(candidate["floor1_f2f"]) - float(height)) > 0.001:
            raise ValueError("placement candidate height mismatch")
        if int(candidate.get("feasible_placement_count", 0)) < 1:
            raise ValueError("selected placement has no feasible alternatives")
        if candidate.get("doorway_clearance_gate_pass") is not True:
            raise ValueError("placement candidate failed doorway clearance")
        if any(candidate.get(floor + "_doorway_clearance", {}).get("pass") is not True
               for floor in ("floor1", "floor2")):
            raise ValueError("placement candidate includes a blocked doorway")
        validate(candidate, types)
    rejected = (doc.get("doorway_clearance_rejections", [])
                + doc.get("strict_placement_rejections", []))
    rejected_keys = [pair_key(record) for record in rejected]
    outcome_keys = list(seen) + rejected_keys
    if len(outcome_keys) != len(set(outcome_keys)):
        raise ValueError("{} {} duplicate pair outcome".format(height, kind))
    if set(outcome_keys) != expected_keys:
        raise ValueError("{} {} did not retain one outcome for every pair".format(
            height, kind))
    return candidates


def validate_approved_building(building: Path) -> str:
    decision = load(building / "decision.json")
    build = load(building / "reports" / "build.json")
    isaac = load(building / "reports" / "isaac_validation.json")
    if decision.get("decision") != "approve":
        raise ValueError("approved-building input lacks approve decision: " + str(building))
    if build.get("status") != "pass" or isaac.get("machine_status") != "pass":
        raise ValueError("approved building lacks current machine pass: " + str(building))
    failed = [name for name, passed in isaac.get("acceptance_gates", {}).items()
              if passed is not True]
    if failed:
        raise ValueError("approved building has failed Isaac gates: " + ", ".join(failed))
    for stem in ("01_overview", "02_stairwell", "03_arrival", "04_eyelevel",
                 "05_topdown"):
        for suffix in ("_render.png", "_collision.png"):
            path = building / "renders" / (stem + suffix)
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("approved building missing render: " + path.name)
    if decision.get("batch_generation_authorized") is not False:
        raise ValueError("approved first-pair decision must remain batch-false")
    return str(decision["BID"])


def manifest_record(candidate: dict) -> dict:
    x0, y0, x1, y1 = candidate["rect"]
    def minimum_doorway(storey: str):
        rows = candidate.get(storey + "_doorway_clearance", {}).get("doorways", [])
        return min((float(row["minimum_clear_opening_m"]) for row in rows),
                   default=None)
    return {
        "BID": bid_for(candidate),
        "floor1": candidate["floor1"],
        "floor2": candidate["floor2"],
        "kind": candidate["kind"],
        "height_m": candidate["floor1_f2f"],
        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "rotation_deg": candidate["rotation_deg"],
        "room": candidate["room"],
        "scene_type": candidate["scene_type"],
        "doorway_clearance_gate_pass": candidate.get(
            "doorway_clearance_gate_pass") is True,
        "floor1_min_doorway_clearance_m": minimum_doorway("floor1"),
        "floor2_min_doorway_clearance_m": minimum_doorway("floor2"),
    }


def _candidate_quality(candidate: dict) -> tuple[float, float, str]:
    rank = candidate.get("selection_rank_metrics", {})
    return (
        float(rank.get("minimum_corridor_extra_clearance_m", 0.0)),
        float(candidate.get("footprint_iou", 0.0)),
        bid_for(candidate),
    )


def select_review_batch(
    package: dict, excluded_bids: set[str], review_limit: int,
) -> None:
    """Choose a diverse deterministic H1 batch while retaining full inventory."""
    all_buildings = list(package["buildings"])
    all_docs = package["_candidate_docs"]
    eligible = [record for record in all_buildings
                if record["BID"] not in excluded_bids]
    excluded = [record for record in all_buildings
                if record["BID"] in excluded_bids]
    by_bid = {record["BID"]: record for record in eligible}
    remaining = set(by_bid)
    selected: list[str] = []
    cell_counts: Counter = Counter()
    pair_counts: Counter = Counter()
    while remaining and len(selected) < review_limit:
        def key(bid: str) -> tuple:
            record = by_bid[bid]
            candidate = all_docs[bid]
            cell = (record["height_m"], record["kind"])
            pair = (record["floor1"], record["floor2"])
            quality = _candidate_quality(candidate)
            return (
                cell_counts[cell],
                pair_counts[pair],
                -quality[0],
                -quality[1],
                quality[2],
            )
        chosen = min(remaining, key=key)
        selected.append(chosen)
        remaining.remove(chosen)
        record = by_bid[chosen]
        cell_counts[(record["height_m"], record["kind"])] += 1
        pair_counts[(record["floor1"], record["floor2"])] += 1

    chosen_set = set(selected)
    not_selected = [record for record in eligible if record["BID"] not in chosen_set]
    package["full_strict_candidate_inventory_count"] = len(all_buildings)
    package["excluded_prior_batch_candidate_count"] = len(excluded)
    package["excluded_prior_batch_candidates"] = sorted(
        record["BID"] for record in excluded)
    package["eligible_new_candidate_count_before_h1_limit"] = len(eligible)
    package["strict_candidates_not_selected_count"] = len(not_selected)
    package["strict_candidates_not_selected"] = [
        {"BID": record["BID"], "reason": "H1_BATCH_SIZE_LIMIT_NOT_A_FAILURE"}
        for record in sorted(not_selected, key=lambda row: row["BID"])
    ]
    package["review_selection_policy"] = (
        "iterative minimum coverage count by (height,shape), then minimum scene-pair "
        "reuse, then maximum A4 corridor clearance and footprint IoU"
    )
    package["review_candidate_count"] = len(selected)
    package["new_candidate_count"] = len(selected)
    package["buildings"] = [by_bid[bid] for bid in selected]
    package["_candidate_docs"] = {bid: all_docs[bid] for bid in selected}
    package["status"] = "H1_PENDING" if len(selected) == review_limit else "H1_INSUFFICIENT_CANDIDATES"


def validate_floorplan_bundle(bundle: Path, candidate: dict) -> dict:
    """Prove that both required A5 plans exist and describe this candidate."""
    image_paths = [bundle / "floorplan_floor1.png",
                   bundle / "floorplan_floor2.png"]
    for image_path in image_paths:
        if not image_path.is_file() or image_path.stat().st_size == 0:
            raise ValueError("A5 candidate missing floorplan: " + str(image_path))
        with Image.open(image_path) as image:
            image.verify()
        with Image.open(image_path) as image:
            if image.size != (FLOORPLAN_WIDTH, FLOORPLAN_HEIGHT):
                raise ValueError("A5 floorplan has unexpected dimensions: " + str(image_path))
    plan = load(bundle / "placement.json")
    if plan.get("placement_contract_version") != 3:
        raise ValueError("floorplan bundle uses stale placement contract")
    if plan.get("doorway_clearance_contract_version") != 2:
        raise ValueError("floorplan bundle omitted doorway-clearance evidence")
    if plan.get("corridor_obstacle_source") != "all_obstacle_boxes_walk_prism":
        raise ValueError("floorplan bundle omitted visual-only obstacles")
    if abs(float(plan.get("corridor_m", -1)) - CORRIDOR_M) > 1e-9:
        raise ValueError("floorplan bundle changed the 1.2 m corridor")
    if abs(float(plan.get("corridor_body_radius_m", -1)) - BODY_RADIUS_M) > 1e-9:
        raise ValueError("floorplan bundle changed the 0.22 m body radius")
    if int(plan.get("candidate_count", 0)) < 1:
        raise ValueError("floorplan bundle contains no strict candidate")
    selected = plan.get("selected", {})
    if selected.get("doorway_clearance_gate_pass") is not True:
        raise ValueError("floorplan selected a doorway-blocked candidate")
    for field in ("floor1", "floor2", "kind", "room", "rotation_deg"):
        if selected.get(field) != candidate.get(field):
            raise ValueError("floorplan selected {} differs from A5 candidate".format(field))
    if [float(v) for v in selected.get("rect", [])] != [
            float(v) for v in candidate["rect"]]:
        raise ValueError("floorplan selected rect differs from A5 candidate")
    if float(selected.get("floor2_overhang_ratio", 1.0)) > 0.12:
        raise ValueError("floorplan selected candidate exceeds Floor-2 overhang threshold")
    overlap = plan.get("selected_projection_exclusion_overlap", {})
    for field in ("floor1_count", "floor2_count"):
        if not isinstance(overlap.get(field), int) or overlap[field] < 0:
            raise ValueError("floorplan projection-overlap audit is missing: " + field)
    required = bool(overlap["floor1_count"] or overlap["floor2_count"])
    if plan.get("gpu_spatial_review_required") is not required:
        raise ValueError("floorplan GPU spatial-review policy is inconsistent")
    expected_policy = ("GPU_3D_REQUIRED_BEFORE_PLACEMENT_APPROVAL" if required else
                       "CPU_2D_CLEAR_AWAITING_HUMAN_PLACEMENT_REVIEW")
    if plan.get("placement_approval_policy") != expected_policy:
        raise ValueError("floorplan placement-approval policy is inconsistent")
    dependencies = plan.get("source_dependency_audit", {})
    for floor in ("floor1", "floor2"):
        record = dependencies.get(floor, {})
        if record.get("dependency_contract_version") != 2:
            raise ValueError("source dependency audit contract is stale: " + floor)
        if record.get("scene") != candidate[floor]:
            raise ValueError("source dependency audit scene mismatch: " + floor)
        if not isinstance(record.get("missing_asset_count"), int):
            raise ValueError("source dependency audit missing count: " + floor)
        if len(record.get("missing_assets", [])) != record["missing_asset_count"]:
            raise ValueError("source dependency audit count/list mismatch: " + floor)
        classified = record.get("missing_dependencies")
        if not isinstance(classified, list) or len(classified) != record["missing_asset_count"]:
            raise ValueError("source dependency classifications are incomplete: " + floor)
        if sorted(item.get("path") for item in classified) != record["missing_assets"]:
            raise ValueError("source dependency classification paths differ: " + floor)
        for item in classified:
            if item.get("category") not in DEPENDENCY_CATEGORIES:
                raise ValueError("invalid dependency category: " + str(item.get("category")))
            if item.get("critical") is not (item["category"] != "noncritical_metadata"):
                raise ValueError("dependency criticality/category mismatch")
            if item.get("candidate_effect") != ("FAIL" if item["critical"] else
                                                  "WARNING_ONLY"):
                raise ValueError("dependency candidate effect is inconsistent")
            if not item.get("classification_basis"):
                raise ValueError("dependency lacks classification basis")
            if not isinstance(item.get("reference_evidence"), list):
                raise ValueError("dependency lacks reference evidence list")
        critical = [item for item in classified if item["critical"]]
        if record.get("critical_missing_dependency_count") != len(critical):
            raise ValueError("critical dependency count mismatch: " + floor)
        if record.get("dependency_gate_pass") is not (not critical):
            raise ValueError("source dependency gate result mismatch: " + floor)
    missing_union = sorted({
        asset for floor in ("floor1", "floor2")
        for asset in dependencies[floor]["missing_assets"]})
    if dependencies.get("unique_missing_assets") != missing_union:
        raise ValueError("source dependency audit unique set mismatch")
    if dependencies.get("unique_missing_asset_count") != len(missing_union):
        raise ValueError("source dependency audit unique count mismatch")
    combined = dependencies.get("missing_dependencies")
    if not isinstance(combined, list) or [item.get("path") for item in combined] != missing_union:
        raise ValueError("combined dependency classifications are incomplete")
    if dependencies.get("dependency_contract_version") != 2:
        raise ValueError("combined dependency contract is stale")
    critical = [item for item in combined if item.get("critical") is True]
    if dependencies.get("critical_missing_dependency_count") != len(critical):
        raise ValueError("combined critical dependency count mismatch")
    if dependencies.get("noncritical_missing_dependency_count") != len(combined) - len(critical):
        raise ValueError("combined noncritical dependency count mismatch")
    if dependencies.get("critical_categories") != sorted({
            item["category"] for item in critical}):
        raise ValueError("combined critical dependency categories mismatch")
    if dependencies.get("all_missing_dependencies_classified") is not True:
        raise ValueError("not every source missing dependency was classified")
    if dependencies.get("dependency_gate_pass") is not (not critical):
        raise ValueError("combined dependency gate result mismatch")
    expected_decision = ("PASS" if not critical else
                         "FAIL_CRITICAL_OR_UNCLASSIFIED_MISSING_DEPENDENCY")
    if dependencies.get("candidate_dependency_decision") != expected_decision:
        raise ValueError("combined dependency decision mismatch")
    if dependencies.get("final_build_dependency_audit_required") is not True:
        raise ValueError("A5 must require a post-build dependency audit")
    legend = plan.get("legend", {})
    for key in ("red_hatch", "green", "blue", "orange", "blue_arrow",
                "purple_arrow", "selected_label"):
        if not legend.get(key):
            raise ValueError("floorplan legend is incomplete: " + key)
    return plan


def render_floorplans(out_dir: Path, buildings: list[dict], candidates: dict[str, dict],
                      python: Path, script: Path,
                      scene_cache: Path | None = None) -> dict[str, dict]:
    """Render the two CPU-only §6.1 plans; any missing plan aborts A5."""
    if buildings and (not script.is_file() or script.stat().st_size == 0):
        raise ValueError("floorplan renderer missing: " + str(script))
    plans = {}
    for manifest in buildings:
        bid = manifest["BID"]
        candidate = candidates[bid]
        bundle = out_dir / bid / "placement"
        bundle.mkdir(parents=True, exist_ok=True)
        source = bundle / "a4_candidate.json"
        source.write_text(json.dumps({"selected": candidate}, indent=2),
                          encoding="utf-8")
        command = [str(python), str(script), "--placements", str(source),
                   "--out-dir", str(bundle)]
        if scene_cache:
            command += ["--scene-cache", str(scene_cache)]
        completed = subprocess.run(command, text=True, capture_output=True)
        execution = {
            "BID": bid, "command": command, "returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr,
        }
        (bundle / "floorplan_execution.json").write_text(
            json.dumps(execution, indent=2), encoding="utf-8")
        if completed.returncode:
            raise RuntimeError("floorplan renderer failed for {}: {}".format(
                bid, completed.stderr.strip()))
        plans[bid] = validate_floorplan_bundle(bundle, candidate)
    return plans


def write_scene_dependency_inventory(out_dir: Path, scene_cache: Path,
                                     pair_inventory: list[dict]) -> dict:
    """Write every missing dependency in every pair-inventory scene."""
    cache = load(scene_cache)
    if cache.get("cache_contract_version") not in (2, 3):
        raise ValueError("cannot export stale scene dependency inventory")
    fields = (
        "scene", "path", "category", "critical", "candidate_effect",
        "classification_basis", "reference_evidence",
    )
    rows = []
    scene_gate_failures = []
    for scene, cached in sorted(cache.get("scenes", {}).items()):
        audit = cached.get("dependencies", {})
        if audit.get("dependency_contract_version") != 2:
            raise ValueError("scene dependency contract is stale: " + scene)
        records = audit.get("missing_dependencies")
        if not isinstance(records, list):
            raise ValueError("scene dependency classifications absent: " + scene)
        if len(records) != audit.get("missing_asset_count"):
            raise ValueError("scene dependency classification count mismatch: " + scene)
        for item in records:
            if item.get("category") not in DEPENDENCY_CATEGORIES:
                raise ValueError("invalid scene dependency category: " + scene)
            if item.get("critical") is not (item["category"] != "noncritical_metadata"):
                raise ValueError("scene dependency criticality mismatch: " + scene)
            rows.append({
                "scene": scene, "path": item["path"],
                "category": item["category"], "critical": item["critical"],
                "candidate_effect": item["candidate_effect"],
                "classification_basis": item["classification_basis"],
                "reference_evidence": json.dumps(item["reference_evidence"]),
            })
        if audit.get("dependency_gate_pass") is not True:
            scene_gate_failures.append(scene)
    with (out_dir / "scene_missing_dependencies.csv").open(
            "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
    counts = Counter(row["category"] for row in rows)
    summary = {
        "dependency_contract_version": 2,
        "scene_count": len(cache.get("scenes", {})),
        "missing_dependency_record_count": len(rows),
        "unique_missing_dependency_count": len({row["path"] for row in rows}),
        "classification_counts": dict(sorted(counts.items())),
        "critical_record_count": sum(row["critical"] is True for row in rows),
        "noncritical_record_count": sum(row["critical"] is False for row in rows),
        "dependency_failed_scene_count": len(scene_gate_failures),
        "dependency_failed_scenes": scene_gate_failures,
        "all_missing_dependencies_classified": True,
        "batch_generation_authorized": False,
    }
    (out_dir / "scene_dependency_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    pair_fields = (
        "floor1", "floor2", "kind", "height_m",
        "floor1_dependency_gate_pass", "floor2_dependency_gate_pass",
        "floor1_missing_dependency_count", "floor2_missing_dependency_count",
        "critical_dependency_categories", "pair_dependency_gate_pass",
        "candidate_effect",
    )
    with (out_dir / "pair_dependency_eligibility.csv").open(
            "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=pair_fields, lineterminator="\n")
        writer.writeheader()
        for pair in sorted(pair_inventory, key=lambda item: (
                item["floor1"], item["floor2"], item["kind"])):
            f1 = cache["scenes"][pair["floor1"]]["dependencies"]
            f2 = cache["scenes"][pair["floor2"]]["dependencies"]
            passed = (f1["dependency_gate_pass"] is True
                      and f2["dependency_gate_pass"] is True)
            writer.writerow({
                "floor1": pair["floor1"], "floor2": pair["floor2"],
                "kind": pair["kind"], "height_m": pair["floor1_f2f"],
                "floor1_dependency_gate_pass": f1["dependency_gate_pass"],
                "floor2_dependency_gate_pass": f2["dependency_gate_pass"],
                "floor1_missing_dependency_count": f1["missing_asset_count"],
                "floor2_missing_dependency_count": f2["missing_asset_count"],
                "critical_dependency_categories": json.dumps(sorted(
                    set(f1["critical_categories"] + f2["critical_categories"]))),
                "pair_dependency_gate_pass": passed,
                "candidate_effect": "ELIGIBLE" if passed else "FAIL",
            })
    summary["pair_kind_record_count"] = len(pair_inventory)
    summary["dependency_clean_pair_kind_record_count"] = sum(
        cache["scenes"][pair["floor1"]]["dependencies"]["dependency_gate_pass"]
        and cache["scenes"][pair["floor2"]]["dependencies"]["dependency_gate_pass"]
        for pair in pair_inventory)
    summary["dependency_failed_pair_kind_record_count"] = (
        len(pair_inventory) - summary["dependency_clean_pair_kind_record_count"])
    (out_dir / "scene_dependency_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def compile_package(pair_doc: dict, type_doc: dict, searches: list[tuple[float, str, dict]],
                    approved_bids: set[str], target_count: int,
                    asset_coverage: dict | None = None) -> dict:
    inventory = strict_pair_inventory(pair_doc)
    types = type_doc["scenes"]
    by_cell: dict[tuple[str, str], list[dict]] = {}
    for record in inventory:
        by_cell.setdefault((height_key(record["floor1_f2f"]), record["kind"]), []).append(record)

    covered: dict[tuple[str, str], dict] = {}
    candidate_by_bid: dict[str, dict] = {}
    asset_by_cell: dict[tuple[str, str], dict] = {}
    rejected_asset_cells: dict[tuple[str, str], dict] = {}
    if asset_coverage is not None:
        for asset in asset_coverage.get("asset_evidence", []):
            cell = (height_key(asset["height_m"]), asset["kind"])
            if cell in asset_by_cell:
                raise ValueError("duplicate stair asset audit for cell {}".format(cell))
            gates = asset.get("strict_collider_normalization_gates", {})
            actual_pass = all(gates.get(name) is True for name in STRICT_COLLIDER_GATES)
            if asset.get("strict_collider_normalization_pass") is not actual_pass:
                raise ValueError("stair collider audit summary mismatch: {}".format(cell))
            if asset.get("post_A5_build_blocked") is not (not actual_pass):
                raise ValueError("stair build-block summary mismatch: {}".format(cell))
            asset_by_cell[cell] = asset
        for rejection in asset_coverage.get("missing_asset_cells", []):
            cell = (height_key(rejection["height_m"]), rejection["kind"])
            rejected_asset_cells[cell] = rejection
    for height, kind, doc in searches:
        cell = (height_key(height), kind)
        if cell in covered:
            raise ValueError("duplicate search document for cell {}".format(cell))
        expected = by_cell.get(cell, [])
        if asset_coverage is not None and cell not in asset_by_cell:
            raise ValueError("searched cell lacks stair asset audit: {}".format(cell))
        candidates = validate_search_document(doc, height, kind, expected, types)
        covered[cell] = {
            "eligible": len(expected),
            "searched": int(doc["pairs_height_and_kind_eligible"]),
            "candidates": len(candidates),
        }
        for candidate in candidates:
            bid = bid_for(candidate)
            if bid in candidate_by_bid:
                raise ValueError("duplicate BID across placement searches: " + bid)
            candidate_by_bid[bid] = candidate

    coverage_rows = []
    for cell in sorted(by_cell, key=lambda item: (float(item[0]), item[1])):
        height, kind = cell
        records = by_cell[cell]
        if cell in covered:
            state = ("SEARCHED_CURRENT_CONTRACT_EXHAUSTIVE"
                     if len(records) <= MAX_PAIRS_PER_SEARCH
                     else "SEARCH_LIMIT_EXCEEDED_FAIL_CLOSED")
            searched = covered[cell]["searched"]
            found = covered[cell]["candidates"]
            asset = asset_by_cell.get(cell)
            collider_pass = (True if asset is None else
                             asset["strict_collider_normalization_pass"])
            failed_gates = ([] if asset is None else sorted(
                name for name, passed in asset[
                    "strict_collider_normalization_gates"].items() if not passed))
        else:
            state = ("REJECTED_NO_EXACT_FINAL_LANDING_STAIR_ASSET"
                     if cell in rejected_asset_cells else
                     "MISSING_FINAL_LANDING_ASSET_OR_STRICT_A4_SEARCH")
            searched = 0
            found = 0
            collider_pass = False
            failed_gates = ["NO_EXISTING_EXACT_FINAL_LANDING_STAIR_ASSET"]
        coverage_rows.append({
            "height_m": float(height), "kind": kind,
            "eligible_pair_count": len(records), "searched_pair_count": searched,
            "strict_candidate_count": found,
            "strict_collider_normalization_pass": collider_pass,
            "post_A5_build_blocked": not collider_pass,
            "strict_collider_failed_gates": failed_gates,
            "coverage_status": state,
        })

    all_candidates = [manifest_record(candidate) for candidate in candidate_by_bid.values()]
    already_approved = sorted(record["BID"] for record in all_candidates
                              if record["BID"] in approved_bids)
    for record in all_candidates:
        record["prior_human_approved"] = record["BID"] in approved_bids
        asset = asset_by_cell.get((height_key(record["height_m"]), record["kind"]))
        record["strict_collider_normalization_pass"] = (
            True if asset is None else asset["strict_collider_normalization_pass"])
        record["post_A5_build_blocked"] = not record[
            "strict_collider_normalization_pass"]
        record["strict_collider_failed_gates"] = ([] if asset is None else sorted(
            name for name, passed in asset[
                "strict_collider_normalization_gates"].items() if not passed))
    review_candidates = sorted(all_candidates, key=lambda r: (
        -float(r["height_m"]), r["BID"]))
    new_candidate_count = sum(not record["prior_human_approved"]
                              for record in review_candidates)
    missing_cells = [row for row in coverage_rows
                     if row["coverage_status"].startswith("MISSING_")]
    asset_rejected_cells = [row for row in coverage_rows
                            if row["coverage_status"].startswith("REJECTED_")]
    asset_rejected_cell_keys = {
        (height_key(row["height_m"]), row["kind"])
        for row in asset_rejected_cells}
    missing_asset_rejections = []
    for record in inventory:
        cell = (height_key(record["floor1_f2f"]), record["kind"])
        if cell not in asset_rejected_cell_keys:
            continue
        named_record = dict(record)
        named_record.setdefault("scene_type", types[record["floor1"]]["type"])
        missing_asset_rejections.append({
            "candidate_name": bid_for(named_record),
            "floor1": record["floor1"], "floor2": record["floor2"],
            "kind": record["kind"], "height_m": record["floor1_f2f"],
            "reason": "A4_NO_EXISTING_EXACT_FINAL_LANDING_STAIR_ASSET",
        })
    remaining_target = max(0, target_count - len(approved_bids))
    if remaining_target == 0:
        status = "TARGET_ALREADY_MET_NO_BATCH_NEEDED"
    elif missing_cells:
        status = "ASSET_OR_PLACEMENT_COVERAGE_INCOMPLETE"
    elif new_candidate_count < remaining_target:
        status = "INSUFFICIENT_STRICT_PLACEMENTS"
    else:
        status = "AWAITING_HUMAN_APPROVAL"
    return {
        "status": status,
        "batch_generation_authorized": False,
        "target_building_count": target_count,
        "prior_approved_building_count": len(approved_bids),
        "approved_building_count": len(approved_bids),
        "remaining_target_count": remaining_target,
        "gated_pair_kind_inventory_count": len(inventory),
        "coverage_cell_count": len(by_cell),
        "covered_cell_count": len(covered),
        "missing_cell_count": len(missing_cells),
        "asset_rejected_cell_count": len(asset_rejected_cells),
        "classified_coverage_cell_count": len(covered) + len(
            asset_rejected_cells),
        "a4_missing_stair_asset_rejection_count": len(
            missing_asset_rejections),
        "a4_missing_stair_asset_rejections": missing_asset_rejections,
        "strict_candidate_count": len(all_candidates),
        "strict_collider_blocked_candidate_count": sum(
            record["post_A5_build_blocked"] for record in all_candidates),
        "already_approved_candidate_bids": already_approved,
        "review_candidate_count": len(review_candidates),
        "new_candidate_count": new_candidate_count,
        "manifest_tsv_fields": list(MANIFEST_FIELDS),
        "coverage": coverage_rows,
        "buildings": review_candidates,
        "_candidate_docs": candidate_by_bid,
        "_pair_inventory": inventory,
    }


def write_package(out_dir: Path, package: dict, type_doc: dict,
                  floorplan_script: Path | None = None,
                  python: Path = Path(sys.executable),
                  scene_cache: Path | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates = package.pop("_candidate_docs")
    pair_inventory = package.pop("_pair_inventory")
    floorplan_script = (floorplan_script or Path(__file__).with_name(
        "make_floorplans.py")).resolve()
    plans = render_floorplans(out_dir, package["buildings"], candidates,
                              python.resolve(), floorplan_script, scene_cache)
    for manifest in package["buildings"]:
        dependencies = plans[manifest["BID"]]["source_dependency_audit"]
        manifest["dependency_gate_pass"] = dependencies["dependency_gate_pass"]
        manifest["candidate_dependency_decision"] = dependencies[
            "candidate_dependency_decision"]
        manifest["critical_missing_dependency_count"] = dependencies[
            "critical_missing_dependency_count"]
        manifest["critical_dependency_categories"] = dependencies[
            "critical_categories"]
    eligible = sum(record["dependency_gate_pass"] is True
                   for record in package["buildings"])
    clean_prior_approved = [record for record in package["buildings"]
                            if record["prior_human_approved"]
                            and record["dependency_gate_pass"]]
    failed_prior_approved = [record["BID"] for record in package["buildings"]
                             if record["prior_human_approved"]
                             and not record["dependency_gate_pass"]]
    prior_candidates_in_current_pool = sum(
        record["prior_human_approved"] for record in package["buildings"])
    external_prior_approved = (
        package["prior_approved_building_count"] - prior_candidates_in_current_pool)
    if external_prior_approved < 0:
        raise ValueError("current-pool prior approvals exceed validated prior inputs")
    package["approved_building_count"] = (
        external_prior_approved + len(clean_prior_approved))
    package["prior_approved_dependency_failed_bids"] = failed_prior_approved
    package["remaining_target_count"] = max(
        0, package["target_building_count"] - package["approved_building_count"])
    package["dependency_eligible_candidate_count"] = eligible
    package["dependency_failed_candidate_count"] = len(package["buildings"]) - eligible
    package["new_dependency_eligible_candidate_count"] = sum(
        record["dependency_gate_pass"] and not record["prior_human_approved"]
        for record in package["buildings"])
    if package["missing_cell_count"] == 0:
        total_possible = (package["approved_building_count"]
                          + package["new_dependency_eligible_candidate_count"])
        if package["approved_building_count"] >= package["target_building_count"]:
            package["status"] = "TARGET_ALREADY_MET_NO_BATCH_NEEDED"
        elif total_possible >= package["target_building_count"]:
            package["status"] = "AWAITING_HUMAN_APPROVAL"
        else:
            package["status"] = "INSUFFICIENT_DEPENDENCY_CLEAN_STRICT_PLACEMENTS"
    if scene_cache:
        package["scene_dependency_summary"] = write_scene_dependency_inventory(
            out_dir, scene_cache, pair_inventory)
    with (out_dir / "manifest.tsv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, dialect="excel-tab", lineterminator="\n")
        for record in package["buildings"]:
            writer.writerow([record[field] for field in MANIFEST_FIELDS])
    (out_dir / "manifest.json").write_text(
        json.dumps(package, indent=2), encoding="utf-8")

    coverage_fields = ("height_m", "kind", "eligible_pair_count", "searched_pair_count",
                       "strict_candidate_count", "strict_collider_normalization_pass",
                       "post_A5_build_blocked", "strict_collider_failed_gates",
                       "coverage_status")
    with (out_dir / "coverage.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=coverage_fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(package["coverage"])

    review_fields = (
        "BID", "floor1", "floor2", "scene_type", "floor1_type_evidence",
        "floor2_type_evidence", "footprint_IoU", "floor2_overhang_ratio",
        "floor2_overhang_threshold", "floor2_overhang_pass",
        "floor1_type_bearing_rooms", "floor2_type_bearing_rooms",
        "cross_room_vote_pass", "height_m", "height_error_m", "stair_kind",
        "room", "placement_rect", "rotation_deg", "corridor_m",
        "corridor_body_radius_m", "floorplan_floor1", "floorplan_floor2",
        "floor1_selected_projection_exclusion_overlap_count",
        "floor2_selected_projection_exclusion_overlap_count",
        "gpu_spatial_review_required", "placement_approval_policy",
        "floor1_source_missing_asset_count", "floor2_source_missing_asset_count",
        "source_unique_missing_asset_count", "source_missing_assets",
        "all_missing_dependencies_classified", "dependency_gate_pass",
        "candidate_dependency_decision", "critical_missing_dependency_count",
        "critical_dependency_categories", "missing_dependency_classifications",
        "final_build_dependency_audit_required",
        "strict_collider_normalization_pass", "post_A5_build_blocked",
        "strict_collider_failed_gates",
        "approach_corridor_clear", "egress_corridor_clear",
        "precision_clip_gate_pass", "pipeline_status", "human_decision",
        "doorway_clearance_gate_pass", "floor1_min_doorway_clearance_m",
        "floor2_min_doorway_clearance_m", "review_notes", "prior_human_approved",
    )
    types = type_doc["scenes"]
    with (out_dir / "candidate_review.csv").open(
            "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=review_fields, lineterminator="\n")
        writer.writeheader()
        for manifest in package["buildings"]:
            candidate = candidates[manifest["BID"]]
            plan = plans[manifest["BID"]]
            projection = plan["selected_projection_exclusion_overlap"]
            dependencies = plan["source_dependency_audit"]
            writer.writerow({
                "BID": manifest["BID"], "floor1": manifest["floor1"],
                "floor2": manifest["floor2"], "scene_type": manifest["scene_type"],
                "floor1_type_evidence": evidence_text(types[manifest["floor1"]]),
                "floor2_type_evidence": evidence_text(types[manifest["floor2"]]),
                "footprint_IoU": candidate["footprint_iou"],
                "floor2_overhang_ratio": candidate["floor2_overhang_ratio"],
                "floor2_overhang_threshold": 0.12,
                "floor2_overhang_pass": float(
                    candidate["floor2_overhang_ratio"]) <= 0.12,
                "floor1_type_bearing_rooms": json.dumps(
                    candidate.get("floor1_type_bearing_rooms", [])),
                "floor2_type_bearing_rooms": json.dumps(
                    candidate.get("floor2_type_bearing_rooms", [])),
                "cross_room_vote_pass": bool(
                    candidate.get("floor1_cross_room_vote_pass")
                    and candidate.get("floor2_cross_room_vote_pass")),
                "height_m": manifest["height_m"],
                "height_error_m": candidate["height_error_m"],
                "stair_kind": manifest["kind"],
                "room": manifest["room"],
                "placement_rect": json.dumps(candidate["rect"]),
                "rotation_deg": manifest["rotation_deg"],
                "corridor_m": CORRIDOR_M,
                "corridor_body_radius_m": BODY_RADIUS_M,
                "floorplan_floor1": manifest["BID"] + "/placement/floorplan_floor1.png",
                "floorplan_floor2": manifest["BID"] + "/placement/floorplan_floor2.png",
                "floor1_selected_projection_exclusion_overlap_count":
                    projection["floor1_count"],
                "floor2_selected_projection_exclusion_overlap_count":
                    projection["floor2_count"],
                "gpu_spatial_review_required": plan["gpu_spatial_review_required"],
                "placement_approval_policy": plan["placement_approval_policy"],
                "floor1_source_missing_asset_count":
                    dependencies["floor1"]["missing_asset_count"],
                "floor2_source_missing_asset_count":
                    dependencies["floor2"]["missing_asset_count"],
                "source_unique_missing_asset_count":
                    dependencies["unique_missing_asset_count"],
                "source_missing_assets": json.dumps(
                    dependencies["unique_missing_assets"]),
                "all_missing_dependencies_classified":
                    dependencies["all_missing_dependencies_classified"],
                "dependency_gate_pass": dependencies["dependency_gate_pass"],
                "candidate_dependency_decision":
                    dependencies["candidate_dependency_decision"],
                "critical_missing_dependency_count":
                    dependencies["critical_missing_dependency_count"],
                "critical_dependency_categories": json.dumps(
                    dependencies["critical_categories"]),
                "missing_dependency_classifications": json.dumps(
                    dependencies["missing_dependencies"]),
                "final_build_dependency_audit_required": True,
                "strict_collider_normalization_pass": manifest[
                    "strict_collider_normalization_pass"],
                "post_A5_build_blocked": manifest["post_A5_build_blocked"],
                "strict_collider_failed_gates": json.dumps(
                    manifest["strict_collider_failed_gates"]),
                "approach_corridor_clear": candidate["approach_clear"],
                "egress_corridor_clear": candidate["egress_clear"],
                "precision_clip_gate_pass": candidate["precision_clip_gate_pass"],
                "doorway_clearance_gate_pass": candidate[
                    "doorway_clearance_gate_pass"],
                "floor1_min_doorway_clearance_m": manifest[
                    "floor1_min_doorway_clearance_m"],
                "floor2_min_doorway_clearance_m": manifest[
                    "floor2_min_doorway_clearance_m"],
                "pipeline_status": (
                    "FAIL_CRITICAL_OR_UNCLASSIFIED_MISSING_DEPENDENCY"
                    if not dependencies["dependency_gate_pass"] else
                    "FAIL_POST_A5_BUILD_BLOCKED_STRICT_COLLIDER_NORMALIZATION"
                    if manifest["post_A5_build_blocked"] else
                    "GPU_SPATIAL_REVIEW_REQUIRED_BEFORE_PLACEMENT_APPROVAL"
                    if plan["gpu_spatial_review_required"] else
                    "AWAITING_HUMAN_PLACEMENT_APPROVAL"),
                "human_decision": "", "review_notes": "",
                "prior_human_approved": manifest["prior_human_approved"],
            })

    dependency_fields = (
        "BID", "floor", "scene", "path", "category", "critical",
        "candidate_effect", "classification_basis", "reference_evidence",
    )
    with (out_dir / "missing_dependencies.csv").open(
            "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=dependency_fields, lineterminator="\n")
        writer.writeheader()
        for manifest in package["buildings"]:
            dependencies = plans[manifest["BID"]]["source_dependency_audit"]
            for floor in ("floor1", "floor2"):
                source = dependencies[floor]
                for item in source["missing_dependencies"]:
                    writer.writerow({
                        "BID": manifest["BID"], "floor": floor,
                        "scene": source["scene"], "path": item["path"],
                        "category": item["category"], "critical": item["critical"],
                        "candidate_effect": item["candidate_effect"],
                        "classification_basis": item["classification_basis"],
                        "reference_evidence": json.dumps(item["reference_evidence"]),
                    })

    coverage_lines = [
        "| height | kind | eligible pairs | searched pairs | strict candidates | collider ready | build blocked | status |",
        "|---:|---|---:|---:|---:|---|---|---|",
    ]
    for row in package["coverage"]:
        coverage_lines.append("| {height_m:.2f} | {kind} | {eligible_pair_count} | "
                              "{searched_pair_count} | {strict_candidate_count} | "
                              "{strict_collider_normalization_pass} | "
                              "{post_A5_build_blocked} | "
                              "{coverage_status} |".format(**row))
    report = """# H1 placement review package

**Status: {status}**

- Fresh batch generation authorization: **{batch_authorized}**
- H1 placement approval: **PENDING**; Stage B remains blocked
- Target buildings: {target}; already approved: {approved}; remaining: {remaining}
- Prior approvals before dependency v2 re-audit: {prior_approved}; prior approvals now dependency-FAIL: {prior_failed}
- Gated pair/kind inventory: {inventory}
- Coverage cells classified: {classified}/{cells}; searched: {covered}; asset-rejected: {asset_rejected}; missing: {missing}
- Strict candidates found: {strict}; new candidates awaiting review: {new}
- Dependency-clean candidates: {dep_ok}; dependency-FAIL candidates: {dep_fail}

The authorized CPU-only A1--A4 placement search is complete for the reported expanded
scene pool and every listed height/kind cell records whether a current strict
A4 search exists. No staircase asset was generated in this run, no building
was assembled, and no Isaac validation was invoked. An empty `manifest.tsv`
is intentional when no dependency-clean candidate exists.

This package now awaits human H1 placement review. The fresh batch authorization
does not substitute for H1 approval, so building assembly and Isaac validation
remain blocked. See `AUTHORIZATION_REQUIRED.md`.

## Coverage

{coverage}
""".format(
        status=package["status"],
        batch_authorized=("YES" if package.get("batch_generation_authorized") else "NO"),
        target=package["target_building_count"],
        approved=package["approved_building_count"],
        prior_approved=package["prior_approved_building_count"],
        prior_failed=len(package["prior_approved_dependency_failed_bids"]),
        remaining=package["remaining_target_count"],
        inventory=package["gated_pair_kind_inventory_count"],
        covered=package["covered_cell_count"], cells=package["coverage_cell_count"],
        classified=package["classified_coverage_cell_count"],
        asset_rejected=package["asset_rejected_cell_count"],
        missing=package["missing_cell_count"], strict=package["strict_candidate_count"],
        new=package["new_candidate_count"],
        dep_ok=package["dependency_eligible_candidate_count"],
        dep_fail=package["dependency_failed_candidate_count"],
        coverage="\n".join(coverage_lines))
    (out_dir / "A5_REVIEW.md").write_text(report, encoding="utf-8")
    (out_dir / "H1_REVIEW.md").write_text(report, encoding="utf-8")
    authorization_note = """# H1 placement approval required

Fresh authorization exists to generate this new batch, but this CPU-only review
package has not received H1 placement approval.  No building assembly, Isaac
validation, GPU work, or training may begin from this package yet.

Before any next stage:

- review both Floor 1 and Floor 2 plans for every candidate;
- record an explicit human decision in `candidate_review.csv`;
- keep the 1.2 m corridor and 0.22 m body radius unchanged;
- record the named candidates approved to advance.

The package records `batch_generation_authorized = true`,
`h1_placement_approved = false`, and `stage_b_blocked_pending_h1 = true`.
"""
    (out_dir / "AUTHORIZATION_REQUIRED.md").write_text(
        authorization_note, encoding="utf-8")
    decision_template = {
        "decision_contract_version": 1,
        "decision_scope": "selected_candidates_build_and_gpu_evidence_only",
        "authorization_source": "PENDING_EXPLICIT_USER_REVIEW",
        "a5_package_pass_sha256": "PENDING_UNTIL_A5_PACKAGE_PASS_EXISTS",
        "automatic_batch_generation_authorized": False,
        "candidates": [
            {
                "BID": record["BID"],
                "decision": ("PENDING" if record["dependency_gate_pass"] and not
                             record["post_A5_build_blocked"] else "REJECT_REQUIRED"),
                "review_notes": (
                    "Critical or unclassified source dependency; cannot be overridden at A5"
                    if not record["dependency_gate_pass"] else
                    "Strict collider normalization failed; post-A5 build is blocked"
                    if record["post_A5_build_blocked"] else ""),
                "prior_human_approved": record["prior_human_approved"],
            }
            for record in package["buildings"]
        ],
    }
    (out_dir / "candidate_decisions.pending.json").write_text(
        json.dumps(decision_template, indent=2) + "\n", encoding="utf-8")
    rows = "".join("<tr><td>{:.2f}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
        float(row["height_m"]), html.escape(str(row["kind"])),
        int(row["eligible_pair_count"]), int(row["searched_pair_count"]),
        int(row["strict_candidate_count"]),
        str(row["strict_collider_normalization_pass"]),
        str(row["post_A5_build_blocked"]),
        html.escape(str(row["coverage_status"]))) for row in package["coverage"])
    candidate_cards = "".join(
        "<section class='{dep_class}'><h2>{bid}</h2><p>Prior human approval: "
        "<b>{prior}</b>. <b>Dependency gate: "
        "{dep_decision}</b>. Critical/unclassified: {critical}; categories: "
        "{categories}. <b>Strict collider ready: {collider}; build blocked: "
        "{blocked}</b>.</p><p>Floor 2 overhang: {overhang:.1%} (limit 12%). "
        "Source missing dependencies: {missing}; post-build dependency audit required.</p>{warning}"
        "<div class='plans'><figure><img src='{bid}/placement/floorplan_floor1.png' "
        "alt='{bid} Floor 1 plan'><figcaption>Floor 1</figcaption></figure>"
        "<figure><img src='{bid}/placement/floorplan_floor2.png' "
        "alt='{bid} Floor 2 plan'><figcaption>Floor 2</figcaption></figure></div></section>".format(
            bid=html.escape(manifest["BID"]),
            prior=("YES (re-audited under v2)" if manifest["prior_human_approved"] else "NO"),
            overhang=float(candidates[manifest["BID"]]["floor2_overhang_ratio"]),
            missing=plans[manifest["BID"]]["source_dependency_audit"][
                "unique_missing_asset_count"],
            dep_class=("" if manifest["dependency_gate_pass"] else "dependency-fail"),
            dep_decision=html.escape(manifest["candidate_dependency_decision"]),
            critical=manifest["critical_missing_dependency_count"],
            categories=html.escape(", ".join(manifest[
                "critical_dependency_categories"]) or "none"),
            collider=manifest["strict_collider_normalization_pass"],
            blocked=manifest["post_A5_build_blocked"],
            warning=(
                "<p class='warn'>Selected area overlaps a red 2D exclusion projection. "
                "GPU 3D spatial evidence is required before placement approval.</p>"
                if plans[manifest["BID"]]["gpu_spatial_review_required"] else
                "<p>Selected area is clear of red 2D exclusion projections.</p>"))
        for manifest in package["buildings"] if manifest["BID"] in plans)
    page = """<!doctype html><html><head><meta charset='utf-8'><title>A5 batch preflight</title>
<style>body{{font:15px system-ui;max-width:1400px;margin:auto;padding:24px}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left}}.warn{{color:#a15c00;font-weight:800}}.dependency-fail{{border:4px solid #b42318;padding:12px;background:#fff2f0}}.dependency-fail>h2,.dependency-fail>b{{color:#b42318}}.plans{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}figure{{margin:0}}img{{max-width:100%;height:auto}}</style></head><body>
<h1>H1 placement review</h1><p class='warn'>{status}</p><p>Fresh batch generation authorization: <b>{batch_authorized}</b>; H1 placement approval: <b>PENDING</b>; Stage B: <b>BLOCKED</b>.</p>
<p>Approved {approved}/{target}; new candidates {new}; missing coverage cells {missing}.</p>
<p>Dependency-clean candidates: {dep_ok}; dependency-FAIL candidates: {dep_fail}. <a href='missing_dependencies.csv'>Candidate dependency list</a> · <a href='scene_missing_dependencies.csv'>All pair-inventory scene dependencies</a> · <a href='pair_dependency_eligibility.csv'>All pair/kind dependency decisions</a>.</p>
<p><a href='AUTHORIZATION_REQUIRED.md'>Review the H1 candidates and locked post-H1 scope</a>. Fresh generation authorization does not replace placement approval.</p>
<table><thead><tr><th>Height</th><th>Kind</th><th>Eligible pairs</th><th>Searched pairs</th><th>Strict candidates</th><th>Collider ready</th><th>Build blocked</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>{candidate_cards}</body></html>""".format(
        status=html.escape(package["status"]),
        batch_authorized=("YES" if package.get("batch_generation_authorized") else "NO"),
        approved=package["approved_building_count"],
        target=package["target_building_count"], new=package["new_candidate_count"],
        dep_ok=package["dependency_eligible_candidate_count"],
        dep_fail=package["dependency_failed_candidate_count"],
        missing=package["missing_cell_count"], rows=rows,
        candidate_cards=candidate_cards)
    (out_dir / "index.html").write_text(page, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--types", type=Path, required=True)
    ap.add_argument("--search", action="append", nargs=3,
                    metavar=("HEIGHT", "KIND", "PLACEMENT_JSON"), default=[])
    ap.add_argument("--approved-building", action="append", type=Path, default=[])
    ap.add_argument("--invalidate-prior-approvals", action="store_true",
                    help="do not carry historical approvals into the review package")
    ap.add_argument("--target-count", type=int, default=30)
    ap.add_argument("--review-limit", type=int, default=0)
    ap.add_argument("--exclude-bids", type=Path,
                    help="JSON list of BIDs already delivered in another batch")
    ap.add_argument("--authorization", type=Path,
                    help="fresh authorization record for this new batch")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--floorplan-script", type=Path,
                    default=Path(__file__).with_name("make_floorplans.py"))
    ap.add_argument("--python", type=Path, default=Path(sys.executable))
    ap.add_argument("--scene-cache", type=Path)
    ap.add_argument("--stair-asset-coverage", type=Path, required=True)
    args = ap.parse_args()
    if args.target_count < 1:
        raise SystemExit("--target-count must be positive")
    searches = [(float(height), kind, load(Path(path)))
                for height, kind, path in args.search]
    approved = {validate_approved_building(path) for path in args.approved_building}
    if args.invalidate_prior_approvals:
        approved = set()
    package = compile_package(load(args.pairs), load(args.types), searches,
                              approved, args.target_count,
                              load(args.stair_asset_coverage))
    exclusions = set(load(args.exclude_bids).get("BIDs", [])) if args.exclude_bids else set()
    if args.review_limit:
        select_review_batch(package, exclusions, args.review_limit)
    elif exclusions:
        select_review_batch(package, exclusions, len(package["buildings"]))
    doorway_rejections = [rejection for _height, _kind, document in searches
                          for rejection in document.get("doorway_clearance_rejections", [])]
    package["a4_doorway_clearance_rejection_count"] = len(doorway_rejections)
    package["a4_doorway_clearance_rejections"] = doorway_rejections
    strict_rejections = [rejection for _height, _kind, document in searches
                         for rejection in document.get(
                             "strict_placement_rejections", [])]
    package["a4_strict_placement_rejection_count"] = len(strict_rejections)
    package["a4_strict_placement_rejections"] = strict_rejections
    package["all_a4_pair_outcomes_retained"] = all(
        document.get("search_exhaustive") is True
        and int(document.get("pairs_evaluated_count", -1)) == int(
            document.get("pairs_height_and_kind_eligible", -2))
        for _height, _kind, document in searches)
    if args.authorization:
        authorization = load(args.authorization)
        if authorization.get("batch_generation_authorized") is not True:
            raise ValueError("fresh authorization lacks batch_generation_authorized=true")
        if authorization.get("automatic_batch_generation_authorized") is not True:
            raise ValueError("fresh authorization lacks automatic authorization=true")
        if authorization.get("training_authorized") is not False:
            raise ValueError("fresh authorization must keep training_authorized=false")
        package.update({
            "batch_generation_authorized": True,
            "automatic_batch_generation_authorized": True,
            "training_authorized": False,
            "authorization_source": authorization.get("authorization_source"),
            "authorization_record": str(args.authorization.resolve()),
            "h1_placement_approved": False,
            "stage_b_blocked_pending_h1": True,
        })
    if package["buildings"] and args.scene_cache is None:
        raise SystemExit("--scene-cache is required when A5 has new candidates")
    if args.scene_cache:
        validate_scene_cache(args.scene_cache, args.pairs, args.types,
                             package["buildings"])
    write_package(args.out_dir, package, load(args.types), args.floorplan_script,
                  args.python, args.scene_cache)
    print(json.dumps({k: v for k, v in package.items()
                      if k not in ("coverage", "buildings")}, indent=2))


if __name__ == "__main__":
    main()
