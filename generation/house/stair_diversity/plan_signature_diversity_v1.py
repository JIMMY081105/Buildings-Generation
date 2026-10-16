"""Break the signature collision with the cheapest change per building.

The publish gate in publish_phase1.py refuses a design signature repeated more
than four times.  The delivered portfolio has one signature 19 times, so the
gate is blocked.

Not every field of the signature costs the same to change:

    tread_material / step_material / handrail_material   appearance only
    handrail_type                                        geometry (posts vs glass)
    support_type                                         geometry (sides, rails, stringer)
    staircase_family                                     geometry, new asset

Only the first row leaves the collision geometry untouched, which is what lets
a candidate reuse the frozen geometry gates and screen on CPU.  So this planner
changes tread_material first and only escalates when the material axis is
exhausted for that group.

It plans on the four AUTHORED fields and ignores dominant_palette, which is
measured from the baked asset.  That is deliberate and conservative: the
multiplicity of a coarser key is always >= the multiplicity of the finer key
that adds palette, so a plan that reaches <= 4 here cannot exceed 4 once the
measured palette is appended.

This script authors no asset and starts no job.  It writes a plan.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

# Infinigen material pools, read from
# https://github.com/princeton-vl/infinigen  src/infinigen/assets/composition/
# material_assignments.py (main, fetched 2026-09-26).  Weights are the upstream
# sampling weights and are kept only for reference; this planner enumerates.
WOODS = ["Wood", "WhitePlywood", "BlackPlywood", "BlondePlywood"]
METALS = [
    "MetalBasic", "BrushedMetal", "GalvanizedMetal",
    "GrainedMetal", "HammeredMetal", "BrushedBlackMetal",
]
FABRICS = ["CoarseKnitFabric", "FineKnitFabric", "Leather", "SofaFabric"]
MARBLE = ["Marble", "MarbleRegular", "MarbleVoronoi"]
GLASSES = ["Glass"]
STEP_PLASTICS = ["Plastic", "PlasticTranslucent"]

#: Per-role pools, exactly as material_assignments composes them.  A role must
#: only be given a material its own pool contains; the CLI in
#: native_style_overrides.py currently offers the union for every role, which
#: is looser than upstream and is why Marble reached treads.
POOLS = {
    "step": FABRICS + WOODS + MARBLE + STEP_PLASTICS,
    "rail": FABRICS + WOODS + MARBLE + STEP_PLASTICS,
    "tread": WOODS + METALS + GLASSES,
    "side": FABRICS + WOODS + MARBLE + STEP_PLASTICS + METALS,
    "handrail": WOODS + METALS + FABRICS,
    "post": WOODS + METALS + FABRICS,
    "glasses": GLASSES,
}

PUBLISH_CAP = 4
AUTHORED = ("staircase_family", "support_type", "tread_material", "handrail_type")


def signature(row: dict, fields=AUTHORED) -> tuple:
    return tuple(str(row.get(field)) for field in fields)


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_feasible(path: Path) -> set[str]:
    """Read a measured CPU summary or an explicit building allowlist."""
    document = load(path)
    values = document.get('cpu_passed_buildings') if isinstance(document, dict) else document
    if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
        raise ValueError('Feasible input must list building names, not planned changes')
    if len(set(values)) != len(values):
        raise ValueError('Duplicate building in feasible input')
    return set(values)


def plan(actual_rows: list[dict], cap: int = PUBLISH_CAP,
         feasible: set[str] | None = None) -> dict:
    """Assign a new tread material to the overflow of each oversized group."""

    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for row in actual_rows:
        groups[signature(row)].append(row)

    # Materials already spoken for, so a reassignment does not create a new
    # collision somewhere else.
    taken = collections.Counter(signature(row) for row in actual_rows)
    changes: list[dict] = []
    exhausted: list[dict] = []
    blocked: list[str] = []
    blocked_required = 0
    if cap < 1:
        raise ValueError('cap must be positive')
    if feasible is not None:
        unknown = feasible - {row['building'] for row in actual_rows}
        if unknown:
            raise ValueError('Feasible buildings absent from measured portfolio: ' + ','.join(sorted(unknown)))

    for key, members in sorted(groups.items(), key=lambda item: -len(item[1])):
        if len(members) <= cap:
            continue
        family, support, tread, handrail = key
        # Keep `cap` members as they are; the rest need a different material.
        # Retained originals go first: they were never styled, so restyling one
        # is a correction rather than a new variant of an accepted result.
        members = sorted(
            members,
            key=lambda row: (row.get("disposition") != "retained_original", row["building"]),
        )
        # The first `cap` members keep the signature; the overflow is restyled.
        overflow = members[cap:]
        if feasible is not None:
            # An eligible member among the original keepers must remain usable;
            # filtering only members[cap:] would wrongly declare it infeasible.
            needed = len(members) - cap
            eligible = [row for row in members if row['building'] in feasible]
            overflow = eligible[-needed:]
            blocked_required += max(0, needed - len(eligible))
            blocked.extend(row['building'] for row in members if row['building'] not in feasible)
        for row in overflow:
            candidates = [
                material for material in POOLS["tread"]
                if material != tread
                and taken[(family, support, material, handrail)] < cap
            ]
            if not candidates:
                exhausted.append(row)
                continue
            # Prefer a material this portfolio has never used, which is the
            # point of the exercise: the upstream library is 20 materials wide
            # and the delivered portfolio used 8 of them.
            used_now = {signature(r)[2] for r in actual_rows}
            candidates.sort(key=lambda material: (material in used_now, material))
            chosen = candidates[0]
            taken[key] -= 1
            taken[(family, support, chosen, handrail)] += 1
            changes.append({
                "building": row["building"],
                "disposition": row.get("disposition"),
                "from_signature": "|".join(key),
                "change": {"field": "tread_material", "from": tread, "to": chosen},
                "cost": "appearance_only",
                "reason": (
                    "tread_surface is a material swap on unchanged collision geometry, "
                    "so the frozen geometry gates apply to the same mesh"
                ),
            })

    final = collections.Counter()
    moved = {change["building"]: change for change in changes}
    for row in actual_rows:
        key = signature(row)
        if row["building"] in moved:
            change = moved[row["building"]]["change"]
            key = (key[0], key[1], change["to"], key[3])
        final[key] += 1

    result = {
        "schema": "Residential54SignatureDiversityPlanV1",
        "publish_cap": cap,
        "planned_on_fields": list(AUTHORED),
        "palette_note": (
            "dominant_palette is measured after bake and is deliberately excluded. "
            "Adding a measured field can only split a group further, never merge "
            "two, so a plan that reaches the cap here still reaches it with palette."
        ),
        "before": {
            "distinct": len({signature(r) for r in actual_rows}),
            "max_multiplicity": max(collections.Counter(
                signature(r) for r in actual_rows).values()),
        },
        "after": {
            "distinct": len(final),
            "max_multiplicity": max(final.values()) if final else 0,
        },
        "changes": changes,
        "appearance_only_changes": sum(1 for c in changes if c["cost"] == "appearance_only"),
        "geometry_changes_required": len(exhausted),
        "exhausted": [row["building"] for row in exhausted],
        "material_inventory": {
            "upstream_union": sorted(set(sum(POOLS.values(), []))),
            "upstream_union_count": len(set(sum(POOLS.values(), []))),
            "valid_for_tread": POOLS["tread"],
            "used_in_delivered_portfolio": sorted({signature(r)[2] for r in actual_rows}),
        },
        "cannot_support": (
            "A signature plan is not an acceptance. Each changed building still has "
            "to pass the unchanged gate chain on its own evidence, and an appearance-only "
            "change is not automatically topology-preserving: a baseline that already "
            "fails a graph gate will keep failing it."
        ),
    }
    if feasible is not None:
        result['feasibility'] = {
            'enabled': True, 'eligible_buildings': sorted(feasible),
            'excluded_overfull_group_buildings': sorted(blocked),
            'required_changes_without_feasible_building': blocked_required,
            'scope': 'Material allocation only. Collider repair is a geometry change; no geometry or topology preservation claim.',
        }
    return result


def geometry_plan(actual_rows: list[dict], scope: set[str], cap: int = PUBLISH_CAP, families=None) -> dict:
    """Plan new geometry only inside an explicit pool; never infer acceptance."""
    if cap < 1 or not scope:
        raise ValueError('Positive cap and nonempty geometry scope required')
    if families is not None and (not families or len(set(families)) != len(families)
                                or not set(families) <= {'straight','l_shaped','u_shaped'}):
        raise ValueError('Unknown or duplicate native staircase family')
    by_id = {r['building']: r for r in actual_rows}
    if len(by_id) != len(actual_rows) or not scope <= by_id.keys():
        raise ValueError('Duplicate or unknown geometry building')
    if any(by_id[b].get('disposition') != 'retained_original' for b in scope):
        raise ValueError('Geometry scope may not modify accepted or READY rows')
    counts = collections.Counter(signature(r) for r in actual_rows)
    before = dict(distinct=len(counts), max_multiplicity=max(counts.values()))
    changes = []
    for bid in sorted(scope):
        row = by_id[bid]; old = signature(row)
        # Rail/support changes are genuine geometry, even if material also changes.
        configurations = [(family, 'side', rail) for family in families
                          for rail in ('vertical-post','horizontal-post')] if families else (
                       ('straight', 'side', 'vertical-post'),
                       ('straight', 'side', 'horizontal-post'),
                       ('straight', 'solid', 'vertical-post'),
                       ('straight', 'double-rail', 'vertical-post'))
        options = [(family, support, material, rail)
                   for family, support, rail in configurations
                   for material in ('BrushedMetal', 'GalvanizedMetal', 'GrainedMetal', 'BrushedBlackMetal')
                   if (family, support, rail) != (old[0], old[1], old[3])
                   and counts[(family, support, material, rail)] < cap]
        if not options:
            continue
        target = options[0]
        counts[old] -= 1; counts[target] += 1
        changes.append(dict(building=bid, from_signature='|'.join(old),
            to_signature='|'.join(target), target_fields=dict(zip(AUTHORED, target)),
            cost='new_geometry', reason='Fresh native railing/support geometry, fresh placement and full gates required'))
    counts = +counts
    return dict(schema='Residential54GeometryDiversityPlanV1', publish_cap=cap,
        planned_on_fields=list(AUTHORED), before=before,
        after=dict(distinct=len(counts), max_multiplicity=max(counts.values())),
        changes=changes, appearance_only_changes=0, geometry_changes_required=len(changes),
        geometry_scope=sorted(scope),
        cannot_support='Targets are not measurements or acceptance; unchanged frozen gates and actual signatures remain mandatory.')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--actual", type=Path, required=True,
                        help="PHASE4_ACTUAL_SIGNATURE_*_PALETTE_GROUPS_*.json")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--cap", type=int, default=PUBLISH_CAP)
    parser.add_argument("--feasible", type=Path,
                        help="Allow material allocation only to measured CPU-passed buildings in this JSON")
    parser.add_argument('--geometry-scope', type=Path,
                        help='Explicit building allowlist for newly authorized geometric variants, not a CPU PASS list')
    parser.add_argument('--geometry-family', action='append', choices=('straight','l_shaped','u_shaped'),
                        help='Native family search axis for a newly authorized geometry round only')
    args = parser.parse_args()

    rows = load(args.actual)["rows"]
    if args.geometry_scope and args.feasible:
        parser.error('Geometry authorization and measured material feasibility are different inputs')
    if args.geometry_family and not args.geometry_scope:
        parser.error('--geometry-family requires --geometry-scope')
    record = geometry_plan(rows, load_feasible(args.geometry_scope), args.cap, args.geometry_family) if args.geometry_scope else plan(rows, cap=args.cap,
                  feasible=load_feasible(args.feasible) if args.feasible is not None else None)
    record["inputs"] = {"actual_signature": str(args.actual)}
    if args.feasible is not None:
        record['inputs']['feasible'] = {'path': str(args.feasible),
            'sha256': hashlib.sha256(args.feasible.read_bytes()).hexdigest()}

    print(f"before: {record['before']['distinct']:3d} distinct, "
          f"max {record['before']['max_multiplicity']}")
    print(f"after : {record['after']['distinct']:3d} distinct, "
          f"max {record['after']['max_multiplicity']}  (cap {args.cap})")
    print(f"appearance-only changes : {record['appearance_only_changes']}")
    print(f"geometry changes needed : {record['geometry_changes_required']}")
    inventory = record.get("material_inventory")
    if inventory:
        print(f"materials upstream      : {inventory['upstream_union_count']}")
        print(f"materials used so far   : {len(inventory['used_in_delivered_portfolio'])}")
    for change in record["changes"]:
        if args.geometry_scope:
            print(change['building'] + ' -> ' + change['to_signature'])
            continue
        print(f"  {change['building']:52s} tread {change['change']['from']:>14s}"
              f" -> {change['change']['to']}")
    if args.out:
        args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print("wrote", args.out)
    return 0 if record["after"]["max_multiplicity"] <= args.cap else 1


if __name__ == "__main__":
    raise SystemExit(main())
