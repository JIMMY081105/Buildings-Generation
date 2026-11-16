#!/usr/bin/env python3
"""Download exact official package ranges to Ceph and safely extract them."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import threading
import time


BASE_URL = "https://huggingface.co/datasets/allenai/molmospaces/resolve/main/isaac/scenes"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_members(package: Path) -> list[str]:
    result = subprocess.run(
        ["tar", "--use-compress-program=unzstd", "-tf", str(package)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    members = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not members:
        raise RuntimeError("package tar is empty")
    for name in members:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"unsafe tar member: {name}")
    return members


def extracted_ok(root: Path, scene: str) -> bool:
    selected = root / scene
    counterpart = root / (scene.removesuffix("_ceiling"))
    required = (
        selected / "scene.usda",
        selected / "scene_metadata.json",
        counterpart / "scene.usda",
        counterpart / "scene_metadata.json",
    )
    return all(path.is_file() and path.stat().st_size > 0 for path in required)


def download_one(
    row: dict,
    package_root: Path,
    extract_root: Path,
    download_only: bool = False,
    extract_only: bool = False,
) -> dict:
    source = row["source"]
    version = row["version"]
    shard_id = int(row["shard_id"])
    offset = int(row["offset"])
    size = int(row["size"])
    end = offset + size - 1
    package = package_root / source / row["package"]
    package.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    reused_download = False
    if package.exists():
        if package.stat().st_size != size:
            raise RuntimeError(f"existing package has wrong size: {package}")
        reused_download = True
    else:
        if extract_only:
            raise FileNotFoundError(f"extract-only package is missing: {package}")
        part = package.with_suffix(package.suffix + ".part")
        header = package.with_suffix(package.suffix + ".headers.part")
        if part.exists() or header.exists():
            raise RuntimeError(f"stale partial download requires audit: {part}")
        url = f"{BASE_URL}/{source}/{version}/shards/{shard_id:05d}.tar"
        command = [
            "curl", "--location", "--fail", "--silent", "--show-error",
            "--retry", "10", "--retry-all-errors", "--retry-delay", "5",
            "--connect-timeout", "20", "--max-time", "1800",
            "--header", f"Range: bytes={offset}-{end}",
            "--dump-header", str(header), "--output", str(part), url,
        ]
        subprocess.run(command, check=True)
        if part.stat().st_size != size:
            raise RuntimeError(
                f"download size mismatch: got {part.stat().st_size}, expected {size}"
            )
        header_text = header.read_text(encoding="latin-1").lower()
        expected = f"content-range: bytes {offset}-{end}/"
        if expected not in header_text:
            raise RuntimeError(f"final headers lack exact range contract: {expected}")
        os.replace(part, package)
        header.rename(package.with_suffix(package.suffix + ".headers"))

    package_hash = sha256(package)
    if download_only:
        return {
            "status": "PASS",
            "source": source,
            "version": version,
            "scene": row["scene"],
            "package": row["package"],
            "shard_id": shard_id,
            "offset": offset,
            "size": size,
            "sha256": package_hash,
            "package_path": str(package),
            "download_reused": reused_download,
            "extraction_skipped": True,
            "elapsed_s": round(time.monotonic() - started, 3),
            "completed_utc": datetime.now(timezone.utc).isoformat(),
        }
    reused_extract = extracted_ok(extract_root, row["scene"])
    member_count = None
    if not reused_extract:
        selected_dir = extract_root / row["scene"]
        base_dir = extract_root / row["scene"].removesuffix("_ceiling")
        if selected_dir.exists() or base_dir.exists():
            raise RuntimeError(f"incomplete pre-existing extraction for {row['scene']}")
        members = safe_members(package)
        member_count = len(members)
        top_levels = sorted({PurePosixPath(name).parts[0] for name in members})
        if row["scene"] not in top_levels or row["scene"].removesuffix("_ceiling") not in top_levels:
            raise RuntimeError(f"package does not contain both variants for {row['scene']}")
        extract_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="extract_", dir=extract_root) as temp_name:
            temp = Path(temp_name)
            subprocess.run(
                ["tar", "--use-compress-program=unzstd", "-xf", str(package), "-C", str(temp)],
                check=True,
            )
            for top_level in top_levels:
                source_path = temp / top_level
                if not source_path.exists():
                    raise RuntimeError(f"listed top-level path missing after extraction: {top_level}")
                target_path = extract_root / top_level
                if target_path.exists():
                    raise RuntimeError(f"refusing to overwrite extracted scene: {target_path}")
                source_path.rename(target_path)
        if not extracted_ok(extract_root, row["scene"]):
            raise RuntimeError(f"extraction verification failed for {row['scene']}")

    return {
        "status": "PASS",
        "source": source,
        "version": version,
        "scene": row["scene"],
        "package": row["package"],
        "shard_id": shard_id,
        "offset": offset,
        "size": size,
        "sha256": package_hash,
        "package_path": str(package),
        "extract_root": str(extract_root),
        "member_count": member_count,
        "download_reused": reused_download,
        "extraction_reused": reused_extract,
        "elapsed_s": round(time.monotonic() - started, 3),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--extract-root", type=Path, required=True)
    parser.add_argument("--jsonl", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--extract-only", action="store_true")
    args = parser.parse_args()
    if args.download_only and args.extract_only:
        raise SystemExit("--download-only and --extract-only are mutually exclusive")
    rows = json.loads(args.selection.read_text(encoding="utf-8"))["selected"]
    args.jsonl.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    passed = 0
    failures = []
    with args.jsonl.open("a", encoding="utf-8") as output:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    download_one,
                    row,
                    args.package_root,
                    args.extract_root,
                    args.download_only,
                    args.extract_only,
                ): row
                for row in rows
            }
            for index, future in enumerate(as_completed(futures), 1):
                row = futures[future]
                try:
                    record = future.result()
                    passed += 1
                except Exception as exc:
                    record = {
                        "status": "FAIL",
                        "source": row["source"],
                        "scene": row["scene"],
                        "package": row["package"],
                        "error": f"{type(exc).__name__}: {exc}",
                        "completed_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    failures.append(record)
                with lock:
                    output.write(json.dumps(record) + "\n")
                    output.flush()
                print(json.dumps({
                    "done": index, "total": len(rows), "passed": passed,
                    "failed": len(failures), "scene": row["scene"],
                }), flush=True)
    if failures:
        raise RuntimeError(f"package materialization failed for {len(failures)} scenes")
    print(json.dumps({"status": "PASS", "scene_count": passed}), flush=True)


if __name__ == "__main__":
    main()
