#!/usr/bin/env python3
"""Fail-capable filesystem dependency verification, never an acceptance gate."""
import argparse,concurrent.futures,hashlib,json,pathlib,re
from datetime import datetime,timezone
TASK=pathlib.Path('/cephfs/shared/yixing/outputs/factory80_20261011/work/scene_expansion')
OLD=pathlib.Path('/cephfs/shared/yixing/outputs/factory10/work/objects')
REFERENCE=re.compile(r'@([^@]+)@')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--dependencies',required=True);p.add_argument('--out',required=True);p.add_argument('--hash',action='store_true');p.add_argument('--workers',type=int,default=4);p.add_argument('--require-task-links',action='store_true');a=p.parse_args()
 doc=json.load(open(a.dependencies));rows=[]
 def one(d):
  candidates=[TASK/'objects'/d['source']/d['directory']/(d['directory']+'.usda'),OLD/d['source']/d['directory']/(d['directory']+'.usda')]
  if a.require_task_links:candidates=candidates[:1]
  root=next((p for p in candidates if p.is_file() and p.stat().st_size>0),candidates[0]);refs=[];missing=[];builtin=[]
  if not root.is_file() or root.stat().st_size<=0:missing.append(str(root))
  else:
   pending=[root];visited=set()
   while pending:
    source=pending.pop();real=source.resolve()
    if real in visited:continue
    visited.add(real)
    if not real.is_file() or real.stat().st_size<=0:missing.append(str(real));continue
    r={'path':str(real),'size':real.stat().st_size}
    if a.hash:r['sha256']=sha(real)
    refs.append(r)
    if real.suffix.lower()=='.usda':
     for ref in REFERENCE.findall(real.read_text(encoding='utf-8')):
      if ref in ['OmniPBR.mdl','OmniGlass.mdl']:builtin.append(ref);continue
      q=(real.parent/ref).resolve()
      if not q.is_file() or q.stat().st_size<=0:missing.append(str(q))
      elif q.suffix.lower()=='.usda':pending.append(q)
      else:
       row={'path':str(q),'size':q.stat().st_size}
       if a.hash:row['sha256']=sha(q)
       refs.append(row)
  return dict(d,present=not missing,expected_usda=str(root),source_files=refs,missing_references=sorted(set(missing)),builtin_renderer_resources_unverified=sorted(set(builtin)))
 checkpoint=pathlib.Path(a.out+'.jsonl')
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool,checkpoint.open('a',encoding='utf-8') as stream:
  futures={pool.submit(one,d):d for d in doc['dependencies']}
  for future in concurrent.futures.as_completed(futures):
   try:row=future.result()
   except Exception as e:row=dict(futures[future],present=False,source_files=[],missing_references=[],error=f'{type(e).__name__}: {e}')
   rows.append(row);stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
   if len(rows)%100==0 or len(rows)==len(doc['dependencies']):print(json.dumps({'objects_checked':len(rows),'total':len(doc['dependencies']),'missing':sum(not r['present'] for r in rows)}),flush=True)
 rows.sort(key=lambda r:(r['source'],r['directory']))
 out={'schema':'Factory80ExternalObjectFileIntegrityV1','generated_utc':datetime.now(timezone.utc).isoformat(),'dependency_count':len(rows),'present_count':sum(r['present'] for r in rows),'missing_count':sum(not r['present'] for r in rows),'status':'PASS' if all(r['present'] for r in rows) else 'FAIL','hashes_computed':a.hash,'task_cache_paths_required':a.require_task_links,'rows':rows,'cannot_support':'Filesystem readability/size/hash only; no USD composition, geometry, visual acceptance or humanoid validation.'}
 pathlib.Path(a.out).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:out[k] for k in ['status','dependency_count','present_count','missing_count','hashes_computed']}),flush=True)
if __name__=='__main__':main()
