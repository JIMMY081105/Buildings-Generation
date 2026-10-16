#!/usr/bin/env python3
"""Bind A3 pairs to the pre-A4 strict semantic/structural factory review."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--source-review", type=Path, required=True)
    parser.add_argument("--geometry-cache", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    pairs_path = args.pairs.expanduser().resolve()
    review_path = args.source_review.expanduser().resolve()
    cache_path = args.geometry_cache.expanduser().resolve()
    pairs = json.loads(pairs_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    accepted = set(review["accepted_factory_floors"])
    results = [row for row in pairs["results"]
               if row["floor1"] in accepted and row["floor2"] in accepted]
    result_keys = {(row["floor1"], row["floor2"], row["kind"]) for row in results}
    if len(result_keys) != len(results):
        raise RuntimeError("duplicate strict A3 pair/kind")
    out_dir = args.out_dir.expanduser().resolve()
    if out_dir.exists():
        raise FileExistsError(f"refusing to overwrite strict pair directory: {out_dir}")
    out_dir.mkdir(parents=True)
    document = {
        **pairs,
        "contract": "factory10_strict_A3_floor_level_pair_catalog_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scene_root": cache["scene_root"],
        "source_unfiltered_pairs": str(pairs_path),
        "source_unfiltered_pairs_sha256": sha256(pairs_path),
        "strict_source_review": str(review_path),
        "strict_source_review_sha256": sha256(review_path),
        "strict_factory_floor_count": len(accepted),
        "candidates": len(results), "usable": len(results), "results": results,
    }
    (out_dir / "scene_pairs_factory_strict.json").write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    heights = sorted({int(round(float(row["floor1_f2f"]) * 100)) for row in results})
    for height in heights:
        selected = [row for row in results
                    if int(round(float(row["floor1_f2f"]) * 100)) == height]
        (out_dir / f"pairs_h{height}.json").write_text(json.dumps({
            "contract": "factory10_strict_A3_height_cell_v1",
            "scene_root": cache["scene_root"], "height_m": height / 100.0,
            "candidates": len(selected), "usable": len(selected), "results": selected,
            "source_review": str(review_path), "source_review_sha256": sha256(review_path),
        }, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "strict_factory_floor_count": len(accepted),
        "strict_pair_kind_count": len(results),
        "height_kind_counts": {
            str(height): sum(int(round(float(row["floor1_f2f"]) * 100)) == height
                             for row in results)
            for height in heights
        },
    }, indent=2))


if __name__ == "__main__":
    main()
