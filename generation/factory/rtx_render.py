"""Six unchanged RGB views, then a source-mapped pure-collision gray stage."""
from pathlib import Path
import argparse
import asyncio
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from isaac_native_c5 import selected_gpu,lifecycle_cleanup,digest


def visibility_targets(stage, prefix):
    """Retained wall/slab containers can be untyped USD defs."""
    from pxr import Usd,UsdGeom
    root=stage.GetPrimAtPath(prefix)
    assert root,'VISIBILITY_PREFIX_MISSING:'+prefix
    if root.IsA(UsdGeom.Imageable):return [prefix]
    paths=[]
    for prim in Usd.PrimRange(root):
        path=str(prim.GetPath())
        if prim.IsA(UsdGeom.Imageable) and not any(path.startswith(p+'/') for p in paths):
            paths.append(path)
    assert paths,'VISIBILITY_PREFIX_HAS_NO_IMAGEABLE_DESCENDANT:'+prefix
    return paths


def authored_duplicate_root(stage,source):
    """Independently authored same-path sources require exact mesh evidence."""
    import numpy as np
    from pxr import Usd,UsdGeom,UsdPhysics
    root=source['proxy_root_path'];assert source['composed_proxy_root_path']==root
    path=source['proxy_path'];path=path['path'] if isinstance(path,dict) else path
    proxy=Usd.Stage.OpenMasked(str(Path(path).resolve()),Usd.StagePopulationMask([root]));assert proxy
    def enabled_meshes(scene):
        prim=scene.GetPrimAtPath(root);assert prim
        return {str(p.GetPath())[len(root):]:p for p in Usd.PrimRange(prim,Usd.TraverseInstanceProxies())
                if p.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False}
    actual=enabled_meshes(stage);expected=enabled_meshes(proxy)
    assert actual and set(actual)==set(expected),'AUTHORED_DUPLICATE_COLLIDER_PATH_MISMATCH'
    ax=UsdGeom.XformCache();px=UsdGeom.XformCache()
    ar=ax.GetLocalToWorldTransform(stage.GetPrimAtPath(root)).GetInverse()
    pr=px.GetLocalToWorldTransform(proxy.GetPrimAtPath(root)).GetInverse();evidence=[]
    for suffix,a in actual.items():
        b=expected[suffix];assert a.IsA(UsdGeom.Mesh) and b.IsA(UsdGeom.Mesh),'AUTHORED_DUPLICATE_NOT_MESH'
        hashes={}
        for name in ('points','faceVertexCounts','faceVertexIndices','holeIndices','subdivisionScheme','orientation'):
            av=a.GetAttribute(name).Get();bv=b.GetAttribute(name).Get()
            aa=np.asarray(av);bb=np.asarray(bv)
            assert np.array_equal(aa,bb),'AUTHORED_DUPLICATE_GEOMETRY_MISMATCH:'+suffix+':'+name
            hashes[name]=hashlib.sha256(aa.tobytes() if aa.dtype.kind!='O' else str(av).encode()).hexdigest()
        assert np.array_equal(np.asarray(ax.GetLocalToWorldTransform(a)*ar),np.asarray(px.GetLocalToWorldTransform(b)*pr)),'AUTHORED_DUPLICATE_TRANSFORM_MISMATCH'
        physical=lambda p:{v.GetName():str(v.Get()) for v in p.GetAttributes() if v.GetName().startswith(('physics:','physx'))}
        assert physical(a)==physical(b),'AUTHORED_DUPLICATE_PHYSICS_ATTRIBUTE_MISMATCH'
        evidence.append(dict(relative_path=suffix,attribute_sha256=hashes,physics_attributes=physical(a),relative_transform_equal=True))
    return dict(proof='Exact independently authored same-path enabled Mesh topology/points/relative transform/physics attributes',
                enabled_colliders=len(actual),meshes=evidence)


