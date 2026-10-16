#!/usr/bin/env python3
"""Fail-capable controls for the multi-search A5 compiler."""

from copy import deepcopy
from pathlib import Path
import json
import tempfile

from PIL import Image

from compile_a5_batch import (compile_package, validate_floorplan_bundle,
                              validate_scene_cache, write_package)
from dependency_policy import classify_missing_dependency


def scene(kind="home"):
    return {"type": kind, "home_votes": 4 if kind == "home" else 0,
            "school_votes": 4 if kind == "school" else 0,
            "evidence": {"home": [], "school": []}}


def pair(kind, floor1="f1", floor2="f2", height=3.2):
    return {"floor1": floor1, "floor2": floor2, "kind": kind,
            "usable": True, "pair_gate_passed": True, "floor1_f2f": height}


def candidate(kind="straight"):
    return {"floor1": "f1", "floor2": "f2", "kind": kind,
            "scene_type": "home", "floor1_f2f": 3.2, "height_error_m": 0.0,
            "floor2_overhang_ratio": 0.11, "approach_clear": True,
            "egress_clear": True, "precision_clip_gate_pass": True,
            "placement_contract_version": 3,
            "corridor_obstacle_source": "all_obstacle_boxes_walk_prism",
            "doorway_clearance_gate_pass": True,
            "doorway_clearance_contract_version": 2,
            "floor1_doorway_clearance": {"pass": True},
            "floor2_doorway_clearance": {"pass": True},
            "floor1_cross_room_vote_pass": True,
            "floor2_cross_room_vote_pass": True, "rotation_deg": 0,
            "feasible_placement_count": 1, "rect": [0, 0, 1, 5],
            "room": "r", "footprint_iou": 0.7}


def rejection(kind="straight", floor1="f1", floor2="f2", height=3.2):
    return {"floor1": floor1, "floor2": floor2, "kind": kind,
            "height_m": height, "candidate_name": f"{floor1}_{floor2}_{kind}",
            "reason": "A4_NO_STRICT_PLACEMENT_AFTER_EXHAUSTIVE_GRID_SEARCH"}


def search(kind="straight", candidates=None):
    selected = [candidate(kind)] if candidates is None else candidates
    return {"placement_contract_version": 3,
            "corridor_obstacle_source": "all_obstacle_boxes_walk_prism",
            "doorway_clearance_contract_version": 2,
            "doorway_clearance_rule": (
                "minimum doorway centre-line clearance >= 0.60 m"),
            "corridor_m": 1.2, "corridor_body_radius_m": 0.22,
            "grid_step_m": 0.01, "height_tolerance_m": 0.001,
            "rotations_tested_deg": [0, 90, 180, 270],
            "stair_height_m": 3.2, "pairs_height_and_kind_eligible": 1,
            "pairs_evaluated_count": 1, "search_exhaustive": True,
            "doorway_clearance_rejections": [],
            "strict_placement_rejections": (
                [] if selected else [rejection(kind)]),
            "candidates": selected}


def asset_coverage(kind="straight", passed=True):
    gates = {
        "all_authored_tread_colliders_normalized": passed,
        "source_tread_collision_prims_deactivated": passed,
        "combined_tread_collider_contains_all_boxes": passed,
        "combined_tread_collider_is_static_closed_triangle_mesh": passed,
    }
    return {"asset_evidence": [{
        "height_m": 3.2, "kind": kind,
        "strict_collider_normalization_gates": gates,
        "strict_collider_normalization_pass": passed,
        "post_A5_build_blocked": not passed,
    }]}


def must_fail(fn):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("known-bad A5 input was accepted")


def dependency(path="metadata/readme.txt", category="noncritical_metadata"):
    critical = category != "noncritical_metadata"
    return {"path": path, "category": category, "critical": critical,
            "classification_basis": "unit-test evidence",
            "candidate_effect": "FAIL" if critical else "WARNING_ONLY",
            "reference_evidence": [{"kind": "unit_test"}]}


def source_dependencies(scene_name, record):
    return {"dependency_contract_version": 2, "scene": scene_name,
            "missing_asset_count": 1, "missing_assets": [record["path"]],
            "missing_dependencies": [record],
            "critical_missing_dependency_count": int(record["critical"]),
            "dependency_gate_pass": not record["critical"]}


