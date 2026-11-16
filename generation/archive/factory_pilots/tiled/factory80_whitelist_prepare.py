"""Explicitly authorized allocator color additions, actual negative controls and 80 preassignments."""
import hashlib
import itertools
import json
import random
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from factory80_whitelist import METALS, FAMILIES, RAILS, SUPPORTS, read_palette_table, ref, require, resolve_palette

TASK = Path('/cephfs/shared/yixing/outputs/factory80_20261011')
BASE = Path('/cephfs/shared/yixing/outputs/diversity54_20260908')
ALLOCATOR = BASE / 'scripts/portfolio_allocator.py'
ORIGINAL_SHA256 = 'a030297c903151f677a6c8461fa41efc18f76ef573b12e92306f87fc95784e78'
NEW_PALETTES = {
    'metal_safety_yellow': [0.96, 0.78, 0.08],
    'metal_industrial_grey': [0.42, 0.45, 0.47],
    'metal_machine_blue': [0.10, 0.28, 0.54],
    'metal_safety_orange': [0.94, 0.32, 0.06],
    'metal_galvanised': [0.69, 0.72, 0.74],
    'metal_dark_green': [0.12, 0.29, 0.21],
}

def write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        json.dump(doc, stream, ensure_ascii=False, indent=2); stream.write('\n')

