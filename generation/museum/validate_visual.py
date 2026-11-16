"""Read actual visual USD faces/material bindings and 3-D railing centre lines."""
from pathlib import Path
import sys,json,hashlib,datetime,re
import numpy as np
from pxr import Usd,UsdGeom,UsdShade

def check(path):
 s=Usd.Stage.Open(str(path));assert s;cache=UsdGeom.XformCache();floor_materials=set();ceil=[];lines=[];groups={};meshes=0
 for p in s.Traverse(Usd.TraverseInstanceProxies()):
  if not p.IsA(UsdGeom.Mesh):continue
  img=UsdGeom.Imageable(p)
  if img.ComputeVisibility()=='invisible' or img.ComputePurpose()=='guide' or str(p.GetPath()).startswith('/World/Physics'):continue
  meshes+=1;name=p.GetName();me=UsdGeom.Mesh(p);v=np.array(me.GetPointsAttr().Get());M=np.array(cache.GetLocalToWorldTransform(p));v=(np.c_[v,np.ones(len(v))]@M)[:,:3]
  role=p.GetAttribute('museum:role').Get();counts=me.GetFaceVertexCountsAttr().Get();idx=np.array(me.GetFaceVertexIndicesAttr().Get());bound=UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0];mats=[str(bound.GetPath()) if bound else 'UNBOUND']*len(counts)
  for sub in UsdGeom.Subset.GetAllGeomSubsets(me):
   mat=UsdShade.MaterialBindingAPI(sub.GetPrim()).ComputeBoundMaterial()[0]
   if mat:
    for i in sub.GetIndicesAttr().Get():mats[i]=str(mat.GetPath())
  k=0
  for i,n in enumerate(counts):
   pts=v[idx[k:k+n]];k+=n
   if len(pts)<3:continue
   normal=np.cross(pts[1]-pts[0],pts[2]-pts[0]);length=np.linalg.norm(normal)
   if length<1e-12:continue
   nz=normal[2]/length
   if role=='floor' and nz>.5:floor_materials.add(mats[i])
   if nz<-.5 and ((role=='floor' and pts[:,2].mean()>1.) or role=='roof' or name.startswith('Coffer_')):ceil.append({'mesh':str(p.GetPath()),'face':i,'material_id':mats[i]})
  if not any(x in name for x in ['Handrail','_Top']) or 'Post' in name or 'WallHandrail' in name:continue
  if len(v)%8:continue
  centers=v.reshape(-1,8,3).mean(1)
  if abs(centers[-1,2]-centers[0,2])>.9*np.linalg.norm(centers[-1]-centers[0]):continue
  # Each connected tube has ordered 8-vertex cross sections. Break curved
  # tubes into 0.6 m+ nearly collinear runs, preserving their actual 3-D height.
  if name.startswith('AtriumGuard'):
   key=re.sub(r'_\d+_Top$','',name);groups.setdefault(key,[]).append((centers[0],centers[-1]));continue
  start=0
  for end in range(2,len(centers)+1):
   a=centers[start];b=centers[end-1];L=np.linalg.norm(b-a)
   error=np.max(np.linalg.norm(np.cross(centers[start:end]-a,(b-a)/max(L,1e-9)),axis=1))
   if error>.025 and end-start>2:
    b=centers[end-2]
    if np.linalg.norm(b-a)>.5:lines.append((str(p.GetPath())+f':{start}',a,b))
    start=end-2
  a=centers[start];b=centers[-1]
  if np.linalg.norm(b-a)>.5:lines.append((str(p.GetPath())+f':{start}',a,b))
 for name,segments in groups.items():
  # Merge only connected collinear old edge segments. Gaps remain gaps.
  pending=list(segments)
  while pending:
   a,b=pending.pop(0);changed=True
   while changed:
    changed=False
    for i,(c,d) in enumerate(pending):
     if np.linalg.norm(b-c)<.005:b=d;pending.pop(i);changed=True;break
   if np.linalg.norm(b-a)>.5:lines.append((name,a,b))
 bad=[];nearest=None
 for i,(name,a,b) in enumerate(lines):
  d=(b-a)/np.linalg.norm(b-a)
  for name2,c,e in lines[i+1:]:
   if name.split(':')[0]==name2.split(':')[0]:continue
   u=(e-c)/np.linalg.norm(e-c)
   if '_Rail' in name and '_Rail' in name2 and name.split('_Rail')[0]==name2.split('_Rail')[0]:continue
   if abs(np.dot(d,u))<np.cos(np.deg2rad(10)):continue
   overlap=min(np.linalg.norm(b-a),max(np.dot(c-a,d),np.dot(e-a,d)))-max(0,min(np.dot(c-a,d),np.dot(e-a,d)))
   if overlap<=.5:continue
   dist=float(np.linalg.norm(np.cross(c-a,d)));row={'a':name,'b':name2,'distance_3d_m':dist,'overlap_m':float(overlap)}
   if nearest is None or dist<nearest['distance_3d_m']:nearest=row
   if dist<1.0:bad.append(row)
 violations=[row for row in ceil if row['material_id'] in floor_materials or row['material_id']=='UNBOUND']
 return {'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source':str(path),'source_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'validator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'visible_meshes_examined':meshes,'V1_ceiling_not_floor':{'status':'PASS' if ceil and floor_materials and not violations else 'FAIL','floor_material_ids':sorted(floor_materials),'downward_ceiling_faces':len(ceil),'violations':violations},'V2_no_double_railing':{'status':'PASS' if lines and not bad else 'FAIL','angle_max_deg':10,'parallel_dot_threshold':float(np.cos(np.deg2rad(10))),'distance_threshold_m':1.,'overlap_threshold_m':.5,'segments':len(lines),'violation_pairs':bad,'nearest_parallel_pair':nearest,'scope':'ADJUST03 actual 3-D handrail geometry, excluding invisible/guide copies, vertical balusters, same-flight opposite rails and wall handrails'}}

if __name__=='__main__':
 path=Path(sys.argv[1]);dest=Path(sys.argv[2]);assert not dest.exists();result=check(path);dest.write_text(json.dumps(result,indent=2));print(json.dumps({k:{i:v for i,v in x.items() if i not in ['violations','violation_pairs']} if isinstance(x,dict) else x for k,x in result.items()},indent=2))
