"""Design a stair for a measured storey height.

Two rules decide everything here, and both exist because their absence produced
stairs that looked right and could not be climbed:

1. **Fit the risers to the measured height; never scale the asset.**  Scaling a
   generated flight to a new storey height changes the riser and the going by
   the same factor, which walks straight out of the comfort envelope and, at
   the top end, past the 0.20 m support step the body proxy will accept.  The
   riser count is searched instead, and the asset is generated at its final
   size.

2. **The upper floor provides the last tread.**  A freestanding flight authors
   one tread per riser.  Integrated into a building, tread *n* duplicates the
   upper slab: two walking surfaces a plate-thickness apart, which reads to a
   voxel bake as a step of the wrong height right at the arrival.  So the
   design carries ``n`` risers and ``n - 1`` authored treads, and the last rise
   is measured against the slab.

The going follows the Blondel comfort relation ``2 * riser + going = target``,
clamped into the profile's range.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..config import StairDesign


class StairDesignError(ValueError):
    """No stair inside the profile's envelope fits this storey height."""


@dataclass(frozen=True)
class StairSpec:
    """A fully determined flight, in metres."""

    family: str
    total_rise_m: float
    riser_count: int
    riser_m: float
    going_m: float
    width_m: float
    handrail: str
    #: Riser index of the turn platform for L- and U-shaped flights.
    turn_after_riser: int | None = None
    #: Authored treads.  One fewer than the risers: the upper slab is tread n.
    authored_tread_count: int = 0

    @property
    def flight_run_m(self) -> float:
        """Horizontal run if the flight were straight."""

        return self.riser_count * self.going_m

    @property
    def blondel_m(self) -> float:
        return 2.0 * self.riser_m + self.going_m

    def footprint_m(self) -> tuple[float, float]:
        """Approximate XY footprint (length, width) including the turn."""

        if self.family == "straight" or self.turn_after_riser is None:
            return (self.flight_run_m, self.width_m)
        first = self.turn_after_riser * self.going_m
        second = (self.riser_count - self.turn_after_riser) * self.going_m
        if self.family == "u_shaped":
            return (max(first, second) + self.going_m, 2.0 * self.width_m)
        return (first + self.width_m, second + self.width_m)

    def expected_rises_m(self) -> list[float]:
        """Ground -> tread 1, tread -> tread, last tread -> upper slab.

        Every entry equals the riser when the flight is authored correctly.
        The point of listing them is that Gate 6 measures the same three
        categories from geometry and can disagree.
        """

        return [self.riser_m] * self.riser_count

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "total_rise_m": round(self.total_rise_m, 4),
            "riser_count": self.riser_count,
            "riser_m": round(self.riser_m, 4),
            "going_m": round(self.going_m, 4),
            "width_m": round(self.width_m, 4),
            "handrail": self.handrail,
            "turn_after_riser": self.turn_after_riser,
            "authored_tread_count": self.authored_tread_count,
            "flight_run_m": round(self.flight_run_m, 4),
            "blondel_m": round(self.blondel_m, 4),
            "footprint_m": [round(value, 4) for value in self.footprint_m()],
        }


def _riser_counts(total_rise_m: float, design: StairDesign, family: str) -> list[int]:
    """Candidate riser counts, nearest-to-preferred first."""

    preferred = max(2, int(round(total_rise_m / design.preferred_riser_m)))
    lowest = max(2, int(math.ceil(total_rise_m / design.max_riser_m - 1e-9)))
    highest = max(lowest, int(math.floor(total_rise_m / design.min_riser_m + 1e-9)))
    candidates = list(range(lowest, highest + 1))
    if family == "u_shaped" and design.even_risers_for_u_shaped:
        candidates = [n for n in candidates if n % 2 == 0]
    candidates.sort(key=lambda n: (abs(n - preferred), n))
    return candidates


def _turn_after(family: str, riser_count: int) -> int | None:
    if family == "straight":
        return None
    if family == "u_shaped":
        return riser_count // 2
    # L-shaped: turn near the middle, but keep at least two risers each side.
    return min(riser_count - 2, max(2, int(round(riser_count * 0.5))))


