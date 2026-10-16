#!/usr/bin/env python3
"""Verify every listed result file by size and SHA-256 without modifying the source tree."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(root: Path, row: dict) -> dict:
    relative = PurePosixPath(row["relative_path"])
    if relative.is_absolute() or ".." in relative.parts:
        return {**row, "status": "FAIL", "error": "unsafe manifest path"}
    path = root.joinpath(*relative.parts)
    if not path.is_file():
        return {**row, "status": "FAIL", "error": "file absent"}
    actual_size = path.stat().st_size
    actual_hash = sha256(path)
    passed = actual_size == int(row["bytes"]) and actual_hash == row["sha256"]
    return {
        **row, "status": "PASS" if passed else "FAIL",
        "actual_bytes": actual_size, "actual_sha256": actual_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--phase", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    source_rows = manifest.get("files", [])
    names = [row.get("relative_path") for row in source_rows]
    uniqueness_pass = len(names) == len(set(names)) and all(isinstance(name, str) for name in names)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(lambda row: check(root, row), source_rows))
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*") if path.is_file() and path != args.out.resolve()
    }
    listed = set(names)
    # The source manifest never lists itself by design.
    unlisted = sorted(actual_files - listed - {manifest_path.relative_to(root).as_posix()})
    failures = [row for row in rows if row["status"] != "PASS"]
    payload = {
        "schema": "ResultsManifestIndependentVerificationV1",
        "phase": args.phase,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": str(root),
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": sha256(manifest_path),
        "source_manifest_declared_status": manifest.get("status"),
        "listed_file_count": len(rows),
        "unique_relative_paths": uniqueness_pass,
        "passed_file_count": len(rows) - len(failures),
        "failed_file_count": len(failures),
        "status": "PASS" if uniqueness_pass and not failures else "FAIL",
        "unlisted_files_nonblocking": unlisted,
        "failures": failures,
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite verification: {args.out}")
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "phase", "status", "listed_file_count", "passed_file_count",
        "failed_file_count", "unlisted_files_nonblocking")}, indent=2, ensure_ascii=False))
    if payload["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
