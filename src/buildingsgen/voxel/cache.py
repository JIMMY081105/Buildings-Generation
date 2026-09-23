"""The ``.scene_geometry_v1`` cache: write once, verify on read.

Layout and bit conventions follow NVIDIA ProtoMotions ``scene_geometry_cache``
(Apache-2.0) so a cache written here loads unchanged downstream; see NOTICE and
docs/provenance.md.

    <name>.scene_geometry_v1/
        manifest.json              grid, policy, per-array sha256
        occupancy_5cm_bits.npy     packbits(C-order, little)
        signed_clearance_5cm.npy   float32
        signed_clearance_10cm.npy  float32
        COMMITTED.json             written last; its absence means "torn"

A cache is immutable.  ``write_cache`` refuses to overwrite: re-baking means
removing the directory, which keeps "which cache produced this number" answerable
after the fact.  The commit marker is written last so an interrupted bake is
detectable rather than silently half-valid.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .grid import COARSE_FACTOR, GridSpec

CACHE_SUFFIX = ".scene_geometry_v1"
MANIFEST_FILENAME = "manifest.json"
COMMITTED_FILENAME = "COMMITTED.json"
OCCUPANCY_FILENAME = "occupancy_5cm_bits.npy"
FINE_CLEARANCE_FILENAME = "signed_clearance_5cm.npy"
COARSE_CLEARANCE_FILENAME = "signed_clearance_10cm.npy"

SCHEMA = "SceneGeometryCacheV1"


class CacheError(ValueError):
    """A cache is missing, torn, or does not match its own manifest."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _array_record(path: Path, array: np.ndarray) -> dict[str, Any]:
    return {
        "name": path.name,
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


@dataclass(frozen=True)
class LoadedCache:
    path: Path
    grid: GridSpec
    occupancy: np.ndarray
    manifest: dict[str, Any]

    @property
    def collision_policy(self) -> str:
        return str(self.manifest.get("collision", {}).get("policy", ""))


def write_cache(
    cache_path: Path,
    grid: GridSpec,
    occupancy: np.ndarray,
    fine_clearance: np.ndarray,
    coarse_clearance: np.ndarray,
    *,
    collision_policy: str,
    source: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically publish one cache directory and return its manifest."""

    cache_path = Path(cache_path)
    if cache_path.exists():
        raise CacheError(f"cache is immutable, remove it first: {cache_path}")
    grid.validate()
    occupancy = np.ascontiguousarray(occupancy, dtype=bool)
    if occupancy.shape != tuple(grid.fine_shape):
        raise CacheError("occupancy shape does not match grid.fine_shape")
    if fine_clearance.shape != tuple(grid.fine_shape):
        raise CacheError("fine clearance shape does not match grid.fine_shape")
    if coarse_clearance.shape != tuple(grid.coarse_shape):
        raise CacheError("coarse clearance shape does not match grid.coarse_shape")
    for name, array in (("fine", fine_clearance), ("coarse", coarse_clearance)):
        if not np.all(np.isfinite(array)):
            raise CacheError(f"{name} clearance contains NaN or Inf")

    packed = np.packbits(occupancy.reshape(-1), bitorder="little")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{cache_path.name}.tmp-", dir=cache_path.parent))
    try:
        occupancy_path = staging / OCCUPANCY_FILENAME
        fine_path = staging / FINE_CLEARANCE_FILENAME
        coarse_path = staging / COARSE_CLEARANCE_FILENAME
        np.save(occupancy_path, packed, allow_pickle=False)
        np.save(
            fine_path, np.ascontiguousarray(fine_clearance, dtype=np.float32),
            allow_pickle=False,
        )
        np.save(
            coarse_path, np.ascontiguousarray(coarse_clearance, dtype=np.float32),
            allow_pickle=False,
        )

        manifest: dict[str, Any] = {
            "schema": SCHEMA,
            "grid": grid.as_dict(),
            "occupancy": {
                "bit_order": "little",
                "flatten_order": "C",
                "logical_shape": list(grid.fine_shape),
                "logical_voxels": int(occupancy.size),
                "occupied_voxels": int(occupancy.sum()),
            },
            "collision": {"policy": collision_policy},
            "source": source,
            "arrays": {
                "occupancy_bits": _array_record(occupancy_path, packed),
                "signed_clearance_fine": _array_record(fine_path, fine_clearance),
                "signed_clearance_coarse": _array_record(coarse_path, coarse_clearance),
            },
        }
        if extra:
            manifest.update(extra)
        manifest_path = staging / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        committed = {
            "schema": "SceneGeometryCacheCommitV1",
            "files": [
                {"name": MANIFEST_FILENAME, "sha256": sha256_file(manifest_path),
                 "size": manifest_path.stat().st_size},
                manifest["arrays"]["occupancy_bits"],
                manifest["arrays"]["signed_clearance_fine"],
                manifest["arrays"]["signed_clearance_coarse"],
            ],
        }
        # Written last: an interrupted bake leaves no marker, and load_cache
        # refuses such a directory instead of quietly trusting partial arrays.
        (staging / COMMITTED_FILENAME).write_text(
            json.dumps(committed, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        staging.replace(cache_path)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def load_cache(cache_path: Path, *, verify_hashes: bool = True) -> LoadedCache:
    """Open a committed cache, optionally re-hashing every array."""

    cache_path = Path(cache_path)
    if not cache_path.is_dir():
        raise CacheError(f"no cache directory at {cache_path}")
    marker = cache_path / COMMITTED_FILENAME
    if not marker.is_file():
        raise CacheError(f"cache has no {COMMITTED_FILENAME}; it is torn or still being written")
    manifest_path = cache_path / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise CacheError(f"cache has no {MANIFEST_FILENAME}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise CacheError(f"unexpected cache schema {manifest.get('schema')!r}")
    grid = GridSpec.from_dict(manifest["grid"])

    if verify_hashes:
        committed = json.loads(marker.read_text(encoding="utf-8"))
        for record in committed["files"]:
            path = cache_path / record["name"]
            if not path.is_file():
                raise CacheError(f"cache is missing {record['name']}")
            if sha256_file(path) != record["sha256"]:
                raise CacheError(f"cache file {record['name']} does not match its committed sha256")

    logical = math.prod(grid.fine_shape)
    packed = np.load(cache_path / OCCUPANCY_FILENAME, allow_pickle=False)
    occupancy = (
        np.unpackbits(np.asarray(packed), count=logical, bitorder="little")
        .reshape(grid.fine_shape)
        .astype(np.bool_)
    )
    if int(occupancy.sum()) != manifest["occupancy"]["occupied_voxels"]:
        raise CacheError("cache occupancy count does not match its manifest")
    occupancy.setflags(write=False)
    return LoadedCache(path=cache_path, grid=grid, occupancy=occupancy, manifest=manifest)


def load_clearance(cache_path: Path, *, coarse: bool = False) -> np.ndarray:
    name = COARSE_CLEARANCE_FILENAME if coarse else FINE_CLEARANCE_FILENAME
    return np.load(Path(cache_path) / name, allow_pickle=False)


def coarse_shape_of(fine_shape: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(size // COARSE_FACTOR for size in fine_shape)  # type: ignore[return-value]
