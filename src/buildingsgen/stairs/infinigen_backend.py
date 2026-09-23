"""Optional Infinigen backend for detailed stair assets.

Infinigen (BSD-3-Clause, https://github.com/princeton-vl/infinigen) ships
staircase factories with materials, balusters and stringer profiles that this
repository does not attempt to reproduce.  None of its code is vendored here:
this module imports it at call time and subclasses its factories with two
corrections.  Install it separately (`pip install buildingsgen[stairs]` pulls
`bpy`; Infinigen itself follows its own install instructions).

--------------------------------------------------------------------------------
Correction 1 - tread tops on the riser grid
--------------------------------------------------------------------------------
The upstream factories place the *underside* of every tread plate at
``(i + 1) * step_height``.  The plate thickness therefore leaks into the first
rise: ground -> first tread measures ``step_height + tread_height`` while the
final tread -> upper-floor rise is short by the same amount.  At a 0.175 m riser
and a 0.0615 m plate the first rise is 0.228-0.242 m, over the 0.20 m support
step - the flight is unclimbable, and no render shows it.

``WalkingSurfaceTreadPlacementMixin`` shifts each tread object (including a
turning platform) down by its own thickness before export.  It changes tread
placement only; it is not the cruder downstream workaround of translating the
whole staircase, which fixes the first rise and breaks the last one.

--------------------------------------------------------------------------------
Correction 2 - the upper floor supplies the last tread
--------------------------------------------------------------------------------
The upstream factory authors one tread per riser, which is right for a
freestanding asset and wrong once the flight is integrated: tread ``n``
duplicates the upper slab.  ``UpperFloorLandingMixin`` keeps all ``n`` risers
and regenerates the asset with ``n - 1`` treads, recording the removed tread's
bounds so the deletion is auditable rather than implicit.

--------------------------------------------------------------------------------
Dimensions
--------------------------------------------------------------------------------
``configure_factory`` recomputes step count, riser, going, flight length,
landing index and rail stride *before* ``create_asset()`` is called, from a
:class:`~buildingsgen.stairs.spec.StairSpec`.  Object scale stays identity -
see the module docstring of ``spec.py`` for why scaling a stair is never the
answer.
"""

from __future__ import annotations

import math
from typing import Any

from .spec import StairSpec

__all__ = [
    "available",
    "build_factories",
    "configure_factory",
    "generate_stair_asset",
]


def available() -> bool:
    """True when Infinigen and Blender's ``bpy`` can both be imported."""

    try:  # pragma: no cover - depends on an optional heavy dependency
        import bpy  # noqa: F401
        import infinigen  # noqa: F401
    except Exception:
        return False
    return True


def _require() -> None:
    if not available():
        raise RuntimeError(
            "the Infinigen stair backend needs Infinigen and Blender's `bpy`.\n"
            "Use buildingsgen.stairs.asset.build_stair_usd for a dependency-light "
            "flight, or install Infinigen to get detailed assets."
        )


