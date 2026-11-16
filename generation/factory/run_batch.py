"""Batch v16: per-house SDK, verified OOM priority and kernel-evidenced resource retries.

Uses the frozen native6/render5 qualifications and exact round4 source bindings.
No tier thresholds or C1-C5 predicates are defined in this driver.
"""
from pathlib import Path
import argparse, datetime, hashlib, importlib, json, os, signal, subprocess, sys, time, traceback, uuid
import xml.etree.ElementTree as ET
import resource_trial as ops
import gpu_identity_check as gpu_identity
import wall_clock_policy as clock_policy
import revision_gate as revision
CLEAN_HARD_LIMIT_S=clock_policy.HARD_LIMIT_S

R=Path('/cephfs/shared/yixing/outputs/factory80_20261011');D=R/'pipeline/v4'
PY='/cephfs/shared/yixing/envs/main/bin/python';IPY='/cephfs/shared/yixing/envs/isaac_prod/env/bin/python'
CFG=D/'config_batch_v1.yaml';CONFIG_SHA='7197ff1e08e204ee26de7e67a8e54fe4cd7218af162e551f74148af5f77b0429'
ROUND4_SCOPE=('001','049','006','013','061','063','011','029','054','060','027','057','067')
ROUND4_PRIOR_SHA='cae6ae65cb58d2be39d005bfc0a498ae2eb4a6c535d4003cacc632f14c806755'
APPROVAL=D/'SECTION4_NATIVE_TRIANGLES_APPROVAL_20261031.json';CACHE=D/'proxy_geometry_cache'
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--run-directory',type=Path,default=D/'ROUND4_RUN_V16')
ap.add_argument('--freeze',type=Path,default=D/'ROUND4_RULES_FREEZE_V7.json')
ap.add_argument('--native-tool',type=Path,default=D/'isaac_native_c5.py')
ap.add_argument('--native-qualification',type=Path,default=D/'native6_batch_qualification_run01')
ap.add_argument('--voxel-qualification',type=Path,default=D/'voxel005_native6_requal/VOXEL_RESULT.json')
ap.add_argument('--checks005-qualification',type=Path,default=D/'b1005_checks12_requal/CHECK_RESULT.json')
ap.add_argument('--checks001-qualification',type=Path,default=D/'checks001_checks12_requal/CHECK_RESULT.json')
ap.add_argument('--phase2-plan',type=Path,default=D/'PHASE2_PLAN_V1.json')
ap.add_argument('--round4-plan',type=Path,default=D/'ROUND4_PLAN_V3.json')
ap.add_argument('--round4-only',action='store_true',help='Consume only the authorized13 fourth-round jobs; preserve the16 existingREADY houses')
ap.add_argument('--plan-poll-seconds',type=float,default=2.)
ap.add_argument('--resource-policy',type=Path,required=True,help='Immutable JSON resource policy; byte units are explicit')
ap.add_argument('--resource-policy-sha256',help='Optional independent expected policy SHA256; full runs also require the source freeze')
ap.add_argument('--resource-preflight-only',action='store_true',help='Read resource evidence and exit before source/freeze checks or RUN creation')
ap.add_argument('--worker-request',type=Path,help='Immutable one-house resource-attempt request')
ap.add_argument('--startup-validate-only',action='store_true',help='Verify all frozen source and qualification bindings; exit before RUN, SDK or science')
args=ap.parse_args()
ops.priority()
OWN_TOKEN=os.environ.get(ops.TOKEN_KEY) or uuid.uuid4().hex
os.environ[ops.TOKEN_KEY]=OWN_TOKEN
WORKER_REQUEST=json.loads(args.worker_request.read_text(encoding='utf-8')) if args.worker_request else None
if not args.round4_only:ap.error('v15 requires --round4-only; old rounds use their preserved original driver')
if args.round4_only:args.phase2_plan=args.round4_plan
EXECUTION_PHASE='round4' if args.round4_only else 'phase2'
RUN=args.run_directory
NATIVE_TOOL=args.native_tool
INITIAL_CFG=CFG
MASTER_PATH=D/'V4_BUILDING_PLAN.json'
ACTIVE_JOB=None
TOOL_PATHS={
 'layout':D/'hall_layout.py','scene':D/'scene_compose.py','lighting':D/'hall_lights.py',
 'voxel':D/'voxel_gpu.py','checks':D/'gpu_checks.py',
 'alternative':D/'c4_alternative_check.py','report':D/'report_batch.py',
 'deliver':D/'deliver_batch.py','camera':D/'camera_planner.py',
 'native':NATIVE_TOOL,'renderer':D/'rtx_render.py','package':D/'scene_package.py',
 'voxel_helper':D/'voxel_scene.py','resource_runtime':D/'resource_trial.py','gpu_identity':D/'gpu_identity_check.py','time_policy_runtime':D/'wall_clock_policy.py','RGB_wrapper':D/'rgb_instance_wrappers.py','revision_runtime':D/'revision_gate.py','revision_policy':D/revision.POLICY_NAME}
INITIAL_TOOLS=dict(TOOL_PATHS)
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.tmp');tmp.write_text(json.dumps(x,indent=2)+'\n');tmp.replace(p)
def birth(pid):return Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19]
def alive(pid,tick):
 try:return birth(pid)==str(tick)
 except FileNotFoundError:return False
def rss(p):
 try:return max(int(x.split()[1])*1024 for x in Path(f'/proc/{p.pid}/status').read_text().splitlines() if x.startswith(('VmRSS:','VmHWM:')))
 except (FileNotFoundError,ValueError):return 0
def setup(cores):
 ops.setup(cores)
def load_resource_policy(path):
 p=Path(path);raw=p.read_bytes();digest=hashlib.sha256(raw).hexdigest();doc=json.loads(raw)
 if not p.is_absolute():raise ValueError('Resource policy path must be absolute')
 if args.resource_policy_sha256 is not None and digest!=args.resource_policy_sha256:raise RuntimeError('Resource policy SHA differs')
 if doc.get('schema')!='Factory80Round4TrialResourcePolicyV1' or set(doc.get('scope_buildings',[]))!=set(ROUND4_SCOPE):raise ValueError('Trial policy scope differs')
 expected={'gpu_free_min_bytes':12000000000,'container_limit_bytes':50*1024**3,'min_available_memory_bytes':8*1024**3,'OOM_retries_max':2,'OOM_retry_delay_s':600,'oom_score_adj':1000}
 for key,value in expected.items():
  if type(doc.get(key)) is not int or doc[key]!=value:raise ValueError('Authorized trial resource threshold differs '+key)
 if doc.get('historical_peak_is_admission_gate') is not False or doc.get('SDK_scope')!='PER_HOUSE':raise ValueError('Per-house SDK and trial admission required')
 return doc,dict(path=str(p),sha256=digest)

def resource_preflight():
 # Read-only resource predicate. Native SESSION reports a process peak, not
 # a historical cgroup rss+shmem peak. The user's required reserve is separate.
 if sha(RESOURCE_POLICY_REF['path'])!=RESOURCE_POLICY_REF['sha256']:raise RuntimeError('Resource policy bytes changed after initial binding')
 group=Path('/sys/fs/cgroup/memory');limit=int((group/'memory.limit_in_bytes').read_text())
 stat={line.split()[0]:int(line.split()[1]) for line in (group/'memory.stat').read_text().splitlines()}
 if limit!=RESOURCE_POLICY['container_limit_bytes']:raise RuntimeError('Actual cgroup limit differs from the authorized50GiB container')
 for key in ('total_rss','total_shmem'):
  if key not in stat or stat[key]<0:raise RuntimeError('Missing or invalid cgroup memory.stat '+key)
 remaining=CLEAN_HARD_LIMIT_S-(time.monotonic_ns()-house_start)/1e9 if globals().get('house_start') else 30
 if remaining<0:raise TimeoutError('clean building exceeds180min during resource snapshot')
 try:raw_gpu=subprocess.check_output(['nvidia-smi','-i','0','-q','-x'],text=True,timeout=min(30,max(.001,remaining)),preexec_fn=ops.setup)
 except subprocess.TimeoutExpired:
  if globals().get('house_start') and (time.monotonic_ns()-house_start)/1e9>CLEAN_HARD_LIMIT_S:raise TimeoutError('clean building exceeds180min during GPU resource snapshot')
  raise
 xml=ET.fromstring(raw_gpu)
 devices=xml.findall('gpu')
 if len(devices)!=1:raise RuntimeError('Expected exactly one actualGPU0 resource snapshot')
 gpu=devices[0];text=gpu.findtext('fb_memory_usage/free','');parts=text.split()
 if len(parts)!=2 or parts[1]!='MiB' or not parts[0].isdigit():raise RuntimeError('Cannot parse actual GPU free memory as integer MiB')
 gpu_free=int(parts[0])*1024**2
 available=limit-stat['total_rss']-stat['total_shmem']
 required=RESOURCE_POLICY['min_available_memory_bytes']
 gpu_ok=gpu_free>=RESOURCE_POLICY['gpu_free_min_bytes'];ram_ok=available>=required
 return dict(schema='Factory80Round4TrialResourcePreflightV1',UTC=utc(),status='PASS' if gpu_ok and ram_ok else 'FAIL',ready=gpu_ok and ram_ok,
  scope_buildings=list(ROUND4_SCOPE),resource_policy=RESOURCE_POLICY_REF,driver_source=dict(path=str(Path(__file__).resolve()),sha256=sha(__file__)),
  GPU=dict(index=0,uuid=gpu.findtext('uuid'),name=gpu.findtext('product_name'),free_memory_bytes=gpu_free,min_free_memory_bytes=RESOURCE_POLICY['gpu_free_min_bytes'],status='PASS' if gpu_ok else 'FAIL',unit='bytes',min_free_definition='12GB decimal =12000000000bytes',foreign_process_occupancy_is_not_a_gate=True),
  container=dict(cgroup_path=str(group),limit_bytes=limit,total_rss_bytes=stat['total_rss'],total_shmem_bytes=stat['total_shmem'],available_bytes=available,formula='limit_bytes-total_rss_bytes-total_shmem_bytes',cache_subtracted=False),
  requirement=dict(required_bytes=required,formula='min_available_memory_bytes',historical_peak_is_admission_gate=False,status='PASS' if ram_ok else 'FAIL'),
  scientific_tasks_started=globals().get('current_clean') is not None,SDK_started=globals().get('native') is not None,RUN_created=globals().get('owner') is not None)

RESOURCE_POLICY,RESOURCE_POLICY_REF=load_resource_policy(args.resource_policy)
TIME_POLICY_REF=clock_policy.reference()
ALLOCATOR_POLICY_PATH=D/'ROUND4_CUDA_ALLOCATOR_POLICY_V2.json'
ALLOCATOR_POLICY=read(ALLOCATOR_POLICY_PATH)
if ALLOCATOR_POLICY.get('schema')!='Factory80CUDAAllocatorPolicyV1' or ALLOCATOR_POLICY.get('scope_stage')!='C1_C4_GPU' or ALLOCATOR_POLICY.get('environment_key')!='PYTORCH_ALLOC_CONF' or ALLOCATOR_POLICY.get('environment_value')!='expandable_segments:True':raise RuntimeError('CUDA allocator runtime policy differs')
if ALLOCATOR_POLICY['scope_tool']!={'path':str(D/'gpu_checks.py'),'sha256':sha(D/'gpu_checks.py')}:raise RuntimeError('CUDA allocator scope differs from qualified unchanged checker')
ALLOCATOR_POLICY_REF=dict(path=str(ALLOCATOR_POLICY_PATH),sha256=sha(ALLOCATOR_POLICY_PATH))
if args.resource_preflight_only:
 try:
  preflight=resource_preflight();print(json.dumps(preflight),flush=True)
 except Exception as error:
  print(json.dumps(dict(status='ERROR',UTC=utc(),error=repr(error),resource_policy=RESOURCE_POLICY_REF,scientific_tasks_started=False,SDK_started=False,RUN_created=False)),flush=True)
  raise SystemExit(1)
 raise SystemExit(0 if preflight['ready'] else 2)

REVISION_POLICY=revision.load_policy(R)
REVISION_POLICY_REF=REVISION_POLICY['_reference']
NATIVE_QUALIFIED_SHA=REVISION_POLICY['qualified_scientific_tools']['isaac_native_c5.py']
env=os.environ.copy();env.update(OMP_NUM_THREADS='8',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PXR_WORK_THREAD_LIMIT='8',ACCEPT_EULA='Y',OMNI_KIT_ACCEPT_EULA='YES')
assert sha(CFG)==CONFIG_SHA
config=read(CFG);master=read(D/'V4_BUILDING_PLAN.json');plans=sorted(master['buildings'],key=lambda x:(x['grid'],master['buildings'].index(x)))
assert len(plans)==29 and plans[0]['building']=='001' and config['v4']['batch_release']
freeze=read(args.freeze)
if freeze['source_sha256'].get(Path(RESOURCE_POLICY_REF['path']).name)!=RESOURCE_POLICY_REF['sha256']:raise RuntimeError('Resource policy missing from the new exact source freeze')
for n,h in freeze['source_sha256'].items():assert sha(D/n)==h,(n,'frozen source differs')
assert read(args.voxel_qualification)['comparison']['status']=='PASS'
assert read(args.checks005_qualification)['status']=='PASS'
q=read(args.checks001_qualification)
assert q['status']=='PASS' and q['negative_controls']['status']=='PASS_EXPECTED_SEMANTIC_FAILURES'
assert read(args.native_qualification/'c5_positive/C5.json')['status']=='PASS'
nq=read(args.native_qualification/'c5_negative/C5.json')
assert nq['status']=='FAIL' and nq['negative_control']['real_machine_detected']
ns=read(args.native_qualification/'SESSION.json')
assert ns['intended_exit_code']==0 and ns['source_sha256']==sha(D/'isaac_native_c5_legacy.py')
native8=NATIVE_TOOL.read_text();native6=(D/'isaac_native_c5_legacy.py').read_text()
assert native8.replace('from rtx_render import capture_session','from rtx_render_legacy import capture_session').replace('isaac_c5@v8.0','isaac_c5@v6.0')==native6
assert sha(NATIVE_TOOL)==NATIVE_QUALIFIED_SHA
for result in (read(args.native_qualification/'c5_positive/C5.json'),nq):
 assert result['lifecycle_v5']['source_sha256']==sha(D/'isaac_native_c5_legacy.py')
 assert all(row['status']=='PASS' for row in result['lifecycle_v5']['boundaries'])
