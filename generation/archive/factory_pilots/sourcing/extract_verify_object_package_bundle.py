#!/usr/bin/env python3
"""Verify, safely extract, and re-verify a transferred object-package bundle."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out_root.exists() or args.out.exists():
        raise FileExistsError("refusing to overwrite extracted bundle outputs")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    actual_archive = (args.archive.stat().st_size, sha256(args.archive))
    expected_archive = (
        int(manifest["archive_size"]), str(manifest["archive_sha256"]).lower()
    )
    if actual_archive != expected_archive:
        raise RuntimeError(
            f"transferred bundle mismatch: {actual_archive} != {expected_archive}"
        )
    expected = {row["relative_path"]: row for row in manifest["packages"]}
    with tarfile.open(args.archive, "r") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
    names = [member.name for member in members]
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"unsafe bundle member: {name}")
    if len(names) != len(set(names)):
        raise RuntimeError("bundle has duplicate file members")
    if set(names) != set(expected):
        raise RuntimeError("bundle member set differs from manifest")
    args.out_root.mkdir(parents=True)
    subprocess.run(["tar", "-xf", str(args.archive), "-C", str(args.out_root)], check=True)
    mismatches = []
    for relative, wanted in expected.items():
        path = args.out_root / relative
        actual = (path.stat().st_size, sha256(path)) if path.is_file() else None
        expected_value = (int(wanted["size"]), str(wanted["sha256"]).lower())
        if actual != expected_value:
            mismatches.append({
                "path": relative, "actual": actual, "expected": expected_value,
            })
    output = {
        "schema": "Factory10TransferredOfficialObjectBundleVerificationV1",
        "status": "PASS" if not mismatches else "FAIL",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "archive": str(args.archive.resolve()),
        "archive_size": actual_archive[0],
        "archive_sha256": actual_archive[1],
        "manifest": str(args.manifest.resolve()),
        "manifest_sha256": sha256(args.manifest),
        "package_count": len(expected),
        "member_count": len(names),
        "unsafe_member_count": 0,
        "duplicate_member_count": 0,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "out_root": str(args.out_root.resolve()),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "status", "archive_size", "archive_sha256", "package_count",
        "mismatch_count",
    )}, indent=2))
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
