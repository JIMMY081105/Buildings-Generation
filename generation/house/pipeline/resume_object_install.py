#!/usr/bin/env python3
"""Resume the expanded object install only if its foreground process fails."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def emit(path: Path, event: str, **extra: object) -> None:
    record = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **extra,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch-pid", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--event-log", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        raise ValueError("resume command is required")
    if args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        raise ValueError("resume command is required after --")

    args.event_log.parent.mkdir(parents=True, exist_ok=True)
    emit(args.event_log, "watch_started", watch_pid=args.watch_pid)
    while process_exists(args.watch_pid):
        time.sleep(args.poll_seconds)

    if args.report.is_file() and args.report.stat().st_size > 0:
        emit(args.event_log, "report_present_no_resume")
        return 0

    emit(args.event_log, "resume_started", command=args.command)
    completed = subprocess.run(args.command, check=False)
    emit(args.event_log, "resume_finished", returncode=completed.returncode)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
