#!/usr/bin/env python3
"""Freeze explicit per-candidate A5 decisions into a C/D input manifest.

This does not build anything. It validates the immutable A5 package and allows
only candidates explicitly named by the human decision record to progress.
Candidates with a positive-area red 2-D projection overlap cannot be marked as
placement-approved before GPU spatial evidence exists.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


APPROVE = "APPROVE_PLACEMENT_AND_AUTHORIZE_BUILD_GPU"
EVIDENCE = "AUTHORIZE_BUILD_GPU_EVIDENCE_ONLY"
REJECT = "REJECT"
ALLOWED = {APPROVE, EVIDENCE, REJECT}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object: " + str(path))
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify_package(package: Path) -> tuple[dict, dict]:
    proof_path = package / "A5_PACKAGE_PASS.json"
    proof = load(proof_path)
    require(proof.get("status") == "pass", "A5 package pass marker is absent")
    require(proof.get("coverage_cells") == 18, "A5 package did not cover 18 cells")
    require(proof.get("dependency_contract_version") == 2,
            "A5 package predates strict missing-dependency classification")
    require(proof.get("batch_generation_authorized") is False,
            "A5 package marker must remain batch-false")
    listed = {}
    for record in proof.get("files", []):
        relative = Path(record["path"])
        require(not relative.is_absolute() and ".." not in relative.parts,
                "unsafe A5 proof path")
        require(relative.as_posix() not in listed, "duplicate A5 proof path")
        path = package / relative
        require(path.is_file(), "A5 proof file missing: " + str(relative))
        require(path.stat().st_size == int(record["bytes"]),
                "A5 proof byte count changed: " + str(relative))
        require(digest(path) == record["sha256"],
                "A5 proof hash changed: " + str(relative))
        listed[relative.as_posix()] = record
    actual = {str(path.relative_to(package)).replace("\\", "/")
              for path in package.rglob("*") if path.is_file()}
    require(actual == set(listed) | {"A5_PACKAGE_PASS.json"},
            "A5 package contains unproved or unlisted files")
    manifest = load(package / "manifest.json")
    require(manifest.get("covered_cell_count") == 18
            and manifest.get("coverage_cell_count") == 18
            and manifest.get("missing_cell_count") == 0,
            "A5 manifest coverage is incomplete")
    require(manifest.get("batch_generation_authorized") is False,
            "A5 manifest must remain batch-false")
    require(manifest.get("dependency_eligible_candidate_count")
            + manifest.get("dependency_failed_candidate_count")
            == manifest.get("new_candidate_count"),
            "A5 manifest dependency candidate counts are inconsistent")
    require(manifest.get("review_candidate_count") == len(manifest.get("buildings", [])),
            "A5 manifest review candidate count mismatch")
    return proof, manifest


def freeze(package: Path, decision_path: Path, out_dir: Path) -> dict:
    proof, manifest = verify_package(package)
    decision = load(decision_path)
    require(decision.get("decision_contract_version") == 1,
            "A5 decision contract is stale")
    require(decision.get("decision_scope") ==
            "selected_candidates_build_and_gpu_evidence_only",
            "A5 decision scope is not build/GPU-evidence-only")
    source = decision.get("authorization_source")
    require(isinstance(source, str) and source.strip()
            and "PENDING" not in source.upper(),
            "explicit A5 human authorization source is absent")
    require(decision.get("automatic_batch_generation_authorized") is False,
            "automatic batch generation must remain unauthorized")
    require(decision.get("a5_package_pass_sha256") ==
            digest(package / "A5_PACKAGE_PASS.json"),
            "A5 decision refers to a different package")

    buildings = {record["BID"]: record for record in manifest["buildings"]}
    decisions = decision.get("candidates", [])
    require(isinstance(decisions, list), "candidate decisions must be a list")
    by_bid = {}
    for record in decisions:
        bid = record.get("BID")
        require(bid in buildings, "decision names unknown A5 candidate: " + str(bid))
        require(bid not in by_bid, "duplicate A5 candidate decision: " + bid)
        require(record.get("decision") in ALLOWED,
                "candidate decision is pending or invalid: " + bid)
        if record["decision"] == REJECT:
            require(str(record.get("review_notes", "")).strip(),
                    "rejected candidate requires review notes: " + bid)
        by_bid[bid] = record
    require(set(by_bid) == set(buildings),
            "every A5 candidate requires an explicit decision")

    with (package / "candidate_review.csv").open(
            encoding="utf-8-sig", newline="") as stream:
        review_rows = {row["BID"]: row for row in csv.DictReader(stream)}
    require(set(review_rows) == set(buildings),
            "candidate review CSV does not match A5 manifest")

    authorized, rejected = [], []
    for bid, summary in buildings.items():
        row = review_rows[bid]
        plan_path = package / bid / "placement" / "placement.json"
        plan = load(plan_path)
        selected = plan["selected"]
        dependencies = plan.get("source_dependency_audit", {})
        require(dependencies.get("dependency_contract_version") == 2,
                "candidate dependency contract is stale: " + bid)
        require(dependencies.get("all_missing_dependencies_classified") is True,
                "candidate has unclassified missing dependencies: " + bid)
        require(selected["floor1"] == summary["floor1"]
                and selected["floor2"] == summary["floor2"]
                and selected["kind"] == summary["kind"],
                "A5 placement does not match manifest: " + bid)
        require([float(v) for v in selected["rect"]] == [
            float(summary[field]) for field in ("x0", "y0", "x1", "y1")],
            "A5 placement rect does not match manifest: " + bid)
        human = by_bid[bid]
        if (dependencies.get("dependency_gate_pass") is not True
                and human["decision"] != REJECT):
            raise ValueError(
                "critical/unclassified missing dependency requires REJECT: " + bid)
        gpu_required = plan.get("gpu_spatial_review_required") is True
        if gpu_required and human["decision"] == APPROVE:
            raise ValueError(
                "2-D exclusion overlap forbids placement approval before GPU 3-D: "
                + bid)
        frozen = {
            "manifest": summary,
            "placement": selected,
            "a5_review": row,
            "source_dependency_audit": dependencies,
            "human_decision": human,
            "gpu_spatial_review_required_before_final_placement_approval": gpu_required,
            "final_placement_approved": human["decision"] == APPROVE,
            "build_and_gpu_evidence_authorized": human["decision"] in
            (APPROVE, EVIDENCE),
            "a5_placement_sha256": digest(plan_path),
        }
        (rejected if human["decision"] == REJECT else authorized).append(frozen)

    result = {
        "status": "FROZEN_EXPLICIT_CANDIDATE_DECISIONS",
        "decision_contract_version": 1,
        "source_a5_run_id": proof["run_id"],
        "source_a5_package_pass_sha256": digest(
            package / "A5_PACKAGE_PASS.json"),
        "human_decision_sha256": digest(decision_path),
        "authorization_source": source,
        "automatic_batch_generation_authorized": False,
        "authorized_candidate_count": len(authorized),
        "rejected_candidate_count": len(rejected),
        "authorized_buildings": authorized,
        "rejected_buildings": rejected,
    }
    if out_dir.exists():
        raise FileExistsError("refusing to overwrite frozen decisions: " + str(out_dir))
    out_dir.mkdir(parents=True)
    (out_dir / "authorized_manifest.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with (out_dir / "authorized_manifest.tsv").open(
            "w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, dialect="excel-tab", lineterminator="\n")
        for record in authorized:
            summary = record["manifest"]
            writer.writerow([summary[field] for field in (
                "BID", "floor1", "floor2", "kind", "height_m", "x0", "y0",
                "x1", "y1", "rotation_deg", "room", "scene_type")])
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    result = freeze(args.package, args.decisions, args.out_dir)
    print(json.dumps({k: result[k] for k in (
        "status", "authorized_candidate_count", "rejected_candidate_count",
        "automatic_batch_generation_authorized")}, indent=2))


if __name__ == "__main__":
    main()
