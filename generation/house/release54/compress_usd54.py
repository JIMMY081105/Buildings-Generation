#!/usr/bin/env python3
"""Compress the exact-54 flat USD export into resumable per-file payloads."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import subprocess
from pathlib import Path


R3 = Path(
    "/cephfs/shared/yixing/outputs/building_poc/"
    "batch_20260831_50_h1_r1/post_h1_50_r1/"
    "post_stage_d50_cpu_results_r3"
)
SOURCE = R3 / "unify54" / "usd_flat54"
DESTINATION = R3 / "unify54" / "usd_zstd3"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compress(row: dict) -> dict:
    source = SOURCE / row["filename"]
    output = DESTINATION / f"{row['filename']}.zst"
    assert source.is_file(), source
    if not output.is_file():
        subprocess.run(
            [
                "zstd",
                "-3",
                "-T4",
                "-f",
                "--no-progress",
                str(source),
                "-o",
                str(output),
            ],
            check=True,
        )
    subprocess.run(["zstd", "-t", "--no-progress", str(output)], check=True)
    return {
        "building": row["building"],
        "filename": row["filename"],
        "raw_bytes": row["bytes"],
        "raw_sha256": row["sha256"],
        "compressed_filename": output.name,
        "compressed_bytes": output.stat().st_size,
        "compressed_sha256": sha256(output),
    }


def main() -> None:
    source_manifest = json.loads((SOURCE / "USD_MANIFEST.json").read_text(encoding="utf-8"))
    assert source_manifest["status"] == "PASS"
    assert source_manifest["count"] == 54
    assert len(source_manifest["rows"]) == 54
    DESTINATION.mkdir(parents=True, exist_ok=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(compress, source_manifest["rows"]))
    rows.sort(key=lambda row: row["building"])

    result = {
        "schema": "usd_zstd_transfer_manifest_v1",
        "status": "PASS",
        "count": len(rows),
        "raw_total_bytes": sum(row["raw_bytes"] for row in rows),
        "compressed_total_bytes": sum(row["compressed_bytes"] for row in rows),
        "rows": rows,
    }
    (DESTINATION / "USD_ZSTD_MANIFEST.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: result[key] for key in result if key != "rows"}))


if __name__ == "__main__":
    main()
