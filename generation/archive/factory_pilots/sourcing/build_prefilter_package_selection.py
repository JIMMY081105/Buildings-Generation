#!/usr/bin/env python3
"""Bind strict prefilter survivors to official package and PASS download records."""

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
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--prefilter", type=Path, required=True)
    parser.add_argument("--download-log", type=Path, required=True)
    parser.add_argument("--selection-out", type=Path, required=True)
    parser.add_argument("--download-log-out", type=Path, required=True)
    args = parser.parse_args()
    if args.selection_out.exists() or args.download_log_out.exists():
        raise FileExistsError("refusing to overwrite prefilter package evidence")
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    prefilter = json.loads(args.prefilter.read_text(encoding="utf-8"))
    wanted = set(prefilter["potential_scenes"])
    selected = [row for row in selection["selected"] if row["scene"] in wanted]
    if len(selected) != len(wanted) or {row["scene"] for row in selected} != wanted:
        raise RuntimeError("prefilter scene set does not bind exactly to package selection")
    latest = {}
    for line in args.download_log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("status") == "PASS":
                latest[row["scene"]] = row
    records = [latest[row["scene"]] for row in selected]
    if any(record["package"] != row["package"] for row, record in zip(selected, records)):
        raise RuntimeError("download evidence package mismatch")
    output = {
        **{key: value for key, value in selection.items()
           if key not in ("selected", "selected_count")},
        "schema": "Factory10StrictPrefilterOfficialPackageSelectionV1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "parent_selection": str(args.selection.resolve()),
        "parent_selection_sha256": sha256(args.selection),
        "strict_prefilter": str(args.prefilter.resolve()),
        "strict_prefilter_sha256": sha256(args.prefilter),
        "dependency_gate_deferred_not_waived": True,
        "selected_count": len(selected),
        "selected_total_bytes": sum(int(row["size"]) for row in selected),
        "selected": selected,
    }
    args.selection_out.parent.mkdir(parents=True, exist_ok=True)
    args.selection_out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    args.download_log_out.write_text(
        "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
    )
    print(json.dumps({
        "status": "PASS", "selected_count": len(selected),
        "selected_total_bytes": output["selected_total_bytes"],
        "download_pass_records": len(records),
    }, indent=2))


if __name__ == "__main__":
    main()
