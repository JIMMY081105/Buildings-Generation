# Museum style · Museum29

Twenty-nine museums in twelve architectural styles, each with one of five
grand-staircase families, two to four storeys, furnished galleries and a
collision package that a 0.25 m × 1.6 m capsule has been shown to traverse.

| | |
|---|---|
| Buildings | 29 (M01–M29), all delivered |
| Styles | neoclassical, Victorian natural history, Gothic, industrial conversion, modern, brutalist, Art Deco, Nordic, East Asian, Mediterranean, marine, glasshouse |
| Staircase families | **H** grand helical · **A** grand arc · **I** imperial (bifurcated) · **W** three-flight square well · **C** cantilever — each used in ≥ 4 buildings |
| Storeys | 2–4 |
| Photographs | 12 per building, 1920 × 1080, Isaac Sim RTX path tracing (348 in total) |
| Acceptance | C1–C5 per building (below), plus image, colour, exhibit-size and stair-joint gates |

Unlike the residential and industrial generators, which stack or tile
existing single-storey scenes, a museum is authored from a design spec:
the shell, the staircase, the galleries and the furniture are all generated,
and third-party assets are limited to scanned exhibits, textures and HDRIs.

## Pipeline

| Stage | Files | What it does |
|---|---|---|
| Design spec | `specs/M01.json` … `specs/M29.json` | Style, footprint, storey count and heights, staircase family and dimensions, palette, exhibition themes. One file per building. |
| Shell and staircase | `build_candidate.py` → `building_core.py`, `staircase_core.py`, `stair_assemblies.py` | Parametric shell and slabs; the staircase is one of the five families, authored as explicit watertight meshes in metres, Z-up. Wedge and arc treads are exact meshes, never boxes. |
| Style and architecture | `museum_style.py`, `museum_facades.py`, `museum_details.py`, `museum_dome.py`, `museum_rails.py`, `museum_<style>*.py` | Style-specific facades, columns, roofs, balustrades; Infinigen architectural factories for doors, casings, pillars and windows. |
| Galleries | `generate_museum.py`, `gallery_layout.py`, `museum_exhibits.py`, `museum_themes.py`, `museum_originals.py`, `museum_lighting.py` | Exhibition walls checked against the real gallery polygons; licensed scans placed on physically supported plinths and cases; procedurally generated original artwork; supported practical fixtures. |
| Export | `export_museum.py`, `blender_common.py` | Evaluated Blender geometry to portable `UsdPreviewSurface` visual USD and an exact collision USD. |
| Static checks (C1, C3, C4) | `validate_c1.py`, `validate_stair_tests.py`, `validate_visual.py`, `validate_static_bundle.py` | Collider manifest and native dependency readback; independent triangle readback of the exported stair with support rays and area samples; railing centre lines and material bindings. |
| Physics (C5) | `isaac_building_c5.py`, `native_c5.py` | Native PhysX cooking, capsule overlap control, and drop-and-settle trials on every fifth tread, landing and floor, with a slab-penetration negative control. |
| Photographs | `isaac_building_rtx.py`, `render_hdr.py`, `gallery_planning.py`, `exterior_camera.py`, `image_gate.py` | One admitted Isaac Sim session captures the 12 views from the exact visual USD as linear HDR, then applies one exposure and tone curve; black or near-empty frames are rejected. |
| Resource control | `resource_guard.py`, `resource_admission*.py`, `resource_sampler*.py`, `resource_source_audit.py`, `resource_watchdog_probe.py` | Admission on a shared GPU node: free memory must exceed the measured peak of the job class plus 8 GiB, one Isaac process at a time, a 1 Hz cgroup watchdog. |

## The five acceptance checks

| Check | Requirement |
|---|---|
| C1 | Collision inventory: exact meshes only, metres, Z-up, no invalid values. |
| C2 | Per-storey reachability for a 0.25 m-radius, 1.6 m-tall body ≥ 95 %, through the open doors; negative control: a real wall must make it fail. |
| C3 | The main staircase connects every storey; measured stair-to-slab joint, and a 0.6 m set-back negative control. |
| C4 | ≥ 90 % of every tread (wedge and arc treads measured on their real shape) has 1.6 m of clear headroom. |
| C5 | Isaac Sim load; a capsule stands without penetrating on every fifth tread, every landing and every floor; a slab-penetration negative control. |

Photographs 05 and 11 share one camera: 05 is the path-traced stair-to-slab
joint and 11 the same view of the collision geometry (stairs orange, slabs
blue), so the claim "the staircase reaches the floor" is visible, not just
asserted.

## What is not in this directory

The full Museum29 workspace holds about 2,200 scripts; most are one-off
audits, diagnostics and per-building repairs written while the batch ran.
This directory is the **import closure** of the generator, exporter,
Isaac C5/RTX runners and static validators, computed from their Python
imports and literal script references — 70 files — plus the 29 specs.

The original files carry round and revision numbers (`museum_nordic28.py`,
`ad98_sampler1927.py`). They are published under role names, and every
reference to an old module name inside the files was replaced by the new one;
nothing else changed. [`../MANIFEST.json`](../MANIFEST.json) keeps the mapping
(`renamed_modules`), the origin and each file's `original_sha256`, and
`tests/test_generation_snapshot.py` substitutes the names back and checks that
the original bytes are reproduced.

## Known limitation

The legacy exporter writes Blender AREA lights as USD `DiskLight` with
`intensity = 1.5 × power` and `normalize = false`, while Cycles normalises AREA
power by emitter area. In an unoccluded analytic model of five exhibit
positions in M21, the AREA-to-sky ratio in USD is about 2 % of the Blender
design value. Interiors are therefore lit mostly by the sky dome. Ten exhibits
in three buildings (M21, M25, M29) fail only the colour-saturation-ratio gate
and are listed per item in each delivery's `KNOWN_EXCEPTIONS.json`; the other
26 buildings pass every gate.
