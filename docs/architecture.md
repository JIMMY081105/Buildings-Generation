# Architecture

A reading order, and the reasoning behind the boundaries.

## One pass through the system

```
buildingsgen run building.usd --type factory
        │
        ├── config.load_profile("factory")          buildingsgen/profiles/*.yaml
        │
        ├── pipeline.connect_storeys
        │     ├── voxel.bake.bake_stage(source)     colliders → occupancy cache
        │     ├── voxel.standability.derive_standable   occupancy → walkable volume
        │     ├── usd.storeys.detect_storeys        volume → storey bands
        │     ├── stairs.spec.design_stair          rise + profile → StairSpec
        │     ├── stairs.placement.find_placements  spec + volume → poses
        │     ├── pipeline.verify_placement         rasterise + re-flood each pose
        │     ├── stairs.asset.build_stair_usd      spec → stair.usda
        │     └── usd.stage.open_with_overlay       author into an overlay
        │
        ├── pipeline.bake_connected                 connected stage → cache
        └── pipeline.accept_building
              └── acceptance.runner.run_acceptance  G1..G7 → evidence JSON
```

## The four layers

### `voxel/` — what is solid, and where a body fits

Knows nothing about buildings, stairs or profiles. Takes occupancy, returns
geometry.

- `grid.py` — the aligned 5 cm / 10 cm grid. Both sizes pinned.
- `rasterize.py` — triangles → voxels, conservatively. Errors make the scene
  more solid, never more open.
- `cache.py` — the immutable `.scene_geometry_v1` directory.
- `standability.py` — the pinned body proxy and the eroded walkable volume.

**Read `standability.py` first.** Everything else in the repository is
downstream of the question it answers, and its module docstring explains the
one mistake that matters.

### `usd/` — reading a stage without trusting its conventions

- `colliders.py` — the single definition of "which prims are solid". Every other
  module reads colliders through it, so there is one triangulation path and one
  answer.
- `stage.py` — opening, flattening, and the repair-by-overlay rule.
- `storeys.py` — geometric storey detection plus `authored_surface_z_m`, which
  recovers an authored floor height from the voxel-quantised estimate.

### `stairs/` — design, geometry, placement

- `spec.py` — dimensions only. No USD, no voxels. The two rules are in its
  docstring: fit risers to the measured height, and let the upper floor supply
  the last tread.
- `asset.py` — spec → boxes → USD, and `world_triangles` for the same geometry
  at a pose without writing a file.
- `placement.py` — the base / shaft / arrival / departure search.
- `infinigen_backend.py` — optional, richer assets through the same interface.

### `acceptance/` — measurement

- `base.py` — `GateResult` and `NegativeControl`. `finish()` refuses PASS when a
  control that ran did not behave as required.
- `context.py` — lazily derived shared inputs, so two gates cannot disagree
  about which cells are walkable.
- `gates.py` — the seven gates.
- `runner.py` — ordered, fail-closed, one evidence record.

---

## Decisions worth knowing

### The source is never modified

`open_with_overlay` composes the source under a new writable root layer.
Everything the pipeline authors lands in the overlay; `assert_source_unchanged`
re-hashes the source afterwards. A repair can be reverted by deleting one file,
and it cannot leak into another experiment that references the same asset.

The overlay also has to carry `upAxis` and `metersPerUnit` explicitly — stage
metadata lives on the root layer, so a bare overlay flattens to a Y-up, unitless
stage no matter what the source says.

### Storeys are geometry, names are labels

Detection histograms walkable heights and claims bands greedily, largest first.
Prim names are used only to label the result, and only when there is exactly one
hinted name per detected band — otherwise labelling by position would be a guess
presented as a fact.

### The placement search is verified, not trusted

`find_placements` works on a proxy: a rectangle that must have standable base
under it and a clear shaft above it, with a standable arrival within a tight
distance of the last tread's edge. That proxy is good enough to *propose* and
not good enough to *commit*: two voxels of horizontal slack at the top of a
flight are invisible to a footprint search and fatal to a walk.

So `verify_placement` rasterises the flight where it would actually go,
re-derives standability over the combined occupancy, and floods from the lower
storey — the same measurement Gate 5 and Gate 7 make later, done early enough to
reject the pose instead of shipping it. The first pose that connects at least
the profile's required area wins.

### Two clearance models, and why a stair needs the second

`fixed_base` treats the body as a rigid cylinder from the support up to 1.60 m.
That is right on a flat floor and *necessarily wrong on a stair*: standing on
tread *k*, a 0.25 m cylinder reaches tread *k+1*, one riser above. Every flight
then reports zero standable treads — not because the stair is bad, but because
the proxy cannot bend a knee.

`terrain_following` (the default) treats the first `max_support_step_m` above the
support as a foot/lower-leg terrain band; above that band the eroded clearance
must hold all the way to 1.60 m. Tread *k+2* and everything higher still has to
clear the eroded footprint. The mode is recorded in the evidence, so a result is
never ambiguous about which one produced it.

### Exact colliders, and watertight ones are solid

The bake refuses nothing but records everything: approximation counts, the
policy string, how many colliders were treated as solid. Gate 1 rejects any
approximation other than `none`, because a cooked convex hull turns a staircase
into a ramp and a ramp passes reachability for the wrong reason. Gate 2 refuses
a cache whose policy string is not an exact bake.

Watertight colliders are filled. Leaving a closed box hollow is not a harmless
approximation: under the terrain-following model the cavity inside a hollow slab
reads as a second standable floor one slab-thickness below the real one, and
storey detection finds twice the area it should.

### Fail-closed, and the third column

The runner stops at the first FAIL unless told otherwise. Carrying a
known-broken building through the remaining gates produces numbers measured on
geometry that was already wrong, and those numbers get quoted later.

Every gate carries a `not_supported` list into its result. A reader who is given
only "what passed" will fill in the rest themselves, and they will fill it in
generously.
