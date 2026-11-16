"""Finite CPU dependency cache, accepting only actual final archive fingerprints."""
import json,pathlib,tempfile,time
from datetime import datetime,timezone
from scene_expansion_extract_objects import WORK,sha,extract
TASK=WORK.parent.parent
rows=json.loads((WORK/'all_missing_object_downloaded_selection.json').read_text())['selected']
out=WORK/'all_missing_object_cache_verified.json';log=WORK/'all_missing_object_cache_verified.jsonl'
records={}
for prior in [WORK/'new263_early_object_cache_verified.json',out]:
 if prior.exists():
  try:
   for r in json.loads(prior.read_text()).get('records',[]):records[r['package']]=r
  except (ValueError,KeyError):pass
if log.exists():
 for line in log.read_text().splitlines():
  try:
   r=json.loads(line);records[r['package']]=r
  except (ValueError,KeyError):pass

def critical():
 return [*TASK.glob('CRITICAL_STOP*.json'),*TASK.glob('work/CRITICAL_STOP*.json'),*TASK.glob('work/*/CRITICAL_STOP*.json')]

def save():
 result={'generated_utc':datetime.now(timezone.utc).isoformat(),'count':len(rows),'processed_count':len(records),'passed':sum(r['status']=='PASS' for r in records.values()),'records':sorted(records.values(),key=lambda r:r['package'])}
 result['status']='PASS' if result['passed']==len(rows) else 'PARTIAL'
 temporary=out.with_suffix('.json.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');temporary.replace(out)

save()
while True:
 waiting=0;processed=0
 for r in rows:
  flags=critical()
  if flags:
   save();print(json.dumps({'status':'CRITICAL_STOP_OBSERVED','files':[str(p) for p in flags]}),flush=True);raise SystemExit(2)
  previous=records.get(r['package'],{})
  if previous.get('status')=='PASS':continue
  package=WORK/'packages'/r['source']/r['package']
  if not package.is_file():waiting+=1;continue
  stamp=[package.stat().st_size,package.stat().st_mtime_ns]
  if previous.get('status')=='FAIL' and previous.get('package_stat')==stamp:continue
  try:
   if stamp[0]!=r['size'] or sha(package)!=r['sha256']:raise RuntimeError('object final official package size/SHA mismatch')
   root=WORK/'official_objects'/r['source'];root.mkdir(parents=True,exist_ok=True);target=root/r['directory']
   if not target.exists():
    with tempfile.TemporaryDirectory(prefix='object_ready_',dir=WORK) as temp:
     names=extract(package,temp)
     if names!=[r['directory']]:raise RuntimeError('object package top-level mismatch')
     (pathlib.Path(temp)/r['directory']).rename(target)
   expected=target/(r['directory']+'.usda')
   if not expected.is_file() or expected.stat().st_size<=0:raise RuntimeError('object root USD missing')
   files=[{'path':str(f),'size':f.stat().st_size,'sha256':sha(f)} for f in sorted(target.rglob('*')) if f.is_file()]
   link=WORK/'objects'/r['source']/r['directory'];link.parent.mkdir(parents=True,exist_ok=True)
   if not link.exists() and not link.is_symlink():link.symlink_to(target,target_is_directory=True)
   if not (link/(r['directory']+'.usda')).is_file():raise RuntimeError('object symlink is unreadable')
   result=dict(r,status='PASS',object_root=str(target),source_files=files,package_stat=stamp)
  except Exception as error:result=dict(r,status='FAIL',error=f'{type(error).__name__}: {error}',package_stat=stamp)
  records[r['package']]=result;processed+=1
  with log.open('a') as stream:stream.write(json.dumps(result,ensure_ascii=False)+'\n')
  if processed%25==0:
   save();print(json.dumps({'objects_processed':len(records),'total':len(rows),'passed':sum(r['status']=='PASS' for r in records.values())}),flush=True)
 save()
 print(json.dumps({'status':'WAITING_FINAL_ARCHIVES' if waiting else 'COMPLETE_OBJECT_ATTEMPTS','waiting':waiting,'new_attempts':processed,'passed':sum(r['status']=='PASS' for r in records.values())}),flush=True)
 if not waiting:break
 time.sleep(30)
