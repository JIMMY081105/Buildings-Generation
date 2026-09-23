"""Building-type profiles.

The pipeline is one algorithm.  What changes between a house, a factory, a
school and whatever comes next is a *profile*: stair design range, storey
geometry expectations, prim-naming hints and the acceptance thresholds that
are genuinely type-dependent.

Two things are deliberately **not** profile-settable:

* ``buildingsgen.voxel.standability.StandabilityV1`` - the body proxy
  (0.25 m radius, 1.60 m height, 0.20 m support step, 1.00 m^2 minimum
  component, 5 cm voxel).  A humanoid does not change size because the
  building is a school.  Attempting to override it raises.
* The voxel grid (5 cm fine / 10 cm coarse).  Caches from different profiles
  must stay comparable.

A profile may only *tighten* a threshold it shares with ``generic``; loosening
one has to be an explicit, reviewed edit of ``generic.yaml`` itself.  See
``docs/building-types.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

import yaml

#: Shipped profiles live inside the package, so they survive a non-editable
#: install.  Point ``--config-root`` at your own directory to override or add
#: types without touching the package.
BUILDING_TYPE_ROOT = Path(__file__).resolve().parent / "profiles"

BASE_PROFILE_NAME = "generic"

#: Stair families this repository knows how to synthesise and to measure.
STAIR_FAMILIES = ("straight", "l_shaped", "u_shaped")


class ProfileError(ValueError):
    """A building-type profile is missing, malformed or internally impossible."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProfileError(message)


def _positive(value: float, name: str) -> float:
    value = float(value)
    _require(math.isfinite(value) and value > 0.0, f"{name} must be finite and positive")
    return value


@dataclass(frozen=True)
class StairDesign:
    """The design envelope a stair for this building type must land inside.

    ``preferred_riser_m`` only seeds the search: the riser count is chosen so
    the *measured* storey height divides into risers inside
    ``[min_riser_m, max_riser_m]``.  Nothing is scaled after generation -
    scaling a stair asset silently breaks the riser/going relation, which is
    the single most common way a "generated" stair becomes unclimbable.
    """

    families: tuple[str, ...] = STAIR_FAMILIES
    preferred_riser_m: float = 0.175
    min_riser_m: float = 0.150
    max_riser_m: float = 0.190
    min_going_m: float = 0.26
    max_going_m: float = 0.30
    blondel_target_m: float = 0.63
    width_m: float = 1.00
    min_width_m: float = 0.90
    handrail: str = "single"
    even_risers_for_u_shaped: bool = True

    def validate(self) -> None:
        _require(bool(self.families), "stair.families must not be empty")
        unknown = sorted(set(self.families) - set(STAIR_FAMILIES))
        _require(not unknown, f"unknown stair families {unknown}; known: {list(STAIR_FAMILIES)}")
        _positive(self.preferred_riser_m, "stair.preferred_riser_m")
        _require(
            0.0 < self.min_riser_m <= self.preferred_riser_m <= self.max_riser_m,
            "stair risers must satisfy 0 < min <= preferred <= max",
        )
        _require(
            0.0 < self.min_going_m <= self.max_going_m,
            "stair goings must satisfy 0 < min <= max",
        )
        _positive(self.blondel_target_m, "stair.blondel_target_m")
        _require(
            self.min_width_m <= self.width_m,
            "stair.min_width_m must not exceed stair.width_m",
        )
        _require(
            self.handrail in ("none", "single", "double"),
            "stair.handrail must be none, single or double",
        )
        # A design range that cannot satisfy Blondel at its own preferred riser
        # is a configuration bug, not a runtime surprise.
        going = self.blondel_target_m - 2.0 * self.preferred_riser_m
        _require(
            self.min_going_m - 1e-9 <= going <= self.max_going_m + 1e-9,
            f"Blondel going {going:.3f} m at the preferred riser falls outside "
            f"[{self.min_going_m}, {self.max_going_m}]",
        )


