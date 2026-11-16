"""Placement-only welding units: three real arms face their own real fixture."""
import copy,collections,json,math
from pathlib import Path
import numpy as np
from equipment_units import Allocator,TEMPLATES,rank,transform_members,blueprint as previous_blueprint
from unit_geometry import turned_bounds
P=Path('/cephfs/shared/yixing/outputs/factory80_20261011/pipeline')
AXIS_PATH=P/'evidence/V32_BATCH_WELDING_AXES_REVIEW_V1/AXIS_REVIEW.json'
def axes():
 d=json.loads(AXIS_PATH.read_text());assert d['status']=='ACTUAL_SOURCE_VIEWS_AND_WORKING_DIRECTION_REVIEWED';return {r['asset_id']:r for r in d['arms']}
def blueprint(unit):
 if unit['kind']not in ['welding_cell','welding_cell_controlled']:return previous_blueprint(unit)
 lookup=axes();roles=unit['ingredients'];members=[]
 def angle(p):
  lo,hi=np.asarray(p['actual_collision_and_visual_bounds_m']);return 0 if hi[0]-lo[0]>=hi[1]-lo[1]else 90
 def size(p,yaw):
  lo,hi=np.asarray(turned_bounds(p['actual_collision_and_visual_bounds_m'],yaw));return hi-lo
 def add(p,role,center,yaw):
  lo,hi=np.asarray(turned_bounds(p['actual_collision_and_visual_bounds_m'],yaw));t=np.r_[np.asarray(center)-(lo+hi)[:2]/2,-lo[2]];return add_pose(p,role,t,yaw)
 def add_pose(p,role,t,yaw,**extra):
  lo,hi=np.asarray(turned_bounds(p['actual_collision_and_visual_bounds_m'],yaw));row=dict(asset_id=p['key'],asset_class=p['class'],role=role,source_root=p['root'],source_union_bounds_m=p['actual_collision_and_visual_bounds_m'],artistic_scale=1.0,yaw_deg=float(yaw%360),translation_m=np.asarray(t).tolist(),local_union_bounds_m=[(lo+t).tolist(),(hi+t).tolist()],**extra);members.append(row);return row
 fixture=roles['body_fixture'][0];fy=angle(fixture);fd=size(fixture,fy);conveyor=roles['straight_conveyor_line'][0];cy=angle(conveyor);cd=size(conveyor,cy)
 # The fixture is the origin of the cell's task frame. All arm mount-to-tool
 # source vectors are rotated toward that same fixture, without articulating,
 # scaling, simplifying, or changing any source component.
 f=add(fixture,'body_fixture',[0.,0.],fy);flo,fhi=np.asarray(f['local_union_bounds_m'])
 for p,direction in zip(roles['robot_arm'],[np.array([-1.,0.]),np.array([1.,0.]),np.array([0.,1.])]):
  axis=lookup[p['key']];mount=np.asarray(axis['source_mount_center_m']);forward=np.asarray(axis['source_working_vector_xy']);source_angle=math.degrees(math.atan2(forward[1],forward[0]));desired=math.degrees(math.atan2(-direction[1],-direction[0]));yaw=(desired-source_angle)%360;a=math.radians(yaw);rot=np.asarray([[math.cos(a),math.sin(a)],[-math.sin(a),math.cos(a)]])
  ml,mh=np.asarray(turned_bounds(p['actual_collision_and_visual_bounds_m'],yaw));mount_xy=mount[:2]@rot;axis_index=int(abs(direction[1])>.5);toward=(mount_xy[axis_index]-ml[axis_index])if direction[axis_index]>0 else(mh[axis_index]-mount_xy[axis_index]);distance=fd[axis_index]/2+toward+.35
  for k in range(40):
   target=direction*(distance+k*.1);t=np.r_[target-mount_xy,-ml[2]];lo,hi=ml+t,mh+t
   if all(not np.all(np.minimum(hi,np.asarray(m['local_union_bounds_m'])[1])-np.maximum(lo,np.asarray(m['local_union_bounds_m'])[0])>2e-5)for m in members):break
  else:raise ValueError('WELDING_CELL_ARM_ENVELOPES_CANNOT_BE_SEPARATED')
  add_pose(p,'robot_arm',t,yaw,source_mount_center_m=mount.tolist(),source_working_vector_xy=forward.tolist(),own_fixture_asset_id=fixture['key'])
 # Keep the conveyor at the open feed side, within the same outer unit.
 low=min(m['local_union_bounds_m'][0][1]for m in members);add(conveyor,'straight_conveyor_line',[0.,low-.65-cd[1]/2],cy)
 for p in roles['electrical_cabinet']:
  py=angle(p);pd=size(p,py);allhi=np.max([m['local_union_bounds_m'][1]for m in members],axis=0);add(p,'electrical_cabinet',[allhi[0]+.35+pd[0]/2,low-.65-cd[1]/2],py)
 core=np.asarray([q for m in members for q in m['local_union_bounds_m']]);lo=core.min(0);hi=core.max(0);mid=(lo+hi)/2
 for p,side in zip(roles['machine_guard_segment'],['south','north','west','east']):
  yaw=angle(p)+(90 if side in ['west','east']else 0);s=size(p,yaw);center=mid[:2].copy()
  if side=='south':center[1]=lo[1]-.25-s[1]/2
  if side=='north':center[1]=hi[1]+.25+s[1]/2
  if side=='west':center[0]=lo[0]-.25-s[0]/2
  if side=='east':center[0]=hi[0]+.25+s[0]/2
  r=add(p,'machine_guard_segment',center,yaw);r['unit_outer_side']=side
 assert collections.Counter(m['role']for m in members)==collections.Counter({r:len(v)for r,v in roles.items()if v})
 for i,m in enumerate(members):
  lo,hi=np.asarray(m['local_union_bounds_m'])
  for n in members[i+1:]:
   nl,nh=np.asarray(n['local_union_bounds_m']);assert not np.all(np.minimum(hi,nh)-np.maximum(lo,nl)>2e-5),'WELDING_INTERNAL_PAIR_OVERLAP'
 pts=np.asarray([q for m in members for q in m['local_union_bounds_m']]);out={k:v for k,v in unit.items()if k!='ingredients'}|dict(members=members,bounds_m=[pts.min(0).tolist(),pts.max(0).tolist()],external_column_pipe_assets=[],welding_placement_revision='USER_20261023_ARMS_TO_OWN_FIXTURE_FOUR_GUARDS_OUTSIDE')
 proof=check_welding(out['members'],local=True);assert proof['status']=='PASS',proof;return out