sources={p.name:sha(p) for p in TOOL_PATHS.values()};sources[Path(__file__).name]=sha(__file__)
INITIAL_SOURCE_SHAS={role:sha(p) for role,p in INITIAL_TOOLS.items()}
for p in INITIAL_TOOLS.values():
 if freeze['source_sha256'].get(p.name)!=sha(p):raise RuntimeError('Initial tool missing from exact source freeze '+str(p))
if freeze['source_sha256'].get(Path(__file__).name)!=sha(__file__):raise RuntimeError('Driver source missing from exact freeze')
if args.startup_validate_only:
 assert datetime.datetime.now(datetime.timezone.utc)<datetime.datetime.fromisoformat(REVISION_POLICY['T_plus5_deadline_UTC']),'Original T+5 deadline exceeded'
 print(json.dumps(dict(status='PASS_STARTUP_SOURCE_AND_QUALIFICATION_VALIDATION',source_bindings=len(freeze['source_sha256']),cohort_bindings=119,approved_operational_revision=REVISION_POLICY_REF,SDK_started=False,scientific_tasks_started=False,RUN_created=False)),flush=True)
 raise SystemExit(0)
RUN.mkdir(parents=True,exist_ok=False)
owner=dict(pid=os.getpid(),start_ticks=birth(os.getpid()),started_utc=utc(),config_sha256=CONFIG_SHA,source_tool_shas=sources,batch_authorized=True,execution_phase=EXECUTION_PHASE,old_initial_execution_skipped=args.round4_only,scope_buildings=list(ROUND4_SCOPE),resource_policy=RESOURCE_POLICY_REF)
owner['operational_freeze']=dict(path=str(args.freeze),sha256=sha(args.freeze))
owner['CUDA_allocator_policy']=ALLOCATOR_POLICY_REF
owner['approved_operational_revision']=REVISION_POLICY_REF
owner['oom_score_adj_readback']=int(Path('/proc/self/oom_score_adj').read_text(encoding='utf-8'));owner['child_subreaper_readback']=ops.subreaper();owner['supervisor_run']=WORKER_REQUEST.get('supervisor_run') if WORKER_REQUEST else None
write(RUN/'OWNER.json',owner);print('BATCH_STARTED '+json.dumps(owner),flush=True)
native=None;nlog=None;native_started_for=None;active=None;house_start=None;rows=[];packs=[];own_gpu_pids=set();current_clean=None;cold_admission=None;resident_admission=None;last_report_ns=None
owned=ops.Ownership(OWN_TOKEN,RUN/'OWNERSHIP_PRIORITY_JOURNAL.json');owned.scan(True)
NATIVE_BASELINE=None;NATIVE_OWNER=None;native_peak=0;FROZEN_INPUT_REF=None

def tool(role):return Path(TOOL_PATHS[role])
def verify_ref(row,label):
 p=Path(row['path']);expected=row['sha256']
 if not p.is_absolute() or not isinstance(expected,str) or len(expected)!=64:raise ValueError('Incomplete immutable reference '+label)
 if sha(p)!=expected:raise RuntimeError('Immutable source differs '+label+': '+str(p))
 return p
def product_ref(row,label,terminal_statuses=None):
 p=Path(row['path'])
 if not p.is_absolute():raise ValueError('Product path must be absolute '+label)
 while not p.is_file() or (terminal_statuses is not None and read(p).get('status') not in terminal_statuses):
  if not row.get('pending'):raise RuntimeError('Required source missing '+label+': '+str(p))
  if native is not None and native.poll() is not None:raise RuntimeError('resident SDK exited while waiting for '+label)
  owner=row.get('producer_owner')
  if owner and Path(owner['path']).is_file():
   o=read(owner['path']);pid=o.get('pid',o.get('driver_pid'));tick=o.get('start_ticks',o.get('birth'))
   if pid and tick and not alive(pid,tick):raise RuntimeError('CPU producer ended without '+label+': '+str(p))
  write(RUN/'WAITING_CPU_PRODUCT.json',dict(UTC=utc(),label=label,path=str(p),native_pid=native.pid if native else None))
  time.sleep(min(args.plan_poll_seconds,report_if_due()))
 if row.get('sha256'):verify_ref(row,label)
 return p
def resolve_job(job):
 """Resolve a pending CPU selection; its actual outputs are bound at consumption."""
 job=dict(job);selection_path=None
 if job.get('tier_selection'):
  selection_path=product_ref(job['tier_selection'],'TIER_SELECTION',('LAYOUT_PASS','EXCLUDED_LAYOUT'))
  selection=read(selection_path)
  if selection.get('status') not in ('LAYOUT_PASS','EXCLUDED_LAYOUT'):raise RuntimeError('Tier selection is not a scientific terminal result')
  if selection.get('building')!=job['building']:raise RuntimeError('Tier selection building differs')
  expected=['R1','R2','R3'];observed=selection['tiers']
  selected=selection.get('selected_tier');last=expected.index(selected) if selected is not None else 2
  if [r['tier'] for r in observed]!=expected[:last+1]:raise RuntimeError('Tier selection is not strictest-first actual execution')
  for index,row in enumerate(observed):
   actual=read(verify_ref(row['precompute_result'],'executed tier result'));verify_ref(row['config'],'executed tier config')
   if actual.get('config')!=row['config'] or actual.get('layout_tier')!=row['tier']:raise RuntimeError('Executed tier result source differs')
   if actual.get('layout_threshold_profile')!=row['tier'] or read(row['config']['path'])['v4'].get('layout_threshold_profile')!=row['tier']:raise RuntimeError('Executed R-tier threshold profile differs from its actual config')
   if index<last or selected is None:
    if actual.get('status') not in ('LAYOUT_FAIL','EXCLUDED_LAYOUT') or set(actual.get('layout_seed_indices_used',[]))!=set(range(20)):raise RuntimeError('Stricter tier lacks its actual20-seed scientific failure')
  if selection.get('selected_tier') is not None:
   result_ref=selection['selected_precompute_result'];layout_dir=Path(selection['selected_layout_directory']);tier=selection['selected_tier']
  else:
   if selection['status']!='EXCLUDED_LAYOUT' or selection.get('final_tier')!='R3':raise RuntimeError('Tier selection has no actual strictest PASS or complete R3 failure')
   result_ref=selection['final_precompute_result'];layout_dir=Path(result_ref['path']).parent;tier=selection['final_tier']
  p=verify_ref(result_ref,'selected/final PRECOMPUTE_RESULT');result=read(p)
  if selection['status']=='LAYOUT_PASS' and result.get('status')!='LAYOUT_PASS':raise RuntimeError('Selected tier does not have actual LAYOUT_PASS')
  if selection['status']=='EXCLUDED_LAYOUT' and result.get('status') not in ('LAYOUT_FAIL','EXCLUDED_LAYOUT'):raise RuntimeError('Final failed tier has no actual scientific layout failure')
  if result.get('layout_tier')!=tier:raise RuntimeError('Tier selection and actual result differ')
  job.update(config=result['config'],tier=tier,layout_directory=str(layout_dir),precompute_result=result_ref,
             tier_selection_actual=dict(path=str(selection_path),sha256=sha(selection_path)),selection_status=selection['status'])
 else:
  p=product_ref(job['precompute_result'],'PRECOMPUTE_RESULT');result=read(p)
  job['precompute_result']=dict(path=str(p),sha256=sha(p))
  if args.round4_only:
   cfg=read(verify_ref(job['config'],'fixed-profile island config'));profile=cfg['v4']['layout_threshold_profile']
   if result.get('config')!=job['config'] or result.get('layout_tier')!=job['tier'] or result.get('layout_threshold_profile')!=profile:
    raise RuntimeError('Fixed-profile island PRECOMPUTE config/tier/profile binding differs')
   if result.get('status') in ('LAYOUT_FAIL','EXCLUDED_LAYOUT') and set(result.get('layout_seed_indices_used',[]))!=set(range(20)):
    raise RuntimeError('Fixed-profile island scientific layout exclusion requires all20 actual seeds')
   job['layout_threshold_profile']=profile
 if Path(job['precompute_result']['path']).parent.resolve()!=Path(job['layout_directory']).resolve():raise RuntimeError('Actual result is not in declared layout directory')
 if result.get('status') not in ('LAYOUT_PASS','LAYOUT_FAIL','EXCLUDED_LAYOUT'):raise RuntimeError('CPU implementation/source failure is not a layout exclusion')
 job['_layout_result']=result
 return job
def activate_job(job):
 global CFG,CONFIG_SHA,MASTER_PATH,TOOL_PATHS,sources,ACTIVE_JOB,config
 if house_start is not None:raise RuntimeError('Cannot switch job inside a clean house clock')
 CFG=verify_ref(job['config'],'job config');CONFIG_SHA=job['config']['sha256'];config=read(CFG)
 MASTER_PATH=verify_ref(job['master_plan'],'master plan')
 TOOL_PATHS=dict(INITIAL_TOOLS)
 for role,row in job.get('tools',{}).items():
  original=verify_ref(row,'tool '+role)
  if role in ('report','deliver','checks'):
   expected={'report':'report_batch_legacy.py','deliver':'deliver_batch_legacy.py','checks':'gpu_checks_legacy.py'}[role]
   if original!=D/expected or freeze['source_sha256'].get(expected)!=row['sha256']:raise RuntimeError('Operational time-policy override requires exact original tool')
   TOOL_PATHS[role]=INITIAL_TOOLS[role]
  else:TOOL_PATHS[role]=original
 for role,p in TOOL_PATHS.items():
  if role not in job.get('tools',{}) and sha(p)!=INITIAL_SOURCE_SHAS[role]:raise RuntimeError('Inherited frozen tool changed '+str(p))
 if tool('native').resolve()!=NATIVE_TOOL.resolve() or sha(tool('native'))!=NATIVE_QUALIFIED_SHA:raise RuntimeError('Phase cannot replace the qualified resident SDK tool')
 for row in job.get('source_bindings',[]):verify_ref(row,'job source')
 bid=job['building'];plan=next(p for p in read(MASTER_PATH)['buildings'] if p['building']==bid)
 B=Path(job['house_directory']);allowed=(R/'runs'/bid/'v4').resolve()
 if not B.resolve().is_relative_to(allowed) or B.resolve()==allowed:raise RuntimeError('House output is outside the explicitly named building v4 directory')
 if job.get('phase') in ('phase2','round4') and B.resolve()==(allowed/'batch_v1').resolve():raise RuntimeError('Recovery must preserve batch_v1 outputs')
 if args.round4_only:
  if bid not in REVISION_POLICY['houses'] or B.resolve()!=Path(REVISION_POLICY['houses'][bid]['new_directory']).resolve():raise RuntimeError('Approved revision house differs from its new immutable output epoch')
 ACTIVE_JOB=dict(job);ACTIVE_JOB['roles_root']=str(Path(job['roles_root']))
 sources={Path(p).name:sha(p) for p in TOOL_PATHS.values()};sources[Path(__file__).name]=sha(__file__)
 B.mkdir(parents=True,exist_ok=True)
 bindings=dict(job_id=job['job_id'],phase=job['phase'],building=bid,tier=job['tier'],layout_threshold_profile=config['v4'].get('layout_threshold_profile',job['tier']),UTC=utc(),
               master_plan=job['master_plan'],config=job['config'],house_directory=str(B),layout_directory=job['layout_directory'],
               precompute_result=job['precompute_result'],tier_selection=job.get('tier_selection_actual'),
               phase_plan=job.get('phase_plan'),resource_policy=RESOURCE_POLICY_REF,
               tools={k:dict(path=str(p),sha256=sha(p)) for k,p in TOOL_PATHS.items()},source_bindings=job.get('source_bindings',[]))
 if job['_layout_result']['status']=='LAYOUT_PASS':
  bindings['actual_layout_inputs']={n:sha(Path(job['layout_directory'])/n) for n in ('LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json')}
  doc=read(Path(job['layout_directory'])/'LAYOUT.json')
  if doc.get('status')!='LAYOUT_PASS' or doc.get('input_sha',{}).get(str(CFG))!=CONFIG_SHA:raise RuntimeError('Chosen layout lacks actual ordinary PASS/config binding')
  if job.get('tier_selection_actual') and doc.get('input_sha',{}).get(str(tool('layout')))!=sha(tool('layout')):raise RuntimeError('Chosen tier layout lacks its frozen tool binding')
  bindings['actual_layout_authoring_tool_shas']={p:h for p,h in doc.get('input_sha',{}).items() if Path(p).name.startswith('layout_v') and Path(p).suffix=='.py'}
 write(RUN/'job_bindings'/(job['job_id']+'.json'),bindings)
 return plan,B,dict(job['_layout_result'])
