#!/usr/bin/env python3
"""Fail-capably verify the contract-v2 H1 hold amendment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    package = args.package
    hold_path = package / "H1_HOLD_CLOSED_COLLIDABLE_DOORS.json"
    rule_path = package / "DOORWAY_RULE_FIXTURE_RESULT_V2.json"
    policy_path = package / "CLOSED_DOOR_POLICY_DECISION_REQUIRED.json"
    cuts_path = package / "closed_doors_and_minimum_cuts_v2.json"
    hold, rule, policy, cuts = map(load, (hold_path, rule_path, policy_path, cuts_path))
    require(hold["status"] == "HOLD_DO_NOT_APPROVE", "H1 hold is absent")
    require(hold["candidate_split_under_current_machine_gates"] == {
        "pending": 0,
        "reject_required_strict_stair_collider_normalization": 5,
        "reject_required_closed_collidable_doors": 5,
    }, "held machine split differs")
    require(rule["contract_version"] == 2
            and rule["status"] == "PASS_RULE_PROOF_H1_CANDIDATES_RED",
            "corrected doorway contract proof is absent")
    require(rule["pending_candidates_tested"] == 5
            and rule["pending_candidates_passing_corrected_gate"] == 0,
            "a formerly pending candidate did not go RED")
    require(policy["status"] == "PENDING_HUMAN_DECISION"
            and policy["codex_selected_option"] is None,
            "Codex selected the human closed-door policy")
    require(cuts["status"] == "PASS_WITH_RETAINED_TWO_CELL_FLOOR_SUPPORT_FINDING",
            "minimum-cut evidence is incomplete")
    require(len(cuts["buildings"]) == 3, "minimum-cut building scope differs")
    require("H1 HOLD — DO NOT APPROVE" in
            (package / "index.html").read_text(encoding="utf-8"),
            "review page does not display the H1 hold")
    require(not list(package.rglob("decision.json")),
            "decision.json exists despite the H1 hold")
    require(len(list(package.rglob("floorplan_*.png"))) == 20,
            "retained H1 floorplan evidence differs")
    audit = {
        "status": "H1_HOLD_VERIFIED",
        "doorway_clearance_contract_version": 2,
        "candidate_machine_pending_count": 0,
        "candidate_machine_reject_required_count": 10,
        "human_closed_door_policy": "PENDING",
        "gpu_used": False,
        "training_started": False,
        "decision_json_written": False,
        "delivered_usdz_modified": False,
        "batch_20260819_h2_reopened": False,
        "evidence_sha256": {
            hold_path.name: sha256(hold_path),
            rule_path.name: sha256(rule_path),
            policy_path.name: sha256(policy_path),
            cuts_path.name: sha256(cuts_path),
            "index.html": sha256(package / "index.html"),
        },
    }
    output = package / "H1_HOLD_AUDIT.json"
    output.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