def check_welding(members,local=False):
 field='local_union_bounds_m'if local else'world_union_bounds_m';errors=[];fixtures=[m for m in members if m['role']=='body_fixture'];arms=[m for m in members if m['role']=='robot_arm'];guards=[m for m in members if m['role']=='machine_guard_segment']
 if len(fixtures)!=1 or len(arms)!=3 or len(guards)!=4:return dict(status='FAIL',errors=['ORIGINAL_CELL_RECIPE_COVERAGE_MISMATCH'])
 center=np.mean(fixtures[0][field],axis=0)[:2];directions=[]
 for arm in arms:
  a=math.radians(arm['yaw_deg']);rot=np.asarray([[math.cos(a),math.sin(a)],[-math.sin(a),math.cos(a)]]);mount=np.asarray(arm['source_mount_center_m'])[:2]@rot+np.asarray(arm['translation_m'])[:2];front=np.asarray(arm['source_working_vector_xy'])@rot;target=center-mount;cosine=float(np.dot(front,target)/(np.linalg.norm(front)*np.linalg.norm(target)));directions.append(dict(asset=arm['asset_id'],cosine=cosine,mount_xy=mount.tolist(),fixture_xy=center.tolist()))
  if abs(cosine-1)>1e-8:errors.append('ARM_NOT_TOWARDS_OWN_FIXTURE:'+arm['asset_id'])
 core=np.asarray([q for m in members if m['role']!='machine_guard_segment'for q in m[field]]);lo=core.min(0);hi=core.max(0);sides=[]
 for g in guards:
  gl,gh=np.asarray(g[field]);actual=[]
  if gh[1]<=lo[1]+2e-5:actual.append('south')
  if gl[1]>=hi[1]-2e-5:actual.append('north')
  if gh[0]<=lo[0]+2e-5:actual.append('west')
  if gl[0]>=hi[0]-2e-5:actual.append('east')
  sides+=actual
  if len(actual)!=1:errors.append('GUARD_NOT_ON_ONE_UNIT_OUTER_SIDE:'+g['asset_id'])
 if collections.Counter(sides)!=collections.Counter(['south','north','west','east']):errors.append('FOUR_DISTINCT_OUTER_SIDES_REQUIRED')
 return dict(status='FAIL'if errors else'PASS',errors=errors,actual_arm_working_axis_proofs=directions,actual_guard_outer_sides=sides,source_articulation_scale_or_collision_changed=False)
