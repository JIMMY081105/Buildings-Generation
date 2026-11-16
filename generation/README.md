# Generation snapshot

The scripts that produced the residential (**house**), industrial
(**factory**) and **museum** buildings: house and the factory pilots as of
2026-10-16, the industrial Round 4 and the museums as of 2026-11-15.

This directory is a research artefact, not a library. It is the code that ran,
so that every number reported in the top-level README can be traced to the
procedure that produced it. The installable, tested library is
[`src/buildingsgen`](../src/buildingsgen); it re-implements the storey
connection and acceptance logic in a portable form. The snapshot keeps the
generators as they were.

| | Residential (house) | Industrial (factory) | Museum |
|---|---|---|---|
| Directory | [`house/`](house/README.md) | [`factory/`](factory/README.md) (pilots in [`archive/`](archive/factory_pilots/README.md)) | [`museum/`](museum/README.md) |
| Construction | two different single-storey scenes stacked, joined by one generated staircase | N×N bay plan by function with a mezzanine, equipment from role pools, whitelisted metal staircases (pilots: stacked scenes; tiled scenes) | authored from a design spec: parametric shell, one of five grand-staircase families, furnished galleries |
| Source scenes / assets | MolmoSpaces (residential) | MolmoSpaces `holodeck-objaverse`; NVIDIA SimReady, Industrial and Warehouse packs | Poly Haven, ambientCG, Smithsonian 3D, Cleveland Museum of Art open access; Infinigen architectural factories |
| Staircases | Infinigen `straight` / `l_shaped` / `u_shaped` factories | the same factories under an industrial whitelist | helical, arc, imperial, three-flight well, cantilever (`staircase_core.py`) |
| Released | 54 buildings | 27 halls (Round 4); 10 stacked pilot buildings | 29 buildings |

---

## What "the code that ran" means here

[`MANIFEST.json`](MANIFEST.json) lists every file with its size, SHA-256 and
the path it was copied from. The copy step hashed each file at the source and
again at the destination and aborted on any difference.

`house/` and `archive/` are byte-identical to their origin. In `factory/` and
`museum/` the original files carry round and revision numbers
(`layout_v7.py`, `museum_nordic28.py`); they are published under role names
(`hall_layout.py`, `museum_nordic.py`), and every occurrence of an old module
name inside the files was replaced by the new one. Nothing else changed: the
manifest keeps the mapping (`renamed_modules`) and each file's
`original_sha256`, and substituting the old names back reproduces the original
bytes exactly.

107 of the 395 files are additionally listed in a **frozen contract manifest**
written by the original run — [`house/FROZEN_CONTRACT_SOURCES.json`](house/FROZEN_CONTRACT_SOURCES.json)
(112 rows), [`house/stairs/release_manifest.json`](house/stairs/release_manifest.json)
(13 rows) and [`house/navigation_repair/SOURCE_SNAPSHOT.json`](house/navigation_repair/SOURCE_SNAPSHOT.json)
(3 rows). For those, `contract.sha256_matches` is `true` only because the hash
recorded at the time equals the hash of the file published here. The remaining
149 house and factory-pilot files have no independent record; their hash is
this snapshot's own. The 40 industrial Round 4 files are the final runner, every
tool it calls and the 14 helper modules those tools import; the tool hashes
match those recorded in the `E2E_RESULT.json` of the READY buildings, and the
19 earlier revisions recorded there are listed under `superseded`. The 99
museum files were hashed at their origin when copied.

`tests/test_generation_snapshot.py` re-hashes the tree on every CI run. It
fails on a changed byte, on a file that is not in the manifest, on an edit
beyond the renaming and on a missing rename entry, and four negative controls
in the same file show that it does.

```bash
pytest tests/test_generation_snapshot.py
```

`.gitattributes` marks `generation/**` as binary so that line-ending
conversion on checkout cannot alter the published bytes.

## What it takes to run

These scripts are bound to the environment they ran in. Expect, without
exception:

- **Absolute paths** to a shared cluster filesystem (`/cephfs/shared/...`,
  `/data/run01/...`) and to the originating workspace. They are left as they
  were; rewriting them would make the published code differ from the code
  that ran.
- **Original file names.** Some runners compare tool hashes with those
  recorded by earlier runs; re-running them on the original layout needs the
  original names from `renamed_modules`.
- **Sibling imports.** Scripts import each other by module name from their own
  directory, and some insert a second frozen directory into `sys.path`. Each
  directory therefore keeps the flat layout it had; the stage structure is
  documented in the per-style READMEs rather than encoded in folders.
- **External runtimes**: OpenUSD (`usd-core`), Blender `bpy` with Infinigen for
  staircase synthesis, NVIDIA Isaac Sim for PhysX validation and RTX
  rendering, and the geometry modules of ProtoMotions'
  `protomotions/simulator/base_simulator/` (`scene_geometry_cache`,
  `standability`, `scene_storey_bands`, `free_space_topology`,
  `multistorey_topology`). The last are imported, not redistributed.
- **Source corpora** that are not in this repository: MolmoSpaces scene and
  object packages, and the generated staircase assets.

Reading the code to understand a step is the intended use. Re-running a step
means adapting its paths, and the result is then a new run, not this one.

## What is deliberately not here

The manifest's `excluded` list names every omission and its reason. In
summary:

| Omitted | Why |
|---|---|
| Prop insertion, site evidence and delivery packaging for a downstream motion-learning project | Out of scope: this repository generates and accepts buildings. |
| Graph-token packing and capacity, "Transformer-readable" proofs, point-cloud input capacity | These format a scene for a learned policy's input; they are a training interface, not generation. |
| Runtime smoke test of a motion-learning framework (gate 5); spawning a robot model in the factory (stage 3) | They exercise a learning runtime or a robot, not the building. |
| `generate_staircase_diversity_v4`–`v7` | v4 (140 kB) imports the campaign's batch orchestration, GPU lease and gate runners; v5–v7 only wrap v4. v3, the last self-contained revision, is included. |
| ProtoMotions `simulator/*.py` (5 files) | Upstream NVIDIA code; a dependency. |
| MolmoSpaces `download.py` | Upstream code; use the original. |
| Transfer, GPU scheduling, shared-card hold, process supervision, read-only probes, report publication | Operations, not generation. Several hundred such scripts exist in the original workspaces. |
| Run data (`TILING_PLAN.json`, `SCENE_EXPANSION_600.json`, equipment geometry index) | Data, not code; regenerated by the scripts that wrote them. |

The navigation-graph builders (`build_multistorey_graphs*`) and the graph gate
(`gate7_global_graph*`) are kept: they describe the building's topology and
are part of acceptance. Only the step that packs that graph into tensors for a
learner is omitted.

## Status labels used in the code

The scripts write their own status strings, and they are deliberately narrow.
`READY_FOR_ACCEPTANCE` means machine gates passed and nothing more; `PASS` on
one gate says nothing about another; a rendered video says nothing about
geometry. Where a record carries a `cannot_support` field, that field is the
scope of the result and should be read with it.
