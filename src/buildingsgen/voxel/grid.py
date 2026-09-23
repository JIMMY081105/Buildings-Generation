"""Aligned voxel grid shared by every bake and every gate.

Derived from NVIDIA ProtoMotions ``scene_geometry_cache`` (Apache-2.0); see
NOTICE and docs/provenance.md.  Kept byte-compatible on purpose: a cache baked
here must load in the downstream simulator without translation.

Both voxel sizes are pinned.  A cache baked at another resolution is not
comparable with any accepted building, and the standability semantics below it
are defined in cells, not metres.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

FINE_VOXEL_SIZE_M = 0.05
COARSE_VOXEL_SIZE_M = 0.10
COARSE_FACTOR = int(round(COARSE_VOXEL_SIZE_M / FINE_VOXEL_SIZE_M))


def _tuple3(values: Sequence[float], name: str) -> tuple[float, float, float]:
    array = np.asarray(values, dtype=np.float64)
    if array.shape != (3,) or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain three finite values")
    return (float(array[0]), float(array[1]), float(array[2]))


def _shape3(values: Sequence[int], name: str) -> tuple[int, int, int]:
    array = np.asarray(values, dtype=np.int64)
    if array.shape != (3,) or np.any(array <= 0):
        raise ValueError(f"{name} must contain three positive integers")
    return (int(array[0]), int(array[1]), int(array[2]))


@dataclass(frozen=True)
class GridSpec:
    """A 5 cm grid whose extent is a whole number of 10 cm cells.

    ``scene_bounds_max_m`` is the real query boundary.  ``grid_bounds_max_m``
    may exceed it by up to one coarse cell so both tiers cover the same volume;
    that padding is **not** known free space and every consumer here masks it
    out through :meth:`real_cell_mask`.
    """

    scene_bounds_min_m: tuple[float, float, float]
    scene_bounds_max_m: tuple[float, float, float]
    grid_bounds_max_m: tuple[float, float, float]
    fine_shape: tuple[int, int, int]
    coarse_shape: tuple[int, int, int]
    fine_voxel_size_m: float = FINE_VOXEL_SIZE_M
    coarse_voxel_size_m: float = COARSE_VOXEL_SIZE_M
    axis_order: str = "xyz"
    sample_location: str = "cell_center"

    def validate(self) -> None:
        lower = _tuple3(self.scene_bounds_min_m, "scene_bounds_min_m")
        upper = _tuple3(self.scene_bounds_max_m, "scene_bounds_max_m")
        grid_upper = _tuple3(self.grid_bounds_max_m, "grid_bounds_max_m")
        fine_shape = _shape3(self.fine_shape, "fine_shape")
        coarse_shape = _shape3(self.coarse_shape, "coarse_shape")
        if any(end <= start for start, end in zip(lower, upper)):
            raise ValueError("scene bounds must have positive extent")
        if self.axis_order != "xyz" or self.sample_location != "cell_center":
            raise ValueError("cache grids use XYZ cell-center semantics")
        if not math.isclose(self.fine_voxel_size_m, FINE_VOXEL_SIZE_M):
            raise ValueError("fine voxel size is pinned at 5 cm")
        if not math.isclose(self.coarse_voxel_size_m, COARSE_VOXEL_SIZE_M):
            raise ValueError("coarse voxel size is pinned at 10 cm")
        if any(size % COARSE_FACTOR for size in fine_shape):
            raise ValueError("fine shape must align to complete 10 cm voxels")
        if tuple(size // COARSE_FACTOR for size in fine_shape) != coarse_shape:
            raise ValueError("coarse shape must be the 2x reduction of fine_shape")
        expected = tuple(
            start + size * FINE_VOXEL_SIZE_M for start, size in zip(lower, fine_shape)
        )
        if not np.allclose(grid_upper, expected, atol=1.0e-9):
            raise ValueError("grid_bounds_max_m does not match origin and fine_shape")
        if any(scene > grid + 1.0e-9 for scene, grid in zip(upper, grid_upper)):
            raise ValueError("grid bounds must contain the scene bounds")

    # -- derived quantities ------------------------------------------------

    @property
    def cell_area_m2(self) -> float:
        return self.fine_voxel_size_m * self.fine_voxel_size_m

    def axis_centers_m(self, axis: int) -> np.ndarray:
        origin = self.scene_bounds_min_m[axis]
        count = self.fine_shape[axis]
        return origin + (np.arange(count, dtype=np.float64) + 0.5) * self.fine_voxel_size_m

    def real_cell_mask(self) -> np.ndarray:
        """True where a fine cell centre lies inside the real scene bounds.

        The alignment padding is excluded.  Treating it as free space is how a
        building can appear to have an open exterior wall.
        """

        masks = []
        for axis in range(3):
            centers = self.axis_centers_m(axis)
            masks.append(
                (centers >= self.scene_bounds_min_m[axis])
                & (centers < self.scene_bounds_max_m[axis])
            )
        return masks[0][:, None, None] & masks[1][None, :, None] & masks[2][None, None, :]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_bounds_min_m": list(self.scene_bounds_min_m),
            "scene_bounds_max_m": list(self.scene_bounds_max_m),
            "grid_bounds_max_m": list(self.grid_bounds_max_m),
            "fine_shape": list(self.fine_shape),
            "coarse_shape": list(self.coarse_shape),
            "fine_voxel_size_m": self.fine_voxel_size_m,
            "coarse_voxel_size_m": self.coarse_voxel_size_m,
            "axis_order": self.axis_order,
            "sample_location": self.sample_location,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GridSpec:
        spec = cls(
            scene_bounds_min_m=_tuple3(payload["scene_bounds_min_m"], "scene_bounds_min_m"),
            scene_bounds_max_m=_tuple3(payload["scene_bounds_max_m"], "scene_bounds_max_m"),
            grid_bounds_max_m=_tuple3(payload["grid_bounds_max_m"], "grid_bounds_max_m"),
            fine_shape=_shape3(payload["fine_shape"], "fine_shape"),
            coarse_shape=_shape3(payload["coarse_shape"], "coarse_shape"),
            fine_voxel_size_m=float(payload.get("fine_voxel_size_m", FINE_VOXEL_SIZE_M)),
            coarse_voxel_size_m=float(payload.get("coarse_voxel_size_m", COARSE_VOXEL_SIZE_M)),
        )
        spec.validate()
        return spec


def make_aligned_grid(
    scene_bounds_min_m: Sequence[float],
    scene_bounds_max_m: Sequence[float],
) -> GridSpec:
    """Smallest 5 cm grid over the bounds that also tiles whole 10 cm cells."""

    lower = _tuple3(scene_bounds_min_m, "scene_bounds_min_m")
    upper = _tuple3(scene_bounds_max_m, "scene_bounds_max_m")
    if any(end <= start for start, end in zip(lower, upper)):
        raise ValueError("scene bounds must have positive extent")
    raw = tuple(
        int(math.ceil((end - start) / FINE_VOXEL_SIZE_M - 1.0e-12))
        for start, end in zip(lower, upper)
    )
    fine_shape = tuple(
        max(COARSE_FACTOR, size + (-size) % COARSE_FACTOR) for size in raw
    )
    coarse_shape = tuple(size // COARSE_FACTOR for size in fine_shape)
    grid_upper = tuple(
        start + size * FINE_VOXEL_SIZE_M for start, size in zip(lower, fine_shape)
    )
    spec = GridSpec(
        scene_bounds_min_m=lower,
        scene_bounds_max_m=upper,
        grid_bounds_max_m=grid_upper,  # type: ignore[arg-type]
        fine_shape=fine_shape,  # type: ignore[arg-type]
        coarse_shape=coarse_shape,  # type: ignore[arg-type]
    )
    spec.validate()
    return spec
