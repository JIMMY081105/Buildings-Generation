# Residential style

Two-storey houses built from two **different** single-storey MolmoSpaces
scenes, stacked and joined by one staircase generated for the measured storey
height. 54 buildings were released this way.

```
 scene pool ─▶ A1 geometry ─▶ A2 type ─▶ A3 stackable ─▶ A4 placement ─▶ A5 package ─▶ H1 review
                                          pairs           search                        (human)
                                                                                           │
   ┌───────────────────────────────────────────────────────────────────────────────────────┘
   ▼
 B staircase cells ─▶ C assemble + normalise ─▶ D validate ─▶ E release ─▶ diversity pass
   (Infinigen)          (stack, cut, colliders)   (Isaac + CPU)  (54 USDZ)    (materials)
```

## Stages

### A — source pool, pairing and placement (CPU) · [`pipeline/`](pipeline/)

| Step | Question | Files |
|---|---|---|
| inventory | Which scenes and object packages exist, and what do they depend on? | `expanded_pool_inventory.py`, `install_expanded_scenes.py`, `install_expanded_objects.py`, `resume_object_install.py`, `extract_expanded_dependencies.py`, `dependency_policy.py` |
| A1 | What is each scene's walkable geometry? | `build_scene_geometry_cache.py`, `enrich_scene_geometry_cache.py` |
| A2–A3 | Which two scenes can stack — footprint agreement, a stair-sized clear patch on Floor 1, and the *same* patch open on Floor 2? | `pair_scenes.py` |
| A4 | Where can a flight of each family go so that both its entry and its exit lead somewhere walkable, every doorway keeps a 0.60 m gap, and nothing clips? | `pick_placement.py`, `doorway_clearance.py`, `door_collision_policy.py`, `inspect_door_panel_colliders.py`, `run_next_batch_a4.py` |
| A5 | Which height × family cells are covered, and what still is not? | `compile_a5_batch.py`, `make_floorplans.py`, `prepare_manifest.py`, `freeze_a5_decisions.py`, `verify_expanded_a5_package.py` |
| driver | | `run_expanded_cpu_a1_a5.py`, `prepare_next_batch_stage_a.py` |
| H1 | Human placement review; generation does not proceed without it. | `finalize_h1_review.py`, `audit_h1_hold.py`, `finalize_doorway_v2_evidence.py` |

A4 searches all four quarter turns on a fine grid with the placement
contract's body envelope (`pipeline_contract.py`: 0.22 m radius, 1.75 m height)
and a 1.2 m approach/egress corridor; `run_expanded_cpu_a1_a5.py` refuses an A4
result that changed any of those. This envelope is for placement only; the
gates use the Standability V1 body proxy (0.25 m, 1.60 m).

Doors are non-blocking by a recorded policy: the swinging leaf and handle lose
collision, frames and jambs keep it, appliance doors are never touched.

### B — staircase assets · [`stairs/`](stairs/)

| Question | Files |
|---|---|
| Generate a flight per storey-height × family cell with Infinigen's `StraightStaircaseFactory`, `LShapedStaircaseFactory`, `UShapedStaircaseFactory` and its USD exporter | `generate_staircase.py`, `run_stage_b_assets.py`, `run_stage_b_r2.sbatch` |
| Is **every** walking-surface rise inside the support step, measured on authored tread facets rather than traversal metadata? | `stair_rise_contract.py` |
| Is the exported asset structurally what it claims? | `audit_staircase_usd.py`, `promote_stage_b_surface_fix.py` |
| Does a 128 px texture twin change anything but textures? | `make_tex128.py` |

`release_manifest.json` and `contracts/` are the original release record; all
13 hashes match the files here.

### C — assembly and physics normalisation · [`pipeline/`](pipeline/)

`build_building.py` references both source scenes read-only, places Floor 2 at
the storey height, cuts the Floor 2 slab and the Floor 1 ceiling around the
stairwell, replaces Floor 2's plane collider with a holed mesh collider, and
checks the flight for clipping against walls and furniture on both floors. The
sources are checksummed before and after.

`normalize_building_physics.py` / `normalize_selected_batch.py` then author
exact, static, per-object colliders (`approximation = none`, no
`RigidBodyAPI`) under the mesh-derived floor contract in
`floor_collider_contract.py`. An earlier version collapsed each storey into a
single aggregate collider and so made the per-floor parity check pass as
1 == 1 on an empty collision scene; the docstring records it.
`build_selected_batch.py` drives a batch.

### D — validation · [`pipeline/`](pipeline/), [`validation/`](validation/), [`navigation_repair/`](navigation_repair/)

