# Industrial style

Two constructions, in the order they were tried. The first reused the
residential pipeline and produced buildings the size of an apartment; the
second is a different construction built because of that measurement.

| | Stacked (`stacked_factory10/`) | Tiled (`tiled_factory80/`) |
|---|---|---|
| Construction | two industrially screened single-storey scenes, stacked; one staircase | N×N scenes (N = 3…7) merged into one hall; partial mezzanine; 2–3 staircases |
| Walkable area per building | 111.8–238.4 m², median 123.4 m² (both storeys, measured by G25) | 3,555–10,064 m² on the ground floor alone (6 pilots, G25) |
| Status | 10 buildings, frozen G25 10/10 PASS | 6 pilots measured; 29 two-storey buildings in scope; **0/29 READY** |

The stacked numbers come from the delivered G25 reports of all ten buildings.
A real factory floor is one to two orders of magnitude larger, and each source
scene occupies roughly 20 × 21 m, so no amount of screening yields a large
hall. That is the reason for tiling.

## Sourcing (shared) · [`sourcing/`](sourcing/)

Source scenes are MolmoSpaces `holodeck-objaverse` scenes. Other MolmoSpaces
families were rejected at the source level: the procedural and iTHOR families
are residential, and the MJCF family is a converted copy.

| Step | Files |
|---|---|
| Inventory every official scene subset; sample scene packages deterministically by HTTP byte range | `build_molmospaces_subset_inventory.py`, `sample_official_scene_metadata.py`, `subset_selection_from_log.py` |
| Rank scenes by explicit industrial evidence in their object annotations, then apply a strict semantic and structural review | `analyze_installed_scenes.py`, `strict_factory_source_review.py`, `build_scene_source_decision.py`, `build_selected_screen.py`, `combine_official_scene_selections.py`, `build_factory10_final_source_decision.py`, `audit_factory10_post_repair_industrial_identity.py` |
| Discard scenes that cannot pair before downloading their dependencies | `prefilter_strict_factory_sources.py`, `build_predependency_geometry_cache.py`, `prefilter_a3_necessary_pairs.py`, `build_prefilter_package_selection.py`, `select_official_factory_candidates.py` |
| Download, extract and verify scene and object packages; resolve every external USD dependency | `download_extract_official_packages.py`, `download_official_object_packages.py`, `discover_scene_object_dependencies.py`, `select_missing_object_packages.py`, `audit_object_dependencies.py`, `extract_*`, `materialize_*`, `upgrade_geometry_only_overlay_to_full.py`, `verify_*` |
| Geometry cache, factory-only pair catalogue, doorway cache, sharded A4 placement | `make_*_geometry_cache_inputs.py`, `build_geometry_validation_scene_union.py`, `verify_canonical_scene_cache.py`, `build_factory_pair_catalog.py`, `filter_strict_factory_pairs.py`, `bind_factory_compile_inputs.py`, `augment_doorway_cache.py`, `run_doorway_cache*.sh`, `run_a4_sharded.py`, `run_a4_exact_combined184.sh` |
| Stair assets available for the measured storey heights | `adapt_stair_asset_coverage.py`, `make_available_stair_coverage.py`, `make_stair_height_variant.py`, `audit_factory_stair_assets.py`, `inspect_extract_stair.py` |

The industrial screen is a **text match** on object annotations
("conveyor", "workbench", "control panel", "pallet", …). A hit is evidence
that an object is described as industrial, not a measurement that it is a
metre-scale machine.

## Stacked factories · [`stacked_factory10/`](stacked_factory10/)

The residential chain with factory adapters: `prepare_manifest.py` (factory
type adapter for the frozen A5 contract), `compile_a5_variants10.py`,
`make_floorplans_factory.py`, `run_stage_b10_cpu.py`, Isaac validation
(`finalize_stage_b_a40_isaac.py`, `stage_d10_a40_contract.py`,
`finalize_h1_after_a40.py`), frozen G25 (`prepare_post10_inputs.py`,
`run_g25_exact10.py`), a strict 0.25 m body-erosion audit kept separate from G25
(`audit_strict_body_radius10.py`), collision and door inventory, and the
navigation graph and its gate. All 15 files carry the hash recorded in the
frozen contract manifest.

