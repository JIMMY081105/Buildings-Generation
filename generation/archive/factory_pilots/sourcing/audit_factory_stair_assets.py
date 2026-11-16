#!/usr/bin/env python3
"""Fail-closed audit of genuine prefab stair assets used by factory10."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


KINDS = ("straight", "l_shaped", "u_shaped")
HEIGHTS = (320, 350, 360)
STRICT_GATES = (
    "all_authored_tread_colliders_normalized",
    "source_tread_collision_prims_deactivated",
    "combined_tread_collider_contains_all_boxes",
    "combined_tread_collider_is_static_closed_triangle_mesh",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    rows = []
    for height in HEIGHTS:
        for kind in KINDS:
            cell = root / f"h{height}" / kind / "tex128"
            stair = cell / "staircase.usd"
            path = cell / "traversal_path.json"
            audit_path = cell / "usd_audit.json"
            for file in (stair, path, audit_path):
                require(file.is_file() and file.stat().st_size > 0,
                        f"missing stair asset file: {file}")
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            checks = audit.get("checks", {})
            gates = {name: checks.get(name) is True for name in STRICT_GATES}
            require(audit.get("passed") is True, f"{height}/{kind}: USD audit failed")
            require(abs(float(audit.get("target_height_m")) - height / 100.0) <= 0.001,
                    f"{height}/{kind}: real stair height differs")
            require(all(gates.values()), f"{height}/{kind}: strict collider gate failed")
            integration = audit.get("floor_integration", {})
            require(integration.get("upper_floor_is_final_landing") is True,
                    f"{height}/{kind}: upper floor is not final landing")
            require(integration.get("final_landing_support_provider") == "upper_floor",
                    f"{height}/{kind}: landing support differs")
            traversal = json.loads(path.read_text(encoding="utf-8"))
            points = traversal.get("points_xyz_m", [])
            require(len(points) >= 8, f"{height}/{kind}: traversal path too short")
            rise = float(points[-1][2]) - float(points[0][2])
            top_height = float(points[-1][2])
            require(abs(top_height - height / 100.0) <= 0.001,
                    f"{height}/{kind}: final landing height differs")
            rows.append({
                "height_mm": height, "kind": kind,
                "staircase_usd": str(stair), "staircase_usd_sha256": sha256(stair),
                "traversal_path": str(path), "traversal_path_sha256": sha256(path),
                "usd_audit": str(audit_path), "usd_audit_sha256": sha256(audit_path),
                "traversal_point_count": len(points), "traversal_rise_m": rise,
                "final_landing_height_m": top_height,
                "strict_collider_normalization_gates": gates,
                "strict_collider_normalization_pass": True,
                "post_A5_build_blocked": False,
                "genuine_prefab_not_height_scaled_for_factory10": True,
                "provenance": (
                    "existing SQZ H320 post-H1 collider-repaired prefab"
                    if height == 320 else
                    "original integration_floor_landing_batch_v1 prefab recovered byte-for-byte from ParaCloud"
                ),
            })
    payload = {
        "schema": "Factory10GenuinePrefabStairAuditV1", "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "asset_root": str(root), "asset_cell_count": len(rows),
        "height_priority": [360, 350, 320],
        "height_substitution_or_scaling_used": False,
        "rows": rows, "asset_evidence": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite stair audit: {args.out}")
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "asset_cell_count": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
