"""Can-fail glTF source color audit; normal/AO never counts as base color."""
from pathlib import Path
import json,struct,hashlib,datetime,sys

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

def inspect_color(path):
 p=Path(path);binary_size=None
 if p.suffix.lower()=='.glb':
  with p.open('rb') as f:
   head=f.read(20);assert len(head)==20 and head[:4]==b'glTF' and struct.unpack_from('<I',head,4)[0]==2;size,kind=struct.unpack_from('<I4s',head,12);assert kind==b'JSON' and size<=64*1024**2;doc=json.loads(f.read(size));tail=f.read(8)
   if tail:binary_size=struct.unpack('<I4s',tail)[0]
 else:doc=json.loads(p.read_text())
 def valid_image(index):
  images=doc.get('images',[])
  if not isinstance(index,int) or not 0<=index<len(images):return False
  image=images[index]
  if 'uri' in image:
   uri=image['uri'];return bool(uri.startswith('data:') and ',' in uri and len(uri.split(',',1)[1])>8 or not uri.startswith(('http:','https:','data:')) and (p.parent/uri).is_file())
  views=doc.get('bufferViews',[]);i=image.get('bufferView',-1)
  if not isinstance(i,int) or not 0<=i<len(views):return False
  v=views[i];return bool(v.get('byteLength',0)>0 and (binary_size is None or v.get('buffer',0)!=0 or v.get('byteOffset',0)+v['byteLength']<=binary_size))
 rows=[]
 for mi,mesh in enumerate(doc.get('meshes',[])):
  for pi,primitive in enumerate(mesh.get('primitives',[])):
   material_index=primitive.get('material');materials=doc.get('materials',[]);material=materials[material_index] if isinstance(material_index,int) and 0<=material_index<len(materials) else {};info=material.get('pbrMetallicRoughness',{}).get('baseColorTexture');texture_ok=False
   if info is not None:
    i=info.get('index',-1);textures=doc.get('textures',[])
    if isinstance(i,int) and 0<=i<len(textures):
     texture=textures[i];image_index=texture.get('source',texture.get('extensions',{}).get('KHR_texture_basisu',{}).get('source',-1));texture_ok=valid_image(image_index)
   a=primitive.get('attributes',{}).get('COLOR_0');accessors=doc.get('accessors',[]);vertex_ok=isinstance(a,int) and 0<=a<len(accessors) and accessors[a].get('count',0)>0 and accessors[a].get('type') in ['VEC3','VEC4'];rows.append({'mesh_index':mi,'primitive_index':pi,'material_index':material_index,'baseColor_texture_with_present_image':texture_ok,'COLOR_0_accessor_present':vertex_ok,'status':'PASS' if texture_ok or vertex_ok else 'FAIL'})
 return {'status':'PASS' if rows and all(q['status']=='PASS' for q in rows) else 'FAIL','source_path':str(p),'source_sha256':sha(p),'source_images':len(doc.get('images',[])),'primitive_count':len(rows),'primitives':rows,'scope':'Actual source GLB/glTF JSON and referenced color image existence or COLOR_0 accessor presence. Normal/occlusion/baseColorFactor alone does not pass. This is not an exported shader/color preservation or missing-component proof.'}

def library_models(root):
 r=Path(root);lib=json.loads((r/'LIBRARY_MANIFEST.json').read_text());rows=[]
 for row in lib['assets'].values():
  path=next((p for p in row.get('paths',[]) or [row.get('path','')] if Path(p).suffix.lower() in ['.gltf','.glb']),None)
  if path:rows.append({**row,'path':path})
 prior=r/'library/reused_museum_v1/polyhaven/DOWNLOADS.json'
 if prior.exists():
  for row in json.loads(prior.read_text())['assets']:
   p=Path(row['path'].replace('/cephfs/shared/yixing/outputs/museum_v1_20261104/assets',str(r/'library/reused_museum_v1')))
   if p.exists() and p.suffix.lower() in ['.gltf','.glb']:rows.append({**row,'path':str(p)})
 return {row['source']+':'+str(row['id']):row for row in rows}

