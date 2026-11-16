"""Independent exported-USD triangle readback: exact support rays and area samples."""
from pathlib import Path
import hashlib,json,math,sys,datetime,os
import numpy as np
from pxr import Usd,UsdGeom,UsdPhysics
ROOT=Path('/cephfs/shared/yixing/outputs/museum29_20261107')

def flat(T):
 n=np.cross(T[:,1]-T[:,0],T[:,2]-T[:,0]);return T[np.abs(n[:,2])>1e-12]
def hits(T,P,minimum=None,maximum=None):
 out=[];lo=T[:,:,:2].min(1) if len(T) else np.empty((0,2));hi=T[:,:,:2].max(1) if len(T) else np.empty((0,2))
 for q in P:
  mask=(lo[:,0]<=q[0]+1e-6)&(hi[:,0]>=q[0]-1e-6)&(lo[:,1]<=q[1]+1e-6)&(hi[:,1]>=q[1]-1e-6);S=T[mask]
  if not len(S):out.append(np.empty(0));continue
  a=S[:,0,:2];u=S[:,1,:2]-a;v=S[:,2,:2]-a;w=q-a;den=u[:,0]*v[:,1]-u[:,1]*v[:,0];valid=abs(den)>1e-12;den=np.where(valid,den,1)
  s=(w[:,0]*v[:,1]-w[:,1]*v[:,0])/den;t=(u[:,0]*w[:,1]-u[:,1]*w[:,0])/den
  inside=valid&(s>=-1e-5)&(t>=-1e-5)&(s+t<=1+1e-5);z=S[:,0,2]+s*(S[:,1,2]-S[:,0,2])+t*(S[:,2,2]-S[:,0,2])
  if minimum is not None:inside&=z>minimum
  if maximum is not None:inside&=z<maximum
  out.append(z[inside])
 return out
