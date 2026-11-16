"""Model-level ADJUST24 predicate, preserving per-primitive diagnostics."""
from pathlib import Path
import json,datetime,sys
import scan_color as original
sha=original.sha

def inspect_color(path):
 q=original.inspect_color(path);q['status']='PASS' if q['primitives'] and any(x['status']=='PASS' for x in q['primitives']) else 'FAIL';q['colorless_primitive_count']=sum(x['status']=='FAIL' for x in q['primitives']);q['scope']='Actual model must contain a valid baseColor texture image or COLOR_0 accessor, exactly as ADJUST24. Normal/AO/baseColorFactor alone cannot pass. Per-primitive diagnostics retained; untextured glass/metal submaterials do not make an otherwise colored model colorless. This does not prove missing-part completeness or exported material preservation.';return q

def audit_used(candidate,models,cache):
 d=Path(candidate);layout=json.loads((d/'LAYOUT.json').read_text());licenses=json.loads((d/'ASSET_LICENSES.json').read_text())['assets'];bykey={q['source']+':'+str(q.get('id')):q for q in licenses}
 for key in {q['source_key'] for q in layout if q.get('kind')=='exhibit' and q.get('source_key') in models}:
  path=bykey.get(key,{}).get('path') or models[key]['path']
  if path not in cache:cache[path]=inspect_color(path)
 return original.audit_used(d,models,cache)

def negative(models,cache):
 key=next(k for k in models if '2632d078-a354-412c-825e-e70d2f546793' in k);p=models[key]['path'];q=cache.get(p) or inspect_color(p);assert q['status']=='FAIL';return {**q,'negative_control_status':'FAIL_AS_REQUIRED','known_missing_component':'Slip seat absent according to actual ADJUST24; color audit does not claim component detection.'}

if __name__=='__main__':
 r=Path('/cephfs/shared/yixing/outputs/museum29_20261107');models=original.library_models(r);cache={};validator={'entry_sha256':sha(__file__),'primitive_reader_sha256':sha(Path(original.__file__))}
 if len(sys.argv)==3 and sys.argv[1]=='--candidate':
  d=Path(sys.argv[2]);doc=audit_used(d,models,cache);doc.update(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),validator=validator,negative_chair=negative(models,cache));out=d/'SCAN_COLOR24.json';assert not out.exists();out.write_text(json.dumps(doc,indent=2));print('SCAN_COLOR24',d.parent.name,d.name,doc['status'],doc['models_examined'],[x['source_key'] for x in doc['uncolored_or_unbound']]);sys.exit(0 if doc['status']=='PASS' else 2)
 reports={}
 for key,row in models.items():
  path=row['path']
  if path not in cache:cache[path]=inspect_color(path)
  reports[key]={'source_key':key,'title':row.get('title'),'source':row['source'],**cache[path]}
 registry=json.loads((r/'evidence/BUILDING_REGISTRY.json').read_text());ready={q['building']:audit_used(q['candidate'],models,cache) for q in registry if q['status']=='READY'};pending={b:audit_used(r/'buildings'/b/c,models,cache) for b,c in [('M20','furnished_candidate002'),('M15','furnished_candidate002'),('M24','furnished_candidate002'),('M05','furnished_candidate001'),('M10','furnished_candidate002'),('M18','furnished_candidate001'),('M07','furnished_candidate015')]};doc={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'validator':validator,'status':'AUDITED_NOT_ALL_ACCEPTED','negative_chair':negative(models,cache),'library_models':reports,'ready_buildings':ready,'pending_built_candidates':pending,'scope_correction':'V1 added a stricter all-primitives color condition not required by ADJUST24 and falsely flagged Camera_01 and lantern_chandelier_01 despite actual valid baseColor textures on main parts. V2 uses each model has color criterion, retains all primitive details and keeps truly colorless chair failing. V1 report/source remain intact. No uncolored model exemption applied.'};out=r/'evidence/SCAN_COLOR24_V2_AUDIT.json';assert not out.exists();out.write_text(json.dumps(doc,indent=2));print(json.dumps({'output':str(out),'library_count':len(reports),'library_fail':sum(x['status']=='FAIL' for x in reports.values()),'negative_chair':doc['negative_chair']['status'],'ready':{b:{'status':q['status'],'failed_keys':[x['source_key'] for x in q['uncolored_or_unbound']]} for b,q in ready.items()},'pending':{b:{'status':q['status'],'failed_keys':[x['source_key'] for x in q['uncolored_or_unbound']]} for b,q in pending.items()}},indent=2))
