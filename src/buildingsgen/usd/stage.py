"""Stage access, and the repair-by-overlay rule.

**A source building is never modified.**  Every correction - a stair shifted
down by its tread thickness, a doorway collider re-enabled, a stray visual mesh
deactivated - is authored into a *root-layer overlay* that sublayers the
untouched source.  Three reasons, all of them learned the expensive way:

1. The provenance of a measurement stays answerable: the source hash in an
   evidence record still resolves months later.
2. A repair can be reverted by deleting one file.
3. A repair cannot silently leak into a different experiment that references
   the same source asset.

``open_with_overlay`` is therefore the normal way to open a building for
editing, and ``assert_source_unchanged`` is cheap enough to call after every
write.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pxr import Gf, Sdf, Usd, UsdGeom

USD_SUFFIXES = (".usd", ".usda", ".usdc", ".usdz")


class StageError(ValueError):
    """A stage is missing, malformed, or was opened in a way that risks the source."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class SourceRef:
    """An immutable reference to an input file, for evidence records."""

    path: str
    size: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


def source_ref(path: Path) -> SourceRef:
    path = Path(path)
    if not path.is_file():
        raise StageError(f"no such file: {path}")
    return SourceRef(path=str(path), size=path.stat().st_size, sha256=sha256_file(path))


def assert_source_unchanged(reference: SourceRef) -> None:
    """Re-hash the source and raise if anything wrote to it."""

    current = source_ref(Path(reference.path))
    if current.sha256 != reference.sha256 or current.size != reference.size:
        raise StageError(
            f"source file changed during the run: {reference.path}\n"
            "repairs must be authored into an overlay, never into the source"
        )


def open_stage(path: Path, *, load_all: bool = True) -> Usd.Stage:
    path = Path(path)
    if path.suffix.lower() not in USD_SUFFIXES:
        raise StageError(f"{path} is not a USD file")
    if not path.is_file():
        raise StageError(f"no such stage: {path}")
    stage = Usd.Stage.Open(str(path), Usd.Stage.LoadAll if load_all else Usd.Stage.LoadNone)
    if stage is None:
        raise StageError(f"OpenUSD refused to open {path}")
    return stage


def open_with_overlay(source: Path, overlay: Path) -> tuple[Usd.Stage, SourceRef]:
    """Open ``source`` read-only beneath a writable root layer at ``overlay``.

    Edits land in the overlay.  ``overlay`` is created if absent and must not
    be the source itself.
    """

    source = Path(source).resolve()
    overlay = Path(overlay).resolve()
    if overlay == source:
        raise StageError("the overlay must be a different file from the source")
    reference = source_ref(source)
    overlay.parent.mkdir(parents=True, exist_ok=True)
    if overlay.exists():
        layer = Sdf.Layer.FindOrOpen(str(overlay))
        if layer is None:
            raise StageError(f"could not open existing overlay {overlay}")
    else:
        layer = Sdf.Layer.CreateNew(str(overlay))
    layer.subLayerPaths = [str(source)]
    stage = Usd.Stage.Open(layer, Usd.Stage.LoadAll)
    if stage is None:
        raise StageError(f"could not compose {overlay} over {source}")
    stage.SetEditTarget(Usd.EditTarget(layer))
    # Stage metadata lives on the root layer, so a bare overlay would flatten to
    # a Y-up, unit-less stage no matter what the source says.  Copy the composed
    # values onto the overlay explicitly.
    source_stage = Usd.Stage.Open(str(source), Usd.Stage.LoadNone)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(source_stage))
    UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.GetStageMetersPerUnit(source_stage))
    default_prim = source_stage.GetDefaultPrim()
    if default_prim and default_prim.IsValid():
        target = stage.GetPrimAtPath(default_prim.GetPath())
        if target and target.IsValid():
            stage.SetDefaultPrim(target)
    return stage, reference


def export_flattened(stage: Usd.Stage, destination: Path) -> Path:
    """Write the composed stage to one self-contained layer."""

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    flattened = stage.Flatten()
    flattened.documentation = ""
    if not flattened.Export(str(destination)):
        raise StageError(f"failed to export flattened stage to {destination}")
    return destination


def translate_prim(stage: Usd.Stage, prim_path: str, offset_m: tuple[float, float, float]) -> None:
    """Add a world-space translation op to a prim, in metres.

    Used by the stair integration fix: the whole flight moves down by one tread
    thickness so the first walking surface lands on the riser grid instead of a
    plate thickness above it.  Authored into the current edit target, which
    under :func:`open_with_overlay` is the overlay.
    """

    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        raise StageError(f"no prim at {prim_path}")
    xformable = UsdGeom.Xformable(prim)
    if not xformable:
        raise StageError(f"{prim_path} is not transformable")
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    units = tuple(value / mpu for value in offset_m)
    op_name = "xformOp:translate:buildingsgen_fix"
    existing = [op for op in xformable.GetOrderedXformOps() if op.GetOpName() == op_name]
    op = existing[0] if existing else xformable.AddTranslateOp(opSuffix="buildingsgen_fix")
    op.Set(Gf.Vec3d(*units))


def stage_statistics(stage: Usd.Stage) -> dict[str, Any]:
    prims = list(stage.Traverse())
    return {
        "prim_count": len(prims),
        "mesh_count": sum(1 for prim in prims if prim.IsA(UsdGeom.Mesh)),
        "meters_per_unit": float(UsdGeom.GetStageMetersPerUnit(stage)),
        "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
        "default_prim": stage.GetDefaultPrim().GetName() if stage.GetDefaultPrim() else None,
    }
