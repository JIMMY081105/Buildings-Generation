#!/usr/bin/env python3
"""Build the ten selected H1 candidates with repaired stairs and door policy."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from pxr import Usd

from doorway_clearance import scan_stage


STRICT_GATES = (
    "all_authored_tread_colliders_normalized",
    "source_tread_collision_prims_deactivated",
    "combined_tread_collider_contains_all_boxes",
    "combined_tread_collider_is_static_closed_triangle_mesh",
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("expected object: " + str(path))
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def kind_from_bid(bid: str) -> str:
    if "_lshaped_" in bid:
        return "l_shaped"
    if "_ushaped_" in bid:
        return "u_shaped"
    if "_straight_" in bid:
        return "straight"
    raise ValueError("cannot derive stair kind: " + bid)


def height_mm_from_bid(bid: str) -> int:
    return int(bid.rsplit("_h", 1)[1])


def stair_root(
    height_mm: int, kind: str, batch_v1: Path, repaired_h320: Path,
    repaired_stage_b: Path | None,
) -> Path:
    if repaired_stage_b is not None:
        repaired = repaired_stage_b / f"h{height_mm}" / kind / "tex128"
        if repaired.is_dir():
            return repaired
    if height_mm == 320:
        candidate = repaired_h320 / kind / "tex128"
        return candidate if candidate.is_dir() else repaired_h320 / kind
    return batch_v1 / f"h{height_mm}" / kind / "tex128"


def floor_summary(report: dict[str, Any], prefix: str) -> dict[str, Any]:
    rows = [row for row in report["doorways"]
            if row["doorway_prim"].startswith(prefix)]
    return {
        "doorway_count": len(rows),
        "failed_doorway_count": sum(not row["pass"] for row in rows),
        "minimum_clear_opening_m": (
            round(min(float(row["minimum_clear_opening_m"]) for row in rows), 4)
            if rows else None),
        "pass": all(row["pass"] for row in rows),
        "doorways": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--h1-package", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--scenes-root", type=Path, required=True)
    parser.add_argument("--stair-batch-v1", type=Path, required=True)
    parser.add_argument("--repaired-h320", type=Path, required=True)
    parser.add_argument("--repaired-stage-b", type=Path)
    parser.add_argument("--build-script", type=Path, required=True)
    args = parser.parse_args()
    if args.out_root.exists():
        raise FileExistsError("refusing to overwrite build root: " + str(args.out_root))
    args.out_root.mkdir(parents=True)

    bids = sorted(
        path.name for path in args.h1_package.iterdir()
        if path.is_dir() and path.name.startswith("home_")
    )
    if len(bids) != 10:
        raise ValueError(f"expected 10 selected candidates, found {len(bids)}")
    env = os.environ.copy()
    env["BUILDING_SCENES_ROOT"] = str(args.scenes_root.resolve())
    rows = []
    stage_b_assets: dict[tuple[int, str], dict[str, Any]] = {}
    for index, bid in enumerate(bids, 1):
        selected = load(
            args.h1_package / bid / "placement" / "a4_candidate.json")["selected"]
        kind = kind_from_bid(bid)
        height_mm = height_mm_from_bid(bid)
        height_m = height_mm / 100.0
        root = stair_root(
            height_mm, kind, args.stair_batch_v1, args.repaired_h320,
            args.repaired_stage_b)
        stair = root / "staircase.usd"
        traversal = root / "traversal_path.json"
        audit_path = root / "usd_audit.json"
        if not all(path.is_file() for path in (stair, traversal, audit_path)):
            raise FileNotFoundError("missing stair asset beneath " + str(root))
        audit = load(audit_path)
        strict = {name: audit.get("checks", {}).get(name) is True
                  for name in STRICT_GATES}
        path_doc = load(traversal)
        rise_contract_path = root / "stair_rise_contract.json"
        rise_contract = load(rise_contract_path) if rise_contract_path.is_file() else None
        first_tread = (
            float(rise_contract["first_rise_m"])
            if rise_contract is not None else
            min(float(point[2]) for point in path_doc["points_xyz_m"])
        )
        asset = {
            "height_mm": height_mm,
            "kind": kind,
            "asset_root": str(root),
            "staircase_usd_sha256": sha256(stair),
            "traversal_path_sha256": sha256(traversal),
            "usd_audit_sha256": sha256(audit_path),
            "strict_normalization_gates": strict,
            "strict_normalization_pass": all(strict.values()),
            "ground_to_first_tread_m": round(first_tread, 6),
            "ground_to_first_tread_limit_m": 0.20,
            "ground_to_first_tread_pass": first_tread <= 0.20 + 1e-9,
            "ground_to_first_tread_source": (
                "exact authored walking-surface stair_rise_contract"
                if rise_contract is not None else "traversal path fallback"),
            "exact_rise_contract_pass": (
                rise_contract.get("passes") is True
                if rise_contract is not None else None),
        }
        if (not asset["strict_normalization_pass"]
                or not asset["ground_to_first_tread_pass"]
                or (rise_contract is not None
                    and not asset["exact_rise_contract_pass"])):
            raise ValueError("stair asset gate failed: " + json.dumps(asset))
        stage_b_assets[(height_mm, kind)] = asset

        building = args.out_root / bid
        scene_dir = building / "scene"
        reports = building / "reports"
        logs = building / "logs"
        for path in (scene_dir, reports, logs):
            path.mkdir(parents=True)
        placement = {
            "rect": selected["rect"],
            "room_rect": selected["room_rect"],
            "rotation_deg": selected["rotation_deg"],
        }
        command = [
            sys.executable, str(args.build_script),
            "--floor1-scene", selected["floor1"],
            "--floor2-scene", selected["floor2"],
            "--kind", kind,
            "--stair", str(stair),
            "--stair-path", str(traversal),
            "--placement", json.dumps(placement, separators=(",", ":")),
            "--floor-to-floor", str(height_m),
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
        if build_report.get("status") != "pass":
            raise RuntimeError(f"build gates failed for {bid}")

        stage = Usd.Stage.Open(str(scene_dir / "building.usda"), Usd.Stage.LoadAll)
        if stage is None:
            raise RuntimeError("built stage did not reopen: " + bid)
        doorway = scan_stage(stage)
        doorway["storeys"] = {
            "Floor1": floor_summary(doorway, "/World/Floor1/"),
            "Floor2": floor_summary(doorway, "/World/Floor2/"),
        }
        doorway["pass"] = all(
            record["pass"] for record in doorway["storeys"].values())
        doorway["source"] = "composed generated building.usda after door policy"
        (reports / "a4_v2_doorway.json").write_text(
            json.dumps(doorway, indent=2) + "\n", encoding="utf-8")
        if not doorway["pass"]:
            raise RuntimeError(f"A4 v2 doorway gate failed for {bid}")
        row = {
            "BID": bid,
            "floor1_scene": selected["floor1"],
            "floor2_scene": selected["floor2"],
            "height_mm": height_mm,
            "kind": kind,
            "stage_b_asset": asset,
            "build_status": build_report["status"],
            "door_policy_disabled_count": build_report[
                "door_collision_policy"]["authored_collision_disabled_count"],
            "A4_v2": {
                floor: {key: value for key, value in summary.items()
                        if key != "doorways"}
                for floor, summary in doorway["storeys"].items()
            },
            "status": "PASS",
        }
        rows.append(row)
        print(f"DONE {index}/10 {bid}", flush=True)

    stage_b = {
        "schema": "StageBStairAssetRemediationV1",
        "status": "PASS",
        "assets": list(stage_b_assets.values()),
        "h320_candidate_count_regenerated_through_stage_b": sum(
            row["height_mm"] == 320 for row in rows),
        "height_substitution_used": False,
    }
    (args.out_root.parent / "stage_b_summary.json").write_text(
        json.dumps(stage_b, indent=2) + "\n", encoding="utf-8")
    summary = {
        "schema": "SelectedBatchStageCRemediationV1",
        "status": "PASS",
        "building_count": len(rows),
        "pass_count": sum(row["status"] == "PASS" for row in rows),
        "fail_count": sum(row["status"] != "PASS" for row in rows),
        "rows": rows,
    }
    (args.out_root / "stage_c_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in (
        "status", "building_count", "pass_count", "fail_count")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
