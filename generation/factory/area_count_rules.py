"""v3.1 literal area/count predicates. Geometry authoring/physics are separate."""
import collections,math
from shapely.geometry import Point,box,shape
from shapely.ops import unary_union

def placeable_regions(config,domain):
    result={}
    for name,region in domain['regions'].items():
        floor=region['floor'];supported=shape(region['geometry'])
        reserves=[box(*a['bounds']) for a in domain['reserved_aisles'] if a['floor']==floor]
        reserves += [Point(*s['center_xy']).buffer(s['radius_m'],quad_segs=256) for s in domain['stair_entry_exit_keepouts'] if s['floor']==floor]
        reserves += [shape(d['geometry']) for d in domain['exterior_door_keepouts'] if d['floor']==floor]
        excluded=unary_union(reserves).intersection(supported)
        available=supported.difference(excluded)
        result[name]=dict(floor=floor,supported=supported,reserved=excluded,available=available,supported_area_m2=supported.area,reserved_union_area_m2=excluded.area,placeable_area_m2=available.area)
    return result

def evaluate(config,theme,domain,instances,bay_purposes=None):
    assert config['schema']=='Factory80PipelineConfig20261020V31'
    equipment=[r for r in instances if r['asset_class']!='STRUCTURE']
    areas=placeable_regions(config,domain);rules=[]
    def add(name,passed,**data):rules.append(dict(rule=name,status='PASS' if passed else 'FAIL',**data))
    lim=config['layout_rules']['equipment_density_m2_per_instance']
    for name,region in areas.items():
        members=[r for r in equipment if r['domain']==name]
        count=len(members);area=region['placeable_area_m2']
        minimum=math.ceil(area/lim['maximum']-1e-9);maximum=math.floor(area/lim['minimum']+1e-9)
        add('region_density|'+name,minimum<=count<=maximum,placeable_area_m2=area,supported_region_area_m2=region['supported_area_m2'],reserved_union_area_m2=region['reserved_union_area_m2'],equipment_count=count,actual_m2_per_instance=area/count if count else None,required_integer_count_range=[minimum,maximum],required_m2_per_instance=[lim['minimum'],lim['maximum']])
    ground=[r for r in equipment if r['floor']=='Floor1'];machines=sum(r['asset_class']=='MACHINE' for r in ground)
    required=config['layout_rules']['machine_instance_fraction_min_by_theme'][theme]
    add('ground_MACHINE_fraction',bool(ground) and machines>=required*len(ground),MACHINE_count=machines,equipment_count=len(ground),actual_fraction=machines/len(ground) if ground else None,required_minimum=required)
    dressing=sum(r['asset_class']=='DRESSING' for r in equipment)
    add('whole_building_DRESSING_fraction',bool(equipment) and dressing<=config['layout_rules']['dressing_instance_fraction_max']*len(equipment),DRESSING_count=dressing,equipment_count=len(equipment),actual_fraction=dressing/len(equipment) if equipment else None,required_maximum=config['layout_rules']['dressing_instance_fraction_max'])
    repeats=collections.Counter(r['asset_id'] for r in equipment);cap=domain['area_scaled_equipment_repeat_cap']
    add('per_asset_repeat_cap',all(n<=cap for n in repeats.values()),cap=cap,actual_counts=dict(repeats),STRUCTURE_exempt=True)
    ground_shape=shape(domain['floors']['Floor1']['support_geometry']);xmin,ymin,xmax,ymax=ground_shape.bounds
    pitchx,pitchy=config['physical_v3']['bay_pitch_xy_m'];nx=round((xmax-xmin)/pitchx);ny=round((ymax-ymin)/pitchy)
    assert abs(nx*pitchx-(xmax-xmin))<1e-4 and abs(ny*pitchy-(ymax-ymin))<1e-4
    bybay=collections.defaultdict(list)
    for row in equipment:
        lo,hi=row['world_union_bounds_m'];cx=(lo[0]+hi[0])/2;cy=(lo[1]+hi[1])/2
        ix=min(nx-1,math.floor((cx-xmin)/pitchx));iy=min(ny-1,math.floor((cy-ymin)/pitchy))
        bybay[(row['floor'],ix,iy)].append(row)
    bays=[];bay_rule=config['layout_rules']['bay_occupancy_v31'];bay_purposes=bay_purposes or {}
    for floor in ['Floor1','Floor2']:
        floor_available=unary_union([r['available'] for r in areas.values() if r['floor']==floor])
        for ix in range(nx):
            for iy in range(ny):
                key=f'{floor}:{ix}:{iy}';bounds=[xmin+ix*pitchx,ymin+iy*pitchy,xmin+(ix+1)*pitchx,ymin+(iy+1)*pitchy]
                area=floor_available.intersection(box(*bounds)).area;members=bybay[(floor,ix,iy)];count=len(members)
                machine_count=sum(r['asset_class']=='MACHINE' for r in members);purpose=bay_purposes.get(key,'NONWAREHOUSE')
                active=area>=bay_rule['minimum_placeable_area_m2'];minimum=math.ceil(area/bay_rule['maximum_m2_per_equipment']-1e-9) if active else 0
                density_pass=count>=minimum
                # No arbitrary minimum area exception to the user's ground MACHINE rule.
                machine_applies=floor=='Floor1' and area>1e-6 and purpose!='WAREHOUSE'
                machine_pass=not machine_applies or machine_count>=bay_rule['nonwarehouse_ground_MACHINE_min']
                bays.append(dict(id=key,floor=floor,bay_xy=[ix,iy],bounds=bounds,purpose=purpose,placeable_area_m2=area,equipment_count=count,MACHINE_count=machine_count,actual_m2_per_equipment=area/count if count else None,required_equipment_min=minimum,density_status='PASS' if density_pass else 'FAIL',MACHINE_rule_applies=machine_applies,MACHINE_status='PASS' if machine_pass else 'FAIL'))
    add('no_empty_or_sparse_bay',all(r['density_status']=='PASS' for r in bays),failed_bays=[r['id'] for r in bays if r['density_status']=='FAIL'])
    add('two_MACHINE_per_nonwarehouse_ground_bay',all(r['MACHINE_status']=='PASS' for r in bays),failed_bays=[r['id'] for r in bays if r['MACHINE_status']=='FAIL'])
    return dict(schema='Factory80V31AreaCountEvaluationV1',status='PASS' if all(r['status']=='PASS' for r in rules) else 'FAIL',theme=theme,rules=rules,bays=bays,equipment_total=len(equipment),ground_equipment_count=len(ground),whole_building_class_counts=dict(collections.Counter(r['asset_class'] for r in equipment)),scope='v3.1 area/count checks only; actual placement/source/whole-unit/physics checks separate',batch_release=False)