## Tiled factories · [`tiled_factory80/`](tiled_factory80/)

### Construction

`plan_factory80_tiling.py` draws, per building, a grid size N, a storey count
and three alternative tile assignments (seed 20261011). Every tile in one
building is a different source scene. The mezzanine occupies the first
k(N) cells, k = {3: 4, 4: 7, 5: 10, 6: 15, 7: 21}, which keeps its coverage
between one third and one half; two staircases are planned for N ≤ 4 and three
above. `refine_factory80_source_pairing.py` swaps in more diverse eligible
scenes without discarding the original screen.

`build_factory80_overlay.py` writes one root layer over unmodified sources:

1. **Tiles.** Each source scene is referenced into its cell (pitch 20 × 21 m,
   storey height 4.0 m). Only objects whose asset carries industrial,
   operational or warehouse evidence survive; walls, floors, ceilings and every
   other object are deactivated by opinion. An asset already used elsewhere in
   the building is removed, so equipment does not repeat. The surviving
   equipment keeps its native scale and must fit inside the cell with a 1 m
   aisle on each side, or the tile is rejected.
2. **Floors.** One continuous ground slab over the whole footprint and one
   mezzanine slab over the union of its cells, both in a single material,
   with exact static colliders partitioned into ≤ 10 m triangles that are
   verified coplanar with the visual surface.
3. **Staircases.** Each flight is oriented by the exit direction of its own
   traversal path so that it arrives facing the rear facade, centred in the
   column bay nearest its evenly spaced target along that facade, and placed
   within the deepest continuous mezzanine strip with at least 4 m of floor
   between the arrival and the facade. Its stairwell is an enclosed opening in
   the mezzanine that stops at the final-riser interface, so the mezzanine
   supplies the last tread. The cut mezzanine must remain a single polygon.
4. **Structure.** Perimeter walls with two 4 m openings; a column every half
   pitch, (2N + 1)² columns to the 9 m roof; a truss line per column line
   (two chords and alternating diagonal webs); a roof that is visual only and
   carries no standable collider.
5. **Mezzanine guardrail.** 1.1 m posts every 1.5 m with rails at 0.55 and
   1.05 m along the mezzanine's whole outline, interrupted at each stair
   arrival.
6. **Clearance.** Equipment that intersects any stair's approach or egress
   envelope is deactivated and recorded. The envelope uses the placement
   contract in `house/pipeline/pipeline_contract.py` — 0.22 m radius, 1.75 m
   height, 1.2 m corridor — which is the residential A4 envelope, not the
   Standability V1 body proxy (0.25 m, 1.60 m) that the gates use.

The source layers are re-hashed after saving; a changed source aborts the run.
The receipt `AUTHORING.json` records every tile, retained and removed object,
stair pose, structural member and slab area.

`factory80_deploy_measured_wall_partition_v1.py` and
`factory80_wall_partition_equivalence_fixture_v1.py` replace the wall
colliders only after a frozen-voxeliser fixture shows the partitioned and
original walls rasterise identically. `equipment_density/` adds a second
equipment layer to raise density, packed by `density_rows.py`.

### Staircases under an industrial whitelist

`factory80_whitelist.py` admits a candidate only if, **independently**:

| Parameter | Allowed |
|---|---|
| `staircase_family` | `straight`, `l_shaped`, `u_shaped` |
| `step_material`, `tread_material`, `handrail_material` | `BrushedMetal`, `MetalBasic` |
| `handrail_type` | `vertical-post`, `horizontal-post` (no glass) |
| `support_type` | `double-rail`, `solid`, `hole` |

