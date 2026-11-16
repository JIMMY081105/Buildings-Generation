"""v3.2 preregistered height-filtered density and real individual AABB footprints."""
import copy,collections
from shapely.geometry import Point,box
from shapely.ops import unary_union
from area_count_rules import placeable_regions,evaluate as previous_evaluate

def eligible(row):
    lo,hi=row['world_union_bounds_m']
    return row['asset_class']!='STRUCTURE' and hi[2]-lo[2]>=0.5

def footprint(row):
    lo,hi=row['world_union_bounds_m'];return box(lo[0],lo[1],hi[0],hi[1])

def evaluate(config,theme,domain,instances,bay_purposes=None):
    assert config['schema']=='Factory80PipelineConfig20261021V32' and config['version']=='3.2.0'
    assert config['layout_rules']['eligible_equipment_minimum_actual_height_m']==0.5
    selected=[r for r in instances if eligible(r)]
    shim=copy.deepcopy(config);shim['schema']='Factory80PipelineConfig20261020V31'
    result=previous_evaluate(shim,theme,domain,selected,bay_purposes)
    repeats=collections.Counter(r['asset_id'] for r in instances if r['asset_class']!='STRUCTURE')
    for r in result['rules']:
        if r['rule']=='per_asset_repeat_cap':r.update(status='PASS' if all(n<=r['cap'] for n in repeats.values()) else 'FAIL',actual_counts=dict(repeats),low_props_also_counted=True)
    areas=placeable_regions(config,domain);rules=result['rules']
    for name,region in areas.items():
        members=[r for r in selected if r['domain']==name];a=sum(footprint(r).area for r in members);net=region['placeable_area_m2'];limit=config['layout_rules']['region_footprint_fraction_min']
        rules.append(dict(rule='region_footprint|'+name,status='PASS' if a>=net*limit else 'FAIL',equipment_count=len(members),individual_AABB_sum_m2=a,placeable_area_m2=net,actual_fraction=a/net if net else None,required_fraction_min=limit))
    br=config['layout_rules']['bay_occupancy_v32']
    for b in result['bays']:
        floor=b['floor'];bounds=b['bounds'];cell=box(*bounds);cx=(bounds[0]+bounds[2])/2;cy=(bounds[1]+bounds[3])/2;center=box(cx-5,cy-5,cx+5,cy+5)
        members=[r for r in selected if r['floor']==floor];polys=[footprint(r) for r in members]
        a=sum(p.intersection(cell).area for p in polys);ca=sum(p.intersection(center).area for p in polys)
        reserved=unary_union([r['reserved'] for r in areas.values() if r['floor']==floor]);exempt=reserved.covers(Point(cx,cy));active=b['placeable_area_m2']>=br['minimum_placeable_area_m2']
        b.update(individual_AABB_sum_m2=a,footprint_fraction=a/b['placeable_area_m2'] if b['placeable_area_m2'] else None,footprint_status='PASS' if not active or a>=b['placeable_area_m2']*br['footprint_fraction_min'] else 'FAIL',center_bounds=list(center.bounds),center_footprint_m2=ca,center_exempt=exempt,center_rule_applies=active and not exempt,center_status='PASS' if not active or exempt or ca>=br['center_equipment_footprint_min_m2'] else 'FAIL')
    for key,name in [('footprint_status','every_eligible_bay_footprint'),('center_status','every_eligible_bay_center_footprint')]:
        rules.append(dict(rule=name,status='PASS' if all(b[key]=='PASS' for b in result['bays']) else 'FAIL',failed_bays=[b['id'] for b in result['bays'] if b[key]=='FAIL']))
    result.update(schema='Factory80V32AreaCountFootprintEvaluationV1',status='PASS' if all(r['status']=='PASS' for r in rules) else 'FAIL',all_equipment_total=sum(r['asset_class']!='STRUCTURE' for r in instances),low_equipment_excluded_from_counts=sum(r['asset_class']!='STRUCTURE' and not eligible(r) for r in instances),scope='v3.2 actual height-filtered counts, individual world-AABB footprint sums, bay-center rules. Placement/source/functional-unit/native physics separately verified.')
    return result
