"""Coherent complete/partial v3.2 units; all original mesh sizes preserved."""
import collections,hashlib,math
import numpy as np
from unit_geometry import blueprint as original_blueprint,turned_bounds

def rank(seed,*parts):return hashlib.sha256('|'.join(map(str,(seed,*parts))).encode()).digest()

TEMPLATES={
 'warehouse_full':('double_height_warehouse','double_height',dict(high_rack_group=12,wrapped_pallet_load=12,forklift_or_stacker=1,AMR_trolley=1)),
 'warehouse_row':('double_height_warehouse','double_height',dict(high_rack_group=4,wrapped_pallet_load=4,forklift_or_stacker=0,AMR_trolley=0)),
 'warehouse_handling':('double_height_warehouse','double_height',dict(forklift_or_stacker=1,AMR_trolley=1)),
 'welding_cell':('double_height_welding','double_height',dict(straight_conveyor_line=1,body_fixture=1,robot_arm=3,machine_guard_segment=4,electrical_cabinet=0)),
 'welding_cell_controlled':('double_height_welding','double_height',dict(straight_conveyor_line=1,body_fixture=1,robot_arm=3,machine_guard_segment=4,electrical_cabinet=1)),
 'power_full':('double_height_power','double_height',dict(non_flat_top_tank=4,IBC=6,generator_set=2,fire_suppression_cylinder=2,column_side_pipe_gallery=1)),
 'maintenance':('under_mezz_maintenance','under_mezz',dict(parts_washer_or_spray_booth=1,welding_station=1,air_compressor=1)),
 'pumps_full':('under_mezz_pumps','under_mezz',dict(air_compressor=2,electrical_cabinet=2,tool_cabinet=1,IBC=1)),
 'pump_service_row':('under_mezz_pumps','under_mezz',dict(electrical_cabinet=2,tool_cabinet=1,IBC=1)),
 'sort_pack_full':('under_mezz_sort_pack','under_mezz',dict(straight_conveyor=2,curved_conveyor=1,pallet_load=4,tool_cabinet=2,IBC=1)),
 'sort_conveyor_straight':('under_mezz_sort_pack','under_mezz',dict(straight_conveyor=1)),
 'sort_conveyor_curve':('under_mezz_sort_pack','under_mezz',dict(curved_conveyor=1)),
 'packing_row':('under_mezz_sort_pack','under_mezz',dict(pallet_load=4,tool_cabinet=2,IBC=1)),
 'upper_storage_full':('mezz_storage','upper_storage_control',dict(light_rack_group=8,pallet=8,bin=8,storage_cabinet=2)),
 'upper_rack_row':('mezz_storage','upper_storage_control',dict(light_rack_group=4,pallet=4,bin=0,storage_cabinet=0)),
 'upper_bin_row':('mezz_storage','upper_storage_control',dict(bin=4)),
 'upper_cabinet_pair':('mezz_storage','upper_storage_control',dict(storage_cabinet=2)),
 'upper_rack_only_row':('mezz_storage','upper_storage_control',dict(light_rack_group=4)),
 'sorting_row':('under_mezz_sort_pack','under_mezz',dict(straight_conveyor=2,pallet_load=2)),
 'sorting_curve_row':('under_mezz_sort_pack','under_mezz',dict(curved_conveyor=2,pallet_load=2)),
 'packing_load_row':('under_mezz_sort_pack','under_mezz',dict(pallet_load=4,IBC=1)),
 'upper_control':('mezz_control','upper_storage_control',dict(control_or_electrical_cabinet=4,workbench=2,storage_cabinet=2)),
}

class Allocator:
 def __init__(self,pools,seed):self.pools=pools;self.seed=seed;self.used=collections.Counter()
 def ingredients(self,kind,serial,salt=0):
    zone,domain,roles=TEMPLATES[kind];chosen={};temporary=self.used.copy()
    for role,number in roles.items():
      chosen[role]=[]
      for i in range(number):
       candidates=[p for p in self.pools if any(e['role']==role and e['domain']==domain for e in p['allowed_recipe_role_domain_bindings']) and (p['capacity'] is None or temporary[p['key']]<p['capacity']) and (domain!='upper_storage_control' or not p.get('heavy_machine',False))]
       if not candidates:raise ValueError('ROLE_CAPACITY_EXHAUSTED:'+role)
       def key(p):
        lo,hi=np.asarray(p['actual_collision_and_visual_bounds_m']);size=hi-lo
        machine_penalty=(p['class']!='MACHINE') if role=='body_fixture' else 0
        compact_roles=['high_rack_group','wrapped_pallet_load']+(['non_flat_top_tank','IBC','generator_set'] if kind=='power_full' else [])
        dimensional=size[0]*size[1] if role in compact_roles+['light_rack_group','forklift_or_stacker'] else 0
        compact_first=role in compact_roles and salt==0
        ordinary=(machine_penalty,dimensional if compact_first else temporary[p['key']],temporary[p['key']] if compact_first else (dimensional if salt==0 else 0),rank(self.seed,kind,serial,role,i,salt,p['key']))
        if domain=='under_mezz':
         return (-float(size[0]*size[1]),temporary[p['key']],rank(self.seed,kind,serial,role,i,salt,p['key']))
        if domain!='upper_storage_control':return ordinary
        # Selection heuristic only: use the already measured isolated-asset
        # V1 unreachable area, without changing its 4m2 PASS limit or classes.
        top=p.get('static_technical_record',{}).get('V1_static',{}).get('unreachable_top_area_total_m2',float('inf'))
        pallet_height=float(size[2]) if role=='pallet' else 0.
        triangle_cost=sum(m.get('triangles',0)for m in p.get('static_technical_record',{}).get('collision_methods',[]))
        if not triangle_cost:triangle_cost=float('inf')
        return (top,-float(size[0]*size[1]) if role=='light_rack_group' else pallet_height,triangle_cost,*ordinary)

       p=min(candidates,key=key);chosen[role].append(p);temporary[p['key']]+=1
    return dict(id=f'{kind}_{serial:04d}',kind=kind,zone=zone,domain=domain,floor='Floor2' if domain=='upper_storage_control' else 'Floor1',ingredients=chosen)
 def commit(self,unit):
    for values in unit['ingredients'].values():
      for p in values:self.used[p['key']]+=1

