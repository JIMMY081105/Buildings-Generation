"""Actual frozen voxel comparison for owned wall triangle partition fixtures."""
import argparse,fcntl,hashlib,importlib.util,json,os,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
W=Path('/cephfs/shared/yixing/outputs/factory80_20261011');CPU='/cephfs/shared/yixing/envs/main/bin/python'
REPO=Path('/cephfs/shared/yixing/code/Scenesmith-code-study/ProtoMotions-main')
FROZEN=REPO/'protomotions/simulator/isaaclab/utils/scene_geometry_baking.py'
EXPECTED_FROZEN='2c12e261e80ded1351610083562425087833f3d3159c39c4175d9d268f73e96e'
CASES=[dict(name='front_panel_40m',lo=[0,0,0],hi=[40,.12,9]),
       dict(name='front_panel_56m_translated',lo=[44,0,0],hi=[100,.12,9]),
       dict(name='side_panel_20m_translated',lo=[0,.12,0],hi=[.12,20.12,9])]
def now():return datetime.now(timezone.utc).isoformat()
def ref(p):
 p=Path(p);h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
 return dict(path=str(p),bytes=p.stat().st_size,sha256=h.hexdigest())
def read(p):return json.loads(Path(p).read_text())
def write(p,d):
 with Path(p).open('x') as f:json.dump(d,f,indent=2);f.write('\n')
def load(p,name):
 spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def check_stop():
 for p in W.glob('CRITICAL*.json'):
  if read(p).get('status')=='ENTIRE_TASK_STOP_REQUIRED':raise RuntimeError('ENTIRE_TASK_STOP_REQUIRED')
 p=W/'work/gate_audit/CPU_PIPELINE_STOP_AFTER_PILOT.json'
 if p.is_file() and read(p).get('status')=='PILOT6_WAIT_FOR_USER':raise RuntimeError('PILOT6_WAIT_FOR_USER')
