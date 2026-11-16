"""Select a camera on real retained floor, with exact triangle rays to last6 treads."""
from pathlib import Path
import os,json,hashlib,sys
for k in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','PXR_WORK_THREAD_LIMIT']:os.environ[k]='1'
import numpy as np
from pxr import Usd,UsdGeom,UsdPhysics
from shapely import wkt
from shapely.geometry import Point

def fit(d=None,model_override=None,max_height=3.7):
 d=Path(d) if d is not None else None;m=model_override if model_override is not None else json.loads((d/'RAW_MODEL.json').read_text());e=m['exits'][0];z=e['floor_z_m'];start=np.asarray(e['start_xy']);direction=np.asarray(e['direction_xy']);side=np.array([-direction[1],direction[0]]);floor=wkt.loads(m['floors'][e['floor_index']-1]['polygon_wkt']);prefix=e['last_tread'].rsplit('Tread',1)[0];treads=[q for q in m['parts'] if q['role']=='tread' and q['name'].startswith(prefix)];treads=sorted(treads,key=lambda q:q['name'])[-6:];targets=[]
 centers=[]
 for q in treads:
  v=np.asarray(q['vertices']);top=v[:,2].max();v=v[abs(v[:,2]-top)<1e-6];centers.append(np.r_[v[:,:2].mean(0),top+.02])
 centers=np.asarray(centers)
 for i,q in enumerate(treads):
  heading=centers[min(i+1,len(centers)-1),:2]-centers[max(i-1,0),:2];heading/=np.linalg.norm(heading);route=next(row for row in m['routes'] if row['name']==q['name'].rsplit('_Tread',1)[0]);targets.append(centers[i]-np.r_[heading*(route['going_m']/2-.02),0])
 station=next(float(t) for t in np.arange(0,e['landing_length_m']+2.,.01) if floor.covers(Point(*(start+direction*t)).buffer(.02)));joint=np.r_[start+direction*(station+.10),z+.02];assert floor.covers(Point(*joint[:2]));targets=np.asarray(targets);target=np.mean(np.vstack([targets,joint,joint]),0);eyes=[]
 for along in sorted(set([e['landing_length_m']+.35,e['landing_length_m']+.60,e['landing_length_m']+1.,e['landing_length_m']+1.5,e['landing_length_m']+2.,2.8,3.3,3.8,4.4,5.0])):
  for across in [-1.0,-.5,0,.5,1.0]:
   xy=start+direction*along+side*across
   if not floor.covers(Point(*xy).buffer(.25)):continue
   for height in [1.0,1.4,1.7,1.8,2.2,2.6,3.2,3.7]:
    if height<=max_height:eyes.append(np.r_[xy,z+height])
 # An exit can include a long landing; inspect retained floor beside the flight
 # as well as behind that landing, so a low oblique camera can see the treads.
 center=targets[:,:2].mean(0)
 for dx in [-4,-3,-2,-1,0,1,2,3,4]:
  for dy in [-4,-3.2,-2.6,-2.,-1.4,1.4,2.,2.6,3.2,4]:
   xy=center+[dx,dy]
   if not floor.covers(Point(*xy).buffer(.25)):continue
   for height in [1.0,1.4,1.7,1.8,2.2,2.6,3.2,3.7]:
    if height<=max_height:eyes.append(np.r_[xy,z+height])
 assert eyes;allpoints=np.vstack([eyes,targets,joint]);lo=allpoints.min(0)-.05;hi=allpoints.max(0)+.05;triangles=[];meshrows=[]
 if model_override is not None:
  for row in m['parts']:
   v=np.asarray(row['vertices'],dtype=float);f=np.asarray([(a[0],a[k],a[k+1]) for a in row['faces'] for k in range(1,len(a)-1)]);meshrows.append((v,f))
 else:
  stage=Usd.Stage.Open(str(d/'collision_package/collision_only.usdc'));xf=UsdGeom.XformCache()
  for p in stage.Traverse():
   if not p.HasAPI(UsdPhysics.CollisionAPI):continue
   me=UsdGeom.Mesh(p);v=np.asarray(me.GetPointsAttr().Get(),dtype=float);tr=np.asarray(xf.GetLocalToWorldTransform(p));v=v@tr[:3,:3]+tr[3,:3];meshrows.append((v,np.asarray(me.GetFaceVertexIndicesAttr().Get()).reshape(-1,3)))
 for v,faces in meshrows:
  if (v.max(0)<lo).any() or (v.min(0)>hi).any():continue
  t=v[faces];keep=(t.max(1)>=lo).all(1)&(t.min(1)<=hi).all(1);triangles.extend(t[keep])
 T=np.asarray(triangles);A=T[:,0];U=T[:,1]-A;V=T[:,2]-A
 def visible(eye,p):
  ray=p-eye;h=np.cross(ray,V);det=np.einsum('ij,ij->i',U,h);mask=abs(det)>1e-10;inv=np.divide(1.,det,out=np.zeros_like(det),where=mask);s=eye-A;u=inv*np.einsum('ij,ij->i',s,h);q=np.cross(s,U);v=inv*(q@ray);t=inv*np.einsum('ij,ij->i',V,q);hit=mask&(u>=-1e-6)&(v>=-1e-6)&(u+v<=1+1e-6)&(t>.001)&(t<.995);return not hit.any()
 candidates=[];diagnostic=[]
 for eye in eyes:
  sight=[visible(eye,p) for p in targets];floor_sight=visible(eye,joint)
  diagnostic.append({'eye':eye.tolist(),'visible_tread_centers':sum(sight),'floor_joint_visible':floor_sight})
  if sum(sight)<4 or not floor_sight:continue
  forward=target-eye;forward/=np.linalg.norm(forward);right=np.cross(forward,[0,0,1]);right/=np.linalg.norm(right);up=np.cross(right,forward)
  for lens in [14,16,18,20,22,26,30,34]:
   delta=np.vstack([targets,joint])-eye;depth=delta@forward;px=delta@right/depth*lens/18;py=delta@up/depth*lens/10.125
   if (abs(px)>.82).any() or (abs(py)>.78).any():continue
   size=float(np.ptp(px)*np.ptp(py));score=sum(sight)*10+size*4-abs(eye[2]-z-2.2)*.5;candidates.append({'score':score,'eye':eye.tolist(),'target':target.tolist(),'lens':lens,'last6_tread_front_band_clear_rays':sight,'tread_front_sampling_offset_inside_m':.02,'actual_floor_joint_axis_station_m':station+.10,'floor_joint_clear_ray':floor_sight,'actual_floor_footprint_radius_m':.25,'sampled_tread_names':[q['name'] for q in treads],'projected_target_span_normalized_xy':[float(np.ptp(px)),float(np.ptp(py))]})
 assert candidates,('NO_CLEAR_LAST4_TO6_TREADS_AND_JOINT_CAMERA_ON_ACTUAL_FLOOR',sorted(diagnostic,key=lambda q:-q['visible_tread_centers'])[:5]);best=max(candidates,key=lambda q:q['score']);best.update(status='PLANNED_SOURCE_RAYS_FINAL_IMAGE_REQUIRED',source_geometry_kind='RAW_NOMINAL_FOR_INTERMEDIATE_BLENDER_ONLY' if model_override is not None else 'ACTUAL_EXPORTED_COLLISION_TRIANGLES',source_collision_sha256=hashlib.sha256((d/'collision_package/collision_only.usdc').read_bytes()).hexdigest() if d is not None else None,source_raw_canonical_sha256=hashlib.sha256(json.dumps(m,sort_keys=True).encode()).hexdigest(),retained_triangle_count=len(T),candidate_count=len(candidates),scope='Source triangle line segments to6 top-tread centers, >=4 clear and real floor joint, camera footprint on retained floor. RAW option is intermediate preview only; final native plan uses actual exported CP and actual images remain mandatory.4–6 treads is visual contract; C1–C5 unchanged.');return best

if __name__=='__main__':
 d=Path(sys.argv[1]);q=fit(d);p=d/'JUNCTION22_CAMERA_PLAN.json';assert not p.exists();p.write_text(json.dumps(q,indent=2));print(json.dumps(q))
