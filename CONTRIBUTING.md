# Contributing

```bash
pip install -e ".[dev]"
ruff check src tests
pytest              # unit tests, seconds
pytest --runslow    # + end-to-end runs, minutes
```

## Three rules

### 1. A check must be able to fail

Before writing an assertion, answer: **what input makes this fail?** If you
cannot name one, the check is not testing anything — it is decoration that will
later be quoted as evidence.

Then write that input down as a `NegativeControl`, run it, and let
`GateResult.finish()` enforce it. A gate whose control stopped failing reports
FAIL, because it can no longer tell a good building from a broken one.

Every test in `tests/` that asserts something is *rejected* has a sibling that
asserts the near-miss is *accepted* — `test_narrow_slot_is_not_walkable` next to
`test_wide_doorway_is_walkable`. Without the second, the first also passes on a
check that refuses everything.

### 2. Do not move a threshold to make something pass

If a threshold is wrong, say what it should be, why, and what it changes. Then
change it in `configs/building_types/generic.yaml`, where the diff is visible.

Profiles may only tighten; the loader enforces it. The body proxy
(`StandabilityV1`) cannot be changed from a profile at all — it raises.

If a *measurement* is wrong, fix the measurement and say so. Several checks here
were replaced rather than retuned: G4's headroom was counting the stair's own
treads, G6 was comparing an authored tread against a voxel-quantised slab, G5's
route length depended on array order. Each of those looked like a threshold
problem and was not.

### 3. Say what a result does not support

Every gate carries a `not_supported` list. Keep it accurate when you change what
a gate measures. A reader given only "what passed" will fill in the rest
themselves, generously.

---

## Where things go

| Change | File |
|---|---|
| A new building type | `configs/building_types/<name>.yaml` — no code |
| A new gate | `acceptance/gates.py`, add to `ALL_GATES`, document in `docs/acceptance-gates.md` |
| Stair geometry | `stairs/spec.py` for dimensions, `stairs/asset.py` for geometry |
| Anything derived from upstream | note it in `docs/provenance.md` with what changed and why |

`voxel/` knows nothing about buildings. `stairs/spec.py` knows nothing about USD
or voxels. Keep it that way; it is why the gates can be tested on synthetic
arrays without a scene.

## Commits

Say what changed and what it means for a measurement. "fix gate 4" is not as
useful as "gate 4: measure headroom against the building, not against the
stair's own treads — nosing made every correct flight fail".
