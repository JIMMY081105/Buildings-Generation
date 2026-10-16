#!/usr/bin/env python3
"""Verify the extracted prefilter scene-layer tree against its immutable manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--verification-schema",
        default="Factory10ScreeningSceneLayersVerificationV1",
    )
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite verification: {args.out}")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    expected = {row["path"]: row for row in manifest["files"]}
    actual_paths = sorted(
        path.relative_to(args.root).as_posix()
        for path in args.root.rglob("*") if path.is_file()
    )
    missing = sorted(set(expected).difference(actual_paths))
    extra = sorted(set(actual_paths).difference(expected))
    mismatches = []
    tree_digest = hashlib.sha256()
    for relative in actual_paths:
        path = args.root / Path(relative)
        row = {"path": relative, "size": path.stat().st_size, "sha256": sha256(path)}
        wanted = expected.get(relative)
        if wanted is not None and row != wanted:
            mismatches.append({"actual": row, "expected": wanted})
        tree_digest.update(
            f"{row['path']}\0{row['size']}\0{row['sha256']}\n".encode("utf-8")
        )
    tree_hash = tree_digest.hexdigest()
    textures = [path for path in actual_paths if "/Textures/" in f"/{path}"]
    passed = (
        not missing and not extra and not mismatches and not textures
        and tree_hash == manifest["tree_sha256"]
        and len(actual_paths) == int(manifest["extracted_file_count"])
    )
    output = {
        "schema": args.verification_schema,
        "status": "PASS" if passed else "FAIL",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(args.root.resolve()),
        "manifest": str(args.manifest.resolve()),
        "manifest_sha256": sha256(args.manifest),
        "file_count": len(actual_paths),
        "tree_sha256": tree_hash,
        "expected_tree_sha256": manifest["tree_sha256"],
        "missing": missing,
        "extra": extra,
        "mismatches": mismatches,
        "texture_files": textures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": output["status"], "file_count": len(actual_paths),
        "tree_sha256": tree_hash, "missing": len(missing),
        "extra": len(extra), "mismatches": len(mismatches),
    }, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
