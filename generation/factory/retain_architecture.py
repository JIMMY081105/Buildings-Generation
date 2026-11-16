"""Retain source-bound architecture only; no old stair, upper floor or equipment inheritance."""
from pathlib import Path
import json,hashlib,argparse,datetime
import numpy as np
from pxr import Usd,UsdGeom,UsdShade,UsdUtils,UsdPhysics,Sdf,Gf
P=Path('/cephfs/shared/yixing/outputs/factory80_20261011/pipeline');R=P.parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();ref=lambda p:dict(path=str(p),sha256=sha(p));read=lambda p:json.loads(Path(p).read_text())
def write(path,value):
 with path.open('x')as stream:json.dump(value,stream,indent=2)
def canonical(value):
 if value is None:return None
 if isinstance(value,Sdf.AssetPath):
  resolved=value.resolvedPath
  return dict(asset_resolved=resolved,sha256=sha(resolved)if resolved and Path(resolved).is_file()else None,unresolved_literal=value.path if not resolved else None)
 if isinstance(value,(str,bool,int,float)):return value
 array=np.asarray(value)
 if array.dtype.kind not in ['O']:return dict(dtype=str(array.dtype),shape=list(array.shape),sha256=hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest())
 return str(value)
def fingerprints(stage,allowed):
 result={};cache=UsdGeom.XformCache()
 for prim in stage.Traverse():
  path=str(prim.GetPath());assert any(path==x or path.startswith(x+'/')or x.startswith(path+'/')for x in allowed),'Unexpected populated scope: '+path
  attrs={attr.GetName():dict(value=canonical(attr.Get()),interpolation=attr.GetMetadata('interpolation'),connections=[str(x)for x in attr.GetConnections()])for attr in prim.GetAttributes()}
  result[path]=dict(type=prim.GetTypeName(),schemas=list(prim.GetAppliedSchemas()),attributes=attrs,relationships={rel.GetName():[str(x)for x in rel.GetTargets()]for rel in prim.GetRelationships()},world_transform=canonical(cache.GetLocalToWorldTransform(prim))if prim.IsA(UsdGeom.Xformable)else None)
 return result
def validate(source,candidate,expected,allowed,coverage):
 assert UsdGeom.GetStageMetersPerUnit(candidate)==1.0 and UsdGeom.GetStageUpAxis(candidate)=='Z'
 for path, geometry in coverage.items():
  prim=candidate.GetPrimAtPath(path);assert prim and prim.IsDefined() and prim.IsActive(), 'Structural object coverage mismatch: '+path
  actual_geometry=[str(x.GetPath()) for x in Usd.PrimRange(prim) if x.IsA(UsdGeom.Gprim)]
  assert actual_geometry==geometry,'Structural Gprim coverage mismatch: '+path
 actual=fingerprints(candidate,allowed);assert actual==expected,'Retained source attribute/geometry/physics/world-transform fingerprint mismatch'
 assert not any(str(p.GetPath()).startswith(('/World/Stair','/World/FactoryEquipment','/World/Cut/Floor2Slab','/World/FactoryLighting'))for p in candidate.Traverse()),'Old non-architecture scope present'
 return actual
