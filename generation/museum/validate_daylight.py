"""Reject the actual banned sky bytes, regardless of filename or catalog label."""
from pathlib import Path
import os,sys,json,hashlib,datetime
os.environ['PXR_WORK_THREAD_LIMIT']='1'
from pxr import Usd,UsdLux
d=Path(sys.argv[1]);expected=sys.argv[2] if len(sys.argv)>2 else 'PASS';sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();banned='462a3cc63bfcd9fe0299137a0f33f4ade84e237f4cb6115fe46f5ab400693fe6';stage=Usd.Stage.Open(str(d/'scene/museum.usdc'));rows=[];errors=[]
for p in stage.Traverse():
 if p.IsA(UsdLux.DomeLight):
  a=p.GetAttribute('inputs:texture:file').Get()
  if a:
   path=Path(a.resolvedPath or str(d/'scene'/a.path));digest=sha(path) if path.is_file() else None;rows.append({'path':str(p.GetPath()),'actual_texture':a.path,'resolved_file':str(path),'sha256':digest,'intensity':p.GetAttribute('inputs:intensity').Get()})
   if not digest:errors.append('UNRESOLVED_DOME_TEXTURE')
   if digest==banned:errors.append('ADJUST38_ACTUAL_M16_M15_DARK_SKY_BYTES_BANNED')
if not rows:errors.append('ACTUAL_DOME_SOURCE_NOT_FOUND')
doc={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'FAIL' if errors else 'PASS','validator_sha256':sha(__file__),'source_visual_sha256':sha(d/'scene/museum.usdc'),'source_collision_sha256':sha(d/'collision_package/collision_only.usdc'),'actual_domes':rows,'banned_actual_texture_sha256':banned,'violations':errors,'negative_control_status':'FAIL_AS_REQUIRED' if errors and expected=='FAIL' else None,'scope':'Actual byte rejection for ADJUST38, not filename/label inference; final native01 daylight, shadows and appearance still require actual review. For M16 ribbonv2 specifically, source sun25–40deg/side-front and visible fin shadows also required.'};p=d/('DAYLIGHT38.json' if expected=='PASS' else 'DAYLIGHT38_NEGATIVE.json')
if p.exists():
 old=json.loads(p.read_text());assert all(old.get(k)==doc.get(k) for k in ['status','validator_sha256','source_visual_sha256','source_collision_sha256','actual_domes','banned_actual_texture_sha256','violations','negative_control_status']),'DAYLIGHT38_PRIOR_REPORT_STALE';print('DAYLIGHT38_REVALIDATED',doc['status'])
else:p.write_text(json.dumps(doc,indent=2));print('DAYLIGHT38',doc['status'],errors)
sys.exit(0 if doc['status']==expected else 2)
