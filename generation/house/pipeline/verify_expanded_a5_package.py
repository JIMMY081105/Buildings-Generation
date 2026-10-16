#!/usr/bin/env python3
"""Verify integrity and authorization boundaries of an expanded CPU A5 package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object: " + str(path))
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify(package: Path) -> dict:
    proof_path = package / "A5_EXPANDED_PACKAGE_PASS.json"
    proof = load(proof_path)
    require(proof.get("package_contract_version") == 1,
            "expanded A5 proof contract is stale")
    require(proof.get("status") == "PASS", "expanded A5 proof is not PASS")
    require(proof.get("scope") ==
            "expanded_official_scene_pool_CPU_A1_through_A5_only",
            "expanded A5 scope mismatch")
    for field in ("building_assembly_ran", "isaac_validation_ran", "gpu_used",
                  "gpu_queue_mutated", "automatic_batch_generation_authorized",
                  "batch_generation_authorized"):
        require(proof.get(field) is False, "proof boundary failed: " + field)
    listed = {}
    for record in proof.get("files", []):
        relative = Path(record["path"])
        require(not relative.is_absolute() and ".." not in relative.parts,
                "unsafe proof path")
        key = relative.as_posix()
        require(key not in listed, "duplicate proof path: " + key)
        path = package / relative
        require(path.is_file(), "proved file is missing: " + key)
        require(path.stat().st_size == int(record["bytes"]),
                "proved byte count changed: " + key)
        require(sha256(path) == record["sha256"],
                "proved file hash changed: " + key)
        listed[key] = record
    actual = {path.relative_to(package).as_posix()
              for path in package.rglob("*") if path.is_file()}
    require(actual == set(listed) | {proof_path.name},
            "package has unproved, missing, or extra files")

    manifest = load(package / "manifest.json")
    require(manifest.get("batch_generation_authorized") is False,
            "manifest unexpectedly authorized a batch")
    require(manifest.get("prior_approved_building_count") == 1,
            "accepted restored single building is absent")
    buildings = manifest.get("buildings", [])
    blocked = []
    for record in buildings:
        collider_pass = record.get("strict_collider_normalization_pass")
        build_blocked = record.get("post_A5_build_blocked")
        require(isinstance(collider_pass, bool) and isinstance(build_blocked, bool),
                "candidate lacks strict collider/build-block decision")
        require(build_blocked is (not collider_pass),
                "candidate collider/build-block decision is inconsistent")
        require(isinstance(record.get("strict_collider_failed_gates"), list),
                "candidate lacks collider failed-gate list")
        if build_blocked:
            blocked.append(record["BID"])
    require(manifest.get("strict_collider_blocked_candidate_count") == len(blocked),
            "blocked-candidate summary mismatch")
    with (package / "candidate_review.csv").open(
            encoding="utf-8-sig", newline="") as stream:
        review = {row["BID"]: row for row in csv.DictReader(stream)}
    decisions = load(package / "candidate_decisions.pending.json")
    decision_by_bid = {row["BID"]: row for row in decisions.get("candidates", [])}
    require(set(review) == {row["BID"] for row in buildings},
            "candidate review rows differ from manifest")
    require(set(decision_by_bid) == set(review),
            "pending decision rows differ from manifest")
    for bid in blocked:
        require(review[bid].get("pipeline_status") ==
                "FAIL_POST_A5_BUILD_BLOCKED_STRICT_COLLIDER_NORMALIZATION",
                "collider-blocked candidate is not FAIL in candidate review")
        require(decision_by_bid[bid].get("decision") == "REJECT_REQUIRED",
                "collider-blocked candidate is not rejected in pending decisions")
    auth_path = package / "expanded_pool_provenance" / "AUTHORIZATION.json"
    auth = load(auth_path)
    require(sha256(auth_path) == proof.get("authorization_sha256"),
            "proof refers to a different authorization")
    for field in ("building_assembly_authorized", "isaac_validation_authorized",
                  "gpu_use_authorized", "gpu_queue_mutation_authorized",
                  "automatic_batch_generation_authorized",
                  "batch_generation_authorized"):
        require(auth.get(field) is False, "authorization boundary failed: " + field)
    objects = load(package / "expanded_pool_provenance" / "object_install.json")
    require(objects.get("missing_dependency_count") == 0,
            "object dependency installation is incomplete")
    require(objects.get("replacement_record_count") == 0,
            "expanded pool contains replacement records")
    require(objects.get("installation_kind") ==
            "official_same_asset_id_and_path_not_replacement",
            "object source/equivalence policy mismatch")
    forbidden = [path.relative_to(package).as_posix() for path in package.rglob("*")
                 if path.is_file() and (path.name == "building.usda"
                                        or path.name == "isaac_validation.json")]
    require(not forbidden, "post-A5 output found in CPU-only package")
    return {
        "status": "PASS",
        "file_count": len(listed),
        "a5_status": manifest["status"],
        "review_candidate_count": manifest["review_candidate_count"],
        "dependency_eligible_candidate_count": manifest[
            "dependency_eligible_candidate_count"],
        "strict_collider_blocked_candidate_count": len(blocked),
        "batch_generation_authorized": False,
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        package = Path(directory)
        (package / "manifest.json").write_text(json.dumps({
            "status": "AWAITING_HUMAN_APPROVAL",
            "batch_generation_authorized": False,
            "prior_approved_building_count": 1,
            "review_candidate_count": 0,
            "dependency_eligible_candidate_count": 0,
            "strict_collider_blocked_candidate_count": 0,
            "buildings": [],
        }), encoding="utf-8")
        (package / "candidate_review.csv").write_text("BID,pipeline_status\n",
                                                       encoding="utf-8-sig")
        (package / "candidate_decisions.pending.json").write_text(
            json.dumps({"candidates": []}), encoding="utf-8")
        provenance = package / "expanded_pool_provenance"
        provenance.mkdir()
        (provenance / "AUTHORIZATION.json").write_text(json.dumps({
            "building_assembly_authorized": False,
            "isaac_validation_authorized": False,
            "gpu_use_authorized": False,
            "gpu_queue_mutation_authorized": False,
            "automatic_batch_generation_authorized": False,
            "batch_generation_authorized": False,
        }), encoding="utf-8")
        (provenance / "object_install.json").write_text(json.dumps({
            "missing_dependency_count": 0,
            "replacement_record_count": 0,
            "installation_kind": "official_same_asset_id_and_path_not_replacement",
        }), encoding="utf-8")
        files = [{"path": path.relative_to(package).as_posix(),
                  "bytes": path.stat().st_size, "sha256": sha256(path)}
                 for path in sorted(package.rglob("*")) if path.is_file()]
        proof = {
            "package_contract_version": 1, "status": "PASS",
            "scope": "expanded_official_scene_pool_CPU_A1_through_A5_only",
            "authorization_sha256": sha256(provenance / "AUTHORIZATION.json"),
            "files": files, "building_assembly_ran": False,
            "isaac_validation_ran": False, "gpu_used": False,
            "gpu_queue_mutated": False,
            "automatic_batch_generation_authorized": False,
            "batch_generation_authorized": False,
        }
        (package / "A5_EXPANDED_PACKAGE_PASS.json").write_text(
            json.dumps(proof), encoding="utf-8")
        require(verify(package)["status"] == "PASS", "positive control failed")
        with (package / "manifest.json").open("a", encoding="utf-8") as stream:
            stream.write(" ")
        try:
            verify(package)
        except ValueError:
            print(json.dumps({"positive_control": "PASS",
                              "tampered_file_negative_control": "FAIL_AS_EXPECTED"},
                             indent=2))
            return
        raise AssertionError("tampered package passed verification")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.package is None:
        raise SystemExit("--package is required unless --self-test is used")
    print(json.dumps(verify(args.package), indent=2))


if __name__ == "__main__":
    main()