def initial_job(plan):
 bid=plan['building'];B=R/'runs'/bid/'v4/batch_v1'
 return dict(job_id='initial_'+bid,phase='initial',building=bid,master_plan=dict(path=str(D/'V4_BUILDING_PLAN.json'),sha256=sha(D/'V4_BUILDING_PLAN.json')),
   house_directory=str(B),layout_directory=str(B/'layout'),precompute_result=dict(path=str(B/'layout/PRECOMPUTE_RESULT.json'),pending=True),
   config=dict(path=str(INITIAL_CFG),sha256=sha(INITIAL_CFG)),tier='ORIGINAL_BATCH_V1',roles_root=str(R/'runs'/bid/'v4/c4_v9_geometry'))
def wait_phase2():
 """A root-produced immutable plan, not a user checkpoint or a new server."""
 while True:
  if native is not None and native.poll() is not None:raise RuntimeError('resident SDK exited while awaiting automatic phase2 plan')
  if args.phase2_plan.is_file():
   try:plan=read(args.phase2_plan)
   except json.JSONDecodeError:plan=None
   if plan and plan.get('status')=='READY':break
  write(RUN/('WAITING_ROUND4_PLAN.json' if args.round4_only else 'WAITING_PHASE2_PLAN.json'),dict(status='WAITING_AUTOMATIC_'+EXECUTION_PHASE.upper()+'_PLAN',UTC=utc(),path=str(args.phase2_plan),
    native_pid=native.pid if native else None,native_birth=birth(native.pid) if native else None,clean_house_clock_active=False,human_checkpoint=False))
  time.sleep(args.plan_poll_seconds)
 expected_schema='Factory80Round4PlanV1' if args.round4_only else 'Factory80Phase2PlanV1'
 if plan.get('schema')!=expected_schema or plan.get('ready') is not True:raise RuntimeError('Incomplete cohort READY contract')
 for row in plan['source_bindings']:verify_ref(row,'phase2 frozen source')
 # Root marks READY only after the amended C4(d) qualification. Bind the raw
 # measured control, rather than interpreting a plan flag as a science PASS.
 control=plan['C4_negative_d'];qualified_checks=verify_ref(control['checks_tool'],'Qualified checker for the frozen C4 contract');doc=read(verify_ref(control,'C4 negative d'))
 if doc.get('status')!='PASS_EXPECTED_TARGET_FAILURE_WITH_REAL_PANEL_DETECTION' or doc.get('target_actual',{}).get('status')!='FAIL' or doc.get('real_panel_detected') is not True:raise RuntimeError('C4 contract negative(d) did not actually fail its target with the real panel')
 jobs=plan['jobs'];ids=[j['job_id'] for j in jobs];bids=[j['building'] for j in jobs]
 if len(set(ids))!=len(ids) or len(set(bids))!=len(bids):raise RuntimeError('Duplicate phase2 jobs/buildings')
 if args.round4_only:
  if plan['prior_cohort_report']['sha256']!=ROUND4_PRIOR_SHA:raise RuntimeError('Round4 prior must bind the actual frozen16READY13EXCLUDED final report')
  prior=read(verify_ref(plan['prior_cohort_report'],'round4 prior actual cohort'))
  prior_ids=[h['building'] for h in prior['houses']]
  if len(prior_ids)!=29 or len(set(prior_ids))!=29 or set(prior_ids)!={p['building'] for p in plans}:raise RuntimeError('Round4 prior must contain exactly the frozen29 master buildings')
  excluded={h['building'] for h in prior['houses'] if h['category']=='EXCLUDED'}
  if len(bids)!=13 or excluded!=set(ROUND4_SCOPE) or set(bids)!=excluded:raise RuntimeError('Round4 must contain exactly the prior13 exclusions')
  if sum(h['category']=='READY' for h in prior['houses'])!=16:raise RuntimeError('Frozen retained16READY scope differs')
  if plan.get('delivery',{}).get('mode')!='REMOTE_ONLY' or plan['delivery'].get('remote_directory')!='/cephfs/shared/yixing/deliveries/Factory80_v4':raise RuntimeError('Round4 delivery must remain remote-only in the authorized directory')
 for job in jobs:
  if job.get('phase')!=EXECUTION_PHASE:raise RuntimeError('Job phase differs from explicit execution mode')
  if not job.get('tier_selection'):raise RuntimeError('Every round4 job requires its actual strictest-first R-tier selection')
  for required in ('layout','checks','alternative','report','deliver'):
   if required not in job.get('tools',{}):raise RuntimeError('Phase2 descriptor needs explicit tool '+required)
  for role,row in job['tools'].items():verify_ref(row,'phase2 tool '+role)
  if Path(job['tools']['checks']['path']).resolve()!=qualified_checks.resolve() or job['tools']['checks']['sha256']!=control['checks_tool']['sha256']:raise RuntimeError('Phase2 checker differs from actual negative(d) qualification')
  verify_ref(job['master_plan'],'phase2 master')
 # Physics consumes the actual first passing R-tier strictly grid3→7.
 def order(job):
  entries=read(job['master_plan']['path'])['buildings'];p=next(p for p in entries if p['building']==job['building'])
  return (0 if not args.round4_only and job['building']=='042' else 1,p['grid'],next(i for i,p in enumerate(entries) if p['building']==job['building']))
 jobs=sorted(jobs,key=order);validated_cohort_job_order=[j['job_id'] for j in jobs]
 if len(jobs)!=13 or {job['building'] for job in jobs}!=set(ROUND4_SCOPE):raise RuntimeError('v12 requires exactly the validated13 fourth-round jobs')
 receipt=dict(path=str(args.phase2_plan),sha256=sha(args.phase2_plan),accepted_utc=utc(),job_order=[j['job_id'] for j in jobs],validated_cohort_job_order=validated_cohort_job_order,scope_buildings=list(ROUND4_SCOPE),resource_policy=RESOURCE_POLICY_REF,
              C4_negative_d=control,source_bindings=plan['source_bindings'],SDK_PID=native.pid if native else None)
 receipt['approved_operational_revision']=REVISION_POLICY_REF
 original_jobs={j['building']:j for j in jobs}
 jobs=[revision.job_overlay(original_jobs[bid],REVISION_POLICY) for bid in REVISION_POLICY['execution_order']]
 receipt['original_validated_cohort_job_order']=receipt['job_order'];receipt['job_order']=[j['job_id'] for j in jobs];receipt['execution_subset']=[j['building'] for j in jobs]
 write(RUN/('ROUND4_PLAN_ACCEPTED.json' if args.round4_only else 'PHASE2_PLAN_ACCEPTED.json'),receipt)
 return jobs,receipt
def report():
 global last_report_ns
 argv=[PY,str(tool('report')),'--project-root',str(R),'--batch-config',str(CFG),'--cohort-plan',str(args.round4_plan)]
 if ACTIVE_JOB:argv+=['--house-directory',ACTIVE_JOB['house_directory']]
 p=subprocess.Popen(argv,env=env,preexec_fn=lambda:ops.setup(),start_new_session=True);row=proc_fact(p,'REPORT')
 while ops.poll(p) is None:own_tick();time.sleep(.1)
 if p.returncode:raise RuntimeError('Report process failed '+str(p.returncode))
 last_report_ns=time.monotonic_ns();write(RUN/'LAST_REPORT_PUBLICATION.json',dict(status='PASS',UTC=utc(),actual_OS_exit_code=p.returncode,priority_readback=row,cohort_plan=dict(path=str(args.round4_plan),sha256=sha(args.round4_plan)),max_interval_seconds=7200))

def report_if_due():
 """Use existing wait loops; no timer process or background controller."""
 if last_report_ns is None or (time.monotonic_ns()-last_report_ns)/1e9>=7200:report()
 return max(.001,7200-(time.monotonic_ns()-last_report_ns)/1e9)
def before_gpu(name,E):
 wait_gpu_capacity(name,E)
 remaining=CLEAN_HARD_LIMIT_S-(time.monotonic_ns()-house_start)/1e9 if house_start else 30
 if remaining<0:raise TimeoutError('clean building exceeds180min before GPU step')
 try:x=subprocess.check_output(['nvidia-smi'],text=True,timeout=min(30,max(.001,remaining)),preexec_fn=ops.setup)
 except subprocess.TimeoutExpired:
  if house_start and (time.monotonic_ns()-house_start)/1e9>CLEAN_HARD_LIMIT_S:raise TimeoutError('clean building exceeds180min before GPU step')
  raise
 (E/(name+'_GPU_BEFORE.txt')).write_text(x)
def gpu_processes():
 xml=ET.fromstring(subprocess.check_output(['nvidia-smi','-q','-x'],text=True,timeout=30,preexec_fn=ops.setup));out={}
 for proc in xml.findall('.//gpu/processes/process_info'):
  pid=int(proc.findtext('pid'));value=proc.findtext('used_memory','0 MiB').split()[0]
  memory=float(value) if value.replace('.','',1).isdigit() else 0.
  out[pid]=max(out.get(pid,0.),memory)
 return out
def wait_gpu_capacity(name,E):
 """Each GPU step requires actual free bytes; waits use the existing clean clock."""
 E=Path(E);E.mkdir(parents=True,exist_ok=True)
 while True:
  elapsed=(time.monotonic_ns()-house_start)/1e9 if house_start else None
  if elapsed is not None and elapsed>CLEAN_HARD_LIMIT_S:raise TimeoutError('clean building exceeds180min while awaiting GPU free bytes')
  snapshot=resource_preflight();snapshot.update(RUN_created=True,admission_scope='EACH_GPU_STEP_FREE_BYTES_ONLY',GPU_step=name,RAM_predicate_is_diagnostic=True,clean_elapsed_s=elapsed,next_poll_seconds=30)
  if house_start and (time.monotonic_ns()-house_start)/1e9>CLEAN_HARD_LIMIT_S:raise TimeoutError('clean building exceeds180min while awaiting GPU free bytes')
  snapshot['GPU_step_ready']=snapshot['GPU']['status']=='PASS'
  write(E/(name+'_RESOURCE_PREFLIGHT.json'),snapshot)
  if snapshot['GPU_step_ready']:return snapshot
  write(RUN/'SHARED_GPU_WAIT.json',snapshot)
  print('GPU_FREE_WAIT '+json.dumps(snapshot),flush=True)
  remaining=CLEAN_HARD_LIMIT_S-(time.monotonic_ns()-house_start)/1e9 if house_start else 300
  if remaining<0:raise TimeoutError('clean building exceeds180min while awaiting GPU free bytes')
  time.sleep(min(30,max(.001,remaining),report_if_due()))

def admit_clean_stack(bid,index):
 global cold_admission
 receipt=RUN/'resource_admissions'/f'{bid}_clean_{index:02d}.json'
 if receipt.exists():raise RuntimeError('Admission receipt already exists')
 while True:
  snapshot=resource_preflight();snapshot.update(RUN_created=True,building=bid,clean_index=index,admission_scope='TRIAL_8GIB_GPU12GB')
  write(RUN/'RESOURCE_PREFLIGHT.json',snapshot)
  if snapshot['ready']:
   cold_admission=dict(status='ADMITTED_TRIAL',UTC=utc(),building=bid,clean_index=index,resource_policy=RESOURCE_POLICY_REF,resource_snapshot=snapshot,receipt_path=str(receipt))
   write(receipt,cold_admission);return cold_admission
  write(RUN/'TRIAL_RESOURCE_WAIT.json',snapshot);print('TRIAL_RESOURCE_WAIT '+json.dumps(snapshot),flush=True)
  time.sleep(min(30,report_if_due()))
