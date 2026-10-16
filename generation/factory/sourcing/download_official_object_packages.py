#!/usr/bin/env python3
"""Download exact official USD object-package ranges with resumable evidence."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time


BASE_URL = "https://huggingface.co/datasets/allenai/molmospaces/resolve/main/isaac/objects"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bucket_for(directory: str) -> str:
    value = hashlib.sha256(directory.encode("utf-8")).digest()[0] & 1
    return f"bucket{value}"


def download_one(row: dict, root: Path) -> dict:
    source = row["source"]
    version = row["version"]
    offset = int(row["offset"])
    size = int(row["size"])
    end = offset + size - 1
    bucket = bucket_for(row["directory"])
    package = root / bucket / row["package"]
    package.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    reused = False
    if package.exists():
        if package.stat().st_size != size:
            raise RuntimeError(f"existing package has wrong size: {package}")
        reused = True
    else:
        part = package.with_suffix(package.suffix + ".part")
        header = package.with_suffix(package.suffix + ".headers.part")
        if part.exists() or header.exists():
            raise RuntimeError(f"stale partial download requires audit: {package}")
        url = f"{BASE_URL}/{source}/{version}/shards/{int(row['shard_id']):05d}.tar"
        subprocess.run([
            "curl", "--location", "--fail", "--silent", "--show-error",
            "--retry", "10", "--retry-all-errors", "--retry-delay", "3",
            "--connect-timeout", "20", "--max-time", "900",
            "--header", f"Range: bytes={offset}-{end}",
            "--dump-header", str(header), "--output", str(part), url,
        ], check=True)
        if part.stat().st_size != size:
            raise RuntimeError(f"download size mismatch for {row['package']}")
        header_text = header.read_text(encoding="latin-1").lower()
        if f"content-range: bytes {offset}-{end}/" not in header_text:
            raise RuntimeError(f"exact HTTP range was not honored for {row['package']}")
        os.replace(part, package)
        header.rename(package.with_suffix(package.suffix + ".headers"))
    return {
        **row,
        "status": "PASS",
        "bucket": bucket,
        "package_path": str(package),
        "sha256": sha256(package),
        "download_reused": reused,
        "elapsed_s": round(time.monotonic() - started, 3),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--jsonl", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    rows = json.loads(args.selection.read_text(encoding="utf-8"))["selected"]
    args.jsonl.parent.mkdir(parents=True, exist_ok=True)
    passed = 0
    failures = []
    lock = threading.Lock()
    with args.jsonl.open("a", encoding="utf-8") as output:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(download_one, row, args.package_root): row for row in rows}
            for index, future in enumerate(as_completed(futures), 1):
                source = futures[future]
                try:
                    result = future.result()
                    passed += 1
                except Exception as error:
                    result = {
                        "status": "FAIL", "source": source["source"],
                        "directory": source["directory"], "package": source["package"],
                        "error": f"{type(error).__name__}: {error}",
                        "completed_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    failures.append(result)
                with lock:
                    output.write(json.dumps(result) + "\n")
                    output.flush()
                if index % 25 == 0 or index == len(rows):
                    print(json.dumps({"done": index, "total": len(rows), "passed": passed,
                                      "failed": len(failures)}), flush=True)
    if failures:
        raise RuntimeError(f"object download failed for {len(failures)} packages")
    print(json.dumps({"status": "PASS", "downloaded": passed}), flush=True)


if __name__ == "__main__":
    main()
