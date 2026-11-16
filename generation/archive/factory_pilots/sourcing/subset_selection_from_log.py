#!/usr/bin/env python3
"""Create an immutable selection snapshot from PASS package-log records."""

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    passed = set()
    for line in args.log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("status") == "PASS":
                passed.add(row["package"])
    selected = [row for row in selection["selected"] if row["package"] in passed]
    if not selected:
        raise RuntimeError("log snapshot contains no PASS packages")
    output = {
        **selection,
        "parent_selection": str(args.selection.resolve()),
        "parent_selection_sha256": sha256(args.selection),
        "package_log": str(args.log.resolve()),
        "package_log_sha256": sha256(args.log),
        "snapshot_generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "selected_count": len(selected),
        "selected": selected,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite snapshot: {args.out}")
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected_count": len(selected), "out": str(args.out)}))


if __name__ == "__main__":
    main()