def resolved_proxy_roots(stage,manifest):
    """Actual PrimStack arcs, including collision references nested in visuals."""
    from pxr import Usd
    rows=[]
    for index,source in enumerate(manifest['collision_sources']):
        proxy=source['proxy_path'];proxy=proxy['path'] if isinstance(proxy,dict) else proxy
        target=str(Path(proxy).resolve());root=source['proxy_root_path']
        composed=source['composed_proxy_root_path'];prim=stage.GetPrimAtPath(composed)
        assert prim,'COMPOSED_SOURCE_ROOT_MISSING:'+composed
        def carries_source(candidate):
            return any(spec.layer.realPath and str(Path(spec.layer.realPath).resolve())==target and str(spec.path)==root
                       for spec in candidate.GetPrimStack())
        matches=[str(prim.GetPath())] if carries_source(prim) else [str(p.GetPath()) for p in Usd.PrimRange(prim,Usd.TraverseInstanceProxies()) if carries_source(p)]
        duplicate=None
        if not matches and composed==root:
            duplicate=authored_duplicate_root(stage,source);matches=[composed]
        assert len(matches)==1,'ACTUAL_PROXY_REFERENCE_ROOT_ABSENT_OR_AMBIGUOUS:'+composed
        rows.append(dict(source_index=index,manifest_composed_root=composed,original_root=matches[0],
                         source_layer=target,source_prim=root,proof='Independently authored exact duplicate' if duplicate else 'Actual composed PrimStack layer and root prim match',
                         authored_duplicate_evidence=duplicate))
    return rows