def run(name,argv,E,gpu=False,timed=False):
 global active
 E.mkdir(parents=True,exist_ok=True)
 if gpu:before_gpu(name,E)
 start=time.monotonic_ns();peak=0;baseline=ops.checkpoint()
 suffix='_resource_'+str(WORKER_REQUEST['resource_attempt']) if WORKER_REQUEST and E.name=='preparation' else ''
 with (E/(name+suffix+'.log')).open('x') as log:
  childenv=env.copy();allocator_runtime=None
  if name==ALLOCATOR_POLICY['scope_stage']:
   if not gpu or Path(argv[1]).resolve()!=Path(ALLOCATOR_POLICY['scope_tool']['path']).resolve() or sha(argv[1])!=ALLOCATOR_POLICY['scope_tool']['sha256']:raise RuntimeError('Allocator policy cannot apply outside exact qualified check stage')
   childenv.pop(ALLOCATOR_POLICY['remove_conflicting_alias'],None)
   childenv[ALLOCATOR_POLICY['environment_key']]=ALLOCATOR_POLICY['environment_value']
   allocator_runtime=dict(policy=ALLOCATOR_POLICY_REF,stage=name,requested_environment={ALLOCATOR_POLICY['environment_key']:ALLOCATOR_POLICY['environment_value']},scope='CHECKS_PROCESS_ONLY_NOT_SDK_OR_VOXEL',scientific_source_unchanged=True)
  if not gpu:childenv['CUDA_VISIBLE_DEVICES']=''
  active=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT,env=childenv,preexec_fn=lambda:setup(range(32,48) if gpu else range(48,64)),start_new_session=True)
  o=proc_fact(active,name);o.update(start_ticks=o['birth'],argv=argv)
  if allocator_runtime:
   actual_environment={}
   for entry in Path(f'/proc/{active.pid}/environ').read_bytes().split(b'\0'):
    key,sep,value=entry.partition(b'=')
    if sep and key.decode(errors='replace') in ('PYTORCH_ALLOC_CONF','PYTORCH_CUDA_ALLOC_CONF'):actual_environment[key.decode()]=value.decode()
   if actual_environment!={'PYTORCH_ALLOC_CONF':'expandable_segments:True'}:raise RuntimeError('Owned checks allocator environment readback differs')
   allocator_runtime['actual_environment_readback']=actual_environment;o['allocator_runtime']=allocator_runtime
   write(E/(name+suffix+'_ALLOCATOR_RUNTIME.json'),allocator_runtime)
  write(E/(name+suffix+'_OWNER.json'),o)
  print('STAGE_STARTED '+name+' '+str(active.pid),flush=True)
  while ops.poll(active) is None:
   own_tick();peak=max(peak,rss(active))
   if timed and house_start and (time.monotonic_ns()-house_start)/1e9>CLEAN_HARD_LIMIT_S:raise TimeoutError('clean building exceeds180min')
   report_if_due();time.sleep(.2)
  process=active;peak=max(peak,getattr(process,'factory80_wait4',{}).get('peak_RSS_bytes',0))
  result=dict(**o,name=name,exit_code=process.returncode,elapsed_s=(time.monotonic_ns()-start)/1e9,peak_memory_bytes=peak,wait4=getattr(process,'factory80_wait4',{}),ended_utc=utc())
  write(E/(name+suffix+'_PROCESS_RECEIPT.json'),result);active=None
  print('STAGE_FINISHED '+json.dumps({k:result[k] for k in ('name','exit_code','elapsed_s')}),flush=True)
  if process.returncode==-9:
   evidence=killed_evidence(process,baseline,o);evidence['interrupted_stage']=result
   if evidence['confirmed']:raise OOMInterruption(evidence)
  return result
def checked(*args,**kwargs):
 r=run(*args,**kwargs)
 if r['exit_code']:raise RuntimeError('Tool failed '+str(r))
 return r
def wait_native(path):
 global native_peak
 while True:
  own_tick()
  if native is not None:
   native_peak=max(native_peak,rss(native))
   if ops.poll(native) is not None:
    process=native;proof=killed_evidence(process,NATIVE_BASELINE,NATIVE_OWNER);finish_native(allow_interruption=True)
    if proof['confirmed']:raise OOMInterruption(proof)
    raise RuntimeError('Per-house SDK exited without kernel OOM: '+str(process.returncode))
  if Path(path).is_file():
   try:
    x=read(path)
    if x.get('status') is not None or Path(path).name=='SESSION.json':return x
   except json.JSONDecodeError:pass
  if house_start and (time.monotonic_ns()-house_start)/1e9>CLEAN_HARD_LIMIT_S:raise TimeoutError('clean building exceeds180min')
  report_if_due();time.sleep(.5)
def command(x):
 if native is None:raise RuntimeError('No live SDK for command')
 if ops.poll(native) is not None:
  process=native;proof=killed_evidence(process,NATIVE_BASELINE,NATIVE_OWNER);finish_native(allow_interruption=True)
  if proof['confirmed']:raise OOMInterruption(proof)
  raise RuntimeError('SDK ended without kernel OOM')
 native.stdin.write(json.dumps(x)+'\n');native.stdin.flush()
def release_native(bid):
 global NATIVE_BASELINE
 if native is None:return
 NATIVE_BASELINE=ops.checkpoint();job=bid+'_release_'+str(time.monotonic_ns());command(dict(mode='release',job_id=job))
 while True:
  if ops.poll(native) is not None:wait_native(RUN/'native_session/SESSION.json')
  own_tick();x=read(RUN/'native_session/SESSION.json')
  if any(r['job_id']==job and r['status']=='PASS' for r in x['jobs']):return
  report_if_due();time.sleep(.25)
def start_native(bid,E):
 global native,nlog,native_started_for,own_gpu_pids,NATIVE_BASELINE,NATIVE_OWNER,native_peak
 if native is not None:
  if native_started_for!=bid:raise RuntimeError('Cross-house SDK reuse forbidden')
  return wait_native(RUN/'native_session/SESSION.json')
 before_gpu('SDK',E);before_ids=set(gpu_processes());NATIVE_BASELINE=ops.checkpoint();nlog=(RUN/'NATIVE_STDOUT.log').open('x')
 native=subprocess.Popen([IPY,str(NATIVE_TOOL),'--serve','--session-out',str(RUN/'native_session')],stdin=subprocess.PIPE,stdout=nlog,stderr=subprocess.STDOUT,text=True,bufsize=1,env=env,preexec_fn=lambda:setup(range(32,48)),start_new_session=True)
 native_started_for=bid;native_peak=0;NATIVE_OWNER=proc_fact(native,'ISAAC_SDK');NATIVE_OWNER.update(start_ticks=NATIVE_OWNER['birth'],building=bid,resource_attempt=WORKER_REQUEST['resource_attempt'],native_source=dict(path=str(NATIVE_TOOL),sha256=sha(NATIVE_TOOL)))
 write(RUN/'NATIVE_OWNER.json',NATIVE_OWNER);session=wait_native(RUN/'native_session/SESSION.json');after=gpu_processes()
 identity=gpu_identity.observe(NATIVE_OWNER,session,before_ids,after,sha(NATIVE_TOOL));own_gpu_pids=set()
 NATIVE_OWNER.update(GPU_host_pids=[],GPU_host_PID_identity_status=identity['host_PID_identity_status']);write(RUN/'NATIVE_OWNER.json',NATIVE_OWNER)
 write(RUN/'GPU_PROCESS_IDENTITY.json',identity)
 return session
def native_job(mode,bid,C,fields):
 global NATIVE_BASELINE
 before_gpu(mode,C/'execution');NATIVE_BASELINE=ops.checkpoint();t=time.monotonic_ns();command(dict(mode=mode,job_id=bid+'_'+C.name+'_'+mode,**fields))
 path=C/('c5/C5.json' if mode=='c5' else 'photos/PHOTOS.json');x=wait_native(path)
 if mode=='photos' and x.get('status')=='PASS':
  audit=C/'photos/capture_raw/RGB_ANONYMOUS_WRAPPER_PREFLIGHT.json'
  if not audit.is_file():raise RuntimeError('Qualified RGB wrapper preflight evidence is missing')
  target=RUN/'RGB_PREFLIGHT_EVIDENCE'/(C.name+'.json');target.parent.mkdir(exist_ok=True)
  if target.exists():raise RuntimeError('Refuse overwriting prior RGB preflight evidence')
  audit_sha=sha(audit);audit.replace(target)
  if sha(target)!=audit_sha:raise RuntimeError('RGB audit move changed bytes')
  write(RUN/('RGB_PREFLIGHT_MOVE_'+C.name+'.json'),dict(source=str(audit),retained_evidence=dict(path=str(target),sha256=audit_sha),package_extra_file_removed=True,PNG_and_PHOTOS_JSON_unchanged=True))
 return x,dict(name='C5' if mode=='c5' else 'photos',elapsed_s=(time.monotonic_ns()-t)/1e9,exit_code=None,resident_job_status=x['status'],receipt_path=str(path))
def link(src,dest):
 dest=Path(dest)
 if dest.is_symlink():dest.unlink()
 elif dest.exists():raise RuntimeError('Refuse overwriting existing batch artifact '+str(dest))
 dest.symlink_to(Path(src).resolve(),target_is_directory=Path(src).is_dir());assert dest.resolve(strict=True)==Path(src).resolve(strict=True)
def await_layout(B):
 p=B/'layout/PRECOMPUTE_RESULT.json'
 while not p.is_file():
  o=read(D/'BATCH_LAYOUT_PRECOMPUTE_OWNER.json');o=next((v for v in o.get('owners',[]) if v['pid']==v.get('ppid',-1) or v.get('ppid') not in {w['pid'] for w in o.get('owners',[])}),o);pid=o.get('pid',o.get('driver_pid'));tick=o.get('start_ticks',o.get('birth'))
  if not pid or not alive(pid,tick):raise RuntimeError('layout source missing with terminal owner '+str(p))
  time.sleep(2)
 return read(p)
def await_cpu_building_slot():
 if args.round4_only:
  owner_path=Path(read(args.round4_plan)['layout_producer_owner']['path'])
  producer=read(owner_path);memory=producer['resource_policy'];workers=producer['worker_owner_paths']
  legacy=read(args.round4_plan).get('legacy_layout_runtime',{})
  legacy_cap=0;legacy_active=0
  if legacy:
   import resource
   verify_ref(legacy['control_receipt'],'owned legacy layout scope control')
   for row in legacy['processes']:
    pid=row['pid']
    if not Path(f'/proc/{pid}').exists():continue
    if not alive(pid,row['start_ticks']):continue  # Completed historical layout PID is now foreign; never inspect/control it.
    state=Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[0]
    if state=='Z':continue
    actual=resource.prlimit(pid,resource.RLIMIT_AS)[1]
    if actual==resource.RLIM_INFINITY or actual<=0 or actual>row['hard_address_space_bytes']:raise RuntimeError('Legacy layout cap exceeds actual recorded scope control')
    legacy_cap+=actual
    if row.get('worker') and state not in ('T','t'):legacy_active+=1
  if len(workers)+legacy_active>3 or memory['coordinator_hard_address_space_bytes']+len(workers)*memory['worker_address_space_bytes']+legacy_cap>8*1024**3:
   raise RuntimeError('Round3 active layout houses or aggregate hard envelope exceed the unchanged limits')
  return
 # The precompute pool owns at most four buildings. Reserve one CPU slot
 # for assembly once at most three of those source buildings remain.
 while sum(not (R/'runs'/p['building']/'v4/batch_v1/layout/PRECOMPUTE_RESULT.json').is_file() for p in plans)>3:
  o=read(D/'BATCH_LAYOUT_PRECOMPUTE_OWNER.json');o=next((v for v in o.get('owners',[]) if v.get('ppid') not in {w['pid'] for w in o.get('owners',[])}),o);pid=o.get('pid',o.get('driver_pid'));tick=o.get('start_ticks',o.get('birth'))
  if not pid or not alive(pid,tick):return
  time.sleep(2)
def metadata(plan,B,layout,status,**extra):
 actual_path=Path(ACTIVE_JOB['layout_directory'])/'LAYOUT.json';actual=read(actual_path) if actual_path.exists() else {}
 kinds=extra.pop('actual_stair_kinds',plan['stair_kinds'])
 return dict(building=plan['building'],status=status,disposition=status,READY_V4=status=='READY',primary_function=plan['primary_function'],secondary_function=plan['secondary_function'],mezzanine_shape=plan['mezzanine_shape'],stair_kinds=kinds,master_stair_kinds=plan['stair_kinds'],handrail_type=plan['handrail_type'],material=plan['material'],config_sha256=CONFIG_SHA,config=dict(path=str(CFG),sha256=CONFIG_SHA),source_tool_shas=sources,layout_seed_index=layout.get('layout_seed_index'),layout_seed_indices_used=layout.get('layout_seed_indices_used',[]),layout_seed_count=len(layout.get('layout_seed_indices_used',[])),layout_seed_budget=20,failed_rules=layout.get('failed_rules',[]),layout_stage_actual_status=layout.get('status'),original_master_plan_sha256=sha(MASTER_PATH),layout_tier=ACTIVE_JOB['tier'],layout_threshold_profile=config['v4'].get('layout_threshold_profile',ACTIVE_JOB['tier']),phase=ACTIVE_JOB['phase'],job_id=ACTIVE_JOB['job_id'],house_directory=str(B),tier_selection=ACTIVE_JOB.get('tier_selection_actual'),phase_plan=ACTIVE_JOB.get('phase_plan'),user_approval=freeze['source_sha256']['BATCH_USER_APPROVAL_20261031.md'],round4_user_instruction=freeze['source_sha256'].get('USER_ROUND4_GOAL_20261102.md'),trial_user_instruction=freeze['source_sha256'].get('USER_ROUND4_TRIAL_POLICY_20261102.md'),resource_policy=RESOURCE_POLICY_REF,SDK_scope='PER_HOUSE',time_policy=TIME_POLICY_REF,CUDA_allocator_policy=ALLOCATOR_POLICY_REF,operational_freeze=dict(path=str(args.freeze),sha256=sha(args.freeze)),technical_recovery=(read(B/'RESOURCE_RETRY_STATE.json').get('technical_resume') if (B/'RESOURCE_RETRY_STATE.json').is_file() else None),approved_operational_revision=REVISION_POLICY_REF,C2_definition_version=2,RGB_technical_fix='ANONYMOUS_WRAPPER_SESSION_INSTANCING',historical_house_directory=REVISION_POLICY['houses'][plan['building']]['old_directory'],historical_disposition=REVISION_POLICY['houses'][plan['building']]['historical_status'],carryover_budget_source=REVISION_POLICY['preparation_snapshot'],UTC=utc(),**extra)

