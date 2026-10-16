#!/usr/bin/env python3
"""Render the two auditable 2-D placement plans required by the review UI."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pxr import Sdf, UsdUtils
from dependency_policy import (DEPENDENCY_CONTRACT_VERSION,
                               classify_missing_dependency)

from pair_scenes import classify_scene, floor2_overhang, scan  # noqa: E402
from pair_scenes import SCENES  # noqa: E402
from pick_placement import corridor_poly  # noqa: E402
from pipeline_contract import (BODY_RADIUS_M, CORRIDOR_HALF_WIDTH_M,
                               CORRIDOR_M)  # noqa: E402
from shapely.geometry import box
from shapely.ops import unary_union


WIDTH, HEIGHT, PAD = 1200, 900, 70


def dependency_reference_evidence(layers, missing):
    """Find fail-auditable layer text sites for every unresolved path."""
    evidence = {path: [] for path in missing}
    for layer in layers:
        identifier = str(layer.realPath or layer.identifier)
        try:
            lines = layer.ExportToString().splitlines()
        except Exception as exc:  # noqa: BLE001
            # The dependency remains unclassified/critical if layer evidence
            # cannot be read. Record the failure instead of silently dropping it.
            for path in missing:
                evidence[path].append({
                    "kind": "layer_evidence_read_error", "layer": identifier,
                    "error": "{}: {}".format(type(exc).__name__, exc),
                })
            continue
        for path in missing:
            needles = {path.replace("\\", "/"), Path(path).name}
            for line_number, line in enumerate(lines, 1):
                normalized_line = line.replace("\\", "/")
                if any(needle and needle in normalized_line for needle in needles):
                    evidence[path].append({
                        "kind": "layer_text_reference", "layer": identifier,
                        "line": line_number,
                        "reference_text": normalized_line.strip()[:1000],
                    })
                    if len(evidence[path]) >= 20:
                        break
    return evidence


def arrow(draw, start, direction, transform, color, label):
    length = 1.0
    end = (start[0] + direction[0] * length,
           start[1] + direction[1] * length)
    a = transform(start); b = transform(end)
    draw.line((a, b), fill=color, width=7)
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    wing = 15
    for delta in (2.55, -2.55):
        q = (b[0] + wing * math.cos(angle + delta),
             b[1] + wing * math.sin(angle + delta))
        draw.line((b, q), fill=color, width=7)
    if label:
        draw.text((b[0] + 8, b[1] - 18), label, fill=color)


def hatched(draw, rect, transform):
    x0, y0 = transform((rect[0], rect[1]))
    x1, y1 = transform((rect[2], rect[3]))
    left, right = sorted((x0, x1)); top, bottom = sorted((y0, y1))
    draw.rectangle((left, top, right, bottom), fill=(255, 218, 218, 210),
                   outline=(185, 25, 25, 255), width=2)
    step = 13
    span = int((right - left) + (bottom - top))
    for offset in range(-int(bottom - top), int(right - left) + step, step):
        xa = max(left, left + offset)
        ya = bottom - max(0, -offset)
        xb = min(right, left + offset + (bottom - top))
        yb = bottom - (xb - (left + offset))
        if xa <= right and xb >= left:
            draw.line((xa, ya, xb, yb), fill=(205, 55, 55, 230), width=2)


def corridor_record(start, direction, backwards=False):
    d = (-direction[0], -direction[1]) if backwards else direction
    poly = corridor_poly(start, d, CORRIDOR_M + BODY_RADIUS_M,
                         CORRIDOR_HALF_WIDTH_M)
    return {"bounds": [float(v) for v in poly.bounds],
            "corridor_m": CORRIDOR_M, "body_radius_m": BODY_RADIUS_M,
            "reserved_length_m": CORRIDOR_M + BODY_RADIUS_M}


def exclusion_rects(scene):
    """Every wall/furniture/door AABB used by the auditable red overlay."""
    rects = list(scene["walls"])
    rects.extend(
        (record["box"][0], record["box"][1],
         record["box"][3], record["box"][4])
        for record in scene.get("obstacle_boxes", [])
        if float(record["box"][5]) > 0.0 and float(record["box"][2]) < 1.75)
    return rects


def positive_xy_overlap(a, b):
    return (min(float(a[2]), float(b[2])) > max(float(a[0]), float(b[0]))
            and min(float(a[3]), float(b[3])) > max(float(a[1]), float(b[1])))


def source_dependency_audit(scene_name):
    """List, classify, and fail-gate every unresolved source dependency."""
    # Keep the install-index path instead of resolving the scene.usda symlink
    # into the versioned scene cache. Scene layers reference ../../../../objects;
    # collapsing the symlink moves that relative anchor under cache/scenes and
    # falsely reports every already-installed object as missing.
    stage = SCENES / scene_name / "scene.usda"
    result = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(stage)))
    if len(result) != 3:
        raise RuntimeError("unexpected dependency audit result: " + str(stage))
    layers, assets, unresolved = result
    missing = sorted({str(value) for value in unresolved})
    evidence = dependency_reference_evidence(layers, missing)
    for path in missing:
        evidence[path].insert(0, {
            "kind": "usdutils_compute_all_dependencies_unresolved",
            "stage": str(stage), "unresolved_path": path,
        })
    classified = [classify_missing_dependency(path, evidence[path])
                  for path in missing]
    critical = [record for record in classified if record["critical"]]
    all_classified = all(
        record.get("category") and record.get("classification_basis")
        for record in classified)
    counts = Counter(record["category"] for record in classified)
    return {
        "dependency_contract_version": DEPENDENCY_CONTRACT_VERSION,
        "scene": scene_name,
        "stage": str(stage),
        "layer_count": len({str(layer.realPath or layer.identifier)
                            for layer in layers}),
        "asset_count": len({str(value) for value in assets}),
        "missing_asset_count": len(missing),
        "missing_assets": missing,
        "missing_dependencies": classified,
        "all_missing_dependencies_classified": all_classified,
        "classification_counts": dict(sorted(counts.items())),
        "critical_missing_dependency_count": len(critical),
        "noncritical_missing_dependency_count": len(classified) - len(critical),
        "critical_categories": sorted({record["category"] for record in critical}),
        "dependency_gate_pass": not critical,
        "status": ("fail_critical_missing_dependencies" if critical else
                   "pass_with_noncritical_missing_dependencies" if classified else
                   "pass"),
        "fail_policy": (
            "building_structure, furniture_visibility, collision, visual/geometry, "
            "and unclassified missing dependencies fail the candidate; only "
            "proven noncritical metadata may remain warning-only"),
    }


def cached_scene(cache_doc, name):
    record = cache_doc.get("scenes", {}).get(name)
    if not record:
        raise ValueError("scene missing from A1 geometry cache: " + name)
    geometry = dict(record["geometry"])
    geometry["room_union"] = unary_union(
        [box(*room["rect"]) for room in geometry["rooms"]])
    geometry["wall_union"] = (unary_union([box(*wall) for wall in geometry["walls"]])
                              if geometry["walls"] else None)
    return geometry, record["dependencies"], record["type"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-report", type=Path)
    ap.add_argument("--placements", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--candidate-index", type=int, default=0)
    ap.add_argument("--scene-cache", type=Path)
    args = ap.parse_args()

    placement_doc = json.loads(args.placements.read_text(encoding="utf-8"))
    candidates = placement_doc.get("candidates")
    if candidates is None:
        candidates = [placement_doc["selected"]]
    selected = dict(candidates[args.candidate_index])
    build = (json.loads(args.build_report.read_text(encoding="utf-8"))
             if args.build_report else None)
    if build and (selected["floor1"] != build["floor1_scene"]
                  or selected["floor2"] != build["floor2_scene"]):
        raise SystemExit("selected placement does not match the build report")
    if build and build.get("upper_floor_is_final_landing"):
        # The regenerated integration path ends at the real Floor2-supported
        # landing entry, not the centre of the removed legacy top tread.
        selected["exit_world"] = list(build["last_walkable_xy"])
    cache_doc = None
    if args.scene_cache:
        cache_doc = json.loads(args.scene_cache.read_text(encoding="utf-8-sig"))
        if cache_doc.get("cache_contract_version") not in (2, 3):
            raise SystemExit("stale A1 scene geometry cache")
        if cache_doc.get("complete_for_pair_cache") is not True:
            raise SystemExit("partial A1 scene geometry cache is not valid for A5")
        lower, lower_dependencies, lower_type = cached_scene(
            cache_doc, selected["floor1"])
        upper, upper_dependencies, upper_type = cached_scene(
            cache_doc, selected["floor2"])
    else:
        lower = scan(selected["floor1"]); upper = scan(selected["floor2"])
        lower_dependencies = source_dependency_audit(selected["floor1"])
        upper_dependencies = source_dependency_audit(selected["floor2"])
        lower_type = classify_scene(selected["floor1"])
        upper_type = classify_scene(selected["floor2"])
    if not lower or not upper:
        raise SystemExit("could not scan one of the selected scenes")
    dependencies = {"floor1": lower_dependencies, "floor2": upper_dependencies}
    dependencies["unique_missing_assets"] = sorted({
        asset for key in ("floor1", "floor2")
        for asset in dependencies[key]["missing_assets"]})
    dependencies["unique_missing_asset_count"] = len(
        dependencies["unique_missing_assets"])
    by_path = {}
    for floor in ("floor1", "floor2"):
        for record in dependencies[floor]["missing_dependencies"]:
            path = record["path"]
            current = by_path.setdefault(path, dict(record, floors=[]))
            if (current["category"] != record["category"]
                    or current["critical"] is not record["critical"]):
                raise ValueError("inconsistent dependency classification: " + path)
            current["floors"].append(floor)
    dependencies["missing_dependencies"] = [by_path[path] for path in sorted(by_path)]
    critical = [record for record in dependencies["missing_dependencies"]
                if record["critical"]]
    dependencies["dependency_contract_version"] = DEPENDENCY_CONTRACT_VERSION
    dependencies["all_missing_dependencies_classified"] = all(
        record.get("category") and record.get("classification_basis")
        for record in dependencies["missing_dependencies"])
    dependencies["critical_missing_dependency_count"] = len(critical)
    dependencies["noncritical_missing_dependency_count"] = (
        len(dependencies["missing_dependencies"]) - len(critical))
    dependencies["critical_categories"] = sorted({
        record["category"] for record in critical})
    dependencies["dependency_gate_pass"] = (
        dependencies["all_missing_dependencies_classified"] and not critical)
    dependencies["candidate_dependency_decision"] = (
        "PASS" if dependencies["dependency_gate_pass"] else
        "FAIL_CRITICAL_OR_UNCLASSIFIED_MISSING_DEPENDENCY")
    dependencies["final_build_dependency_audit_required"] = True
    dependencies["interpretation"] = (
        "Every unresolved source dependency is listed and classified. Any "
        "building-structure, furniture-visibility, collision, geometry/visual, "
        "or unclassified impact fails the candidate. This source audit does "
        "not replace the post-build dependency audit required by brief §7.15.")
    overhang_area, overhang_ratio = floor2_overhang(lower, upper)
    selected_out = dict(selected)
    selected_out.setdefault("scene_type", lower_type["type"]
                            if lower_type["type"] == upper_type["type"] else "unknown")
    selected_out.setdefault("floor1_type_votes", {
        "home": lower_type["home_votes"], "school": lower_type["school_votes"]})
    selected_out.setdefault("floor2_type_votes", {
        "home": upper_type["home_votes"], "school": upper_type["school_votes"]})
    selected_out["floor2_overhang_area_m2"] = overhang_area
    selected_out["floor2_overhang_ratio"] = overhang_ratio
    selected_out["floor2_overhang_pass"] = overhang_ratio <= 0.12
    selected_out["approach_corridor"] = corridor_record(
        selected["entry_world"], selected["entry_dir"], backwards=True)
    selected_out["egress_corridor"] = corridor_record(
        selected["exit_world"], selected["exit_dir"])

    matching = selected.get("feasible_placements") or [
        c for c in candidates if c["floor1"] == selected["floor1"]
        and c["floor2"] == selected["floor2"]]
    if build and (build.get("QA_approach_corridor_clear") is False
                  or build.get("QA_egress_corridor_clear") is False):
        matching = []
        selected_out["approach_clear"] = bool(
            build.get("QA_approach_corridor_clear"))
        selected_out["egress_clear"] = bool(
            build.get("QA_egress_corridor_clear"))
        selected_out["corridor_failure"] = {
            "approach": build.get("approach_corridor_audit"),
            "egress": build.get("egress_corridor_audit"),
        }
    if build and build.get("upper_floor_is_final_landing"):
        matching = [dict(item) for item in matching]
        for item in matching:
            if item.get("rect") == selected.get("rect"):
                item["exit_world"] = list(build["last_walkable_xy"])
    hole = build["opening_rect"] if build else selected["rect"]
    opening_kind = "built_opening" if build else "proposed_stair_footprint"
    room = selected.get("room_rect") or next(
        r["rect"] for r in lower["rooms"] if r["prim"] == selected["room"])
    hole_area = max(0.0, hole[2] - hole[0]) * max(0.0, hole[3] - hole[1])
    room_area = max(1e-9, (room[2] - room[0]) * (room[3] - room[1]))
    hole_ratio = hole_area / room_area

    floor_exclusions = {1: exclusion_rects(lower), 2: exclusion_rects(upper)}
    selected_projection_hits = {
        floor: [list(rect) for rect in rects
                if positive_xy_overlap(selected["rect"], rect)]
        for floor, rects in floor_exclusions.items()
    }
    gpu_spatial_review_required = any(selected_projection_hits.values())
    # Include every drawn exclusion in the viewport calculation. Otherwise a
    # furniture/door AABB beyond the room union can be silently clipped out of
    # the image even though it is part of the A4 obstacle contract.
    all_rects = ([r["rect"] for r in lower["rooms"] + upper["rooms"]]
                 + floor_exclusions[1] + floor_exclusions[2]
                 + [c["rect"] for c in matching] + [selected["rect"], hole])
    x0 = min(r[0] for r in all_rects); y0 = min(r[1] for r in all_rects)
    x1 = max(r[2] for r in all_rects); y1 = max(r[3] for r in all_rects)
    scale = min((WIDTH - 2 * PAD) / max(1e-6, x1 - x0),
                (HEIGHT - 2 * PAD - 90) / max(1e-6, y1 - y0))

    def transform(point):
        return (PAD + (point[0] - x0) * scale,
                HEIGHT - PAD - (point[1] - y0) * scale)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for floor_num, scene in ((1, lower), (2, upper)):
        image = Image.new("RGB", (WIDTH, HEIGHT), (250, 250, 248))
        draw = ImageDraw.Draw(image, "RGBA")
        draw.text((PAD, 18), "Floor {}: {}".format(floor_num, scene["scene"]),
                  fill=(20, 20, 20, 255), font=ImageFont.load_default())
        for record in scene["rooms"]:
            a = transform((record["rect"][0], record["rect"][1]))
            b = transform((record["rect"][2], record["rect"][3]))
            box_px = (min(a[0], b[0]), min(a[1], b[1]),
                      max(a[0], b[0]), max(a[1], b[1]))
            draw.rectangle(box_px, fill=(205, 205, 205, 255),
                           outline=(115, 115, 115, 255), width=2)
            draw.text((box_px[0] + 4, box_px[1] + 4), record["prim"],
                      fill=(55, 55, 55, 255))
        # Use every audited visual/collider furniture AABB in the plan.  The
        # legacy 2-D obstacle projection omits some valid exclusion geometry.
        for rect in floor_exclusions[floor_num]:
            hatched(draw, rect, transform)
        for candidate in matching:
            a = transform((candidate["rect"][0], candidate["rect"][1]))
            b = transform((candidate["rect"][2], candidate["rect"][3]))
            draw.rectangle((min(a[0], b[0]), min(a[1], b[1]),
                            max(a[0], b[0]), max(a[1], b[1])),
                           outline=(24, 145, 60, 190), width=2)
        a = transform((selected["rect"][0], selected["rect"][1]))
        b = transform((selected["rect"][2], selected["rect"][3]))
        selected_px = (min(a[0], b[0]), min(a[1], b[1]),
                       max(a[0], b[0]), max(a[1], b[1]))
        draw.rectangle(selected_px, outline=(20, 85, 210, 255), width=8)
        selected_label = ("SELECTED #01 - FAILED A4"
                          if build and build.get("status") == "fail"
                          else "SELECTED #01")
        draw.text((selected_px[0] + 8, selected_px[1] + 8), selected_label,
                  fill=(10, 55, 180, 255))
        if floor_num == 2:
            a = transform((hole[0], hole[1])); b = transform((hole[2], hole[3]))
            draw.rectangle((min(a[0], b[0]), min(a[1], b[1]),
                            max(a[0], b[0]), max(a[1], b[1])),
                           outline=(10, 70, 175, 255), width=5)
            draw.text((min(a[0], b[0]) + 8, min(a[1], b[1]) + 24),
                      "OPENING" if build else "PROPOSED OPENING",
                      fill=(10, 70, 175, 255))
        corridor = (selected_out["approach_corridor"] if floor_num == 1
                    else selected_out["egress_corridor"])
        ca = transform((corridor["bounds"][0], corridor["bounds"][1]))
        cb = transform((corridor["bounds"][2], corridor["bounds"][3]))
        draw.rectangle((min(ca[0], cb[0]), min(ca[1], cb[1]),
                        max(ca[0], cb[0]), max(ca[1], cb[1])),
                       fill=(245, 125, 20, 35),
                       outline=(245, 125, 20, 255), width=4)
        # Show the direction relevant to each floor. Drawing both arrows on
        # both plans made the old review evidence needlessly ambiguous.
        if floor_num == 1:
            arrow(draw, selected["entry_world"], selected["entry_dir"], transform,
                  (20, 105, 210, 255), "ENTRY")
        else:
            arrow(draw, selected["exit_world"], selected["exit_dir"], transform,
                  (115, 40, 175, 255), "")
        legend_y = HEIGHT - 58
        draw.rectangle((PAD, legend_y, PAD + 20, legend_y + 16), fill=(205, 205, 205, 255))
        draw.text((PAD + 28, legend_y), "usable room", fill=(35, 35, 35, 255))
        draw.rectangle((PAD + 170, legend_y, PAD + 190, legend_y + 16), fill=(245, 190, 190, 255))
        draw.text((PAD + 198, legend_y), "wall/furniture/door geometry exclusion",
                  fill=(35, 35, 35, 255))
        draw.rectangle((PAD + 510, legend_y, PAD + 530, legend_y + 16),
                       outline=(24, 145, 60, 255), width=2)
        draw.text((PAD + 538, legend_y), "{} strict candidates".format(
            len(matching)), fill=(35, 35, 35, 255))
        draw.rectangle((PAD + 650, legend_y, PAD + 670, legend_y + 16), outline=(20, 85, 210, 255), width=3)
        draw.text((PAD + 678, legend_y), "selected", fill=(35, 35, 35, 255))
        stair_w = selected["rect"][2] - selected["rect"][0]
        stair_d = selected["rect"][3] - selected["rect"][1]
        note = ("room {:.2f} x {:.2f} m | stair {:.2f} x {:.2f} m | "
                "opening/room {:.1%} | rotation {} deg").format(
                    room[2] - room[0], room[3] - room[1], stair_w, stair_d,
                    hole_ratio, selected.get("rotation_deg", 0))
        draw.text((PAD, 42), note, fill=(30, 30, 30, 255))
        rank = selected.get("selection_rank_metrics", {})
        if rank:
            rank_note = (
                "balanced choice | extra clearance A/E {:.3f}/{:.3f} m | "
                "Floor2 forward {:.3f} m"
            ).format(
                float(rank.get("approach_corridor_extra_clearance_m", 0.0)),
                float(rank.get("egress_corridor_extra_clearance_m", 0.0)),
                float(rank.get("egress_forward_clearance_m", 0.0)),
            )
            draw.text((PAD, 58), rank_note, fill=(30, 30, 30, 255))
        direction_note = (
            "orange = approach reserve 1.2 m + R0.22; blue arrow = stair entry"
            if floor_num == 1 else
            "orange = egress reserve 1.2 m + R0.22; purple arrow = Floor2 arrival/exit"
        )
        draw.text((PAD, 74), direction_note, fill=(125, 65, 10, 255))
        overlap_count = len(selected_projection_hits[floor_num])
        if overlap_count:
            draw.text(
                (PAD, 92),
                ("WARNING: selected area overlaps {} red 2D exclusion "
                 "projection(s); inspect GPU 3D before placement approval").format(
                     overlap_count),
                fill=(180, 20, 20, 255),
            )
        image.save(args.out_dir / "floorplan_floor{}.png".format(floor_num))

    placement_out = {
        "placement_contract_version": 3,
        "corridor_obstacle_source": "all_obstacle_boxes_walk_prism",
        "doorway_clearance_contract_version": selected_out[
            "doorway_clearance_contract_version"],
        "doorway_clearance_gate_pass": selected_out[
            "doorway_clearance_gate_pass"],
        "floor1_doorway_clearance": selected_out[
            "floor1_doorway_clearance"],
        "floor2_doorway_clearance": selected_out[
            "floor2_doorway_clearance"],
        "corridor_m": CORRIDOR_M,
        "corridor_body_radius_m": BODY_RADIUS_M,
        "corridor_reserved_length_m": CORRIDOR_M + BODY_RADIUS_M,
        "selected": selected_out,
        "all_matching_candidates": matching,
        "candidate_count": len(matching),
        "opening_rect": hole,
        "opening_kind": opening_kind,
        "hole_area_m2": hole_area,
        "selected_room_area_m2": room_area,
        "hole_area_ratio": hole_ratio,
        "selected_projection_exclusion_overlap": {
            "floor1_count": len(selected_projection_hits[1]),
            "floor2_count": len(selected_projection_hits[2]),
            "floor1_rects": selected_projection_hits[1],
            "floor2_rects": selected_projection_hits[2],
            "interpretation": (
                "2D XY projection only; does not prove or disprove Z collision"),
        },
        "gpu_spatial_review_required": gpu_spatial_review_required,
        "placement_approval_policy": (
            "GPU_3D_REQUIRED_BEFORE_PLACEMENT_APPROVAL"
            if gpu_spatial_review_required else
            "CPU_2D_CLEAR_AWAITING_HUMAN_PLACEMENT_REVIEW"),
        "source_dependency_audit": dependencies,
        "scene_geometry_source": (
            {"kind": "a1_json_cache", "path": str(args.scene_cache),
             "sha256": hashlib.sha256(args.scene_cache.read_bytes()).hexdigest()}
            if args.scene_cache else {"kind": "direct_usd_scan"}),
        "legend": {
            "gray": "usable room floor",
            "red_hatch": "walls, furniture AABBs, and door geometry exclusion",
            "green": "feasible placement candidates",
            "blue": "selected placement/opening",
            "orange": "1.2 m corridor plus 0.22 m body-radius end cap",
            "blue_arrow": "Floor 1 stair entry direction",
            "purple_arrow": "Floor 2 arrival/exit direction",
            "selected_label": "SELECTED #01",
        },
    }
    (args.out_dir / "placement.json").write_text(
        json.dumps(placement_out, indent=2), encoding="utf-8")
    print(json.dumps({"status": "pass", "candidate_count": len(matching),
                      "hole_area_ratio": hole_ratio,
                      "out_dir": str(args.out_dir)}, indent=2))


if __name__ == "__main__":
    main()
