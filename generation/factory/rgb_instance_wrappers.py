"""RGB-only anonymous source wrappers; original shader-locator opinions compose inside prototypes."""
import hashlib,json
from pathlib import Path

def collect_locator_overrides(stage,policy):
    from pxr import Sdf,Usd,UsdShade
    rows=[]
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdShade.Shader):continue
        attr=prim.GetAttribute('info:mdl:sourceAsset');value=attr.Get() if attr else None
        if not isinstance(value,Sdf.AssetPath) or value.resolvedPath:continue
        name=policy['missing_locator_patterns'].get(value.path)
        if name is None:continue
        target=Path(policy['modules'][name]['path']);sub=prim.GetAttribute('info:mdl:sourceAsset:subIdentifier').Get()
        if not target.is_file() or sub!=name:continue
        rows.append(dict(shader=str(prim.GetPath()),original=value.path,resolved=str(target)))
    assert len(rows)==len({r['shader'] for r in rows}),'Duplicate original locator shader'
    return rows

def _source_reference(prim):
    from pxr import Sdf
    op=prim.GetMetadata('references');assert op is not None,('Missing direct asset reference',str(prim.GetPath()))
    refs=list(op.GetAppliedItems());assert len(refs)==1,('Expected original single SourceAsset reference',str(prim.GetPath()),refs)
    ref=refs[0];assert ref.assetPath,'SourceAsset internal reference not supported by frozen scene_compose'
    authors=[spec for spec in prim.GetPrimStack() if spec.path==prim.GetPath() and any(item.assetPath==ref.assetPath and item.primPath==ref.primPath for item in spec.referenceList.GetAppliedItems())]
    assert authors,('Cannot resolve authored asset reference layer',str(prim.GetPath()))
    resolved=Sdf.ComputeAssetPathRelativeToLayer(authors[0].layer,ref.assetPath)
    assert resolved
    original=Sdf.Reference(resolved,ref.primPath,ref.layerOffset,ref.customData)
    identity=dict(assetPath=resolved,primPath=str(ref.primPath),layerOffset=dict(offset=float(ref.layerOffset.offset),scale=float(ref.layerOffset.scale)),customData=ref.customData)
    key=json.dumps(identity,sort_keys=True,default=str)
    return key,identity,original

def _matrix(prim):
    from pxr import UsdGeom
    return tuple(tuple(float(v) for v in row) for row in UsdGeom.XformCache().GetLocalToWorldTransform(prim))

