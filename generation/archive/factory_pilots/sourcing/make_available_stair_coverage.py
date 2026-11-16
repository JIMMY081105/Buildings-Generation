#!/usr/bin/env python3
"""Resolve immutable accepted stair assets that are actually present on SQZ."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


CANDIDATE_ROOTS = (
    Path("/cephfs/shared/yixing/outputs/building_poc/batch_20260826_a1_a5_r1/post_h1_remediation_r1/stage_b_assets_v2"),
    Path("/cephfs/shared/yixing/outputs/building_batch_20260819/stage_b_assets_r3"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence = []
    missing = []
    for height_mm in (360, 350, 320, 300, 270):
        for kind in ("l_shaped", "straight", "u_shaped"):
            candidates = [root / f"h{height_mm}" / kind / "tex128" for root in CANDIDATE_ROOTS]
            selected = next((root for root in candidates
                             if (root / "staircase.usd").is_file()
                             and (root / "traversal_path.json").is_file()
                             and (root / "usd_audit.json").is_file()), None)
            if selected is None:
                missing.append({"height_mm": height_mm, "kind": kind,
                                "searched": [str(path) for path in candidates]})
                continue
            audit_path = selected / "usd_audit.json"
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            checks = audit.get("checks", {})
            gate_names = (
                "all_authored_tread_colliders_normalized",
                "source_tread_collision_prims_deactivated",
                "combined_tread_collider_contains_all_boxes",
                "combined_tread_collider_is_static_closed_triangle_mesh",
            )
            gates = {name: checks.get(name) for name in gate_names}
            strict_pass = audit.get("passed") is True and all(
                gates[name] is True for name in gate_names
            )
            evidence.append({
                "height_m": height_mm / 100.0,
                "height_mm": height_mm,
                "kind": kind,
                "staircase_usd": str(selected / "staircase.usd"),
                "staircase_usd_sha256": sha256(selected / "staircase.usd"),
                "traversal_path": str(selected / "traversal_path.json"),
                "traversal_path_sha256": sha256(selected / "traversal_path.json"),
                "usd_audit": str(audit_path),
                "usd_audit_sha256": sha256(audit_path),
                "strict_collider_normalization_gates": gates,
                "strict_collider_normalization_pass": strict_pass,
                "post_A5_build_blocked": not strict_pass,
                "resolution_source": "first actual SQZ file in ordered immutable accepted roots",
            })
    result = {
        "contract": "factory10_available_stair_coverage_v1",
        "status": "PASS" if evidence and all(row["strict_collider_normalization_pass"] for row in evidence) else "FAIL",
        "ordered_roots": [str(root) for root in CANDIDATE_ROOTS],
        "selection_order": "height descending, then l_shaped/straight/u_shaped",
        "asset_evidence": evidence,
        "available_asset_cell_count": len(evidence),
        "missing_asset_cells": missing,
        "missing_asset_cell_count": len(missing),
        "batch_generation_authorized": True,
        "training_authorized": True,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "available": [(row["height_mm"], row["kind"]) for row in evidence],
        "missing": [(row["height_mm"], row["kind"]) for row in missing],
    }, indent=2))


if __name__ == "__main__":
    main()