def clone(stage):
 layer=Sdf.Layer.CreateAnonymous();layer.TransferContent(stage.GetRootLayer());return Usd.Stage.Open(layer)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--building',required=True);a=ap.parse_args();config_ref=ref(P/'config.yaml');cfg=read(P/'config.yaml');policy=cfg['sample_architecture_v3'];assert a.building in policy['scope_buildings']and cfg['approval']['batch_release']is False
 known=read(P/'evidence/G13_MEASURED_SEAL_FULL_QUALIFICATION_V1/STOP_RESOLUTION.json')['old_stop_files_preserved']
 for folder in [R,P]:
  for stop in folder.glob('CRITICAL_STOP*.json'):assert str(stop)in known and sha(stop)==known[str(stop)],'Unresolved global stop evidence'
 inspection_path=R/'runs'/a.building/'v3_sample/source_inspection/STAGE_INSPECTION.json';inspection=read(inspection_path);source=Path(inspection['source']);assert sha(source)==inspection['source_sha256']
 allowed=list(policy['retained_scene_prefixes'])
 for item in inspection['structure']:
  if item['type'] not in policy['additional_retained_structure_types_from_actual_inspection']:continue
  path=item['path'];assert path.startswith('/World/') and not path.startswith(('/World/Stair','/World/FactoryEquipment','/World/Cut/Floor2Slab'))
  if not any(path==x or path.startswith(x+'/') for x in allowed):allowed.append(path)
 mask=Usd.StagePopulationMask()
 for prefix in allowed:mask.Add(prefix)
 source_stage=Usd.Stage.OpenMasked(str(source),mask);assert source_stage
 materials=set()
 for closure_iteration in range(32):
  targets=set()
  for prim in source_stage.Traverse():
   for rel in prim.GetRelationships():
    if rel.GetName().startswith('material:binding'):
     targets.update(str(target.GetPrimPath())for target in rel.GetTargets()if target.IsPrimPath())
   for attr in prim.GetAttributes():targets.update(str(target.GetPrimPath())for target in attr.GetConnections())
  extra={x for x in targets if not any(x==prefix or x.startswith(prefix+'/')for prefix in allowed)}
  if not extra:break
  for path in sorted(extra):
   assert path not in ['/','/World']and not path.startswith(('/World/Stair','/World/FactoryEquipment','/World/Cut/Floor2Slab')),'Material mask would broaden into forbidden source scope'
   mask.Add(path);allowed.append(path);materials.add(path)
  source_stage=Usd.Stage.OpenMasked(str(source),mask)
  for path in materials:
   prim=source_stage.GetPrimAtPath(path);assert prim and any(prim.IsA(kind)for kind in [UsdShade.Material,UsdShade.Shader,UsdShade.NodeGraph]),'Referenced external material node not resolved: '+path
 else:raise RuntimeError('Material/shader connection closure did not converge')
 source_layer_files={}
 for used in source_stage.GetUsedLayers():
  if used.anonymous:continue
  outer=Path(used.realPath.split('[',1)[0]);assert outer.is_file(),'Unbound source layer: '+used.identifier
  source_layer_files[str(outer)]=sha(outer)
 coverage={}
 for item in inspection['structure']:
  prim=source_stage.GetPrimAtPath(item['path']);assert prim and prim.IsDefined() and prim.IsActive(),'Actual source structure missing: '+item['path']
  geometry=[str(x.GetPath()) for x in Usd.PrimRange(prim) if x.IsA(UsdGeom.Gprim)];assert geometry
  coverage[item['path']]=geometry
 expected=fingerprints(source_stage,allowed);assert expected
 output=R/'runs'/a.building/'v3_sample/retained_architecture';output.mkdir(parents=True,exist_ok=True)
 stage_path=output/policy['layer_filename'];assert not stage_path.exists() and not(output/policy['receipt_filename']).exists();layer=source_stage.Flatten();assert layer.Export(str(stage_path));candidate=Usd.Stage.Open(str(stage_path));validate(source_stage,candidate,expected,allowed,coverage)
 controls=[]
 for kind,fragment in [('actual_ground_face_removed','fingerprint mismatch'),('actual_structure_vertex_shifted','fingerprint mismatch'),('actual_foreign_geometry_inserted','Unexpected populated scope'),('actual_perimeter_wall_deactivated','Structural object coverage mismatch')]:
  negative=clone(candidate)
  if kind=='actual_ground_face_removed':
   prim=negative.GetPrimAtPath('/World/Cut/Floor1Slab/Visual');mesh=UsdGeom.Mesh(prim);counts=list(mesh.GetFaceVertexCountsAttr().Get());indices=list(mesh.GetFaceVertexIndicesAttr().Get());assert counts
   mesh.GetFaceVertexCountsAttr().Set(counts[:-1]);mesh.GetFaceVertexIndicesAttr().Set(indices[:-counts[-1]])
  elif kind=='actual_structure_vertex_shifted':
   prim=next(p for p in negative.Traverse()if str(p.GetPath()).startswith('/World/FactoryStructure/')and p.IsA(UsdGeom.Mesh));mesh=UsdGeom.Mesh(prim);points=mesh.GetPointsAttr().Get();p=points[0];points[0]=Gf.Vec3f(p[0]+0.01,p[1],p[2]);mesh.GetPointsAttr().Set(points)
  elif kind=='actual_perimeter_wall_deactivated':
   wall=next(x['path'] for x in inspection['structure'] if x['type']=='perimeter_wall');negative.OverridePrim(wall).SetActive(False)
  else:UsdGeom.Cube.Define(negative,'/World/Stair/ScopeNegative').CreateSizeAttr(2.0)
  try:validate(source_stage,negative,expected,allowed,coverage)
  except AssertionError as error:
   assert fragment in str(error),'Negative failed for unrelated reason: '+str(error);controls.append(dict(control=kind,status='FAIL_AS_REQUIRED',actual_rejection=str(error)))
  else:
   write(R/'CRITICAL_STOP_V3_ARCHITECTURE_RETENTION_NEGATIVE.json',dict(status='ENTIRE_TASK_STOP_REQUIRED',control=kind,building=a.building));raise RuntimeError('Actually executed architecture-retention negative escaped')
  write(output/(kind+'_v2.json'),controls[-1])
 _,assets,missing=UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(stage_path)));assert not missing
 assert sha(source)==inspection['source_sha256']and ref(P/'config.yaml')==config_ref
 assert all(sha(path)==digest for path,digest in source_layer_files.items())
 count=sum(p.IsA(UsdGeom.Mesh)for p in candidate.Traverse());colliders=sum(p.HasAPI(UsdPhysics.CollisionAPI)and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get()is not False for p in candidate.Traverse())
 lower=inspection['slabs']['/World/Cut/Floor1Slab/Visual'];upper=inspection['slabs']['/World/Cut/Floor2Slab/Visual'];fraction=upper['actual_top_area_m2']/lower['actual_top_area_m2']
 value=dict(status='ARCHITECTURE_ONLY_DERIVED_LAYER_AND_EXACT_SOURCE_FINGERPRINTS_VERIFIED_NEW_UPPER_FLOOR_STAIRS_EQUIPMENT_REQUIRED',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),building=a.building,canonical_config=config_ref,source_inspection=ref(inspection_path),historical_source=dict(path=str(source),sha256=inspection['source_sha256']),retained_architecture=ref(stage_path),retained_prefixes=allowed,
  populated_prims=len(expected),structural_object_and_gprim_coverage=coverage,actual_retained_meshes=count,actual_retained_enabled_colliders=colliders,all_attributes_geometry_uv_physics_and_world_transforms_identical=True,material_binding_target_prefixes=sorted(materials),actual_source_layer_sha256=source_layer_files,resource_dependencies=[str(x)for x in assets],missing_dependencies=[],
  actual_controls=controls,original_lower_slab_area_m2=lower['actual_top_area_m2'],historical_upper_slab_area_m2=upper['actual_top_area_m2'],historical_mezzanine_fraction=fraction,historical_upper_slab_meets_v3_fraction=cfg['physical_v3']['mezzanine_coverage_fraction'][0]<=fraction<=cfg['physical_v3']['mezzanine_coverage_fraction'][1],
  historical_upper_slab_not_carried=True,old_equipment_stairs_and_lighting_not_carried=True,new_mezzanine_net_area_target_m2=policy['mezzanine_net_area_target_fraction']*lower['actual_top_area_m2'],source_stage_modified=False,historical_scientific_verdicts_inherited=False,new_building_layout_or_gates='NOT_EXECUTED',batch_release=False)
 write(output/policy['receipt_filename'],value);print(json.dumps(dict(status=value['status'],building=a.building,prims=len(expected),meshes=count,old_mezzanine_fraction=fraction,output=ref(output/policy['receipt_filename']))),flush=True)
if __name__=='__main__':main()