def _capture_rgb(app, args, log):
    """Capture the frozen geometry-derived six RGB views and one collider gray view."""
    started=time.monotonic();args.out.mkdir(parents=True,exist_ok=False)
    plan=json.loads(args.cameras.read_text())
    ids=['exterior_01','exterior_02_cutaway','ground_01','ground_02','mezzanine_01','stair_01']
    assert plan['schema']=='Factory80AutomaticCameraPlanV1'
    assert plan.get('camera_rules_sha256'), 'FROZEN_CAMERA_RULES_BINDING_MISSING'
    if getattr(args,'camera_rules',None):
        assert hashlib.sha256(Path(args.camera_rules).read_bytes()).hexdigest()==plan['camera_rules_sha256']
    assert getattr(args,'scale_xy',1.)==1., 'AUTOMATIC_CAMERAS_ALREADY_USE_ACTUAL_WORLD_UNITS'
    assert [r['id'] for r in plan['views']]==ids
    exposure_path=Path('/cephfs/shared/yixing/outputs/factory80_20261011/sample/capture/FROZEN_EXPOSURE_PROFILE.json')
    exposure=json.loads(exposure_path.read_text())
    assert hashlib.sha256(exposure_path.read_bytes()).hexdigest()=='fc0dc60ae041f10ced9ed6b1b80577c47df286eaaf51a03a60e447baa5eeeead'
    before=subprocess.check_output(['nvidia-smi'],text=True)
    import carb
    import omni.usd
    import omni.replicator.core as rep
    import numpy as np
    from PIL import Image
    from omni.kit.viewport.utility import get_active_viewport
    from pxr import Gf,Sdf,Usd,UsdGeom,UsdPhysics,UsdShade
    selected=selected_gpu(log)
    context=omni.usd.get_context();assert context.open_stage(str(args.stage.resolve()))
    # Author session instancing before ANY app.update for this RGB stage.
    stage=context.get_stage();assert stage
    stage.SetEditTarget(stage.GetSessionLayer())
    locator_policy=Path('/cephfs/shared/yixing/outputs/factory80_20261011/pipeline/evidence/V3_NATIVE_MDL_DEPENDENCY_LOCATOR_POLICY_V1/POLICY.json')
    locator_data=json.loads(locator_policy.read_text())
    root_SHA_before=digest(args.stage)
    # Preserve the existing RGB guide/proxy/collision hiding operation.
    for p in stage.Traverse():
        if p.IsA(UsdGeom.Mesh) and (str(UsdGeom.Imageable(p).ComputePurpose()) in ('guide','proxy') or 'collision' in str(p.GetPath()).lower()):
            UsdGeom.Imageable(p).MakeInvisible()
    manifest_path=Path(args.stage).parent/'COLLISION_MANIFEST.json'
    baseline_path=getattr(args,'baseline_photos',None)
    baseline_overrides=json.loads(Path(baseline_path).read_text())['shader_runtime_locator_overrides'] if baseline_path else None
    from rgb_instance_wrappers import apply_rgb_anonymous_wrappers
    rgb_instancing,rgb_wrapper_layers,locator_overrides=apply_rgb_anonymous_wrappers(
        stage,json.loads(manifest_path.read_text()),locator_data,
        baseline_overrides=baseline_overrides,expected_unique_assets=getattr(args,'expected_unique_assets',None))
    (args.out/'RGB_ANONYMOUS_WRAPPER_PREFLIGHT.json').write_text(json.dumps(rgb_instancing,indent=2))
    assert all(layer.identifier.startswith('anon:') for layer in rgb_wrapper_layers)
    # All prototype readbacks/relative coverage checks above precede the first update.
    while context.get_stage_loading_status()[2]:app.update()
    assert digest(args.stage)==root_SHA_before,'RGB_CHANGED_DELIVERABLE_STAGE_BYTES'
    settings=carb.settings.get_settings()
    for k,v in exposure['settings_readback'].items():settings.set(k,v)
    settings.set_bool('/app/viewport/grid/enabled',False)
    settings.set_bool('/app/viewport/outline/enabled',False)
    views=[];xc=UsdGeom.XformCache()
    for row in plan['views']:
        eye=[float(v) for v in row['eye']]
        target=[float(v) for v in row['target']]
        path='/Factory80V4PhotoCameras/'+row['id']
        cam=UsdGeom.Camera.Define(stage,path);cam.CreateProjectionAttr('perspective')
        cam.CreateFocalLengthAttr(float(plan['intrinsics']['focal_length_mm']))
        cam.CreateHorizontalApertureAttr(float(plan['intrinsics']['horizontal_aperture_mm']))
        cam.CreateClippingRangeAttr(Gf.Vec2f(*plan['intrinsics']['clipping_range_m']))
        cam.CreateVerticalApertureAttr(cam.GetHorizontalApertureAttr().Get()*720/1280)
        cam.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye),Gf.Vec3d(*target),Gf.Vec3d(0,0,1)).GetInverse())
        views.append(dict(id=row['id'],camera_path=path,cutaway=row['cutaway'],
                          role=row['role'],planned_eye=eye,planned_target=target,
                          hide_prefixes=row['hide_prefixes'],keep_prefixes=row['keep_prefixes'],
                          actual_world_matrix=np.asarray(xc.GetLocalToWorldTransform(cam.GetPrim())).tolist(),
                          actual_focal_length_mm=float(cam.GetFocalLengthAttr().Get()),
                          actual_horizontal_aperture_mm=float(cam.GetHorizontalApertureAttr().Get()),
                          actual_vertical_aperture_mm=float(cam.GetVerticalApertureAttr().Get()),
                          actual_clipping_range_m=list(cam.GetClippingRangeAttr().Get())))
    # Snapshot complete authored visibility for every prefix any view may edit.
    # Restore that baseline before EVERY frame, including collider gray.
    prefix_set=sorted({p for row in plan['views'] for p in row['hide_prefixes']+row['keep_prefixes']}
                      |set(plan['collision_gray']['hide_prefixes']))
    resolved_visibility={prefix:visibility_targets(stage,prefix) for prefix in prefix_set}
    visibility_baseline={}
    for path in sorted({p for targets in resolved_visibility.values() for p in targets}):
        prim=stage.GetPrimAtPath(path)
        assert prim and prim.IsA(UsdGeom.Imageable),'VISIBILITY_PREFIX_MISSING:'+path
        visibility_baseline[path]=str(UsdGeom.Imageable(prim).GetVisibilityAttr().Get() or 'inherited')
    frame_visibility=[]
    viewport=get_active_viewport();viewport.resolution=(1280,720)
    rgb=rep.AnnotatorRegistry.get_annotator('rgb',device='cpu',do_array_copy=True)
    rgb.attach(viewport.render_product_path)
    def frame(row,name):
        frame_started=time.monotonic()
        print('FACTORY80_PHOTO_STARTED '+name,flush=True)
        for path,value in visibility_baseline.items():
            UsdGeom.Imageable(stage.GetPrimAtPath(path)).CreateVisibilityAttr().Set(value)
        hidden=[];kept=[]
        for path in row['hide_prefixes']:
            assert not any(k==path or k.startswith(path+'/') for k in row['keep_prefixes']), 'PROTECTED_STAIR_WOULD_BE_HIDDEN'
            for actual_path in resolved_visibility[path]:
                prim=stage.GetPrimAtPath(actual_path)
                UsdGeom.Imageable(prim).MakeInvisible();hidden.append(actual_path)
        for path in row['keep_prefixes']:
            for actual_path in resolved_visibility[path]:
                prim=stage.GetPrimAtPath(actual_path);assert prim
                assert UsdGeom.Imageable(prim).ComputeVisibility()!='invisible','PROTECTED_STAIR_NOT_VISIBLE:'+actual_path
                kept.append(actual_path)
        frame_visibility.append(dict(file=name,role=row['role'],actual_hidden_prim_paths=hidden,
                                     actual_protected_visible_prim_paths=kept,
                                     baseline_restored_prim_paths=sorted(visibility_baseline)))
        viewport.camera_path=row['camera_path']
        for _ in range(12):app.update()
        for k,v in exposure['settings_readback'].items():settings.set(k,v)
        future=asyncio.ensure_future(rep.orchestrator.step_async(rt_subframes=4,delta_time=0.,pause_timeline=True,wait_for_render=True))
        while not future.done():app.update()
        future.result()
        value=rgb.get_data(device='cpu',do_array_copy=True,use_legacy_structure=True)
        arr=np.asarray(value.get('data') if isinstance(value,dict) else value)
        assert arr.ndim==3 and arr.shape[:2]==(720,1280) and arr.shape[2] in (3,4),'RENDER_BUFFER_UNAVAILABLE'
        Image.fromarray(arr[:,:,:3]).save(args.out/name)
        print('FACTORY80_PHOTO_SAVED '+json.dumps(dict(file=name,elapsed_s=time.monotonic()-frame_started)),flush=True)
    try:
        enabled_paths=sorted(str(p.GetPath()) for p in stage.Traverse(Usd.TraverseInstanceProxies())
                             if p.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False)
        light=stage.GetPrimAtPath('/World/SampleLighting');assert light,'RGB_LIGHT_ROOT_MISSING'
        light_world=np.asarray(xc.GetLocalToWorldTransform(light)).tolist()
        manifest_path=Path(args.stage).parent/'COLLISION_MANIFEST.json'
        source_roots=resolved_proxy_roots(stage,json.loads(manifest_path.read_text()))
        for row in views:frame(row,row['id']+'.png')
    finally:
        rgb.detach()
    assert len(rgb_wrapper_layers)==rgb_instancing['anonymous_wrappers'],'Anonymous wrapper lifetime lost during capture'
    assert digest(args.stage)==root_SHA_before,'RGB_CHANGED_DELIVERABLE_STAGE_BYTES'
    return dict(plan=plan,exposure=exposure,actual_exposure_settings_readback={k:settings.get(k) for k in exposure['settings_readback']},views=views,frame_visibility=frame_visibility,rgb_session_instancing=rgb_instancing,
                locator_overrides=locator_overrides,selected_GPU=selected[-1],GPU_before=before,
                enabled_collider_paths=enabled_paths,light_root_world_matrix=light_world,
                resolved_source_roots=source_roots,collision_manifest_sha256=digest(manifest_path),
                RGB_elapsed_s=time.monotonic()-started)


