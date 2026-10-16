#!/usr/bin/env python3
"""Link verified geometry-only objects into the task overlay for A3/A4 only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--geometry-root", type=Path, required=True)
    parser.add_argument("--overlay-root", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite overlay report: {args.out}")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    verification = json.loads(args.verification.read_text(encoding="utf-8"))
    if manifest.get("status") != "PASS" or verification.get("status") != "PASS":
        raise ValueError("geometry-only manifest/verification is not PASS")
    if verification.get("tree_sha256") != manifest.get("tree_sha256"):
        raise ValueError("geometry-only verification tree hash is not bound to manifest")
    geometry_root = args.geometry_root.resolve()
    objaverse_root = args.overlay_root.resolve() / "objaverse"
    objaverse_root.mkdir(parents=True, exist_ok=True)
    linked, reused_same, preexisting_full = [], [], []
    for row in manifest["packages"]:
        directory = row["directory"]
        source = geometry_root / directory
        required = source / f"{directory}.usda"
        if not required.is_file() or required.stat().st_size == 0:
            raise FileNotFoundError(f"verified source object disappeared: {required}")
        target = objaverse_root / directory
        target_required = target / f"{directory}.usda"
        if target.is_symlink():
            if target.resolve() == source:
                reused_same.append(directory)
                continue
            raise RuntimeError(f"conflicting overlay symlink: {target} -> {target.resolve()}")
        if target.exists():
            if target_required.is_file() and target_required.stat().st_size > 0:
                preexisting_full.append(directory)
                continue
            raise RuntimeError(f"incomplete pre-existing overlay directory: {target}")
        target.symlink_to(source, target_is_directory=True)
        if not target_required.is_file():
            raise RuntimeError(f"new geometry-only overlay link is unresolved: {target}")
        linked.append(directory)
    missing = []
    for row in manifest["packages"]:
        expected = objaverse_root / row["directory"] / f"{row['directory']}.usda"
        if not expected.is_file() or expected.stat().st_size == 0:
            missing.append(str(expected))
    output = {
        "schema": "Factory10GeometryOnlyObjectOverlayV1",
        "status": "PASS" if not missing else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "role": "dependency_doorway_and_A4_geometry_validation_only",
        "prohibited_uses": ["visual_release", "final_build", "final_USDZ_packaging"],
        "manifest": str(args.manifest.resolve()),
        "manifest_sha256": sha256(args.manifest),
        "verification": str(args.verification.resolve()),
        "verification_sha256": sha256(args.verification),
        "geometry_root": str(geometry_root),
        "overlay_root": str(args.overlay_root.resolve()),
        "object_count": len(manifest["packages"]),
        "linked_geometry_only_count": len(linked),
        "reused_same_geometry_link_count": len(reused_same),
        "preexisting_full_count": len(preexisting_full),
        "linked_geometry_only": linked,
        "reused_same_geometry_links": reused_same,
        "preexisting_full": preexisting_full,
        "missing": missing,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "status", "object_count", "linked_geometry_only_count",
        "reused_same_geometry_link_count", "preexisting_full_count",
    )}, indent=2))
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
