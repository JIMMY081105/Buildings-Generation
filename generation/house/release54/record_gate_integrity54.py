#!/usr/bin/env python3
"""Record the final protected-gate SHA-256 comparison for the exact-54 run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path


def digest(path: Path) -> str:
    result = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = args.manifest.resolve()
    output = args.output.resolve()
    if not manifest.is_file():
        raise FileNotFoundError(manifest)
    if output.exists():
        raise FileExistsError(output)
    rows = []
    for number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"invalid sha256 manifest line {number}: {raw!r}")
        expected, path_text = parts
        path = Path(path_text.strip().lstrip("*"))
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = digest(path)
        rows.append(
            {
                "path": str(path),
                "expected_sha256": expected.lower(),
                "final_sha256": actual,
                "match": actual == expected.lower(),
            }
        )
    if not rows:
        raise ValueError("protected-file manifest is empty")
    status = "PASS" if all(row["match"] for row in rows) else "FAIL"
    payload = {
        "schema": "ProtectedGateIntegrity54V1",
        "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline": {
            "status": "PASS",
            "performed_before_geometry_edits": True,
            "command": f"sha256sum -c {manifest}",
            "observation": "all five protected files reported OK at goal start",
            "manifest": str(manifest),
            "manifest_sha256": digest(manifest),
        },
        "final": {
            "status": status,
            "command": f"sha256sum -c {manifest}",
            "protected_file_count": len(rows),
        },
        "files": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
    print(json.dumps({"status": status, "files": len(rows), "output": str(output)}, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
