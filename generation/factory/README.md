# Industrial style

The construction that produced the 27 delivered industrial halls (Round 4).
Each hall is an N × N bay plan with a mezzanine, planned by function, filled
from role pools of NVIDIA SimReady, NVIDIA Industrial and Warehouse pack and
MolmoSpaces assets, and accepted by C1–C5 in one clean pass under a one-hour
wall-clock limit.

| | |
|---|---|
| Buildings | 27 READY |
| Colliders per hall | 9,354–217,277 (median 79,356) |
| Placed asset instances | 294–1,205 (median 595) |
| C2 reachability (same-bay and cross-bay) | minimum 95.27 % over 108 floor × class measurements (threshold 95 %) |
| End-to-end time | 644–3,500 s per building (median 1,487 s; limit 3,600 s) |

The earlier stacked and tiled pilots are in
[`../archive/factory_pilots/`](../archive/factory_pilots/README.md), with the
measurements that led to this construction.

## Pipeline

| Stage | Files | What it does |
|---|---|---|
| Layout | `hall_layout.py`, `c4_alternative_check.py`; helpers `unit_geometry.py`, `equipment_units.py`, `welding_units.py`, `area_count_rules.py`, `density_counts.py`, `layout_unit_audit.py`, `floor_placement_audit.py` | Deterministic function and bay planning (assembly, machining, paint/clean, power, warehouse, welding); mezzanine shapes strip, L-shaped and central island; source-bound asset placement with aisles; a deterministic alternative when a staircase fails C4. |
| Assembly | `scene_compose.py`, `sample_floor_stairs.py`, `retain_architecture.py`, `rgb_instance_wrappers.py`, `hall_lights.py` | Retained hall architecture, placed assets and native staircases composed into one stage; calibrated LED fixtures. |
| Voxel cache | `voxel_scene.py`, `voxel_extract.py`, `voxel_gpu.py`, `voxel_raster.py` | Typed CUDA rasterisation at 5 cm with the exact native stair geometry declared by the source. |
| C1–C4 | `gpu_checks.py` | One shared 5 cm occupancy grid for the collider inventory, per-bay reachability (≥ 95 % same-bay and cross-bay), stair connectivity and 1.6 m tread headroom (≥ 90 %). |
| C5 | `isaac_native_c5.py`, `isaac_capsule_query.py` | Native Isaac Sim capsule stand test on treads, landings and floors. |
| Photographs | `camera_planner.py`, `rtx_render.py` | Camera poses planned from layout geometry without the render SDK; six RGB views and a pure-collision grey view. |
| Package | `scene_package.py`, `deliver_batch.py`, `report_batch.py`, `archive_review.py` | Portable collision USD built from unique source proxies, a ProtoMotions SceneLib `scenes.pt`, and a reviewed archive. |
| Batch control | `run_batch.py`, `wall_clock_policy.py`, `revision_gate.py`, `approved_revision_policy.json`, `resource_trial.py`, `gpu_identity_check.py` | Per-building SDK session, out-of-memory priority, resource retries and the wall-clock contract. None of them contains a scientific predicate. |

`run_batch.py` is the entry point. A file ending in `_legacy.py` is the
previous revision of the tool with the same name; the runner still calls it to
re-verify and recover halls produced before the last revision.

## File names

The original files carry version suffixes (`layout_v7.py`, `batch_run_v16.py`).
They are published under role names, and every reference to an old module
name inside the files was replaced by the new one; nothing else changed.
[`../MANIFEST.json`](../MANIFEST.json) keeps the mapping (`renamed_modules`)
and each file's `original_sha256`, and `tests/test_generation_snapshot.py`
substitutes the names back and checks that the original bytes are reproduced.

Several self-checks inside the runner compare tool hashes with those recorded
by earlier runs, so a re-run on the original layout needs the original names.
The 19 earlier revisions recorded by READY runs (`batch_run_v2` … `v13`,
`layout_v3`, `layout_v5`, …) are listed under `superseded` in the manifest with
their hashes and are not included.

Assets: NVIDIA SimReady (CC-BY-4.0), NVIDIA Industrial, Warehouse and USD
Explorer packs (Omniverse terms, not redistributed), MolmoSpaces objects and a
few project-authored parts. See [`docs/assets.md`](../../docs/assets.md).
