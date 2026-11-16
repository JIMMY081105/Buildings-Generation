#!/usr/bin/env python3
"""Adapt the audited genuine-stair inventory to the frozen A5 compiler schema."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


KINDS = ("straight", "l_shaped", "u_shaped")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-decision", type=Path, required=True)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite coverage adapter: {args.out}")
    source = json.loads(args.asset_decision.read_text(encoding="utf-8"))
    if source.get("status") != "PASS" or source.get("height_substitution_or_scaling_used") is not False:
        raise ValueError("source stair decision is not a strict PASS without scaling")
    pairs = json.loads(args.pairs.read_text(encoding="utf-8"))
    pair_cells = sorted({
        (round(float(row["floor1_f2f"]), 2), str(row["kind"]))
        for row in pairs["results"] if row.get("usable") is True
    })
    evidence = []
    seen = set()
    for original in source.get("asset_evidence", []):
        row = dict(original)
        height_m = round(float(row["height_mm"]) / 100.0, 2)
        cell = (height_m, str(row["kind"]))
        if row["kind"] not in KINDS or cell in seen:
            raise ValueError(f"invalid or duplicate audited asset cell: {cell}")
        seen.add(cell)
        if row.get("strict_collider_normalization_pass") is not True:
            raise ValueError(f"audited stair cell is not collider-safe: {cell}")
        if row.get("post_A5_build_blocked") is not False:
            raise ValueError(f"audited stair cell is build-blocked: {cell}")
        for path_key, hash_key in (
            ("staircase_usd", "staircase_usd_sha256"),
            ("traversal_path", "traversal_path_sha256"),
            ("usd_audit", "usd_audit_sha256"),
        ):
            path = Path(row[path_key])
            if not path.is_file() or sha256(path) != row[hash_key]:
                raise ValueError(f"audited stair artifact hash mismatch: {path}")
        row["height_m"] = height_m
        row["compiler_schema_adapter"] = "height_m derived exactly as height_mm/100"
        evidence.append(row)
    missing = [
        {
            "height_m": height,
            "kind": kind,
            "reason": "NO_EXISTING_EXACT_FINAL_LANDING_STAIR_ASSET",
            "height_substitution_or_scaling_forbidden": True,
        }
        for height, kind in pair_cells if (height, kind) not in seen
    ]
    output = {
        "schema": "Factory10CompilerStairAssetCoverageV1",
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_asset_decision": str(args.asset_decision.resolve()),
        "source_asset_decision_sha256": sha256(args.asset_decision),
        "source_pair_catalog": str(args.pairs.resolve()),
        "source_pair_catalog_sha256": sha256(args.pairs),
        "height_substitution_or_scaling_used": False,
        "asset_evidence": sorted(evidence, key=lambda row: (row["height_m"], row["kind"])),
        "missing_asset_cells": missing,
        "pair_cell_count": len(pair_cells),
        "audited_asset_cell_count": len(seen.intersection(pair_cells)),
        "missing_asset_cell_count": len(missing),
        "all_pair_cells_classified": set(pair_cells) == seen.intersection(pair_cells).union(
            {(row["height_m"], row["kind"]) for row in missing}
        ),
    }
    if not output["all_pair_cells_classified"]:
        raise RuntimeError("not every strict pair cell was classified")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "status", "pair_cell_count", "audited_asset_cell_count",
        "missing_asset_cell_count", "all_pair_cells_classified",
    )}, indent=2))


if __name__ == "__main__":
    main()
