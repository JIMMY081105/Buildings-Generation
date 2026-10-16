#!/usr/bin/env python3
"""Create an exact-54 flat hard-link export of the opened USDZ assets."""

from __future__ import annotations

import json
import os
from pathlib import Path


R3 = Path(
    "/cephfs/shared/yixing/outputs/building_poc/"
    "batch_20260831_50_h1_r1/post_h1_50_r1/"
    "post_stage_d50_cpu_results_r3"
)
OPENED = R3 / "unify54" / "opened54"
AGGREGATE = OPENED / "DOOR_OPENING_54.json"
EXPORT = R3 / "unify54" / "usd_flat54"


def main() -> None:
    data = json.loads(AGGREGATE.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    assert data.get("status") == "PASS", data.get("status")
    assert data.get("expected_buildings") == 54
    assert data.get("observed_buildings") == 54
    assert len(rows) == 54, len(rows)

    building_ids = [row["building"] for row in rows]
    assert len(set(building_ids)) == 54
    assert all(row.get("status") == "PASS" for row in rows)

    EXPORT.mkdir(parents=True, exist_ok=True)
    expected_names = {f"{building_id}.usdz" for building_id in building_ids}
    unexpected = [
        path.name
        for path in EXPORT.iterdir()
        if path.name not in expected_names and path.name != "USD_MANIFEST.json"
    ]
    assert not unexpected, unexpected

    manifest_rows = []
    for row in sorted(rows, key=lambda item: item["building"]):
        building_id = row["building"]
        source = Path(row["output"])
        expected_source = OPENED / building_id / "scene" / "building.usdz"
        assert source == expected_source, (source, expected_source)
        assert source.is_file(), source
        destination = EXPORT / f"{building_id}.usdz"

        if destination.exists():
            assert destination.is_file(), destination
            assert os.path.samefile(source, destination), destination
        else:
            os.link(source, destination)

        stat = destination.stat()
        manifest_rows.append(
            {
                "building": building_id,
                "filename": destination.name,
                "bytes": stat.st_size,
                "sha256": row["output_sha256"],
                "source": str(source),
            }
        )

    actual_usdz = sorted(path.name for path in EXPORT.glob("*.usdz"))
    assert actual_usdz == sorted(expected_names)
    manifest = {
        "schema": "usd_flat54_manifest_v1",
        "status": "PASS",
        "count": len(manifest_rows),
        "total_bytes": sum(row["bytes"] for row in manifest_rows),
        "source_aggregate": str(AGGREGATE),
        "source_aggregate_status": data["status"],
        "rows": manifest_rows,
    }
    (EXPORT / "USD_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: manifest[key] for key in ("status", "count", "total_bytes")}))


if __name__ == "__main__":
    main()
