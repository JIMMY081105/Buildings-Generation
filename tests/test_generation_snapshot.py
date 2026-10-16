"""The generation snapshot is byte-identical to what generated the buildings.

`generation/MANIFEST.json` pins the size and SHA-256 of every snapshot file.
These tests re-hash the tree, and prove the audit can fail: a changed byte and
an unlisted file must each be reported.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "generation"
MANIFEST = ROOT / "MANIFEST.json"
# Documentation written for this repository, not part of the snapshot.
UNPINNED = {"MANIFEST.json", "README.md"}


def audit_snapshot(root: Path, manifest: dict) -> list[str]:
    problems = []
    listed = set()
    for row in manifest["files"]:
        rel = row["path"].removeprefix("generation/")
        listed.add(rel)
        path = root / rel
        if not path.is_file():
            problems.append(f"missing: {rel}")
            continue
        data = path.read_bytes()
        if len(data) != row["bytes"]:
            problems.append(f"size: {rel}")
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            problems.append(f"sha256: {rel}")
    for path in root.rglob("*"):
        if path.is_file() and path.name not in UNPINNED and "__pycache__" not in path.parts:
            rel = path.relative_to(root).as_posix()
            if rel not in listed:
                problems.append(f"unlisted: {rel}")
    return problems


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_snapshot_matches_manifest():
    manifest = _manifest()
    assert manifest["file_count"] == len(manifest["files"])
    assert audit_snapshot(ROOT, manifest) == []


def test_contract_hashes_are_recorded_as_matches_only():
    # A file is only marked contract-verified when the original run's frozen
    # manifest lists the same SHA-256; the assembler aborts otherwise.
    manifest = _manifest()
    verified = [
        r for r in manifest["files"] if r["contract"] and r["contract"].get("sha256_matches")
    ]
    assert len(verified) == manifest["contract_verified_count"]
    assert all(r["contract"]["sha256_matches"] is True for r in verified)


def test_audit_detects_a_changed_byte(tmp_path):
    copy = tmp_path / "generation"
    shutil.copytree(ROOT, copy)
    manifest = _manifest()
    victim = copy / manifest["files"][0]["path"].removeprefix("generation/")
    data = bytearray(victim.read_bytes())
    data[0] ^= 0x01
    victim.write_bytes(bytes(data))
    problems = audit_snapshot(copy, manifest)
    assert problems == [f"sha256: {victim.relative_to(copy).as_posix()}"]


def test_audit_detects_an_unlisted_file(tmp_path):
    copy = tmp_path / "generation"
    shutil.copytree(ROOT, copy)
    (copy / "house" / "pipeline" / "extra.py").write_text("print('not in the run')\n")
    assert audit_snapshot(copy, _manifest()) == ["unlisted: house/pipeline/extra.py"]
