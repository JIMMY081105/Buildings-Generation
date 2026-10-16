#!/usr/bin/env python3
"""Assemble and audit the machine-clean batch_20260826_r3 H1 delivery."""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import shutil
from pathlib import Path

from batch_execution_contract import REQUIRED_18_GATES


CPU_GATES = (
    "G19_zero_rigid_body_api",
    "G20_all_approximation_none",
    "G21_per_floor_parity",
    "G22_collider_bounds_match_vertex_union",
    "G23_voxel_bake_clean",
    "G24_stair_reachability",
)
VIEWS = ("01_overview", "02_stairwell", "03_arrival", "04_eyelevel", "05_topdown")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def link_or_copy(src: str, dst: str) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def render_review(path: Path, bid: str, row: dict) -> None:
    metrics = []
    for storey in ("Floor1", "Floor2"):
        g25 = row["G25"][storey]
        metrics.append(
            "<tr><td>{}</td><td>{:.4f}</td><td>{:.4f} × {:.4f}</td>"
            "<td>{:.4f}</td><td>{:.4f}</td></tr>".format(
                storey, g25["reachable_area_m2"], g25["x_extent_m"],
                g25["y_extent_m"], g25["maximum_free_radius_m"],
                g25["minimum_headroom_m"]))
    images = []
    for view in VIEWS:
        images.append(
            f"<h3>{view}</h3><div class='pair'>"
            f"<img src='renders/{view}_render.png'>"
            f"<img src='renders/{view}_collision.png'></div>")
    content = """<!doctype html><meta charset='utf-8'>
<title>{bid} — H1 review</title>
<style>body{{font-family:system-ui;margin:2rem;max-width:1400px}}table{{border-collapse:collapse}}
td,th{{border:1px solid #aaa;padding:.4rem}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:1rem}}
img{{width:100%;height:auto;background:#222}}</style>
<h1>{bid}</h1><p><strong>Machine status: {status}</strong>; human H1 decision pending.</p>
<p>A4 v2 minimum openings: Floor1 {f1:.4f} m; Floor2 {f2:.4f} m. First rise {rise:.7f} m.</p>
<table><tr><th>storey</th><th>reachable area m²</th><th>reachable XY extent m</th>
<th>max free radius m</th><th>minimum headroom m</th></tr>{metrics}</table>{images}
""".format(
        bid=html.escape(bid), status=row["status"],
        f1=row["A4_v2"]["Floor1_minimum_clear_opening_m"],
        f2=row["A4_v2"]["Floor2_minimum_clear_opening_m"],
        rise=row["ground_to_first_tread_m"], metrics="".join(metrics),
        images="".join(images))
    path.write_text(content, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geometry-root", type=Path, required=True)
    ap.add_argument("--placement-root", type=Path, required=True)
    ap.add_argument("--stage-summary", type=Path, required=True)
    ap.add_argument("--normalization-summary", type=Path, required=True)
    ap.add_argument("--cpu-summary", type=Path, required=True)
    ap.add_argument("--cpu-evidence-root", type=Path, required=True)
    ap.add_argument("--gpu-root", type=Path, required=True)
    ap.add_argument("--g25-root", type=Path, required=True)
    ap.add_argument("--packaging-summary", type=Path, required=True)
    ap.add_argument("--capacity-summary", type=Path, required=True)
    ap.add_argument("--aux-report", type=Path, action="append", default=[])
    ap.add_argument("--output-root", type=Path, required=True)
    args = ap.parse_args()
    if args.output_root.exists():
        raise FileExistsError(f"refusing to overwrite {args.output_root}")

    stage_summary = load(args.stage_summary)
    normalization = load(args.normalization_summary)
    cpu_summary = load(args.cpu_summary)
    packaging = load(args.packaging_summary)
    capacity = load(args.capacity_summary)
    rows_source = stage_summary.get("rows", [])
    if not (
        stage_summary.get("status") == "PASS" and len(rows_source) == 10
        and normalization.get("status") == "PASS"
        and cpu_summary.get("status") == "PASS"
        and packaging.get("status") == "PASS"
        and capacity.get("status") == "PASS"
    ):
        raise ValueError("one or more prerequisite batch summaries are not PASS")

    validation = args.output_root / "validation"
    reports_root = args.output_root / "batch_reports"
    validation.mkdir(parents=True)
    reports_root.mkdir()
    rows = []
    for source in rows_source:
        bid = source["BID"]
        source_dir = args.geometry_root / bid
        output = validation / bid
        shutil.copytree(source_dir, output, copy_function=link_or_copy)
        shutil.copytree(args.placement_root / bid / "placement",
                        output / "placement", copy_function=link_or_copy)
        reports = output / "reports"
        renders = output / "renders"
        gpu = args.gpu_root / bid
        shutil.copytree(gpu / "renders", renders, copy_function=link_or_copy)
        shutil.copy2(gpu / "reports" / "isaac_validation.json",
                     reports / "isaac_validation.json")
        shutil.copy2(gpu / "reports" / "isaac_console.log",
                     reports / "isaac_console.log")
        cpu_path = args.cpu_evidence_root / bid / "g19_g24.json"
        shutil.copy2(cpu_path, reports / "g19_g24.json")
        g25_path = args.g25_root / f"{bid}.json"
        shutil.copy2(g25_path, reports / "g25_storey_navigation.json")
        build_report = reports / "build_report.json"
        shutil.copy2(build_report, reports / "build.json")

        isaac = load(reports / "isaac_validation.json")
        cpu = load(cpu_path)
        g25 = load(g25_path)
        package = load(reports / "self_contained_package.json")
        a4 = source["A4_v2"]
        asset = source["stage_b_asset"]
        frame_names = sorted(path.name for path in renders.glob("*.png"))
        expected_frames = sorted(
            f"{view}_{kind}.png" for view in VIEWS
            for kind in ("render", "collision"))
        gates_18 = {
            name: isaac.get("acceptance_gates", {}).get(name) is True
            for name in REQUIRED_18_GATES
        }
        gates_6 = {
            name: cpu.get("gates", {}).get(name, {}).get("status") == "PASS"
            for name in CPU_GATES
        }
        exact_rise = cpu["gates"]["G24_stair_reachability"][
            "A_authored_geometry_exact"]
        g25_rows = {}
        for storey in ("Floor1", "Floor2"):
            value = g25["storeys"][storey]
            g25_rows[storey] = {
                "status": value["status"],
                "reachable_area_m2": value[
                    "area_reachable_from_stair_arrival_m2"],
                "x_extent_m": value["reachable_x_extent_m"],
                "y_extent_m": value["reachable_y_extent_m"],
                "bbox_diagonal_m": value["reachable_bbox_diagonal_m"],
                "maximum_free_radius_m": value[
                    "maximum_free_radius_on_reachable_m"],
                "minimum_headroom_m": value["minimum_headroom_m"],
                "oversized_unreachable_component_count": value[
                    "A4_unreachable_component_rule"][
                        "oversized_unreachable_component_count"],
                "unreachable_component_rule_pass": value[
                    "A4_unreachable_component_rule"]["pass"],
            }
        checks = {
            "A4_v2_both_storeys_at_least_0_60m": all(
                floor["pass"] and floor["minimum_clear_opening_m"] >= 0.60
                for floor in a4.values()),
            "strict_stair_normalization": asset["strict_normalization_pass"],
            "ground_to_first_tread_at_most_0_20m": (
                exact_rise["first_rise_m"] <= 0.20),
            "existing_24_gates": all(gates_18.values()) and all(gates_6.values()),
            "G25_both_storeys": all(
                value["status"] == "PASS"
                and value["unreachable_component_rule_pass"]
                for value in g25_rows.values()),
            "ten_render_files": frame_names == expected_frames,
            "self_contained_zero_unresolved": (
                package["status"] == "PASS"
                and package["off_cluster_unresolved_references"] == 0
                and package["cluster_absolute_paths_in_package"] == 0),
        }
        row = {
            "BID": bid,
            "status": "PASS" if all(checks.values()) else "FAIL",
            "failed_candidates_retained": True,
            "A4_v2": {
                "Floor1_minimum_clear_opening_m": a4["Floor1"][
                    "minimum_clear_opening_m"],
                "Floor2_minimum_clear_opening_m": a4["Floor2"][
                    "minimum_clear_opening_m"],
            },
            "strict_stair_normalization": asset["strict_normalization_pass"],
            "ground_to_first_tread_m": exact_rise["first_rise_m"],
            "ground_to_first_tread_source": (
                "G24 exact authored walking-surface geometry in the composed building"),
            "gates_18": gates_18,
            "gates_6": gates_6,
            "existing_24_gate_pass_count": sum(gates_18.values()) + sum(gates_6.values()),
            "G25": g25_rows,
            "package": {
                "usdz_bytes": package["package_bytes"],
                "unresolved_references": package[
                    "off_cluster_unresolved_references"],
                "external_absolute_dependencies": package[
                    "cluster_absolute_paths_in_package"],
                "relative_usda_wrapper": True,
            },
            "checks": checks,
        }
        write_json(reports / "machine_acceptance.json", row)
        render_review(output / "review.html", bid, row)
        rows.append(row)

    passed = sum(row["status"] == "PASS" for row in rows)
    decision_files = list(args.output_root.rglob("decision.json"))
    if decision_files:
        raise ValueError(f"H1 delivery must not contain decision.json: {decision_files}")
    component_trips = sum(
        value["oversized_unreachable_component_count"]
        for row in rows for value in row["G25"].values())
    audit = {
        "schema": "Batch20260826R3MachineAuditV1",
        "status": "PASS" if passed == 10 else "FAIL",
        "human_gate": "H1_PENDING",
        "H1_approved": False,
        "training_authorized": False,
        "batch_generation_authorized": False,
        "automatic_batch_generation_authorized": False,
        "decision_json_written": False,
        "building_count": len(rows),
        "passed_buildings": passed,
        "failed_buildings": len(rows) - passed,
        "failed_candidates_retained": True,
        "A4_unreachable_component_rule_threshold_m2": 4.0,
        "A4_unreachable_component_rule_trip_count": component_trips,
        "capacity": capacity["new_batch"] | capacity[
            "required_scene_policy_config"],
        "rows": rows,
    }
    write_json(reports_root / "machine_audit_table.json", audit)
    for name, source_path in {
        "stage_c_summary.json": args.stage_summary,
        "physics_normalization_batch.json": args.normalization_summary,
        "cpu_g19_g24_summary.json": args.cpu_summary,
        "self_contained_packaging_summary.json": args.packaging_summary,
        "capacity_batch_summary.json": args.capacity_summary,
    }.items():
        shutil.copy2(source_path, reports_root / name)
    for source_path in args.aux_report:
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        shutil.copy2(source_path, reports_root / source_path.name)
    flat_rows = []
    for row in rows:
        flat_rows.append({
            "BID": row["BID"], "status": row["status"],
            "A4_Floor1_m": row["A4_v2"]["Floor1_minimum_clear_opening_m"],
            "A4_Floor2_m": row["A4_v2"]["Floor2_minimum_clear_opening_m"],
            "first_rise_m": row["ground_to_first_tread_m"],
            "strict_normalization": row["strict_stair_normalization"],
            "gate_pass_count": row["existing_24_gate_pass_count"],
            "G25_Floor1_area_m2": row["G25"]["Floor1"]["reachable_area_m2"],
            "G25_Floor2_area_m2": row["G25"]["Floor2"]["reachable_area_m2"],
            "unresolved_references": row["package"]["unresolved_references"],
        })
    for path in (args.output_root / "summary.csv",
                 reports_root / "machine_audit_table.csv"):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(flat_rows[0]))
            writer.writeheader()
            writer.writerows(flat_rows)
    index_rows = "".join(
        "<tr><td><a href='validation/{0}/review.html'>{0}</a></td>"
        "<td>{1}</td><td>{2}/24</td></tr>".format(
            html.escape(row["BID"]), row["status"],
            row["existing_24_gate_pass_count"]) for row in rows)
    (args.output_root / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>batch_20260826_r3 H1</title>"
        "<h1>batch_20260826_r3 — H1 pending</h1>"
        "<p>Machine clean; no H1 decision has been written.</p>"
        "<table><tr><th>BID</th><th>status</th><th>gates</th></tr>"
        + index_rows + "</table>", encoding="utf-8")
    review_lines = [
        "# batch_20260826_r3 — machine-clean H1 handoff", "",
        f"Machine split: **{passed} PASS / {len(rows)-passed} FAIL**.",
        "Human H1 decision remains pending. No `decision.json` was written; training "
        "and both generation authorization flags remain false.", "",
        "## Per-candidate acceptance", "",
        "| BID | A4 F1/F2 m | first rise m | strict | gates | G25 area F1/F2 m² | package |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        review_lines.append(
            "| {BID} | {f1:.4f} / {f2:.4f} | {rise:.7f} | {strict} | "
            "{gates}/24 | {a1:.4f} / {a2:.4f} | {package} |".format(
                BID=row["BID"], f1=row["A4_v2"]["Floor1_minimum_clear_opening_m"],
                f2=row["A4_v2"]["Floor2_minimum_clear_opening_m"],
                rise=row["ground_to_first_tread_m"],
                strict="PASS" if row["strict_stair_normalization"] else "FAIL",
                gates=row["existing_24_gate_pass_count"],
                a1=row["G25"]["Floor1"]["reachable_area_m2"],
                a2=row["G25"]["Floor2"]["reachable_area_m2"],
                package="PASS" if row["package"]["unresolved_references"] == 0 else "FAIL"))
    review_lines += [
        "", "## Blocker findings", "",
        "- Closed locomotion doors are fixed in generation: leaf/attached-handle "
        "collision is disabled; frames and jambs remain collidable.",
        "- h320/h360 assets use combined static exact tread collision and place the "
        "walking-surface top on the riser grid; no height substitution was used.",
        "- The old 74/98 barriers are deliberate authored partitions. New generation "
        "enforces the separately justified 4.0 m² A4 unreachable-component limit.",
        "- The old 92/96 two-cell discrepancy is a 5 cm raster-phase effect: source "
        "floor triangles and the bounded terrain footprint both cover both cells.",
        "", "## Capacity", "",
        "- Maximum faces: `{}`; configured face capacity: `{}`.".format(
            capacity["new_batch"]["maximum_face_count"],
            capacity["required_scene_policy_config"]["face_capacity"]),
        "- Maximum 1 m boundary segments: `{}`; configured boundary capacity: `{}`.".format(
            capacity["new_batch"]["maximum_boundary_segments_at_1m"],
            capacity["required_scene_policy_config"]["boundary_segment_capacity"]),
        "", "Detailed evidence is in `batch_reports/`.", "",
    ]
    (args.output_root / "H1_REVIEW.md").write_text(
        "\n".join(review_lines), encoding="utf-8")
    ready = {
        "status": "AWAITING_HUMAN_H1" if passed == 10 else "MACHINE_FAIL",
        "machine_split": f"{passed} PASS / {len(rows)-passed} FAIL",
        "training_authorized": False,
        "batch_generation_authorized": False,
        "automatic_batch_generation_authorized": False,
        "decision_json_written": False,
        "artifact_provenance": {
            "geometry": "post_h1_remediation_r2 Stage C; corrected Stage B assets",
            "renders": "single RTX 5090 H1 Stage D run",
            "G25": "published self-contained USDZ with A4 unreachable-component rule enforced",
        },
    }
    write_json(args.output_root / "H1_MACHINE_READY.json", ready)
    print(json.dumps({
        "status": audit["status"], "building_count": len(rows),
        "passed_buildings": passed, "failed_buildings": len(rows)-passed,
        "A4_unreachable_component_rule_trip_count": component_trips,
    }, indent=2))
    return 0 if passed == 10 else 1


if __name__ == "__main__":
    raise SystemExit(main())
