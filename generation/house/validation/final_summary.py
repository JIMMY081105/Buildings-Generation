"""Aggregate the six gates into one acceptance decision.

TRAINING_AUTHORIZED is true only if every building passes every gate.

Run:  python final_summary.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
EV = HERE / "evidence"
NAMES = ["1_l", "2_u", "3_s300", "4_s270"]

GATES = [
    ("Structural", "gate1_structural.json", "GATE1"),
    ("Cache", "gate2_cache_integrity.json", "GATE2"),
    ("USD-Voxel", "gate3_usd_voxel_consistency.json", "GATE3"),
    ("Structure", "gate3b_structural_connectivity.json", "GATE3B"),
    ("Visual", "gate4_visual_audit.json", "GATE4"),
    ("Runtime", "gate5_runtime_smoke.json", "GATE5"),
    ("Reachable", "gate6_stair_reachability.json", "GATE6"),
]


def main():
    loaded = {}
    for label, fn, key in GATES:
        path = EV / fn
        loaded[label] = (json.loads(path.read_text()) if path.exists() else None, key)

    summary, all_pass = {}, True
    for n in NAMES:
        row, ok = {}, True
        for label, _, _ in GATES:
            data, key = loaded[label]
            v = "MISSING" if not data or n not in data else data[n].get(key, "MISSING")
            row[label] = v
            ok &= (v == "PASS")
        row["FINAL"] = "PASS" if ok else "FAIL"
        all_pass &= ok
        summary[n] = row

    header = ["Building"] + [g[0] for g in GATES] + ["FINAL"]
    widths = [9] + [len(h) + 2 for h in header[1:]]
    print("| " + " | ".join(h.ljust(w) for h, w in zip(header, widths)) + " |")
    print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for n in NAMES:
        cells = [n] + [summary[n][g[0]] for g in GATES] + [summary[n]["FINAL"]]
        print("| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |")

    blocking = [f"{n}:{g}" for n in NAMES for g in [x[0] for x in GATES]
                if summary[n][g] != "PASS"]
    out = {
        "TRAINING_AUTHORIZED": bool(all_pass),
        "table": summary,
        "blocking_failures": blocking,
        "gates_run": [g[0] for g in GATES],
        "seed": 20260818,
        "caches_validated": [f"{n}_fixed.scene_geometry_v1" for n in NAMES],
        "note": "Gate 5 exercises SceneGeometryBackend, the real runtime geometry "
                "path. It does not exercise PhysX contact dynamics, which needs "
                "Isaac Sim on the RTX 5090 and is outside the cache's scope. "
                "Gate 6 asks whether ProtoMotions' own robot proxy can climb "
                "from the terrain to the upper storey at all.",
    }
    (EV / "FINAL_ACCEPTANCE.json").write_text(json.dumps(out, indent=2))
    print("\nTRAINING_AUTHORIZED = " + str(all_pass).lower())
    if blocking:
        print("blocking: " + ", ".join(blocking))
    print("wrote " + str(EV / "FINAL_ACCEPTANCE.json"))


if __name__ == "__main__":
    main()