@dataclass(frozen=True)
class StoreyModel:
    """How storeys are recognised and what counts as a plausible one.

    Detection is geometric (see ``buildingsgen.usd.storeys``).  ``name_hints``
    are *hints only*: they are used to label detected bands and to break ties,
    never to decide how many storeys exist.  A corpus that names its roots
    ``/World/Level_00`` must not need a code change.
    """

    min_storey_height_m: float = 2.70
    max_storey_height_m: float = 6.00
    max_slab_thickness_m: float = 0.60
    min_storey_floor_area_m2: float = 8.0
    name_hints: tuple[str, ...] = ("floor", "storey", "story", "level")
    expected_storey_count: int | None = None

    def validate(self) -> None:
        _positive(self.min_storey_height_m, "storeys.min_storey_height_m")
        _require(
            self.min_storey_height_m < self.max_storey_height_m,
            "storeys.min_storey_height_m must be below max_storey_height_m",
        )
        _positive(self.max_slab_thickness_m, "storeys.max_slab_thickness_m")
        _positive(self.min_storey_floor_area_m2, "storeys.min_storey_floor_area_m2")
        if self.expected_storey_count is not None:
            _require(
                self.expected_storey_count >= 2,
                "storeys.expected_storey_count must be >= 2; a single storey needs no stair",
            )


@dataclass(frozen=True)
class AcceptanceThresholds:
    """Type-dependent gate thresholds.

    These are the numbers a reviewer is allowed to argue about.  The pinned
    body proxy is not among them.
    """

    #: G7/G25 - standable area reachable from the stair arrival, per storey.
    min_reachable_area_per_storey_m2: float = 15.0
    #: G7/G25 - a standable island the stair cannot reach, larger than this,
    #: is a routing failure rather than a harmless dead corner.
    max_unreachable_standable_component_m2: float = 4.0
    #: G3b - clear width a body of the pinned radius needs through a doorway.
    min_doorway_clear_width_m: float = 0.60
    #: G5 - measured route length divided by straight-line distance.
    max_geodesic_to_euclidean_ratio: float = 2.0
    #: G4 - fraction of the stair run that must have full body headroom.
    min_top_clearance_coverage: float = 0.90
    #: G3 - USD <-> voxel precision and recall must both reach this.
    min_voxel_agreement: float = 1.0

    def validate(self) -> None:
        _positive(
            self.min_reachable_area_per_storey_m2,
            "acceptance.min_reachable_area_per_storey_m2",
        )
        _positive(
            self.max_unreachable_standable_component_m2,
            "acceptance.max_unreachable_standable_component_m2",
        )
        _positive(self.min_doorway_clear_width_m, "acceptance.min_doorway_clear_width_m")
        _require(
            self.max_geodesic_to_euclidean_ratio >= 1.0,
            "acceptance.max_geodesic_to_euclidean_ratio cannot be below 1.0",
        )
        for name in ("min_top_clearance_coverage", "min_voxel_agreement"):
            value = float(getattr(self, name))
            _require(0.0 < value <= 1.0, f"acceptance.{name} must lie in (0, 1]")

    def tightens_over(self, base: AcceptanceThresholds) -> list[str]:
        """Return the fields where ``self`` is *looser* than ``base``.

        Empty list means the profile only tightened, which is what the loader
        enforces.  Direction is per-field: minima may only rise, maxima may
        only fall.
        """

        looser: list[str] = []
        for name in ("min_reachable_area_per_storey_m2", "min_doorway_clear_width_m",
                     "min_top_clearance_coverage", "min_voxel_agreement"):
            if getattr(self, name) < getattr(base, name) - 1e-12:
                looser.append(name)
        for name in ("max_unreachable_standable_component_m2", "max_geodesic_to_euclidean_ratio"):
            if getattr(self, name) > getattr(base, name) + 1e-12:
                looser.append(name)
        return looser


