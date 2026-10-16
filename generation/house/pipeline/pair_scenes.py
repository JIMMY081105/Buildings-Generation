#!/usr/bin/env python3
"""Pick two MolmoSpaces scenes that can legitimately stack as Floor 1 + Floor 2.

Floor 2 is a DIFFERENT scene, so three things have to line up:

  1. footprint agreement -- Floor 2 must sit on Floor 1's walls, not hang in air
  2. a room on Floor 1 big enough for the stair, clear of furniture
  3. the SAME xy patch on Floor 2 must also be open room floor, clear of
     furniture and not inside a wall -- otherwise the stair arrives into a wall

Emits a ranked table of candidate pairs plus the placement that makes each work.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from itertools import permutations
import os
from pathlib import Path

from pxr import Usd, UsdGeom
from shapely.geometry import box
from shapely.ops import unary_union

from doorway_clearance import scan_stage as scan_doorway_clearance

SCENES = Path(os.environ.get(
    "BUILDING_SCENES_ROOT",
    "/ssd_xs/scvj260/scene_comparison/restore/assets_index/scenes/holodeck-objaverse-val",
))
STAIRS = {"straight": (1.62, 5.22), "l_shaped": (4.32, 4.08), "u_shaped": (2.99, 4.19)}
MARGIN = 0.25
MAX_FLOOR2_OVERHANG = 0.12
RESIDENTIAL = ("bed", "sofa", "couch", "armchair", "wardrobe", "dresser",
               "nightstand", "crib", "bathtub", "toilet", "fridge",
               "refrigerator", "stove", "oven", "microwave", "dining",
               "coffee_table", "tv_stand", "bookshelf")
SCHOOL_OFFICE = ("desk", "whiteboard", "blackboard", "projector", "locker",
                 "podium", "lectern", "conference", "cubicle", "office_chair",
                 "filing", "printer", "copier")


def classify_assets(asset_ids) -> dict:
    """Classify one scene and retain enough evidence for a human to overturn it."""
    hits = {"home": Counter(), "school": Counter()}
    terms = {"home": RESIDENTIAL, "school": SCHOOL_OFFICE}
    matched_terms = {"home": {}, "school": {}}
    clean_ids = [str(asset_id) for asset_id in asset_ids if asset_id]
    for asset_id in clean_ids:
        lowered = asset_id.lower()
        for label, keywords in terms.items():
            matched = sorted({keyword for keyword in keywords if keyword in lowered})
            if matched:
                hits[label][asset_id] += 1
                matched_terms[label].setdefault(asset_id, set()).update(matched)

    votes = {label: sum(counter.values()) for label, counter in hits.items()}
    high = max(votes.values())
    low = min(votes.values())
    # The task contract says both categories below three votes, or a margin below
    # 30%, is unknown.  Relative margin uses the winning vote count.
    ambiguous = high < 3 or high == 0 or (high - low) / high < 0.30
    if ambiguous or votes["home"] == votes["school"]:
        label = "unknown"
    else:
        label = "home" if votes["home"] > votes["school"] else "school"

    evidence = {}
    for label_name, counter in hits.items():
        evidence[label_name] = [
            {"asset_id": asset_id, "count": count,
             "matched_terms": sorted(matched_terms[label_name][asset_id])}
            for asset_id, count in sorted(counter.items())
        ]
    return {
        "type": label,
        "home_votes": votes["home"],
        "school_votes": votes["school"],
        "vote_margin_ratio": round((high - low) / high, 4) if high else 0.0,
        "asset_count": len(clean_ids),
        "evidence": evidence,
    }


def spatial_room_assets(name: str, records: list[dict]) -> tuple[dict, dict]:
    """Assign metadata objects to actual USD rooms by world-space geometry.

    MolmoSpaces' metadata room_id is 0 for every type-bearing object in all 30
    scenes, so it cannot support a cross-room evidence rule.  The USD geometry
    is authoritative: map each object's root bound to the room containing its
    XY centre, falling back to maximum AABB/room overlap.
    """
    stage = Usd.Stage.Open(str(SCENES / name / "scene.usda"), load=Usd.Stage.LoadAll)
    if stage is None:
        raise RuntimeError("could not open stage for spatial room assignment")
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.guide])
    rooms = []
    wanted = {str(record.get("hash_name")) for record in records if record.get("hash_name")}
    object_prims = {}
    for prim in stage.Traverse():
        n = prim.GetName()
        if prim.IsA(UsdGeom.Mesh) and n.startswith("room_") and "_visual" in n:
            rng = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if not rng.IsEmpty():
                mn, mx = rng.GetMin(), rng.GetMax()
                rooms.append({"room": n, "rect": (
                    float(mn[0]), float(mn[1]), float(mx[0]), float(mx[1]))})
        if n in wanted and n not in object_prims:
            object_prims[n] = prim

    def intersection_area(a, b):
        dx = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        dy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        return dx * dy

    groups, assignments = {}, {"spatial": 0, "unassigned": 0,
                                "room_count": len(rooms), "objects": {}}
    for record in records:
        asset_id = record.get("asset_id")
        hash_name = str(record.get("hash_name", ""))
        prim = object_prims.get(hash_name)
        assigned, rect, prim_path = "unassigned", None, None
        if prim and prim.IsValid():
            prim_path = str(prim.GetPath())
            rng = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if not rng.IsEmpty():
                mn, mx = rng.GetMin(), rng.GetMax()
                rect = (float(mn[0]), float(mn[1]), float(mx[0]), float(mx[1]))
                cx, cy = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
                containing = [room for room in rooms
                              if room["rect"][0] - 1e-6 <= cx <= room["rect"][2] + 1e-6
                              and room["rect"][1] - 1e-6 <= cy <= room["rect"][3] + 1e-6]
                candidates = containing or rooms
                if candidates:
                    best = max(candidates, key=lambda room: intersection_area(rect, room["rect"]))
                    if containing or intersection_area(rect, best["rect"]) > 0.0:
                        assigned = best["room"]
        groups.setdefault(assigned, []).append(asset_id)
        assignments["spatial" if assigned != "unassigned" else "unassigned"] += 1
        assignments["objects"][hash_name] = {
            "asset_id": asset_id, "room": assigned, "prim_path": prim_path,
            "rect": list(rect) if rect else None,
            "metadata_room_id_ignored": record.get("room_id"),
        }
    return groups, assignments


def enforce_cross_room_votes(result: dict, room_distribution: dict) -> None:
    voting_rooms = sorted(room_id for room_id, room_vote in room_distribution.items()
                          if room_id != "unassigned"
                          and room_vote["home_votes"] + room_vote["school_votes"] > 0)
    result["type_bearing_rooms"] = voting_rooms
    result["type_bearing_room_count"] = len(voting_rooms)
    result["cross_room_vote_pass"] = len(voting_rooms) >= 2
    if result["type"] in ("home", "school") and not result["cross_room_vote_pass"]:
        result["pre_cross_room_type"] = result["type"]
        result["type"] = "unknown"
        result["unknown_reason"] = "all_type_votes_concentrated_in_one_spatial_room"


def classify_scene(name: str) -> dict:
    metadata_path = SCENES / name / "scene_metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        objects = metadata.get("objects", {})
        records = list(objects.values() if isinstance(objects, dict) else objects)
        result = classify_assets(record.get("asset_id") for record in records)
        room_records, assignment_audit = spatial_room_assets(name, records)
        room_distribution = {}
        for room_id, asset_ids in sorted(room_records.items()):
            room_vote = classify_assets(asset_ids)
            room_vote["asset_id_counts"] = dict(sorted(Counter(
                str(asset_id) for asset_id in asset_ids if asset_id).items()))
            room_distribution[room_id] = room_vote
        result["room_distribution"] = room_distribution
        result["room_assignment"] = assignment_audit
        enforce_cross_room_votes(result, room_distribution)
        result.update({"scene": name, "metadata": str(metadata_path), "error": None})
        return result
    except Exception as exc:  # noqa: BLE001
        return {
            "scene": name, "type": "unknown", "home_votes": 0,
            "school_votes": 0, "vote_margin_ratio": 0.0, "asset_count": 0,
            "evidence": {"home": [], "school": []},
            "room_distribution": {}, "room_assignment": {},
            "type_bearing_rooms": [], "type_bearing_room_count": 0,
            "cross_room_vote_pass": False,
            "metadata": str(metadata_path), "error": str(exc),
        }


def type_compatible(lower_type: dict, upper_type: dict) -> bool:
    """Hard gate: unknowns and mixed scene types must never reach shape scoring."""
    lo = lower_type.get("type", "unknown")
    up = upper_type.get("type", "unknown")
    return lo in ("home", "school") and lo == up


def apply_type_overrides(scene_types: dict, overrides: dict) -> None:
    """Apply auditable human decisions without discarding automatic evidence."""
    for scene, decision in overrides.items():
        if scene not in scene_types:
            raise ValueError("type override names unknown scene: " + scene)
        override_type = decision.get("type")
        if override_type not in ("home", "school", "unknown"):
            raise ValueError("invalid override type for {}".format(scene))
        if not str(decision.get("reason", "")).strip():
            raise ValueError("type override requires a reason for {}".format(scene))
        record = scene_types[scene]
        record["automatic_type"] = record["type"]
        record["type"] = override_type
        record["human_override"] = decision


def floor2_overhang(lower: dict, upper: dict) -> tuple[float, float]:
    """Return unsupported Floor-2 area and its fraction of Floor-2 footprint."""
    upper_area = float(upper["room_union"].area)
    if upper_area <= 0.0:
        return (0.0, 1.0)
    area = float(upper["room_union"].difference(lower["room_union"]).area)
    return (area, area / upper_area)


def scan(name: str) -> dict | None:
    stage = Usd.Stage.Open(str(SCENES / name / "scene.usda"), load=Usd.Stage.LoadAll)
    if stage is None:
        return None
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    rooms, walls, obstacles = [], [], []
    # Keep the full 3-D boxes as well as the legacy 2-D navigation obstacles.
    # A hanging light can be irrelevant to a floor corridor yet still intersect
    # a rising stair/handrail, so A4 must not discard it based on min-z.
    obstacle_boxes, wall_boxes = [], []
    floor_top = wall_top = None
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        n = prim.GetName()
        r = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if r.IsEmpty():
            continue
        mn, mx = r.GetMin(), r.GetMax()
        rect = (float(mn[0]), float(mn[1]), float(mx[0]), float(mx[1]))
        bounds = (float(mn[0]), float(mn[1]), float(mn[2]),
                  float(mx[0]), float(mx[1]), float(mx[2]))
        if "_visual" in n and n.startswith("room_"):
            rooms.append({"prim": n, "rect": rect})
            floor_top = float(mx[2]) if floor_top is None else max(floor_top, float(mx[2]))
        elif "_visual" in n and n.startswith("wall_"):
            walls.append(rect)
            wall_boxes.append({"prim": str(prim.GetPath()), "box": bounds})
            wall_top = float(mx[2]) if wall_top is None else max(wall_top, float(mx[2]))
        else:
            # BUGFIX: this branch used to be `"_visual" not in n and
            # "_collision" not in n`, which excluded furniture -- MolmoSpaces
            # names furniture visual meshes `*_visual`, so only `*_colliderN`
            # ever landed here and any object without a collider was invisible
            # to placement. The stair then drove straight through it.
            # Classify by architecture prefix instead, and take visual meshes
            # AND colliders so nothing can slip past.
            is_arch = (n.startswith("room_") or n.startswith("wall_")
                       or n.startswith("ceiling_") or n == "floor")
            if not is_arch:
                obstacle_boxes.append({"prim": str(prim.GetPath()), "box": bounds})
                if float(mx[2]) - float(mn[2]) > 0.12 and float(mn[2]) < 2.2:
                    obstacles.append(rect)
    if not rooms or wall_top is None:
        return None
    return {
        "scene": name,
        "floor_to_floor": wall_top - (floor_top or 0.0),
        "rooms": rooms,
        "walls": walls,
        "obstacles": obstacles,
        "wall_boxes": wall_boxes,
        "obstacle_boxes": obstacle_boxes,
        "room_union": unary_union([box(*r["rect"]) for r in rooms]),
        "wall_union": unary_union([box(*w) for w in walls]) if walls else None,
        "doorway_clearance": scan_doorway_clearance(stage),
    }


def free_spot(sc: dict, fx: float, fy: float):
    """First clear placement of an fx x fy footprint inside any room of sc."""
    need_x, need_y = fx + 2 * MARGIN, fy + 2 * MARGIN
    obs = [box(*o) for o in sc["obstacles"]]
    for room in sorted(sc["rooms"], key=lambda r: -((r["rect"][2] - r["rect"][0]) * (r["rect"][3] - r["rect"][1]))):
        rx0, ry0, rx1, ry1 = room["rect"]
        if rx1 - rx0 < need_x or ry1 - ry0 < need_y:
            continue
        step = 0.25
        i = 0
        while rx0 + MARGIN + i * step + fx <= rx1 - MARGIN:
            j = 0
            while ry0 + MARGIN + j * step + fy <= ry1 - MARGIN:
                x0 = rx0 + MARGIN + i * step
                y0 = ry0 + MARGIN + j * step
                cand = box(x0, y0, x0 + fx, y0 + fy)
                if not any(cand.intersects(o) for o in obs):
                    return {"room": room["prim"], "rect": [x0, y0, x0 + fx, y0 + fy],
                            "room_rect": list(room["rect"])}
                j += 1
            i += 1
    return None


def arrival_ok(upper: dict, rect) -> dict:
    """Is that same xy patch usable floor on the upper scene?"""
    poly = box(*rect)
    inside = None
    for room in upper["rooms"]:
        rp = box(*room["rect"])
        if rp.contains(poly.buffer(-1e-6)):
            inside = room["prim"]
            break
    covered = poly.intersection(upper["room_union"]).area / poly.area if upper["room_union"] else 0.0
    wall_hit = (upper["wall_union"] is not None
                and poly.intersection(upper["wall_union"]).area > 0.02)
    furn = sum(1 for o in upper["obstacles"] if poly.intersects(box(*o)))
    return {"inside_single_room": inside, "room_coverage": round(covered, 4),
            "intersects_wall": bool(wall_hit), "furniture_overlaps": furn,
            "ok": bool(inside is not None and not wall_hit and furn == 0)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--types-out", type=Path,
                    help="default: scene_types.json beside --out")
    ap.add_argument("--type-overrides", type=Path,
                    help="auditable human overrides; defaults beside --out if present")
    ap.add_argument("--max-floor2-overhang", type=float,
                    default=MAX_FLOOR2_OVERHANG)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not SCENES.is_dir():
        raise FileNotFoundError("scene root does not exist: " + str(SCENES))

    names = sorted(d.name for d in SCENES.iterdir()
                   if d.is_dir() and d.name.endswith("_ceiling"))
    if args.limit:
        names = names[:args.limit]

    scene_types = {name: classify_scene(name) for name in names}
    types_out = args.types_out or args.out.with_name("scene_types.json")
    overrides_path = args.type_overrides or args.out.with_name("scene_type_overrides.json")
    overrides = (json.loads(overrides_path.read_text(encoding="utf-8"))
                 if overrides_path.exists() else {})
    apply_type_overrides(scene_types, overrides)
    types_doc = {
        "scene_root": str(SCENES.resolve()),
        "rules": {
            "minimum_winning_votes": 3,
            "minimum_vote_margin_ratio": 0.30,
            "minimum_type_bearing_spatial_rooms": 2,
            "room_assignment": "USD world-space object bounds; metadata room_id ignored",
            "home_keywords": list(RESIDENTIAL),
            "school_keywords": list(SCHOOL_OFFICE),
        },
        "counts": {
            label: sum(1 for record in scene_types.values() if record["type"] == label)
            for label in ("home", "school", "unknown")
        },
        "type_overrides_file": str(overrides_path) if overrides else None,
        "type_override_count": len(overrides),
        "scenes": scene_types,
    }
    types_out.parent.mkdir(parents=True, exist_ok=True)
    types_out.write_text(json.dumps(types_doc, indent=2), encoding="utf-8")

    data = {}
    for n in names:
        if scene_types[n]["type"] == "unknown":
            continue
        try:
            s = scan(n)
            if s:
                data[n] = s
        except Exception as exc:  # noqa: BLE001
            print("skip {}: {}".format(n, exc))

    results = []
    overhang_rejections = []
    type_gate_rejected = 0
    same_type_pairs = 0
    for lower_n, upper_n in permutations(data.keys(), 2):
        # This is deliberately before any footprint intersection/union work.
        if not type_compatible(scene_types[lower_n], scene_types[upper_n]):
            type_gate_rejected += 1
            continue
        same_type_pairs += 1
        lo, up = data[lower_n], data[upper_n]
        if lo["room_union"].area <= 0 or up["room_union"].area <= 0:
            continue
        inter = lo["room_union"].intersection(up["room_union"]).area
        union = lo["room_union"].union(up["room_union"]).area
        iou = inter / union if union > 0 else 0.0
        if iou < 0.60:
            continue
        overhang_area, overhang_ratio = floor2_overhang(lo, up)
        if overhang_ratio > args.max_floor2_overhang:
            overhang_rejections.append({
                "floor1": lower_n, "floor2": upper_n,
                "scene_type": scene_types[lower_n]["type"],
                "footprint_iou": round(iou, 4),
                "floor2_overhang_area_m2": round(overhang_area, 4),
                "floor2_overhang_ratio": round(overhang_ratio, 6),
                "threshold": args.max_floor2_overhang,
                "reason": "floor2_overhang_exceeds_threshold",
            })
            continue
        for kind, (fx, fy) in STAIRS.items():
            # Retain the old zero-degree probe only as diagnostic evidence.
            # It must not reject the pair: exact placement (including all four
            # rotations, height, both floors, furniture, walls, and corridors)
            # belongs exclusively to A4/pick_placement.py.
            spot = free_spot(lo, fx, fy)
            arr = arrival_ok(up, spot["rect"]) if spot else None
            results.append({
                "floor1": lower_n, "floor2": upper_n, "kind": kind,
                "scene_type": scene_types[lower_n]["type"],
                "floor1_type_votes": {
                    "home": scene_types[lower_n]["home_votes"],
                    "school": scene_types[lower_n]["school_votes"],
                },
                "floor1_type_bearing_rooms": scene_types[lower_n]["type_bearing_rooms"],
                "floor1_cross_room_vote_pass": scene_types[lower_n]["cross_room_vote_pass"],
                "floor2_type_votes": {
                    "home": scene_types[upper_n]["home_votes"],
                    "school": scene_types[upper_n]["school_votes"],
                },
                "floor2_type_bearing_rooms": scene_types[upper_n]["type_bearing_rooms"],
                "floor2_cross_room_vote_pass": scene_types[upper_n]["cross_room_vote_pass"],
                "footprint_iou": round(iou, 4),
                "floor2_overhang_area_m2": round(overhang_area, 4),
                "floor2_overhang_ratio": round(overhang_ratio, 6),
                "floor2_overhang_pass": True,
                "floor1_f2f": round(lo["floor_to_floor"], 4),
                "floor2_f2f": round(up["floor_to_floor"], 4),
                "initial_zero_degree_probe": {
                    "placement": spot,
                    "arrival": arr,
                    "passed": bool(arr and arr["ok"]),
                    "decision_role": "diagnostic_only_not_an_A3_gate",
                },
                "pair_gate_passed": True,
                "usable": True,
            })

    results.sort(key=lambda r: (not r["usable"], -r["footprint_iou"]))
    out = {
        "scene_root": str(SCENES.resolve()),
        "scenes_discovered": len(names),
        "scenes_geometry_scanned": len(data),
        "scene_type_counts": types_doc["counts"],
        "type_gate_rejected_pairs": type_gate_rejected,
        "same_type_pairs_shape_scored": same_type_pairs,
        "max_floor2_overhang": args.max_floor2_overhang,
        "floor2_overhang_rejected_pairs": len(overhang_rejections),
        "overhang_rejections": overhang_rejections,
        "candidates": len(results),
        "usable": sum(1 for r in results if r["usable"]),
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")

    # Height groups are cached outputs consumed by pick_placement.py.  h320 is
    # retained for compatibility with the task's established BID convention.
    height_groups = {}
    for result in results:
        key = "h{}".format(int(round(float(result["floor1_f2f"]) * 100)))
        height_groups.setdefault(key, []).append(result)
    for key, grouped in height_groups.items():
        group_out = args.out.with_name("pairs_{}.json".format(key))
        group_out.write_text(json.dumps({
            "height_m": float(grouped[0]["floor1_f2f"]),
            "candidates": len(grouped),
            "usable": sum(1 for r in grouped if r["usable"]),
            "results": grouped,
        }, indent=2), encoding="utf-8")

    print("discovered {} scenes | typed home={} school={} unknown={} | geometry-scanned {}".format(
        len(names), types_doc["counts"]["home"], types_doc["counts"]["school"],
        types_doc["counts"]["unknown"], len(data)))
    print("type gate rejected {} directed pairs before shape scoring".format(type_gate_rejected))
    print("{} candidate pairs | {} usable".format(len(results), out["usable"]))
    print()
    print("{:<20} {:<20} {:<10} {:>6} {:>7} {:>7}  {}".format(
        "floor1", "floor2", "kind", "IoU", "f1_h", "f2_h", "arrival"))
    for r in results[:14]:
        print("{:<20} {:<20} {:<10} {:>6} {:>7} {:>7}  {}".format(
            r["floor1"], r["floor2"], r["kind"], r["footprint_iou"],
            r["floor1_f2f"], r["floor2_f2f"],
            ("probe OK room={}".format(
                r["initial_zero_degree_probe"]["arrival"]["inside_single_room"])
             if r["initial_zero_degree_probe"]["passed"]
             else "probe failed (A4 still evaluates rotations)")))


if __name__ == "__main__":
    main()