def repair_state(B,P,used,repairs,alt,roles_root=None,terminal_fit_failure=None):
 # A new ordinary metadata record; old v2 budgets/failures remain untouched.
 x=dict(building=B.parent.parent.name,layout_seed_indices_used=sorted(set(used)),
        postC2_C4_layout_repairs_used=repairs,C4_alternatives_used=alt,
        accepted_input_directory=str(P),accepted_layout_seed_index=read(P/'LAYOUT.json')['layout_attempt'],
        accepted_input_sha256={n:sha(P/n) for n in ('LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json')},
        roles_root=str(roles_root) if roles_root else None,terminal_fit_failure=terminal_fit_failure,UTC=utc())
 write(B/'REPAIR_STATE_V3.json',x);return x

def restored_repairs(B,layout,initialP):
 """Recover facts from actual completed outputs, not default-zero counters."""
 module=importlib.import_module(tool('alternative').stem)
 if Path(module.__file__).resolve()!=tool('alternative').resolve():raise RuntimeError('Imported alternative helper differs from frozen tool path')
 named_fit_failure=module.named_fit_failure
 used=set(layout.get('layout_seed_indices_used',range(layout['layout_seed_index']+1)))
 carry=revision.carry_row(ACTIVE_JOB['building'],REVISION_POLICY);used.update(carry['used'])
 P=carry['P'] or initialP;passed=[];roles_root=carry['roles_root'];alt=carry['alt'];terminal=None
 def accepted(directory):
  if not all((directory/n).is_file() for n in ('LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json')):
   raise RuntimeError('Incomplete accepted repair inputs; preserve artifacts '+str(directory))
  doc=read(directory/'LAYOUT.json')
  if doc.get('status')!='LAYOUT_PASS':raise RuntimeError('Accepted repair has no actual ordinary PASS '+str(directory))
  if doc.get('input_sha',{}).get(str(CFG))!=CONFIG_SHA:raise RuntimeError('Accepted repair config binding differs '+str(directory))
  return doc
 for Q in sorted(B.glob('layout_after_check_seed_*')):
  if not Q.is_dir():continue
  if not (Q/'LAYOUT.json').is_file():raise RuntimeError('Incomplete layout repair; do not spend another seed '+str(Q))
  doc=read(Q/'LAYOUT.json');seed=doc.get('layout_attempt')
  if seed not in range(20) or seed!=int(Q.name.rsplit('_',1)[1]):raise RuntimeError('Repair seed evidence differs '+str(Q))
  if doc.get('input_sha',{}).get(str(CFG))!=CONFIG_SHA:raise RuntimeError('Existing repair config binding differs '+str(Q))
  if doc.get('status') not in ('LAYOUT_PASS','LAYOUT_FAIL'):raise RuntimeError('Unknown actual layout repair status '+str(Q))
  used.add(seed)
  if doc['status']=='LAYOUT_PASS':accepted(Q);passed.append(Q);P=Q
 repairs=carry['repairs']+len(passed)
 if repairs>3:raise RuntimeError('Existing evidence already exceeds3 postlayout repairs')
 roots=[B/'c4_alternative_1']+sorted(p for p in (B/'c4_alternative_1').glob('authoring_attempt_*') if p.is_dir())
 completed=[]
 for A in roots:
  receipt=A/'C4_ALTERNATIVE_RECEIPT.json';failure=A/'AUTHORING_FAILURE.json'
  if receipt.is_file():
   r=read(receipt)
   if r.get('status')=='PHYSICS_INPUTS_PREPARED_NOT_CHECKED':
    candidate=A/'physics_inputs';accepted(candidate)
    role_dir=A/'roles_root'
    if not all((role_dir/s['id']/'NATIVE_FACE_ROLES.json').is_file() for s in read(candidate/'stairs_spec.json')['stairs']):raise RuntimeError('Unresolved actual donor role bindings '+str(A))
    completed.append((candidate,role_dir,None))
   elif r.get('status') in ('LAYOUT_FAIL','EXCLUDED_LAYOUT'):
    completed.append((P,None,dict(receipt_path=str(receipt),receipt_sha256=sha(receipt),effective_plan=str(A/'EFFECTIVE_BUILDING_PLAN.json'))))
   else:raise RuntimeError('Unknown existing C4 alternative receipt '+str(receipt))
  elif failure.is_file():
   f=read(failure);code=named_fit_failure(f.get('error_type'),f.get('error',''))
   if code is not None:
    completed.append((P,None,dict(legacy_failure_path=str(failure),legacy_failure_sha256=sha(failure),fit_failure_code=code,effective_plan=str(A/'EFFECTIVE_BUILDING_PLAN.json'))))
   # Unknown/source failures are preserved and are not a consumed alternative.
 if len(completed)>1:raise RuntimeError('More than one completed C4 alternative in actual evidence')
 if completed:P,roles_root,terminal=completed[0];alt=1
 for path in (B/'REPAIR_BUDGET.json',B/'REPAIR_STATE_V3.json'):
  if not path.is_file():continue
  saved=read(path)
  if not set(saved.get('layout_seed_indices_used',[]))<=used:raise RuntimeError('Saved budget has missing immutable layout evidence '+str(path))
  if saved.get('postC2_C4_layout_repairs_used',0)>repairs or saved.get('C4_alternatives_used',0)>alt:raise RuntimeError('Saved consumed budget has missing actual repair evidence '+str(path))
  if path.name=='REPAIR_STATE_V3.json' and saved.get('accepted_input_directory')==str(P):
   if any(sha(P/n)!=h for n,h in saved['accepted_input_sha256'].items()):raise RuntimeError('Accepted input bytes changed since saved repair state')
 accepted(P)
 if any(i not in range(20) for i in used):raise RuntimeError('Actual layout seed evidence outside0..19')
 return dict(P=P,used=sorted(used),repairs=repairs,alt=alt,roles_root=roles_root,terminal_fit_failure=terminal)
def physics_inputs(plan,B):
 P=Path(ACTIVE_JOB.get('initial_physics_directory') or B/'physics_inputs')
 if ACTIVE_JOB.get('initial_geometry_binding'):
  binding=ACTIVE_JOB['initial_geometry_binding'];source=Path(ACTIVE_JOB['layout_directory'])
  if P.resolve()!=(B/'physics_inputs').resolve():raise RuntimeError('Round3 accepted geometry must bind into this new house physics_inputs')
  effective=verify_ref(binding['effective_plan'],'accepted initial geometry plan')
  metas=[str(verify_ref(row,'accepted initial stair metadata')) for row in binding['stair_metadata']]
  receipt_path=P/'PHYSICS_BINDING_RECEIPT.json'
  if not receipt_path.is_file():
   if P.exists():raise RuntimeError('Partial initial physics binding retained without a real receipt: '+str(P))
   checked('BIND_PHYSICS',[PY,str(tool('layout')),'bind-physics','--project-root',str(R),'--config',str(CFG),'--building',plan['building'],'--source-directory',str(source),'--stair-metadata',*metas,'--effective-plan',str(effective),'--approval-record',str(APPROVAL),'--output',str(P)],B/'preparation')
  receipt=read(receipt_path)
  if receipt.get('building')!=plan['building'] or Path(receipt['source_directory']).resolve()!=source.resolve() or Path(receipt['output_directory']).resolve()!=P.resolve():raise RuntimeError('Initial physics binding source/output identity differs')
  expected_inputs={str(CFG):CONFIG_SHA,str(effective):binding['effective_plan']['sha256'],**{str(source/n):sha(source/n) for n in ('LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json')},**{row['path']:row['sha256'] for row in binding['stair_metadata']}}
  if any(receipt.get('input_sha',{}).get(p)!=h for p,h in expected_inputs.items()):raise RuntimeError('Initial physics binding input sources differ')
  expected_outputs={str(P/n):sha(P/n) for n in ('LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json')}
  # The receipt compares in-memory tuples against JSON lists before writing.
  # Compare the two actual serialized audits; retain the original receipt flag.
  if receipt.get('output_sha')!=expected_outputs or receipt.get('ordinary_layout_audit_status')!='PASS' or read(P/'LAYOUT.json')['audit']!=read(source/'LAYOUT.json')['audit']:raise RuntimeError('Initial physics binding output/audit differs')
  if any(receipt.get(k) is not True for k in ('instances_unchanged','units_unchanged','layout_seed_unchanged','stair_offsets_rotations_unchanged','physics_candidate_created')) or receipt.get('layout_only') is not False:raise RuntimeError('Initial physics binding changed accepted geometry or layout')
  return P
 if (P/'PHYSICS_BINDING.json').is_file() or (P/'LAYOUT.json').is_file():return P
 if ACTIVE_JOB.get('initial_physics_directory'):raise RuntimeError('Explicit initial physics inputs are missing; do not reconstruct another seed')
 metas=[]
 for i in range(1,plan['stair_count']+1):
  S=R/'runs'/plan['building']/'v4/stairs'/f'S{i:02d}';m=S/'collision_v9/STAIR_COLLISION.json'
  if not m.is_file():m=S/'collision_v8/STAIR_COLLISION.json'
  metas.append(str(m))
 checked('BIND_PHYSICS',[PY,str(tool('layout')),'bind-physics','--project-root',str(R),'--config',str(CFG),'--building',plan['building'],'--source-directory',ACTIVE_JOB['layout_directory'],'--stair-metadata',*metas,'--approval-record',str(APPROVAL),'--output',str(P)],B/'preparation')
 return P
def prepare_views_and_lights(plan,B,P,suffix=''):
 L=B/('lighting'+suffix);cam=B/('CAMERA_PLAN'+suffix+'.json')
 if not (L/'LIGHTING.json').is_file():checked('LIGHTS'+suffix,[PY,str(tool('lighting')),'--receipt',str(R/'runs/001/v32_batch/scene/AUTHORING_RECEIPT.json'),'--domains',str(P/'SPATIAL_DOMAINS.json'),'--output',str(L),'--placement-mode','frozen-bay-template'],B/'preparation')
 if not cam.is_file():checked('CAMERAS'+suffix,[PY,str(tool('camera')),'--rules',str(D/'CAMERA_RULES_BATCH_V1.json'),'--layout',str(P/'LAYOUT.json'),'--domains',str(P/'SPATIAL_DOMAINS.json'),'--stairs',str(P/'stairs_spec.json'),'--out',str(cam)],B/'preparation')
 return L,cam
