"""Chinese mergeable fragment from real readback JSON, never planned PASS placeholders."""
import json
from pathlib import Path
from factory80_whitelist import ref

TASK=Path('/cephfs/shared/yixing/outputs/factory80_20261011')
readback=TASK/'evidence/NATIVE_PILOT6_ACTUAL_READBACK_FINAL.json'
if not readback.exists():readback=TASK/'evidence/NATIVE_PILOT6_ACTUAL_READBACK_INTERIM_11.json'
data=json.loads(readback.read_text())
plan=json.loads((TASK/'SIGNATURE_PLAN_FACTORY80.json').read_text())['rows'][:6]
lines=[f"原生楼梯输入实测：{data['actual_asset_count']}/14份，读回状态 `{data['work_state']}`。本片段从真实读回JSON生成，没有用计划填入ACTUAL，也没有正式验收结论。读回源：`{readback}`，SHA `{ref(readback)['sha256']}`。",
       '', '生成前计划（仅计划，不计作实测）：', '',
       '| 建筑 | family / support / handrail | step / tread / handrail材质计划 | RGB目标 | 工作状态 | 验证状态 | 不可支持 |',
       '|---|---|---|---|---|---|---|']
for row in plan:
    lines.append(f"| {row['building']} | {row['staircase_family']} / {row['support_type']} / {row['handrail_type']} | {row['step_material']} / {row['tread_material']} / {row['handrail_material']} | {row['rgb_target_srgb']} | 生成前已分配 | 仅白名单预检，不是ACTUAL | 不支持后生成签名、颜色、几何或门禁通过 |")
lines += ['', '实际输入（每个值来自实际metrics/shader/资源读回；同栋signature协调及seed独立已单独测量）：', '',
          '| 建筑 | ACTUAL family / support / handrail / palette | ACTUAL step / tread / handrail材质 | 实际shader反算sRGB | selected seeds | 工作状态 | 验证状态 | 不可支持 |',
          '|---|---|---|---|---|---|---|---|']
for building in data['rows']:
    rows=building['rows']
    if rows:
        sigs={json.dumps(row['ACTUAL_SIGNATURE'],sort_keys=True) for row in rows}
        signatures=[' / '.join(str(json.loads(sig)[key]) for key in ('staircase_family','support_type','handrail_type','dominant_palette')) for sig in sorted(sigs)]
        material_tuples=sorted({tuple(row['actual_three_materials'][key] for key in ('step_material','tread_material','handrail_material')) for row in rows})
        colors=sorted({tuple(round(float(v),6) for v in shader) for row in rows for shader in row['actual_shader_srgb']})
        lines.append(f"| {building['building']} | {'; '.join(signatures)} | {'; '.join(' / '.join(values) for values in material_tuples)} | {colors} | {[row['seed'] for row in rows]} | 实际{len(rows)}/{building['planned_stair_count']}份可用 | {building['validation_state']} | 不支持整栋门禁、RTX实看、正式验收或人形可爬 |")
    else:
        lines.append(f"| {building['building']} | 未实测，不能填计划值 | 未实测 | 未实测 | 尚无可用selected seed | 0/{building['planned_stair_count']}；fresh-seed执行未完成 | {building['validation_state']}；{building.get('error','')} | 新seed计划、参数筛查、导入、总体进程rc0均不证明生成成功或验收 |")
lines += ['', '逐梯实际判读证据（这里只读真实资产输入；native elapsed来自metrics，CPU bake来自真实operator记录）：', '',
          '| 建筑 / 梯 / seed | 选中attempt | 实际判读 | 证据路径 | 工作状态 | 验证状态 | 不可支持 |',
          '|---|---|---|---|---|---|---|']