def apply_rgb_anonymous_wrappers(stage,manifest,policy,baseline_overrides=None,expected_unique_assets=None):
    from pxr import Sdf,Usd,UsdShade,UsdLux
    assert stage.GetEditTarget().GetLayer()==stage.GetSessionLayer(),'RGB edits must target session layer'
    original_rows=collect_locator_overrides(stage,policy)
    if baseline_overrides is not None:
        canonical=lambda rows:{r['shader']:(r['original'],r['resolved']) for r in rows}
        assert len(baseline_overrides)==len(original_rows) and canonical(baseline_overrides)==canonical(original_rows),'Original noninstance locator set differs from delivered baseline'
    roots=[row['prim_path']+'/SourceAsset' for row in manifest['instances']];assert len(roots)==len(set(roots))
    by_root={root:{} for root in roots};outside=[]
    for row in original_rows:
        end=row['shader'].find('/SourceAsset/')
        root=row['shader'][:end+len('/SourceAsset')] if end>=0 else None
        if root not in by_root:outside.append(row);continue
        relative=row['shader'][len(root):];by_root[root][relative]=dict(original=row['original'],resolved=row['resolved'])
    groups={};before={}
    for root in roots:
        prim=stage.GetPrimAtPath(root);assert prim and not prim.IsInstanceProxy(),('Uneditable original SourceAsset',root)
        key,identity,reference=_source_reference(prim);lights=[str(p.GetPath()) for p in Usd.PrimRange(prim,Usd.TraverseInstanceProxies()) if p.HasAPI(UsdLux.LightAPI)]
        before[root]=_matrix(prim)
        if key not in groups:groups[key]=dict(identity=identity,reference=reference,expected=by_root[root],roots=[],contains_light=bool(lights))
        group=groups[key];assert group['expected']==by_root[root],('Same asset has different original relative locator sets',root)
        assert group['contains_light']==bool(lights),('Same asset has inconsistent light membership',root)
        group['roots'].append(root)
    if expected_unique_assets is not None:assert len(groups)==expected_unique_assets,('Unique source asset count differs',len(groups),expected_unique_assets)
    layers=[];evidence=[];skipped=[];instance_count=0;prototypes=set()
    for key,group in sorted(groups.items()):
        digest=hashlib.sha256(key.encode()).hexdigest();expected=group['expected']
        if group['contains_light']:
            for root in group['roots']:
                prim=stage.GetPrimAtPath(root)
                if prim.IsInstance():assert prim.SetInstanceable(False)
                assert not prim.IsInstance(),'Keep original disabled-light local opinions, no instancing'
                for relative,row in expected.items():stage.GetPrimAtPath(root+relative).GetAttribute('info:mdl:sourceAsset').Set(Sdf.AssetPath(row['resolved']))
            skipped.append(dict(asset=group['identity'],roots=group['roots'],reason='USDLUX_SUBTREE_KEEP_ORIGINAL_NONINSTANCE_AND_DISABLED_LIGHT_LOGIC'));continue
        layer=Sdf.Layer.CreateAnonymous('Factory80RGBWrapper_'+digest[:16]+'.usda');wrapper=Usd.Stage.Open(layer);wrapper.SetEditTarget(layer)
        wrapper_path='/Factory80RGBSourceAsset';root_prim=wrapper.OverridePrim(wrapper_path);assert root_prim.GetReferences().SetReferences([group['reference']]);wrapper.SetDefaultPrim(root_prim)
        authored={}
        for relative,row in sorted(expected.items()):
            shader=wrapper.GetPrimAtPath(wrapper_path+relative);assert shader and shader.IsA(UsdShade.Shader),('Wrapper shader path missing',relative)
            attr=shader.GetAttribute('info:mdl:sourceAsset');value=attr.Get();assert isinstance(value,Sdf.AssetPath) and value.path==row['original'],('Wrapper original shader path differs',relative,value,row)
            assert attr.Set(Sdf.AssetPath(row['resolved']))
            value=attr.Get();assert value.path==row['resolved'] and value.resolvedPath,('Anonymous wrapper path did not resolve',relative,value)
            authored[relative]=dict(original=row['original'],resolved=value.path)
        assert authored==expected,'Anonymous wrapper locator set mismatch'
        layers.append(layer);asset_prototypes=set()
        for root in group['roots']:
            prim=stage.GetPrimAtPath(root);assert prim.GetReferences().SetReferences([Sdf.Reference(layer.identifier,Sdf.Path(wrapper_path))])
            assert prim.SetInstanceable(True) and prim.IsInstanceable() and prim.IsInstance(),('Wrapper native instance failed',root)
            assert _matrix(prim)==before[root],('SourceAsset world transform changed',root)
            prototype=prim.GetPrototype();asset_prototypes.add(str(prototype.GetPath()));instance_count+=1
        assert len(asset_prototypes)==1,('Same anonymous wrapper did not share one prototype',group['identity'],asset_prototypes)
        prototype=stage.GetPrimAtPath(next(iter(asset_prototypes)));observed={};unresolved=[]
        for prim in Usd.PrimRange(prototype,Usd.TraverseInstanceProxies()):
            if not prim.IsA(UsdShade.Shader):continue
            attr=prim.GetAttribute('info:mdl:sourceAsset');value=attr.Get() if attr else None
            if not isinstance(value,Sdf.AssetPath):continue
            relative=str(prim.GetPath())[len(str(prototype.GetPath())):]
            if value.path.replace('\\','/').startswith('../source_assets/') and not value.resolvedPath:unresolved.append(dict(relative_shader=relative,path=value.path))
            if relative in expected:
                target=expected[relative]['resolved'];assert value.path==target and value.resolvedPath,('Prototype shader locator override ineffective',relative,value,target)
                observed[relative]=dict(original=expected[relative]['original'],resolved=value.path,resolvedPath=value.resolvedPath)
        assert not unresolved,('Unresolved ../source_assets shader paths in prototype',group['identity'],unresolved)
        assert set(observed)==set(expected),'Prototype relative locator coverage differs'
        prototypes.update(asset_prototypes);evidence.append(dict(asset_identity=group['identity'],asset_key_sha256=digest,instance_count=len(group['roots']),anonymous_layer_identifier=layer.identifier,prototype=next(iter(asset_prototypes)),original_relative_overrides=expected,authored_relative_overrides=authored,prototype_shader_readbacks=observed,coverage_exact_equal=True,unresolved_source_assets_shader_paths=unresolved))
    # Non-device shaders retain precisely the original noninstance locator edits.
    for row in outside:assert stage.GetPrimAtPath(row['shader']).GetAttribute('info:mdl:sourceAsset').Set(Sdf.AssetPath(row['resolved']))
    result=dict(status='PASS_PRE_FIRST_RGB_UPDATE',scope='RGB_SESSION_AND_ANONYMOUS_IN_MEMORY_LAYERS_ONLY',before_first_RGB_stage_app_update=True,source_asset_instances=len(roots),unique_source_assets=len(groups),anonymous_wrappers=len(layers),instanced_count=instance_count,prototype_count=len(prototypes),skipped_light_assets=len(skipped),skipped=skipped,original_locator_override_count=len(original_rows),outside_device_override_count=len(outside),assets=evidence,all_relative_override_sets_exact_equal=True,all_prototype_source_assets_shader_paths_resolved=True,all_world_transforms_exact_equal=True,no_disk_asset_or_layer_saved=True)
    return result,layers,original_rows
