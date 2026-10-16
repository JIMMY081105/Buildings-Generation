#!/usr/bin/env python3
"""Verify the organized exact-54 release and write its SHA256 manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import torch


TOKEN_SUFFIX = ".scene_kimodo_graph_tokens_v1.pt"
GRAPH_SUFFIX = ".multistorey_topology_v1.npz"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--usd-transfer-manifest", type=Path, required=True)
    parser.add_argument("--source-copy-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.root
    usd_dir = root / "USD"
    token_dir = root / "Tokens"
    graph_dir = root / "Graphs"
    collision_dir = root / "Reports" / "Collision_Audit"
    door_dir = root / "Reports" / "Door_Opening"
    errors: list[str] = []

    usd_transfer = json.loads(args.usd_transfer_manifest.read_text(encoding="utf-8-sig"))
    source_copy = json.loads(args.source_copy_manifest.read_text(encoding="utf-8-sig"))
    require(usd_transfer.get("status") == "PASS", "USD transfer manifest is not PASS", errors)
    require(usd_transfer.get("count") == 54, "USD transfer manifest is not exact-54", errors)
    require(source_copy.get("status") == "PASS", "source copy manifest is not PASS", errors)

    usd_rows = usd_transfer.get("rows", [])
    expected_ids = {row["building"] for row in usd_rows}
    require(len(expected_ids) == 54, "USD building IDs are not 54 unique values", errors)

    usd_files = sorted(usd_dir.glob("*.usdz"))
    token_files = sorted(token_dir.glob("*.pt"))
    graph_files = sorted(graph_dir.glob("*.npz"))
    collision_files = sorted(collision_dir.glob("*.json"))
    door_files = sorted(door_dir.glob("*.json"))

    usd_ids = {path.name.removesuffix(".usdz") for path in usd_files}
    token_ids = {path.name.removesuffix(TOKEN_SUFFIX) for path in token_files}
    graph_ids = {path.name.removesuffix(GRAPH_SUFFIX) for path in graph_files}
    collision_ids = {path.stem for path in collision_files if path.name != "COLLISION_AUDIT_54.json"}
    door_ids = {
        path.name.removesuffix(".door_opening.json")
        for path in door_files
        if path.name != "DOOR_OPENING_54.json"
    }

    require(len(usd_files) == 54, f"USD count is {len(usd_files)}, expected 54", errors)
    require(len(token_files) == 54, f"Token count is {len(token_files)}, expected 54", errors)
    require(len(graph_files) == 54, f"Graph count is {len(graph_files)}, expected 54", errors)
    require(len(collision_files) == 55, f"Collision report count is {len(collision_files)}, expected 55", errors)
    require(len(door_files) == 55, f"Door report count is {len(door_files)}, expected 55", errors)
    for label, ids in (
        ("USD", usd_ids),
        ("Token", token_ids),
        ("Graph", graph_ids),
        ("Collision", collision_ids),
        ("Door", door_ids),
    ):
        require(ids == expected_ids, f"{label} building-ID set differs from exact-54", errors)

    for label, path in (
        ("Collision", collision_dir / "COLLISION_AUDIT_54.json"),
        ("Door", door_dir / "DOOR_OPENING_54.json"),
    ):
        if not path.is_file():
            errors.append(f"Missing {label} aggregate: {path.name}")
            continue
        aggregate = json.loads(path.read_text(encoding="utf-8-sig"))
        require(aggregate.get("status") == "PASS", f"{label} aggregate is not PASS", errors)
        require(
            len(aggregate.get("rows", [])) == 54,
            f"{label} aggregate does not contain 54 rows",
            errors,
        )

    hashes: dict[str, str] = {}
    bytes_by_relative_path: dict[str, int] = {}
    for usd_index, row in enumerate(usd_rows, start=1):
        path = usd_dir / row["filename"]
        if not path.is_file():
            errors.append(f"Missing USD: {path.name}")
            continue
        actual_hash = sha256(path)
        hashes[path.relative_to(root).as_posix()] = actual_hash
        bytes_by_relative_path[path.relative_to(root).as_posix()] = path.stat().st_size
        require(path.stat().st_size == row["raw_bytes"], f"USD size mismatch: {path.name}", errors)
        require(actual_hash == row["raw_sha256"], f"USD SHA256 mismatch: {path.name}", errors)
        if usd_index % 5 == 0 or usd_index == len(usd_rows):
            print(f"USD_SHA_PROGRESS {usd_index}/{len(usd_rows)}", flush=True)

    prefix_map = {
        "graph_tokens/": "Tokens/",
        "graphs/": "Graphs/",
        "collision_audit/": "Reports/Collision_Audit/",
        "doors/": "Reports/Door_Opening/",
    }
    verified_source_files = 0
    for row in source_copy.get("files", []):
        old_relative = row["relative_path"].replace("\\", "/")
        new_relative = None
        for old_prefix, new_prefix in prefix_map.items():
            if old_relative.startswith(old_prefix):
                new_relative = new_prefix + old_relative[len(old_prefix) :]
                break
        if new_relative is None:
            continue
        path = root / Path(new_relative)
        if not path.is_file():
            errors.append(f"Missing copied artifact: {new_relative}")
            continue
        actual_hash = sha256(path)
        hashes[new_relative] = actual_hash
        bytes_by_relative_path[new_relative] = path.stat().st_size
        require(path.stat().st_size == row["bytes"], f"Size mismatch: {new_relative}", errors)
        require(actual_hash == row["source_sha256"], f"SHA256 mismatch: {new_relative}", errors)
        verified_source_files += 1
        if verified_source_files % 50 == 0 or verified_source_files == 220:
            print(f"NONUSD_SHA_PROGRESS {verified_source_files}/220", flush=True)
    require(verified_source_files == 220, f"Verified {verified_source_files} copied files, expected 220", errors)

    token_load_failures = []
    for token_index, path in enumerate(token_files, start=1):
        try:
            payload = torch.load(path, map_location="cpu", weights_only=True)
            features = payload["scene_graph_node_features"]
            valid_mask = payload["scene_graph_node_valid_mask"]
            if tuple(features.shape) != (1, 4096, 14) or features.dtype != torch.float32:
                raise ValueError(f"features {tuple(features.shape)} {features.dtype}")
            if tuple(valid_mask.shape) != (1, 4096) or valid_mask.dtype != torch.bool:
                raise ValueError(f"valid_mask {tuple(valid_mask.shape)} {valid_mask.dtype}")
        except Exception as exc:  # noqa: BLE001 - evidence should record every load failure
            token_load_failures.append({"file": path.name, "error": repr(exc)})
        if token_index % 10 == 0 or token_index == len(token_files):
            print(f"TOKEN_LOAD_PROGRESS {token_index}/{len(token_files)}", flush=True)
    require(not token_load_failures, f"Token load failures: {len(token_load_failures)}", errors)

    for extra in (
        root / "README.md",
        root / "Reports" / "TECHNICAL_REPORT.md",
        root / "Reports" / "SOURCE_COPY_MANIFEST.json",
        usd_dir / "USD_MANIFEST.json",
    ):
        if extra.is_file():
            relative = extra.relative_to(root).as_posix()
            hashes[relative] = sha256(extra)
            bytes_by_relative_path[relative] = extra.stat().st_size
        else:
            errors.append(f"Missing documentation file: {extra.relative_to(root).as_posix()}")

    rows = [
        {"relative_path": relative, "bytes": bytes_by_relative_path[relative], "sha256": digest}
        for relative, digest in sorted(hashes.items())
    ]
    result = {
        "schema": "organized_f_results_manifest_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not errors else "FAIL",
        "root_layout": ["USD", "Tokens", "Graphs", "Reports"],
        "counts": {
            "buildings": len(expected_ids),
            "usd": len(usd_files),
            "tokens": len(token_files),
            "graphs": len(graph_files),
            "collision_json": len(collision_files),
            "door_json": len(door_files),
            "token_load_pass": len(token_files) - len(token_load_failures),
            "hashed_files_excluding_manifest": len(rows),
        },
        "building_id_sets_equal": not any("building-ID set" in error for error in errors),
        "token_contract": {
            "features_shape": [1, 4096, 14],
            "features_dtype": "torch.float32",
            "valid_mask_shape": [1, 4096],
            "valid_mask_dtype": "torch.bool",
        },
        "errors": errors,
        "token_load_failures": token_load_failures,
        "files": rows,
    }
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "counts", "building_id_sets_equal", "errors")}, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
