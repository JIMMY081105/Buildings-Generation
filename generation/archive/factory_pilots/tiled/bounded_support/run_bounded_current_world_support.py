"""Explicit bounded resource producer; unchanged original scientific functions only.

No claim that precompute_one or rasterize_storey_bands was invoked. The first
producer must be qualified against a genuine original completed golden world.
"""
import argparse,hashlib,importlib,json,os,resource,shutil,sys,tempfile,traceback,zipfile
from pathlib import Path
from stat_hash_cache import StatHashCache
W=Path('/cephfs/shared/yixing/outputs/factory80_20261011');HERE=Path(__file__).resolve().parent
C=StatHashCache([W/'work/P1_HASH_CACHE_20261015.json',W/'work/CURRENT001_A1_NAV_INPUTS_20261015/ACTUAL_STAT_HASH_CACHE.json'])
def read(p):return json.loads(Path(p).read_bytes())
def require(ok,s):
 if not ok:raise ValueError(s)
def save(p,d):
 with Path(p).open('x')as f:json.dump(d,f,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
def checked(r):require(C.ref(r['path'])==r,'Actual producer source/input differs: '+r['path'])
def arrref(v):return dict(dtype=str(v.dtype),shape=list(v.shape),array_bytes_sha256=hashlib.sha256(memoryview(v).cast('B')).hexdigest())
def compare_array(name,actual,expected):
 import numpy as np
 require(actual.dtype==expected.dtype and actual.shape==expected.shape,'Golden array shape/dtype differs: '+name);different=0;a=hashlib.sha256();b=hashlib.sha256()
 for start in range(0,actual.shape[0],16):
  x=np.ascontiguousarray(actual[start:start+16]);y=np.ascontiguousarray(expected[start:start+16]);different+=int(np.count_nonzero(x!=y));a.update(memoryview(x).cast('B'));b.update(memoryview(y).cast('B'))
 require(different==0 and a.digest()==b.digest(),'Golden array bytes differ: '+name)
 return dict(array=name,shape=list(actual.shape),dtype=str(actual.dtype),different_element_count=different,actual_sha256=a.hexdigest(),golden_sha256=b.hexdigest())
def main():
 Path('/proc/self/oom_score_adj').write_text('1000');require(Path('/proc/self/oom_score_adj').read_text().strip()=='1000','Own OOM priority readback differs')
 p=argparse.ArgumentParser(allow_abbrev=False);p.add_argument('--execute',action='store_true');p.add_argument('--reviewed-source-index-sha256',required=True);p.add_argument('--actual-inputs',type=Path,required=True);p.add_argument('--expected-actual-inputs-sha256',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--golden-original-finite',type=Path);p.add_argument('--expected-golden-sha256');p.add_argument('--qualified-resource-finite',type=Path);p.add_argument('--expected-resource-finite-sha256');a=p.parse_args()
 indexref=C.ref(HERE/'SOURCE_INDEX.json');require(indexref['sha256']==a.reviewed_source_index_sha256,'Explicit actual bounded source index differs');index=read(indexref['path']);own=[]
 for r in index['own_runtime_sources']:
  checked(r);require(Path(r['path']).parent==HERE,'Runtime source outside explicit reviewed bounded source tree');own.append(r)
 initial_ref=index['initial_resource_equivalence_ref'];checked(initial_ref);initial=read(initial_ref['path']);require(initial['all_array_bytes_exactly_equal']is True and initial['source_unchanged']is True and all(initial['metadata_checks'].values())and initial['original_scientific_functions_modified']is False,'Original-array resource experiment is not qualified')
 helper=next(r for r in own if Path(r['path']).name=='bounded_surface_raster.py');tested=next(r for r in initial['source_refs_before']if Path(r['path']).name=='bounded_surface_raster.py');require((helper['bytes'],helper['sha256'])==(tested['bytes'],tested['sha256']),'Resource face-partition helper differs from actual original-array experiment')
 resource_dependencies=index['resource_dependency_refs']
 for r in resource_dependencies:checked(r)
 closure_ref=index['original_closure_ref'];require(closure_ref['sha256']=='d862f26bc420a7ed5e3b77b765a872857fb1100ac2cdfa0ed09ca29012cf9286','Original22 closure differs');checked(closure_ref);science=read(closure_ref['path'])['actual_remote_original_source_refs'];require(len(science)==22,'Complete frozen22-source closure required')
 for r in science:checked(r)
 world_ixref=index['readonly_world_binding_index_ref'];require(world_ixref['sha256']=='c5c5222499e335e0514d2904698e5599e732cf2e17cd20a05be6e279625528ca','Reviewed current-world lineage binder differs');checked(world_ixref);worlddir=Path(world_ixref['path']).parent;binding_refs=[]
 for r in read(world_ixref['path'])['own_runtime_sources']:
  rr=C.ref(worlddir/str(r['path']).replace(chr(92),'/').rsplit('/',1)[-1]);require((rr['bytes'],rr['sha256'])==(r['bytes'],r['sha256']),'Actual current-world binder runtime differs');binding_refs.append(rr)
 input_ref=C.ref(a.actual_inputs);require(input_ref['sha256']==a.expected_actual_inputs_sha256,'Actual explicit bounded input differs');doc=read(a.actual_inputs)
 require(doc['schema']=='Factory80OwnedN2CurrentWorldScratchV1InputBindingsV1'and len(doc['rows'])==1,'One exact original current N2 world per guarded bounded job')
 row=doc['rows'][0];require(row['building']in ['factory80_001','factory80_002','factory80_003','factory80_004'],'Remaining19/005/006 promotion is gated on the actual003 native prerequisite')
 plan_ref=doc['original_n2_plan_ref'];checked(plan_ref);require(plan_ref['sha256']=='100c4fe2be62c7c8abe0043aed053cced40f69feeec4186047a32096a9510e78','Actual original29 N2 scope differs');mainrow=next(r for r in read(plan_ref['path'])['rows']if r['building']==row['building']);require(mainrow['storeys']==2 and row['attempt']in(1,2,3),'Only original N2 attempts1-3')
 require(bool(a.golden_original_finite)!=bool(a.qualified_resource_finite),'Choose genuine original golden qualification or this exact producer validated receipt')
 golden=None;qualification_ref=None;qualification_supervision=[]
 if a.golden_original_finite:
  qualification_ref=C.ref(a.golden_original_finite);require(qualification_ref['sha256']==a.expected_golden_sha256,'Actual original golden finite differs');golden_doc=read(a.golden_original_finite);require(golden_doc['all_scientific_functions_source_unchanged']and golden_doc['scientific_source_refs_before']==golden_doc['scientific_source_refs_after']==science,'Original golden scientific source changed');golden=next(r for r in golden_doc['rows']if(r['building'],r['attempt'])==(row['building'],row['attempt']));require(golden['source_bytes_unchanged']and golden['actual_original_scratch']['source_usdz']==row['root_ref']['path']and golden['actual_original_scratch']['source_usdz_sha256']==row['root_ref']['sha256'],'Original golden root differs from this current world')
 else:
  qualification_ref=C.ref(a.qualified_resource_finite);require(qualification_ref['sha256']==a.expected_resource_finite_sha256,'Actual completed resource qualification differs');qualification=read(a.qualified_resource_finite)
  require(qualification['schema']=='Factory80BoundedCurrentWorldScratchV1MeasurementsV1'and qualification['producer_source_index_ref']==indexref and qualification['source_unchanged']is True and qualification['all_scientific_functions_source_unchanged']is True and qualification['rows'][0].get('golden_qualification',{}).get('all_scientific_arrays_and_metadata_exact')is True,'No completed exact-source original-array qualification; no new world')
  parent=a.qualified_resource_finite.parent.parent;begin=read(parent/'BEGIN.json');wait=read(parent/'TRUE_WAIT.json');require(a.qualified_resource_finite==parent/'original_subject/FINITE_RESULT.json'and wait.get('actual_wait_completed')is True and wait.get('actual_signed_returncode')==0 and wait.get('actual_child_pid')==begin.get('child_pid')and begin['argv']==wait['argv']and begin['argv'][:3]==['/cephfs/shared/yixing/envs/main/bin/python','-u',str(HERE/'run_bounded_current_world_support.py')],'Resource qualification requires its genuine matching terminal BEGIN/TRUE_WAIT')
  require(begin['argv'][begin['argv'].index('--output')+1]==str(parent/'original_subject')and qualification['scientific_source_refs_before']==qualification['scientific_source_refs_after']==science,'Resource qualification source/output differs');qualification_supervision=[C.ref(parent/'BEGIN.json'),C.ref(parent/'TRUE_WAIT.json')]
 if not a.execute:print(json.dumps(dict(status='SOURCE_ONLY_NO_SCIENCE_EXECUTED',building=row['building'])));return 0
 require(os.environ.get('CUDA_VISIBLE_DEVICES')==''and os.stat('/tmp').st_dev!=os.stat(W).st_dev and shutil.disk_usage('/tmp').free>=5*1024**3,'CPU-only job with at least5GiB local ephemeral disk required')
 D=a.output.resolve();require(D.is_relative_to(W/'work')and not D.exists(),'Fresh explicit W/work output required');D.mkdir();folder=D/row['building']/f'attempt_{row["attempt"]:02d}';folder.mkdir(parents=True);tmp=Path(tempfile.mkdtemp(prefix='factory80_bounded_support_',dir='/tmp'));resource.setrlimit(resource.RLIMIT_AS,(12*1024**3,12*1024**3))
 byname={Path(r['path']).name:r for r in science};sys.path[:0]=[str(HERE),str(worlddir),str(Path(byname['standability.py']['path']).parents[3]),str(Path(byname['build_multistorey_graphs.py']['path']).parent),str(Path(byname['gate25_storey_navigation.py']['path']).parent)]
 from current_factory80_world_binding import bind_current_world
 from bounded_world_resources import rasterize_storey_bands_bounded
 from protomotions.simulator.base_simulator import standability as stand
 import numpy as np
 graph=importlib.import_module('build_multistorey_graphs');g25=importlib.import_module('gate25_storey_navigation');config=stand.StandabilityConfig();require(vars(config)==vars(g25.CONFIG)and(config.body_radius_m,config.body_height_m,config.max_support_step_m,config.min_component_area_m2,g25.GRID_M)==(.25,1.6,.2,1.,.05),'Frozen scientific configuration differs')
 info,proof=bind_current_world(row,mainrow,hash_cache=C)
 before=[indexref,*own,initial_ref,*resource_dependencies,world_ixref,*binding_refs,closure_ref,*science,input_ref,plan_ref,qualification_ref,*qualification_supervision,row['root_ref'],row['authoring_ref'],*row['source_dependencies'],*proof]
 if golden:before.extend(golden['actual_original_scratch_refs']);before.extend(f['support_ref']for f in golden['support_rows'])
 before=list({r['path']:r for r in before}.values())
 for r in before:checked(r)
 save(D/'PREREGISTERED_ACTUAL_BOUNDED_SOURCE.json',dict(source_refs_before=before,original_precompute_one_called=False,original_rasterize_storey_bands_called=False,original_scientific_functions_modified=False,local_ephemeral_temp_path=str(tmp),GPU_used=False));C.save(D/'ACTUAL_STAT_HASH_CACHE_BEFORE.json')
 extraction,resources=rasterize_storey_bands_bounded(row['root_ref']['path'],tmp,D/'ACTUAL_ORIGINAL_SURFACE_CALLS.jsonl');extraction.validate()
 scratch=folder/(row['building']+'.bounded_storey_bands_scratch.npz');graph._save_scratch(scratch,extraction);scratchref=C.ref(scratch);binding=folder/(row['building']+'.bounded_storey_bands_scratch.npz.binding.json');save(binding,dict(schema='Factory80BoundedResourceScratchBindingV1',building=row['building'],source_root_ref=row['root_ref'],scratch_ref=scratchref,producer_source_index_ref=indexref,original_container_serializer_ref=byname['build_multistorey_graphs.py'],original_precompute_one_called=False));scratchrefs=[scratchref,C.ref(binding)]
 comparisons=[];goldmeta=None;golden_support={}
 if golden:
  goldpath=Path(golden['actual_original_scratch']['scratch'])
  with np.load(goldpath,allow_pickle=False)as z:goldmeta=json.loads(bytes(z['metadata_json_utf8']).decode())
  with zipfile.ZipFile(goldpath)as z:
   values=dict(full_occupancy=extraction.full_occupancy,full_stair_mask=extraction.full_stair_mask,floor1_occupancy=extraction.bands[0].occupancy,floor1_stair_mask=extraction.bands[0].stair_mask,floor2_occupancy=extraction.bands[1].occupancy,floor2_stair_mask=extraction.bands[1].stair_mask)
   for name,actual in values.items():
    dest=tmp/('golden_'+name+'.npy')
    with z.open(name+'.npy')as src,dest.open('xb')as out:shutil.copyfileobj(src,out,1024**2)
    expected=np.load(dest,mmap_mode='r',allow_pickle=False);comparisons.append(compare_array(name,actual,expected));del expected
  meta=dict(full_grid=graph._grid_payload(extraction.full_grid_spec),floor1_grid=graph._grid_payload(extraction.bands[0].grid_spec),floor2_grid=graph._grid_payload(extraction.bands[1].grid_spec),peak_z_index=extraction.peak_z_index,cut_z_index=extraction.cut_z_index,floor1_support_top_z_m=extraction.floor1_support_top_z_m,rasterization=extraction.rasterization,storey_split=extraction.storey_split,floor1_modelling=extraction.floor1_modelling)
  require(all(goldmeta[k]==v for k,v in meta.items()),'Original metadata differs after shared bounded resource function')
  damaged=np.zeros((2,2),dtype=np.bool_);expected=damaged.copy();damaged[0,0]=True
  try:compare_array('ONE_CHANGED_VOXEL',damaged,expected)
  except ValueError:save(D/'ARRAY_COMPARISON_COUNTERCASE.json',dict(status='FAIL_AS_REQUIRED',actual_changed_voxel_count=1))
  else:save(D/'CRITICAL_STOP_NEGATIVE_ESCAPE.json',dict(entire_task_stop=True,case='ONE_CHANGED_VOXEL'));raise SystemExit(2)
 floors=[]
 for band in extraction.bands:
  occupancy_hash=arrref(band.occupancy);derived=stand.derive_standability(band.occupancy,band.grid_spec,config);support=derived.support_z_index;labels,components=g25._components(support);total=int(np.count_nonzero(support>=0));largest=max(components,key=len)if components else[];xy=np.asarray(largest,dtype=np.int64);spans=[float((xy[:,j].max()-xy[:,j].min()+1)*.05)if largest else 0. for j in(0,1)]
  measurements=dict(standable_cell_count=total,standable_area_m2=total*.05**2,component_count_report_only=len(components),largest_component_area_m2=len(largest)*.05**2,largest_component_fraction=len(largest)/total if total else 0.,largest_component_cell_union_span_xy_m=spans);arrays={k:arrref(v)for k,v in [('support_z_index',support),('component_labels',labels)]};gridpayload=graph._grid_payload(band.grid_spec)
  if golden:
   source=next(f['support_ref']for f in golden['support_rows']if f['storey']==band.storey_id)
   with np.load(source['path'],allow_pickle=False)as z:comparisons.extend([compare_array(band.storey_id+'_support_z_index',support,z['support_z_index']),compare_array(band.storey_id+'_component_labels',labels,z['component_labels'])])
  meta=dict(grid_origin_m=list(band.grid_spec.scene_bounds_min_m),original_band_grid=gridpayload,voxel_size_m=.05,storey=band.storey_id,original_returned_array_bindings=arrays,source_root_ref=row['root_ref'],source_authoring_ref=row['authoring_ref'],bounded_resource_scratch_ref=scratchrefs[0],bounded_resource_scratch_binding_ref=scratchrefs[1],original_Standability_callable_ref=byname['standability.py'],original_G25_components_callable_ref=byname['gate25_storey_navigation.py'],standability_configuration=vars(config),measurements_only=measurements,original_precompute_one_called=False,A1a_A1b_acceptance='NOT EXECUTED; current authored slabs must be measured separately')
  path=folder/(band.storey_id+'.original_support.npz')
  with path.open('xb')as f:np.savez_compressed(f,support_z_index=support,component_labels=labels,metadata_json_utf8=np.frombuffer(json.dumps(meta,allow_nan=False).encode(),dtype=np.uint8));f.flush();os.fsync(f.fileno())
  with np.load(path,allow_pickle=False)as z:require(all(np.array_equal(z[k],v)and z[k].dtype==v.dtype for k,v in [('support_z_index',support),('component_labels',labels)]),'Persisted original scientific output differs')
  require(arrref(band.occupancy)==occupancy_hash,'Original Standability modified occupancy input');floors.append(dict(storey=band.storey_id,support_ref=C.ref(path),grid_origin_m=meta['grid_origin_m'],original_band_grid=gridpayload,voxel_size_m=.05,original_returned_array_bindings=arrays,measurements_only=measurements));del derived,labels,support,components,largest,xy
 after=[C.ref(r['path'])for r in before];require(before==after,'Original/current source changed during bounded producer')
 result=dict(building=row['building'],attempt=row['attempt'],status='BOUNDED_CURRENT_WORLD_SCRATCH_V1_COMPONENT_MEASUREMENTS_COMPLETED_NOT_ACCEPTED',bounded_resource_scratch_refs=scratchrefs,resource_orchestration=resources,support_rows=floors,original_rasterization=extraction.rasterization,original_storey_split=extraction.storey_split,original_floor1_modelling=extraction.floor1_modelling,source_refs_before=before,source_refs_after=after,source_bytes_unchanged=True,old_scientific_results_inherited=False,original_precompute_one_called=False,original_rasterize_storey_bands_called=False,actual_current_floor_profiles_preserved=info['actual_current_floor_profiles'],actual_current_layout_shortfall_preserved=info['actual_current_layout_shortfall'],golden_qualification=dict(all_scientific_arrays_and_metadata_exact=True,original_golden_finite_ref=qualification_ref,array_comparisons=comparisons)if golden else None,G25_StageD_B2_G8_G13_ingest_images_READY='NOT EXECUTED',VmHWM_kB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
 save(folder/'RESULT.json',result);final=dict(schema='Factory80BoundedCurrentWorldScratchV1MeasurementsV1',rows=[result],producer_source_index_ref=indexref,scientific_source_refs_before=science,scientific_source_refs_after=[C.ref(r['path'])for r in science],all_scientific_functions_source_unchanged=True,source_unchanged=True,original_precompute_one_called=False,original_rasterize_storey_bands_called=False,original_scientific_functions_modified=False,formal_acceptance='NOT EXECUTED',GPU_used=False,official_negative_control_executed=False)
 save(D/'FINITE_RESULT.json',final);C.save(D/'ACTUAL_STAT_HASH_CACHE.json');C.save(W/'work/P1_HASH_CACHE_20261015.json');print(json.dumps(dict(status=result['status'],building=row['building'],source_unchanged=True,VmHWM_kB=result['VmHWM_kB'],golden_qualification_exact=bool(golden),finite_ref=C.ref(D/'FINITE_RESULT.json'))),flush=True);return 0
if __name__=='__main__':raise SystemExit(main())