def build(plan,B,P,L,cam,index,roles_root=None):
 global house_start,current_clean,FROZEN_INPUT_REF
 admit_clean_stack(plan['building'],index)
 C=B/f'clean_{index:02d}';C.mkdir(exist_ok=False);E=C/'execution';E.mkdir();stages=[];house_start=time.monotonic_ns();started=utc();current_clean=C
 write(C/'CLEAN_E2E_START.json',dict(monotonic_start_ns=house_start,started_utc=started,clean_run=True,config_sha256=CONFIG_SHA,source_tool_shas=sources))
 FROZEN_INPUT_REF=input_snapshot(P,L,cam,roles_root or ACTIVE_JOB['roles_root'],index)
 sdk_started_here=native is None;t=time.monotonic_ns();start_native(plan['building'],E)
 stages.append(dict(name='SDK_start',elapsed_s=(time.monotonic_ns()-t)/1e9,new_process=sdk_started_here,priority_readback=1000))
 shell=next(s['retained_shell'] for s in read(D/'RETAINED_SHELLS.json')['buildings'] if s['building']==plan['building'])
 stages.append(checked('assembly',[PY,str(tool('scene')),'--layout',str(P/'LAYOUT.json'),'--stairs',str(P/'stairs_spec.json'),'--shell',shell,'--lighting',str(L/'LIGHTING.json'),'--out',str(C)],E,timed=True))
 stages.append(checked('voxel',[PY,str(tool('voxel')),'--manifest',str(C/'COLLISION_MANIFEST.json'),'--cache',str(CACHE),'--output',str(C/'voxel')],E,gpu=True,timed=True))
 ct=time.monotonic_ns();checked('C4_geometry',[PY,str(tool('checks')),'--prepare-c4-geometry','--manifest',str(C/'COLLISION_MANIFEST.json'),'--stairs',str(P/'stairs_spec.json'),'--roles-root',str(roles_root or R/'runs'/plan['building']/'v4/c4_v9_geometry'),'--cache',str(CACHE),'--output',str(C/'c4_geometry')],E,timed=True)
 cr=run('C1_C4_GPU',[PY,str(tool('checks')),'--grid',str(C/'voxel'),'--manifest',str(C/'COLLISION_MANIFEST.json'),'--stairs',str(P/'stairs_spec.json'),'--openings',str(P/'SPATIAL_DOMAINS.json'),'--scene',str(C/'root.usda'),'--c4-geometry',str(C/'c4_geometry/C4_GEOMETRY.json'),'--cache',str(CACHE),'--output',str(C/'checks')],E,gpu=True,timed=True)
 stages.append(dict(name='C1_C4',elapsed_s=(time.monotonic_ns()-ct)/1e9,exit_code=cr['exit_code']))
 if not (C/'checks/CHECK_RESULT.json').is_file():raise RuntimeError('C1-C4 tool failed without actual result')
 checks=read(C/'checks/CHECK_RESULT.json')['checks'];assert checks['C1']['status']=='PASS','C1 implementation/source failure'
 c5=None;photos=None
 if all(v['status']=='PASS' for v in checks.values()):
  c5start=time.monotonic_ns();session=start_native(plan['building'],E)
  c5,row=native_job('c5',plan['building'],C,dict(stage=str(C/'collision_package/collision_only.usdc'),spawns=str(C/'checks/SPAWN_POINTS.json'),expected_colliders=read(C/'COLLISION_MANIFEST.json')['actual_enabled_colliders'],out=str(C/'c5')))
  row['elapsed_s']=(time.monotonic_ns()-c5start)/1e9;stages.append(row);checks['C5']=c5
  if c5['status'] in ('ERROR','NEGATIVE_ESCAPE_STOP_ALL'):raise RuntimeError('native tool failure '+c5['status'])
  if c5['status']=='PASS':
   photos,row=native_job('photos',plan['building'],C,dict(stage=str(C/'root.usda'),cameras=str(cam),scale_xy=1.,out=str(C/'photos')));stages.append(row)
   files=photos.get('image_files',[])
   if photos['status']!='PASS' or len(files)!=7 or len(set(files))!=7 or 'collision_gray.png' not in files or not all((C/'photos'/n).is_file() and (C/'photos'/n).stat().st_size>0 for n in files):raise RuntimeError('Rendering/tool output failed; do not charge layout or C2-C4 repair budgets')
 if c5 is not None:
  t=time.monotonic_ns();finish_native();stages.append(dict(name='SDK_shutdown',elapsed_s=(time.monotonic_ns()-t)/1e9,actual_exit_code=0))
 sdk_ref=sdk_summary() if native is None else None
 end=time.monotonic_ns();elapsed=(end-house_start)/1e9
 files=photos.get('image_files',[]) if photos else [];ready=all(checks.get(f'C{i}',{}).get('status')=='PASS' for i in range(1,6)) and bool(photos and photos['status']=='PASS' and len(files)==7 and len(set(files))==7 and 'collision_gray.png' in files and all((C/'photos'/n).is_file() and (C/'photos'/n).stat().st_size>0 for n in files))
 timed_out=elapsed>CLEAN_HARD_LIMIT_S
 ready=ready and not timed_out
 result=dict(status='TIME_LIMIT' if timed_out else 'PASS' if ready else 'FAIL',clean_run=True,started_utc=started,ended_utc=utc(),monotonic_start_ns=house_start,monotonic_end_ns=end,elapsed_s=elapsed,CUDA_allocator_policy=ALLOCATOR_POLICY_REF,**clock_policy.record(elapsed),included_stages=[r['name'] for r in stages],planned_stages=['assembly','voxel','C1_C4','C5','photos'],debug_and_retry_included=False,SDK_started_this_building=True,SDK_startup_included=sdk_started_here,SDK_scope='PER_HOUSE',operational_freeze=dict(path=str(args.freeze),sha256=sha(args.freeze)),SDK_peak_RSS=sdk_ref,resident_session=str(RUN/'native_session'),stages=stages,source_tool_shas=sources,input_sha256={str(P/n):sha(P/n) for n in ['LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json']},config_sha256=CONFIG_SHA,photos_count=len(files),READY_V4=ready)
 write(C/'CLEAN_E2E_RESULT.json',result);write(C/'CHECKS.json',checks)
 if timed_out:raise TimeoutError('clean building exceeds180min')
 house_start=None
 return C,result,checks
def process_house(plan,B,layout):
 bid=plan['building']
 if (B/'STATUS.json').is_file() and read(B/'STATUS.json').get('status') in ('READY','EXCLUDED_LAYOUT','EXCLUDED_CHECKS','EXCLUDED_C5','EXCLUDED_RESOURCE'):return
 if layout['status']!='LAYOUT_PASS':
  if layout.get('disposition')!='EXCLUDED_LAYOUT' and ACTIVE_JOB.get('selection_status')!='EXCLUDED_LAYOUT' and layout['status']!='EXCLUDED_LAYOUT':raise RuntimeError('layout implementation/source failure '+str(layout))
  write(B/'STATUS.json',metadata(plan,B,layout,'EXCLUDED_LAYOUT'));rows.append(dict(building=bid,status='EXCLUDED_LAYOUT',phase=ACTIVE_JOB['phase']));report();return
 await_cpu_building_slot();write(B/'STATUS.json',metadata(plan,B,layout,'RUNNING'))
 initialP=physics_inputs(plan,B);state=restored_repairs(B,layout,initialP)
 P=state['P'];used=state['used'];repairs=state['repairs'];alt=state['alt'];roles_root=state['roles_root'] or Path(ACTIVE_JOB['roles_root'])
 layout['layout_seed_index']=read(P/'LAYOUT.json')['layout_attempt'];layout['layout_seed_indices_used']=used
 repair_state(B,P,used,repairs,alt,roles_root,state['terminal_fit_failure'])
 suffix='' if P==initialP else '_resume_v3_'+sha(P/'LAYOUT.json')[:12]
 if WORKER_REQUEST and WORKER_REQUEST.get('previous_frozen_input'):
  frozen=validate_input_snapshot(WORKER_REQUEST['previous_frozen_input'])
  if Path(frozen['P']).resolve()!=P.resolve():raise RuntimeError('OOM retry accepted layout differs')
  L,cam=(Path(frozen['L']),Path(frozen['cam'])) if frozen.get('L') and frozen.get('cam') else prepare_views_and_lights(plan,B,P,suffix)
 else:
  L,cam=prepare_views_and_lights(plan,B,P,suffix) if not state['terminal_fit_failure'] else (None,None)
 # Failed tool/debug attempts remain immutable. A new clean run starts at
 # assembly without charging any scientific layout/type repair budget.
 attempt=max([int(p.name.split('_')[1]) for p in B.glob('clean_*') if p.is_dir() and p.name.split('_')[-1].isdigit()]+[0])+1
 excluded=None
 while True:
  if state['terminal_fit_failure']:
   # This alternative already ended in an actual named geometry fit failure;
   # retain the last real C4 FAIL instead of rerunning physics or another seed.
   e=read(state['terminal_fit_failure']['effective_plan']);cp=Path(e['original_failed_checks']['path'])
   checks=read(cp);C=cp.parent;result=read(C/'CLEAN_E2E_RESULT.json')
   if checks.get('C4',{}).get('status')!='FAIL':raise RuntimeError('Recovered alternative lacks its real preceding C4 failure')
   excluded='EXCLUDED_CHECKS';break
  C,result,checks=build(plan,B,P,L,cam,attempt,roles_root)
  if result['READY_V4']:break
  release_native(bid)
  write(B/'CHECKS.json',checks);write(B/'E2E_RESULT.json',result)
  if checks.get('C5',{}).get('status')=='FAIL':excluded='EXCLUDED_C5';break
  if repairs<3 and len(set(used))<20 and not alt:
   found=False
   for seed in range(max(used)+1,20):
    Q=B/f'layout_after_check_seed_{seed:02d}';Q.mkdir(exist_ok=False)
    # Fixed approved stairs/domain; change only the equipment layout seed.
    (Q/'stairs_spec.json').write_bytes((P/'stairs_spec.json').read_bytes())
    r=run('LAYOUT_SEED_'+str(seed),[PY,str(tool('layout')),'place','--project-root',str(R),'--config',str(CFG),'--building',bid,'--domain',str(P/'SPATIAL_DOMAINS.json'),'--attempt',str(seed),'--output',str(Q)],B/'preparation')
    if not (Q/'LAYOUT.json').is_file():raise RuntimeError('layout repair implementation failure')
    y=read(Q/'LAYOUT.json')
    if y.get('status') not in ('LAYOUT_PASS','LAYOUT_FAIL') or r['exit_code']!=(0 if y['status']=='LAYOUT_PASS' else 1):raise RuntimeError('Layout repair technical failure; seed is not consumed')
    if y.get('layout_attempt')!=seed or y.get('input_sha',{}).get(str(CFG))!=CONFIG_SHA:raise RuntimeError('Layout repair source/seed binding differs; seed is not consumed')
    used.append(seed)
    if y['status']=='LAYOUT_PASS':
     P=Q;repairs+=1;found=True;layout['layout_seed_index']=seed
     repair_state(B,P,used,repairs,alt,roles_root);break
    repair_state(B,P,used,repairs,alt,roles_root)
   if found:
    attempt+=1;layout['layout_seed_indices_used']=sorted(set(used));L,cam=prepare_views_and_lights(plan,B,P,'_repair'+str(repairs));continue
  if checks.get('C4',{}).get('status')=='FAIL' and not alt:
   budget=repair_state(B,P,used,repairs,alt,roles_root)
   # A technical authoring retry gets a new immutable folder but keeps the
   # same one-alternative identity, donor ordering and accepted equipment seed.
   ar=B/'c4_alternative_1';numbers=[int(p.name.split('_')[2]) for p in ar.glob('authoring_attempt_*') if len(p.name.split('_'))>=3 and p.name.split('_')[2].isdigit()]
   A=ar/f'authoring_attempt_{max(numbers+[0])+1:02d}';seed=read(P/'LAYOUT.json')['layout_attempt']
   # The mutable resume pointer must never be a SHA-bound helper input.
   ar.mkdir(parents=True,exist_ok=True);snapshot=ar/(A.name+'_BUDGET.json')
   with snapshot.open('x') as stream:json.dump(budget,stream,indent=2)
   r=run('C4_ALTERNATIVE_'+A.name,[PY,str(tool('alternative')),'prepare','--project-root',str(R),'--config',str(CFG),'--building',bid,'--source-directory',str(P),'--failed-checks',str(C/'CHECKS.json'),'--budget-state',str(snapshot),'--layout-index',str(seed),'--approval-record',str(APPROVAL),'--output',str(A)],B/'preparation')
   if r['exit_code']!=0:
    receipt=A/'C4_ALTERNATIVE_RECEIPT.json'
    if not receipt.is_file() or read(receipt).get('status') not in ('LAYOUT_FAIL','EXCLUDED_LAYOUT'):
     raise RuntimeError('C4 alternative implementation/source failure; not a scientific exclusion')
   alt=1
   if r['exit_code']==0 and (A/'physics_inputs/LAYOUT.json').is_file():
    P=A/'physics_inputs';roles_root=A/'roles_root';attempt+=1
    repair_state(B,P,used,repairs,alt,roles_root)
    L,cam=prepare_views_and_lights(plan,B,P,'_c4alt_'+A.name);continue
   repair_state(B,P,used,repairs,alt,roles_root,dict(receipt_path=str(A/'C4_ALTERNATIVE_RECEIPT.json'),effective_plan=str(A/'EFFECTIVE_BUILDING_PLAN.json')))
  excluded='EXCLUDED_CHECKS';break
 layout['layout_seed_indices_used']=sorted(set(used))
 if excluded:
  finish_native();sdk_ref=sdk_summary()
  write(B/'STATUS.json',metadata(plan,B,layout,excluded,actual_stair_kinds=[r['kind'] for r in read(P/'stairs_spec.json')['stairs']],SDK_peak_RSS=sdk_ref,failed_checks={k:v['status'] for k,v in checks.items()},last_clean_directory=str(C),postC2_C4_layout_repairs_used=repairs,C4_alternatives_used=alt));rows.append(dict(building=bid,status=excluded,phase=ACTIVE_JOB['phase']));report();return
 for name in ('root.usda','collision_package','photos'):link(C/name,B/name)
 write(B/'CHECKS.json',checks);write(B/'E2E_RESULT.json',result);write(B/'STATUS.json',metadata(plan,B,layout,'READY',actual_stair_kinds=[r['kind'] for r in read(P/'stairs_spec.json')['stairs']],clean_directory=str(C),resident_session=str(RUN/'native_session'),SDK_peak_RSS=result['SDK_peak_RSS'],SDK_started_this_building=result['SDK_started_this_building'],postC2_C4_layout_repairs_used=repairs,C4_alternatives_used=alt))
 report();log=(B/('PACK_resource_'+str(WORKER_REQUEST['resource_attempt'])+'.log')).open('x');pack_baseline=ops.checkpoint();argv=[PY,str(tool('deliver')),'pack','--project-root',str(R),'--building',bid,'--cpu-core','63'];argv+=['--house-directory',str(B)] if ACTIVE_JOB['phase'] in ('phase2','round4') else []
 if args.round4_only:argv+=['--cohort-plan',str(args.round4_plan)]
 pack=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT,env=env,preexec_fn=lambda:setup([63]),start_new_session=True);pack_owner=proc_fact(pack,'PACK');packs.append((bid,pack,log,pack_baseline,pack_owner));write(B/'PACK_OWNER.json',pack_owner)
 release_native(bid);rows.append(dict(building=bid,status='READY',phase=ACTIVE_JOB['phase'],tier=ACTIVE_JOB['tier'],elapsed_s=result['elapsed_s']));print('BUILDING_READY '+json.dumps(rows[-1]),flush=True)
def finish_packs():
 for bid,p,log,baseline,owner_row in packs:
  while ops.poll(p) is None:own_tick();time.sleep(min(.5,report_if_due()))
  log.close()
  if p.returncode==-9:
   proof=killed_evidence(p,baseline,owner_row)
   if proof['confirmed']:raise OOMInterruption(proof)
  if p.returncode:raise RuntimeError('Pack failed '+bid+' '+str(p.returncode))
 packs.clear()
