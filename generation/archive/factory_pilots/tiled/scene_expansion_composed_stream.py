"""One CPU process: actual OpenUSD recursion and stat-bound byte/SHA receipts."""
import collections,hashlib,json,pathlib,resource,time
from datetime import datetime,timezone
from pxr import UsdUtils,Sdf,Work
TASK=pathlib.Path('/cephfs/shared/yixing/outputs/factory80_20261011')
WORK=TASK/'work/scene_expansion';OUT=WORK/'composed_source_closure_verified.json'
LOG=WORK/'composed_source_closure_verified.jsonl';RECEIPTS=WORK/'dependency_file_hash_receipts.jsonl'
resource.setrlimit(resource.RLIMIT_AS,(8*1024**3,8*1024**3))
if hasattr(Work,'SetConcurrencyLimit'):Work.SetConcurrencyLimit(1)
def utc():return datetime.now(timezone.utc).isoformat()
def read(p,default):
 try:return json.loads(p.read_text())
 except (FileNotFoundError,ValueError):return default
def critical():return [*TASK.glob('CRITICAL_STOP*.json'),*TASK.glob('work/CRITICAL_STOP*.json'),*TASK.glob('work/*/CRITICAL_STOP*.json')]
def signature(s):return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
cache={};parsed_mapping={};records={};last_layers=[]
if RECEIPTS.exists():
 for line in RECEIPTS.read_text().splitlines():
  try:
   r=json.loads(line);cache[(r['authored_path'],r['resolved_path'])]=r
  except (ValueError,KeyError):pass
for r in read(OUT,{'records':[]})['records']:
 if r.get('status')=='PASS':
  try:
   for f in r['resolved_files']:
    p=pathlib.Path(f['authored_path']);real=p.resolve()
    if str(real)!=f['resolved_path'] or signature(real.stat())!=f['stat_signature']:raise RuntimeError('changed file/path since prior receipt')
   records[r['scene']]=r
  except (OSError,RuntimeError):pass
scene_deps=collections.defaultdict(set)
for d in read(WORK/'current_pool_dependencies.json',{})['dependencies']:
 for scene in d['scenes']:scene_deps[scene].add((d['source'],d['directory']))
missing_keys={(r['source'],r['directory']) for r in read(WORK/'all_missing_object_downloaded_selection.json',{})['selected']}

def fingerprint(p):
 authored=str(p);real=p.resolve();resolved=str(real);before=signature(real.stat())
 if not real.is_file() or before[2]<=0:raise RuntimeError('missing/empty resolved dependency: '+authored)
 key=(authored,resolved);prior=cache.get(key)
 if prior and prior['stat_signature']==before:
  return dict(prior,reused_actual_sha_receipt=True,checked_utc=utc())
 h=hashlib.sha256()
 with real.open('rb') as stream:
  for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
 if signature(real.stat())!=before or str(p.resolve())!=resolved:raise RuntimeError('resolved file changed during hash: '+authored)
 record={'authored_path':authored,'resolved_path':resolved,'bytes':before[2],'sha256':h.hexdigest(),'stat_signature':before,'hashed_utc':utc()}
 record['receipt_id']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
 with RECEIPTS.open('a') as stream:stream.write(json.dumps(record)+'\n')
 cache[key]=record;return dict(record,reused_actual_sha_receipt=False,checked_utc=utc())

def sources():
 result={}
 for name in ['existing184_verified.json','base_extracted_verified.json','batch2_extracted_verified.json']:
  for r in read(WORK/name,{'records':[]})['records']:
   if r.get('status')=='PASS':result[r['scene']]=r
 return result

def save(source_rows,pending):
 result={'schema':'Factory80StreamedUsdDependencyClosureV1','generated_utc':utc(),'source_pool_count':630,'source_files_verified_count_at_probe':len(source_rows),'input_file_integrity_ready_source_count':sum(r['status']=='PASS' for r in records.values()),'attempted_source_count':len(records),'records':sorted(records.values(),key=lambda r:r['scene']),'pending':pending,'method':'Actual UsdUtils.ComputeAllDependencies on selected source root, followed by bytes/SHA for every resolved layer/asset and every full source inventory file. Cached actual hash receipts reused only with unchanged authored/resolved paths and stat signature.','cpu_work_thread_limit':1,'process_address_space_limit_bytes':8*1024**3,'cannot_support':'File composition/dependency integrity only; no geometry, visual, Standability or humanoid acceptance.'}
 result['status']='PASS_INPUT_FILE_INTEGRITY' if result['input_file_integrity_ready_source_count']>=600 else 'PARTIAL'
 temporary=OUT.with_suffix('.json.tmp');temporary.write_text(json.dumps(result,indent=2)+'\n');temporary.replace(OUT)

