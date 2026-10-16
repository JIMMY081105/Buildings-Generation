# Provenance

Every module is in one of three categories. Keeping them separate is what makes
"where did this number come from" answerable later.

1. **Upstream, used as-is** — imported at runtime, nothing vendored.
2. **Derived from upstream** — the algorithm is upstream's; the representation,
   interface or a specific correction is not. Each is listed below with what
   changed and why.
3. **New here** — the connection pipeline, the profile system, the gates.
4. **Snapshot** — `generation/`, the generator scripts as they ran, published
   byte for byte and documented in §4.

---

## 1. Upstream, used as-is

| Project | Licence | How it is used |
|---|---|---|
| [Infinigen](https://github.com/princeton-vl/infinigen) | BSD-3-Clause | `stairs/infinigen_backend.py` imports its staircase factories at call time and subclasses them. No Infinigen source or asset is in this repository. Optional dependency. |
| [OpenUSD](https://github.com/PixarAnimationStudios/OpenUSD) | Apache-2.0 (modified) | `usd-core` dependency for stage composition, geometry and physics schemas. |
| trimesh, numpy, scipy | MIT / BSD | Voxelisation, distance transforms, connected components. |
| [MolmoSpaces](https://huggingface.co/datasets/allenai/molmospaces) | see the dataset card | Source scenes and object packages for both generators. Downloaded by `generation/` scripts; no scene or asset is in this repository. |
| [Blender](https://www.blender.org) `bpy` | GPL-2.0-or-later | Runtime for Infinigen's staircase factories in `generation/`. Not vendored. |
| [NVIDIA Isaac Sim](https://developer.nvidia.com/isaac/sim) | NVIDIA licence | PhysX validation and RTX rendering in `generation/`. Not vendored. |
| ProtoMotions `simulator/base_simulator/` | Apache-2.0 | `scene_geometry_cache`, `standability`, `scene_storey_bands`, `free_space_topology`, `multistorey_topology` are imported by `generation/` gates. Not vendored there. |

---

## 2. Derived from NVIDIA ProtoMotions

Source: <https://github.com/NVlabs/ProtoMotions>, Apache-2.0,
`SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES`.

| File here | Upstream origin | What is the same | What changed, and why |
|---|---|---|---|
| `voxel/grid.py` | `scene_geometry_cache.SceneGeometryGridSpec`, `make_aligned_grid_spec` | Alignment rule, pinned 5 cm / 10 cm sizes, cell-centre semantics, padding-vs-scene-bounds distinction | Added `real_cell_mask()` and `axis_centers_m()` as public helpers, because several consumers here were each re-deriving them and one of them had the padding wrong. |
| `voxel/rasterize.py` | `scene_geometry_baking.voxelize_triangle_mesh`, `conservative_splat_voxel_centers`, `rasterize_triangle_mesh` | Subdivision voxelisation, the conservative equal-size splat and its two-cells-per-axis assertion | Added `fill="auto"`: a **watertight** collider is filled, an open mesh is not. Upstream fills only approximated colliders, which leaves a closed box hollow. Under the terrain-following clearance model the cavity inside a hollow slab then reads as a second standable floor one slab-thickness below the real one, and storey detection finds twice the area it should. Also derived `SPLAT_TOLERANCE_M` explicitly so Gate 3's tolerance is a consequence of the method rather than a tuned constant. |
| `voxel/standability.py` | `standability.StandabilityConfig`, `_proxy_footprint`, `_candidate_support_fixed_base`, `_candidate_support_terrain_following`, `_filter_small_components` | The pinned numbers and their `validate()`; the footprint stamp; **both** candidate-support derivations, including the terrain-relief band | **Representation.** Upstream keeps one support height per XY column, which is sufficient for a single-storey scene and structurally unable to describe a building where one column is floor on level 0 and ceiling on level 1. This keeps the full `[nx, ny, nz]` volume. Connectivity is a sparse graph over that volume (cardinal in XY, bounded step in Z) rather than a per-column flood, so components, routes and reachability all come from one adjacency. |
| `voxel/cache.py` | `scene_geometry_cache` write/load | Directory layout, file names, `packbits(C-order, little)`, commit-marker-last discipline, per-array sha256 | Trimmed to what this pipeline needs (no movable-link bookkeeping beyond exclusion paths) and made the tamper path an explicit `CacheError` so Gate 2 can use it as a negative control. |
| `acceptance/gates.py` G6 | `final_training_validation/gate6_stair_reachability.py` of the originating project | The two-measurement structure: authored rises are the criterion, the flood corroborates; the terrain-lowering control sweep | Generalised off four hard-coded buildings and fixed prim paths. Ground and slab heights are now read from **authored** collider tops chosen by largest footprint, not from the voxel-quantised storey estimate — the estimate is conservative by up to one cell, which showed up as a 0.05 m "defect" that was an artefact of the grid. |

### Two corrections applied to Infinigen's staircase factories

Both live in `stairs/infinigen_backend.py` and are reproduced by the
dependency-light backend in `stairs/asset.py`.

**Tread tops on the riser grid.** The upstream factories place the *underside*
of each tread plate at `(i + 1) * step_height`. The plate thickness therefore
leaks into the first rise — ground → first tread measures
`step_height + tread_height` — while the final tread → upper-floor rise is short
by the same amount. At a 0.175 m riser and a 0.0615 m plate the first rise
measures 0.228–0.242 m against the 0.20 m support step: the flight is
unclimbable and nothing in a render shows it. The mixin shifts each tread object
(including a turning platform) down by its own thickness before export. It
changes tread placement only, and is **not** the cruder workaround of
translating the whole staircase, which fixes the first rise and breaks the last.

**The upper floor supplies the last tread.** The upstream factory authors one
tread per riser, correct for a freestanding asset and wrong once integrated:
tread *n* duplicates the upper slab. The mixin keeps all *n* risers and
regenerates with *n − 1* treads, recording the removed tread's vertex count,
triangle count and AABB so the deletion is auditable rather than implicit.

---

## 3. New here

- `config.py` — building-type profiles, `extends` resolution, and the rule that
  a profile may only tighten a threshold.
- `usd/storeys.py` — geometric storey detection. Prim names label; they never
  decide.
- `usd/stage.py` — the repair-by-overlay discipline and `assert_source_unchanged`.
- `stairs/spec.py` — riser-count search against the measured storey height under
  the Blondel relation, with the rule that an asset is never scaled after
  generation.
- `stairs/placement.py` — the base / shaft / arrival / departure search.
- `pipeline.verify_placement` — rasterise the chosen flight where it will go,
  re-derive standability and flood, before committing to the pose. The search
  works on a proxy; this is what makes the proxy's approximations safe.
- `acceptance/` — gate result type with mandatory controls and scope, the seven
  gates, the fail-closed runner.
- `fixtures/synthetic.py` — buildings with known defects, so the gates have
  inputs that must fail.

---

## Numbers that are pinned, and where they came from

| Value | Where | Why it is fixed |
|---|---|---|
| body radius 0.25 m | `StandabilityV1` | Shoulder/hip half-width of the human-scale body proxy; fixed across the project. |
| body height 1.60 m | `StandabilityV1` | 32 cells at 5 cm. |
| support step 0.20 m | `StandabilityV1` | Maximum single step the body proxy admits; also the ceiling on any riser. |
| minimum component 1.00 m² | `StandabilityV1` | 400 cells; below this a "region" is not somewhere a body can turn around. |
| voxel 5 cm / 10 cm | `voxel/grid.py` | Cache format; caches at other resolutions are not comparable. |
| Blondel target 0.63 m | profile, editable | `2 × riser + going`; the comfort relation, per building type. |

---

## 4. Generation snapshot

`generation/` holds the scripts that generated the residential and industrial
buildings, copied byte for byte from the workspaces they ran in on 2026-10-16.
They are neither derived nor rewritten here, and they are not part of the
`buildingsgen` package.

- `generation/MANIFEST.json` gives each file's size, SHA-256 and origin path,
  and lists every omitted file with the reason for omitting it.
- 107 files also appear in a frozen contract manifest written by the original
  run (`house/FROZEN_CONTRACT_SOURCES.json`, `house/stairs/release_manifest.json`,
  `house/navigation_repair/SOURCE_SNAPSHOT.json`) with the same SHA-256.
- `tests/test_generation_snapshot.py` re-hashes the tree in CI and fails on a
  changed byte or an unlisted file.

Upstream code that the original workspaces contained is omitted rather than
redistributed: the MolmoSpaces downloader (`official_download.py`, a copy
of MolmoSpaces' `download.py`) and the five ProtoMotions simulator modules
listed above.
