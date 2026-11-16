"""OWN source support for future RTX capture; importing/CLI never starts Kit.

No PXR, torch, Isaac or network import. ROOT may call apply_l1 only from its
authorized renderer after checking the installed settings registry and leases.
"""
import hashlib
import json
import math
from pathlib import Path


def file_ref(path):
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": h.hexdigest()}


def verify_texture_input(row):
    if not row.get("license") or not row.get("source_url"):
        raise ValueError("Texture/HDRI provenance and license required")
    if file_ref(row["file_ref"]["path"]) != row["file_ref"]:
        raise ValueError("Texture/HDRI full source reference differs")
    return row


def requested_settings(profile):
    """No operator enum guessed: installed registry source must supply it."""
    if type(profile.get("tone_mapping_operator_value")) is not int:
        raise ValueError("Installed tone-mapping operator enum is not bound")
    if not profile.get("installed_operator_mapping_source_ref"):
        raise ValueError("Installed operator mapping source evidence is required")
    p = profile["path_tracing"]
    e = profile["fixed_exposure"]
    return {
        "/rtx/rendermode": "PathTracing",
        "/rtx/pathtracing/spp": p["spp_per_update"],
        "/rtx/pathtracing/totalSpp": p["total_spp"],
        "/rtx/pathtracing/maxBounces": p["max_bounces"],
        "/rtx/pathtracing/optixDenoiser/enabled": p["denoise"],
        "/rtx/post/histogram/enabled": False,
        "/rtx/post/tonemap/op": profile["tone_mapping_operator_value"],
        "/rtx/post/tonemap/filmIso": e["film_iso"],
        "/rtx/post/tonemap/cameraShutter": e["camera_shutter_registry_value"],
        "/rtx/post/tonemap/fNumber": e["f_number"],
        "/rtx/post/tonemap/cm2Factor": e["cm2_factor"],
    }


def apply_l1(settings, profile):
    """Runtime-only injected Carb object; reject absent installed keys first.

    Readback supports configuration evidence, not renderer convergence or V1–V5
    PASS. Each capture must additionally retain actual mode/sample/camera/AOV
    evidence and must use the same profile SHA for the whole building.
    """
    expected = requested_settings(profile)
    old = {key: settings.get(key) for key in expected}
    absent = [key for key, value in old.items() if value is None]
    if absent:
        raise ValueError("Installed setting keys absent: " + repr(absent))
    mapping = profile["installed_operator_mapping_source_ref"]
    if file_ref(mapping["path"]) != mapping:
        raise ValueError("Installed settings mapping source changed")
    for key, value in expected.items():
        settings.set(key, value)
    actual = {key: settings.get(key) for key in expected}
    if actual != expected:
        raise ValueError("RTX settings readback differs from registered request")
    return {"requested": expected, "before": old, "actual_readback": actual,
            "visual_quality_pass": "NOT_EXECUTED"}


def rect_light_plan(column_positions, roof_height_m, floor2_height_m,
                    under_mezzanine_xy_membership):
    """Plan lights at centres of the actual AUTHORING column bays.

    Membership callback must come from actual authored slab triangles, including
    holes. It is a placement query only; no slab or collision geometry is filled.
    Intensity/exposure are rendering inputs and never scientific thresholds.
    """
    xs = sorted({float(p[0]) for p in column_positions})
    ys = sorted({float(p[1]) for p in column_positions})
    if len(xs) < 2 or len(ys) < 2:
        raise ValueError("Actual column grid missing")
    rows = []
    for ix, (x0, x1) in enumerate(zip(xs, xs[1:])):
        for iy, (y0, y1) in enumerate(zip(ys, ys[1:])):
            x, y = (x0+x1)/2, (y0+y1)/2
            rows.append({"id": f"high_{ix}_{iy}", "type": "RectLight",
                         "xyz_m": [x, y, roof_height_m-0.8],
                         "width_m": 2.4, "height_m": 1.2,
                         "intensity": 1800.0, "exposure": 0.0,
                         "normal_direction": [0, 0, -1]})
            if under_mezzanine_xy_membership(x, y):
                rows.append({"id": f"under_{ix}_{iy}", "type": "RectLight",
                             "xyz_m": [x, y, floor2_height_m-0.6],
                             "width_m": 2.0, "height_m": 1.0,
                             "intensity": 1200.0, "exposure": 0.0,
                             "normal_direction": [0, 0, -1]})
    return rows


def segment_hits_aabb(eye, target, lower, upper):
    """Exact segment/slab intersection on provided measured boxes."""
    t0, t1 = 0.0, 1.0
    for axis in range(3):
        delta = target[axis]-eye[axis]
        if abs(delta) < 1e-12:
            if eye[axis] < lower[axis] or eye[axis] > upper[axis]:
                return False
            continue
        a, b = (lower[axis]-eye[axis])/delta, (upper[axis]-eye[axis])/delta
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return False
    return True


def rank_camera_candidates(candidates, actual_column_boxes):
    """Geometric proposal only; image-depth/instance-AOV evidence still needed."""
    allowed = []
    for candidate in candidates:
        eye, target = candidate["eye"], candidate["target"]
        blocked = any(segment_hits_aabb(eye, target, box["min"], box["max"])
                      for box in actual_column_boxes)
        if not blocked:
            allowed.append({**candidate,
                "camera_column_centre_ray_check": "PROPOSAL_CLEAR",
                "actual_subject_image_fraction": None,
                "required_subject_fraction": [0.30, 0.60],
                "visual_quality_pass": "NOT_EXECUTED"})
    if not allowed:
        raise ValueError("No preregistered unobstructed candidate; no pillar hidden")
    return allowed


def check_captured_subject_fraction(actual_subject_pixels, total_pixels):
    """30–60% comes from the user; requires measured instance-mask counts."""
    if type(actual_subject_pixels) is not int or type(total_pixels) is not int:
        raise ValueError("Actual subject/total pixel counts must be integers")
    if total_pixels <= 0 or not 0 <= actual_subject_pixels <= total_pixels:
        raise ValueError("Invalid measured subject mask")
    fraction = actual_subject_pixels/total_pixels
    return {"actual_subject_fraction": fraction,
            "camera_coverage_status": "PASS" if .30 <= fraction <= .60 else "FAIL",
            "cannot_support": "Coverage does not establish V1–V5, photo realism, gates or READY."}


if __name__ == "__main__":
    print(json.dumps({"status": "SOURCE_SUPPORT_ONLY_NO_RENDER_ENTRY",
                      "SDK_PXR_GPU_science_executed": False}))
