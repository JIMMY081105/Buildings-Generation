#!/usr/bin/env python3
"""Compile a fail-closed 10-placement factory H1 package from strict A4 alternatives.

The ordinary A5 compiler intentionally emits one building per source
pair/kind.  This companion keeps that source-level contract intact, validates
it first with the ordinary compiler, and then promotes geometrically distinct
members of each candidate's already-audited ``feasible_placements`` list.
No A4 threshold is changed and every promoted variant remains attributable to
its source search document and feasible-placement index.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

from compile_a5_batch import (
    compile_package,
    height_key,
    load,
    manifest_record,
    validate_scene_cache,
    write_package,
)
from prepare_manifest import validate


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _placement_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        candidate["floor1"],
        candidate["floor2"],
        candidate["kind"],
        int(candidate["rotation_deg"]),
        candidate["room"],
        *(round(float(value), 9) for value in candidate["rect"]),
    )


def _center(candidate: dict[str, Any]) -> tuple[float, float]:
    x0, y0, x1, y1 = (float(value) for value in candidate["rect"])
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _distance(first: dict[str, Any], second: dict[str, Any]) -> float:
    a = _center(first)
    b = _center(second)
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _variant_bid(base_bid: str, variant_number: int) -> str:
    marker = base_bid.rfind("_h")
    if marker < 0:
        raise ValueError("base BID lacks terminal height token: " + base_bid)
    return "{}_v{:03d}{}".format(
        base_bid[:marker], variant_number, base_bid[marker:]
    )


def _variant_candidates(
    base_bid: str,
    base: dict[str, Any],
    types: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merge placement-only A4 alternatives back into their validated base."""
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    sources = [(-1, {})] + list(enumerate(base.get("feasible_placements", [])))
    for source_index, alternative in sources:
        candidate = deepcopy(base)
        candidate.pop("feasible_placements", None)
        candidate.update(deepcopy(alternative))
        if "rank_metrics" in candidate:
            candidate["selection_rank_metrics"] = candidate.pop("rank_metrics")
        candidate["source_base_bid"] = base_bid
        candidate["source_feasible_placement_index"] = source_index
        candidate["selection_policy"] = (
            "validated A4 feasible placement promoted by deterministic "
            "global farthest-point H1 variant selection"
        )
        validate(candidate, types)
        key = _placement_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    if not candidates:
        raise ValueError(base_bid + ": no validated placement variants")
    return candidates


