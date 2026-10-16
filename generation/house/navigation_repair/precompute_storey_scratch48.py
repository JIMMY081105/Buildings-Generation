#!/usr/bin/env python3
"""Precompute USDZ storey-band rasterization for the exact H1-approved 48.

This is deliberately limited to the Stage-D/G25-independent rasterization
substep.  It does not create navigation, graph, capacity, or release verdicts.
Every cache is bound to the exact delivered USDZ SHA-256 and is published one
building at a time so an interrupted run can resume without recomputation.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "vendor"))
sys.path.insert(0, str(HERE.parent / "ProtoMotions-main"))

from build_multistorey_graphs import _load_scratch, _save_scratch  # noqa: E402
from protomotions.simulator.base_simulator.scene_storey_bands import (  # noqa: E402
    rasterize_storey_bands,
)


EXPECTED = 48
DEFERRED = {
    "home_train123_train142_straight_v001_h320",
    "home_train84_train64_straight_v001_h270",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def read_decisions(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    require(len(rows) == 50, "candidate review must contain exactly 50 rows")
    decisions: dict[str, str] = {}
    for row in rows:
        bid = row.get("BID") or row.get("building") or row.get("candidate")
        require(isinstance(bid, str) and bid, "candidate row lacks BID")
        require(bid not in decisions, f"duplicate candidate decision: {bid}")
        decisions[bid] = str(row.get("human_decision", ""))
    return decisions


def binding_payload(bid: str, usdz: Path, scratch: Path) -> dict[str, Any]:
    return {
        "schema": "StoreyBandScratchBindingV1",
        "building": bid,
        "source_usdz": str(usdz.resolve()),
        "source_usdz_sha256": file_sha256(usdz),
        "scratch": str(scratch.resolve()),
        "scratch_sha256": file_sha256(scratch),
    }


def exclusive_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    require(not path.exists(), f"refusing pre-existing JSON: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_publish_scratch(path: Path, extraction: Any) -> None:
    require(not path.exists(), f"refusing pre-existing scratch: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.stem}.{uuid4().hex}.partial{path.suffix}")
    try:
        _save_scratch(temporary, extraction)
        _load_scratch(temporary)
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_or_recover(
    bid: str,
    usdz: Path,
    scratch: Path,
    binding: Path,
) -> dict[str, Any] | None:
    if binding.exists() and not scratch.is_file():
        raise ValueError(f"{bid}: orphan scratch binding exists")
    if not scratch.is_file():
        return None
    require(scratch.stat().st_size > 0, f"{bid}: scratch is empty")
    _load_scratch(scratch)
    expected = binding_payload(bid, usdz, scratch)
    if binding.is_file():
        require(load_json(binding) == expected, f"{bid}: scratch binding differs")
    else:
        exclusive_atomic_json(binding, expected)
    return expected


def precompute_one(bid: str, delivery_arg: str, scratch_arg: str) -> dict[str, Any]:
    started = now_utc()
    delivery_root = Path(delivery_arg).resolve()
    scratch_root = Path(scratch_arg).resolve()
    usdz = (delivery_root / bid / "scene" / "building.usdz").resolve()
    require(usdz.is_file() and usdz.stat().st_size > 0, f"missing USDZ: {usdz}")
    scratch = scratch_root / f"{bid}.storey_bands_scratch.npz"
    binding = scratch.with_suffix(scratch.suffix + ".binding.json")
    verified = verify_or_recover(bid, usdz, scratch, binding)
    disposition = "VERIFIED_EXISTING" if verified is not None else "COMPUTED"
    if verified is None:
        extraction = rasterize_storey_bands(usdz)
        extraction.validate()
        atomic_publish_scratch(scratch, extraction)
        expected = binding_payload(bid, usdz, scratch)
        exclusive_atomic_json(binding, expected)
        verified = verify_or_recover(bid, usdz, scratch, binding)
        require(verified == expected, f"{bid}: post-publish verification differs")
    return {
        "BID": bid,
        "status": "PASS",
        "disposition": disposition,
        "started_at_utc": started,
        "finished_at_utc": now_utc(),
        "source_usdz": str(usdz),
        "source_usdz_sha256": verified["source_usdz_sha256"],
        "scratch": str(scratch.resolve()),
        "scratch_sha256": verified["scratch_sha256"],
        "binding": str(binding.resolve()),
        "binding_sha256": file_sha256(binding),
    }


def atomic_progress(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h1-package", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    require(1 <= args.workers <= 4, "workers must be in [1, 4]")
    h1 = args.h1_package.expanduser().resolve()
    delivery = args.delivery_root.expanduser().resolve()
    scratch_root = args.scratch_root.expanduser().resolve()
    summary = args.summary.expanduser().resolve()
    manifest_path = h1 / "manifest.json"
    review_path = h1 / "candidate_review.csv"
    require(manifest_path.is_file(), f"missing H1 manifest: {manifest_path}")
    require(review_path.is_file(), f"missing candidate review: {review_path}")
    manifest = load_json(manifest_path)
    decisions = read_decisions(review_path)
    approved = sorted(manifest.get("approved_building_bids", []))
    require(manifest.get("status") == "H1_APPROVED_48_DEFERRED_2",
            "H1 manifest status differs")
    require(len(approved) == EXPECTED and len(set(approved)) == EXPECTED,
            "H1 approved set is not 48 unique BIDs")
    require(set(manifest.get("deferred_building_bids", [])) == DEFERRED,
            "H1 deferred set differs")
    require(set(decisions) == set(approved) | DEFERRED,
            "candidate decision set differs")
    require(all(decisions[bid] == "approve" for bid in approved),
            "an approved BID lacks decision=approve")
    require(all(decisions[bid] == "deferred_no_gpu_spatial_review"
                for bid in DEFERRED), "a deferred decision differs")
    require(manifest.get("graph_token_capacity_evidence_invalidated") is True,
            "capacity invalidation flag is absent")
    require(manifest.get("graph_token_capacity_change_authorized_now") is False,
            "capacity change was prematurely authorized")
    require(manifest.get("OBSERVATION_MANIFEST_FROZEN") is False,
            "T7 is already frozen")
    require(delivery.is_dir(), f"missing delivery root: {delivery}")

    scratch_root.mkdir(parents=True, exist_ok=True)
    started = now_utc()
    rows: dict[str, dict[str, Any]] = {}
    failures: dict[str, dict[str, str]] = {}

    def write_progress() -> None:
        payload = {
            "schema": "StoreyBandScratchPrecompute48V1",
            "status": "PASS" if len(rows) == EXPECTED and not failures else
                      ("FAIL" if failures else "IN_PROGRESS"),
            "scope": "USDZ_TO_STOREY_BANDS_ONLY",
            "expected_building_count": EXPECTED,
            "completed_building_count": len(rows),
            "failed_building_count": len(failures),
            "remaining_building_count": EXPECTED - len(rows) - len(failures),
            "started_at_utc": started,
            "updated_at_utc": now_utc(),
            "workers": args.workers,
            "h1_manifest": str(manifest_path.resolve()),
            "h1_manifest_sha256": file_sha256(manifest_path),
            "candidate_review": str(review_path.resolve()),
            "candidate_review_sha256": file_sha256(review_path),
            "scratch_root": str(scratch_root),
            "rows": [rows[bid] for bid in sorted(rows)],
            "failures": [failures[bid] for bid in sorted(failures)],
            "claims": {
                "isaac_validation_performed": False,
                "g25_performed": False,
                "graph_extraction_performed": False,
                "capacity_evidence_produced": False,
                "training_authorized": False,
                "OBSERVATION_MANIFEST_FROZEN": False,
            },
        }
        atomic_progress(summary, payload)

    write_progress()
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                precompute_one, bid, str(delivery), str(scratch_root)): bid
            for bid in approved
        }
        for future in as_completed(futures):
            bid = futures[future]
            try:
                rows[bid] = future.result()
                print(
                    f"scratch {len(rows) + len(failures)}/{EXPECTED} "
                    f"{bid} PASS {rows[bid]['disposition']}", flush=True)
            except Exception as error:
                failures[bid] = {
                    "BID": bid,
                    "status": "FAIL",
                    "error": f"{type(error).__name__}: {error}",
                }
                print(
                    f"scratch {len(rows) + len(failures)}/{EXPECTED} "
                    f"{bid} FAIL {error}", flush=True)
            write_progress()

    write_progress()
    if failures or len(rows) != EXPECTED:
        print(
            f"STOREY_SCRATCH_PRECOMPUTE_FAIL completed={len(rows)} "
            f"failed={len(failures)}", flush=True)
        return 1
    print(f"STOREY_SCRATCH_PRECOMPUTE_PASS buildings={EXPECTED}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