def child(a):
 import numpy as np
 from pxr import Gf,Usd,UsdGeom,UsdPhysics,UsdShade
 sys.path.insert(0,str(REPO))
 from protomotions.simulator.isaaclab.utils.scene_geometry_baking import _authored_mesh_geometry_m,make_aligned_grid_spec,rasterize_triangle_mesh
 assert ref(FROZEN)['sha256']==EXPECTED_FROZEN
 old=load(a.baseline_builder,'baseline_owned_wall_author');new=load(a.candidate_builder,'candidate_owned_wall_author')
 if a.mode=='author':
  cases=[]
  for case in CASES:
   folder=a.output/case['name'];folder.mkdir()
   records={}
   for label,module in [('original',old),('partitioned',new)]:
    stage=Usd.Stage.CreateNew(str(folder/(label+'.usda')));root=UsdGeom.Xform.Define(stage,'/World').GetPrim();stage.SetDefaultPrim(root)
    UsdGeom.SetStageMetersPerUnit(stage,1);UsdGeom.SetStageUpAxis(stage,'Z')
    mat=module.material(stage,'/World/Looks/FactorySteel',(.15,.19,.22),1)
    group='/World/Floor1/Geometry/wall_0_0';visual=module.cube(stage,group+'/wall_0_0_visual_0',case['lo'],case['hi'],mat,False)
    collider=module.cube(stage,group+'/wall_0_0_collision_0',case['lo'],case['hi'],mat);collider.CreatePurposeAttr('guide')
    if label=='original':record=dict(refinement=module.refine_synthetic_collider(collider))
    else:record=module.partition_synthetic_wall_collider(stage,collider,mat)
    stage.GetRootLayer().Save();records[label]=dict(stage=ref(folder/(label+'.usda')),authoring=record)
   cases.append(dict(case=case,**records))
  write(a.output/'ACTUAL_FIXTURE_INPUTS.json',dict(schema='Factory80ActualOwnedWallPartitionFixtureInputsV1',cases=cases,
   original_owned_author_source=ref(a.baseline_builder),partition_owned_author_source=ref(a.candidate_builder),frozen_voxelizer_source=ref(FROZEN),
   not_a_candidate_or_gate_negative=True,acceptance_claim=False))
 elif a.mode in ('original','partitioned'):
  inputs=read(a.output/'ACTUAL_FIXTURE_INPUTS.json');entry=next(r for r in inputs['cases'] if r['case']['name']==a.case)
  folder=a.output/a.case;source=entry[a.mode]['stage'];assert ref(source['path'])==source
  stage=Usd.Stage.Open(source['path']);cache=UsdGeom.XformCache(Usd.TimeCode.Default());pieces=[];all_paths=[]
  for p in stage.Traverse():
   if p.IsA(UsdGeom.Mesh) and p.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False:
    v,f=_authored_mesh_geometry_m(stage,p,1,cache)
    material=UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0]
    assert str(material.GetPath())=='/World/Looks/FactorySteel'
    pieces.append((v,f));all_paths.append(str(p.GetPath()))
  lo=np.min(np.concatenate([v for v,f in pieces]),axis=0);hi=np.max(np.concatenate([v for v,f in pieces]),axis=0)
  grid=make_aligned_grid_spec(lo,hi);occ=np.zeros(grid.fine_shape,dtype=np.bool_)
  for v,f in pieces:rasterize_triangle_mesh(occ,grid,v,f,fill=False)
  actual_indices=np.argwhere(occ).astype(np.int32);np.save(folder/(a.mode+'_occupied_grid_indices.npy'),actual_indices,allow_pickle=False)
  write(folder/(a.mode+'_ACTUAL_FROZEN_RASTER_RESULT.json'),dict(schema='Factory80ActualOwnedWallFrozenRasterResultV1',mode=a.mode,source_stage=source,
   actual_enabled_collider_paths=all_paths,actual_occupied_cell_count=len(actual_indices),actual_occupied_grid_indices=ref(folder/(a.mode+'_occupied_grid_indices.npy')),
   grid_scene_bounds_min_m=list(grid.scene_bounds_min_m),grid_scene_bounds_max_m=list(grid.scene_bounds_max_m),grid_fine_shape=list(grid.fine_shape),
   actual_voxel_size_m=.05,actual_fill_argument=False,frozen_voxelizer=ref(FROZEN),acceptance_claim=False))
 elif a.mode=='compare':
  inputs=read(a.output/'ACTUAL_FIXTURE_INPUTS.json');comparisons=[]
  for entry in inputs['cases']:
   folder=a.output/entry['case']['name'];o=read(folder/'original_ACTUAL_FROZEN_RASTER_RESULT.json');p=read(folder/'partitioned_ACTUAL_FROZEN_RASTER_RESULT.json')
   for key in ('grid_scene_bounds_min_m','grid_scene_bounds_max_m','grid_fine_shape'):assert o[key]==p[key],key
   oi=np.load(o['actual_occupied_grid_indices']['path'],allow_pickle=False);pi=np.load(p['actual_occupied_grid_indices']['path'],allow_pickle=False)
   same=np.array_equal(oi,pi);oset={tuple(v) for v in oi};pset={tuple(v) for v in pi}
   original=Usd.Stage.Open(entry['original']['stage']['path']);partitioned=Usd.Stage.Open(entry['partitioned']['stage']['path'])
   path='/World/Floor1/Geometry/wall_0_0/wall_0_0_collision_0';mesh=UsdGeom.Mesh(original.GetPrimAtPath(path));cache=UsdGeom.XformCache(Usd.TimeCode.Default())
   v,f=_authored_mesh_geometry_m(original,mesh.GetPrim(),1,cache);expanded=v[f];rebuild=np.empty_like(expanded)
   partition=entry['partitioned']['authoring']['triangle_surface_partition']
   for part in partition['parts']:
    prim=partitioned.GetPrimAtPath(part['path']);pv,pf=_authored_mesh_geometry_m(partitioned,prim,1,cache)
    rebuild[part['original_wall_triangle_indices']]=pv[pf]
    assert str(UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0].GetPath())=='/World/Looks/FactorySteel'
   assert np.array_equal(expanded,rebuild),'Expanded world triangles changed'
   bounds_o=np.stack((expanded.reshape(-1,3).min(axis=0),expanded.reshape(-1,3).max(axis=0)))
   bounds_p=np.stack((rebuild.reshape(-1,3).min(axis=0),rebuild.reshape(-1,3).max(axis=0)))
   visualpath='/World/Floor1/Geometry/wall_0_0/wall_0_0_visual_0'
   ov,of=_authored_mesh_geometry_m(original,original.GetPrimAtPath(visualpath),1,cache)
   pv,pf=_authored_mesh_geometry_m(partitioned,partitioned.GetPrimAtPath(visualpath),1,cache)
   visual_same=np.array_equal(ov,pv) and np.array_equal(of,pf)
   volume_o=float(np.einsum('ij,ij->i',expanded[:,0],np.cross(expanded[:,1],expanded[:,2])).sum()/6)
   volume_p=float(np.einsum('ij,ij->i',rebuild[:,0],np.cross(rebuild[:,1],rebuild[:,2])).sum()/6)
   result=dict(case=entry['case'],actual_occupied_original=len(oi),actual_occupied_partitioned=len(pi),missing_voxel_centers=len(oset-pset),extra_voxel_centers=len(pset-oset),
    exact_5cm_occupied_center_set_equal=same,expanded_world_triangles_exactly_equal=True,bounds_exactly_equal=bool(np.array_equal(bounds_o,bounds_p)),
    signed_volume_before_m3=volume_o,signed_volume_after_m3=volume_p,signed_volume_exactly_equal=volume_o==volume_p,
    material_paths_equal=True,beauty_visual_vertices_faces_exactly_equal=visual_same,
    original_result=ref(folder/'original_ACTUAL_FROZEN_RASTER_RESULT.json'),partitioned_result=ref(folder/'partitioned_ACTUAL_FROZEN_RASTER_RESULT.json'))
   comparisons.append(result)
   assert same and result['bounds_exactly_equal'] and volume_o==volume_p and visual_same,'Fixture comparison actually failed'
  assert ref(FROZEN)['sha256']==EXPECTED_FROZEN
  write(a.output/'ACTUAL_EQUIVALENCE_RESULT.json',dict(schema='Factory80OwnedWallPartitionActualFrozenEquivalenceV1',utc=now(),status='ACTUAL_EXACT_FIXTURE_EQUIVALENCE',
   actual_comparisons=comparisons,input_manifest=ref(a.output/'ACTUAL_FIXTURE_INPUTS.json'),frozen_voxelizer_after=ref(FROZEN),
   measured_scope='The three explicitly authored representative wall fixture geometries only; no factory gate or whole-building memory improvement is established.',
   acceptance_claim=False,whole_building_memory_improvement='UNVERIFIED',actual_negative_control_executed=False))
 return 0
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline-builder',type=Path,required=True);p.add_argument('--candidate-builder',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 p.add_argument('--mode',choices=('parent','author','original','partitioned','compare'),default='parent');p.add_argument('--case');a=p.parse_args()
 if a.mode!='parent':return child(a)
 a.output.mkdir(parents=True,exist_ok=False);history=[];leases=[]
 write(a.output/'ACTUAL_REQUEST.json',dict(utc=now(),command=sys.argv,baseline_builder=ref(a.baseline_builder),candidate_builder=ref(a.candidate_builder),cases=CASES,
  source_stage_or_native_asset_changed=False,frozen_voxelizer=ref(FROZEN),actual_execution_complete=False))
 with (W/'work/OWNED_GPU_SERIAL.lock').open('a+') as gpu,(W/'work/gate_audit/CPU_HEAVY.lock').open('a+') as cpu:
  for name,handle in [('OWNED_GPU_SERIAL.lock',gpu),('CPU_HEAVY.lock',cpu)]:
   while True:
    check_stop()
    try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);break
    except BlockingIOError:time.sleep(3)
   leases.append(dict(lock=name,actual_flock_acquired=True,utc=now(),actual_owner_pid=os.getpid()))
  write(a.output/'ACTUAL_LEASES.json',dict(lock_order=['OWNED_GPU_SERIAL.lock','CPU_HEAVY.lock'],actual_acquisitions=leases))
  steps=[('author',None)]+[(mode,c['name']) for c in CASES for mode in ('original','partitioned')]+[('compare',None)]
  for index,(mode,case) in enumerate(steps):
   check_stop();cmd=[CPU,str(Path(__file__).resolve()),'--baseline-builder',str(a.baseline_builder),'--candidate-builder',str(a.candidate_builder),'--output',str(a.output),'--mode',mode]
   if case:cmd+=['--case',case]
   prefix=a.output/f'phase_{index:02d}_{mode}';started=now()
   with Path(str(prefix)+'.stdout').open('xb') as out,Path(str(prefix)+'.stderr').open('xb') as err:
    proc=subprocess.Popen(cmd,stdout=out,stderr=err,env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='2'))
    rc=proc.wait()
   result=dict(actual_linux_parent_wait_status=True,actual_phase_pid=proc.pid,actual_phase_returncode=rc,command=cmd,started_at_utc=started,finished_at_utc=now(),
    stdout=ref(str(prefix)+'.stdout'),stderr=ref(str(prefix)+'.stderr'))
   write(str(prefix)+'.EXECUTION.json',result);history.append(ref(str(prefix)+'.EXECUTION.json'))
   if rc!=0:
    write(a.output/'ACTUAL_FIXTURE_TERMINAL.json',dict(status='ORDINARY_FIXTURE_TOOL_OR_EQUIVALENCE_FAILURE',actual_phase_returncode=rc,actual_execution_refs=history,
     frozen_voxelizer=ref(FROZEN),deployment_allowed=False,no_gate_verdict_inferred=True));return rc if rc>=0 else 128-rc
  write(a.output/'ACTUAL_FIXTURE_TERMINAL.json',dict(status='ACTUAL_FIXTURE_COMPLETE',actual_execution_refs=history,actual_equivalence_result=ref(a.output/'ACTUAL_EQUIVALENCE_RESULT.json'),
   frozen_voxelizer=ref(FROZEN),acceptance_claim=False))
  print(json.dumps(dict(actual_equivalence_result=ref(a.output/'ACTUAL_EQUIVALENCE_RESULT.json'),actual_terminal=ref(a.output/'ACTUAL_FIXTURE_TERMINAL.json'))),flush=True)
 return 0
if __name__=='__main__':raise SystemExit(main())