def blueprint(unit):
 kind=unit['kind'];roles=unit['ingredients']
 if kind in ['warehouse_full','warehouse_row','power_full','maintenance','pumps_full','pump_service_row','sort_pack_full','packing_row','upper_storage_full','upper_rack_row','upper_control']:
    out=original_blueprint(unit);out['kind']=kind;return out
 members=[]
 def dims(p,yaw=0):
    lo,hi=np.asarray(turned_bounds(p['actual_collision_and_visual_bounds_m'],yaw));return hi-lo
 def orientation(p):
    lo,hi=np.asarray(p['actual_collision_and_visual_bounds_m']);return 0 if (hi-lo)[0]>=(hi-lo)[1] else 90
 def add(p,role,cx,cy,yaw=None):
    if yaw is None:yaw=orientation(p)
    lo,hi=np.asarray(turned_bounds(p['actual_collision_and_visual_bounds_m'],yaw));t=np.r_[np.asarray([cx,cy])-(lo+hi)[:2]/2,-lo[2]]
    members.append(dict(asset_id=p['key'],asset_class=p['class'],role=role,source_root=p['root'],source_union_bounds_m=p['actual_collision_and_visual_bounds_m'],artistic_scale=1.0,yaw_deg=yaw,translation_m=t.tolist(),local_union_bounds_m=[(lo+t).tolist(),(hi+t).tolist()]))
 def row(items,role,y=0,gap=.35,x0=0):
    x=x0;deep=0
    for p in items:
      yaw=orientation(p);s=dims(p,yaw);add(p,role,x+s[0]/2,y+s[1]/2,yaw);x+=s[0]+gap;deep=max(deep,s[1])
    return x-gap,deep
 if kind in ['welding_cell','welding_cell_controlled']:
    fixture=roles['body_fixture'][0];fd=dims(fixture,orientation(fixture));arms=roles['robot_arm'];arm_sizes=[dims(p,orientation(p)) for p in arms]
    aw=sum(s[0] for s in arm_sizes)+.35*(len(arms)-1);ad=max(s[1] for s in arm_sizes)
    conveyor=roles['straight_conveyor_line'][0];cd=dims(conveyor,orientation(conveyor));w=max(fd[0],aw,cd[0])+2.0
    cy=cd[1]+.75;cell_depth=fd[1]+.8+ad+1.6
    add(conveyor,'straight_conveyor_line',w/2,cd[1]/2)
    add(fixture,'body_fixture',w/2,cy+.8+fd[1]/2)
    row(arms,'robot_arm',cy+.8+fd[1]+.8,x0=(w-aw)/2)
    guards=roles['machine_guard_segment']
    for i,p in enumerate(guards):
      yaw=orientation(p)+(90 if i>=2 else 0);s=dims(p,yaw)
      x,y=[(w/2,cy+s[1]/2),(w/2,cy+cell_depth-s[1]/2),(s[0]/2,cy+cell_depth/2),(w-s[0]/2,cy+cell_depth/2)][i]
      add(p,'machine_guard_segment',x,y,yaw)
    row(roles['electrical_cabinet'],'electrical_cabinet',0,x0=w+.5)
 elif kind=='warehouse_handling':
    _,depth=row(roles['forklift_or_stacker'],'forklift_or_stacker');row(roles['AMR_trolley'],'AMR_trolley',depth+.75)
 else:
    y=0
    for role,items in roles.items():
      if not items:continue
      _,depth=row(items,role,y);y+=depth+.65
 expected=collections.Counter({r:len(v) for r,v in roles.items() if v})
 assert collections.Counter(m['role'] for m in members)==expected
 for i,a in enumerate(members):
    lo,hi=np.asarray(a['local_union_bounds_m'])
    for b in members[i+1:]:
      bl,bh=np.asarray(b['local_union_bounds_m'])
      assert not np.all(np.minimum(hi,bh)-np.maximum(lo,bl)>2e-5),'INTERNAL_UNIT_OVERLAP:'+kind+':'+a['role']+'/'+b['role']
 v=np.asarray([p for m in members for p in m['local_union_bounds_m']]);return {k:v for k,v in unit.items() if k!='ingredients'}|dict(members=members,bounds_m=[v.min(0).tolist(),v.max(0).tolist()],external_column_pipe_assets=[])

def transform_members(unit,yaw,shift,lookup):
 a=math.radians(yaw);rotation=np.asarray([[math.cos(a),math.sin(a)],[-math.sin(a),math.cos(a)]])
 out=[]
 for item in unit['members']:
    lo,hi=np.asarray(turned_bounds(item['local_union_bounds_m'],yaw))+shift;t=np.asarray(item['translation_m']).copy();t[:2]=t[:2]@rotation;t+=shift
    source=lookup[item['asset_id']]
    out.append(dict(item,floor=unit['floor'],domain=unit['domain'],zone=unit['zone'],unit_id=unit['id'],unit_kind=unit['kind'],translation_m=t.tolist(),yaw_deg=(item['yaw_deg']+yaw)%360,source_union_bounds_m=source['actual_collision_and_visual_bounds_m'],world_union_bounds_m=[lo.tolist(),hi.tolist()]))
 return out
