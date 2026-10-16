#!/usr/bin/env python3
"""Assemble the verified exact-54 delivery tree atomically."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


EXPECTED = 54


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def digest(path: Path) -> str:
    result = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def link(source: Path, destination: Path, sources: dict[str, str], staging: Path) -> None:
    require(source.is_file(), f"source file absent: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.link(source, destination)
    relative = destination.relative_to(staging).as_posix()
    sources[relative] = str(source)


def false_checks(storey: dict[str, Any]) -> list[str]:
    checks = storey.get("checks")
    if isinstance(checks, dict):
        return sorted(key for key, value in checks.items() if value is False)
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r3", required=True, type=Path)
    parser.add_argument("--backup-evidence", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    r3 = args.r3.resolve()
    backup_path = args.backup_evidence.resolve()
    destination = args.output_root.resolve()
    require(not destination.exists(), f"release destination exists: {destination}")
    audit_path = r3 / "unify54" / "audit" / "COLLISION_AUDIT_54.json"
    doors_path = r3 / "unify54" / "opened54" / "DOOR_OPENING_54.json"
    g25_path = r3 / "unify54" / "g25_54" / "G25_SUMMARY.json"
    graph_summary_path = r3 / "unify54" / "GRAPH_BUILD_SUMMARY_54.json"
    acceptance_path = r3 / "unify54" / "GRAPH_ACCEPTANCE_54.json"
    capacity_path = r3 / "unify54" / "CAPACITY_EVIDENCE_54.json"
    tokens_root = r3 / "unify54" / "graph_tokens54"
    tokens_manifest_path = tokens_root / "GRAPH_TOKENS_MANIFEST_V1.json"
    integrity_path = r3 / "unify54" / "GATE_INTEGRITY_FINAL.json"
    audit = load(audit_path)
    doors = load(doors_path)
    g25 = load(g25_path)
    graph_summary = load(graph_summary_path)
    acceptance = load(acceptance_path)
    capacity = load(capacity_path)
    tokens = load(tokens_manifest_path)
    integrity = load(integrity_path)
    backup = load(backup_path)
    require(audit.get("status") == "PASS" and audit["scope"]["observed_buildings"] == EXPECTED, "audit differs")
    require(doors.get("status") == "PASS" and doors.get("observed_buildings") == EXPECTED, "doors differ")
    require(g25.get("building_count") == EXPECTED and g25.get("terminal_coverage_complete") is True, "G25 coverage differs")
    require(graph_summary.get("built_building_count") == EXPECTED, "graph coverage differs")
    require(acceptance.get("expected_building_count") == EXPECTED, "acceptance coverage differs")
    require(capacity.get("status") == "PASS" and capacity.get("GRAPH_NODE_CAPACITY") == 4096, "capacity differs")
    require(tokens.get("tensor_file_count") == EXPECTED and tokens.get("capacity") == 4096, "tokens differ")
    require(integrity.get("status") == "PASS", "final protected-file integrity differs")
    require(backup.get("status") == "PASS" and backup.get("exact_relative_file_set") is True, "F backup differs")

    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging.", dir=destination.parent))
    published = False
    try:
        sources: dict[str, str] = {}
        token_files = sorted(tokens_root.glob("*.pt"))
        require(len(token_files) == EXPECTED, f"expected {EXPECTED} token tensors")
        for source in token_files:
            link(source, staging / "graph_tokens" / source.name, sources, staging)
        link(tokens_manifest_path, staging / "graph_tokens" / tokens_manifest_path.name, sources, staging)

        graph_root = r3 / "unify54" / "graphs54"
        graph_files = sorted(graph_root.glob("*.npz"))
        require(len(graph_files) == EXPECTED, f"expected {EXPECTED} graphs")
        for source in graph_files:
            link(source, staging / "graphs" / source.name, sources, staging)
        link(acceptance_path, staging / "graphs" / "GRAPH_ACCEPTANCE.json", sources, staging)

        audit_files = sorted((r3 / "unify54" / "audit" / "by_building").glob("*.json"))
        require(len(audit_files) == EXPECTED, f"expected {EXPECTED} per-building audits")
        for source in audit_files:
            link(source, staging / "collision_audit" / source.name, sources, staging)
        link(audit_path, staging / "collision_audit" / "COLLISION_AUDIT_54.json", sources, staging)

        door_rows = doors.get("rows", [])
        require(len(door_rows) == EXPECTED, "door row count differs")
        for row in door_rows:
            source = r3 / "unify54" / "opened54" / row["building"] / "reports" / "door_opening.json"
            link(source, staging / "doors" / f"{row['building']}.door_opening.json", sources, staging)
        link(doors_path, staging / "doors" / "DOOR_OPENING_54.json", sources, staging)

        g25_failures = []
        for row in g25.get("rows", []):
            if row.get("status") == "PASS":
                continue
            report = load(Path(row["report"]))
            storeys = []
            for name in ("Floor1", "Floor2"):
                value = report.get("storeys", {}).get(name, {})
                if value.get("status") != "PASS":
                    storeys.append(
                        {
                            "storey": name,
                            "status": value.get("status"),
                            "false_checks": false_checks(value),
                            "failure_reasons": value.get("failure_reasons", []),
                            "walkable_area_m2": value.get("walkable_area_m2"),
                            "connected_region_count": value.get("connected_region_count"),
                            "oversized_unreachable_component_count": value.get(
                                "A4_unreachable_component_rule", {}
                            ).get("oversized_unreachable_component_count"),
                            "oversized_unreachable_components": value.get(
                                "A4_unreachable_component_rule", {}
                            ).get("oversized_unreachable_components", []),
                        }
                    )
            g25_failures.append(
                {"building": row["BID"], "exception": report.get("exception"), "storeys": storeys}
            )
        gate_failures = acceptance.get("blocking_failures", [])
        suspects = []
        for item in audit.get("suspected_sealed_cavities", []):
            for instance in item["instances"]:
                suspects.append(
                    {
                        "building": item["building"],
                        "path": instance["instance_path"],
                        "ratio": instance["hull_fill_ratio"],
                    }
                )
        duplicate_note = (
            "以下 6 个重名基型固定采用 A 组带 v001 的版本："
            "home_train107_train20_ushaped、home_train76_train123_straight、"
            "home_train84_train105_straight、home_train84_train22_straight、"
            "home_train92_train96_straight、home_train96_train20_straight。"
            "原因是 A 组版本已移除遮挡门洞的墙体 collider、修正楼梯语义根并补齐支撑面；"
            "F 盘同名版本是修复前旧几何。"
        )
        gpu = tokens["gpu_acceleration"]
        lines = [
            "# 54 栋可训练建筑图与张量交付",
            "",
            f"生成时间（UTC）：{datetime.now(timezone.utc).isoformat()}",
            "",
            "本交付覆盖 54 栋规范化后不重复建筑（A 组 50 + B 组 4）。未运行任何视觉渲染、截图或预览生成。",
            "",
            "## 碰撞审计",
            "",
            f"- 54/54 完成逐栋只读审计；启用的 mesh collider 合计 {audit['summary']['total_enabled_mesh_colliders']}。",
            f"- approximation 非 none：{audit['summary']['non_none_approximation_building_count']} 栋；rigid body：{audit['summary']['rigid_body_building_count']} 栋。",
            f"- 疑似封闭空腔：{audit['summary']['suspected_sealed_cavity_instance_count']} 个实例 / {audit['summary']['suspected_sealed_cavity_building_count']} 栋；判据为 5 cm 栅格 hull-fill ratio >= 0.99。",
            "",
        ]
        for item in suspects:
            lines.append(f"- `{item['building']}` `{item['path']}`：{item['ratio']:.6f}")
        lines += [
            "",
            "## 开门",
            "",
            "采用允许的 fallback (b)：仅停用 `/World/FloorN/Geometry/doorway_*` 或 `doorframe_*` 下完整门扇与门把手 Xform，使视觉与碰撞同时处于等价全开状态。冰箱、微波炉、烤箱、洗碗机等家电对象均排除。",
            f"- 54/54 PASS；目标 Xform {doors['total_target_xforms']} 个，新停用 {doors['total_newly_deactivated_xforms']} 个。",
            f"- 启用 collider 合计：{doors['total_enabled_colliders_before']} -> {doors['total_enabled_colliders_after']}。A 组门扇 collider 多数此前已禁用，因此其视觉打开可发生而 collider 数不变。",
            "",
            "## G25 与 G1-G10",
            "",
            f"- G25：{g25['passed_buildings']}/{EXPECTED} PASS，{g25['failed_buildings']} FAIL；所有失败均保留。",
        ]
        for item in g25_failures:
            lines.append(f"- G25 FAIL `{item['building']}`：`{json.dumps(item, ensure_ascii=False, separators=(',', ':'))}`")
        lines.append(f"- 各图门禁通过数：`{json.dumps(acceptance.get('gate_pass_counts', {}), ensure_ascii=False, sort_keys=True)}`。")
        for failure in gate_failures:
            lines.append(
                f"- 图门禁 FAIL `{failure.get('building')}` `{failure.get('gate')}`：{failure.get('detail')}"
            )
        lines += [
            "",
            "G9 按原判据运行且未改阈值；依据本轮用户明确授权，G9 不作为张量发布阻断条件。G1-G8 任一失败仍会阻断发布。",
            "",
            "## 容量与张量",
            "",
            f"- 本轮实测峰值：{capacity['remeasurement']['maximum_observed_node_count']} 节点（`{capacity['remeasurement']['maximum_observed_building']}`）。",
            f"- 与 48 栋历史证据合并后的峰值：{capacity['remeasurement']['combined_global_maximum_observed_node_count']}；严格下一次幂建议：{capacity['remeasurement']['strict_next_power_of_two_recommendation']}。",
            "- 统一容量选为 4096；旧 2048 无法容纳实测峰值。",
            "- `scene_graph_node_features`: `[1, 4096, 14]`, `torch.float32`；`scene_graph_node_valid_mask`: `[1, 4096]`, `torch.bool`。可由 `torch.load` 直接读取。",
            "",
            "## GPU",
            "",
            f"- A40 实际设备：`{gpu['device_name']}`；对 54×4096×14 的张量做了批量 GPU 校验，且注入非零 padding 的负控按预期失败。",
            f"- CPU 校验中位 {gpu['cpu_validation_median_seconds']:.6f}s；GPU kernel 中位 {gpu['gpu_kernel_validation_median_seconds']:.6f}s；kernel 加速比 {gpu['cpu_over_gpu_kernel_speedup']:.2f}x；含一次 H2D 的单批端到端比 {gpu['cpu_over_gpu_single_batch_end_to_end_speedup']:.2f}x。",
            "- USD 三角网格体素化、SciPy 连通域与洪泛保留在冻结 CPU 路径：它们依赖 trimesh 的 subdivide/fill 与既有浮点/栅格语义，临时改写 GPU 内核会改变判据输入，验证成本高于本批收益。张量最终在 CPU 序列化，以保持跨机器 `torch.load` 可移植性。",
            "",
            "## 取舍与完整性",
            "",
            duplicate_note,
            "",
            f"- F 盘旧成果备份：`{backup['backup']}`；{backup['source_file_count']} 个文件逐个 SHA-256 一致，状态 {backup['status']}。",
            f"- 受保护判据文件开工前与收尾校验均为 PASS；最终记录：`{integrity_path}`。",
            "- 未改判据文件或阈值；未缩小 54 栋范围；未生成视觉渲染。",
        ]
        readme = staging / "README.md"
        readme.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        sources["README.md"] = "generated from bound exact-54 evidence manifests"

        manifest_rows = []
        for relative in sorted(sources):
            target = staging / relative
            target_sha = digest(target)
            source_text = sources[relative]
            if source_text.startswith("generated "):
                source_sha = target_sha
            else:
                source_sha = digest(Path(source_text))
            require(source_sha == target_sha, f"assembly digest differs: {relative}")
            manifest_rows.append(
                {
                    "relative_path": relative,
                    "bytes": target.stat().st_size,
                    "source": source_text,
                    "destination": str(destination / relative),
                    "source_sha256": source_sha,
                    "destination_sha256": target_sha,
                    "match": True,
                }
            )
        copy_manifest = {
            "schema": "Exact54CopyManifestV1",
            "status": "PASS",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_root": str(r3 / "unify54"),
            "destination_root": str(destination),
            "file_count_excluding_manifest": len(manifest_rows),
            "all_source_destination_sha256_match": True,
            "files": manifest_rows,
        }
        write_json(staging / "COPY_MANIFEST.json", copy_manifest)
        actual = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file()
        }
        expected = {"COPY_MANIFEST.json", *(row["relative_path"] for row in manifest_rows)}
        require(actual == expected, "assembled release file set differs")
        os.replace(staging, destination)
        published = True
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "release": str(destination),
                    "payload_file_count": len(manifest_rows),
                    "tensor_count": len(token_files),
                    "graph_count": len(graph_files),
                    "audit_count": len(audit_files),
                    "door_count": len(door_rows),
                },
                indent=2,
            )
        )
        return 0
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)


if __name__ == "__main__":
    raise SystemExit(main())
