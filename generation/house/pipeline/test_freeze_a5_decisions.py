#!/usr/bin/env python3
"""Fail-capable checks for the A5 human-decision freeze boundary."""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
from pathlib import Path

from freeze_a5_decisions import APPROVE, EVIDENCE, REJECT, freeze


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def must_fail(call, label: str) -> None:
    try:
        call()
    except (ValueError, FileExistsError):
        return
    raise AssertionError("negative freeze control passed: " + label)


def make_package(root: Path, overlap: bool) -> tuple[Path, str]:
    package = root / "package"; package.mkdir()
    bid = "home_f1_f2_straight_h320"
    summary = {"BID": bid, "floor1": "f1", "floor2": "f2",
               "kind": "straight", "height_m": 3.2,
               "x0": 0, "y0": 1, "x1": 2, "y1": 6,
               "rotation_deg": 0, "room": "r", "scene_type": "home"}
    write(package / "manifest.json", {
        "covered_cell_count": 18, "coverage_cell_count": 18,
        "missing_cell_count": 0, "batch_generation_authorized": False,
        "review_candidate_count": 1, "new_candidate_count": 1,
        "dependency_eligible_candidate_count": 1,
        "dependency_failed_candidate_count": 0,
        "buildings": [summary]})
    placement = dict(summary)
    placement.update({"rect": [0, 1, 2, 6],
                      "gpu_spatial_review_required": overlap})
    write(package / bid / "placement" / "placement.json", {
        "selected": placement, "gpu_spatial_review_required": overlap,
        "source_dependency_audit": {
            "dependency_contract_version": 2,
            "all_missing_dependencies_classified": True,
            "dependency_gate_pass": True,
            "missing_dependencies": [],
            "critical_missing_dependency_count": 0,
        }})
    with (package / "candidate_review.csv").open(
            "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("BID", "pipeline_status"))
        writer.writeheader(); writer.writerow({"BID": bid, "pipeline_status": "review"})
    write(package / "candidate_decisions.pending.json", {"pending": True})
    files=[]
    for path in sorted(p for p in package.rglob("*") if p.is_file()):
        files.append({"path":str(path.relative_to(package)).replace("\\","/"),
                      "bytes":path.stat().st_size,"sha256":digest(path)})
    write(package / "A5_PACKAGE_PASS.json", {
        "status":"pass","run_id":"a5_test","coverage_cells":18,
        "dependency_contract_version":2,
        "batch_generation_authorized":False,"files":files})
    return package,bid


def decision(package: Path, bid: str, value: str) -> dict:
    return {"decision_contract_version":1,
            "decision_scope":"selected_candidates_build_and_gpu_evidence_only",
            "authorization_source":"explicit unit-test approval",
            "a5_package_pass_sha256":digest(package/"A5_PACKAGE_PASS.json"),
            "automatic_batch_generation_authorized":False,
            "candidates":[{"BID":bid,"decision":value,
                            "review_notes":"reason" if value==REJECT else ""}]}


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp); package,bid=make_package(root, overlap=True)
        decisions=root/"decisions.json"; write(decisions,decision(package,bid,EVIDENCE))
        result=freeze(package,decisions,root/"frozen")
        assert result["authorized_candidate_count"]==1
        assert result["authorized_buildings"][0]["final_placement_approved"] is False
        assert result["automatic_batch_generation_authorized"] is False
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp); package,bid=make_package(root, overlap=True)
        decisions=root/"decisions.json"; write(decisions,decision(package,bid,APPROVE))
        must_fail(lambda: freeze(package,decisions,root/"frozen"),
                  "placement approved despite 2-D exclusion overlap")
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp); package,bid=make_package(root, overlap=False)
        decisions=root/"decisions.json"; write(decisions,decision(package,bid,APPROVE))
        result=freeze(package,decisions,root/"frozen")
        assert result["authorized_buildings"][0]["final_placement_approved"] is True
        (package/"manifest.json").write_text("tampered")
        must_fail(lambda: freeze(package,decisions,root/"second"),
                  "tampered A5 package")
    print("PASS: A5 decision freeze, package hashes, and GPU-evidence-only gate")


if __name__ == "__main__":
    main()