| Check | What it measures | Files |
|---|---|---|
| Isaac, per building | PhysX proof that the slab has a hole (up clear, sides blocked, down lands on the stair); swept-volume traversal Floor 1 → stair → Floor 2 with support and headroom; five review renders | `validate_building_isaac.py`, `collision_render_isolation.py`, `finalize_h1_gpu_job.py` |
| G19–G23 | no rigid bodies; every collider exact; per-floor collider parity; collider bounds equal the vertex union; clean voxel bake | `acceptance_gates.py`, `run_h1_cpu_gates.py` |
| G24 | exact stair rises, corroborated by support connectivity | `reachability_gate.py` |
| G25 | per-storey navigation, with a sealed-closet negative control | `validation/gate25_storey_navigation.py`, `validation/G25_SPEC.md`, `run_g25_batch.py` |
| gates 1–4, 6, 13 | USD structure; cache integrity; USD ↔ voxel consistency; structural connectivity; visual audit; stair reachability; delivered-geometry audit | `validation/gate*.py` |
| graph | deterministic T0–T3 multistorey navigation graph, and its G1–G10 gate | `navigation_repair/build_multistorey_graphs*.py`, `validation/gate7_global_graph*.py` |
| repair | strict-door min-cut attribution; evidence-bound collider repair | `navigation_repair/*.py` |

`validate_building_isaac.py` says of itself: *the traversal part is
swept-volume and support checking, not a locomotion policy.*

### E — release of the 54 · [`release54/`](release54/)

Doors are opened by deactivating complete leaf and handle Xforms
(`unify54_open_doors.py`), collisions and doors re-inventoried
(`unify54_audit.py`), every USDZ re-rasterised and G25 re-run on exactly the
released set (`prepare_exact54_inputs.py`, `run_g25_exact54.py`), the graphs
rebuilt and gated (`build_multistorey_graphs54.py`, `gate_global_graph54.py`),
protected gate sources hash-compared (`record_gate_integrity54.py`), then
flattened, compressed, assembled and independently verified.
`pipeline/assemble_h1_delivery.py` and `pipeline/audit_packaged_geometry.py`
do the same for the H1 delivery.

### Staircase diversity · [`stair_diversity/`](stair_diversity/)

The first release used two Infinigen seeds for all 15 prefab staircase cells,
so 54 buildings shared two visual styles, and 41 of 54 flights were straight.
Two routes were tried.

**Geometry** — `portfolio_allocator_v2.py` allocates family, seed and pose by
measured circulation cost (a MILP, deterministic seed), and
`plan_signature_diversity_v1.py` picks the cheapest change that breaks a
repeated signature. `generate_staircase_native_base.py` and
`generate_staircase_diversity_v2/v3.py` drive the official factories with
`native_style_overrides.py`, which may only set attributes the factories
themselves expose. Re-shaped flights then had to pass every frozen gate again.
None did: in the first round all 40 width variants of the straight flight were
rejected by the navigation-graph gate G8, and in the second round the other
families failed strict A4 placement, G8, or a stair-self collider probe. The
geometric signature multiplicity stayed at 15.

**Materials only** — `bake_cap8_material_on_saved_mesh_v3_ab_20261008.py`
re-bakes Infinigen's native materials onto an already audited mesh and
creates no staircase and no collider; `cap8_ab_material_policy_*.py` orders the
assignment and `assemble_cap8_observed_palette_v2_20261007.py` binds each
assignment to the render that was inspected. The `native_material_bake_only_*`
and `native_preview_surface_semantics_v2.py` modules translate the baked
channels into USD preview surfaces. 44 of the 54 buildings changed material;
their geometry was verified byte-identical, and the geometric gates were not
re-run.

### Rendering · [`rendering/`](rendering/)

`render_orbit_isaac_demo_20261009.py` produced the turntable in the top-level
README (Figure 1), invoked by `launch_orbit_final_20261010.sh` on
`home_train96_train99_straight_v001_h270` after the material pass: 120 frames,
120 samples per pixel. It authors the upper-storey cutaway and the lights into a
transient session layer and saves nothing; no gate runs.

## Provenance

[`FROZEN_CONTRACT_SOURCES.json`](FROZEN_CONTRACT_SOURCES.json) is the frozen
snapshot taken by the original run on 2026-09-08 (112 rows; it covers
`pipeline/`, `validation/`, `release54/`, the five upstream simulator modules
and the stacked-factory chain in [`../factory/stacked_factory10/`](../factory/stacked_factory10/)).
Every file published here that it lists has the hash it records. The files it
lists but this repository omits are named, with reasons, in
[`../MANIFEST.json`](../MANIFEST.json).