def main(form,out=None,source=None,collision=None):
 out=Path(out) if out else ROOT/'stair_tests'/os.environ.get('M29_STAIR_TEST_RUN','attempt002')/form;path=Path(collision) if collision else out/'collision_only.usdc';source=Path(source) if source else out/'MESH_SOURCE.json';spec=json.loads(source.read_text());st=Usd.Stage.Open(str(path));assert st
 assert UsdGeom.GetStageMetersPerUnit(st)==1 and UsdGeom.GetStageUpAxis(st)=='Z'
 named={};roles={};inventory=[];cache=UsdGeom.XformCache()
 for p in st.Traverse():
  if not p.HasAPI(UsdPhysics.CollisionAPI):continue
  assert p.IsA(UsdGeom.Mesh) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is True
  assert UsdPhysics.MeshCollisionAPI(p).GetApproximationAttr().Get()=='none'
  me=UsdGeom.Mesh(p);v=np.asarray(me.GetPointsAttr().Get(),dtype=np.float64);m=np.asarray(cache.GetLocalToWorldTransform(p));v=v@m[:3,:3]+m[3,:3];idx=np.asarray(me.GetFaceVertexIndicesAttr().Get());counts=me.GetFaceVertexCountsAttr().Get();tri=[];k=0
  for n in counts:
   poly=idx[k:k+n];k+=n;tri.extend((poly[0],poly[i],poly[i+1]) for i in range(1,n-1))
  T=v[np.asarray(tri)];assert np.isfinite(T).all();named[p.GetName()]=T;roles[p.GetName()]=p.GetAttribute('museum:role').Get();inventory.append({'name':p.GetName(),'triangles':len(T),'approximation':'none'})
 T=np.concatenate(list(named.values()));Tlo=T.min(1);Thi=T.max(1);allflat=flat(T)
 connections=[]
 for e in spec['exits']:
  z=e['floor_z_m'];d=np.array(e['direction_xy']);s=np.array(e['start_xy']);dist=np.arange(e['sampling_start_m'],e['sampling_end_m']+.0001,.01);P=s[None]+dist[:,None]*d
  floors=flat(np.concatenate([t for n,t in named.items() if roles[n]=='floor']));stair=flat(np.concatenate([t for n,t in named.items() if n.startswith('Stair_') or roles[n] in ['tread','stair','stair_wall','newel']]))
  def profile(A):
   A=np.concatenate([A,floors]);A=A[(A[:,:,2].min(1)<=z+.01)&(A[:,:,2].max(1)>=z-.01)]
   H=hits(A,P,z-.011,z+.011);Z=np.array([a.max() if len(a) else np.nan for a in H]);missing=np.isnan(Z);step=float(np.nanmax(np.abs(np.diff(Z)))) if np.isfinite(Z).sum()>1 else None
   edges=np.diff(np.r_[0,missing.astype(int),0]);lengths=(np.flatnonzero(edges==-1)-np.flatnonzero(edges==1))*.01
   return {'samples':len(P),'sample_interval_m':.01,'uncovered_samples':int(missing.sum()),'max_gap_m':float(max(lengths,default=0)),'max_adjacent_height_m':step,'passed':bool(not missing.any() and step is not None and step<=spec['rise_m']+1e-5)}
  pos=profile(stair);neg=profile(stair+np.array(e['negative_translation_xyz'])[None,None,:]);top=float(named[e['last_tread'].replace('-','N')][:,:,2].max());prefix=e['floor_prefix'].replace('-','N');floor_parts=[t for n,t in named.items() if n==prefix or n.startswith(prefix+'_Part')];assert floor_parts,'MISSING_ACTUAL_TARGET_FLOOR:'+prefix;floor=max(float(t[:,:,2].max()) for t in floor_parts);err=abs(top-floor)
  pos['top_tread_to_floor_error_m']=err;pos['passed'] &= err<=.01
  connections.append({'exit':e['name'],'positive':pos,'negative_control':{'translation_xyz_m':e['negative_translation_xyz'],'actual':neg,'status':'FAIL_AS_REQUIRED' if not neg['passed'] and neg['max_gap_m']>.1 else 'NEGATIVE_ESCAPE'}})
 route_rows=[]
 for route in spec['routes']:
  P=np.asarray(route['xy'])
  if 'fixed_z_m' in route:expected=np.full(len(P),route['fixed_z_m']);rise=spec['rise_m']
  else:
   stepidx=np.arange(len(P))*.01/route['going_m'];expected=route['start_z_m']+np.minimum(route['count'],np.floor(stepidx+1e-7)+1)*route['rise_m'];rise=route['rise_m']
  H=hits(allflat,P);Z=[]
  for values,target in zip(H,expected):
   values=values[(values>=target-(.0001 if 'fixed_z_m' in route else rise+.0001))&(values<=target+.0001)];Z.append(float(max(values)) if len(values) else np.nan)
  Z=np.asarray(Z);missing=int(np.isnan(Z).sum());delta=float(np.nanmax(abs(np.diff(Z)))) if len(Z)>1 else 0
  route_rows.append({'name':route['name'],'sample_interval_m':.01,'samples':len(Z),'uncovered_samples':missing,'maximum_adjacent_rise_m':delta,'passed':missing==0 and delta<=.17001})
 tchecks=[]
 for i,name in enumerate(spec['treads']):
  tt=named[name.replace('-','N')];top=float(tt[:,:,2].max());norm=np.cross(tt[:,1]-tt[:,0],tt[:,2]-tt[:,0]);up=tt[(norm[:,2]>1e-10)&(abs(tt[:,:,2].mean(1)-top)<1e-5)];assert len(up),'NO_UPWARD_TREAD_FACE'
  area=np.linalg.norm(np.cross(up[:,1]-up[:,0],up[:,2]-up[:,0]),axis=1)/2;rng=np.random.default_rng(20261107+i);ids=rng.choice(len(up),500,p=area/area.sum());a=np.sqrt(rng.random(500));b=rng.random(500);P=(1-a[:,None])*up[ids,0]+(a*(1-b))[:,None]*up[ids,1]+(a*b)[:,None]*up[ids,2]
  lo=P.min(0);hi=P.max(0);keep=(Tlo[:,0]<=hi[0])&(Thi[:,0]>=lo[0])&(Tlo[:,1]<=hi[1])&(Thi[:,1]>=lo[1])&(Thi[:,2]>top+.003)&(Tlo[:,2]<top+2.1);above=flat(T[keep])
  H=hits(above,P[:,:2],top+.003,top+1.6);clear=sum(not len(x) for x in H)/len(P)
  H21=hits(above,P[:,:2],top+.003,top+2.1);clear21=sum(not len(x) for x in H21)/len(P)
  tchecks.append({'name':name,'samples':500,'actual_top_m':top,'clear_1_6m_fraction':clear,'clear_2_1m_fraction':clear21,'passed':clear>=.9})
 c3=all(e['positive']['passed'] and e['negative_control']['status']=='FAIL_AS_REQUIRED' for e in connections) and all(x['passed'] for x in route_rows)
 c4=all(x['passed'] for x in tchecks)
 result={'schema':'Museum29StairTestChecksV1','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'form':form,'source_collision_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'source_mesh_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'validator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'C1':{'status':'PASS','colliders':len(named),'triangles':len(T),'units':'metres','up':'Z','all_exact':True,'all_finite':True},'C3':{'status':'PASS' if c3 else 'FAIL','connections':connections,'flight_support_routes':route_rows,'scope':'full building actual floors and stairs' if collision else 'isolated test fixture; building floors must be tested separately'},'C4':{'status':'PASS' if c4 else 'FAIL','threshold':.9,'headroom_m':1.6,'sampling':'500 area-weighted actual upward collision-face points per tread','minimum_clear_fraction':min(x['clear_1_6m_fraction'] for x in tchecks),'treads':tchecks},'status':'PASS' if c3 and c4 else 'FAIL'}
 (out/('C3_C4.json' if collision else 'CHECKS.json')).write_text(json.dumps(result,indent=2));print(form,result['status'],'C3',result['C3']['status'],'C4',result['C4']['minimum_clear_fraction'],flush=True)
 return result['status']=='PASS'
if __name__=='__main__':
 if sys.argv[1:2]==['--building']:
  sys.exit(0 if main(sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5]) else 2)
 ok=True
 for form in sys.argv[1:] or list('HAIWC'):ok=main(form) and ok
 sys.exit(0 if ok else 2)