class OOMInterruption(RuntimeError):
 def __init__(self,evidence):
  super().__init__('Kernel OOM interrupted owned process; no scientific budget charged')
  self.evidence=evidence

def own_tick():
 owned.scan()
 if house_start is not None:
  elapsed=(time.monotonic_ns()-house_start)/1e9
  if elapsed>CLEAN_HARD_LIMIT_S:raise TimeoutError('clean building exceeds180min; stop and report stuck')
  if elapsed>clock_policy.SOFT_LIMIT_S and current_clean is not None:
   marker=current_clean/'OVER_60MIN_RECORD_ONLY.json'
   if not marker.is_file():
    event=dict(UTC=utc(),elapsed_s=elapsed,action='RECORD_ONLY_CONTINUE',**clock_policy.record(elapsed))
    write(marker,event);print('OVER_60MIN_RECORD_ONLY '+json.dumps(event),flush=True)

def proc_fact(process,role):
 row=ops.fact(process.pid)
 if row['oom_score_adj']!=1000:raise RuntimeError('Owned '+role+' lacks verified oom_score_adj1000')
 row.update(role=role,started_utc=utc())
 return row

def killed_evidence(process,baseline,owner_row):
 row=dict(owner_row,exit_code=process.returncode)
 if globals().get('own_gpu_pids'):row['GPU_host_pids']=sorted(own_gpu_pids)
 return ops.classify_oom(baseline,ops.memory(),[row],ops.kernel())

def input_snapshot(P,L,cam,roles_root,index):
 files={str(P/n):sha(P/n) for n in ('LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json')}
 files.update({str(CFG):CONFIG_SHA,str(L/'LIGHTING.json'):sha(L/'LIGHTING.json'),str(cam):sha(cam)})
 shell=next(s['retained_shell'] for s in read(D/'RETAINED_SHELLS.json')['buildings'] if s['building']==ACTIVE_JOB['building'])
 files[str(Path(shell))]=sha(shell)
 for stair in read(P/'stairs_spec.json')['stairs']:
  rp=Path(roles_root)/stair['id']/'NATIVE_FACE_ROLES.json'
  if rp.is_file():files[str(rp)]=sha(rp)
 data=dict(schema='Factory80FrozenTrialInputV1',kind='COMPLETE_PHYSICS_INPUT',building=ACTIVE_JOB['building'],P=str(P),L=str(L),cam=str(cam),roles_root=str(roles_root),source_files=files,
  source_signature=WORKER_REQUEST['source_signature'],resource_attempt=WORKER_REQUEST['resource_attempt'])
 data['signature']=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
 path=RUN/f'FROZEN_INPUT_clean_{index:02d}.json';write(path,data)
 return dict(path=str(path),sha256=sha(path))

def validate_input_snapshot(reference):
 p=verify_ref(reference,'OOM retry exact accepted input');data=read(p)
 if data['building']!=ACTIVE_JOB['building']:raise RuntimeError('Retry input building differs')
 for path,expected in data['source_files'].items():
  if sha(path)!=expected:raise RuntimeError('OOM retry input bytes changed: '+path)
 if hashlib.sha256(json.dumps(data['source_files'],sort_keys=True).encode()).hexdigest()!=data['signature']:raise RuntimeError('Retry input signature differs')
 return data

def preserve_interrupted_preparation(house,event):
 stage=event.get('OOM_evidence',{}).get('interrupted_stage',{});argv=stage.get('argv',[])
 output=None
 for flag in ('--output','--out'):
  if flag in argv:output=Path(argv[argv.index(flag)+1]);break
 if output is None or not output.exists() or output.parent.resolve()!=house.resolve():return
 if not output.name.startswith(('physics_inputs','layout_after_check_seed_','lighting','CAMERA_PLAN')):return
 frozen=read(event['frozen_input']['path']) if event.get('frozen_input') else {}
 if str(output) in [frozen.get('P'),frozen.get('L'),frozen.get('cam')]:return
 history=house/'resource_interruption_history'/f"try_{event['resource_attempt']:02d}";history.mkdir(parents=True,exist_ok=True)
 target=history/output.name
 if target.exists():raise RuntimeError('Partial preparation history already exists')
 output.rename(target)

def reap_orphan_sdk(worker_run,house,number):
 owner=worker_run/'NATIVE_OWNER.json'
 if not owner.is_file() or (worker_run/'SDK_EXIT_RECEIPT.json').is_file():return
 row=read(owner)
 try:pid,status,usage=os.wait4(row['pid'],os.WNOHANG)
 except ChildProcessError:return
 if not pid:return
 doc=dict(row,ended_utc=utc(),actual_exit_code=os.waitstatus_to_exitcode(status),resource_attempt=number,
  actual_peak_RSS_bytes=int(usage.ru_maxrss)*1024,wait4=dict(raw_wait_status=status,peak_RSS_bytes=int(usage.ru_maxrss)*1024),
  no_cross_house_reuse=True,oom_score_adj_readback=1000,scope='ORPHAN_REAPED_BY_OWNED_CHILD_SUBREAPER',session_directory=str(worker_run/'native_session'))
 write(worker_run/'SDK_EXIT_RECEIPT.json',doc);out=gpu_identity.receipt_directory(house,number,read(worker_run/'OWNER.json'));out.mkdir(parents=True,exist_ok=True);write(out/'SDK_EXIT_RECEIPT.json',doc)

def sdk_summary(house=None,building=None):
 house=Path(house or B);building=building or ACTIVE_JOB['building'];path=house/'SDK_RSS.json';rows=[]
 for p in sorted(house.glob('resource_attempts/*/SDK_EXIT_RECEIPT.json')):
  doc=read(p);rows.append(dict(path=str(p),sha256=sha(p),**doc))
 if (RUN/'SDK_EXIT_RECEIPT.json').is_file():
  doc=read(RUN/'SDK_EXIT_RECEIPT.json')
  if not any(r['pid']==doc['pid'] and r['birth']==doc['birth'] for r in rows):rows.append(dict(path=str(RUN/'SDK_EXIT_RECEIPT.json'),sha256=sha(RUN/'SDK_EXIT_RECEIPT.json'),**doc))
 result=dict(schema='Factory80PerHouseSDKRSSV1',building=building,scope='FRESH_SDK_PER_HOUSE_NO_CROSS_HOUSE_REUSE',instances=rows,
  actual_peak_RSS_bytes=max((r['actual_peak_RSS_bytes'] for r in rows),default=None),oom_score_adj_required=1000)
 write(path,result);return dict(path=str(path),sha256=sha(path))

def finish_native(allow_interruption=False):
 global native,nlog,NATIVE_BASELINE,NATIVE_OWNER,native_peak
 if native is None:return None
 process=native
 if ops.poll(process) is None:
  NATIVE_BASELINE=ops.checkpoint();command(dict(mode='shutdown'));process.stdin.close()
  deadline=time.monotonic()+min(60,max(.1,CLEAN_HARD_LIMIT_S-(time.monotonic_ns()-house_start)/1e9)) if house_start else time.monotonic()+60
  while ops.poll(process) is None:
   own_tick();native_peak=max(native_peak,rss(process))
   if time.monotonic()>deadline:raise RuntimeError('Owned per-house SDK did not exit after shutdown')
   time.sleep(.2)
 actual=dict(NATIVE_OWNER,ended_utc=utc(),actual_exit_code=process.returncode,resource_attempt=WORKER_REQUEST['resource_attempt'],building=ACTIVE_JOB['building'],
  actual_peak_RSS_bytes=max(native_peak,getattr(process,'factory80_wait4',{}).get('peak_RSS_bytes',0)),wait4=getattr(process,'factory80_wait4',{}),
  session_directory=str(RUN/'native_session'),no_cross_house_reuse=True,oom_score_adj_readback=1000)
 if (RUN/'native_session/SESSION.json').is_file():actual['SESSION']=dict(path=str(RUN/'native_session/SESSION.json'),sha256=sha(RUN/'native_session/SESSION.json'))
 if not actual['wait4']:raise RuntimeError('Native SDK missing actual wait4 RSS evidence')
 write(RUN/'SDK_EXIT_RECEIPT.json',actual);out=gpu_identity.receipt_directory(B,WORKER_REQUEST['resource_attempt'],read(RUN/'OWNER.json'));out.mkdir(parents=True,exist_ok=True);write(out/'SDK_EXIT_RECEIPT.json',actual)
 nlog.close();nlog=None;native=None
 if process.returncode:
  evidence=killed_evidence(process,NATIVE_BASELINE,NATIVE_OWNER)
  if evidence['confirmed'] and not allow_interruption:raise OOMInterruption(evidence)
  if not allow_interruption:raise RuntimeError('Native SDK exit without confirmed kernel OOM: '+str(process.returncode))
 return actual

def save_technical_oom(error):
 global house_start
 reference=globals().get('FROZEN_INPUT_REF');frozen=read(reference['path']) if reference else None
 repair=read(B/'REPAIR_STATE_V3.json') if (B/'REPAIR_STATE_V3.json').is_file() else None
 if repair and (not frozen or repair['accepted_input_directory']!=frozen['P']):
  accepted=Path(repair['accepted_input_directory']);files={str(accepted/n):sha(accepted/n) for n in ('LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json')};files[str(CFG)]=CONFIG_SHA
  frozen=dict(schema='Factory80FrozenTrialInputV1',kind='PREPARATION_INPUT',building=ACTIVE_JOB['building'],P=str(accepted),L=None,cam=None,roles_root=repair.get('roles_root') or ACTIVE_JOB['roles_root'],source_files=files,signature=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest())
  fp=RUN/'FROZEN_PREPARATION_INPUT.json';write(fp,frozen);reference=dict(path=str(fp),sha256=sha(fp))
 record=dict(schema='Factory80TrialOOMInterruptionV1',status='TECHNICAL_OOM_INTERRUPTION',building=ACTIVE_JOB['building'],UTC=utc(),
  interrupted_monotonic_ns=time.monotonic_ns(),resource_attempt=WORKER_REQUEST['resource_attempt'],OOM_evidence=error.evidence,
  frozen_input=reference,frozen_input_signature=frozen['signature'] if frozen else WORKER_REQUEST['source_signature'],
  source_signature=WORKER_REQUEST['source_signature'],scientific_FAIL=False,scientific_budget_charged=False,
  repair_state=repair,
  partial_elapsed_s=(time.monotonic_ns()-house_start)/1e9 if house_start else None,clean_directory=str(current_clean) if current_clean else None)
 before=B/'STATUS.json'
 if before.exists():write(RUN/'STATUS_BEFORE_RESOURCE_INTERRUPTION.json',read(before))
 write(RUN/'TECHNICAL_OOM_INTERRUPTION.json',record)
 if current_clean:write(current_clean/'TECHNICAL_OOM_INTERRUPTION.json',record)
 write(before,metadata(plan,B,layout,'RESOURCE_OOM_INTERRUPTION',READY=False,technical_interruption=dict(path=str(RUN/'TECHNICAL_OOM_INTERRUPTION.json'),sha256=sha(RUN/'TECHNICAL_OOM_INTERRUPTION.json'))))
 house_start=None;return record

def await_retry_deadline(state):
 previous=state['attempts'][-1]
 deadline=previous['interrupted_monotonic_ns']+600_000_000_000
 while time.monotonic_ns()<deadline:
  remaining=(deadline-time.monotonic_ns())/1e9
  write(RUN/'RESOURCE_RETRY_WAIT.json',dict(building=state['building'],UTC=utc(),remaining_s=remaining,retry_delay_s=600,scientific_budget_charged=False))
  time.sleep(min(30,remaining,report_if_due()))

def source_signature(job):
 files={str(Path(job['layout_directory'])/n):sha(Path(job['layout_directory'])/n) for n in ('LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json')}
 files.update({job['config']['path']:job['config']['sha256'],str(MASTER_PATH):sha(MASTER_PATH)})
 return hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()

