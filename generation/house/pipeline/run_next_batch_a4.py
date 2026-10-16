#!/usr/bin/env python3
"""Run the new doorway-aware A4 searches on CPU and retain every rejection."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--stage-dir", default="stage_a",
                        help="Stage-A directory below --run-root")
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--scenes-root", type=Path, required=True)
    parser.add_argument("--scene-cache", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-pairs", type=int, default=200)
    parser.add_argument("--max-candidates", type=int, default=100000)
    parser.add_argument("--only-heights", default="",
                        help="comma-separated metres, e.g. 3.2,3.5")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    stage_a = args.run_root / args.stage_dir
    scripts = args.run_root / "scripts"
    coverage = json.loads((stage_a / "stair_asset_coverage.json").read_text())
    pairs = stage_a / "scene_pairs.json"
    a4 = stage_a / "a4"
    logs = stage_a / "logs"
    a4.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["BUILDING_SCENES_ROOT"] = str(args.scenes_root.resolve())

    tasks = []
    only_heights = {float(value) for value in args.only_heights.split(",") if value}
    for asset in coverage["asset_evidence"]:
        height = float(asset["height_m"])
        if only_heights and height not in only_heights:
            continue
        millimetres = int(round(height * 100))
        kind = asset["kind"]
        output = a4 / f"placement_h{millimetres}_{kind}_grid001.json"
        log = logs / f"A4_h{millimetres}_{kind}.log"
        tasks.append((height, kind, output, log, [
            str(args.python), str(scripts / "pick_placement.py"),
            "--pairs", str(pairs),
            "--scene-cache", str(args.scene_cache),
            "--stair", asset["staircase_usd"],
            "--stair-path", asset["traversal_path"],
            "--kind", kind,
            "--grid-step", "0.01",
            "--max-pairs", str(args.max_pairs),
            "--max-candidates", str(args.max_candidates),
            "--max-alternatives-per-pair", "250",
            "--out", str(output),
        ]))

    def run(task):
        height, kind, output, log, command = task
        if output.exists():
            if not args.resume:
                raise FileExistsError(f"refusing to overwrite {output}")
            document = json.loads(output.read_text())
            if document.get("placement_contract_version") == 3:
                return height, kind, output, log, 0, "reused"
        completed = subprocess.run(command, env=env, text=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        log.write_text(completed.stdout, encoding="utf-8")
        return height, kind, output, log, completed.returncode, "ran"

    rows = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(run, task): task for task in tasks}
        for future in as_completed(futures):
            height, kind, output, log, code, action = future.result()
            row = {
                "height_m": height, "kind": kind, "action": action,
                "returncode": code, "output": str(output), "log": str(log),
            }
            if code == 0 and output.is_file():
                document = json.loads(output.read_text())
                row.update({
                    "output_sha256": sha256(output),
                    "candidate_count": len(document.get("candidates", [])),
                    "doorway_clearance_rejection_count": document.get(
                        "doorway_clearance_rejection_count"),
                })
            rows.append(row)
            print(json.dumps(row), flush=True)
    rows.sort(key=lambda row: (row["height_m"], row["kind"]))
    report = {
        "status": "PASS" if all(row["returncode"] == 0 for row in rows) else "FAIL",
        "device": "CPU",
        "gpu_used": False,
        "placement_contract_version": 3,
        "max_pairs_per_cell": args.max_pairs,
        "concurrency": args.concurrency,
        "searches": rows,
    }
    (stage_a / "A4_RUN_REPORT.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
