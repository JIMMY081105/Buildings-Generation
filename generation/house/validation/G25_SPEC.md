# G25 — per-storey navigation from the stair arrival

This gate is mandatory for the next building batch. It complements G19–G24;
it does not change their criteria and does not retroactively alter
`batch_20260819`.

For each storey, rasterize the exact enabled colliders on the pinned 5 cm grid
and derive standability with ProtoMotions
`standability.StandabilityConfig`: radius 0.25 m, height 1.60 m, maximum support
step 0.20 m, and minimum retained component area 1.00 m². Floor 1 has no slab
collider in these assets, so its z=0 simulator terrain support must be restored
over the building footprint before deriving standability.

The gate must report, separately for every storey:

- total walkable area;
- every retained connected region and its area;
- uncapped minimum headroom over the walkable set;
- area reachable from the authored stair-arrival point.
- the full-cell-union XY bounding-box diagonal of that reachable set;
- maximum horizontal free radius on that reachable set, measured at both
  0.30 m and 0.90 m above each cell's own support top and taking the smaller
  per-cell radius.

Connectivity uses the same cardinal-neighbour and 0.20 m support-step contract
as `StandabilityConfig`. Arrival seeds must be standable cells within the
0.25 m body radius and one support step of the authored arrival point. No seed
is a gate failure.

The only area criterion is:

```text
PASS iff area_reachable_from_stair_arrival_m2 >= 15.0 on every storey
```

Fragmentation is reported but is not itself a failure. This is deliberate:
the accepted `batch_20260819` source scenes contain full-height interior walls
with no door openings, yet each stair-accessible region is usable.

For `batch_20260826_r3` and later generation, A4 adds a separate hard rule:

```text
no stair-unreachable standable component may exceed 4.0 m²
```

The 4.0 m² limit is a predeclared 2 m by 2 m room-scale region: it is large
enough for a humanoid to translate and turn, rather than a raster sliver.  It
was not fitted to the 8.6525/9.0325 m² components in the frozen 74/98 scene.
The runner enables this rule with `--enforce-unreachable-component-limit` for
the new batch.  It remains disabled for the frozen batch, whose approval and
base G25 result are unchanged.

Reachable extent, maximum free radius and uncapped minimum headroom are
mandatory reported diagnostics. They do not create an alternate PASS route:
the only G25 acceptance criterion remains the pinned 15.0 m2 reachable area.

The fail-capable control is implemented in
`gate25_storey_navigation.py`. It constructs a large otherwise-walkable
storey whose stair lands inside a sealed 2.6 m closet. G25 must report RED even
though total walkable area exceeds 15 m². The same fixture with a 0.90 m doorway
must report GREEN, proving the gate is not permanently red.

Run the controls with:

```powershell
python -m unittest test_gate25_storey_navigation.py
python gate25_storey_navigation.py --fixture sealed_closet
python gate25_storey_navigation.py --fixture open_door_control
```
