from __future__ import annotations

import json
from pathlib import Path
import unittest

from gate25_storey_navigation import run_fixture


HERE = Path(__file__).resolve().parent


class Gate25FixtureTests(unittest.TestCase):
    def test_sealed_closet_is_red_despite_large_total_floor(self) -> None:
        report = run_fixture("sealed_closet")
        gate = report["G25"]
        self.assertTrue(report["fixture_expectation_met"])
        self.assertEqual(gate["status"], "FAIL")
        self.assertGreater(gate["walkable_area_m2"], 15.0)
        self.assertLess(gate["area_reachable_from_stair_arrival_m2"], 15.0)
        self.assertGreaterEqual(gate["connected_region_count"], 2)
        self.assertFalse(gate["fragmentation_is_failure"])

    def test_same_fixture_with_door_is_green(self) -> None:
        report = run_fixture("open_door_control")
        gate = report["G25"]
        self.assertTrue(report["fixture_expectation_met"])
        self.assertEqual(gate["status"], "PASS")
        self.assertGreaterEqual(
            gate["area_reachable_from_stair_arrival_m2"], 15.0)

    def test_retained_fixture_evidence_matches_current_code(self) -> None:
        paths = {
            "sealed_closet": "g25_sealed_closet_negative_control_r4.json",
            "open_door_control": "g25_open_door_positive_control_r4.json",
        }
        for fixture, filename in paths.items():
            retained = json.loads(
                (HERE / "evidence" / filename).read_text(encoding="utf-8"))
            self.assertEqual(retained, run_fixture(fixture))


if __name__ == "__main__":
    unittest.main()
