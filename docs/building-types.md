# Adding a building type

A building type is one YAML file in `src/buildingsgen/profiles/`, or in any
directory you pass as `--config-root`. No code changes either way.

```yaml
name: hospital            # must match the filename
extends: generic          # inherit everything, override key by key
description: >
  Ward blocks: long corridors, wide doors, lifts and stairs in a core.
source_corpus: "healthcare subset of a multi-type USD corpus"

stair:
  families: [u_shaped, straight]
  preferred_riser_m: 0.155
  min_riser_m: 0.150
  max_riser_m: 0.170
  min_going_m: 0.29
  max_going_m: 0.33
  width_m: 1.50
  min_width_m: 1.40
  handrail: double

storeys:
  min_storey_height_m: 3.00
  max_storey_height_m: 4.20
  min_storey_floor_area_m2: 60.0

acceptance:
  min_reachable_area_per_storey_m2: 40.0
  min_doorway_clear_width_m: 1.20
```

Then:

```bash
buildingsgen types                        # it appears
buildingsgen scan ward.usd --type hospital
```

---

## What each section controls

### `stair` — the design envelope

`design_stair` searches the riser **count**, not a scale factor. Given a
measured storey rise it picks the count nearest `preferred_riser_m` whose
resulting riser lands inside `[min_riser_m, max_riser_m]`, then sets the going
from the Blondel relation `2 × riser + going = blondel_target_m`, clamped into
`[min_going_m, max_going_m]`.

If no count fits, `design_stair` raises rather than returning a flight that
nothing downstream will accept. That is the intended behaviour: "this building
type cannot stair this storey height" is a real answer.

The loader checks at load time that Blondel at your `preferred_riser_m` lands
inside your going range. A profile that cannot satisfy its own relation is a
configuration bug, not a runtime surprise.

`max_riser_m` above 0.20 m is pointless: the pinned support step refuses it.

### `storeys` — what counts as a storey

Detection is geometric (see `usd/storeys.py`). These values filter it:

- `min_storey_floor_area_m2` — the one that matters. A Z band with less walkable
  area than this is furniture tops or a mezzanine edge, not a storey. Too low
  and a run of worktops becomes a floor; too high and a real mezzanine
  disappears.
- `min_storey_height_m` / `max_storey_height_m` — plausibility only. Gate 4
  reports a rise outside the window; detection does not use them.
- `max_slab_thickness_m` — the half-width of the band claimed around each
  detected peak.
- `name_hints` — labels only. Never a geometric decision.

### `acceptance` — the thresholds a reviewer may argue about

| Field | Gate | Meaning |
|---|---|---|
| `min_reachable_area_per_storey_m2` | G7 | Walkable area reachable from the stair, per storey. |
| `max_unreachable_standable_component_m2` | G7 | A standable island larger than this that the stair cannot reach is a missing route, not a dead corner. |
| `min_doorway_clear_width_m` | G3b | Clear opening a body of the pinned radius needs. |
| `max_geodesic_to_euclidean_ratio` | G5 | How far out of its way a body in the middle of the floor walks to reach the stair. |
| `min_top_clearance_coverage` | G4 | Fraction of the flight with a full body height of headroom, measured against the building and not against the stair's own treads. |
| `min_voxel_agreement` | G3 | Precision and recall between the USD and the grid. |

---

## The rule about thresholds

**A profile may only tighten.** The loader compares against `generic` and
rejects the file if any threshold is looser, per field: minima may only rise,
maxima may only fall.

```
ProfileError: profile 'lax' loosens ['min_reachable_area_per_storey_m2']
relative to 'generic'; a threshold may only be tightened by a profile
```

Loosening is not forbidden — it is forbidden *quietly*. Change
`src/buildingsgen/profiles/generic.yaml`, where the diff is visible and the
reason has to be written down.

## The rule about the body

`StandabilityV1` is not in any profile and cannot be reached from one. Radius
0.25 m, height 1.60 m, support step 0.20 m, minimum component 1.00 m², voxel
5 cm. Constructing it with other values and calling `validate()` raises
`PinnedSemanticsError`.

A humanoid is the same size in a school and in a factory. If a building type
genuinely needs a different body, that is a new `StandabilityV2` with its own
migration, not a YAML override.

---

## A checklist for a new type

1. Copy the closest existing profile.
2. Set the stair envelope from the relevant building code or from measurements
   of the corpus, not from what makes a particular building pass.
3. Set `min_storey_floor_area_m2` from the smallest floor you would accept.
4. Run `buildingsgen scan` on five buildings of the type and check the storey
   count and heights against what you can see.
5. Only then set acceptance thresholds, and write the reason in a comment.
6. `pytest` — `test_config.py` parametrises over every shipped profile, and
   `test_stairs.py` designs a flight for each across a range of storey heights.
