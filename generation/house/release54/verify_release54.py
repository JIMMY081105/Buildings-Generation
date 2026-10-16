#!/usr/bin/env python3
"""Independently verify the assembled exact-54 Ceph release."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

import torch


EXPECTED = 54
CAPACITY = 4096


def digest(path: Path) -> str:
    result = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.release.resolve()
    output = args.output.resolve()
    require(root.is_dir(), "release directory absent")
    require(not output.exists(), "verification output exists")
    require(
        {path.name for path in root.iterdir()}
        == {"graph_tokens", "graphs", "collision_audit", "doors", "README.md", "COPY_MANIFEST.json"},
        "top-level release file set differs",
    )
    copy = load(root / "COPY_MANIFEST.json")
    require(copy.get("status") == "PASS", "copy manifest is not PASS")
    rows = copy.get("files", [])
    require(len(rows) == copy.get("file_count_excluding_manifest") == 221, "copy row count differs")
    expected = {row["relative_path"] for row in rows}
    require(len(expected) == len(rows), "duplicate copy-manifest relative path")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "COPY_MANIFEST.json"
    }
    require(actual == expected, "release payload file set differs")
    for row in rows:
        path = root / row["relative_path"]
        observed = digest(path)
        require(path.stat().st_size == row["bytes"], f"size differs: {path}")
        require(
            row["match"] is True
            and observed == row["source_sha256"] == row["destination_sha256"],
            f"copy digest differs: {path}",
        )

    token_root = root / "graph_tokens"
    token_manifest = load(token_root / "GRAPH_TOKENS_MANIFEST_V1.json")
    token_files = sorted(token_root.glob("*.pt"))
    require(len(token_files) == token_manifest.get("tensor_file_count") == EXPECTED, "tensor count differs")
    token_rows = {row["file"]: row for row in token_manifest.get("artifacts", [])}
    require(set(token_rows) == {path.name for path in token_files}, "token manifest set differs")
    require(token_manifest.get("capacity") == CAPACITY, "token capacity differs")
    for path in token_files:
        row = token_rows[path.name]
        require(digest(path) == row["sha256"], f"tensor digest differs: {path.name}")
        payload = torch.load(path, map_location="cpu", weights_only=True)
        features = payload["scene_graph_node_features"]
        mask = payload["scene_graph_node_valid_mask"]
        require(features.dtype == torch.float32 and tuple(features.shape) == (1, CAPACITY, 14), f"feature contract differs: {path.name}")
        require(mask.dtype == torch.bool and tuple(mask.shape) == (1, CAPACITY), f"mask contract differs: {path.name}")
        require(bool(torch.isfinite(features).all()), f"non-finite tensor: {path.name}")
        require(int(torch.count_nonzero(features[~mask]).item()) == 0, f"padding differs: {path.name}")

    require(len(list((root / "graphs").glob("*.npz"))) == EXPECTED, "graph count differs")
    acceptance = load(root / "graphs" / "GRAPH_ACCEPTANCE.json")
    require(acceptance.get("expected_building_count") == EXPECTED, "acceptance coverage differs")
    require(all(acceptance["gate_pass_counts"][f"G{i}"] == EXPECTED for i in range(1, 9)), "G1-G8 not all PASS")
    require(acceptance["gate_pass_counts"]["G9"] == 52, "G9 count differs")
    require(len(list((root / "collision_audit").glob("*.json"))) == EXPECTED + 1, "audit count differs")
    require(len(list((root / "doors").glob("*.json"))) == EXPECTED + 1, "door count differs")
    readme = (root / "README.md").read_text(encoding="utf-8")
    for text in ("碰撞审计", "开门", "G25 与 G1-G10", "容量与张量", "4096"):
        require(text in readme, f"README text absent: {text}")
    require("ä»" not in readme and "å¼" not in readme, "README contains mojibake")

    payload = {
        "schema": (
            "Exact54WindowsReleaseVerificationV1"
            if os.name == "nt"
            else "Exact54CephReleaseVerificationV1"
        ),
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "release": str(root),
        "payload_file_count": len(rows),
        "tensor_file_count": len(token_files),
        "tensor_shape": [1, CAPACITY, 14],
        "graph_file_count": EXPECTED,
        "per_building_audit_count": EXPECTED,
        "per_building_door_report_count": EXPECTED,
        "gate_pass_counts": acceptance["gate_pass_counts"],
        "all_payload_sha256_match": True,
        "all_tensors_cpu_loadable": True,
        "readme_utf8": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
