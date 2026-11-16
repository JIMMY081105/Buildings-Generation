#!/usr/bin/env python3
"""Combine disjoint official scene selections with provenance and hash binding."""

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
    parser.add_argument("--selection", type=Path, action="append", required=True)
    parser.add_argument("--label", action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if len(args.selection) != len(args.label):
        raise ValueError("each selection requires one label")
    if len(set(args.label)) != len(args.label):
        raise ValueError("selection labels must be unique")
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite combined selection: {args.out}")
    selected = []
    sources = []
    seen = set()
    for label, path in zip(args.label, args.selection):
        document = json.loads(path.read_text(encoding="utf-8"))
        rows = document["selected"]
        for row in rows:
            if row["scene"] in seen:
                raise RuntimeError(f"duplicate scene across selections: {row['scene']}")
            seen.add(row["scene"])
            selected.append({**row, "factory10_selection_round": label})
        sources.append({
            "label": label, "path": str(path.resolve()), "sha256": sha256(path),
            "selected_count": len(rows),
        })
    output = {
        "schema": "Factory10CombinedOfficialSceneSelectionV1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_selections": sources,
        "selected_count": len(selected),
        "selected_total_bytes": sum(int(row["size"]) for row in selected),
        "selected": selected,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "selected_count": len(selected),
        "selected_total_bytes": output["selected_total_bytes"],
    }, indent=2))


if __name__ == "__main__":
    main()
