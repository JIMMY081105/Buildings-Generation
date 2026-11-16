#!/usr/bin/env python3
import argparse,hashlib,json,pathlib,subprocess,tempfile
from datetime import datetime,timezone
from scene_expansion_zstd import extract
WORK=pathlib.Path('/cephfs/shared/yixing/outputs/factory80_20261011/work/scene_expansion')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--selection',required=True);p.add_argument('--audit');p.add_argument('--out',required=True);a=p.parse_args();rows=json.load(open(a.selection))['selected'];result=[]
 for i,r in enumerate(rows,1):
  try:
   package=WORK/'packages'/r['source']/r['package']
   if package.stat().st_size!=r['size'] or sha(package)!=r['sha256']:raise RuntimeError('object official package size/SHA mismatch')
   root=WORK/'official_objects'/r['source'];root.mkdir(parents=True,exist_ok=True);target=root/r['directory']
   if not target.exists():
    with tempfile.TemporaryDirectory(prefix='object_',dir=WORK) as temp:
     names=extract(package,temp)
     if names!=[r['directory']]:raise RuntimeError('object package top-level mismatch')
     (pathlib.Path(temp)/r['directory']).rename(target)
   expected=target/(r['directory']+'.usda')
   if not expected.is_file() or expected.stat().st_size<=0:raise RuntimeError('object root USD missing')
   files=[{'path':str(f),'size':f.stat().st_size,'sha256':sha(f)} for f in sorted(target.rglob('*')) if f.is_file()]
   link=WORK/'objects'/r['source']/r['directory'];link.parent.mkdir(parents=True,exist_ok=True)
   if not link.exists() and not link.is_symlink():link.symlink_to(target,target_is_directory=True)
   if not (link/(r['directory']+'.usda')).is_file():raise RuntimeError('object symlink is unreadable')
   result.append(dict(r,status='PASS',object_root=str(target),source_files=files))
  except Exception as e:result.append(dict(r,status='FAIL',error=f'{type(e).__name__}: {e}'))
  if i%25==0 or i==len(rows):print(json.dumps({'objects_extracted':i,'total':len(rows),'passed':sum(r['status']=='PASS' for r in result)}),flush=True)
 if a.audit:
  for r in json.load(open(a.audit))['rows']:
   if not r['present']:continue
   root=pathlib.Path(r['expected_usda']).parent;link=WORK/'objects'/r['source']/r['directory'];link.parent.mkdir(parents=True,exist_ok=True)
   if not link.exists() and not link.is_symlink():link.symlink_to(root,target_is_directory=True)
   if not (link/(r['directory']+'.usda')).is_file():raise RuntimeError('reused object symlink unreadable')
 pathlib.Path(a.out).write_text(json.dumps({'generated_utc':datetime.now(timezone.utc).isoformat(),'count':len(rows),'passed':sum(r['status']=='PASS' for r in result),'records':result},ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
