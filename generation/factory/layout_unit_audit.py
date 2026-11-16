"""Source-bound v3.2 count, placement and coherent-unit audit; not physics."""
import collections
import numpy as np
from shapely.geometry import Point,box,shape
from density_counts import evaluate
from equipment_units import TEMPLATES
from unit_geometry import turned_bounds

def audit(config,domain,instances,pools,units,retained=(),bay_purposes=None):
    counts=evaluate(config,domain['theme'],domain,instances,bay_purposes);lookup={p['key']:p for p in pools};rules=list(counts['rules']);errors=collections.defaultdict(list)
    def add(name,passed,**data):rules.append(dict(rule=name,status='PASS' if passed else 'FAIL',**data))
    static=domain['static_collider_world_aabbs'];sb=np.asarray([r['bounds_xyz_m'] for r in static])
    objects=[*instances,*retained]
    def bounds_for(row):
        pieces=lookup[row['asset_id']].get('placement_mesh_bounds_m') if row['role']=='column_side_pipe_gallery' else None
        return [np.asarray(turned_bounds(p,row['yaw_deg']))+row['translation_m'] for p in pieces] if pieces else [np.asarray(row['world_union_bounds_m'])]
    for row in objects:
        source=lookup[row['asset_id']];label=row.get('prim_path',row['unit_id']+':'+row['asset_id'])
        if row['asset_class']!=source['class'] or row['source_root']!=source['root'] or row['artistic_scale']!=1:errors['source_class_pose_binding'].append(label)
        if not any(e['role']==row['role'] and e['domain']==row['domain'] for e in source['allowed_recipe_role_domain_bindings']):errors['source_role_domain_binding'].append(label)
        if row['role']=='visible_LED_fixture':continue
        lo,hi=np.asarray(row['world_union_bounds_m']);foot=box(lo[0],lo[1],hi[0],hi[1]);floor=row['floor'];region=shape(domain['regions'][row['domain']]['geometry'])
        if not region.covers(foot):errors['supported_region'].append(label)
        if row['asset_class']!='STRUCTURE' or row['role']=='column_side_pipe_gallery':
            if any(foot.intersection(box(*a['bounds'])).area>1e-9 for a in domain['reserved_aisles'] if a['floor']==floor):errors['main_and_both_XY_aisles'].append(label)
            if any(foot.distance(Point(*s['center_xy']))<s['radius_m'] for s in domain['stair_entry_exit_keepouts'] if s['floor']==floor):errors['stair_3m_keepout'].append(label)
            if any(foot.intersection(shape(d['geometry'])).area>1e-9 for d in domain['exterior_door_keepouts'] if d['floor']==floor):errors['exterior_door_3m_keepout'].append(label)
        floor_z=domain['floors'][floor]['top_z_m']
        if abs(lo[2]-floor_z)>2e-5 or hi[2]>=domain['roof_z_m'] or (row['domain']=='under_mezz' and hi[2]>=domain['upper_slab_bottom_z_m']):errors['actual_floor_roof_height'].append(label)
        height=hi[2]-lo[2]
        if height>domain['upper_slab_bottom_z_m'] and not (floor=='Floor1' and row['domain']=='double_height'):errors['tall_equipment_only_double_height'].append(label)
        if floor=='Floor2' and source.get('heavy_machine',False):errors['no_heavy_machine_upstairs'].append(label)
        if source['technical_tag']=='UNDER_MEZZ_ONLY':
            clearance=domain['upper_slab_bottom_z_m']-hi[2]
            if not (floor=='Floor1' and row['domain']=='under_mezz' and 0<=clearance<domain['frozen_body_height_m']):errors['actual_UNDER_MEZZ_ONLY_condition'].append(label)
        elif source['technical_tag']!='PASS':errors['admitted_V1_eligibility'].append(label)
        if row.get('retained_in_parent'):continue
        for al,ah in bounds_for(row):
            mask=np.all(np.minimum(ah,sb[:,1])-np.maximum(al,sb[:,0])>2e-5,axis=1)
            if np.any(mask):errors['source_mesh_or_union_static_overlap'].append(dict(instance=label,obstacles=[static[i]['prim_path'] for i in np.where(mask)[0]][:8]));break
    active=[r for r in objects if r['role']!='visible_LED_fixture'];pair_errors=[]
    for i,row in enumerate(active):
        lo,hi=np.asarray(row['world_union_bounds_m'])
        for other in active[i+1:]:
            ol,oh=np.asarray(other['world_union_bounds_m'])
            if not np.all(np.minimum(hi,oh)-np.maximum(lo,ol)>2e-5):continue
            actual=any(np.all(np.minimum(a[1],b[1])-np.maximum(a[0],b[0])>2e-5) for a in bounds_for(row) for b in bounds_for(other))
            if actual:pair_errors.append(dict(first=row['unit_id'],first_asset=row['asset_id'],second=other['unit_id'],second_asset=other['asset_id']))
    errors['new_instance_pair_overlap']=pair_errors
    members=collections.defaultdict(list)
    for row in active:members[row['unit_id']].append(row)
    for unit in units:
        expected=collections.Counter({r:n for r,n in TEMPLATES[unit['kind']][2].items() if n});actual=collections.Counter(r['role'] for r in members[unit['id']])
        if expected!=actual:errors['complete_or_coherent_partial_units'].append(dict(unit=unit['id'],kind=unit['kind'],expected=dict(expected),actual=dict(actual)))
    for key in ['source_class_pose_binding','source_role_domain_binding','supported_region','main_and_both_XY_aisles','stair_3m_keepout','exterior_door_3m_keepout','actual_floor_roof_height','tall_equipment_only_double_height','no_heavy_machine_upstairs','actual_UNDER_MEZZ_ONLY_condition','admitted_V1_eligibility','source_mesh_or_union_static_overlap','new_instance_pair_overlap','complete_or_coherent_partial_units']:
        add(key,not errors[key],failures=errors[key])
    role_domain=collections.Counter((r['domain'],r['role']) for r in active)
    required={'double_height':{'high_rack_group':4,'wrapped_pallet_load':4,'forklift_or_stacker':1,'AMR_trolley':1}}
    if domain['theme']=='A':
        required['double_height'].update(body_fixture=1,robot_arm=2,machine_guard_segment=4,straight_conveyor_line=1,electrical_cabinet=1)
        required['under_mezz']={'parts_washer_or_spray_booth':1,'welding_station':1,'air_compressor':1,'straight_conveyor':2,'curved_conveyor':1,'pallet_load':4,'tool_cabinet':2,'IBC':1}
        required['upper_storage_control']={'light_rack_group':4,'pallet':4,'bin':1,'storage_cabinet':1}
    else:
        required['double_height'].update(non_flat_top_tank=2,IBC=3,generator_set=1,fire_suppression_cylinder=1,column_side_pipe_gallery=1)
        required['under_mezz']={'air_compressor':2,'electrical_cabinet':2,'tool_cabinet':1,'IBC':1}
        required['upper_storage_control']={'control_or_electrical_cabinet':4,'workbench':2,'storage_cabinet':1,'light_rack_group':4,'pallet':4}
    missing=[dict(domain=d,role=r,required=n,actual=role_domain[(d,r)]) for d,roles in required.items() for r,n in roles.items() if role_domain[(d,r)]<n]
    add('required_theme_functional_groups',not missing,failures=missing)
    warehouse_errors=[]
    for bay in counts['bays']:
        if bay['floor']!='Floor1' or bay['purpose']!='WAREHOUSE':continue
        inside=[r for r in active if r['floor']=='Floor1' and box(*bay['bounds']).covers(Point(*np.mean(np.asarray(r['world_union_bounds_m']),axis=0)[:2]))]
        if sum(r['role']=='high_rack_group' for r in inside)<4:warehouse_errors.append(bay['id'])
    add('warehouse_bay_requires_actual_complete_rack_row',not warehouse_errors,failed_bays=warehouse_errors)
    return dict(schema='Factory80V32LayoutAuditV1',status='PASS' if all(r['status']=='PASS' for r in rules) else 'FAIL',rules=rules,counts=counts,scope='Source-bound actual geometry envelopes and mesh-level pipe bounds, coherent units and v3.2 layout; native physics/full building gates NOT INFERRED',batch_release=False)
