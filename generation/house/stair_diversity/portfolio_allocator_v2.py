"""Deterministic, capacity-first allocation. Produces reviewable candidates.

Family and seed/pose are selected by measured circulation cost. The only family
coverage constraint is one of each, required by the user's three-family Phase-3
prototype. There is no balanced family-count target. Materials follow geometry.
Phase 2 must measure the resulting palette and prove collision support; planned
palette labels are never presented as measurements of generated assets.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import platform

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from evidence_common import read_json, ref, require_phase, write_new

VERSION = 'native_capacity_portfolio_v2'
MAX_FACTORY_SEED_REUSE = 3
ALLOCATOR_SEED = 20260908
SUPPORTS = ('solid', 'side', 'hole', 'double-rail', 'single-rail')
RAILINGS = ('vertical-post', 'glass', 'horizontal-post')
PALETTES = {
    'light_wood': ([.77, .64, .46], 'Wood', 'Wood', 'Wood'),
    'warm_medium_wood': ([.58, .39, .24], 'Wood', 'Wood', 'Wood'),
    'dark_wood': ([.27, .17, .10], 'Wood', 'Wood', 'BrushedMetal'),
    'white_cream': ([.86, .83, .76], 'Wood', 'Plastic', 'Wood'),
    'grey': ([.55, .55, .56], 'MetalBasic', 'MetalBasic', 'Wood'),
    'stone_marble': ([.72, .70, .65], 'Marble', 'Marble', 'Wood'),
    'metal_glass': ([.54, .60, .65], 'BrushedMetal', 'MetalBasic', 'BrushedMetal'),
    'black': ([.13, .13, .14], 'Wood', 'MetalBasic', 'MetalBasic'),
}
FABRICS = ('FineKnitFabric', 'SofaFabric', 'CoarseKnitFabric', 'Leather')


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode('utf-8')


def tie(*parts):
    return int.from_bytes(hashlib.sha256('|'.join(map(str, (ALLOCATOR_SEED, *parts))).encode()).digest()[:4], 'big')/2**32


def solve(costs, constraints):
    row_indices = []; col_indices = []; data = []; lower = []; upper = []
    for i, (indices, lo, hi) in enumerate(constraints):
        for j in indices:
            row_indices.append(i); col_indices.append(j); data.append(1.)
        lower.append(lo); upper.append(hi)
    matrix = coo_matrix((data, (row_indices, col_indices)), shape=(len(constraints), len(costs))).tocsc()
    result = milp(np.asarray(costs), integrality=np.ones(len(costs)), bounds=Bounds(0, 1),
                  constraints=LinearConstraint(matrix, lower, upper),
                  options={'presolve': True, 'mip_rel_gap': 0.})
    if not result.success or result.x is None:
        raise RuntimeError(f'Allocation is infeasible or unsolved: {result.message}')
    selected = np.flatnonzero(result.x > .5).tolist()
    return selected


def require_measurements(path):
    doc = read_json(path)
    if doc['status'] != 'MEASURED' or len(doc['rows']) != 54 or any(r['status'] != 'MEASURED' for r in doc['rows']):
        raise RuntimeError(f'Measurement prerequisite is incomplete: {path}')
    return doc


def scene_evidence(palette, choice, palette_path):
    room = next(r for r in palette['candidate_rooms'] if r['room'] == choice['room'])
    floor = room['floor_material']
    if not floor or floor['status'] != 'MEASURED':
        raise RuntimeError('Selected room has no measured floor albedo')
    walls = [r['albedo']['mean_display_srgb'] for r in palette['walls']
             if r['material'] in room['adjacent_wall_material_paths'] and r['albedo'] and r['albedo']['status'] == 'MEASURED']
    if not walls:
        raise RuntimeError('Selected room has no measured wall albedo')
    return {'room': choice['room'], 'floor_material': floor['material'],
            'floor_albedo_srgb': floor['mean_display_srgb'],
            'floor_albedo_linear': floor['mean_linear_rgb'],
            'wall_materials': room['adjacent_wall_material_paths'],
            'wall_albedo_srgb': np.median(walls, axis=0).tolist(),
            'dominant_furniture_wood_tone_srgb': room['dominant_wood_tone_median_srgb'],
            'nearby_window_count': room['nearby_window_count'], 'lights': palette['lights'],
            'source': ref(palette_path)}


def geometry_selection(options_by_bid):
    # The native search measures joint room reservation, clear arrival, and both
    # frozen corridors. Counts do not enter the geometric objective. One of each
    # family is required solely by the explicit three-family prototype contract.
    families = {}; variables = []; costs = []; groups = defaultdict(list)
    for bid, options in sorted(options_by_bid.items()):
        best = options['rows'][0]
        families[bid] = {'individual_minimum_family': best['kind'], 'minimum_geometry_cost': best['score'],
                         'best_by_family': {kind: next((r for r in options['rows'] if r['kind'] == kind), None) for kind in ('straight', 'l_shaped', 'u_shaped')}}
        strata = Counter()
        for row in options['rows']:
            stratum = (row['kind'], row['native_support_type'], row['rotation_deg'], row['mirror'])
            if strata[stratum] >= 12:
                continue
            strata[stratum] += 1
            index = len(variables); variables.append((bid, row))
            costs.append((row['score']-best['score'])*10000 + tie('geometry', bid, row['seed'], row['rotation_deg'], row['mirror']))
            groups[('building', bid)].append(index)
            groups[('support', row['native_support_type'])].append(index)
            groups[('family', row['kind'])].append(index)
            groups[('factory_seed', row['seed'])].append(index)
            groups[('rotation', row['rotation_deg'])].append(index)
            groups[('mirror', row['mirror'])].append(index)
    constraints = [(groups[('building', bid)], 1, 1) for bid in sorted(options_by_bid)]
    constraints += [(groups[('support', kind)], 3, np.inf) for kind in SUPPORTS]
    constraints += [(indices, 0, MAX_FACTORY_SEED_REUSE) for (axis, _), indices in groups.items() if axis == 'factory_seed']
    constraints += [(groups[('family', kind)], 1, np.inf) for kind in ('straight', 'l_shaped', 'u_shaped')]
    constraints += [(groups[('rotation', value)], 1, np.inf) for value in (0, 90, 180, 270)]
    constraints += [(groups[('mirror', value)], 1, np.inf) for value in (False, True)]
    selected = solve(costs, constraints)
    chosen = {variables[i][0]: variables[i][1] for i in selected}
    for bid, row in chosen.items():
        families[bid].update({'selected_family': row['kind'], 'selected_geometry_cost': row['score'],
                              'cost_above_individual_minimum': row['score']-families[bid]['minimum_geometry_cost'],
                              'selection_rule': 'Minimum portfolio circulation cost over measured fits, subject to required support coverage, rotations/mirrors, and one of each family for the explicitly required Phase-3 prototype. No balanced family quota.'})
    return chosen, families


def style_cost(evidence, palette_name, railing):
    rgb = np.asarray(evidence['floor_albedo_srgb']); wall = np.asarray(evidence['wall_albedo_srgb'])
    wood = evidence['dominant_furniture_wood_tone_srgb']
    target = .65*rgb+.35*(np.asarray(wood) if wood is not None else wall)
    swatch = np.asarray(PALETTES[palette_name][0])
    warm = float(rgb[0]-rgb[2])
    cost = float(np.linalg.norm(swatch-target))
    if warm > .08 and palette_name in ('light_wood', 'warm_medium_wood', 'stone_marble', 'white_cream'):
        cost -= .16
    if warm <= .08 and palette_name in ('light_wood', 'white_cream', 'grey', 'metal_glass'):
        cost -= .12
    if np.mean(rgb) < .35 and palette_name in ('dark_wood', 'black', 'metal_glass'):
        cost -= .16
    conventional = 'marble' in evidence['floor_material'].lower() or warm > .12
    cost += ({'vertical-post': 0., 'glass': .06, 'horizontal-post': .08}[railing] if conventional
             else {'vertical-post': .03, 'glass': 0., 'horizontal-post': .01}[railing])
    return cost


def material_selection(geometry, evidence):
    variables = []; costs = []; groups = defaultdict(list)
    for bid, row in sorted(geometry.items()):
        for palette, (_, tread, step, handrail) in PALETTES.items():
            for railing in RAILINGS:
                signature = '|'.join((row['kind'], row['native_support_type'], tread, railing, palette))
                index = len(variables)
                variables.append((bid, {'dominant_palette': palette, 'tread_material': tread, 'step_material': step,
                                        'handrail_material': handrail, 'handrail_type': railing, 'design_signature': signature}))
                costs.append(style_cost(evidence[bid], palette, railing)*10000+tie('material', bid, palette, railing))
                groups[('building', bid)].append(index); groups[('palette', palette)].append(index)
                groups[('railing', railing)].append(index); groups[('signature', signature)].append(index)
                groups[('tread', tread)].append(index)
                if palette in ('dark_wood', 'black'):
                    groups[('dark', True)].append(index)
    constraints = [(groups[('building', bid)], 1, 1) for bid in sorted(geometry)]
    constraints += [(groups[('palette', name)], 0, 12) for name in PALETTES]
    constraints += [(groups[('railing', name)], 6, np.inf) for name in RAILINGS]
    constraints += [(indices, 0, 4) for (axis, _), indices in groups.items() if axis == 'signature']
    constraints += [(groups[('dark', True)], 0, 14)]
    constraints += [(groups[('tread', name)], 1, np.inf) for name in ('Wood', 'Marble', 'MetalBasic', 'BrushedMetal')]
    return {variables[i][0]: variables[i][1] for i in solve(costs, constraints)}


def validate(rows):
    counts = {axis: Counter(r[axis] for r in rows) for axis in ('staircase_family', 'support_type', 'handrail_type', 'dominant_palette', 'rotation_deg', 'mirror', 'design_signature')}
    checks = {'exact54': len(rows) == 54 and len({r['building'] for r in rows}) == 54,
              'maximum_signature_4': max(counts['design_signature'].values()) <= 4,
              'each_support_minimum3_planned_pending_phase2_physical_proof': all(counts['support_type'][s] >= 3 for s in SUPPORTS),
              'each_handrail_minimum6': all(counts['handrail_type'][s] >= 6 for s in RAILINGS),
              'palette_maximum12': max(counts['dominant_palette'].values()) <= 12,
              'dark_plus_black_maximum14': counts['dominant_palette']['dark_wood']+counts['dominant_palette']['black'] <= 14,
              'both_mirrors': set(counts['mirror']) == {False, True},
              'four_rotations': set(counts['rotation_deg']) == {0, 90, 180, 270},
              'no_fabric_or_leather_on_walking_surfaces': all(r[key] not in FABRICS for r in rows for key in ('step_material', 'tread_material')),
              'capacity_and_clear_corridors_measured': all(r['placement']['planning_only'] for r in rows),
              'factory_seed_reuse_maximum3': max(Counter(r['style_seed'] for r in rows).values()) <= MAX_FACTORY_SEED_REUSE}
    if counts['staircase_family']['straight'] >= 40:
        checks['straight_concentration_requires_measured_folded_family_deficits'] = all(
            all(r['family_capacity_diagnostics'][kind]['minimum_tread_footprint_deficit_m'] > 0 for kind in ('l_shaped', 'u_shaped'))
            for r in rows if r['staircase_family'] == 'straight')
    return {'checks': checks, 'counts': {k: {str(a):b for a,b in v.items()} for k,v in counts.items()},
            'status': 'PASS' if all(checks.values()) else 'FAIL'}


def choose_prototypes(rows):
    constraints = [(list(range(len(rows))), 6, 6)]
    for axis, values in [('staircase_family', ('straight', 'l_shaped', 'u_shaped')), ('handrail_type', RAILINGS),
                         ('tread_material', ('Wood', 'Marble', 'MetalBasic', 'BrushedMetal'))]:
        constraints += [([i for i,r in enumerate(rows) if r[axis] == value], 1, np.inf) for value in values]
    constraints.append(([i for i,r in enumerate(rows) if r['baseline']['G25'] == 'FAIL'], 1, np.inf))
    # Small exhaustive repair of a MILP choice is unnecessary: binary coverage
    # variables encode palette/height cardinality directly in one sparse model.
    costs = [tie('prototype', r['building']) for r in rows]
    full = []
    for ids, lo, hi in constraints:
        full.append(({i:1. for i in ids}, lo, hi))
    for axis, minimum in [('dominant_palette', 4), ('floor_height_m', 2)]:
        indicators = []
        for value in sorted({r[axis] for r in rows}):
            idx = len(costs); costs.append(0.); indicators.append(idx)
            members = {i:1. for i,r in enumerate(rows) if r[axis] == value}
            full.append(({**members, idx:-1.}, 0, np.inf))
            full.append(({**members, idx:-6.}, -np.inf, 0))
        full.append(({i:1. for i in indicators}, minimum, np.inf))
    rr=[]; cc=[]; vv=[]; lo=[]; hi=[]
    for i,(values,a,b) in enumerate(full):
        for j,v in values.items(): rr.append(i);cc.append(j);vv.append(v)
        lo.append(a);hi.append(b)
    matrix=coo_matrix((vv,(rr,cc)),shape=(len(full),len(costs))).tocsc()
    result=milp(costs,integrality=np.ones(len(costs)),bounds=Bounds(0,1),constraints=LinearConstraint(matrix,lo,hi),options={'mip_rel_gap':0.})
    if not result.success:
        raise RuntimeError('No six-building prototype covers the required diversity')
    return [rows[i]['building'] for i in range(len(rows)) if result.x[i]>.5]


def allocate(work, inputs):
    phase, options, palettes, palette_paths, dimensions = inputs
    geometry, families = geometry_selection(options)
    evidence = {bid: scene_evidence(palettes[bid], row, palette_paths[bid]) for bid,row in geometry.items()}
    material = material_selection(geometry, evidence)
    native = {(r['height_m'], r['kind'], r['seed']):r for r in dimensions['rows']}
    rows=[]
    for base in phase['rows']:
        bid=base['building']; placement=geometry[bid]; style=material[bid]
        cell=native[(base['height_m'], placement['kind'], placement['seed'])]
        row={'building':bid, 'staircase_family':placement['kind'], 'floor_height_m':base['height_m'],
             'style_seed':placement['seed'], 'assignment_seed':int(tie('assignment',bid)*2**32),
             'rotation_deg':placement['rotation_deg'], 'mirror':placement['mirror'],
             'support_type':placement['native_support_type'], **style,
             'native_factory_attributes_before_overrides':cell['attributes'],
             'planned_height_adaptation':cell['height_adaptation'], 'placement':placement,
             'scene_evidence':evidence[bid], 'family_choice':families[bid],
             'family_capacity_diagnostics':options[bid]['family_diagnostics'],
             'baseline':base['baseline'], 'source_usdz_sha256':base['source_usdz_sha256'],
             'palette_status':'PLANNED_TARGET_REQUIRES_NATIVE_BAKE_AND_FIXED_CAMERA_VERIFICATION',
             'rationale':f"The circulation-cost allocator selected {placement['kind']} in {placement['room']} from measured fits under the required prototype and support coverage. Clear entry/exit and its full planning footprint were measured. The {style['dominant_palette']} target uses the recorded floor, wall and furniture albedos; native {style['tread_material']} treads, {style['step_material']} structure and {style['handrail_material']} handrails implement the plan.",
             'fallback_order':['assigned_family','next_largest_measured_fitting_family','straight','keep_current_asset_last_resort'],
             'fallback_requires_new_evidence':True}
        rows.append(row)
    validation=validate(rows)
    if validation['status']!='PASS':
        raise RuntimeError('Plan violates allocation requirements: '+json.dumps(validation))
    prototypes=choose_prototypes(rows)
    return {'schema':'Residential54DiversityPortfolioPlanV1','status':'PASS',
            'generated_at_utc':phase['generated_at_utc'], 'generation_time_rule':'Frozen input audit timestamp; allocator output is reproducible, not stamped with rerun time.',
            'allocator_version':VERSION,'allocator_seed':ALLOCATOR_SEED,'allocator':ref(__file__),
            'solver_environment':{'python':platform.python_version(),'scipy':scipy.__version__,'numpy':np.__version__},
            'phase0':ref(work/'PHASE0_ROOT_CAUSE.json'),
            'input_measurements': [ref(work/'evidence'/name) for name in ('PHASE1_REFINED_GEOMETRY_R1.json','PHASE1_REFINED_CAPACITY_R1.json','PHASE1_REFINED_OPTIONS_R1.json','PHASE1_PALETTE_MEASUREMENTS_R1.json','NATIVE_ADAPTED_DIMENSIONS_R1.json')],
            'native_material_role_overrides':{'step_material':['step_surface','side_surface','rail_surface'],
                                              'handrail_material':['handrail_surface','post_surface'], 'tread_material':['tread_surface'],
                                              'note':'All are official factory attributes set before generation; glass surfaces remain native. No USD tinting.'},
            'geometry_sampling_rule': {'maximum_factory_seed_reuse': MAX_FACTORY_SEED_REUSE, 'reason': 'Prevent the circulation-cost optimizer from collapsing the native parameter reservoir to the narrowest seed; this is an allocator choice, not a physics or validation gate change.'},
            'walking_material_filter':{'excluded':list(FABRICS),'reason':'Fabric or leather on a structural step/tread is not a plausible residential walking surface.'},
            'support_collision_status':'All five planned; each must obtain actual unchanged Phase-2 normalization, rise and CoACD evidence before inclusion. No type is declared supported by this plan alone.',
            'palette_bucket_labels':list(PALETTES), 'prototype_buildings':prototypes, 'allocation_checks':validation, 'rows':rows}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--work',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    phase=require_phase(args.work/'PHASE0_ROOT_CAUSE.json')
    option_summary=require_measurements(args.work/'evidence/PHASE1_REFINED_OPTIONS_R1.json')
    palette_summary=require_measurements(args.work/'evidence/PHASE1_PALETTE_MEASUREMENTS_R1.json')
    options={r['building']:read_json(r['evidence']['path']) for r in option_summary['rows']}
    palette_paths={r['building']:Path(r['evidence']['path']) for r in palette_summary['rows']}
    palettes={bid:read_json(path) for bid,path in palette_paths.items()}
    inputs=(phase,options,palettes,palette_paths,read_json(args.work/'evidence/NATIVE_ADAPTED_DIMENSIONS_R1.json'))
    first=allocate(args.work,inputs); second=allocate(args.work,inputs)
    if canonical(first)!=canonical(second):
        raise RuntimeError('Allocator did not replay byte-identically')
    write_new(args.output,first)
    print(json.dumps({'status':first['status'],'byte_identical_replay':True,'sha256':hashlib.sha256(canonical(first)).hexdigest(),
                      'counts':first['allocation_checks']['counts'],'prototypes':first['prototype_buildings']},indent=2),flush=True)


if __name__=='__main__':
    main()
