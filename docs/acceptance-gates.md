# The acceptance gates

Seven gates, run in order, stopping at the first failure unless `--keep-going`.
Each returns a `GateResult` with four parts:

```python
GateResult(
    gate="G6", title="Stair rises", status="PASS",
    measurements={...},      # numbers with units
    failures=[...],          # empty when status is PASS
    not_supported=[...],     # what this result does not license
    controls=[NegativeControl(...)],   # ran, and behaved as required
)
```

`finish()` sets PASS only when nothing failed **and** every control that ran
behaved as required. A gate whose control silently stopped failing is a gate
that cannot tell a good building from a broken one, and it reports FAIL.

---

## G1 — USD structure

Is this a stage a static cache can honestly be baked from?

Measures up axis, `metersPerUnit`, collider count, approximation counts,
`UsdPhysics.RigidBodyAPI` prims, collider bounds.

Fails on: not Z-up; not metre-scale; no enabled collider; any approximation
other than `none`; any dynamic body.

*Why approximations matter:* a collider cooked to `convexHull` turns a staircase
into a ramp. A ramp passes every reachability check in this repository, for
entirely the wrong reason.

**Control:** relabel one collider `convexHull` and re-run the check. It must
report a failure.

**Does not support:** structural validity says nothing about navigability.

---

## G2 — Cache integrity

Committed, exact, self-consistent, free of NaN?

Measures the policy string, grid shape, occupied voxels, clearance range, source
stage and composed-layer hash.

Fails on: a policy that is not an exact bake; zero occupied voxels; NaN or Inf in
either clearance tier; any array whose sha256 does not match the commit marker.

**Control:** copy the cache, change the recorded occupied-voxel count, reload.
`load_cache` must raise `CacheError`.

**Does not support:** a verified cache proves the bake was not corrupted, not
that it used the stage revision you think it did. Compare source hashes for that.

---

## G3 — USD ↔ voxel consistency

Does the grid agree with the geometry it claims to represent?

- **Recall** — points sampled on collider surfaces must land in occupied cells.
- **Precision** — points farther than the splat tolerance from any collider must
  land in free cells.

The tolerance is derived, not tuned: subdivision puts a marked cell centre
within half a voxel diagonal of the surface, and the conservative splat can add
at most one further voxel per axis. Points inside that band are counted and
judged by neither test.

"Far" is measured against the collider **surfaces**, not against the sampled
points. Measuring against samples calls a probe sitting on a large wall "far"
merely because no sample landed near it, and the gate then reports the grid as
wrong where it is right.

**Control:** displace the surface samples 0.5 m in +X. Recall must collapse.

**Does not support:** agreement on sampled points bounds systematic error, not
the behaviour of any individual collider.

---

## G4 — Storeys and stair span

Two or more plausible storeys, with a flight that spans them and headroom over
it?

Measures the detected storeys, their separations, the longest vertical gap in
the flight, the flight's footprint, and top-clearance coverage.

Fails on: fewer than two storeys; a storey rise outside the profile's window; a
vertical gap in the flight larger than one support step; coverage below the
profile's threshold.

Two measurement details, each of which would otherwise fail every correct stair:

- Empty voxel layers between treads are **normal** — tread plates are thinner
  than the riser. What must not happen is a gap taller than one support step.
- Headroom is measured against the building with the **stair removed**. With a
  nosing, tread *k+1* overhangs tread *k*, so a column at every tread boundary
  contains the next tread one riser above it; and the rail sits 0.95 m over the
  outer tread cells. This gate asks whether the *building* leaves room over the
  flight.

**Control:** fill the volume one body height above the flight. Coverage must
fall below the threshold.

**Skips** (not passes) when no stair prim path was supplied.

**Does not support:** a flight that spans the gap may still be unclimbable. G6
measures the rises.

---

## G5 — Cross-storey route

Can a body of the pinned size walk from the lower storey to the upper one?

Pass/fail is connectivity: some area of the upper storey must be reachable on
foot from the centre of the lower storey's largest walkable region. The seed is
the cell nearest that region's centroid — a route length that depends on which
cell `np.nonzero` returned first is not a property of the building.

The threshold is a **plan** quality: walking distance to the foot of the stair
versus the straight line across the floor. Comparing a route that climbs four
metres against a straight line drawn through solid floor exceeds any limit on a
perfectly sensible building; the climb is reported as a diagnostic instead.

**Control:** fill every voxel between the two storey slabs and re-derive
standability. The upper storey must become unreachable.

**Does not support:** this is a kinematic walk over the support volume. No
contact dynamics, no balance, no actuation. It cannot conclude that a humanoid
policy succeeds.

---

## G6 — Stair rises

Is every rise inside the 0.20 m support step — including the first and the last?

**(A) Authored geometry, the criterion.** Tread tops come from the composed USD;
ground and slab heights come from authored collider tops, chosen by largest
footprint at that height. No voxel quantisation enters the measurement. Reports
`first_rise_m`, `median_inner_rise_m`, `max_inner_rise_m`, `last_rise_m`,
`max_rise_m`, and the count over the limit.

**(B) Flooded occupancy, corroboration.** Re-derives standability and checks that
the upper storey has reachable area. Explicitly weaker than (A): at an offset of
one voxel the flood can still pass a flight whose authored first rise is already
over the limit, which is why it does not get a vote.

*Why the first and last rise get their own numbers:* they are the two an
"average riser" silently skips, and the two that break. Ground → first tread
carries the tread plate thickness; last tread → upper slab carries whatever the
slab thickness turned out to be. Four buildings once shipped with a first rise of
0.228–0.242 m and a passing report.

**Control:** lower the terrain by 0.05 / 0.10 / 0.15 m, which raises the first
rise by the same amount. By 0.15 m — a first rise of at least 0.32 m — the upper
storey must be unreachable. The seed follows the terrain the sweep placed; using
the original storey band lets the seed land on the first tread once the ground
drops away, and the control then measures the stair reaching itself.

**Skips** when no stair prim path was supplied.

**Does not support:** authored rises bound the geometry, not the contact
behaviour. A legal rise can still be missed by a policy.

---

## G7 — Body-scale navigation per storey

Is each storey usable from the stair, or merely touched by it?

Per storey: standable area, area reachable from the stair, the largest
unreachable standable component and how many there are.

Fails on: reachable area below the profile's minimum; an unreachable standable
component above the profile's limit.

The body radius erodes the **whole** free volume here. This is the check whose
lax version once passed 50 buildings out of 50, and whose correct version passed
none of them — only 5 of 100 storeys retained any usable region at all. The
report carries the full pinned semantics, including which clearance model
produced it.

**Control:** re-derive the volume with the erosion disabled, everything else
identical. The un-eroded field must claim strictly more area, and the
overstatement is reported in m².

**Does not support:** reachable area is geometric. It does not model doors that
open, objects that move, or a policy's ability to follow the route. A body may
also "stand" with part of its footprint over a stairwell opening — the model has
no notion of falling.

---

## Writing a new gate

Answer this before writing the assertion: **what input makes this fail?** If you
cannot name one, the check is not testing anything. Then write that input as a
`NegativeControl`, run it, and let `finish()` enforce it.

Do not adjust a threshold to make a building pass. If a threshold is wrong, say
what it should be and why, and change it where the diff is visible — in
`generic.yaml`, not in a profile and not in the gate.
