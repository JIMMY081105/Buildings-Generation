#!/usr/bin/env python3
"""Promote exact-10 H1 only after immutable SQZ A40 Stage D evidence passes."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


EXPECTED = 10
INITIAL_STATUS = "H1_APPROVED_10_UNSUPERVISED_DIRECT_USER_AUTHORIZATION"
FINAL_STATUS = "H1_APPROVED_10_AFTER_SQZ_A40_REVIEW"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def csv_bytes(fieldnames: list[str], rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def atomic_replace(path: Path, payload: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def exclusive_write(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h1-package", type=Path, required=True)
    parser.add_argument("--stage-d-summary", type=Path, required=True)
    args = parser.parse_args()
    package = args.h1_package.expanduser().resolve()
    stage_path = args.stage_d_summary.expanduser().resolve()
    manifest_path = package / "manifest.json"
    review_path = package / "candidate_review.csv"
    decision_path = package / "H1_DECISION_RECORD.json"
    applied_path = package / "H1_DECISION_APPLIED.json"
    for path in (manifest_path, review_path, decision_path, applied_path, stage_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(path)
    manifest = load(manifest_path)
    decision = load(decision_path)
    stage = load(stage_path)
    approved = sorted(manifest.get("approved_building_bids", []))
    if not (
        manifest.get("status") == INITIAL_STATUS
        and manifest.get("h1_placement_approved") is True
        and manifest.get("h1_human_visual_approval_claimed") is False
        and manifest.get("h1_isaac_review_claimed") is False
        and manifest.get("h1_final_status_pending_sqz_a40_review") is True
        and len(approved) == EXPECTED and len(set(approved)) == EXPECTED
        and manifest.get("deferred_building_count") == 0
        and manifest.get("deferred_building_bids") == []
        and manifest.get("training_authorized") is False
    ):
        raise ValueError("H1 package is not at the exact pre-A40 boundary")
    if not (
        decision.get("human_visual_approval_claimed") is False
        and decision.get("isaac_review_claimed") is False
        and decision.get("approved_count") == EXPECTED
        and sorted(decision.get("approved_bids", [])) == approved
    ):
        raise ValueError("initial autonomous decision record differs")
    rows = stage.get("rows", [])
    if not (
        stage.get("schema") == "StageD10FactoryA40IsaacBatchSummaryV1"
        and stage.get("status") == "PASS"
        and stage.get("building_count") == EXPECTED
        and stage.get("passed_buildings") == EXPECTED
        and stage.get("failed_buildings") == 0
        and stage.get("actual_a40_required_and_proved_per_building") is True
        and stage.get("cpu_renderer_substitution_used") is False
        and stage.get("human_visual_approval_claimed") is False
        and {row.get("BID") for row in rows} == set(approved)
    ):
        raise ValueError("Stage D is not an exact-10 A40 PASS")
    for row in rows:
        verdict = Path(row["stage_b_isaac_verdict"])
        report = Path(row["isaac_report"])
        if not verdict.is_file() or sha256(verdict) != row.get("stage_b_isaac_verdict_sha256"):
            raise ValueError(f"verdict binding differs: {row.get('BID')}")
        if not report.is_file() or sha256(report) != row.get("isaac_report_sha256"):
            raise ValueError(f"Isaac report binding differs: {row.get('BID')}")
        if not (row.get("status") == "PASS"
                and row.get("device") == "NVIDIA A40"
                and row.get("actual_a40_active_device_logged") is True
                and row.get("cpu_renderer_substitution_used") is False
                and row.get("failed_gates") == []
                and row.get("blocking_failures") == []):
            raise ValueError(f"A40 row is not a clean PASS: {row.get('BID')}")

    with review_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError("candidate review has no header")
        fieldnames = list(reader.fieldnames)
        review_rows = list(reader)
    if len(review_rows) != EXPECTED or {row["BID"] for row in review_rows} != set(approved):
        raise ValueError("candidate review set differs")
    if any(row.get("human_decision") != "approve" for row in review_rows):
        raise ValueError("candidate review compatibility decisions differ")

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    event = "h1_after_sqz_a40_exact10_" + now.replace(":", "").replace("-", "")
    history = package / "decision_history" / event
    history.mkdir(parents=True, exist_ok=False)
    for path in (manifest_path, review_path, decision_path, applied_path):
        shutil.copy2(path, history / path.name)
    shutil.copy2(stage_path, history / stage_path.name)

    binding = {
        "schema": "Factory10H1SQZA40ReviewBindingV1",
        "status": "PASS",
        "recorded_at_utc": now,
        "pre_review_h1_status": INITIAL_STATUS,
        "post_review_h1_status": FINAL_STATUS,
        "approved_count": EXPECTED,
        "approved_bids": approved,
        "stage_d_summary": str(stage_path),
        "stage_d_summary_sha256": sha256(stage_path),
        "pre_review_manifest_sha256": sha256(manifest_path),
        "pre_review_candidate_review_sha256": sha256(review_path),
        "initial_decision_record_sha256": sha256(decision_path),
        "initial_decision_application_sha256": sha256(applied_path),
        "actual_device": "NVIDIA A40",
        "actual_a40_proved_per_building": True,
        "cpu_renderer_substitution_used": False,
        "human_visual_approval_claimed": False,
        "isaac_machine_review_completed": True,
        "training_authorized": False,
        "snapshot": str(history),
    }
    binding_path = package / "H1_SQZ_A40_REVIEW_BINDING.json"
    exclusive_write(binding_path, json_bytes(binding))

    for row in review_rows:
        row["pipeline_status"] = "H1_APPROVED_AFTER_SQZ_A40_REVIEW"
        row["gpu_spatial_review_required"] = "False"
        row["review_notes"] = (
            "Direct-user-authorized autonomous advance; exact Stage D machine review "
            "PASS on SQZ NVIDIA A40. No human visual approval claimed."
        )
    atomic_replace(review_path, csv_bytes(fieldnames, review_rows))
    manifest.update({
        "status": FINAL_STATUS,
        "h1_human_visual_approval_claimed": False,
        "h1_isaac_review_claimed": True,
        "h1_isaac_review_kind": "exact Stage D machine review on SQZ NVIDIA A40",
        "h1_final_status_pending_sqz_a40_review": False,
        "h1_sqz_a40_review_completed_at_utc": now,
        "h1_sqz_a40_review_binding": binding_path.name,
        "h1_sqz_a40_review_binding_sha256": sha256(binding_path),
        "h1_stage_d_summary": str(stage_path),
        "h1_stage_d_summary_sha256": sha256(stage_path),
        "deferred_building_count": 0,
        "deferred_building_bids": [],
        "deferred_buildings_are_failures": False,
        "training_authorized": False,
    })
    atomic_replace(manifest_path, json_bytes(manifest))
    audit = {
        "schema": "Factory10H1AfterSQZA40ApplicationAuditV1",
        "status": "PASS",
        "recorded_at_utc": now,
        "approved_count": EXPECTED,
        "final_status": FINAL_STATUS,
        "actual_device": "NVIDIA A40",
        "human_visual_approval_claimed": False,
        "isaac_machine_review_completed": True,
        "training_authorized": False,
        "post_review_sha256": {
            "manifest.json": sha256(manifest_path),
            "candidate_review.csv": sha256(review_path),
            binding_path.name: sha256(binding_path),
        },
    }
    audit_path = package / "H1_SQZ_A40_REVIEW_APPLIED.json"
    exclusive_write(audit_path, json_bytes(audit))
    print(json.dumps(audit, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