def _gray_inputs(args,resolved_source_roots,manifest_sha256):
    """Resolve existing package/light inputs from authored references only."""
    from pxr import Sdf
    root=Path(args.stage).resolve();directory=root.parent
    manifest_path=directory/'COLLISION_MANIFEST.json'
    package=directory/'collision_package/collision_only.usdc'
    package_meta=package.with_name(package.stem+'_PACKAGE.json')
    manifest=json.loads(manifest_path.read_text());meta=json.loads(package_meta.read_text())
    assert digest(manifest_path)==manifest_sha256,'RGB_TO_GRAY_MANIFEST_CHANGED'
    assert Path(manifest['root']).resolve()==root,'VISUAL_MANIFEST_ROOT_MISMATCH'
    package_sha=digest(package)
    assert package_sha==meta['root_sha256'],'COLLISION_PACKAGE_BYTES_CHANGED'
    assert meta['actual_enabled_colliders']==manifest['actual_enabled_colliders'],'COLLISION_PACKAGE_COUNT_MISMATCH'
    layer=Sdf.Layer.FindOrOpen(str(root));assert layer
    lighting_spec=layer.GetPrimAtPath('/World/SampleLighting');assert lighting_spec,'AUTHORED_LIGHT_REFERENCE_MISSING'
    references=list(lighting_spec.referenceList.GetAppliedItems());assert len(references)==1,'AMBIGUOUS_LIGHT_REFERENCE'
    reference=references[0]
    light_path=Path(Sdf.ComputeAssetPathRelativeToLayer(layer,reference.assetPath)).resolve()
    light_root=str(reference.primPath);assert light_root,'LIGHT_REFERENCE_ROOT_MISSING'
    light_metadata=light_path.parent/'LIGHTING.json'
    light=json.loads(light_metadata.read_text())
    rows=[r for r in light['retained_components'] if r['prim_path']=='/World/SampleLighting' and r['source_prim']==light_root]
    assert len(rows)==1,'LIGHT_METADATA_BINDING_AMBIGUOUS'
    binding=rows[0]['source_stage'];assert Path(binding['path']).resolve()==light_path
    assert digest(light_path)==binding['sha256'],'LIGHT_LAYER_BYTES_CHANGED'
    c5_path=directory/'c5/C5.json';c5_ref=None
    if c5_path.is_file():
        c5=json.loads(c5_path.read_text());assert c5['status']=='PASS' and c5['input_sha']['stage']==package_sha,'GRAY_NOT_SAME_AS_PASSED_C5_PACKAGE'
        c5_ref=dict(path=str(c5_path),sha256=digest(c5_path))
    assert len(manifest['collision_sources'])==len(meta['collision_sources'])
    mappings=[]
    assert len(resolved_source_roots)==len(manifest['collision_sources'])
    for index,(original,exported,resolved) in enumerate(zip(manifest['collision_sources'],meta['collision_sources'],resolved_source_roots)):
        assert original['instance_root_path']==exported['instance_root_path']
        assert original['proxy_root_path']==exported['source_root']
        assert original['world_transform']==exported['world_transform'],'PACKAGE_SOURCE_WORLD_MATRIX_MISMATCH'
        assert resolved['source_index']==index and resolved['manifest_composed_root']==original['composed_proxy_root_path']
        assert resolved['source_prim']==original['proxy_root_path']
        mappings.append(dict(original_root=resolved['original_root'],package_root=exported['export_root_path'],reference_arc=resolved,
                             enabled_colliders=exported['enabled_colliders'],proxy_sha256=exported['proxy_sha256']))
    return dict(package=str(package),package_sha256=package_sha,package_metadata=dict(path=str(package_meta),sha256=digest(package_meta)),
                manifest=dict(path=str(manifest_path),sha256=digest(manifest_path)),expected_colliders=meta['actual_enabled_colliders'],
                native_instanceable=meta['native_instanceable'],source_mappings=mappings,
                light_layer=dict(path=str(light_path),sha256=binding['sha256']),light_source_root=light_root,
                light_metadata=dict(path=str(light_metadata),sha256=digest(light_metadata)),
                expected_light_count=light['light_prim_count'],passed_C5=c5_ref)


