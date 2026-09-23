"""Profiles load, and malformed ones are rejected rather than half-applied."""

from __future__ import annotations

import pytest
import yaml

from buildingsgen.config import (
    BASE_PROFILE_NAME,
    AcceptanceThresholds,
    ProfileError,
    StairDesign,
    available_profiles,
    load_profile,
)


def test_every_shipped_profile_loads_and_validates():
    names = available_profiles()
    assert BASE_PROFILE_NAME in names
    for name in names:
        profile = load_profile(name)
        assert profile.name == name
        profile.validate()


def test_profiles_cover_more_than_one_building_type():
    names = set(available_profiles())
    # The whole point of the profile layer: the pipeline is not house-only.
    assert {"house", "factory", "school"} <= names


def test_unknown_type_names_what_is_available():
    with pytest.raises(ProfileError, match="available"):
        load_profile("submarine")


def test_unknown_key_is_rejected(tmp_path):
    (tmp_path / "generic.yaml").write_text(
        yaml.safe_dump(load_profile(BASE_PROFILE_NAME).as_dict()), encoding="utf-8"
    )
    (tmp_path / "odd.yaml").write_text(
        "name: odd\nextends: generic\nstair:\n  numer_of_steps: 12\n", encoding="utf-8"
    )
    with pytest.raises(ProfileError, match="unknown key"):
        load_profile("odd", tmp_path)


def test_inheritance_cycle_is_rejected(tmp_path):
    (tmp_path / "a.yaml").write_text("name: a\nextends: b\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("name: b\nextends: a\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="cycle"):
        load_profile("a", tmp_path)


def test_a_profile_may_not_loosen_a_threshold(tmp_path):
    base = load_profile(BASE_PROFILE_NAME).as_dict()
    (tmp_path / "generic.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
    (tmp_path / "lax.yaml").write_text(
        "name: lax\nextends: generic\nacceptance:\n"
        "  min_reachable_area_per_storey_m2: 1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="loosens"):
        load_profile("lax", tmp_path)


def test_a_profile_may_tighten_a_threshold(tmp_path):
    base = load_profile(BASE_PROFILE_NAME).as_dict()
    (tmp_path / "generic.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
    (tmp_path / "strict.yaml").write_text(
        "name: strict\nextends: generic\nacceptance:\n"
        "  min_reachable_area_per_storey_m2: 40.0\n",
        encoding="utf-8",
    )
    assert load_profile("strict", tmp_path).acceptance.min_reachable_area_per_storey_m2 == 40.0


def test_impossible_stair_envelope_is_rejected():
    # Blondel at the preferred riser must land inside the going range, or no
    # storey height can ever produce a compliant flight.
    design = StairDesign(preferred_riser_m=0.175, min_going_m=0.10, max_going_m=0.12)
    with pytest.raises(ProfileError, match="Blondel"):
        design.validate()


def test_thresholds_direction_is_per_field():
    base = AcceptanceThresholds()
    tighter = AcceptanceThresholds(
        min_reachable_area_per_storey_m2=30.0,
        max_unreachable_standable_component_m2=2.0,
    )
    assert tighter.tightens_over(base) == []
    looser = AcceptanceThresholds(max_geodesic_to_euclidean_ratio=5.0)
    assert "max_geodesic_to_euclidean_ratio" in looser.tightens_over(base)
