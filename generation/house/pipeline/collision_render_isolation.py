#!/usr/bin/env python3
"""Session-layer isolation contract for collision-evidence rendering."""

from __future__ import annotations

from pxr import Usd, UsdGeom, UsdLux


ALLOWED_NEUTRAL_LIGHT_PATHS = (
    "/Rev/Dome",
    "/Rev/Key",
    "/Rev/Fill",
)


def _is_light(prim: Usd.Prim) -> bool:
    return bool(prim and prim.IsValid() and prim.HasAPI(UsdLux.LightAPI))


def _texture_value(prim: Usd.Prim) -> str | None:
    attribute = prim.GetAttribute("inputs:texture:file")
    if not attribute or not attribute.HasValue():
        return None
    value = attribute.Get()
    text = str(value) if value is not None else ""
    return text or None


def _light_record(prim: Usd.Prim) -> dict:
    light = UsdLux.LightAPI(prim)
    imageable = UsdGeom.Imageable(prim)
    intensity = light.GetIntensityAttr().Get()
    color = light.GetColorAttr().Get()
    return {
        "path": str(prim.GetPath()),
        "type": prim.GetTypeName(),
        "active": bool(prim.IsActive()),
        "computed_visibility": str(imageable.ComputeVisibility()),
        "intensity": float(intensity) if intensity is not None else None,
        "color": [float(component) for component in color] if color is not None else None,
        "texture_file": _texture_value(prim),
    }


def enumerate_lights(stage: Usd.Stage) -> list[Usd.Prim]:
    """Enumerate every composed active light by schema, not by hardcoded path."""
    return [prim for prim in stage.Traverse() if _is_light(prim)]


def isolate_collision_render(stage: Usd.Stage) -> dict:
    """Mute all non-/Rev lights and explicitly hide every PathViz imageable."""
    muted = []
    for prim in enumerate_lights(stage):
        path = str(prim.GetPath())
        if path in ALLOWED_NEUTRAL_LIGHT_PATHS:
            continue
        before = _light_record(prim)
        UsdGeom.Imageable(prim).MakeInvisible()
        UsdLux.LightAPI(prim).GetIntensityAttr().Set(0.0)
        muted.append({"before": before, "after": _light_record(prim)})

    hidden_pathviz = []
    root = stage.GetPrimAtPath("/PathViz")
    if root and root.IsValid():
        for prim in Usd.PrimRange(root):
            imageable = UsdGeom.Imageable(prim)
            if not imageable:
                continue
            imageable.MakeInvisible()
            hidden_pathviz.append({
                "path": str(prim.GetPath()),
                "type": prim.GetTypeName(),
                "computed_visibility_after": str(imageable.ComputeVisibility()),
            })
    return {
        "enumeration": "stage.Traverse + UsdLux.LightAPI; no hardcoded source paths",
        "muted_source_lights": muted,
        "hidden_pathviz_imageables": hidden_pathviz,
    }


def assert_collision_render_isolated(stage: Usd.Stage) -> dict:
    """Fail unless only untextured neutral /Rev lights and no PathViz are visible."""
    light_records = [_light_record(prim) for prim in enumerate_lights(stage)]
    enabled = [
        record for record in light_records
        if record["active"]
        and record["computed_visibility"] != str(UsdGeom.Tokens.invisible)
        and (record["intensity"] is None or record["intensity"] > 0.0)
    ]
    enabled_paths = {record["path"] for record in enabled}
    expected_paths = set(ALLOWED_NEUTRAL_LIGHT_PATHS)
    unexpected = sorted(enabled_paths - expected_paths)
    missing = sorted(expected_paths - enabled_paths)
    nonneutral = []
    textured = []
    for record in enabled:
        color = record["color"]
        if color is None or max(color) - min(color) > 1e-7:
            nonneutral.append(record["path"])
        if record["texture_file"]:
            textured.append(record["path"])

    visible_pathviz = []
    pathviz_root = stage.GetPrimAtPath("/PathViz")
    if pathviz_root and pathviz_root.IsValid():
        for prim in Usd.PrimRange(pathviz_root):
            imageable = UsdGeom.Imageable(prim)
            if imageable and imageable.ComputeVisibility() != UsdGeom.Tokens.invisible:
                visible_pathviz.append(str(prim.GetPath()))

    snapshot = {
        "status": "pass",
        "enabled_light_paths": sorted(enabled_paths),
        "expected_neutral_light_paths": sorted(expected_paths),
        "unexpected_enabled_light_paths": unexpected,
        "missing_neutral_light_paths": missing,
        "nonneutral_enabled_light_paths": sorted(nonneutral),
        "textured_enabled_light_paths": sorted(textured),
        "visible_pathviz_paths": sorted(visible_pathviz),
        "all_light_records": light_records,
    }
    failures = []
    if unexpected:
        failures.append("unexpected enabled lights: " + ", ".join(unexpected))
    if missing:
        failures.append("missing neutral lights: " + ", ".join(missing))
    if nonneutral:
        failures.append("enabled non-neutral lights: " + ", ".join(nonneutral))
    if textured:
        failures.append("enabled textured lights: " + ", ".join(textured))
    if visible_pathviz:
        failures.append("visible PathViz prims: " + ", ".join(visible_pathviz))
    if failures:
        snapshot["status"] = "fail"
        raise RuntimeError("collision render isolation failed: " + "; ".join(failures))
    return snapshot
