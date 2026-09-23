"""Run the gates in order and write one evidence record.

Fail-closed and ordered: the first FAIL stops the run unless ``keep_going`` is
set.  Carrying a known-broken building through the remaining gates produces a
report full of numbers measured on geometry that was already wrong, and those
numbers get quoted later.
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import GateResult
from .context import AcceptanceContext
from .gates import ALL_GATES


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_acceptance(
    context: AcceptanceContext,
    *,
    gates=ALL_GATES,
    keep_going: bool = False,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Run every gate and return the acceptance record."""

    results: list[GateResult] = []
    for gate in gates:
        try:
            result = gate(context)
        except Exception as error:  # noqa: BLE001 - a crashed gate is a failed gate
            result = GateResult(
                gate=getattr(gate, "__name__", "unknown"),
                title="gate raised",
                status="FAIL",
                error=f"{type(error).__name__}: {error}",
            )
            result.failures.append("the gate raised before it could measure anything")
        results.append(result)
        if result.status == "FAIL" and not keep_going:
            break

    statuses = [result.status for result in results]
    overall = (
        "FAIL" if "FAIL" in statuses
        else "PARTIAL" if "SKIP" in statuses
        else "PASS"
    )
    record: dict[str, Any] = {
        "schema": "BuildingAcceptanceV1",
        "status": overall,
        "generated_at_utc": utc_now(),
        "building": context.as_dict(),
        "profile": context.profile.as_dict(),
        "gates_run": len(results),
        "gates_total": len(gates),
        "stopped_early": len(results) < len(gates),
        "results": [result.as_dict() for result in results],
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "scope": (
            "Geometric acceptance only. No contact dynamics, no balance, no actuation. "
            "A PASS says the building is not disqualified by its geometry; it does not "
            "say a humanoid policy can traverse it."
        ),
    }
    if report_path is not None:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def summarise(record: dict[str, Any]) -> str:
    """A markdown table, including the column readers otherwise fill in themselves."""

    lines = [
        f"## Acceptance: {record['building']['name']}  **{record['status']}**",
        "",
        f"- building type: `{record['building']['building_type']}`",
        f"- stage: `{record['building']['stage']}`",
        f"- cache: `{record['building']['cache']}`",
        f"- generated: {record['generated_at_utc']}",
        "",
        "| Gate | Result | Key measurement | Controls | Does not support |",
        "|---|---|---|---|---|",
    ]
    for result in record["results"]:
        controls = result["negative_controls"]
        ran = sum(1 for control in controls if control["ran"])
        good = sum(1 for control in controls if control["ran"] and control["behaved_as_required"])
        control_cell = f"{good}/{ran} failed as required" if ran else "none"
        key = _key_measurement(result)
        not_supported = "; ".join(result["not_supported"]) or "-"
        lines.append(
            f"| {result['gate']} {result['title']} | {result['status']} | {key} | "
            f"{control_cell} | {not_supported} |"
        )
    failures = [
        f"- **{result['gate']}**: {failure}"
        for result in record["results"]
        for failure in result["failures"]
    ]
    if failures:
        lines += ["", "### Failures", *failures]
    lines += ["", "### Scope", record["scope"]]
    return "\n".join(lines)


def _key_measurement(result: dict[str, Any]) -> str:
    measurements = result.get("measurements") or {}
    for key in (
        "collider_count", "occupied_voxels", "recall_surface_points_occupied",
    ):
        if key in measurements:
            return f"{key}={measurements[key]}"
    if "authored" in measurements:
        authored = measurements["authored"]
        return (
            f"max rise {authored.get('max_rise_m')} m "
            f"(limit {authored.get('limit_m')}), over={authored.get('rises_over_limit')}"
        )
    if "per_storey" in measurements:
        return ", ".join(
            f"{entry['label']} {entry['reachable_from_stair_m2']} m2"
            for entry in measurements["per_storey"]
        )
    if "upper_storey_reached_area_m2" in measurements:
        return f"upper reached {measurements['upper_storey_reached_area_m2']} m2"
    if "storeys" in measurements:
        return f"{measurements['storeys']['storey_count']} storeys"
    return result.get("error") or "-"
