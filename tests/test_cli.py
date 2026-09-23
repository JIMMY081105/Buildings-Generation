"""The command line, on the parts that do not need a building."""

from __future__ import annotations

import json

import pytest
from pxr import Usd, UsdGeom

from buildingsgen.cli import main


def test_types_lists_every_profile(capsys):
    assert main(["types"]) == 0
    printed = capsys.readouterr().out
    for name in ("generic", "house", "factory", "school", "warehouse", "office"):
        assert name in printed


def test_stairs_prints_a_spec(capsys):
    assert main(["stairs", "--rise", "3.0", "--type", "house"]) == 0
    printed = capsys.readouterr().out
    spec = json.loads(printed[printed.index("{"): printed.rindex("}") + 1])
    assert spec["riser_m"] <= 0.20
    assert spec["authored_tread_count"] == spec["riser_count"] - 1


def test_stairs_writes_an_asset(tmp_path, capsys):
    destination = tmp_path / "stair.usda"
    assert main(["stairs", "--rise", "3.2", "--type", "factory", "--out", str(destination)]) == 0
    assert destination.is_file()
    assert (tmp_path / "traversal_path.json").is_file()
    stage = Usd.Stage.Open(str(destination))
    assert str(UsdGeom.GetStageUpAxis(stage)) == "Z"
    assert "treads" in capsys.readouterr().out


def test_an_unknown_type_fails_loudly():
    with pytest.raises(Exception, match="available"):
        main(["stairs", "--rise", "3.0", "--type", "space-station"])


def test_a_family_the_profile_disables_fails_loudly():
    with pytest.raises(Exception, match="not enabled"):
        main(["stairs", "--rise", "3.5", "--type", "factory", "--family", "l_shaped"])
