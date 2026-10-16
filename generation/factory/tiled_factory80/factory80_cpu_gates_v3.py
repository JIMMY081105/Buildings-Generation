"""Release own USD/array lifetimes between unchanged CPU geometry functions."""
import argparse,fcntl,hashlib,json,os,signal,subprocess,sys,time,traceback
from pathlib import Path
from datetime import datetime,timezone
W=Path('/cephfs/shared/yixing/outputs/factory80_20261011')
def now():return datetime.now(timezone.utc).isoformat()
def read(p):return json.loads(Path(p).read_text())
def ref(p):
 p=Path(p);h=hashlib.sha256()
 with p.open('rb') as s:
  for b in iter(lambda:s.read(8*1024**2),b''):h.update(b)
 return dict(path=str(p),bytes=p.stat().st_size,sha256=h.hexdigest())
def write(p,v):
 with Path(p).open('x') as s:json.dump(v,s,indent=2);s.write('\n')
def source(a):
 receipt=read(a.authoring);root=Path(receipt['stage']['path']).resolve()
 if ref(root)!=receipt['stage']:raise ValueError('Actual unchanged authoring source ref differs')
 return receipt,root
def worker(a):
 import factory80_cpu_gates_v1 as old
 old.validate_pilot_identity(a.building);receipt,root=source(a)
 if a.phase=='inventory':
  bound,stage=old.checked_stage(a.authoring);inventory=old.geometry_inventory(bound,stage)
  old.gate.write_new(a.output/'ACTUAL_GEOMETRY_INVENTORY.json',dict(schema='Factory80ActualGeometryInventoryV1',
   building=a.building,generated_at_utc=old.stamp(),stage=old.gate.ref(root),authoring=old.gate.ref(a.authoring),
   actual_inventory=inventory,validation='ACTUALLY_EXECUTED_USD_GEOMETRY_INVENTORY',
   cannot_support='This inventory alone is not a voxel, navigation, stage_d, render, or acceptance result.'))
 elif a.phase=='scratch':
  view=old.input_view(a.building,root,a.output)
  from factory80_frozen_raster_call_observer_v1 import FrozenCallObserver
  trace=a.output/'ACTUAL_FROZEN_SCRATCH_CALL_TRACE.jsonl'
  with FrozenCallObserver(trace,old.gate.ref(root)):
   scratch=old.precompute.precompute_one(a.building,str(view),str(a.output/'scratch'))
  old.gate.write_new(a.output/'ACTUAL_FROZEN_SCRATCH_CALL_TRACE_RESULT.json',dict(
   schema='Factory80ActualFrozenScratchCallTraceCompletionV1',source_stage=old.gate.ref(root),
   trace=old.gate.ref(trace),observer=old.gate.ref(W/'scripts/factory80_frozen_raster_call_observer_v1.py'),
   original_precompute_returned=True,last_trace_call_is_not_proof_of_oom_cause=True,
   cannot_support='Diagnostic calls and memory observations do not replace native scratch binding, V1 measurements or any gate verdict.'))
  old.gate.write_new(a.output/'SCRATCH_RESULT.json',scratch)
 elif a.phase=='V1':
  scratch=read(a.output/'SCRATCH_RESULT.json')
  extraction=old._load_scratch(Path(scratch['scratch']))
  connectivity=old.voxel_connectivity(extraction,a.output,source=old.gate.ref(root),scratch=scratch)
  old.gate.write_new(a.output/'ACTUAL_VOXEL_CONNECTIVITY_RESULT.json',connectivity)
 elif a.phase=='seams':
  from factory80_tile_seams_v1 import measure
  old.gate.write_new(a.output/'DESCRIPTIVE_TILE_SEAMS.json',measure(a.authoring,a.output))
 source(a)
 return 0
def critical():
 for p in W.glob('CRITICAL*.json'):
  try:
   if read(p).get('status')=='ENTIRE_TASK_STOP_REQUIRED':return ref(p)
  except (OSError,ValueError):pass
 return None
def execute_phase(a,phase):
 prefix=a.output/('PROCESS_LIFECYCLE_'+phase)
 cmd=[sys.executable,str(Path(__file__).resolve()),'_phase','--phase',phase,'--building',a.building,
  '--authoring',str(a.authoring),'--output',str(a.output)]
 if a.original_lifecycle_source:cmd.extend(['--original-lifecycle-source',str(a.original_lifecycle_source)])
 begun=now();before=source(a)[0]['stage']
 with Path(str(prefix)+'.stdout').open('xb') as out,Path(str(prefix)+'.stderr').open('xb') as err:
  process=subprocess.Popen(cmd,stdout=out,stderr=err)
  while process.poll() is None:
   cause=critical()
   if cause:
    process.terminate();process.wait();break
   time.sleep(.5)
 after=source(a)[0]['stage'];assert before==after
 doc=dict(schema='Factory80ActualCpuGeometryLifecyclePhaseExecutionV1',actual_linux_parent_wait_status=True,
  phase=phase,command=cmd,actual_phase_pid=process.pid,actual_phase_returncode=process.returncode,
  source_stage=before,started_at_utc=begun,finished_at_utc=now(),original_own_cpu_functions=ref(W/'scripts/factory80_cpu_gates_v1.py'),
  stdout=ref(str(prefix)+'.stdout'),stderr=ref(str(prefix)+'.stderr'),critical_evidence=critical(),
  scope='Own process lifetime only; original rasterizer, precompute, V1, components and control functions are called unchanged.')
 trace=a.output/'ACTUAL_FROZEN_SCRATCH_CALL_TRACE.jsonl'
 if phase=='scratch' and trace.exists():
  doc['actual_frozen_call_trace']=ref(trace)
  doc['actual_frozen_call_observer']=ref(W/'scripts/factory80_frozen_raster_call_observer_v1.py')
 path=Path(str(prefix)+'.EXECUTION.json');write(path,doc)
 return process.returncode,ref(path)