All three material parameters are checked, not only the one that appears in
the stair signature; a metal tread with a marble handrail has a compliant
signature and is still rejected. Two negative controls ran and failed as
required: metal tread with a marble handrail, and a palette named
`metal_fake` whose step material is wood. `factory80_whitelist_native_adapter.py`
sets the factory attributes before generation and traces the resulting shader
colours; `factory80_whitelist_generate_native.py` runs the unchanged native
generator and exporter; `factory80_whitelist_portable.py` clones assets by
location only and checks the inverse layer. `STAIR_PLACEMENT_RULE_FACTORY80.json`
states the placement rule.

### Measurement

| Question | Files |
|---|---|
| Does each storey stay one walkable region across tile seams? | `factory80_tile_seams_v1/v2.py`, `factory80_corrected_tile_seams_batch_v1.py`, `bounded_support/` |
| Can a 0.25 m body with a 0.20 m step reach the whole storey, and get from any stair to any other? | `factory80_stage2_navigation.py` (with a four-arm self-test: a 0.60 m corridor connects, 0.30 m does not; a 0.15 m step connects, 0.70 m does not), `factory80_stage2_cross_storey.py` |
| Do the frozen CPU geometry functions accept the building? | `factory80_cpu_gates_v3.py`, `factory80_prepare_isaac_inputs.py` |
| Does the navigation graph hold with several staircases and, later, more storeys? | `gate_extension_nstorey_v1.py`, `gate_extension_multistair_builder_v1.py`, `gate_extension_g13_multistair_v1.py`, `gate_extension_controls_v1.py`, `multistair_routes/`, `factory80_graph_stair_distances_v1.py` |

The graph extensions generalise only the storey and stair multiplicities of
the frozen residential gates; thresholds and geometry functions are imported
unchanged.

### Rendering

`render_factory80_isaac.py` renders RTX stills and a 120-frame cutaway orbit
without saving any edit. `lighting_authoring/` writes a render-only layer
(exposure, lights) that is never read by a gate, and `lighting_capture/`
captures it and runs the image checks, with photographic negative controls.
`observe_factory80_isaac.py` plays the Isaac timeline for visually ambiguous
buildings and produces no verdict. `factory80_execute_candidate.py`
orchestrates one candidate from authoring to RTX.

### Scene expansion

A 7 × 7 building needs up to 70 distinct scenes (49 ground-floor and 21
mezzanine tiles), and not every screened scene yields placeable equipment.
`scene_expansion_*.py` inventory, extract, link and verify a pool larger than
the 184 scenes screened at first; `scene_expansion_machine_bounds.py` and
`scene_expansion_missing_equipment_geometry.py` measure equipment geometry
directly in USD.

## Status at the time of the snapshot

Recorded on 2026-10-16. The work is in progress, and no tiled building has
been accepted.

| Measurement | Result | Does not support |
|---|---|---|
| Frozen G25, 6 pilots | 12/12 storeys PASS | body-eroded routes, physics, whole-building acceptance |
| Isaac Stage D, 6 pilots | 1/6 buildings PASS (001, 003–006 FAIL on dark review renders, upper-landing support, approach/egress corridor, furniture clipping) | — |
| Image-quality gates, pilot stills | 34/52 stills excluded | that the renders are usable as images |
| Visual review of the hall | 6/6 rejected: a large open floor with sparse small equipment and no heavy-machinery zone (subjective) | — |
| Navigation-graph G8 / G9 | FAIL on the two pilots where the graph completed (002, 005) | — |
| Negative control N1-3 | **escaped**: a non-stair opening plus a ramp kept the storeys connected after every stair edge was removed, and the frozen stair-dependency check passed | — |
| READY | 0/29 | — |

N1-3 exposed a blind spot in a frozen check rather than a defect in a
building: removing the stair edges is not a proof of stair dependence when
another connection exists. An additional control (A3-V) was registered and
passes its positive and negative cases; it is not part of this snapshot.