def _mapped_cutaway(prefix,mappings):
    """Map every physical source contribution intersecting the original prefix."""
    targets=[]
    for row in mappings:
        old=row['original_root'];new=row['package_root']
        if prefix==old or prefix.startswith(old+'/'):targets.append(new+prefix[len(old):])
        elif old.startswith(prefix+'/'):targets.append(new)
    return sorted(set(targets))


def _capture_gray(app,args,rgb_data):
    """No visual-stage references survive into this separate scope."""
    import omni.usd
    import omni.replicator.core as rep
    import carb
    import numpy as np
    from PIL import Image
    from omni.kit.viewport.utility import get_active_viewport
    from pxr import Gf,Sdf,Usd,UsdGeom,UsdPhysics,UsdShade,UsdLux
    started=time.monotonic();inputs=_gray_inputs(args,rgb_data['resolved_source_roots'],rgb_data['collision_manifest_sha256'])
    context=omni.usd.get_context();assert context.open_stage(inputs['package'])
    while context.get_stage_loading_status()[2]:app.update()
    stage=context.get_stage();assert stage
    stage.SetEditTarget(stage.GetSessionLayer())
    assert inputs['native_instanceable'] is False
    assert not any(p.IsInstance() for p in stage.Traverse()),'PURE_COLLISION_PACKAGE_HAS_INSTANCES'
    enabled=[];disabled=[];edits=[]
    for prim in stage.Traverse():
        path=str(prim.GetPath());is_enabled=prim.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False
        if is_enabled:
            assert prim.IsA(UsdGeom.Gprim),'ENABLED_COLLIDER_NOT_GPRIM:'+path
            enabled.append(path)
        elif prim.HasAPI(UsdPhysics.CollisionAPI):disabled.append(path)
        if prim.IsA(UsdGeom.Imageable):edits.append((path,prim.IsA(UsdGeom.Gprim),is_enabled))
    assert len(enabled)==inputs['expected_colliders'],'GRAY_ENABLED_COLLIDER_COUNT_MISMATCH'
    roots={r['package_root']:r for r in inputs['source_mappings']};bindings=[]
    for path in sorted(enabled):
        ancestor=path
        while ancestor not in roots and ancestor:ancestor=ancestor.rsplit('/',1)[0]
        assert ancestor,'PACKAGE_COLLIDER_WITHOUT_SOURCE_MAPPING:'+path
        origin=roots[ancestor]['original_root']+path[len(ancestor):]
        bindings.append(dict(original_prim_path=origin,package_prim_path=path))
    assert sorted(r['original_prim_path'] for r in bindings)==rgb_data['enabled_collider_paths'],'VISUAL_TO_PACKAGE_ENABLED_PATHS_MISMATCH'
    # Batch Sdf edits only: no Usd queries while a ChangeBlock is open.
    layer=stage.GetSessionLayer()
    with Sdf.ChangeBlock():
        for path,is_gprim,is_enabled in edits:
            spec=Sdf.CreatePrimInLayer(layer,path)
            for name,value in [('purpose','default' if not is_gprim or is_enabled else 'guide'),
                               ('visibility','inherited')]:
                attr=spec.attributes.get(name)
                if attr is None:attr=Sdf.AttributeSpec(spec,name,Sdf.ValueTypeNames.Token,
                    Sdf.VariabilityUniform if name=='purpose' else Sdf.VariabilityVarying)
                attr.default=value
    material=UsdShade.Material.Define(stage,'/Factory80V4Gray')
    shader=UsdShade.Shader.Define(stage,'/Factory80V4Gray/Surface');shader.CreateIdAttr('UsdPreviewSurface')
    shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.42,.42,.42))
    shader.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(.9)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),'surface')
    UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath('/World')).Bind(material,UsdShade.Tokens.strongerThanDescendants)
    light=stage.DefinePrim('/World/SampleLighting')
    light.GetReferences().AddReference(inputs['light_layer']['path'],inputs['light_source_root'])
    assert np.allclose(np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(light)),np.asarray(rgb_data['light_root_world_matrix']),rtol=0,atol=1e-10),'LIGHT_WORLD_POSE_CHANGED'
    light_count=sum(p.HasAPI(UsdLux.LightAPI) for p in Usd.PrimRange(light))
    assert light_count==inputs['expected_light_count'],'GRAY_LIGHT_COUNT_MISMATCH'
    plan=rgb_data['plan'];exposure=rgb_data['exposure'];view=rgb_data['views'][1]
    assert view['id']==plan['collision_gray']['camera_from']
    cam=UsdGeom.Camera.Define(stage,view['camera_path']);cam.CreateProjectionAttr('perspective')
    cam.CreateFocalLengthAttr(view['actual_focal_length_mm']);cam.CreateHorizontalApertureAttr(view['actual_horizontal_aperture_mm'])
    cam.CreateVerticalApertureAttr(view['actual_vertical_aperture_mm']);cam.CreateClippingRangeAttr(Gf.Vec2f(*view['actual_clipping_range_m']))
    cam.AddTransformOp().Set(Gf.Matrix4d(*[v for row in view['actual_world_matrix'] for v in row]))
    mapped={p:_mapped_cutaway(p,inputs['source_mappings']) for p in plan['collision_gray']['hide_prefixes']}
    hide_targets={};absent_cutaway=[]
    for original,prefixes in mapped.items():
        for path in prefixes:
            if stage.GetPrimAtPath(path):hide_targets[path]=visibility_targets(stage,path)
            else:
                assert not any(p==path or p.startswith(path+'/') for p in enabled),'MISSING_CUTAWAY_HAS_ENABLED_COLLIDER'
                absent_cutaway.append(dict(original_prefix=original,package_prefix=path,
                    reason='No corresponding physical prim exists in the pure collision package; no enabled collider omitted'))
    hidden=sorted({p for values in hide_targets.values() for p in values})
    with Sdf.ChangeBlock():
        for path in hidden:
            spec=Sdf.CreatePrimInLayer(layer,path);attr=spec.attributes.get('visibility')
            if attr is None:attr=Sdf.AttributeSpec(spec,'visibility',Sdf.ValueTypeNames.Token,Sdf.VariabilityVarying)
            attr.default='invisible'
    visible=sum(UsdGeom.Imageable(stage.GetPrimAtPath(p)).ComputeVisibility()!='invisible' for p in enabled)
    binding_path=Path(args.out)/'COLLIDER_GRAY_BINDING.json'
    binding_path.write_text(json.dumps(dict(inputs=inputs,enabled_collider_count=len(enabled),disabled_collider_count=len(disabled),
        original_to_package_enabled_paths=bindings,cutaway_prefix_mapping=mapped,actual_hidden_prim_paths=hidden,
        visible_enabled_collider_count=visible,absent_cutaway_without_physical_geometry=absent_cutaway,
        all_edits_in_session_layer=True),indent=2))
    viewport=get_active_viewport();viewport.resolution=(1280,720);viewport.camera_path=view['camera_path']
    settings=carb.settings.get_settings()
    for k,v in exposure['settings_readback'].items():settings.set(k,v)
    settings.set_bool('/app/viewport/grid/enabled',False);settings.set_bool('/app/viewport/outline/enabled',False)
    rgb=rep.AnnotatorRegistry.get_annotator('rgb',device='cpu',do_array_copy=True);rgb.attach(viewport.render_product_path)
    print('FACTORY80_PHOTO_STARTED collision_gray.png',flush=True)
    try:
        for _ in range(12):app.update()
        for k,v in exposure['settings_readback'].items():settings.set(k,v)
        future=asyncio.ensure_future(rep.orchestrator.step_async(rt_subframes=4,delta_time=0.,pause_timeline=True,wait_for_render=True))
        while not future.done():app.update()
        future.result();value=rgb.get_data(device='cpu',do_array_copy=True,use_legacy_structure=True)
        arr=np.asarray(value.get('data') if isinstance(value,dict) else value)
        assert arr.ndim==3 and arr.shape[:2]==(720,1280) and arr.shape[2] in (3,4),'RENDER_BUFFER_UNAVAILABLE'
        Image.fromarray(arr[:,:,:3]).save(Path(args.out)/'collision_gray.png')
    finally:rgb.detach()
    assert digest(inputs['package'])==inputs['package_sha256'] and digest(inputs['light_layer']['path'])==inputs['light_layer']['sha256']
    print('FACTORY80_PHOTO_SAVED '+json.dumps(dict(file='collision_gray.png',elapsed_s=time.monotonic()-started)),flush=True)
    return dict(binding=dict(path=str(binding_path),sha256=digest(binding_path)),inputs=inputs,
        enabled_collider_geometry_total=len(enabled),enabled_collider_geometry_displayed=visible,
        actual_light_count=light_count,actual_frame_visibility=dict(file='collision_gray.png',role='authored_collider_gray_cutaway',
            actual_hidden_prim_paths=hidden,actual_protected_visible_prim_paths=[],baseline_restored_prim_paths=[],original_to_package_cutaway=mapped),
        actual_camera_readback=dict(view,actual_world_matrix=np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(cam.GetPrim())).tolist()),
        elapsed_s=time.monotonic()-started)


