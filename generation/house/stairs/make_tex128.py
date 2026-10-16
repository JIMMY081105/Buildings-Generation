#!/usr/bin/env python3
"""Build a texture-downscaled twin of each staircase, changing nothing else.

Tests the last standing hypothesis for the black-in-scene bug: that the stair's
material fails because its textures cannot stay resident alongside a
161-texture MolmoSpaces scene. Same USD bytes, same geometry, same relative
asset paths -- only the PNGs behind them get smaller.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

KINDS = ["straight", "l_shaped", "u_shaped"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--dst", type=Path, required=True)
    ap.add_argument("--size", type=int, default=128)
    args = ap.parse_args()

    summary = {"size": args.size, "kinds": {}}
    for kind in KINDS:
        src = args.src / kind
        dst = args.dst / kind
        if not (src / "staircase.usd").exists():
            summary["kinds"][kind] = {"error": "missing staircase.usd"}
            continue
        (dst / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / "staircase.usd", dst / "staircase.usd")

        written = []
        for png in sorted((src / "assets").glob("*.png")):
            with Image.open(png) as im:
                before = im.size
                small = im.resize((args.size, args.size), Image.LANCZOS)
                small.save(dst / "assets" / png.name, format="PNG")
            written.append({
                "name": png.name,
                "from": list(before),
                "to": [args.size, args.size],
                "bytes_before": png.stat().st_size,
                "bytes_after": (dst / "assets" / png.name).stat().st_size,
            })
        summary["kinds"][kind] = {"textures": written}

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
