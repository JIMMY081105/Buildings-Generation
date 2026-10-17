"""End-to-end runs on synthetic buildings, including ones that must fail.

Marked ``slow``: each run bakes a few million voxels.  Opt in with
``pytest --runslow``.  They are the only tests that exercise scan -> connect ->
bake -> accept as one thing, so CI runs them even though a unit sweep does not.
"""

from __future__ import annotations

import json

import pytest

from buildingsgen.cli import DEMO_DEFECTS, main
from buildingsgen.config import load_profile
from buildingsgen.fixtures.synthetic import build_synthetic_building
from buildingsgen.pipeline import Workspace, connect_storeys, run_all, scan_building
from buildingsgen.usd.stage import source_ref

pytestmark = pytest.mark.slow

HOUSE = dict(storeys=2, storey_height_m=3.0, interior_m=(8.0, 10.0), opening_m=(1.6, 5.2))


def _build(tmp_path, name="b", **overrides):
    settings = dict(HOUSE)
    settings.update(overrides)
    path = tmp_path / f"{name}.usda"
    info = build_synthetic_building(path, **settings)
    return path, info


def test_clean_building_passes_every_gate(tmp_path):
    source, _ = _build(tmp_path)
    workspace = Workspace(tmp_path / "out", "b")
    record = run_all(source, workspace, load_profile("house"), keep_going=True)

    acceptance = record["acceptance"]
    assert acceptance["status"] == "PASS", [
        failure for result in acceptance["results"] for failure in result["failures"]
    ]
    assert acceptance["gates_run"] == acceptance["gates_total"]

    # A PASS is worth nothing unless the controls actually fired.
    for result in acceptance["results"]:
        for control in result["negative_controls"]:
            assert control["ran"], f"{result['gate']} control {control['name']} never ran"
            assert control["behaved_as_required"], (
                f"{result['gate']} control {control['name']} did not fail as required"
            )


def test_the_source_building_is_never_modified(tmp_path):
    source, _ = _build(tmp_path)
    before = source_ref(source)
    workspace = Workspace(tmp_path / "out", "b")
    record = connect_storeys(source, workspace, load_profile("house"))
    after = source_ref(source)
    assert after.sha256 == before.sha256
    assert record["source_unmodified"] is True
    # The stair lives in an overlay that sublayers the untouched source.
    assert workspace.stage("connected").is_file()


def test_storeys_are_found_geometrically_not_by_prim_name(tmp_path):
    """Rename the storey roots to something no hint matches; nothing changes."""

    standard, _ = _build(tmp_path, name="standard")
    renamed, _ = _build(tmp_path, name="renamed", storey_prefix="Deck")
    profile = load_profile("house")
    a = scan_building(standard, profile)
    b = scan_building(renamed, profile)
    assert a["storeys"]["storey_count"] == b["storeys"]["storey_count"] == 2
    assert [s["floor_z_m"] for s in a["storeys"]["storeys"]] == [
        s["floor_z_m"] for s in b["storeys"]["storeys"]
    ]


def test_a_sealed_slab_cannot_be_connected(tmp_path):
    """No opening means no shaft, and the search must say so rather than guess."""

    source, _ = _build(tmp_path, defect="sealed_opening")
    workspace = Workspace(tmp_path / "out", "sealed")
    with pytest.raises(RuntimeError) as error:
        connect_storeys(source, workspace, load_profile("house"))
    assert "clear" in str(error.value) or "storey" in str(error.value)


def test_a_1_2_m_ceiling_is_not_a_storey(tmp_path):
    """Standable by a point, not by a 1.60 m body."""

    source, _ = _build(tmp_path, defect="low_headroom")
    record = scan_building(source, load_profile("house"))
    assert record["storeys"]["storey_count"] == 1
    assert record["needs_stair"] is False


def test_a_narrow_slot_does_not_make_a_storey_reachable(tmp_path):
    """The upper storey is split by a 0.30 m slot; half of it stays unreachable."""

    source, _ = _build(tmp_path, defect="narrow_slot")
    workspace = Workspace(tmp_path / "out", "slot")
    record = run_all(source, workspace, load_profile("house"), keep_going=True)
    acceptance = record["acceptance"]
    assert acceptance["status"] == "FAIL"
    navigation = next(r for r in acceptance["results"] if r["gate"] == "G7")
    assert navigation["status"] == "FAIL"
    assert any("cannot reach" in failure for failure in navigation["failures"])


def test_a_factory_profile_runs_on_a_factory_shaped_building(tmp_path):
    """Same pipeline, taller storeys, wider flight, stricter thresholds."""

    source, _ = _build(
        tmp_path,
        name="plant",
        storey_height_m=4.0,
        interior_m=(12.0, 14.0),
        opening_m=(3.0, 8.0),
    )
    profile = load_profile("factory")
    workspace = Workspace(tmp_path / "out", "plant")
    record = run_all(source, workspace, profile, keep_going=True)

    connection = record["connect"]["connections"][0]
    assert connection["spec"]["family"] in profile.stair.families
    assert connection["spec"]["width_m"] >= profile.stair.min_width_m
    assert connection["rise_m"] == pytest.approx(4.0, abs=0.06)
    assert record["acceptance"]["status"] == "PASS", [
        failure
        for result in record["acceptance"]["results"]
        for failure in result["failures"]
    ]


def _demo_acceptance(out):
    return json.loads((out / "evidence" / "acceptance.json").read_text(encoding="utf-8"))


def test_the_demo_passes_on_the_clean_house(tmp_path):
    assert main(["demo", "--out", str(tmp_path)]) == 0
    acceptance = _demo_acceptance(tmp_path / "clean")
    assert acceptance["status"] == "PASS"
    assert acceptance["gates_run"] == acceptance["gates_total"]


@pytest.mark.parametrize("defect", DEMO_DEFECTS)
def test_the_demo_rejects_every_known_defect(tmp_path, capsys, defect):
    """Every defect the fixtures can inject must make the demo exit non-zero."""

    assert main(["demo", "--out", str(tmp_path), "--defect", defect]) == 1
    printed = capsys.readouterr().out
    evidence = tmp_path / defect / "evidence" / "acceptance.json"
    if evidence.is_file():
        # Reached the gates: one of them must have said no.
        assert _demo_acceptance(tmp_path / defect)["status"] == "FAIL"
    else:
        # Refused earlier, by scan or connect, and said why.
        assert "REJECTED before acceptance" in printed