print(json.dumps({'status':'STARTED_CPU_COMPOSITION_HASH_STREAM','rlimit_as_bytes':8*1024**3,'memory_max':pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip() if pathlib.Path('/sys/fs/cgroup/memory.max').exists() else 'UNAVAILABLE'}),flush=True)
while True:
 if critical():print(json.dumps({'status':'CRITICAL_STOP_OBSERVED'}),flush=True);break
 available=sources();object_doc=read(WORK/'all_missing_object_cache_verified.json',{'records':[]});complete={(r['source'],r['directory']) for r in object_doc['records'] if r['status']=='PASS'}
 pending=[];attempted=0
 order=sorted(available,key=lambda scene:(scene!='train_26914_ceiling',len(scene_deps[scene]),scene))
 for scene in order:
  if critical():print(json.dumps({'status':'CRITICAL_STOP_OBSERVED'}),flush=True);raise SystemExit(2)
  source=available[scene];signature_input=source['source_tree_sha256']
  prior=records.get(scene,{})
  if prior.get('status')=='PASS':continue
  wait=sorted((scene_deps[scene]&missing_keys)-complete)
  if wait:pending.append({'scene':scene,'status':'PENDING_OBJECT_EXTRACTED_FILE_SHA','object_directories':['/'.join(k) for k in wait]});continue
  if prior.get('source_tree_sha256')==signature_input and prior.get('status')=='FAIL':continue
  started=time.monotonic();record={'scene':scene,'source_path':source['source_path'],'source_tree_sha256':signature_input,'started_utc':utc(),'resolved_files':[]}
  try:
   expected={r['path']:r for r in source['source_files']}
   root=pathlib.Path(source['source_path'])/'scene.usda';t=time.monotonic()
   layers,assets,unresolved=UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(root)));last_layers=layers
   record.update(composition_root=str(root),compute_elapsed_seconds=time.monotonic()-t,layer_paths=[str(layer.realPath or layer.identifier) for layer in layers],asset_paths=list(assets),unresolved=list(unresolved))
   if unresolved:raise RuntimeError('ComputeAllDependencies reported unresolved dependencies')
   paths=sorted(set(record['layer_paths']+record['asset_paths']+list(expected)));t=time.monotonic()
   for name in paths:
    f=fingerprint(pathlib.Path(name));record['resolved_files'].append(f)
    if name in expected and (f['bytes']!=expected[name]['size'] or f['sha256']!=expected[name]['sha256']):raise RuntimeError('full source bytes/SHA changed since source inventory: '+name)
   record.update(status='PASS',hash_elapsed_seconds=time.monotonic()-t,actual_file_count=len(record['resolved_files']),actual_file_bytes=sum(f['bytes'] for f in record['resolved_files']))
  except Exception as error:record.update(status='FAIL',error=f'{type(error).__name__}: {error}')
  record.update(completed_utc=utc(),elapsed_seconds=time.monotonic()-started,rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
  records[scene]=record;attempted+=1
  with LOG.open('a') as stream:stream.write(json.dumps(record)+'\n')
  save(available,pending)
  print(json.dumps({k:record.get(k) for k in ['scene','status','compute_elapsed_seconds','hash_elapsed_seconds','actual_file_count','elapsed_seconds','rss_kib','error']}),flush=True)
 save(available,pending)
 if len(available)==630 and not pending:print(json.dumps({'status':'COMPLETE_AVAILABLE_SOURCE_ATTEMPTS'}),flush=True);break
 print(json.dumps({'status':'WAITING_SOURCE_OR_OBJECT_FILE_SHA','available_sources':len(available),'pending_object_sources':len(pending),'new_attempts':attempted,'closed_sources':sum(r['status']=='PASS' for r in records.values())}),flush=True)
 time.sleep(30)