def combined_dependencies(f1=None, f2=None):
    f1 = f1 or dependency("metadata/readme.txt")
    f2 = f2 or dependency("metadata/license.txt")
    records = sorted((dict(f1, floors=["floor1"]),
                      dict(f2, floors=["floor2"])), key=lambda item: item["path"])
    critical = [record for record in records if record["critical"]]
    return {"floor1": source_dependencies("f1", f1),
            "floor2": source_dependencies("f2", f2),
            "unique_missing_asset_count": 2,
            "unique_missing_assets": sorted([f1["path"], f2["path"]]),
            "missing_dependencies": records,
            "dependency_contract_version": 2,
            "all_missing_dependencies_classified": True,
            "critical_missing_dependency_count": len(critical),
            "noncritical_missing_dependency_count": 2 - len(critical),
            "critical_categories": sorted({r["category"] for r in critical}),
            "dependency_gate_pass": not critical,
            "candidate_dependency_decision": ("PASS" if not critical else
                "FAIL_CRITICAL_OR_UNCLASSIFIED_MISSING_DEPENDENCY"),
            "final_build_dependency_audit_required": True}


def main():
    furniture = classify_missing_dependency(
        "/dataset/objects/thor/Sofa_218_1_mesh/Sofa_218_1_mesh.usda")
    assert furniture["category"] == "furniture_visibility"
    assert furniture["critical"] is True and furniture["candidate_effect"] == "FAIL"
    structure = classify_missing_dependency("/dataset/walls/wall.usda")
    assert structure["category"] == "building_structure" and structure["critical"]
    collision = classify_missing_dependency("/dataset/collision/hull.usda")
    assert collision["category"] == "collision" and collision["critical"]
    unknown = classify_missing_dependency("/dataset/unknown/model.usda")
    assert unknown["category"] == "unclassified_unknown" and unknown["critical"]
    assert "AUTHORIZATION_REQUIRED.md" in (
        Path(__file__).with_name("compile_a5_batch.py").read_text())
    pairs = {"results": [pair(kind) for kind in ("straight", "l_shaped", "u_shaped")]}
    types = {"scenes": {"f1": scene(), "f2": scene()}}
    one = compile_package(pairs, types, [(3.2, "straight", search())], set(), 30)
    assert one["status"] == "ASSET_OR_PLACEMENT_COVERAGE_INCOMPLETE"
    assert one["covered_cell_count"] == 1 and one["missing_cell_count"] == 2
    assert one["new_candidate_count"] == 1
    blocked = compile_package(
        pairs, types, [(3.2, "straight", search())], set(), 30,
        asset_coverage(passed=False))
    assert blocked["strict_collider_blocked_candidate_count"] == 1
    assert blocked["buildings"][0]["post_A5_build_blocked"] is True
    inconsistent_asset = asset_coverage(passed=True)
    inconsistent_asset["asset_evidence"][0]["post_A5_build_blocked"] = True
    must_fail(lambda: compile_package(
        pairs, types, [(3.2, "straight", search())], set(), 30,
        inconsistent_asset))
    bid = one["buildings"][0]["BID"]
    approved = compile_package(pairs, types, [(3.2, "straight", search())], {bid}, 30)
    assert approved["new_candidate_count"] == 0
    assert approved["already_approved_candidate_bids"] == [bid]
    complete = compile_package(pairs, types, [
        (3.2, "straight", search()),
        (3.2, "l_shaped", search("l_shaped", [])),
        (3.2, "u_shaped", search("u_shaped", [])),
    ], {bid}, 1)
    assert complete["status"] == "TARGET_ALREADY_MET_NO_BATCH_NEEDED"
    # Even with the target already met, a fully covered package contains no
    # automatically authorized work.
    assert complete["batch_generation_authorized"] is False
    stale = search(); stale["placement_contract_version"] = 1
    must_fail(lambda: compile_package(pairs, types, [(3.2, "straight", stale)], set(), 30))
    coarse = search(); coarse["grid_step_m"] = 0.02
    must_fail(lambda: compile_package(pairs, types, [(3.2, "straight", coarse)], set(), 30))
    incomplete_rot = search(); incomplete_rot["rotations_tested_deg"] = [0, 90, 180]
    must_fail(lambda: compile_package(
        pairs, types, [(3.2, "straight", incomplete_rot)], set(), 30))
    wrong_count = search(); wrong_count["pairs_height_and_kind_eligible"] = 2
    must_fail(lambda: compile_package(pairs, types, [(3.2, "straight", wrong_count)], set(), 30))
    capped_pairs = {"results": [
        pair("straight", "f1", "f{}".format(index)) for index in range(2, 63)
    ]}
    capped_types = {"scenes": {"f1": scene(), **{
        "f{}".format(index): scene() for index in range(2, 63)
    }}}
    capped_search = search(candidates=[])
    capped_search["pairs_height_and_kind_eligible"] = 61
    capped_search["pairs_evaluated_count"] = 61
    capped_search["strict_placement_rejections"] = [
        rejection("straight", "f1", "f{}".format(index))
        for index in range(2, 63)]
    capped = compile_package(
        capped_pairs, capped_types, [(3.2, "straight", capped_search)], set(), 30)
    assert capped["coverage"][0]["eligible_pair_count"] == 61
    assert capped["coverage"][0]["searched_pair_count"] == 61
    assert capped["coverage"][0]["coverage_status"] == (
        "SEARCHED_CURRENT_CONTRACT_EXHAUSTIVE")
    underreported = deepcopy(capped_search)
    underreported["pairs_height_and_kind_eligible"] = 60
    must_fail(lambda: compile_package(
        capped_pairs, capped_types, [(3.2, "straight", underreported)], set(), 30))
    duplicate = deepcopy(pairs); duplicate["results"].append(pair("straight"))
    must_fail(lambda: compile_package(duplicate, types, [], set(), 30))
    with tempfile.TemporaryDirectory() as temp:
        cache_path = Path(temp) / "cache.json"
        pairs_path = Path(temp) / "pairs.json"
        types_path = Path(temp) / "types.json"
        rooted_pairs = deepcopy(pairs); rooted_pairs["scene_root"] = "/test/scenes"
        rooted_types = deepcopy(types); rooted_types["scene_root"] = "/test/scenes"
        pairs_path.write_text(json.dumps(rooted_pairs), encoding="utf-8")
        types_path.write_text(json.dumps(rooted_types), encoding="utf-8")
        import hashlib
        cache_path.write_text(json.dumps({
            "cache_contract_version": 2,
            "scene_root": "/test/scenes",
            "complete_for_pair_cache": True,
            "pair_cache_sha256": hashlib.sha256(pairs_path.read_bytes()).hexdigest(),
            "type_cache_sha256": hashlib.sha256(types_path.read_bytes()).hexdigest(),
            "scenes": {"f1": {}, "f2": {}},
        }), encoding="utf-8")
        validate_scene_cache(cache_path, pairs_path, types_path,
                             [{"floor1": "f1", "floor2": "f2"}])
        wrong_root = json.loads(cache_path.read_text())
        wrong_root["scene_root"] = "/wrong/scenes"
        cache_path.write_text(json.dumps(wrong_root), encoding="utf-8")
        must_fail(lambda: validate_scene_cache(
            cache_path, pairs_path, types_path,
            [{"floor1": "f1", "floor2": "f2"}]))
        wrong_root["scene_root"] = "/test/scenes"
        cache_path.write_text(json.dumps(wrong_root), encoding="utf-8")
        stale = json.loads(cache_path.read_text()); stale["pair_cache_sha256"] = "0" * 64
        cache_path.write_text(json.dumps(stale), encoding="utf-8")
        must_fail(lambda: validate_scene_cache(
            cache_path, pairs_path, types_path,
            [{"floor1": "f1", "floor2": "f2"}]))
    with tempfile.TemporaryDirectory() as temp:
        bundle = Path(temp)
        selected = candidate()
        for name in ("floorplan_floor1.png", "floorplan_floor2.png"):
            Image.new("RGB", (1200, 900), "white").save(bundle / name)
        (bundle / "placement.json").write_text(json.dumps({
            "placement_contract_version": 3,
            "doorway_clearance_contract_version": 2,
            "corridor_obstacle_source": "all_obstacle_boxes_walk_prism",
            "corridor_m": 1.2,
            "corridor_body_radius_m": 0.22,
            "candidate_count": 1,
            "selected": selected,
            "selected_projection_exclusion_overlap": {
                "floor1_count": 0, "floor2_count": 0,
            },
            "gpu_spatial_review_required": False,
            "placement_approval_policy": "CPU_2D_CLEAR_AWAITING_HUMAN_PLACEMENT_REVIEW",
            "source_dependency_audit": combined_dependencies(),
            "legend": {
                "red_hatch": "exclusion", "green": "candidate",
                "blue": "selected", "orange": "corridor",
                "blue_arrow": "entry", "purple_arrow": "exit",
                "selected_label": "SELECTED #01",
            },
        }), encoding="utf-8")
        validate_floorplan_bundle(bundle, selected)
        (bundle / "floorplan_floor2.png").unlink()
        must_fail(lambda: validate_floorplan_bundle(bundle, selected))
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        renderer = root / "fake_floorplans.py"
        deps = repr(combined_dependencies())
        renderer.write_text("""#!/usr/bin/env python3
import argparse, json
from pathlib import Path
from PIL import Image
p=argparse.ArgumentParser(); p.add_argument('--placements', type=Path); p.add_argument('--out-dir', type=Path); a=p.parse_args()
d=json.loads(a.placements.read_text()); s=d['selected']; a.out_dir.mkdir(parents=True, exist_ok=True)
for n in ('floorplan_floor1.png','floorplan_floor2.png'): Image.new('RGB',(1200,900),'white').save(a.out_dir/n)
(a.out_dir/'placement.json').write_text(json.dumps({'placement_contract_version':3,'doorway_clearance_contract_version':2,'corridor_obstacle_source':'all_obstacle_boxes_walk_prism','corridor_m':1.2,'corridor_body_radius_m':0.22,'candidate_count':1,'selected':s,'selected_projection_exclusion_overlap':{'floor1_count':1,'floor2_count':2},'gpu_spatial_review_required':True,'placement_approval_policy':'GPU_3D_REQUIRED_BEFORE_PLACEMENT_APPROVAL','source_dependency_audit':DEPS,'legend':{'red_hatch':'exclusion','green':'candidates','blue':'selected','orange':'corridor','blue_arrow':'entry','purple_arrow':'exit','selected_label':'SELECTED #01'}}))
""".replace("DEPS", deps), encoding="utf-8")
        package = compile_package(
            pairs, types, [(3.2, "straight", search())], set(), 30)
        out = root / "a5"
        write_package(out, package, types, renderer)
        assert (out / bid / "placement" / "floorplan_floor1.png").is_file()
        assert (out / bid / "placement" / "floorplan_floor2.png").is_file()
        assert (out / "AUTHORIZATION_REQUIRED.md").is_file()
        header = (out / "candidate_review.csv").read_text(encoding="utf-8-sig").splitlines()[0]
        assert "floorplan_floor1" in header and "floor2_overhang_ratio" in header
        assert "gpu_spatial_review_required" in header
        assert "source_unique_missing_asset_count" in header
        assert bid + "/placement/floorplan_floor1.png" in (
            out / "candidate_review.csv").read_text(encoding="utf-8-sig")
        assert "floorplan_floor1.png" in (out / "index.html").read_text()
        review = (out / "candidate_review.csv").read_text(encoding="utf-8-sig")
        assert "GPU_SPATIAL_REVIEW_REQUIRED_BEFORE_PLACEMENT_APPROVAL" in review
        assert "GPU 3D spatial evidence is required" in (out / "index.html").read_text()
        assert "post-build dependency audit required" in (out / "index.html").read_text()
        broken_renderer = root / "broken_floorplans.py"
        broken_renderer.write_text(renderer.read_text().replace(
            "for n in ('floorplan_floor1.png','floorplan_floor2.png'):",
            "for n in ('floorplan_floor1.png',):"), encoding="utf-8")
        broken_package = compile_package(
            pairs, types, [(3.2, "straight", search())], set(), 30)
        must_fail(lambda: write_package(root / "broken", broken_package,
                                        types, broken_renderer))
    print("PASS: A5 coverage, approval exclusion, and negative controls")


if __name__ == "__main__":
    main()
