"""Finite CPU source-cache job driven only by independently fingerprinted final archives."""
import json,pathlib,tempfile,time
from datetime import datetime,timezone
import scene_expansion_remote as original
from scene_expansion_zstd import extract
TASK=original.ROOT;WORK=original.WORK;OUT=WORK/'official_scenes/extracted';OUT.mkdir(parents=True,exist_ok=True)
families=[]
for label,selection in [('base',WORK/'exact_selection.json'),('batch2',WORK/'batch2/exact_selection.json')]:
 rows=json.load(open(selection))['selected'];out=WORK/(label+'_extracted_verified.json');prior=json.load(open(out)) if out.exists() else {'records':[]};records={r['scene']:r for r in prior['records']};families.append((label,rows,out,records))

def save(label,rows,out,records):
 result={'generated_utc':datetime.now(timezone.utc).isoformat(),'mode':'extract','count':len(rows),'processed_count':len(records),'passed':sum(r['status']=='PASS' for r in records.values()),'records':sorted(records.values(),key=lambda r:r['scene'])}
 result['status']='PASS' if result['passed']==len(rows) else 'PARTIAL'
 temporary=out.with_name(out.name+'.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');temporary.replace(out);return result

while True:
 critical=[*TASK.glob('CRITICAL_STOP*.json'),*TASK.glob('work/CRITICAL_STOP*.json'),*TASK.glob('work/*/CRITICAL_STOP*.json')]
 if critical:
  print(json.dumps({'status':'CRITICAL_STOP_OBSERVED','files':[str(p) for p in critical]}),flush=True);break
 waiting=0;processed=0
 for label,rows,out,records in families:
  for row in rows:
   prev=records.get(row['scene'],{})
   if prev.get('status')=='PASS':continue
   package=WORK/'packages'/row['source']/row['package']
   if not package.is_file():waiting+=1;continue
   stamp=[package.stat().st_size,package.stat().st_mtime_ns]
   if prev.get('status')=='FAIL' and prev.get('package_stat')==stamp:continue
   try:
    if stamp[0]!=row['size'] or original.sha(package)!=row['sha256']:raise RuntimeError('final package size/SHA mismatch; source not accepted')
    if not (OUT/row['scene']).exists():
     with tempfile.TemporaryDirectory(prefix='extract_ready_',dir=WORK) as temp:
      tops=extract(package,temp)
      for top in tops:
       src=pathlib.Path(temp)/top;dst=OUT/top
       if dst.exists():raise RuntimeError('refusing source-folder overwrite')
       src.rename(dst)
    result=original.inspect(row,OUT);result.update(package_path=str(package),package_size=row['size'],package_sha256=row['sha256'],package_stat=stamp)
   except Exception as error:result={'scene':row['scene'],'status':'FAIL','package_stat':stamp,'error':f'{type(error).__name__}: {error}'}
   records[row['scene']]=result;processed+=1
   with (WORK/(label+'_extract_ready.jsonl')).open('a') as stream:stream.write(json.dumps(result,ensure_ascii=False)+'\n')
   save(label,rows,out,records)
   print(json.dumps({'family':label,'source':row['scene'],'status':result['status'],'cached_pass':sum(r['status']=='PASS' for r in records.values()),'total':len(rows)}),flush=True)
  save(label,rows,out,records)
 if not waiting:
  print(json.dumps({'status':'COMPLETE_SOURCE_ATTEMPTS','processed_this_cycle':processed}),flush=True);break
 print(json.dumps({'status':'WAITING_VERIFIED_FINAL_ARCHIVES','waiting':waiting,'new_source_attempts':processed}),flush=True);time.sleep(30)