def _select_variants(
    base_candidates: dict[str, dict[str, Any]],
    types: dict[str, Any],
    target_count: int,
    minimum_separation_m: float,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Select one per source first, then global farthest-point additions."""
    pools = {
        bid: _variant_candidates(bid, candidate, types)
        for bid, candidate in sorted(base_candidates.items())
    }
    selection_trace: list[dict[str, Any]] = []
    if target_count < len(pools):
        def source_rank(item: tuple[str, list[dict[str, Any]]]) -> tuple[Any, ...]:
            bid, pool = item
            metrics = pool[0].get("selection_rank_metrics", {})
            return (
                -float(metrics.get("minimum_corridor_extra_clearance_m", 0.0)),
                -float(metrics.get("egress_forward_clearance_m", 0.0)),
                -float(metrics.get("approach_forward_clearance_m", 0.0)),
                bid,
            )

        ranked = sorted(pools.items(), key=source_rank)
        selected = {bid: [pool[0]] for bid, pool in ranked[:target_count]}
        for rank, (bid, pool) in enumerate(ranked, 1):
            selection_trace.append(
                {
                    "source_base_bid": bid,
                    "source_feasible_placement_index": pool[0][
                        "source_feasible_placement_index"
                    ],
                    "source_rank": rank,
                    "selected": rank <= target_count,
                    "selection_mode": "ranked_strict_source_subset",
                    "center_xy_m": list(_center(pool[0])),
                    "rect": pool[0]["rect"],
                    "rotation_deg": pool[0]["rotation_deg"],
                }
            )
        return selected, selection_trace

    selected = {bid: [pool[0]] for bid, pool in pools.items()}
    remaining = {bid: pool[1:] for bid, pool in pools.items()}

    while sum(len(rows) for rows in selected.values()) < target_count:
        best: tuple[float, str, int] | None = None
        for bid, rows in remaining.items():
            for index, candidate in enumerate(rows):
                minimum = min(
                    _distance(candidate, chosen) for chosen in selected[bid]
                )
                key = (minimum, bid, -index)
                if best is None or key > (best[0], best[1], -best[2]):
                    best = (minimum, bid, index)
        if best is None or best[0] + 1.0e-12 < minimum_separation_m:
            raise ValueError(
                "only {} variants satisfy {:.3f} m separation; target is {}".format(
                    sum(len(rows) for rows in selected.values()),
                    minimum_separation_m,
                    target_count,
                )
            )
        distance, bid, index = best
        candidate = remaining[bid].pop(index)
        selected[bid].append(candidate)
        selection_trace.append(
            {
                "source_base_bid": bid,
                "source_feasible_placement_index": candidate[
                    "source_feasible_placement_index"
                ],
                "minimum_center_separation_at_selection_m": distance,
                "center_xy_m": list(_center(candidate)),
                "rect": candidate["rect"],
                "rotation_deg": candidate["rotation_deg"],
            }
        )

    for bid, rows in selected.items():
        for first in range(len(rows)):
            for second in range(first + 1, len(rows)):
                distance = _distance(rows[first], rows[second])
                if distance + 1.0e-12 < minimum_separation_m:
                    raise AssertionError(
                        "{} selected variants are only {:.6f} m apart".format(
                            bid, distance
                        )
                    )
    return selected, selection_trace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--types", type=Path, required=True)
    parser.add_argument(
        "--search",
        action="append",
        nargs=3,
        metavar=("HEIGHT", "KIND", "PLACEMENT_JSON"),
        default=[],
    )
    parser.add_argument("--scene-cache", type=Path, required=True)
    parser.add_argument("--stair-asset-coverage", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--floorplan-script", type=Path, required=True)
    parser.add_argument("--target-count", type=int, default=10)
    parser.add_argument("--minimum-variant-separation-m", type=float, default=0.20)
    parser.add_argument("--exclude-source-scene", action="append", default=[])
    parser.add_argument("--post-normalization-exclusion-evidence", type=Path)
    args = parser.parse_args()

    if args.target_count != 10:
        raise ValueError("this factory release compiler requires exactly 10 buildings")
    if args.out_dir.exists():
        raise FileExistsError("refusing to overwrite H1 package: " + str(args.out_dir))

    pair_doc = load(args.pairs)
    type_doc = load(args.types)
    searches = [
        (float(height), kind, load(Path(path)))
        for height, kind, path in args.search
    ]
    package = compile_package(
        pair_doc,
        type_doc,
        searches,
        set(),
        args.target_count,
        load(args.stair_asset_coverage),
    )
    base_candidates = package["_candidate_docs"]
    if not base_candidates:
        raise ValueError("current-contract A4 searches produced no strict candidates")
    unfiltered_base_candidate_count = len(base_candidates)
    excluded_scenes = set(args.exclude_source_scene)
    excluded_base_bids = sorted(
        bid for bid, candidate in base_candidates.items()
        if candidate["floor1"] in excluded_scenes
        or candidate["floor2"] in excluded_scenes
    )
    if excluded_scenes:
        if args.post_normalization_exclusion_evidence is None:
            raise ValueError("source exclusions require post-normalization evidence")
        evidence_path = args.post_normalization_exclusion_evidence.resolve()
        if not evidence_path.is_file() or evidence_path.stat().st_size == 0:
            raise FileNotFoundError(evidence_path)
        base_candidates = {
            bid: candidate for bid, candidate in base_candidates.items()
            if bid not in set(excluded_base_bids)
        }
        if len(base_candidates) < args.target_count:
            raise ValueError(
                "post-normalization exclusions leave only {} strict candidates".format(
                    len(base_candidates)))
    elif args.post_normalization_exclusion_evidence is not None:
        raise ValueError("exclusion evidence was supplied without a source exclusion")
    selected, trace = _select_variants(
        base_candidates,
        type_doc["scenes"],
        args.target_count,
        args.minimum_variant_separation_m,
    )

    base_manifest = {
        record["BID"]: record for record in package["buildings"]
    }
    variants: dict[str, dict[str, Any]] = {}
    buildings = []
    selection_rows = []
    for base_bid, rows in sorted(selected.items()):
        for number, candidate in enumerate(rows, 1):
            bid = _variant_bid(base_bid, number)
            candidate["placement_variant_id"] = "v{:03d}".format(number)
            candidate["BID"] = bid
            record = manifest_record(candidate)
            record["BID"] = bid
            record.update(
                {
                    "prior_human_approved": False,
                    "strict_collider_normalization_pass": base_manifest[base_bid][
                        "strict_collider_normalization_pass"
                    ],
                    "post_A5_build_blocked": base_manifest[base_bid][
                        "post_A5_build_blocked"
                    ],
                    "strict_collider_failed_gates": base_manifest[base_bid][
                        "strict_collider_failed_gates"
                    ],
                    "source_base_bid": base_bid,
                    "placement_variant_id": candidate["placement_variant_id"],
                    "source_feasible_placement_index": candidate[
                        "source_feasible_placement_index"
                    ],
                }
            )
            variants[bid] = candidate
            buildings.append(record)
            selection_rows.append(
                {
                    "BID": bid,
                    "source_base_bid": base_bid,
                    "placement_variant_id": candidate["placement_variant_id"],
                    "source_feasible_placement_index": candidate[
                        "source_feasible_placement_index"
                    ],
                    "center_xy_m": list(_center(candidate)),
                    "rect": candidate["rect"],
                    "rotation_deg": candidate["rotation_deg"],
                }
            )

    if len(variants) != args.target_count or len(buildings) != args.target_count:
        raise AssertionError("variant materialization count differs from target")
    validate_scene_cache(args.scene_cache, args.pairs, args.types, buildings)
    authorization = load(args.authorization)
    if authorization.get("batch_generation_authorized") is not True:
        raise ValueError("authorization lacks batch_generation_authorized=true")
    if authorization.get("automatic_batch_generation_authorized") is not True:
        raise ValueError("authorization lacks automatic authorization=true")
    if authorization.get("training_authorized") is not False:
        raise ValueError("authorization must keep training_authorized=false")

    package.update(
        {
            "status": "AWAITING_HUMAN_APPROVAL",
            "batch_generation_authorized": True,
            "automatic_batch_generation_authorized": True,
            "training_authorized": False,
            "authorization_source": authorization.get("authorization_source"),
            "authorization_record": str(args.authorization.resolve()),
            "h1_placement_approved": False,
            "stage_b_blocked_pending_h1": True,
            "target_building_count": args.target_count,
            "prior_approved_building_count": 0,
            "approved_building_count": 0,
            "remaining_target_count": args.target_count,
            "strict_source_pair_kind_candidate_count": len(base_candidates),
            "strict_candidate_count": len(variants),
            "review_candidate_count": len(variants),
            "new_candidate_count": len(variants),
            "placement_variant_count": len(variants),
            "minimum_variant_center_separation_m": args.minimum_variant_separation_m,
            "placement_variant_policy": (
                "if strict sources exceed the target, choose the deterministic "
                "A4 clearance ranking; otherwise take one per source pair/kind "
                "first, followed by global farthest-point promotion from the "
                "unchanged A4 feasible list"
            ),
            "pre_post_normalization_base_candidate_count":
                unfiltered_base_candidate_count,
            "post_normalization_source_exclusions": sorted(excluded_scenes),
            "post_normalization_excluded_base_bids": excluded_base_bids,
            "post_normalization_exclusion_evidence": (
                str(args.post_normalization_exclusion_evidence.resolve())
                if args.post_normalization_exclusion_evidence else None),
            "post_normalization_exclusion_evidence_sha256": (
                _sha256(args.post_normalization_exclusion_evidence.resolve())
                if args.post_normalization_exclusion_evidence else None),
            "machine_gate_thresholds_modified": False,
            "already_approved_candidate_bids": [],
            "strict_collider_blocked_candidate_count": sum(
                record["post_A5_build_blocked"] for record in buildings
            ),
            "buildings": sorted(buildings, key=lambda row: row["BID"]),
            "_candidate_docs": variants,
            "a4_doorway_clearance_rejection_count": sum(
                len(document.get("doorway_clearance_rejections", []))
                for _height, _kind, document in searches
            ),
            "a4_doorway_clearance_rejections": [
                row
                for _height, _kind, document in searches
                for row in document.get("doorway_clearance_rejections", [])
            ],
            "a4_strict_placement_rejection_count": sum(
                len(document.get("strict_placement_rejections", []))
                for _height, _kind, document in searches
            ),
            "a4_strict_placement_rejections": [
                row
                for _height, _kind, document in searches
                for row in document.get("strict_placement_rejections", [])
            ],
            "all_a4_pair_outcomes_retained": all(
                document.get("search_exhaustive") is True
                and int(document.get("pairs_evaluated_count", -1))
                == int(document.get("pairs_height_and_kind_eligible", -2))
                for _height, _kind, document in searches
            ),
        }
    )

    args.out_dir.mkdir(parents=True)
    selection_report = {
        "schema": "StrictA4PlacementVariantSelectionV1",
        "status": "PASS",
        "target_count": args.target_count,
        "selected_count": len(selection_rows),
        "strict_source_pair_kind_candidate_count": len(base_candidates),
        "unfiltered_strict_source_pair_kind_candidate_count":
            unfiltered_base_candidate_count,
        "post_normalization_source_exclusions": sorted(excluded_scenes),
        "post_normalization_excluded_base_bids": excluded_base_bids,
        "post_normalization_exclusion_evidence": (
            str(args.post_normalization_exclusion_evidence.resolve())
            if args.post_normalization_exclusion_evidence else None),
        "post_normalization_exclusion_evidence_sha256": (
            _sha256(args.post_normalization_exclusion_evidence.resolve())
            if args.post_normalization_exclusion_evidence else None),
        "machine_gate_thresholds_modified": False,
        "minimum_variant_center_separation_m": args.minimum_variant_separation_m,
        "source_search_documents": [
            {
                "height_m": height,
                "kind": kind,
                "path": str(Path(path).resolve()),
                "sha256": _sha256(Path(path).resolve()),
            }
            for height, kind, path in args.search
        ],
        "selection_trace": trace,
        "variants": selection_rows,
    }
    (args.out_dir / "VARIANT_SELECTION.json").write_text(
        json.dumps(selection_report, indent=2) + "\n", encoding="utf-8"
    )
    write_package(
        args.out_dir,
        package,
        type_doc,
        args.floorplan_script,
        Path(__import__("sys").executable),
        args.scene_cache,
    )
    print(
        json.dumps(
            {
                "status": package["status"],
                "review_candidate_count": package["review_candidate_count"],
                "strict_source_pair_kind_candidate_count": len(base_candidates),
                "minimum_variant_center_separation_m": args.minimum_variant_separation_m,
                "output": str(args.out_dir),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
