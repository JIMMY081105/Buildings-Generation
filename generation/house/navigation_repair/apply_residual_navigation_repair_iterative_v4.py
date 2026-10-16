#!/usr/bin/env python3
"""Apply one evidence-bound residual navigation repair iteration to a USDZ.

The input minimum-cut report must have been produced from the exact input USDZ.
Only oversized inaccessible components with a verified collider-only cut are used.
For furniture/fixtures, the complete authored object root is removed.  For a wall
mesh, all visual and collision siblings of the same logical wall stem are removed,
which creates the missing architectural opening in both rendered and physical
geometry.  A later iteration may add a distinct support scope when a fresh
minimum-cut report proves another floor-support gap.  Formal gate code and
thresholds are never changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import tempfile
from typing import Any

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils

from gate25_storey_navigation import MAX_UNREACHABLE_STANDABLE_COMPONENT_M2
from repair_navigation_colliders import (
    _enabled_collider_count,
    _payload_manifest_from_files,
    _safe_usdz_member_names,
    _sha256,
    _verify_aligned_usdz,
    _visual_geometry_hash,
    _write_aligned_usdz,
)


WALL_ROOT_RE = re.compile(
    r"^(?P<stem>wall_[0-9]+_[0-9]+)_(?P<role>visual|collision)_[0-9]+$"
)
SUPPORT_REPAIR_COLLIDER_RE = re.compile(
    r"^(?P<prefix>/World/NavigationRepairSupports(?:Iter[0-9]+)?/"
    r"Floor[12]_[0-9]+)_collision$"
)
STOREYS = {"Floor1", "Floor2"}
FORBIDDEN_OBJECT_ROOT_RE = re.compile(
    r"^(?:doorway_|doorframe_|parc_storage_crate_)|"
    r"(?:fridge|refrigerator|microwave|oven|dishwasher|"
    r"washing.?machine|dryer)", re.I
)


def _exclusive_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _load_authorized_cuts(
    path: Path, source: Path, building: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != "Factory10StrictDoorScopeMinimumCutV1":
        raise ValueError("unexpected minimum-cut evidence schema")
    if not (
        document.get("door_counterfactual_uses_only_leaf_handle_below_doorway_roots")
        is True
        and document.get("frames_and_jambs_retained") is True
        and document.get("appliance_doors_excluded") is True
    ):
        raise ValueError("minimum-cut evidence violates the strict door scope")
    if document.get("delivered_usdz_modified") is not False:
        raise ValueError("minimum-cut diagnosis was not read-only")
    rows = [
        row for row in document.get("buildings", [])
        if row.get("building") == building
    ]
    if {row.get("storey") for row in rows} != STOREYS or len(rows) != 2:
        raise ValueError("minimum-cut evidence must contain exactly both storeys")
    source_hash = _sha256(source)
    selected: list[dict[str, Any]] = []
    for row in rows:
        if Path(row["source_usdz"]).resolve() != source:
            raise ValueError("minimum-cut evidence source path differs from input")
        if row["source_usdz_sha256"] != source_hash:
            raise ValueError("minimum-cut evidence source hash differs from input")
        for cut in row.get("remaining_component_minimum_cuts", []):
            area = float(cut["original_component_area_m2"])
            if area <= MAX_UNREACHABLE_STANDABLE_COMPONENT_M2 + 1.0e-9:
                continue
            verification = cut.get("verification", {})
            post_missing = verification.get("post_cut_path_missing_support_cells", [])
            collider_only = (
                cut.get("classification") == "COLLIDER_CUT_VERIFIED"
                and verification.get("cut_reconnects_target") is True
            )
            support_repair = (
                cut.get("classification")
                == "CUT_COLLIDERS_IDENTIFIED_BUT_FLOOR_SUPPORT_OR_STEP_EDIT_ALSO_REQUIRED"
                and verification.get("cut_reconnects_target") is False
                and len(post_missing) > 0
                and int(verification.get(
                    "post_cut_path_missing_support_cell_count", -1
                )) == len(post_missing)
                and int(verification.get(
                    "post_cut_path_step_over_limit_edge_count", -1
                )) == 0
            )
            if (
                not (collider_only or support_repair)
                or int(cut.get("minimum_missing_support_cell_count", -1)) != 0
                or int(cut.get("minimum_step_over_limit_edge_count", -1)) != 0
            ):
                raise ValueError(
                    f"oversized component lacks verified collider-only cut: "
                    f"{row['storey']} label={cut.get('original_component_label')}"
                )
            if not cut.get("collider_prims"):
                raise ValueError("verified oversized cut has no collider attribution")
            selected.append({
                "storey": row["storey"],
                "component_label": cut["original_component_label"],
                "component_area_m2": area,
                "minimum_blocked_body_center_cell_count": cut[
                    "minimum_blocked_body_center_cell_count"
                ],
                "collider_prims": cut["collider_prims"],
                "support_repair_cells": post_missing if support_repair else [],
            })
    if not selected:
        raise ValueError("no oversized verified residual component cuts selected")
    return selected, document


def _repair_targets(
    stage: Usd.Stage, cuts: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, Any]]]:
    exact_roots: set[str] = set()
    wall_stems: set[tuple[str, str]] = set()
    component_rows = []
    for cut in cuts:
        storey = cut["storey"]
        attributed = set()
        for collider in cut["collider_prims"]:
            object_root = collider.get("object_root", "")
            if not object_root:
                prim_path = str(collider.get("prim_path", ""))
                support_match = SUPPORT_REPAIR_COLLIDER_RE.match(prim_path)
                if support_match:
                    siblings = {
                        support_match.group("prefix") + "_collision",
                        support_match.group("prefix") + "_visual",
                    }
                    exact_roots.update(siblings)
                    attributed.update(siblings)
                    continue
                if prim_path.startswith(
                    "/World/StairObstacles/world/collision/geom_0_col"
                ):
                    exact_roots.add(prim_path)
                    attributed.add(prim_path)
                    continue
                else:
                    raise ValueError(
                        "cut collider is outside an authorized storey, stair "
                        "obstacle, or prior evidence-bound support root: "
                        f"{prim_path}"
                    )
            match = WALL_ROOT_RE.match(object_root)
            if match:
                wall_stems.add((storey, match.group("stem")))
                attributed.add(f"{storey}/{match.group('stem')}/*")
            else:
                if FORBIDDEN_OBJECT_ROOT_RE.search(object_root):
                    raise ValueError(
                        "minimum cut requests a protected doorway/frame, "
                        f"appliance, or PÀRC object root: {storey}/{object_root}"
                    )
                root = f"/World/{storey}/Geometry/{object_root}"
                exact_roots.add(root)
                attributed.add(root)
        component_rows.append({
            "storey": storey,
            "component_label": cut["component_label"],
            "component_area_m2": cut["component_area_m2"],
            "minimum_blocked_body_center_cell_count": cut[
                "minimum_blocked_body_center_cell_count"
            ],
            "attributed_repair_roots": sorted(attributed),
        })

    wall_rows = []
    for storey, stem in sorted(wall_stems):
        geometry = stage.GetPrimAtPath(f"/World/{storey}/Geometry")
        if not geometry.IsValid():
            raise ValueError(f"missing /World/{storey}/Geometry")
        siblings = sorted(
            str(child.GetPath())
            for child in geometry.GetChildren()
            if WALL_ROOT_RE.match(child.GetName())
            and WALL_ROOT_RE.match(child.GetName()).group("stem") == stem
        )
        roles = {
            WALL_ROOT_RE.match(PurePosixPath(path).name).group("role")
            for path in siblings
        }
        if not siblings or roles != {"visual", "collision"}:
            raise ValueError(
                f"logical wall {storey}/{stem} lacks visual/collision siblings: "
                f"{siblings}"
            )
        exact_roots.update(siblings)
        wall_rows.append({
            "storey": storey,
            "logical_wall_stem": stem,
            "deactivated_sibling_roots": siblings,
        })

    targets = sorted(exact_roots)
    if not targets:
        raise ValueError("no repair roots resolved")
    for path in targets:
        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid() or not prim.IsActive():
            raise ValueError(f"repair target missing or already inactive: {path}")
    return targets, component_rows + wall_rows


def _support_cell_groups(
    cuts: list[dict[str, Any]], storey: str
) -> list[list[dict[str, Any]]]:
    by_index: dict[tuple[int, int], dict[str, Any]] = {}
    for cut in cuts:
        if cut["storey"] != storey:
            continue
        for cell in cut.get("support_repair_cells", []):
            index = tuple(int(value) for value in cell["cell_xy_index"])
            bounds = tuple(float(value) for value in cell["world_xy_bounds_m"])
            if len(index) != 2 or len(bounds) != 4:
                raise ValueError("malformed support repair cell")
            prior = by_index.get(index)
            if prior is not None and prior != cell:
                raise ValueError(f"conflicting support repair cell: {index}")
            by_index[index] = cell
    groups = []
    remaining = set(by_index)
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        queue = [start]
        indices = []
        while queue:
            current = queue.pop()
            indices.append(current)
            for neighbor in (
                (current[0] - 1, current[1]),
                (current[0] + 1, current[1]),
                (current[0], current[1] - 1),
                (current[0], current[1] + 1),
            ):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        groups.append([by_index[index] for index in sorted(indices)])
    return groups


def _bind_support_elevations(
    cuts: list[dict[str, Any]],
    *,
    source: Path,
    building: str,
    g25_path: Path | None,
) -> dict[str, float]:
    required_storeys = {
        cut["storey"] for cut in cuts if cut.get("support_repair_cells")
    }
    elevations = {"Floor1": 0.0} if "Floor1" in required_storeys else {}
    if "Floor2" not in required_storeys:
        for cut in cuts:
            if cut.get("support_repair_cells"):
                cut["support_elevation_m"] = elevations[cut["storey"]]
        return elevations
    if g25_path is None:
        raise ValueError(
            "Floor2 support repair requires frozen-G25 support elevation evidence"
        )
    document = json.loads(g25_path.read_text(encoding="utf-8"))
    if document.get("schema") != "G25DeliveredBuildingV1":
        raise ValueError("unexpected support-elevation G25 schema")
    if document.get("building") != building:
        raise ValueError("support-elevation G25 building differs")
    if document.get("source", {}).get(
        "evaluated_root_layer_sha256"
    ) != _sha256(source):
        raise ValueError("support-elevation G25 source hash differs")
    floor = document.get("storeys", {}).get("Floor2", {})
    reached = [
        row for row in floor.get("connected_regions", [])
        if row.get("reachable_from_stair_arrival") is True
    ]
    if len(reached) != 1:
        raise ValueError("Floor2 G25 must have exactly one arrival-reachable region")
    lower = float(reached[0]["minimum_support_top_m"])
    upper = float(reached[0]["maximum_support_top_m"])
    if abs(lower - upper) > 1.0e-4:
        raise ValueError("Floor2 arrival region does not have a single support elevation")
    arrival_z = float(floor["stair_arrival_xyz_m"][2])
    if abs(arrival_z - lower) > 0.25 + 1.0e-9:
        raise ValueError("Floor2 support elevation is not adjacent to stair arrival")
    elevations["Floor2"] = lower
    for cut in cuts:
        if cut.get("support_repair_cells"):
            cut["support_elevation_m"] = elevations[cut["storey"]]
    return elevations


def _support_plane_mesh(
    stage: Usd.Stage,
    path: str,
    bounds_m: tuple[float, float, float, float, float, float],
) -> UsdGeom.Mesh:
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage))
    if not meters_per_unit > 0:
        raise ValueError("invalid stage metersPerUnit")
    x0, y0, _z0, x1, y1, z1 = (
        value / meters_per_unit for value in bounds_m
    )
    points = [
        Gf.Vec3f(x0, y0, z1), Gf.Vec3f(x1, y0, z1),
        Gf.Vec3f(x1, y1, z1), Gf.Vec3f(x0, y1, z1),
    ]
    counts = [4]
    indices = [0, 1, 2, 3]
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(indices)
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDoubleSidedAttr(True)
    return mesh


def _author_support_repairs(
    stage: Usd.Stage, cuts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    support_groups = {
        storey: _support_cell_groups(cuts, storey)
        for storey in ("Floor1", "Floor2")
    }
    if not any(support_groups.values()):
        return rows

    scope_index = 0
    while True:
        suffix = "" if scope_index == 0 else f"Iter{scope_index:03d}"
        scope_path = f"/World/NavigationRepairSupports{suffix}"
        if not stage.GetPrimAtPath(scope_path).IsValid():
            break
        scope_index += 1
    UsdGeom.Scope.Define(stage, scope_path)

    for storey in ("Floor1", "Floor2"):
        groups = support_groups[storey]
        if not groups:
            continue
        elevations = {
            float(cut["support_elevation_m"])
            for cut in cuts
            if cut["storey"] == storey and cut.get("support_repair_cells")
        }
        if len(elevations) != 1:
            raise ValueError(f"{storey} support elevation evidence differs")
        support_z = next(iter(elevations))
        for index, cells in enumerate(groups):
            all_bounds = [cell["world_xy_bounds_m"] for cell in cells]
            x0 = min(float(value[0]) for value in all_bounds)
            y0 = min(float(value[1]) for value in all_bounds)
            x1 = max(float(value[2]) for value in all_bounds)
            y1 = max(float(value[3]) for value in all_bounds)
            # A single double-sided top face supplies rasterized support without
            # introducing the vertical collision sidewalls that the worst-five
            # pilot proved can pinch a radius-0.25 m navigation corridor.
            bounds = (x0, y0, support_z, x1, y1, support_z)
            prefix = f"{scope_path}/{storey}_{index:03d}"
            visual = _support_plane_mesh(stage, prefix + "_visual", bounds)
            visual.CreateDisplayColorAttr([Gf.Vec3f(0.45, 0.45, 0.45)])
            collision = _support_plane_mesh(stage, prefix + "_collision", bounds)
            UsdGeom.Imageable(collision).CreatePurposeAttr().Set(
                UsdGeom.Tokens.guide
            )
            UsdGeom.Imageable(collision).CreateVisibilityAttr().Set(
                UsdGeom.Tokens.invisible
            )
            UsdPhysics.CollisionAPI.Apply(collision.GetPrim()).CreateCollisionEnabledAttr(
                True
            )
            rows.append({
                "storey": storey,
                "support_scope": scope_path,
                "component_cell_count": len(cells),
                "cell_xy_indices": sorted(cell["cell_xy_index"] for cell in cells),
                "world_bounds_m": list(bounds),
                "visual_prim": str(visual.GetPath()),
                "collision_prim": str(collision.GetPath()),
            })
    return rows


def _apply_repair(
    root: Path, cuts: list[dict[str, Any]]
) -> dict[str, Any]:
    stage = Usd.Stage.Open(str(root), load=Usd.Stage.LoadAll)
    if stage is None or not stage.GetPrimAtPath("/World").IsValid():
        raise ValueError(f"could not open extracted root layer: {root}")
    before_visual = _visual_geometry_hash(stage)
    before_enabled = _enabled_collider_count(stage)
    targets, evidence_rows = _repair_targets(stage, cuts)
    target_collider_count = 0
    for path in targets:
        prim = stage.GetPrimAtPath(path)
        target_collider_count += sum(
            1
            for descendant in Usd.PrimRange(prim)
            if descendant.IsA(UsdGeom.Mesh)
            and descendant.HasAPI(UsdPhysics.CollisionAPI)
            and UsdPhysics.CollisionAPI(descendant).GetCollisionEnabledAttr().Get()
            is not False
        )
        if not prim.SetActive(False):
            raise RuntimeError(f"failed to deactivate repair target: {path}")
    support_repairs = _author_support_repairs(stage, cuts)
    if not stage.GetRootLayer().Save():
        raise RuntimeError(f"failed to save repaired layer: {root}")

    check = Usd.Stage.Open(str(root), load=Usd.Stage.LoadAll)
    if check is None:
        raise ValueError("residual-repaired extracted root did not reopen")
    still_active = [
        path for path in targets
        if check.GetPrimAtPath(path).IsValid() and check.GetPrimAtPath(path).IsActive()
    ]
    if still_active:
        raise ValueError(f"repair targets remain active: {still_active}")
    after_visual = _visual_geometry_hash(check)
    after_enabled = _enabled_collider_count(check)
    if after_enabled != before_enabled - target_collider_count + len(support_repairs):
        raise ValueError(
            f"enabled collider count {after_enabled} != expected "
            f"{before_enabled - target_collider_count + len(support_repairs)}"
        )
    if after_visual == before_visual:
        raise ValueError("residual repair unexpectedly changed no visual geometry")
    return {
        "deactivated_root_count": len(targets),
        "deactivated_roots": targets,
        "deactivated_enabled_collider_count": target_collider_count,
        "enabled_mesh_collider_count_before": before_enabled,
        "enabled_mesh_collider_count_after": after_enabled,
        "visual_geometry_sha256_before": before_visual,
        "visual_geometry_sha256_after": after_visual,
        "visual_geometry_modified": True,
        "component_and_wall_evidence": evidence_rows,
        "authored_support_repair_count": len(support_repairs),
        "authored_support_repairs": support_repairs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--minimum-cut", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--building", required=True)
    parser.add_argument("--residual-root-cause", required=True, type=Path)
    parser.add_argument("--support-elevation-g25", type=Path)
    args = parser.parse_args()

    source = args.input.expanduser().resolve()
    cut_path = args.minimum_cut.expanduser().resolve()
    output = args.output.expanduser().resolve()
    report = args.report.expanduser().resolve()
    residual_root_cause = args.residual_root_cause.expanduser().resolve()
    for path in (source, cut_path, residual_root_cause):
        if not path.is_file():
            raise FileNotFoundError(path)
    if output.exists() or report.exists():
        raise FileExistsError(f"refusing existing output/report: {output}, {report}")

    cuts, cut_document = _load_authorized_cuts(
        cut_path, source, args.building
    )
    support_elevation_path = (
        args.support_elevation_g25.expanduser().resolve()
        if args.support_elevation_g25 else None
    )
    if support_elevation_path is not None and not support_elevation_path.is_file():
        raise FileNotFoundError(support_elevation_path)
    support_elevations = _bind_support_elevations(
        cuts, source=source, building=args.building,
        g25_path=support_elevation_path,
    )
    source_members = _safe_usdz_member_names(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".residual_navigation_repair_", dir=str(output.parent)
    ) as directory:
        staging = Path(directory)
        extracted = staging / "extracted"
        if not UsdUtils.ExtractUsdzPackage(
            str(source), str(extracted), verbose=False, force=False
        ):
            raise RuntimeError(f"USDZ extraction failed: {source}")
        source_non_root_payloads = _payload_manifest_from_files(
            extracted, source_members, exclude_root=True
        )
        changes = _apply_repair(extracted / "building.usda", cuts)
        partial = staging / "repaired.partial.usdz"
        _write_aligned_usdz(extracted, partial, source_members)
        archive_checks = _verify_aligned_usdz(
            partial, source_members, source_non_root_payloads
        )
        os.replace(partial, output)

    packaged = Usd.Stage.Open(str(output), load=Usd.Stage.LoadAll)
    if packaged is None or not packaged.GetPrimAtPath("/World").IsValid():
        raise ValueError("repaired output does not reopen with /World")
    if _visual_geometry_hash(packaged) != changes["visual_geometry_sha256_after"]:
        raise ValueError("packaging changed repaired visual geometry")
    payload = {
        "schema": "Factory10ResidualNavigationRepairV1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "building": args.building,
        "input": str(source),
        "input_sha256": _sha256(source),
        "minimum_cut_evidence": str(cut_path),
        "minimum_cut_evidence_sha256": _sha256(cut_path),
        "residual_root_cause": str(residual_root_cause),
        "residual_root_cause_sha256": _sha256(residual_root_cause),
        "oversized_component_threshold_m2_from_frozen_gate": (
            MAX_UNREACHABLE_STANDABLE_COMPONENT_M2
        ),
        "selected_oversized_component_count": len(cuts),
        "support_elevations_m": support_elevations,
        "support_elevation_g25": (
            str(support_elevation_path) if support_elevation_path else None
        ),
        "support_elevation_g25_sha256": (
            _sha256(support_elevation_path) if support_elevation_path else None
        ),
        "protected_gate_modified": False,
        "formal_threshold_modified": False,
        "stage_d_modified": False,
        "final_stage_d_rerun_required": True,
        "door_policy": (
            "only leaf/handle branches below doorway_/doorframe_ roots were "
            "excluded by diagnosis; frames/jambs retained; appliance doors excluded"
        ),
        "repair_scope": (
            "complete render-and-collision object roots attributed by verified "
            "minimum cuts; logical wall visual/collision sibling groups; "
            "iteration-specific evidence-bound support scopes"
        ),
        **changes,
        "archive_integrity": archive_checks,
        "output": str(output),
        "output_bytes": output.stat().st_size,
        "output_sha256": _sha256(output),
        "checks": {
            "input_bound_to_minimum_cut_evidence": True,
            "only_evidence_bound_oversized_components_selected": True,
            "both_visual_and_collision_wall_siblings_removed": True,
            "unrelated_object_roots_retained": True,
            "post_cut_missing_support_cells_physically_patched": True,
            "non_root_archive_payloads_unchanged": True,
            "output_reopens_with_world": True
        }
    }
    _exclusive_json(report, payload)
    print(json.dumps({
        "status": payload["status"],
        "building": args.building,
        "selected_oversized_components": len(cuts),
        "deactivated_roots": len(changes["deactivated_roots"]),
        "output": str(output),
        "output_sha256": payload["output_sha256"],
        "report": str(report),
        "report_sha256": _sha256(report),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
