"""OWN qualified shading references; restore original targets without geometry."""
from __future__ import annotations
import hashlib
from pathlib import Path
from pxr import Sdf,Usd,UsdGeom,UsdShade

PURPOSES=(UsdShade.Tokens.allPurpose,UsdShade.Tokens.preview,UsdShade.Tokens.full)

def all_prims(stage):
    return list(Usd.PrimRange.Stage(stage,Usd.TraverseInstanceProxies()))

def shading(prim):
    return prim.IsA(UsdShade.Material) or prim.IsA(UsdShade.Shader) or prim.IsA(UsdShade.NodeGraph)

def material_relationship(rel):
    return rel.GetName().startswith(('material:binding','collection:'))

def canonical(value,mapping=None):
    if isinstance(value,Sdf.AssetPath):
        return ('asset',value.path,value.resolvedPath)
    if isinstance(value,Sdf.Path):return ('path',mapping.path(value) if mapping else str(value))
    if isinstance(value,dict):return tuple(sorted((k,canonical(v,mapping)) for k,v in value.items()))
    if value is None or isinstance(value,(str,int,float,bool)):return value
    if hasattr(value,'__iter__'):return (type(value).__name__,tuple(canonical(v,mapping) for v in value))
    return (type(value).__name__,str(value))

def asset_values(value):
    if isinstance(value,Sdf.AssetPath):return [value]
    if isinstance(value,dict):return [a for v in value.values() for a in asset_values(v)]
    if value is None or isinstance(value,(str,int,float,bool,Sdf.Path)):return []
    if hasattr(value,'__iter__'):return [a for v in value for a in asset_values(v)]
    return []

def file_ref(path):
    path=Path(path).resolve();digest=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(4*1024*1024),b''):digest.update(data)
    return {'path':str(path),'bytes':path.stat().st_size,'sha256':digest.hexdigest()}

def source_asset_refs(source):
    records={}
    for prim in all_prims(source):
        if not prim.IsActive() or not shading(prim):continue
        for attr in prim.GetAttributes():
            values=[attr.Get()]+[attr.Get(Usd.TimeCode(t)) for t in attr.GetTimeSamples()]
            for value in values:
                for asset in asset_values(value):
                    container=asset.resolvedPath.split('[',1)[0] if asset.resolvedPath else ''
                    if container and Path(container).is_file():
                        key=str(Path(container).resolve())
                        if key not in records:records[key]=file_ref(container)
    return [records[key] for key in sorted(records)]

def attr_signature(attr,mapping=None):
    samples=attr.GetTimeSamples()
    return {'type':str(attr.GetTypeName()),'custom':attr.IsCustom(),'variability':str(attr.GetVariability()),
        'default':canonical(attr.Get(),mapping),
        'time_samples':[(t,canonical(attr.Get(Usd.TimeCode(t)),mapping)) for t in samples],
        'connections':[mapping.path(p) if mapping else str(p) for p in attr.GetConnections()],
        'metadata':{key:canonical(attr.GetMetadata(key),mapping)
                    for key in ('colorSpace','sdrMetadata','renderType') if attr.HasMetadata(key)}}

def check_material_context(source,frame,mapping):
    """Actual computed bindings and complete shader values, not receipt flags."""
    shader_count=0;attribute_count=0;relationship_count=0;bindings=[];assets=[];asset_refs={}
    for prim in all_prims(source):
        if not prim.IsActive():continue
        path=mapping.path(prim.GetPath());target=frame.GetPrimAtPath(path)
        if shading(prim):
            if not target or target.GetTypeName()!=prim.GetTypeName():
                raise ValueError('Source shader/material missing in frame: '+str(prim.GetPath()))
            shader_count+=1
            source_names={a.GetName() for a in prim.GetAttributes()}
            if source_names!={a.GetName() for a in target.GetAttributes()}:
                raise ValueError('Source shader attribute inventory changed: '+str(prim.GetPath()))
            for attr in prim.GetAttributes():
                mapped=target.GetAttribute(attr.GetName())
                if attr_signature(attr,mapping)!=attr_signature(mapped):
                    raise ValueError('Source shader value/connection/texture changed: '+str(attr.GetPath()))
                attribute_count+=1
                for asset in asset_values(attr.Get()):
                    record={'source_attribute':str(attr.GetPath()),'frame_attribute':str(mapped.GetPath()),
                        'authored_asset_path':asset.path,'actual_resolved_asset_path':asset.resolvedPath}
                    container=asset.resolvedPath.split('[',1)[0] if asset.resolvedPath else ''
                    if container and Path(container).is_file():
                        key=str(Path(container).resolve())
                        if key not in asset_refs:asset_refs[key]=file_ref(container)
                        actual=asset_refs[key]
                        record.update(status='ACTUAL_RESOLVED_DEPENDENCY_HASH_BOUND',dependency_ref=actual)
                    else:record['status']='SOURCE_AND_FRAME_IDENTICALLY_UNRESOLVED_LOCAL_RUNTIME_PENDING'
                    assets.append(record)
        if not target:continue
        for rel in prim.GetRelationships():
            if shading(prim) or material_relationship(rel):
                actual=target.GetRelationship(rel.GetName())
                if not actual or [mapping.path(p) for p in rel.GetTargets()]!=[str(p) for p in actual.GetTargets()]:
                    raise ValueError('Source material/collection relationship changed: '+str(rel.GetPath()))
                if rel.GetMetadata('bindMaterialAs')!=actual.GetMetadata('bindMaterialAs'):
                    raise ValueError('Material binding strength changed: '+str(rel.GetPath()))
                relationship_count+=1
        if prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdGeom.Subset) or prim.HasAPI(UsdShade.MaterialBindingAPI):
            for purpose in PURPOSES:
                left=UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial(purpose)[0]
                right=UsdShade.MaterialBindingAPI(target).ComputeBoundMaterial(purpose)[0]
                expected=mapping.path(left.GetPath()) if left else None
                actual=str(right.GetPath()) if right else None
                if expected!=actual:
                    raise ValueError('Actual computed source/frame bound material differs: '+str(prim.GetPath())+' purpose='+str(purpose))
                bindings.append({'source_prim':str(prim.GetPath()),'frame_prim':str(target.GetPath()),
                    'material_purpose':str(purpose),'source_bound_material':str(left.GetPath()) if left else None,
                    'actual_frame_bound_material':actual})
    return {'actual_shading_prim_count':shader_count,'actual_shader_attribute_count':attribute_count,
        'actual_shading_relationship_count':relationship_count,'actual_computed_material_binding_checks':len(bindings),
        'all_source_computed_bindings_match':True,'all_source_shader_values_and_connections_match':True,
        'binding_readback':bindings,'actual_shader_asset_dependencies':assets,
        'actual_resolved_dependency_refs':list(asset_refs.values()),
        'scope':'Source-bound shading preservation only; unresolved original assets remain explicitly unresolved, no renderer claim.'}