def geometry(a):
 # The parent intentionally imports neither USD nor NumPy, and holds the real
 # existing CPU lease throughout all serial phase children.
 from factory80_cpu_resources_v1 import memory_snapshot
 receipt,root=source(a);a.output.mkdir(parents=True,exist_ok=True)
 suffix=a.building.removeprefix('factory80_')
 if not suffix.isdigit() or not 1<=int(suffix)<=6:raise ValueError('Pilot is limited to 001 through 006')
 lock=W/'work/gate_audit/CPU_HEAVY.lock';execution=[]
 with lock.open('a+') as lease:
  fcntl.flock(lease.fileno(),fcntl.LOCK_EX)
  write(a.output/'CPU_PROCESS_LIFECYCLE_MEMORY_BEFORE.json',memory_snapshot())
  for phase in ('inventory','scratch','V1'):
   rc,ev=execute_phase(a,phase);execution.append(ev)
   if rc!=0:
    cause=critical();code=80 if cause or rc==80 else (128-rc if rc<0 else rc)
    write(a.output/'OBJECTIVE_TILING_QUALITY.json',dict(schema='Factory80PilotObjectiveTilingQualityV1',building=a.building,
     status='FAIL',work_completion='PARTIAL',stage=ref(root),authoring=ref(a.authoring),failed_phase=phase,
     actual_phase_returncode=rc,actual_phase_executions=execution,critical_evidence=cause,
     cannot_support='The unchanged CPU function did not finish. This is an actual tool failure, not a measured V1 or negative-control verdict.'))
    return code
  seam_ref=None;seam_exception=None
  rc,ev=execute_phase(a,'seams');execution.append(ev)
  if critical():return 80
  if rc==0:seam_ref=ref(a.output/'DESCRIPTIVE_TILE_SEAMS.json')
  else:
   path=a.output/'DESCRIPTIVE_TILE_SEAMS_EXCEPTION.json'
   write(path,dict(status='FAIL',execution='ACTUAL_DESCRIPTOR_SUBPROCESS_FAILURE',actual_returncode=rc,execution_receipt=ev,
    cannot_support='The optional descriptor cannot change a measured V1 verdict.'))
   seam_exception=ref(path)
  inventory=read(a.output/'ACTUAL_GEOMETRY_INVENTORY.json')['actual_inventory']
  connectivity=read(a.output/'ACTUAL_VOXEL_CONNECTIVITY_RESULT.json');scratch=read(a.output/'SCRATCH_RESULT.json')
  result=dict(schema='Factory80PilotObjectiveTilingQualityV1',building=a.building,
   status='PASS' if inventory['status']==connectivity['status']=='PASS' else 'FAIL',
   work_completion='MEASURED',validation='ACTUALLY_EXECUTED_CPU_GEOMETRY_AND_VOXELS',generated_at_utc=now(),
   stage=ref(root),authoring=ref(a.authoring),scratch=scratch,actual_inventory=inventory,voxel_connectivity=connectivity,
   descriptive_tile_seams=seam_ref,descriptive_tile_seams_exception=seam_exception,actual_phase_executions=execution,
   sources=[ref(W/'scripts/factory80_cpu_gates_v1.py'),ref(W/'scripts/factory80_cpu_gates_v2.py'),ref(__file__),ref(W/'scripts/factory80_frozen_raster_call_observer_v1.py')],
   cannot_support='This CPU check cannot establish Isaac RTX rendering, stage_d, actual image review, formal acceptance, or humanoid locomotion.')
  write(a.output/'CPU_PROCESS_LIFECYCLE_MEMORY_AFTER.json',memory_snapshot());source(a)
  write(a.output/'OBJECTIVE_TILING_QUALITY.json',result)
  return 0 if result['status']=='PASS' else 1
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('geometry','_phase'))
 p.add_argument('--building',required=True);p.add_argument('--authoring',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 p.add_argument('--original-lifecycle-source',type=Path)
 p.add_argument('--phase',choices=('inventory','scratch','V1','seams'));a=p.parse_args()
 if a.original_lifecycle_source:
  if a.original_lifecycle_source.resolve()!=(W/'scripts/factory80_cpu_gates_v2.py').resolve() or ref(a.original_lifecycle_source)['sha256']!='1287624653ab8d16dd56d91b83e8d0002030e4e66b74e3d4f03c4663b0867810':
   raise ValueError('Actual original lifecycle provenance path or SHA differs')
 try:return worker(a) if a.mode=='_phase' else geometry(a)
 except Exception as error:
  print(traceback.format_exc(),file=sys.stderr,flush=True);return 1
if __name__=='__main__':raise SystemExit(main())
