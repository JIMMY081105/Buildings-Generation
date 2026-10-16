#!/usr/bin/env python3
"""Verify that the already-hashed release was installed without loss or extras."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--quarantine-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.root
    manifest_path = root / "MANIFEST_SHA256.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    errors: list[str] = []
    if manifest.get("status") != "PASS":
        errors.append("Installed manifest is not PASS")
    expected_counts = manifest.get("counts", {})
    for key in ("buildings", "usd", "tokens", "graphs", "token_load_pass"):
        if expected_counts.get(key) != 54:
            errors.append(f"Installed manifest {key} is not 54")
    if len(manifest.get("files", [])) != 278:
        errors.append("Installed manifest does not list 278 hashed payload files")

    expected = {row["relative_path"]: row for row in manifest.get("files", [])}
    actual = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] == args.quarantine_name or relative.as_posix() == "MANIFEST_SHA256.json":
            continue
        actual[relative.as_posix()] = path

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        errors.append(f"Missing installed files: {missing}")
    if extra:
        errors.append(f"Unexpected installed files: {extra}")
    size_mismatches = []
    for relative in sorted(set(expected) & set(actual)):
        observed = actual[relative].stat().st_size
        wanted = expected[relative]["bytes"]
        if observed != wanted:
            size_mismatches.append({"relative_path": relative, "expected": wanted, "actual": observed})
    if size_mismatches:
        errors.append(f"Installed size mismatches: {len(size_mismatches)}")

    actual_counts = {
        "usd": len(list((root / "USD").glob("*.usdz"))),
        "tokens": len(list((root / "Tokens").glob("*.pt"))),
        "graphs": len(list((root / "Graphs").glob("*.npz"))),
    }
    if any(value != 54 for value in actual_counts.values()):
        errors.append(f"Installed exact-54 counts failed: {actual_counts}")

    spot_paths = [
        "README.md",
        "USD/USD_MANIFEST.json",
        "Tokens/GRAPH_TOKENS_MANIFEST_V1.json",
        "Graphs/GRAPH_ACCEPTANCE.json",
        "Reports/Collision_Audit/COLLISION_AUDIT_54.json",
        "Reports/Door_Opening/DOOR_OPENING_54.json",
    ]
    spot_hashes = {}
    for relative in spot_paths:
        path = root / Path(relative)
        if relative not in expected or not path.is_file():
            errors.append(f"Missing spot-check file: {relative}")
            continue
        actual_hash = sha256(path)
        spot_hashes[relative] = actual_hash
        if actual_hash != expected[relative]["sha256"]:
            errors.append(f"Spot-check SHA256 mismatch: {relative}")

    result = {
        "schema": "installed_f_results_verification_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not errors else "FAIL",
        "root": str(root),
        "manifest_sha256": sha256(manifest_path),
        "manifest_payload_count": len(expected),
        "actual_payload_count": len(actual),
        "counts": actual_counts,
        "missing": missing,
        "extra": extra,
        "size_mismatches": size_mismatches,
        "spot_hashes": spot_hashes,
        "errors": errors,
    }
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
