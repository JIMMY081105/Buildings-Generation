"""Gate result type, and the rule that a gate must be able to fail.

Every gate answers three questions, and a result that answers only the first
is not an acceptance:

* **What was measured** - numbers with units, never "looks fine".
* **What would have failed** - at least one negative control that was actually
  run and that the gate confirms behaved as required.  A check whose author
  cannot name an input that breaks it is not testing anything.
* **What this does not support** - the conclusions a reader would otherwise
  draw for free.  Geometric reachability is not "a humanoid can walk here";
  saying so explicitly is part of the result, not politeness.

``status`` is fail-closed: anything that could not be measured is FAIL, not
SKIP.  SKIP exists only for gates whose *inputs* are legitimately absent (no
stair was placed, no control cache was kept).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Status = Literal["PASS", "FAIL", "SKIP"]


@dataclass
class NegativeControl:
    """A deliberately broken input and what the gate must say about it."""

    name: str
    description: str
    expectation: str
    ran: bool = False
    behaved_as_required: bool = False
    observed: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "expectation": self.expectation,
            "ran": self.ran,
            "behaved_as_required": self.behaved_as_required,
            "observed": self.observed,
        }


@dataclass
class GateResult:
    gate: str
    title: str
    status: Status = "FAIL"
    measurements: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    not_supported: list[str] = field(default_factory=list)
    controls: list[NegativeControl] = field(default_factory=list)
    error: str | None = None

    def fail(self, reason: str) -> GateResult:
        self.failures.append(reason)
        self.status = "FAIL"
        return self

    def finish(self) -> GateResult:
        """Set PASS only when nothing failed and every control behaved."""

        if self.status == "SKIP":
            return self
        control_failures = [
            control.name for control in self.controls
            if control.ran and not control.behaved_as_required
        ]
        for name in control_failures:
            self.failures.append(
                f"negative control {name!r} did not fail as required, so this gate "
                "cannot distinguish a good building from a broken one"
            )
        self.status = "PASS" if not self.failures else "FAIL"
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "title": self.title,
            "status": self.status,
            "measurements": self.measurements,
            "failures": self.failures,
            "not_supported": self.not_supported,
            "negative_controls": [control.as_dict() for control in self.controls],
            "error": self.error,
        }