for building in data['rows']:
    for row in building['rows']:
        root=Path(row['selected_attempt_root'])
        refs=f"`{root}/ACTUAL_SIGNATURE.json`；`{row['metrics']['path']}`；`{root}/asset/NATIVE_RGB.json`；`{root}/asset/NATIVE_ROLE_ASSIGNMENT.json`；`{root}/asset/NATIVE_CPU_BAKE_RESULT.json`；`{root}/portable/RESOURCE_RELOCATION.json`"
        evidence=f"三材质/角色/RGB/CPU/portable已读回PASS；接口相等={row['actual_interface_equality']}；maxZ={row['actual_max_traversal_z_m']}；CoACD {row['coacd_invocations']}次；真实CPU bake {row['cpu_bake_operator_count']}次/{row['actual_cpu_bake_seconds']:.3f}s；native metrics elapsed {row['native_elapsed_seconds_after_import']:.3f}s"
        mode=f"{row['execution_mode']}；独立进程rc={row['independent_process_returncode']}；原main函数成功={row['native_function_succeeded']}"
        lines.append(f"| {building['building']} / {row['stair_index']} / {row['seed']} | {root.name} | {evidence}；{mode} | {refs} | 真实输入可用 | NATIVE_FIELDS_RESOURCES_ONLY | 不支持整栋门禁、RTX实际观看、验收；函数返回不冒充独立进程rc |")
lines += ['', '006旧seed真实普通失败（不可改的R2/R3结果均保留；不是负控失效，不触发全停）：', '',
          '| 梯 / seed / attempt | 实际错误与函数耗时 | 原始结果证据 | 工作状态 | 验证状态 | 不可支持 |',
          '|---|---|---|---|---|---|']
for index in (1,2,3):
    for attempt in (2,3):
        path=TASK/f'assets/factory80_006/stair{index:02d}/attempt{attempt}/RESULT.json'
        old=json.loads(path.read_text())
        failure=old.get('function_exception')
        lines.append(f"| {index} / {old['seed']} / {attempt} | {json.dumps(failure,ensure_ascii=False)}；{old['function_elapsed_seconds']:.3f}s | `{path}` | 实际原main函数失败 | 无可用资产，{old['validation_state']} | 不支持修复成功；独立进程rc为null |")
lines += ['', 'fresh-seed修复预登记：`evidence/NATIVE_PILOT6_FRESH_SEED_REPAIR_PLAN.json`。新配置记录只是原factory实际参数，不是资产。原 has_tread guard、参数采样、collision分类区间、Standability V1和冻结判据均未修改。原批overall rc0只表示队列完成，不能覆盖六次原函数失败。',
          '', '此前失败同样保留：首轮14槽实际因editable Infinigen包路径失效在import阶段失败；001两槽R2因自有RGB adapter未递归nodegroup而在导出前失败，修自有adapter后实际R3成功。001–005已有成功11份不会重复生成。',
          '', '两个白名单负控均实际运行并输出FAIL/exit1（Marble扶手；metal_fake含Wood）。80计划覆盖36组合、材质+型式最多18≤20；这些预检不能替代后生成实测。', '']
repair=TASK/'assets/factory80_006/STAIR_ASSETS_PORTABLE_SEED_REPAIR_FINAL.json'
if repair.is_file():
    actual=json.loads(repair.read_text())
    lines += ['006新seed实际结果：`'+str(repair)+'`，实际可用数`'+str(actual['actual_asset_count'])+'`，工作状态`'+actual['work_state']+'`。选中seed为'+str([row['seed'] for row in actual['rows']])+'；旧0/3 FINAL不变，旧六次函数FAIL没有覆盖或删除。seed2086只做原factory参数筛查，tread_height=.013918738低于原.015分类下界而拒绝；随后预登记seed2087执行原native main后真实成功。这些实测仍不支持整栋门禁、RTX或正式验收。', '']
path=TASK/'evidence/NATIVE_STAIR_ACTUAL_EVIDENCE_FRAGMENT.md'
path.write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps(dict(path=str(path),source_readback=ref(readback),actual_asset_count=data['actual_asset_count'],work_state=data['work_state']),ensure_ascii=False))