def restore_material_context(source,frame,mapping,source_path):
    before={str(p.GetPath()) for p in all_prims(frame) if p.IsActive() and p.IsA(UsdGeom.Gprim)}
    dependencies_before=source_asset_refs(source)
    source_prims=[p for p in all_prims(source) if p.IsActive()]
    refs=[];relationships=0;connections=0
    # Reference only shading prims at their fully qualified source paths. This
    # retains authored texture anchors and introduces no copied geometry.
    for prim in source_prims:
        if not shading(prim):continue
        target_path=mapping.path(prim.GetPath())
        target=frame.DefinePrim(target_path,prim.GetTypeName())
        target.GetReferences().AddReference(Path(source_path).resolve().as_posix(),str(prim.GetPath()))
        refs.append({'source_prim':str(prim.GetPath()),'actual_frame_prim':target_path})
    for prim in source_prims:
        target=frame.GetPrimAtPath(mapping.path(prim.GetPath()))
        schemas=prim.GetAppliedSchemas()
        material_related=any(s=='MaterialBindingAPI' or s.startswith('CollectionAPI:') for s in schemas)
        if not target and material_related:
            # Define a collection dependency's schema/properties, never copy a
            # geometry subtree. The active-geometry invariant below still vetoes.
            target=frame.DefinePrim(mapping.path(prim.GetPath()),prim.GetTypeName())
        if not target:continue
        if 'MaterialBindingAPI' in schemas and not target.HasAPI(UsdShade.MaterialBindingAPI):
            UsdShade.MaterialBindingAPI.Apply(target)
        for schema in schemas:
            if schema.startswith('CollectionAPI:'):
                instance=schema.split(':',1)[1]
                if not target.HasAPI(Usd.CollectionAPI,instance):Usd.CollectionAPI.Apply(target,instance)
        for attr in prim.GetAttributes():
            if not attr.GetName().startswith('collection:'):continue
            mapped=target.CreateAttribute(attr.GetName(),attr.GetTypeName(),custom=attr.IsCustom(),variability=attr.GetVariability())
            if attr.Get() is not None:mapped.Set(attr.Get())
            for time in attr.GetTimeSamples():mapped.Set(attr.Get(Usd.TimeCode(time)),Usd.TimeCode(time))
        for rel in prim.GetRelationships():
            if not shading(prim) and not material_relationship(rel):continue
            mapped=target.CreateRelationship(rel.GetName(),custom=rel.IsCustom())
            mapped.SetTargets([Sdf.Path(mapping.path(p)) for p in rel.GetTargets()])
            strength=rel.GetMetadata('bindMaterialAs')
            if strength is not None:mapped.SetMetadata('bindMaterialAs',strength)
            relationships+=1
        if shading(prim):
            for attr in prim.GetAttributes():
                # Only connection opinions are repaired. Default/time-sampled
                # values, color, texture assets and their source layers stay refs.
                target.GetAttribute(attr.GetName()).SetConnections([Sdf.Path(mapping.path(p)) for p in attr.GetConnections()])
                connections+=1
    after={str(p.GetPath()) for p in all_prims(frame) if p.IsActive() and p.IsA(UsdGeom.Gprim)}
    if before!=after:raise ValueError('Material repair introduced or removed active geometry')
    frame.GetRootLayer().Save()
    proof=check_material_context(source,frame,mapping)
    dependencies_after=source_asset_refs(source)
    if dependencies_before!=dependencies_after:raise ValueError('Source shader/texture dependency changed during material repair')
    proof['actual_resolved_dependency_refs']=dependencies_after
    proof.update(qualified_original_shading_references=refs,restored_relationship_count=relationships,
        restored_attribute_connection_count=connections,no_active_geometry_introduced_or_removed=True,
        actual_shader_texture_dependencies_before=dependencies_before,
        actual_shader_texture_dependencies_after=dependencies_after,
        shader_texture_dependencies_unchanged=True,
        original_materials_or_texture_values_reauthored=False)
    return proof
