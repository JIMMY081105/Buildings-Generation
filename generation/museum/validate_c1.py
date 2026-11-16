"""Source manifest, exact geometry, coverage and native USD dependency readback."""
from pathlib import Path
import sys,json,hashlib,datetime
import numpy as np
from pxr import Usd,UsdGeom,UsdPhysics,UsdUtils,Sdf
from PIL import Image
d=Path(sys.argv[1]);source=d/'collision_package/collision_only.usdc';stage=Usd.Stage.Open(str(source));assert stage;inventory=json.loads((d/'collision_package/collision_only_PACKAGE.json').read_text());receipt=json.loads((d/'EXPORT_RECEIPT.json').read_text());raw=json.loads((d/'RAW_MODEL.json').read_text());rows={r['path']:r for r in inventory['colliders']};expected={r['path'] for r in receipt['visual_rows'] if r['collision']};assert len(rows)==len(inventory['colliders']);errors=[];checked=[];cache=UsdGeom.XformCache()
if UsdGeom.GetStageMetersPerUnit(stage)!=1 or UsdGeom.GetStageUpAxis(stage)!='Z':errors.append('WRONG_COORDINATE_CONTRACT')
actual={}
for p in stage.Traverse():
 if not p.HasAPI(UsdPhysics.CollisionAPI):continue
 path=str(p.GetPath());actual[path]=p
 if not p.IsA(UsdGeom.Mesh) or UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not True:errors.append('MISSING_OR_DISABLED_MESH:'+path);continue
 if UsdPhysics.MeshCollisionAPI(p).GetApproximationAttr().Get()!='none':errors.append('NON_EXACT_APPROXIMATION:'+path)
 me=UsdGeom.Mesh(p);v=np.asarray(me.GetPointsAttr().Get(),dtype=np.float32);M=np.asarray(cache.GetLocalToWorldTransform(p));v=(np.c_[v,np.ones(len(v))]@M)[:,:3].astype(np.float32);idx=np.asarray(me.GetFaceVertexIndicesAttr().Get(),dtype=np.int32);counts=np.asarray(me.GetFaceVertexCountsAttr().Get(),dtype=np.int32)
 if not np.all(counts==3):errors.append('NON_EXPLICIT_TRIANGULATION:'+path)
 if not len(v) or not np.isfinite(v).all() or len(idx)!=sum(counts) or idx.min()<0 or idx.max()>=len(v):errors.append('INVALID_GEOMETRY:'+path);continue
 sha=hashlib.sha256(v.tobytes()+idx.tobytes()).hexdigest();old=rows.get(path)
 if not old or sha!=old['sha256_geometry'] or len(v)!=old['vertices'] or int(sum(counts-2))!=old['triangles']:errors.append('GEOMETRY_MANIFEST_MISMATCH:'+path)
 checked.append({'path':path,'geometry_sha256':sha,'vertices':len(v),'triangles':int(sum(counts-2))})
if set(actual)!=set(rows) or set(actual)!=expected:errors.append({'collider_set_difference':{'missing_expected':sorted(expected-set(actual)),'unexpected':sorted(set(actual)-expected),'inventory_missing':sorted(set(actual)-set(rows))}})
names={p.GetName() for p in actual.values()};mandatory=[];replacement_file=d/'ARCHITECTURE_REPLACEMENTS.json';replacement=json.loads(replacement_file.read_text()) if replacement_file.exists() else {};replaced=set(replacement.get('raw_source_names_replaced',[]));actual_replacements=replacement.get('actual_enabled_replacement_meshes',[])
if replaced and (not actual_replacements or any(n not in names for n in actual_replacements)):errors.append('DECLARED_RAIL_REPLACEMENT_GEOMETRY_MISSING')
if replacement and replacement.get('source_model_canonical_json_sha256')!=hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest():errors.append('RAIL_REPLACEMENT_SOURCE_BINDING_MISMATCH')
for row in raw['parts']:
 if row['role'] not in ['floor','wall','stair_wall','tread','stair','stair_beam','newel','roof']:continue
 if raw['spec']['style_family']=='glasshouse' and row['role']=='roof':continue
 name=row['name'].replace('-','N')
 if name in replaced:continue
 mandatory.append(name)
 if name not in names:errors.append('MISSING_AUTHORED_STRUCTURAL_COLLIDER:'+name)
visual=d/'scene/museum.usdc';layers,assets,unresolved=UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(visual)))
unresolved=list(map(str,unresolved));external=[]
for layer in layers:
 if layer.realPath and not Path(layer.realPath).resolve().is_relative_to(d.resolve()):external.append(layer.realPath)
for asset in assets:
 p=Path(str(asset))
 if p.is_absolute() and not p.resolve().is_relative_to(d.resolve()):external.append(str(p))
if unresolved or external:errors.append({'portable_dependency_failure':{'unresolved':unresolved,'external':external}})
asset_binding=json.loads((d/'SCENE_ASSET_BINDING.json').read_text())
for name,digest in asset_binding['files_sha256'].items():
 if not (d/name).is_file() or hashlib.sha256((d/name).read_bytes()).hexdigest()!=digest:errors.append('SCENE_ASSET_CHANGED:'+name)
for p in (d/'scene/textures').iterdir():
 if p.suffix.lower() in ['.png','.jpg','.jpeg']:
  with Image.open(p) as image:
   if max(image.size)>2048:errors.append('TEXTURE_EXCEEDS_SPEC_2048:'+p.name)
blend=Path(receipt['source_blend'])
if not blend.exists() or hashlib.sha256(blend.read_bytes()).hexdigest()!=receipt['source_sha256']:errors.append('SOURCE_BLEND_CHANGED_OR_MISSING')
result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'PASS' if not errors else 'FAIL','source_collision_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_visual_sha256':hashlib.sha256(visual.read_bytes()).hexdigest(),'raw_source_sha256':hashlib.sha256((d/'RAW_MODEL.json').read_bytes()).hexdigest(),'validator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'enabled_colliders':len(actual),'mandatory_structural_parts':len(mandatory),'source_manifest_match':not errors,'coordinate_frame':'metres, Z-up, scale1','approximation':'none for every collider','dependency_layers':len(layers),'dependency_assets':len(assets),'unresolved_dependencies':unresolved,'external_dependencies':external,'errors':errors,'geometry':checked};out=d/'C1.json';assert not out.exists();out.write_text(json.dumps(result,indent=2));print('C1',result['status'],len(actual),'colliders',len(mandatory),'structural','errors',json.dumps(errors)[:2000]);sys.exit(0 if not errors else 2)
