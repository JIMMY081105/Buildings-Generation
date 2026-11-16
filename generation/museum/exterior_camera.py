"""Low three-quarter entrance facade camera fitted to real building bounds."""
import json,hashlib,numpy as np
from pathlib import Path

def fit(points,model):
 from scipy.spatial import ConvexHull
 points=np.asarray(points,dtype=float);points=points[ConvexHull(points).vertices];lo=points.min(0);hi=points.max(0);center=(lo+hi)/2;target=center.copy();target[2]=(lo[2]+hi[2])*.5
 door=next(q for q in model['doorways'] if q.get('kind')!='cloister_open_arch');c=np.array(door['center']);tangent=np.array(door['direction']);normal=np.array([-tangent[1],tangent[0]])
 if np.dot(normal,c-center[:2])<0:normal=-normal
 angle=55 if model['spec'].get('industrial27') else 30;camera_height=8. if model['spec'].get('industrial27') else 6.;horizontal=normal*np.cos(np.deg2rad(angle))+tangent*np.sin(np.deg2rad(angle));horizontal/=np.linalg.norm(horizontal);lens=28.;candidates=[]
 for distance in np.linspace(max(20.,np.linalg.norm(hi[:2]-lo[:2])*.6),300,900):
  eye=np.r_[center[:2]+horizontal*distance,camera_height];forward=target-eye;forward/=np.linalg.norm(forward);right=np.cross(forward,[0,0,1]);right/=np.linalg.norm(right);up=np.cross(right,forward);delta=points-eye;depth=delta@forward
  if depth.min()<.5:continue
  x=(delta@right)/depth*lens/36+.5;y=(delta@up)/depth*lens/20.25+.5;width=float(np.ptp(x))
  if .60<=width<=.85 and x.min()>=.035 and x.max()<=.965 and y.min()>=.035 and y.max()<=.965:
   candidates.append((abs(width-.72),eye.tolist(),width,[float(x.min()),float(y.min()),float(x.max()),float(y.max())]))
 if not candidates:
  for lens2 in [35.,45.,55.]:
   for height in [6.,8.]:
    for tz in [target[2],lo[2]+.46*(hi[2]-lo[2]),lo[2]+.54*(hi[2]-lo[2])]:
     t=target.copy();t[2]=tz
     for distance in np.linspace(max(25.,np.linalg.norm(hi[:2]-lo[:2])*.6),300,900):
      eye=np.r_[center[:2]+horizontal*distance,height];forward=t-eye;forward/=np.linalg.norm(forward);right=np.cross(forward,[0,0,1]);right/=np.linalg.norm(right);up=np.cross(right,forward);delta=points-eye;depth=delta@forward
      if depth.min()<.5:continue
      x=(delta@right)/depth*lens2/36+.5;y=(delta@up)/depth*lens2/20.25+.5;width=float(np.ptp(x))
      if .60<=width<=.85 and x.min()>=.035 and x.max()<=.965 and y.min()>=.035 and y.max()<=.965:candidates.append((abs(width-.72),eye.tolist(),width,[float(x.min()),float(y.min()),float(x.max()),float(y.max())],t.tolist(),lens2))
  assert candidates,'ADJUST16_NO_COMPLETE_LOW_CAMERA_FIT'
  chosen=min(candidates,key=lambda q:q[0]);_,eye,width,bounds,target,lens=chosen
  return {'eye':eye,'target':target,'lens':lens,'camera_height_m':eye[2],'building_width_fraction':width,'projected_bounds_normalized_xy':bounds,'actual_building_bounds_m':[lo.tolist(),hi.tolist()],'actual_building_points_sha256':hashlib.sha256(points.tobytes()).hexdigest(),'actual_source_entrance_center':c.tolist(),'status':'GEOMETRIC_FIT_REQUIRES_ACTUAL_IMAGE_REVIEW','composition':'ADJUST16 low6–8m three-quarter entrance facade, longer focal length fits complete real ground-up spire and roof outline, same strict60–85percent width and full-frame bounds.'}
 _,eye,width,bounds=min(candidates,key=lambda q:q[0]);return {'eye':eye,'target':target.tolist(),'lens':lens,'camera_height_m':camera_height,'building_width_fraction':width,'projected_bounds_normalized_xy':bounds,'actual_building_bounds_m':[lo.tolist(),hi.tolist()],'actual_building_points_sha256':hashlib.sha256(points.tobytes()).hexdigest(),'actual_source_entrance_center':c.tolist(),'status':'GEOMETRIC_FIT_REQUIRES_ACTUAL_IMAGE_REVIEW','composition':'ADJUST16 low6–8m three-quarter entrance facade, complete roof outline and side facade, actual building occupies60–85% width. Industrial27 uses8m and55deg side angle for prominent tooth ends.'}

def author(B,model):
 import bpy
 from mathutils import Vector
 bpy.context.view_layer.update()
 points=[]
 for ob in B.scene.objects:
  if ob.type!='MESH' or ob.hide_render or ob.get('museum_category')=='site' or ob.name.startswith(('Site_Plaza','SOURCE_')):continue
  a=np.empty(len(ob.data.vertices)*3);ob.data.vertices.foreach_get('co',a);a=a.reshape(-1,3);m=np.asarray(ob.matrix_world);points.extend(a@m[:3,:3].T+m[:3,3])
 q=fit(points,model);p=B.out/'EXTERIOR16_PLAN.json';assert not p.exists();q['raw_source_sha256']=hashlib.sha256(json.dumps(model,sort_keys=True).encode()).hexdigest();p.write_text(json.dumps(q,indent=2));return q