def design_stair(
    total_rise_m: float,
    design: StairDesign,
    *,
    family: str | None = None,
    riser_count: int | None = None,
    going_m: float | None = None,
    width_m: float | None = None,
) -> StairSpec:
    """Pick the flight for one storey-to-storey rise.

    ``family`` defaults to the profile's first listed family.  Explicit
    ``riser_count``/``going_m``/``width_m`` are honoured but still validated -
    an override that leaves the envelope raises rather than producing an asset
    nothing downstream will accept.
    """

    design.validate()
    if not math.isfinite(total_rise_m) or total_rise_m <= 0.0:
        raise StairDesignError("total_rise_m must be finite and positive")
    family = family or design.families[0]
    if family not in design.families:
        raise StairDesignError(
            f"family {family!r} is not enabled for this building type; "
            f"enabled: {list(design.families)}"
        )

    if riser_count is not None:
        if riser_count < 2:
            raise StairDesignError("riser_count must be at least 2")
        if family == "u_shaped" and design.even_risers_for_u_shaped and riser_count % 2:
            raise StairDesignError("a U-shaped flight needs an even riser count")
        candidates = [riser_count]
    else:
        candidates = _riser_counts(total_rise_m, design, family)
    if not candidates:
        raise StairDesignError(
            f"no riser count puts a {family} flight of {total_rise_m:.3f} m inside "
            f"[{design.min_riser_m}, {design.max_riser_m}] m per riser"
        )

    chosen_n = candidates[0]
    riser = total_rise_m / chosen_n
    if not (design.min_riser_m - 1e-9 <= riser <= design.max_riser_m + 1e-9):
        raise StairDesignError(
            f"{family} flight of {total_rise_m:.3f} m over {chosen_n} risers gives "
            f"{riser:.4f} m per riser, outside [{design.min_riser_m}, {design.max_riser_m}]"
        )

    if going_m is None:
        blondel = design.blondel_target_m - 2.0 * riser
        going = min(design.max_going_m, max(design.min_going_m, blondel))
    else:
        going = float(going_m)
        if not (design.min_going_m - 1e-9 <= going <= design.max_going_m + 1e-9):
            raise StairDesignError(
                f"going {going:.4f} m is outside [{design.min_going_m}, {design.max_going_m}]"
            )

    width = design.width_m if width_m is None else float(width_m)
    if width < design.min_width_m - 1e-9:
        raise StairDesignError(
            f"width {width:.4f} m is below the profile minimum {design.min_width_m} m"
        )

    return StairSpec(
        family=family,
        total_rise_m=float(total_rise_m),
        riser_count=int(chosen_n),
        riser_m=float(riser),
        going_m=float(going),
        width_m=float(width),
        handrail=design.handrail,
        turn_after_riser=_turn_after(family, int(chosen_n)),
        authored_tread_count=int(chosen_n) - 1,
    )


def traversal_path(spec: StairSpec) -> list[tuple[float, float, float]]:
    """Walking centreline from the lower floor to the upper landing, in metres.

    Local frame: the flight starts at the origin and climbs +Y.  The last point
    is the centre of tread ``n`` - the one the upper floor provides, not an
    authored tread - so a navigation graph can join the stair to the upper
    storey without guessing where the flight ends.
    """

    going, riser, width = spec.going_m, spec.riser_m, spec.width_m
    points: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
    turn = spec.turn_after_riser

    if spec.family == "straight" or turn is None:
        for index in range(1, spec.riser_count + 1):
            points.append((0.0, (index - 0.5) * going, index * riser))
        return points

    for index in range(1, turn + 1):
        points.append((0.0, (index - 0.5) * going, index * riser))
    landing_y = turn * going + 0.5 * width
    points.append((0.0, landing_y, turn * riser))

    if spec.family == "u_shaped":
        # Switchback: descend in -Y one flight-width across.
        points.append((-width, landing_y, turn * riser))
        for step, index in enumerate(range(turn + 1, spec.riser_count + 1), start=1):
            points.append((-width, landing_y - step * going, index * riser))
        return points

    # L-shaped: turn 90 degrees into -X.
    points.append((-0.5 * width, landing_y, turn * riser))
    for step, index in enumerate(range(turn + 1, spec.riser_count + 1), start=1):
        points.append((-0.5 * width - step * going, landing_y, index * riser))
    last_x = -0.5 * width - (spec.riser_count - turn) * going
    points.append((last_x - 0.5 * going, landing_y, spec.riser_count * riser))
    return points
