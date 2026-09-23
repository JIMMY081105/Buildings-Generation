"""Grid alignment, conservative splatting, and cache integrity."""

from __future__ import annotations

import json

import numpy as np
import pytest

from buildingsgen.voxel.cache import CacheError, load_cache, write_cache
from buildingsgen.voxel.grid import COARSE_FACTOR, GridSpec, make_aligned_grid
from buildingsgen.voxel.rasterize import (
    conservative_splat,
    downsample_max,
    is_watertight,
    rasterize_into,
    signed_clearance,
)

VOXEL = 0.05


def _unit_cube(offset=(0.0, 0.0, 0.0)):
    corners = np.array(
        [
            (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
            (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
        ],
        dtype=np.float64,
    ) + np.asarray(offset, dtype=np.float64)
    faces = np.array(
        [
            (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        ],
        dtype=np.int64,
    )
    return corners, faces


def test_grid_tiles_whole_coarse_cells():
    grid = make_aligned_grid((0.0, 0.0, 0.0), (1.03, 2.0, 0.4))
    grid.validate()
    assert all(size % COARSE_FACTOR == 0 for size in grid.fine_shape)
    assert grid.coarse_shape == tuple(size // COARSE_FACTOR for size in grid.fine_shape)
    # The grid must cover the scene, never clip it.
    assert all(
        upper >= scene - 1e-9
        for upper, scene in zip(grid.grid_bounds_max_m, grid.scene_bounds_max_m)
    )


def test_grid_rejects_degenerate_bounds():
    with pytest.raises(ValueError, match="positive extent"):
        make_aligned_grid((0.0, 0.0, 0.0), (0.0, 1.0, 1.0))


def test_real_cell_mask_excludes_alignment_padding():
    grid = make_aligned_grid((0.0, 0.0, 0.0), (1.03, 1.0, 1.0))
    mask = grid.real_cell_mask()
    assert mask.shape == tuple(grid.fine_shape)
    assert not mask.all(), "a padded grid must mark its padding as unreal"
    assert mask.any()


def test_grid_roundtrips_through_its_dict():
    grid = make_aligned_grid((-1.0, 0.5, 0.0), (2.0, 3.0, 4.0))
    assert GridSpec.from_dict(grid.as_dict()) == grid


def test_splat_marks_every_overlapped_cell():
    occupancy = np.zeros((4, 4, 4), dtype=bool)
    # A source cube centred on a cell corner overlaps eight target cells.
    marked = conservative_splat(
        occupancy, np.array([[0.10, 0.10, 0.10]]), grid_origin_m=(0.0, 0.0, 0.0), voxel_size_m=0.05
    )
    assert marked == 1
    assert occupancy.sum() == 8


def test_splat_reports_zero_for_a_mesh_outside_the_grid():
    occupancy = np.zeros((4, 4, 4), dtype=bool)
    marked = conservative_splat(
        occupancy, np.array([[99.0, 99.0, 99.0]]), grid_origin_m=(0.0, 0.0, 0.0)
    )
    assert marked == 0
    assert not occupancy.any()


def test_watertight_meshes_are_detected_and_filled():
    vertices, faces = _unit_cube()
    assert is_watertight(vertices, faces)
    grid = make_aligned_grid((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    solid = np.zeros(grid.fine_shape, dtype=bool)
    hollow = np.zeros(grid.fine_shape, dtype=bool)
    rasterize_into(solid, grid, vertices, faces, fill=True)
    rasterize_into(hollow, grid, vertices, faces, fill=False)
    assert solid.sum() > hollow.sum(), "filling a closed box must add its interior"
    centre = tuple(size // 2 for size in grid.fine_shape)
    assert solid[centre] and not hollow[centre]


def test_rasterizing_outside_the_grid_raises():
    grid = make_aligned_grid((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    occupancy = np.zeros(grid.fine_shape, dtype=bool)
    vertices, faces = _unit_cube(offset=(50.0, 50.0, 50.0))
    with pytest.raises(RuntimeError, match="outside the cache grid"):
        rasterize_into(occupancy, grid, vertices, faces, fill=False)


def test_clearance_is_signed_and_coarse_tier_is_pessimistic():
    occupancy = np.zeros((8, 8, 8), dtype=bool)
    occupancy[0, 0, 0] = True
    clearance = signed_clearance(occupancy, VOXEL)
    assert clearance[0, 0, 0] < 0.0
    assert clearance[7, 7, 7] > 0.0
    coarse = downsample_max(clearance, COARSE_FACTOR)
    assert coarse.shape == (4, 4, 4)
    assert coarse[0, 0, 0] <= clearance[0:2, 0:2, 0:2].min() + 1e-6


def _write_small_cache(path):
    grid = make_aligned_grid((0.0, 0.0, 0.0), (0.2, 0.2, 0.2))
    occupancy = np.zeros(grid.fine_shape, dtype=bool)
    occupancy[0, 0, 0] = True
    fine = signed_clearance(occupancy, VOXEL)
    coarse = downsample_max(fine, COARSE_FACTOR)
    manifest = write_cache(
        path, grid, occupancy, fine, coarse,
        collision_policy="Exact bake: test fixture",
        source={"stage": "synthetic"},
    )
    return grid, occupancy, manifest


def test_cache_roundtrips(tmp_path):
    grid, occupancy, _ = _write_small_cache(tmp_path / "c.scene_geometry_v1")
    loaded = load_cache(tmp_path / "c.scene_geometry_v1")
    assert loaded.grid == grid
    assert np.array_equal(np.asarray(loaded.occupancy), occupancy)
    assert "Exact bake" in loaded.collision_policy


def test_cache_is_immutable(tmp_path):
    _write_small_cache(tmp_path / "c.scene_geometry_v1")
    with pytest.raises(CacheError, match="immutable"):
        _write_small_cache(tmp_path / "c.scene_geometry_v1")


def test_tampered_manifest_is_rejected(tmp_path):
    path = tmp_path / "c.scene_geometry_v1"
    _write_small_cache(path)
    manifest = path / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["occupancy"]["occupied_voxels"] += 1
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(CacheError):
        load_cache(path)


def test_uncommitted_cache_is_rejected(tmp_path):
    path = tmp_path / "c.scene_geometry_v1"
    _write_small_cache(path)
    (path / "COMMITTED.json").unlink()
    with pytest.raises(CacheError, match="torn"):
        load_cache(path)