def build_factories() -> dict[str, type]:
    """Return corrected factory classes keyed by stair family.

    Built at call time so importing this module never pulls in Blender.
    """

    _require()
    import numpy as np
    from infinigen.assets.objects.elements.staircases.l_shaped import LShapedStaircaseFactory
    from infinigen.assets.objects.elements.staircases.straight import StraightStaircaseFactory
    from infinigen.assets.objects.elements.staircases.u_shaped import UShapedStaircaseFactory
    from infinigen.core.util import blender as butil

    class WalkingSurfaceTreadPlacementMixin:
        """Correction 1: place each tread *top* on the riser grid."""

        def make_treads(self):
            parts = super().make_treads()
            for tread in parts:
                tread.location[-1] -= float(self.tread_height)
                butil.apply_transform(tread, loc=True)
            return parts

    class UpperFloorLandingMixin:
        """Correction 2: drop tread n; the upper floor is tread n."""

        def make_treads(self):
            parts = super().make_treads()
            platform_count = int(
                isinstance(self, (LShapedStaircaseFactory, UShapedStaircaseFactory))
            )
            if len(parts) != self.n + platform_count:
                raise RuntimeError(
                    f"unexpected tread topology: {len(parts)} parts for n={self.n} "
                    f"and {platform_count} platform(s)"
                )
            top_index = self.n - 1
            top_tread = parts[top_index]
            top_tread.data.calc_loop_triangles()
            vertices = np.array(
                [top_tread.matrix_world @ vertex.co for vertex in top_tread.data.vertices]
            )
            self.upper_floor_integration_trace = {
                "removed_top_tread_vertex_count": int(len(top_tread.data.vertices)),
                "removed_top_tread_triangle_count": int(len(top_tread.data.loop_triangles)),
                "removed_top_tread_aabb_min_m": [float(v) for v in vertices.min(axis=0)],
                "removed_top_tread_aabb_max_m": [float(v) for v in vertices.max(axis=0)],
            }
            butil.delete(top_tread)
            return parts[:top_index] + parts[top_index + 1:]

    bases = {
        "straight": StraightStaircaseFactory,
        "l_shaped": LShapedStaircaseFactory,
        "u_shaped": UShapedStaircaseFactory,
    }
    corrected: dict[str, type] = {}
    for family, base in bases.items():
        corrected[family] = type(
            f"Integrated{base.__name__}",
            (UpperFloorLandingMixin, WalkingSurfaceTreadPlacementMixin, base),
            {},
        )
    return corrected


def configure_factory(factory: Any, spec: StairSpec) -> dict[str, Any]:
    """Impose ``spec`` on an Infinigen factory instance, in place.

    Returns a record of what changed, for the asset's evidence file.  Nothing
    is scaled: the factory regenerates at the final dimensions.
    """

    original = {
        "n": int(factory.n),
        "step_height": float(factory.step_height),
        "step_length": float(factory.step_length),
        "tread_length": float(factory.tread_length),
        "side_height": float(factory.side_height),
        "glass_margin": float(factory.glass_margin),
        "m": int(factory.m) if hasattr(factory, "m") else None,
    }
    nosing = original["tread_length"] - original["step_length"]
    side_ratio = original["side_height"] / original["step_height"]
    glass_extra = original["glass_margin"] - original["step_height"] / 2.0

    factory.n = spec.riser_count
    factory.step_height = spec.riser_m
    factory.step_length = spec.going_m
    factory.tread_length = spec.going_m + nosing
    factory.step_width = spec.width_m
    factory.side_height = spec.riser_m * side_ratio
    factory.glass_margin = spec.riser_m / 2.0 + glass_extra
    factory.post_k = int(math.ceil(factory.step_width / factory.step_length))
    factory.end_margin = factory.step_length * 8
    if spec.turn_after_riser is not None and hasattr(factory, "m"):
        factory.m = int(spec.turn_after_riser)
    # Normalised orientation keeps cross-family review and camera placement
    # repeatable; placement into the building applies the real pose.
    factory.mirror = False
    factory.rot_z = 0.0

    return {
        "original_factory_dimensions": original,
        "applied_spec": spec.as_dict(),
        "method": (
            "step count, riser, going, width, flight length, landing index and rail "
            "stride recomputed before create_asset(); object scale stays identity"
        ),
    }


def generate_stair_asset(spec: StairSpec, destination: Any, *, seed: int = 0) -> dict[str, Any]:
    """Generate one detailed stair with Infinigen and export it to USD.

    Raises if Infinigen is unavailable.  The export path and the exporter's own
    report are returned so an evidence record can cite them.
    """

    _require()
    from infinigen.core.sim.exporters import usd_exporter
    from infinigen.core.util.math import FixedSeed

    factories = build_factories()
    if spec.family not in factories:
        raise ValueError(f"no Infinigen factory for stair family {spec.family!r}")
    with FixedSeed(seed):
        factory = factories[spec.family](seed)
        adaptation = configure_factory(factory, spec)
        asset = factory.create_asset(i=seed)
        export = usd_exporter.export(asset, str(destination))
    return {
        "schema": "StairAssetV1",
        "backend": "buildingsgen.stairs.infinigen_backend",
        "stage": str(destination),
        "spec": spec.as_dict(),
        "seed": seed,
        "adaptation": adaptation,
        "upper_floor_supplies_last_tread": True,
        "infinigen_export": export,
    }