@dataclass(frozen=True)
class BuildingProfile:
    """Everything type-specific, in one immutable object."""

    name: str
    description: str = ""
    extends: str | None = None
    stair: StairDesign = field(default_factory=StairDesign)
    storeys: StoreyModel = field(default_factory=StoreyModel)
    acceptance: AcceptanceThresholds = field(default_factory=AcceptanceThresholds)
    #: Free-form provenance: where this building type's source scenes come from.
    source_corpus: str = ""

    def validate(self) -> None:
        _require(bool(self.name), "profile name must not be empty")
        self.stair.validate()
        self.storeys.validate()
        self.acceptance.validate()

    def as_dict(self) -> dict[str, Any]:
        """Plain-data form, for embedding in an evidence record."""

        def unpack(value: Any) -> Any:
            if hasattr(value, "__dataclass_fields__"):
                return {f.name: unpack(getattr(value, f.name)) for f in fields(value)}
            if isinstance(value, tuple):
                return list(value)
            return value

        return unpack(self)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """One-level-deep merge; profile sections override key by key."""

    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _section(payload: dict[str, Any], key: str, cls: type) -> Any:
    raw = payload.get(key, {})
    _require(isinstance(raw, dict), f"profile section {key!r} must be a mapping")
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(raw) - known)
    _require(not unknown, f"unknown key(s) {unknown} in profile section {key!r}")
    kwargs = dict(raw)
    for name, value in list(kwargs.items()):
        if isinstance(value, list):
            kwargs[name] = tuple(value)
    return cls(**kwargs)


def _load_payload(name: str, root: Path, seen: tuple[str, ...] = ()) -> dict[str, Any]:
    _require(name not in seen, f"profile inheritance cycle: {' -> '.join((*seen, name))}")
    path = root / f"{name}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in root.glob("*.yaml"))
        raise ProfileError(f"no building type {name!r} in {root}; available: {available}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _require(isinstance(payload, dict), f"{path} must contain a mapping")
    payload.setdefault("name", name)
    _require(
        payload["name"] == name,
        f"{path} declares name {payload['name']!r} but is filed as {name!r}",
    )
    parent = payload.get("extends")
    if parent is None:
        return payload
    base = _load_payload(str(parent), root, (*seen, name))
    merged = _merge(base, payload)
    merged["name"] = name
    merged["extends"] = parent
    return merged


def load_profile(name: str, root: Path | None = None) -> BuildingProfile:
    """Load one building-type profile, resolving ``extends``.

    Raises :class:`ProfileError` for an unknown type, an unknown key, an
    inheritance cycle, an impossible design envelope, or a threshold that is
    looser than ``generic``.
    """

    root = Path(root) if root is not None else BUILDING_TYPE_ROOT
    payload = _load_payload(name, root)
    profile = BuildingProfile(
        name=str(payload["name"]),
        description=str(payload.get("description", "")),
        extends=payload.get("extends"),
        stair=_section(payload, "stair", StairDesign),
        storeys=_section(payload, "storeys", StoreyModel),
        acceptance=_section(payload, "acceptance", AcceptanceThresholds),
        source_corpus=str(payload.get("source_corpus", "")),
    )
    profile.validate()
    if name != BASE_PROFILE_NAME:
        base = load_profile(BASE_PROFILE_NAME, root)
        looser = profile.acceptance.tightens_over(base.acceptance)
        _require(
            not looser,
            f"profile {name!r} loosens {looser} relative to {BASE_PROFILE_NAME!r}; "
            "a threshold may only be tightened by a profile",
        )
    return profile


def available_profiles(root: Path | None = None) -> list[str]:
    root = Path(root) if root is not None else BUILDING_TYPE_ROOT
    return sorted(path.stem for path in root.glob("*.yaml"))


def override_profile(profile: BuildingProfile, **sections: Any) -> BuildingProfile:
    """Return a validated copy with whole sections replaced (used by the CLI)."""

    updated = replace(profile, **sections)
    updated.validate()
    return updated
