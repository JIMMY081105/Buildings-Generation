#!/usr/bin/env python3
"""Scene inventory/extraction only. No quality criterion is introduced here."""
import argparse, hashlib, json, pathlib, subprocess, tempfile
from datetime import datetime, timezone
from scene_expansion_zstd import extract

ROOT=pathlib.Path('/cephfs/shared/yixing/outputs/factory80_20261011')
WORK=ROOT/'work/scene_expansion'
OLD=pathlib.Path('/cephfs/shared/yixing/outputs/factory10/work/official_scenes')

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''): h.update(b)
 return h.hexdigest()

def inspect(row,root):
 records=[]
 for variant in [row['scene'].removesuffix('_ceiling'),row['scene']]:
  base=root/variant
  for name in ['scene.usda','scene_metadata.json','Payload/Geometry.usda','Payload/GeometryLibrary.usdc']:
   p=base/name
   if not p.is_file() or p.stat().st_size<=0: raise RuntimeError(f'missing/nonreadable required source file: {p}')
  for p in sorted(base.rglob('*')):
   if p.is_file(): records.append({'path':str(p),'size':p.stat().st_size,'sha256':sha(p)})
 if not records: raise RuntimeError('zero files')
 tree=hashlib.sha256(''.join(f"{r['path']}\0{r['size']}\0{r['sha256']}\n" for r in records).encode()).hexdigest()
 return {'scene':row['scene'],'source':row['source'],'version':row['version'],'package':row['package'],
         'source_path':str(root/row['scene']),'variants_verified':2,'file_count':len(records),
         'source_tree_sha256':tree,'source_files':records,'status':'PASS'}

def main():
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['inventory','extract']);p.add_argument('--selection',required=True);p.add_argument('--out',required=True)
 a=p.parse_args(); rows=json.load(open(a.selection))['selected'];out=[]
 root=OLD/'extracted' if a.mode=='inventory' else WORK/'official_scenes/extracted'
 root.mkdir(parents=True,exist_ok=True)
 for i,row in enumerate(rows,1):
  try:
   if a.mode=='extract':
    package=WORK/'packages'/row['source']/row['package']
    if package.stat().st_size!=row['size'] or sha(package)!=row['sha256']: raise RuntimeError('package size/SHA mismatch')
    if not (root/row['scene']).exists():
     with tempfile.TemporaryDirectory(prefix='extract_',dir=WORK) as temp:
      names=extract(package,temp)
      for n in names:
       src=pathlib.Path(temp)/n;dst=root/n
       if dst.exists(): raise RuntimeError(f'refusing overwrite {dst}')
       src.rename(dst)
   result=inspect(row,root)
   package=(OLD if a.mode=='inventory' else WORK)/'packages'/row['source']/row['package']
   if package.is_file(): result.update(package_path=str(package),package_size=package.stat().st_size,package_sha256=sha(package))
   else: result['package_archive_available']=False
   out.append(result)
  except Exception as e: out.append({'scene':row['scene'],'status':'FAIL','error':f'{type(e).__name__}: {e}'})
  if i%10==0 or i==len(rows): print(json.dumps({'done':i,'total':len(rows),'passed':sum(r['status']=='PASS' for r in out)}),flush=True)
 result={'generated_utc':datetime.now(timezone.utc).isoformat(),'mode':a.mode,'count':len(rows),'passed':sum(r['status']=='PASS' for r in out),'records':out}
 pathlib.Path(a.out).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':'PASS' if result['passed']==len(rows) else 'PARTIAL','passed':result['passed'],'count':len(rows)}),flush=True)
if __name__=='__main__':main()
