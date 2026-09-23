"""Shared, lazily computed inputs for the gates.

Every gate reads the *same* stage, the same cache and the same support field.
Re-deriving standability inside a gate is how two gates end up disagreeing
about which cells are walkable while both report PASS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
from pxr import Usd

from ..config import BuildingProfile
from ..usd.colliders import ColliderRecord, collect_colliders
from ..usd.stage import open_stage
from ..usd.storeys import StoreyDetection, detect_storeys, name_hint_labels
from ..voxel.cache import LoadedCache, load_cache
from ..voxel.grid import COARSE_FACTOR, make_aligned_grid
from ..voxel.standability import StandabilityV1, StandableVolume, derive_standable


@dataclass
class AcceptanceContext:
    """One building under review."""

    name: str
    stage_path: Path
    cache_path: Path
    profile: BuildingProfile
    #: Flat simulator terrain height.  ``None`` means the building authors its
    #: own lowest slab; if it does not, every storey check below will correctly
    #: report an unreachable ground floor.
    ground_plane_z_m: float | None = 0.0
    #: Prim path prefix of the stair, when the pipeline placed one.
    stair_prim_path: str | None = None
    config: StandabilityV1 = field(default_factory=StandabilityV1)

    @cached_property
    def stage(self) -> Usd.Stage:
        return open_stage(self.stage_path)

    @cached_property
    def colliders(self) -> tuple[list[ColliderRecord], np.ndarray, np.ndarray]:
        return collect_colliders(self.stage)

    @cached_property
    def cache(self) -> LoadedCache:
        return load_cache(self.cache_path, verify_hashes=True)

    @cached_property
    def occupancy(self) -> np.ndarray:
        return np.asarray(self.cache.occupancy, dtype=bool)

    @cached_property
    def standable(self) -> StandableVolume:
        return derive_standable(
            self.occupancy,
            self.cache.grid,
            self.config,
            ground_plane_z_m=self.ground_plane_z_m,
        )

    @cached_property
    def storeys(self) -> StoreyDetection:
        return detect_storeys(
            self.standable,
            self.profile.storeys,
            labels=name_hint_labels(self.stage, self.profile.storeys),
        )

    def standable_with_ground(self, ground_plane_z_m: float | None) -> StandableVolume:
        """Re-derive standability at a different terrain height.

        Used by Gate 6's control sweep: lowering the terrain raises the first
        rise by the same amount and must eventually break the climb.  When the
        requested terrain sits below the baked grid, the grid is extended
        downwards with free rows first - otherwise the plane is not
        representable at all, the ground vanishes instead of dropping, and the
        control measures nothing.
        """

        grid = self.cache.grid
        occupancy = self.occupancy
        if ground_plane_z_m is not None and ground_plane_z_m < grid.scene_bounds_min_m[2]:
            voxel = grid.fine_voxel_size_m
            deficit = grid.scene_bounds_min_m[2] - ground_plane_z_m
            pad = int(np.ceil(deficit / voxel)) + 1
            pad += (-pad) % COARSE_FACTOR  # keep whole 10 cm cells
            lower = list(grid.scene_bounds_min_m)
            lower[2] -= pad * voxel
            grid = make_aligned_grid(lower, grid.scene_bounds_max_m)
            extra = grid.fine_shape[2] - occupancy.shape[2]
            occupancy = np.concatenate(
                [np.zeros(occupancy.shape[:2] + (extra,), dtype=bool), occupancy], axis=2
            )
        return derive_standable(
            occupancy,
            grid,
            self.config,
            ground_plane_z_m=ground_plane_z_m,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stage": str(self.stage_path),
            "cache": str(self.cache_path),
            "building_type": self.profile.name,
            "ground_plane_z_m": self.ground_plane_z_m,
            "stair_prim_path": self.stair_prim_path,
        }
