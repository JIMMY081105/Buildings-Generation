"""The generation snapshot is the code that generated the buildings.

`generation/MANIFEST.json` pins the size and SHA-256 of every snapshot file.
`house/` and `archive/` are byte-identical to their origin. `factory/` and
`museum/` replace versioned module names (``layout_v7``) by role names
(``hall_layout``) and change nothing else: substituting the old names back
must reproduce each file's ``original_sha256``.

These tests re-hash the tree and prove each audit can fail: a changed byte, an
unlisted file, an edit beyond the renaming and a missing rename entry must each
be reported.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath

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


def restore_original_names(data: bytes, renames: dict[str, str]) -> bytes:
    """Undo a role-name rename: every new module name goes back to the old one."""
    back = {new: old for old, new in renames.items()}
    names = "|".join(sorted(map(re.escape, back), key=len, reverse=True))
    pattern = re.compile(rf"(?<![A-Za-z0-9_])({names})(?![A-Za-z0-9_])")
    return pattern.sub(lambda m: back[m.group(1)], data.decode("utf-8")).encode("utf-8")


def audit_renames(root: Path, manifest: dict) -> list[str]:
    problems = []
    groups = manifest["renamed_modules"]
    for row in manifest["files"]:
        if "original_sha256" not in row:
            continue
        group = next((g for g in groups if row["path"].startswith(g + "/")), None)
        rel = row["path"].removeprefix("generation/")
        if group is None:
            problems.append(f"renamed outside a renamed group: {rel}")
            continue
        back = {new: old for old, new in groups[group].items()}
        name = PurePosixPath(rel)
        if back.get(name.stem, name.stem) + name.suffix != PurePosixPath(row["origin"]).name:
            problems.append(f"name: {rel}")
        restored = restore_original_names((root / rel).read_bytes(), groups[group])
        if (
            len(restored) != row["original_bytes"]
            or hashlib.sha256(restored).hexdigest() != row["original_sha256"]
        ):
            problems.append(f"original: {rel}")
    return problems


def test_renamed_files_differ_from_their_origin_only_by_module_names():
    manifest = _manifest()
    renamed = [r for r in manifest["files"] if "original_sha256" in r]
    assert len(renamed) > 50
    assert audit_renames(ROOT, manifest) == []


def test_rename_audit_detects_an_edit_beyond_the_renaming(tmp_path):
    copy = tmp_path / "generation"
    shutil.copytree(ROOT, copy)
    manifest = _manifest()
    row = next(r for r in manifest["files"] if "original_sha256" in r)
    victim = copy / row["path"].removeprefix("generation/")
    victim.write_bytes(victim.read_bytes() + b"#")
    rel = row["path"].removeprefix("generation/")
    assert audit_renames(copy, manifest) == [f"original: {rel}"]


def test_rename_audit_detects_a_missing_rename_entry():
    manifest = _manifest()
    renames = manifest["renamed_modules"]["generation/museum"]
    renames.pop("museum_nordic28")
    problems = audit_renames(ROOT, manifest)
    assert "name: museum/museum_nordic.py" in problems
    assert "original: museum/generate_museum.py" in problems