def supervise_house(descriptor):
 global rows,ACTIVE_JOB
 job=resolve_job(descriptor);planned,house,initial_layout=activate_job(job)
 if initial_layout['status']!='LAYOUT_PASS':
  process_house(planned,house,initial_layout)
  if not any(r['building']==planned['building'] for r in rows):rows.append(dict(building=planned['building'],status='EXCLUDED_LAYOUT',phase='round4'))
  return
 prior=read(house/'STATUS.json') if (house/'STATUS.json').is_file() else {}
 if prior.get('status')=='READY':
  receipt=house/'delivery/REMOTE_DELIVERY_RECEIPT.json'
  if not receipt.is_file() or read(receipt).get('status')!='SUCCESS':raise RuntimeError('Existing READY has unfinished packing; preserve and recover instead of duplicate physics')
  rows.append(dict(building=planned['building'],status='READY',phase='round4'));return
 if prior.get('status') in ('EXCLUDED_CHECKS','EXCLUDED_C5','EXCLUDED_RESOURCE','PHOTO_OOM_PENDING_FIX'):
  rows.append(dict(building=planned['building'],status=prior['status'],phase='round4'));return
 state_path=house/'RESOURCE_RETRY_STATE.json';state=read(state_path) if state_path.is_file() else dict(schema='Factory80ResourceRetryStateV1',building=planned['building'],attempts=[],max_retries=2,retry_delay_s=600,source_signature=source_signature(job))
 if 'approved_operational_revision' not in state:
  if state.get('attempts') or state.get('active_attempt'):raise RuntimeError('New resource epoch cannot reset existing evidence')
  state['approved_operational_revision']=REVISION_POLICY_REF;state['resource_epoch']='round4_v2';state['resource_retries_used']=0
  carry=REVISION_POLICY['houses'][planned['building']]
  if carry['source_frozen_input']:
   state['technical_resume']=dict(kind='APPROVED_RENDER_AND_C2_REVISION',frozen_input=carry['source_frozen_input'],frozen_input_signature=carry['frozen_input']['signature'],scientific_budget_charged=False)
  write(state_path,state)
 if state['approved_operational_revision']!=REVISION_POLICY_REF:raise RuntimeError('Resource epoch revision binding differs')
 if state.get('same_input_retry_forbidden'):raise RuntimeError('PHOTO_OOM_PENDING_FIX forbids same-input retry')
 if state['source_signature']!=source_signature(job):raise RuntimeError('Resource retry source inputs changed')
 if state.get('active_attempt'):raise RuntimeError('Unresolved existing owned attempt; verify its PID/exit instead of duplicate or reset')
 if state['attempts'] and not all(r.get('OOM_evidence',{}).get('confirmed') for r in state['attempts']):raise RuntimeError('Unresolved prior resource attempt; do not restart or reset budget')
 for number in range(len(state['attempts']),3):
  if number:
   await_retry_deadline(state);preserve_interrupted_preparation(house,state['attempts'][-1])
  admit_clean_stack(planned['building'],number+1)
  token=uuid.uuid4().hex;worker_run=RUN/'house_workers'/planned['building']/f'try_{number:02d}'
  request=dict(schema='Factory80HouseTrialRequestV1',building=planned['building'],descriptor=descriptor,supervisor_run=str(RUN),resource_attempt=number,source_signature=state['source_signature'],
   previous_frozen_input=state['attempts'][-1].get('frozen_input') if number else (state.get('technical_resume') or {}).get('frozen_input'),
   retry_input_signature=state['attempts'][-1]['frozen_input_signature'] if number else (state.get('technical_resume') or {}).get('frozen_input_signature',state['source_signature']))
  request_path=RUN/'requests'/f"{planned['building']}_try_{number:02d}.json";write(request_path,request)
  logs=RUN/'worker_logs';logs.mkdir(exist_ok=True);log=(logs/request_path.with_suffix('.log').name).open('x')
  baseline=ops.checkpoint();started_ns=time.monotonic_ns();childenv=env.copy();childenv[ops.TOKEN_KEY]=token
  argv=[PY,str(Path(__file__).resolve()),'--round4-only','--round4-plan',str(args.round4_plan),'--freeze',str(args.freeze),'--resource-policy',str(args.resource_policy),'--resource-policy-sha256',RESOURCE_POLICY_REF['sha256'],'--run-directory',str(worker_run),'--worker-request',str(request_path)]
  process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,env=childenv,preexec_fn=lambda:setup(range(48,64)),start_new_session=True)
  audit=ops.Ownership(token,RUN/'house_ownership'/f"{planned['building']}_try_{number:02d}.json");owner_row=proc_fact(process,'HOUSE_WORKER')
  write(RUN/'CURRENT_WORKER.json',dict(**owner_row,building=planned['building'],resource_attempt=number,request=dict(path=str(request_path),sha256=sha(request_path)),kernel_before=baseline,ownership_token=token))
  state['active_attempt']=dict(**owner_row,resource_attempt=number,worker_run=str(worker_run),request_path=str(request_path),kernel_before=baseline,ownership_token=token,started_monotonic_ns=started_ns);write(state_path,state)
  while ops.poll(process) is None:
   audit.scan();report_if_due();time.sleep(.5)
  audit.scan(True);log.close();exitcode=process.returncode
  result=read(worker_run/'RESULT.json') if (worker_run/'RESULT.json').is_file() else None
  if exitcode==0 and result and result.get('status')=='REMOTE_BATCH_TERMINAL':
   state.pop('active_attempt',None)
   state.update(status='HOUSE_TERMINAL',successful_resource_attempt=number,resource_retries_used=number,worker_result=dict(path=str(worker_run/'RESULT.json'),sha256=sha(worker_run/'RESULT.json')))
   write(state_path,state);rows.append(dict(building=planned['building'],status=read(house/'STATUS.json')['status'],tier=job['tier'],phase='round4',resource_retries_used=number));report();return
  interruption=read(worker_run/'TECHNICAL_OOM_INTERRUPTION.json') if (worker_run/'TECHNICAL_OOM_INTERRUPTION.json').is_file() else None
  if interruption and interruption.get('OOM_evidence',{}).get('confirmed'):
   event=interruption
  else:
   proof=killed_evidence(process,baseline,owner_row)
   if not proof['confirmed']:raise RuntimeError('Owned worker stopped without confirmed kernel OOM: '+str(dict(exitcode=exitcode,result=result)))
   frozen_refs=sorted(worker_run.glob('FROZEN_INPUT_clean_*.json'))
   frozen_ref=dict(path=str(frozen_refs[-1]),sha256=sha(frozen_refs[-1])) if frozen_refs else None
   event=dict(OOM_evidence=proof,interrupted_monotonic_ns=time.monotonic_ns(),frozen_input=frozen_ref,
    frozen_input_signature=read(frozen_ref['path'])['signature'] if frozen_ref else state['source_signature'],clean_directory=str(house/Path(frozen_ref['path']).stem.removeprefix('FROZEN_INPUT_')) if frozen_ref else None,scientific_budget_charged=False)
  actions=audit.terminate();reap_orphan_sdk(worker_run,house,number);event.update(resource_attempt=number,started_monotonic_ns=started_ns,retry_input_signature=request['retry_input_signature'],
   worker_exit_code=exitcode,worker_wait4=getattr(process,'factory80_wait4',{}),cleanup=actions,worker_run=str(worker_run),UTC=utc())
  photo_pending=revision.photo_oom_classification(event,worker_run)
  if photo_pending:
   state=revision.mark_photo_pending(state,event,photo_pending);write(state_path,state)
   out=house/'photo_oom_history'/f'resource_slot_{number:02d}';out.mkdir(parents=True,exist_ok=False);write(out/'PHOTO_OOM_PENDING_FIX.json',dict(event=event,classification=photo_pending))
   repair=read(house/'REPAIR_STATE_V3.json');effective_layout=dict(initial_layout,layout_seed_indices_used=repair['layout_seed_indices_used'],layout_seed_index=read(Path(repair['accepted_input_directory'])/'LAYOUT.json')['layout_attempt'])
   write(house/'STATUS.json',metadata(planned,house,effective_layout,'PHOTO_OOM_PENDING_FIX',postC2_C4_layout_repairs_used=repair['postC2_C4_layout_repairs_used'],C4_alternatives_used=repair['C4_alternatives_used'],photo_oom_pending_fix=dict(path=str(out/'PHOTO_OOM_PENDING_FIX.json'),sha256=sha(out/'PHOTO_OOM_PENDING_FIX.json')),resource_retries_used=state['resource_retries_used'],SDK_peak_RSS=sdk_summary(house,planned['building'])))
   rows.append(dict(building=planned['building'],status='PHOTO_OOM_PENDING_FIX',phase='round4',scientific_budget_charged=False,resource_retry_budget_charged=False));report();return
  state.pop('active_attempt',None);state['attempts'].append(event);state.update(status='RESOURCE_RETRY_WAIT' if number<2 else 'RESOURCE_RETRIES_EXHAUSTED',resource_retries_used=number)
  out=house/'resource_attempts'/f'try_{number:02d}';out.mkdir(parents=True,exist_ok=True);write(out/'OOM_INTERRUPTION.json',event);write(state_path,state)
  if (house/'STATUS.json').is_file():write(out/'STATUS_BEFORE_RESOURCE_INTERRUPTION.json',read(house/'STATUS.json'))
  repair=read(house/'REPAIR_STATE_V3.json') if (house/'REPAIR_STATE_V3.json').is_file() else {}
  effective_layout=dict(initial_layout)
  if repair:
   effective_layout['layout_seed_indices_used']=repair['layout_seed_indices_used'];effective_layout['layout_seed_index']=read(Path(repair['accepted_input_directory'])/'LAYOUT.json')['layout_attempt']
  counters=dict(postC2_C4_layout_repairs_used=repair.get('postC2_C4_layout_repairs_used',0),C4_alternatives_used=repair.get('C4_alternatives_used',0))
  write(house/'STATUS.json',metadata(planned,house,effective_layout,'RESOURCE_OOM_INTERRUPTION',**counters,technical_interruption=dict(path=str(out/'OOM_INTERRUPTION.json'),sha256=sha(out/'OOM_INTERRUPTION.json'))))
  if number==2:
   if not ops.retry_state_valid(state):raise RuntimeError('Resource exclusion lacks3 kernel OOMs,600s delays or exact retry inputs')
   write(house/'STATUS.json',metadata(planned,house,effective_layout,'EXCLUDED_RESOURCE',**counters,resource_exclusion=dict(path=str(state_path),sha256=sha(state_path)),resource_retries_used=2,SDK_peak_RSS=sdk_summary(house,planned['building'])))
   rows.append(dict(building=planned['building'],status='EXCLUDED_RESOURCE',tier=job['tier'],phase='round4',resource_retries_used=2));report();return
  report()

stop=None;phase='round4_trial';worker_OOM=None;cohort_pending_at_end=None
try:
 jobs,phase_receipt=wait_phase2()
 if WORKER_REQUEST:
  if WORKER_REQUEST.get('schema')!='Factory80HouseTrialRequestV1':raise RuntimeError('Worker request differs')
  descriptor=WORKER_REQUEST['descriptor'];job=resolve_job(descriptor);plan,B,layout=activate_job(job)
  process_house(plan,B,layout);finish_packs();report()
 else:
  report()
  for descriptor in jobs:
   descriptor=dict(descriptor,phase_plan=dict(path=phase_receipt['path'],sha256=phase_receipt['sha256']))
   supervise_house(descriptor)
  report();cohort_pending_at_end=read(D/'ROUND4_REPORT.json')['counts']['PENDING']
  write(RUN/'ROUND4_TERMINAL.json',dict(status='ROUND4_TERMINAL' if cohort_pending_at_end==0 else 'ROUND4_QUEUE_FINISHED_WITH_PENDING_FIX',UTC=utc(),rows=rows,scope_buildings=list(ROUND4_SCOPE),execution_subset=REVISION_POLICY['execution_order'],approved_operational_revision=REVISION_POLICY_REF,resource_policy=RESOURCE_POLICY_REF,SDK_scope='PER_HOUSE'))
except OOMInterruption as e:
 if not WORKER_REQUEST:raise
 worker_OOM=save_technical_oom(e);print('TECHNICAL_OOM_INTERRUPTION '+json.dumps(worker_OOM),flush=True)
except BaseException as e:
 stop=dict(error=repr(e),traceback=traceback.format_exc(),UTC=utc())
 if isinstance(e,TimeoutError) and house_start is not None:
  stop.update(partial_elapsed_s=(time.monotonic_ns()-house_start)/1e9,time_policy=TIME_POLICY_REF,hard_stop_s=CLEAN_HARD_LIMIT_S)
  house_start=None  # Stop scientific work; permit bounded SDK shutdown and actual wait4 receipt.
 print('BATCH_STOP '+json.dumps(stop),flush=True)
 if WORKER_REQUEST and globals().get('B') is not None:
  if current_clean:write(current_clean/'TECHNICAL_STOP.json',dict(status='TIME_LIMIT' if isinstance(e,TimeoutError) else 'TECHNICAL_STOP',stop=stop,scientific_budget_charged=False))
  if globals().get('layout') is not None:write(B/'STATUS.json',metadata(plan,B,layout,'TIME_LIMIT' if isinstance(e,TimeoutError) else 'TOOL_STOP',technical_stop=stop))
finally:
 if WORKER_REQUEST:
  try:
   if active is not None and ops.poll(active) is None:
    os.kill(active.pid,signal.SIGTERM)
    deadline=time.monotonic()+30
    while ops.poll(active) is None and time.monotonic()<deadline:time.sleep(.1)
   if native is not None:finish_native(allow_interruption=True)
  except BaseException as cleanup_error:write(RUN/'CLEANUP_ERROR.json',dict(error=repr(cleanup_error)))
  owned.terminate(exclude=[os.getpid()])
 write(RUN/'RESULT.json',dict(status='TECHNICAL_OOM_INTERRUPTION' if worker_OOM else 'STOPPED' if stop else 'REMOTE_QUEUE_FINISHED_WITH_PENDING_FIX' if cohort_pending_at_end else 'REMOTE_BATCH_TERMINAL',phase=phase,rows=rows,stop=stop,ended_utc=utc(),scope_buildings=list(ROUND4_SCOPE),resource_policy=RESOURCE_POLICY_REF,SDK_scope='PER_HOUSE',scientific_budget_charged=False if worker_OOM else None,delivery_mode='REMOTE_ONLY',local_downloads_performed=False))
raise SystemExit(75 if worker_OOM else 1 if stop else 0)