def capture_session(app,args,log):
    """Same SDK: release the completed six-RGB stage before pure-collision gray."""
    started=time.monotonic()
    data=_capture_rgb(app,args,log)  # Returns JSON values only, no Stage/Prim/viewport/annotator.
    if getattr(args,'rgb_only_qualification',False):
        result=dict(tool='render@v7_RGB_ONLY_QUALIFICATION',status='PASS',actual_camera_readbacks=data['views'],
                    actual_frame_visibility=data['frame_visibility'],rgb_session_instancing=data['rgb_session_instancing'],
                    actual_GPU=data['selected_GPU'],exposure_settings_readback=data['actual_exposure_settings_readback'],
                    expected_exposure_settings=data['exposure']['settings_readback'],
                    RGB_elapsed_s=data['RGB_elapsed_s'],image_files=[r['id']+'.png' for r in data['views']],
                    source_sha256=digest(Path(__file__)),input_sha=dict(stage=digest(args.stage),cameras=digest(args.cameras)))
        (Path(args.out)/'PHOTOS.json').write_text(json.dumps(result,indent=2));return result
    transition=lifecycle_cleanup(app,'rgb_to_collision_gray')
    gray=_capture_gray(app,args,data)
    plan=data['plan'];result=dict(tool='render@v7',status='PASS',automatic_camera_plan=str(args.cameras),
        camera_rules_sha256=plan['camera_rules_sha256'],camera_planner_source_sha256=plan['source_sha256'],
        camera_plan_input_sha256=plan['input_sha256'],actual_frame_visibility=data['frame_visibility']+[gray['actual_frame_visibility']],
        scale_xy=1.,actual_camera_readbacks=data['views'],actual_gray_camera_readback=gray['actual_camera_readback'],
        actual_GPU=data['selected_GPU'],GPU_before=data['GPU_before'],
        enabled_collider_geometry_displayed=gray['enabled_collider_geometry_displayed'],
        enabled_collider_geometry_total=gray['enabled_collider_geometry_total'],collision_gray=gray,
        rgb_session_instancing=data['rgb_session_instancing'],shader_runtime_locator_overrides=data['locator_overrides'],RGB_elapsed_s=data['RGB_elapsed_s'],
        lifecycle_rgb_to_gray=transition,image_files=[r['id']+'.png' for r in data['views']]+['collision_gray.png'],
        image_scoring='NOT_EXECUTED_USER_VISUAL_REVIEW',elapsed_s=time.monotonic()-started,
        peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        source_sha256=digest(Path(__file__)),input_sha=dict(stage=digest(args.stage),cameras=digest(args.cameras)))
    (Path(args.out)/'PHOTOS.json').write_text(json.dumps(result,indent=2))
    return result