def audit_used(candidate,models,cache):
 d=Path(candidate);layout=json.loads((d/'LAYOUT.json').read_text());licenses=json.loads((d/'ASSET_LICENSES.json').read_text())['assets'];bykey={q['source']+':'+str(q.get('id')):q for q in licenses};rows=[]
 for key in sorted({q['source_key'] for q in layout if q.get('kind')=='exhibit' and q.get('source_key') in models}):
  licensed=bykey.get(key,{});path=licensed.get('path') or models[key]['path'];report=cache.get(path)
  if report is None:report=inspect_color(path);cache[path]=report
  expected={q['source_model_sha256'] for q in layout if q.get('source_key')==key and q.get('source_model_sha256')};bound=not expected or expected=={report['source_sha256']};rows.append({'source_key':key,'source_title':models[key].get('title'),'source_path':path,'source_sha256':report['source_sha256'],'source_color_status':report['status'],'layout_source_hash_match':bound,'status':'PASS' if report['status']=='PASS' and bound else 'FAIL','documented_monochrome_exception':'NONE_APPLIED'})
 return {'status':'PASS' if all(q['status']=='PASS' for q in rows) else 'FAIL','candidate':str(d),'models_examined':len(rows),'uncolored_or_unbound':[q for q in rows if q['status']!='PASS'],'models':rows,'scope':'Every actual LAYOUT source model resolved through licensed source/library. No guessed monochrome exemption. Failures require replacement or separately researched material evidence; source presence does not prove component completeness.'}

if __name__=='__main__':
 r=Path('/cephfs/shared/yixing/outputs/museum29_20261107');models=library_models(r);cache={};reports={}
 for key,row in models.items():
  p=row['path']
  if p not in cache:cache[p]=inspect_color(p)
  reports[key]={'source_key':key,'title':row.get('title'),'source':row['source'],**cache[p]}
 negative=next(q for key,q in reports.items() if '2632d078-a354-412c-825e-e70d2f546793' in key);assert negative['status']=='FAIL','KNOWN_NO_COLOR_SEATLESS_CHAIR_ESCAPED_COLOR_GATE';negative={**negative,'negative_control_status':'FAIL_AS_REQUIRED','known_missing_component':'Removable slip seat absent, as actually documented in ADJUST24; no mesh-completeness claim from this color audit.'};registry=json.loads((r/'evidence/BUILDING_REGISTRY.json').read_text());ready={q['building']:audit_used(q['candidate'],models,cache) for q in registry if q['status']=='READY'};pending={bid:audit_used(r/'buildings'/bid/cand,models,cache) for bid,cand in [('M20','furnished_candidate002'),('M15','furnished_candidate002'),('M24','furnished_candidate002'),('M05','furnished_candidate001'),('M10','furnished_candidate002'),('M18','furnished_candidate001'),('M07','furnished_candidate014')]};doc={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'validator_sha256':sha(__file__),'status':'AUDITED_NOT_ALL_ACCEPTED','negative_chair':negative,'library_models':reports,'ready_buildings':ready,'pending_built_candidates':pending,'scope':'Source audit only, no geometry/material/source asset changes. Failed READYv1 packages stay immutable and require M3 revision. No monochrome exemptions inferred.'};out=r/'evidence/SCAN_COLOR24_INITIAL_AUDIT.json';assert not out.exists();out.write_text(json.dumps(doc,indent=2));print(json.dumps({'output':str(out),'library_count':len(reports),'library_fail':sum(x['status']=='FAIL' for x in reports.values()),'negative_chair':negative['status'],'ready':{b:{'status':q['status'],'models':q['models_examined'],'failed_keys':[x['source_key'] for x in q['uncolored_or_unbound']]} for b,q in ready.items()},'pending':{b:{'status':q['status'],'failed_keys':[x['source_key'] for x in q['uncolored_or_unbound']]} for b,q in pending.items()}},indent=2))
