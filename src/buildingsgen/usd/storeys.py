"""Find the storeys of a building without being told where they are.

Detection is geometric.  Prim names are used only to *label* what geometry
already proved, never to decide how many storeys exist or where they sit -
which is what lets the same code accept a residential USD naming its roots
``/World/Floor1`` and an industrial one naming them ``/World/Level_00``.

The signal is the count of standable voxels per Z row: a storey is a height
band where a large area is walkable.  Furniture tops, mezzanine edges and stair
treads also contribute standable voxels, but each covers far less area than the
slab it sits on, so an area threshold separates them without any naming
convention at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from pxr import Usd

from ..config import StoreyModel
from ..voxel.standability import StandableVolume


@dataclass(frozen=True)
class Storey:
    """One detected walking level."""

    index: int
    label: str
    floor_z_m: float
    band_min_z_m: float
    band_max_z_m: float
    area_m2: float
    #: Standable voxels assigned to this storey, same shape as the volume mask.
    cells: np.ndarray

    @property
    def footprint(self) -> np.ndarray:
        """XY columns this storey occupies."""

        return self.cells.any(axis=2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "floor_z_m": round(self.floor_z_m, 4),
            "band_min_z_m": round(self.band_min_z_m, 4),
            "band_max_z_m": round(self.band_max_z_m, 4),
            "area_m2": round(self.area_m2, 3),
        }


@dataclass(frozen=True)
class StoreyDetection:
    storeys: list[Storey]
    #: Storey-to-storey rises, in order.
    separations_m: list[float]
    #: Separations outside the profile's [min, max] storey-height window.
    implausible_separations: list[dict[str, float]]
    #: Standable area no storey band claimed (furniture tops, stair treads).
    unassigned_area_m2: float

    @property
    def count(self) -> int:
        return len(self.storeys)

    def as_dict(self) -> dict[str, Any]:
        return {
            "storey_count": self.count,
            "storeys": [storey.as_dict() for storey in self.storeys],
            "separations_m": [round(value, 4) for value in self.separations_m],
            "implausible_separations": self.implausible_separations,
            "unassigned_standable_area_m2": round(self.unassigned_area_m2, 3),
        }


def detect_storeys(
    volume: StandableVolume,
    model: StoreyModel,
    *,
    labels: list[str] | None = None,
) -> StoreyDetection:
    """Cluster standable voxels into storeys by height.

    Greedy, largest band first: take the Z row holding the most standable
    voxels, claim every row within half a slab thickness of it, and repeat on
    what is left.  A band below ``min_storey_floor_area_m2`` is not a storey.
    """

    cell_area = volume.grid.cell_area_m2
    mask = volume.mask
    if not mask.any():
        return StoreyDetection([], [], [], 0.0)

    per_row = mask.sum(axis=(0, 1)).astype(np.int64)
    surface_z = volume.surface_z_m()
    half_band = 0.5 * model.max_slab_thickness_m
    minimum_cells = int(np.ceil(model.min_storey_floor_area_m2 / cell_area))

    remaining = per_row.copy()
    bands: list[tuple[float, np.ndarray, int]] = []
    while remaining.max(initial=0) > 0:
        peak = int(np.argmax(remaining))
        peak_z = float(surface_z[peak])
        inside = np.abs(surface_z - peak_z) <= half_band + 1e-9
        cells = int(remaining[inside].sum())
        if cells < minimum_cells:
            break
        bands.append((peak_z, inside, cells))
        remaining[inside] = 0

    bands.sort(key=lambda item: item[0])
    if labels and len(labels) != len(bands):
        # The corpus names some storey-like prims but not one per detected
        # band.  Labelling by position would then be a guess presented as a
        # fact, so fall back to generic labels.
        labels = None
    storeys: list[Storey] = []
    assigned = np.zeros(mask.shape, dtype=bool)
    for index, (peak_z, rows, cells) in enumerate(bands):
        band = np.zeros(mask.shape, dtype=bool)
        band[:, :, rows] = mask[:, :, rows]
        band &= ~assigned
        assigned |= band
        storeys.append(
            Storey(
                index=index,
                label=_label_for(index, len(bands), labels),
                floor_z_m=peak_z,
                band_min_z_m=float(surface_z[rows].min()),
                band_max_z_m=float(surface_z[rows].max()),
                area_m2=float(cells) * cell_area,
                cells=band,
            )
        )

    separations = [
        storeys[i + 1].floor_z_m - storeys[i].floor_z_m for i in range(len(storeys) - 1)
    ]
    implausible = [
        {
            "lower_storey": round(float(storeys[i].floor_z_m), 4),
            "upper_storey": round(float(storeys[i + 1].floor_z_m), 4),
            "separation_m": round(value, 4),
            "min_allowed_m": model.min_storey_height_m,
            "max_allowed_m": model.max_storey_height_m,
        }
        for i, value in enumerate(separations)
        if value < model.min_storey_height_m - 1e-9 or value > model.max_storey_height_m + 1e-9
    ]
    unassigned = float((mask & ~assigned).sum()) * cell_area
    return StoreyDetection(storeys, separations, implausible, unassigned)


def _label_for(index: int, total: int, labels: list[str] | None) -> str:
    if labels and index < len(labels):
        return labels[index]
    if index == 0:
        return "ground"
    if index == total - 1 and total > 2:
        return f"level_{index}_top"
    return f"level_{index}"


def name_hint_labels(stage: Usd.Stage, model: StoreyModel) -> list[str]:
    """Storey-like prim names, ordered, for labelling only.

    Returns the names of prims directly under the default prim (or ``/World``)
    whose name contains one of the profile's hints.  If the corpus uses no such
    convention this is empty and detection still works; nothing here feeds a
    geometric decision.
    """

    root = stage.GetDefaultPrim() or stage.GetPrimAtPath("/World")
    if not root or not root.IsValid():
        return []
    hints = tuple(hint.lower() for hint in model.name_hints)
    return sorted(
        child.GetName()
        for child in root.GetChildren()
        if any(hint in child.GetName().lower() for hint in hints)
    )


def authored_surface_z_m(
    stage: Usd.Stage,
    near_z_m: float,
    *,
    region_min_xy: tuple[float, float] | None = None,
    region_max_xy: tuple[float, float] | None = None,
    tolerance_m: float = 0.15,
    fallback_z_m: float | None = None,
) -> float:
    """The authored walking surface nearest below ``near_z_m``.

    Storey heights detected from voxels carry up to one voxel of conservative
    bias: a slab top exactly on a cell boundary marks the cell above it too, so
    the derived surface sits 5 cm high.  Placing a stair on that biased height
    adds 5 cm to its first rise - which is precisely the defect Gate 6 exists
    to catch, so the placement must not manufacture it.

    Among the collider tops within ``tolerance_m`` below the estimate, the one
    with the largest XY footprint wins - not the highest.  A floor is the widest
    thing at its own height; taking the highest instead picks up a handrail
    cap, a pipe or a worktop that happens to end a few centimetres under the
    slab, and the whole last-rise measurement then reports a defect that is not
    there.  Falls back to ``fallback_z_m`` (the simulator terrain) when the
    storey authors no slab at all, which is common for a ground floor.
    """

    from .colliders import collect_colliders

    records, _, _ = collect_colliders(stage)
    best: tuple[float, float] | None = None  # (footprint area, top z)
    for record in records:
        top = record.bounds_max_m[2]
        if not (near_z_m - tolerance_m - 1e-9 <= top <= near_z_m + 1e-9):
            continue
        if region_min_xy is not None and region_max_xy is not None and (
            record.bounds_max_m[0] < region_min_xy[0]
            or record.bounds_min_m[0] > region_max_xy[0]
            or record.bounds_max_m[1] < region_min_xy[1]
            or record.bounds_min_m[1] > region_max_xy[1]
        ):
            continue
        area = (
            (record.bounds_max_m[0] - record.bounds_min_m[0])
            * (record.bounds_max_m[1] - record.bounds_min_m[1])
        )
        candidate = (area, top)
        if best is None or candidate > best:
            best = candidate
    if best is not None:
        return float(best[1])
    if fallback_z_m is not None:
        return float(fallback_z_m)
    return float(near_z_m)
