#!/usr/bin/env python3
"""Run a USD Python script after initializing the Isaac Sim Kit runtime."""

from __future__ import annotations

import os
import runpy
import sys

os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
from isaacsim import SimulationApp


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: run_in_isaacsim.py SCRIPT [ARGS ...]")
    target = sys.argv[1]
    sys.argv = sys.argv[1:]
    app = SimulationApp({"headless": True})
    try:
        runpy.run_path(target, run_name="__main__")
    finally:
        app.close()


if __name__ == "__main__":
    main()
