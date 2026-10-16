"""Diagnose the Gate 3 connectivity failure: exterior space or sealed rooms?

The first Gate 3 run failed floor{1,2}_free_space_is_one_region on 1_l, 3_s300
and 4_s270. That check counted every navigable component visible in one
horizontal slice, which conflates two very different things:

  * free space OUTSIDE the building, which is legitimately its own component
    because the grid is sized to the colliders and extends a little past the
    walls; and
  * an interior room no doorway reaches, which would be a real defect.

This separates them, and does so in 3D. Connectivity must be measured in 3D
because a humanoid moves through 3D free space: two areas divided by a
partition at torso height may still connect over or under it, and a per-slice
2D labelling would wrongly call them sealed.

A component is exterior if it touches the grid border. The reachable world is
the component containing the Floor 1 seed. Interior components outside it that
are large enough to hold a humanoid are reported as genuinely unreachable.

Read-only. Writes evidence/gate3_component_diagnosis.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "ProtoMotions-main"))

import numpy as np
from scipy import ndimage

from protomotions.simulator.base_simulator.scene_geometry_cache import (
    load_scene_geometry_cache,
)

CACHES = HERE.parent / "voxel_bake" / "caches"
OUT = HERE / "evidence"
NAMES = ["1_l", "2_u", "3_s300", "4_s270"]
VOXEL = 0.05
CLEARANCE_M = 0.15
# a humanoid needs roughly this much free volume to stand in; anything smaller
# is a void inside furniture or between wall leaves, not a room
MIN_ROOM_M3 = 1.0
MIN_ROOM_CELLS = int(round(MIN_ROOM_M3 / VOXEL ** 3))


def diagnose(name):
    loaded = load_scene_geometry_cache(
        CACHES / (name + "_fixed.scene_geometry_v1"), verify_hashes=True)
    occ = np.ascontiguousarray(loaded.occupancy)
    clear = np.asarray(loaded.signed_clearance_fine).astype(np.float32)
    grid = loaded.manifest["grid"]
    lo = np.asarray(grid["scene_bounds_min_m"], float)
    nx, ny, nz = occ.shape
    navigable = clear >= CLEARANCE_M

    labels, n = ndimage.label(navigable)
    if n == 0:
        return {"building": name, "error": "no navigable space"}

    ids, counts = np.unique(labels[labels > 0], return_counts=True)

    # exterior = touches any face of the grid box
    border = set()
    for face in (labels[0, :, :], labels[-1, :, :], labels[:, 0, :],
                 labels[:, -1, :], labels[:, :, 0], labels[:, :, -1]):
        border.update(np.unique(face).tolist())
    border.discard(0)

    def z_of(k):
        return lo[2] + (k + 0.5) * VOXEL

    per_layer = navigable.sum(axis=(0, 1))
    lower = [k for k in range(nz) if 0.6 <= z_of(k) - lo[2] <= 1.6]
    upper = [k for k in range(nz) if (z_of(k) - lo[2]) >= 3.3]
    k1 = max(lower, key=lambda k: per_layer[k])
    k2 = max(upper, key=lambda k: per_layer[k])

    def seed_component(k):
        """Largest interior component present at height index k."""
        sl = labels[:, :, k]
        sids, scnt = np.unique(sl[sl > 0], return_counts=True)
        interior = [(i, c) for i, c in zip(sids, scnt) if i not in border]
        if not interior:
            return None
        return int(max(interior, key=lambda t: t[1])[0])

    c1 = seed_component(k1)
    c2 = seed_component(k2)

    interior_ids = [int(i) for i in ids if int(i) not in border]
    interior_cells = int(sum(c for i, c in zip(ids, counts) if int(i) not in border))
    reachable_cells = int(counts[ids == c1][0]) if c1 is not None else 0

    unreachable = []
    for i, c in zip(ids, counts):
        i = int(i)
        if i in border or i == c1 or c < MIN_ROOM_CELLS:
            continue
        xs, ys, zs = np.nonzero(labels == i)
        unreachable.append({
            "component": i,
            "cells": int(c),
            "volume_m3": round(float(c) * VOXEL ** 3, 3),
            "centroid_xyz_m": [round(float(lo[0] + xs.mean() * VOXEL), 2),
                               round(float(lo[1] + ys.mean() * VOXEL), 2),
                               round(float(lo[2] + zs.mean() * VOXEL), 2)],
            "z_range_m": [round(float(lo[2] + zs.min() * VOXEL), 2),
                          round(float(lo[2] + zs.max() * VOXEL), 2)],
        })
    unreachable.sort(key=lambda r: -r["cells"])

    return {
        "building": name,
        "clearance_threshold_m": CLEARANCE_M,
        "min_room_volume_m3": MIN_ROOM_M3,
        "connectivity": "3D, 6-connected",
        "floor1_probe_z_m": round(float(z_of(k1)), 3),
        "floor2_probe_z_m": round(float(z_of(k2)), 3),
        "components_total": int(n),
        "exterior_components": len(border),
        "exterior_cells": int(sum(c for i, c in zip(ids, counts) if int(i) in border)),
        "interior_components": len(interior_ids),
        "interior_cells": interior_cells,
        "floor1_component": c1,
        "floor2_component": c2,
        "storeys_share_one_component": bool(c1 is not None and c1 == c2),
        "reachable_cells": reachable_cells,
        "reachable_share_of_interior":
            round(reachable_cells / interior_cells, 4) if interior_cells else 0.0,
        "unreachable_rooms_over_min_volume": len(unreachable),
        "unreachable_room_detail": unreachable[:10],
        "small_interior_voids": len(interior_ids) - 1 - len(unreachable),
    }


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    results = {n: diagnose(n) for n in NAMES}
    (OUT / "gate3_component_diagnosis.json").write_text(json.dumps(results, indent=2))
    print(f"{'bldg':8} {'storeys 1 comp':>14} {'reach share':>12} "
          f"{'unreachable rooms':>18} {'small voids':>12} {'ext cells':>10}")
    for n, r in results.items():
        print(f"{n:8} {str(r['storeys_share_one_component']):>14} "
              f"{r['reachable_share_of_interior']:12.4f} "
              f"{r['unreachable_rooms_over_min_volume']:18d} "
              f"{r['small_interior_voids']:12d} {r['exterior_cells']:10d}")
        for u in r["unreachable_room_detail"][:3]:
            print(f"           unreachable {u['volume_m3']:7.2f} m3 at {u['centroid_xyz_m']}"
                  f"  z {u['z_range_m']}")
    print("\nwrote " + str(OUT / "gate3_component_diagnosis.json"))