def main():
    evidence = TASK / 'evidence'; evidence.mkdir(exist_ok=True)
    scripts = TASK / 'scripts'
    audit = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'allocator_before': ref(ALLOCATOR), 'manifests': []}
    for manifest in (BASE / 'scripts/FROZEN_CONTRACT_SOURCES.json', BASE / 'evidence/FROZEN_CONTRACT_SOURCES_GATE5_V2_R1.json'):
        rows = json.loads(manifest.read_text())['rows']
        matched = [row for row in rows if 'portfolio_allocator.py' in json.dumps(row)]
        if matched:
            raise RuntimeError('STOP_FROZEN_SCOPE: allocator appears in frozen sources')
        audit['manifests'].append(dict(source=ref(manifest), row_count=len(rows), portfolio_allocator_entries=matched))
    raw = ALLOCATOR.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ORIGINAL_SHA256:
        raise RuntimeError('Allocator changed since read-only audit; inspect before mutation')
    backup = evidence / 'portfolio_allocator_before_factory80.py'
    with backup.open('xb') as stream: stream.write(raw)
    text = raw.decode()
    anchor = "    'black': ([.13, .13, .14], 'Wood', 'MetalBasic', 'MetalBasic'),\n"
    assert text.count(anchor) == 1
    additions = "    # Factory80: tuple order is RGB / tread / step / handrail. RGB alone varies.\n"
    additions += ''.join(f"    {name!r}: ({rgb!r}, 'MetalBasic', 'BrushedMetal', 'BrushedMetal'),\n" for name, rgb in NEW_PALETTES.items())
    text = text.replace(anchor, anchor + additions)
    compile(text, str(ALLOCATOR), 'exec')
    ALLOCATOR.write_text(text)
    audit.update(allocator_after=ref(ALLOCATOR), allocator_backup=ref(backup), added_color_rows=list(NEW_PALETTES), modified_scope='Only six palette entries plus tuple-order comment; no criterion/generator/frozen file changed', color_rationale=[
        {'url':'https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.144','claim':'Safety yellow is informed by the official caution/physical-hazard marking rule. It is not a claim of code compliance.'},
        {'url':'https://industrial.sherwin-williams.com/na/us/en/protective-marine/color/color-library.html','claim':'Manufacturer industrial protective-coating color library supports using steel greys and colored coatings as plausible industrial variation.'},
        {'source':'FACTORY80_MASTER_PROMPT.md §5.2.1','claim':'Blue, orange, galvanised appearance and green follow the user-provided palette examples. Exact RGB values are representative design targets, not calibrated RAL samples or mandatory stair safety colors.'}],
        observed_tuple_order=['RGB','tread_material','step_material','handrail_material'])
    table = read_palette_table(ALLOCATOR)
    palette_names = ['metal_glass', *NEW_PALETTES]
    for name in palette_names:
        require(dict(resolve_palette(name, table), staircase_family='straight', handrail_type='vertical-post', support_type='double-rail'))
    # Both controls invoke the production CLI, examine exit code and parsed FAIL.
    valid = dict(resolve_palette('metal_safety_yellow', table), staircase_family='straight', handrail_type='vertical-post', support_type='double-rail')
    marble = dict(valid, handrail_material='Marble')
    fake = dict(resolve_palette('metal_fake', {'metal_fake': ([.2,.2,.2], 'MetalBasic', 'Wood', 'BrushedMetal')}), staircase_family='straight', handrail_type='vertical-post', support_type='double-rail')
    controls = []
    for name, candidate in [('metal_tread_marble_handrail', marble), ('metal_fake_contains_wood', fake)]:
        source = evidence / (name + '_INPUT.json'); write(source, candidate)
        command = [sys.executable, str(scripts/'factory80_whitelist.py'), '--candidate', str(source)]
        proc = subprocess.run(command, capture_output=True, text=True)
        output = json.loads(proc.stdout)
        record = dict(name=name, command=command, returncode=proc.returncode, actual_output=output, stderr=proc.stderr, actual_executed=True, negative_control_status='PASS' if proc.returncode == 1 and output.get('status') == 'FAIL' else 'FAIL')
        controls.append(record)
        if record['negative_control_status'] != 'PASS':
            write(evidence/'WHITELIST_STOP_NEGATIVE_CONTROL.json', record)
            raise RuntimeError('STOP: actual negative control failed to report FAIL')
    # Names have no security significance: an arbitrary allowed palette name passes.
    arbitrary = dict(valid, dominant_palette='arbitrary_name')
    require(arbitrary)
    combinations = list(itertools.product(METALS, FAMILIES, RAILS, SUPPORTS))
    pilot = [(material, family, RAILS[(mi+fi)%2], SUPPORTS[(mi+fi)%3]) for mi, material in enumerate(METALS) for fi, family in enumerate(FAMILIES)]
    remainder = [combo for combo in combinations if combo not in pilot]
    random.Random(20261011).shuffle(remainder)
    sequence = pilot + remainder + combinations + combinations[:8]
    assert len(sequence) == 80
    rows = []
    for index, (tread, family, railing, support) in enumerate(sequence):
        palette = palette_names[index % len(palette_names)]
        row = dict(resolve_palette(palette, table), building=f'factory80_{index+1:03d}', staircase_family=family, tread_material=tread, handrail_type=railing, support_type=support,
                   native_seeds=[42+index*2, 43+index*2], planned_signature='|'.join((family, support, tread, railing, palette)),
                   work_state='PLANNED', validation_state='PREFLIGHT_ONLY', ACTUAL_SIGNATURE=None,
                   pilot=index < 6, required_pilot_storeys=2 if index < 6 else None,
                   same_building_stair_signature_policy='All stair assets use this signature/RGB; separate native seeds prevent identical stair objects',
                   actual_measurement_required=['generation_metrics.json.style: all three material factories','NATIVE_ROLE_ASSIGNMENT.json: actual face material roles','NATIVE_RGB.json: actual shader values','Isaac RTX images actually viewed and per-building judgment'],
                   unsupported='The signature/RGB targets do not establish actual exported signatures, geometry, gates, visual plausibility or acceptance.')
        require(row); rows.append(row)
    combo_counts = Counter((r['tread_material'], r['staircase_family'], r['handrail_type'], r['support_type']) for r in rows)
    pair_counts = Counter((r['tread_material'], r['staircase_family']) for r in rows)
    checks = dict(exact80=len(rows)==80, all36_coverage=len(combo_counts)==36, maximum_material_family20=max(pair_counts.values())<=20, first6_distinct_material_family=len({(r['tread_material'],r['staircase_family']) for r in rows[:6]})==6, all_metal_result_preflight=True)
    assert all(checks.values())
    plan = dict(schema='Factory80SignaturePlanV1', generated_at_utc=audit['generated_at_utc'], status='PLANNED_PREFLIGHT_PASS', seed=20261011,
                assignment_before_generation=True, palette_table=ref(ALLOCATOR), whitelist_validator=ref(scripts/'factory80_whitelist.py'),
                negative_controls=controls, allocation_checks=checks, actual_signature_rule='Only post-generation measured values may populate ACTUAL_SIGNATURE; RGB target labels are not actual palettes',
                counts=dict(material_family={f'{a}|{b}':c for (a,b),c in pair_counts.items()}, combinations={ '|'.join(k):v for k,v in combo_counts.items()}, palettes=dict(Counter(r['dominant_palette'] for r in rows))), rows=rows)
    write(TASK/'SIGNATURE_PLAN_FACTORY80.json', plan)
    audit.update(negative_controls=controls, all_metal_palette_count=len(palette_names), plan=ref(TASK/'SIGNATURE_PLAN_FACTORY80.json'), checks=checks, status='PASS', unsupported='This proves mutation scope, production validator controls and pre-generation coverage only.')
    write(evidence/'WHITELIST_FACTORY80_RESULT.json', audit)
    print(json.dumps({'status':audit['status'], 'allocator_after':audit['allocator_after'], 'negative_controls':[{'name':r['name'],'returncode':r['returncode'],'actual_status':r['actual_output']['status']} for r in controls], 'checks':checks,'counts':plan['counts'],'pilot_rows':rows[:6]},indent=2))

if __name__ == '__main__': main()
