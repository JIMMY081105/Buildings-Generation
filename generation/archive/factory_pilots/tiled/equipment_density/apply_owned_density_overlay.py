"""ROOT-only fresh N2 equipment overlay. Default performs no USD work."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, os, stat, sys, traceback
from pathlib import Path
from datetime import datetime, timezone

HERE = Path(__file__).absolute().parent
RESOURCE_FD = None
RESOURCE_ACQUIRED = False
KNOWN_STOP = False
ACTIVE_OUTPUT = None
SOURCE_BEFORE = []
N2_SCOPE = tuple('factory80_%03d'%n for n in (1,2,3,4,5,6,9,11,13,16,17,18,21,27,29,33,34,36,42,46,49,54,57,60,61,63,67,73,77))


from stat_hash_cache import StatHashCache
SOURCE_STAT_CACHE=StatHashCache([Path('/cephfs/shared/yixing/outputs/factory80_20261011/work/P1_HASH_CACHE_20261015.json'),Path('/cephfs/shared/yixing/outputs/factory80_20261011/work/P2_COLLISION_REPAIR_20261015/HASH_CACHE.json')])
def ref(path):return SOURCE_STAT_CACHE.ref(path)


def require(v,reason):
    if not v:raise ValueError(reason)


def checked(r):
    require(ref(r['path'])==r,'Source fullref differs: '+r['path'])


def verify_vector_pass(records):
    # This cache lives for only this single pass; end-of-work gets a new cache.
    current_by_path={};current_vector=[]
    for r in records:
        if r['path'] not in current_by_path:current_by_path[r['path']]=ref(r['path'])
        current=current_by_path[r['path']]
        require(current==r,'Source fullref differs: '+r['path'])
        current_vector.append(current)
    return current_vector


def load(r):
    checked(r);return json.loads(Path(r['path']).read_bytes())


def save(path,value):
    with Path(path).open('x',encoding='utf8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write('\n');stream.flush();os.fsync(stream.fileno())


def fresh_owned_output(path,workspace):
    require('..' not in Path(path).parts,'Output may not contain parent traversal')
    requested=Path(path).absolute();canonical_work=workspace.resolve(strict=True)
    require(requested.is_relative_to(workspace) and requested!=workspace,
            'Output lexical path must be inside the authorized work root')
    relative=requested.relative_to(workspace)
    cursor=workspace
    require(not cursor.is_symlink(),'Authorized work root may not be a symlink')
    for part in relative.parts:
        cursor=cursor/part
        require(not cursor.is_symlink(),'Output/ancestor symlink refused: '+str(cursor))
    canonical=requested.resolve(strict=False)
    require(canonical.is_relative_to(canonical_work) and canonical!=canonical_work
            and not canonical.exists(),'Fresh canonical work-contained output required')
    return canonical


def main():
    global RESOURCE_FD,RESOURCE_ACQUIRED,KNOWN_STOP,ACTIVE_OUTPUT,SOURCE_BEFORE
    parser=argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--input-bindings',type=Path)
    parser.add_argument('--expected-input-bindings-sha256')
    parser.add_argument('--expected-source-index-sha256')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not args.execute:
        print(json.dumps(dict(status='SOURCE_ONLY_DENSITY_OVERLAY_NOT_EXECUTED')));return 0
    require(all(v is not None for v in (args.input_bindings,args.expected_input_bindings_sha256,
                                       args.expected_source_index_sha256,args.output)),
            'Explicit ROOT actual input/source hashes and fresh output required')
    ir=ref(args.input_bindings);require(ir['sha256']==args.expected_input_bindings_sha256,'Input SHA differs')
    data=load(ir);require(data['schema']=='Factory80OwnedN2DensityOverlayInputV1','Explicit density input schema')
    workspace=Path(data['authorized_work_root']).absolute()
    require(str(workspace)=='/cephfs/shared/yixing/outputs/factory80_20261011/work','Authorized original work root required')
    context=json.loads((workspace.parent/'OPEN_ISSUES.json').read_bytes())
    if context.get('entire_task_stop') is True or context.get('critical_stop_markers'):
        KNOWN_STOP=True;raise RuntimeError('Previously observed GLOBAL STOP; no density work')
    require(context.get('schema')=='Factory80OpenIssuesV1' and context.get('entire_task_stop') is False
            and context.get('critical_stop_markers')==[],'Unqualified current GLOBAL context')
    own_index=ref(HERE/'SOURCE_INDEX.json')
    require(own_index['sha256']==args.expected_source_index_sha256,'Reviewed OWN index differs')
    ix=load(own_index);before=[ir,own_index]
    for r in ix['runtime_sources']:
        rr=ref(HERE/str(r['path']).replace(chr(92),'/').rsplit('/',1)[-1])
        require((rr['bytes'],rr['sha256'])==(r['bytes'],r['sha256']),'Own deployed source differs');before.append(rr)
    for key in ('floor_contract_ref','door_policy_ref','pipeline_contract_ref'):
        wanted=ix['unchanged_original_api_refs'][key];actual=data[key]
        require((actual['bytes'],actual['sha256'])==(wanted['bytes'],wanted['sha256']),'Frozen original API differs')
    author=load(data['authoring_ref']);checked(data['root_ref'])
    require(author['schema']=='Factory80RootOverlayAuthoringV1' and author['storeys']==2 and
            author['work_state']=='GEOMETRY_AUTHORED' and author['stage']==data['root_ref'],
            'Only a real closed original N2 author/root binding')
    require(author['building'] in N2_SCOPE and type(author['attempt']) is int and author['attempt'] in (1,2,3)
            and author['stair_count'] in (2,3) and len(author['stairs'])==author['stair_count'],
            'Only the authorized29 N2 and original1..3 recipes/M2..3; no new Native or fourth recipe')
    before.extend([data['authoring_ref'],data['root_ref'],*data['base_dependency_refs'],
                   data['screen_ref'],data['geometry_catalog_ref'],
                   data['floor_contract_ref'],data['door_policy_ref'],data['pipeline_contract_ref']])
    screen=load(data['screen_ref']);catalog=load(data['geometry_catalog_ref'])
    require(catalog['screen_sha256']==data['screen_ref']['sha256'],'Original catalog/screen identity differs')
    sources=data['eligible_scene_sources']
    require(sources and len({s['scene'] for s in sources})==len(sources),'Unique source scene groups required')
    for s in sources:before.extend([s['source_ref'],s['metadata_ref'],*s['dependency_refs']])
    SOURCE_BEFORE=before
    authority={r['path']:r for r in before}
    # One existing CPU lease, no outer lease around this callee. No GPU/SDK.
    import fcntl
    RESOURCE_FD=os.open(workspace/'gate_audit/CPU_HEAVY.lock',os.O_RDWR|os.O_NOFOLLOW)
    lockstat=os.fstat(RESOURCE_FD)
    require(stat.S_ISREG(lockstat.st_mode) and (lockstat.st_dev,lockstat.st_ino)==(55,1100794017351),
            'Original existing CPU lock inode required; NO_CREAT')
    fcntl.flock(RESOURCE_FD,fcntl.LOCK_EX|fcntl.LOCK_NB)
    RESOURCE_ACQUIRED=True
    verify_vector_pass(before)
    from pxr import Gf,Sdf,Usd,UsdGeom,UsdPhysics
    from shapely.geometry import box,LineString
    from shapely.ops import unary_union
    from density_rows import POLICY,row_plan,verify_instance_policy
    from stage_d_material_binding import restore_material_context,check_material_context,source_asset_refs
    for r in (data['floor_contract_ref'],data['door_policy_ref'],data['pipeline_contract_ref']):checked(r)
    api_dir=Path(data['floor_contract_ref']['path']).absolute().parent
    require(api_dir==Path(data['door_policy_ref']['path']).absolute().parent,
            'Original floor/door module directory required')
    sys.path.insert(0,str(api_dir))
    import floor_collider_contract as fc
    require(ref(fc.__file__)==data['floor_contract_ref'] and
            ref(sys.modules['door_collision_policy'].__file__)==data['door_policy_ref'],
            'Actual floor/door original callable origin differs')
    spec=importlib.util.spec_from_file_location('density_original_pipeline',data['pipeline_contract_ref']['path'])
    pipeline=importlib.util.module_from_spec(spec);spec.loader.exec_module(pipeline)
    require((pipeline.BODY_RADIUS_M,pipeline.BODY_HEIGHT_M,pipeline.CORRIDOR_M)==(.22,1.75,1.2),
            'Original placement/landing contract exact; no threshold change')
    output=fresh_owned_output(args.output,workspace)
    output.mkdir(parents=True,exist_ok=False)
    ACTIVE_OUTPUT=output
    # Same-byte bca helper, independently reviewed/actually used for A027.
    # It proves the exact six OWN disabled duplicates and all retained child
    # triangles/materials before adding purpose=proxy; no wall group is removed.
    from prepare_owned_disabled_wall_purpose_overlay import prepare as purpose_prepare
    registered=author.get('synthetic_wall_collision_partitions',[])
    require(len(registered) in (0,6),'Only absent legacy registration or exact six OWN partitions')
    if registered:
        purpose_doc,purpose_receipt_ref,purpose_author_ref=purpose_prepare(
            data['authoring_ref']['path'],data['authoring_ref']['sha256'],
            data['floor_contract_ref']['path'],data['door_policy_ref']['path'],
            output/'OWN_DISABLED_DUPLICATE_PURPOSE_R1')
        purpose_root=purpose_doc['derived_stage']
        generated_purpose_refs=[purpose_root,purpose_receipt_ref,purpose_author_ref]
    else:
        # Actual pilot002 has no registered disabled duplicate partition. No
        # purpose opinion is invented; the original contract still runs below.
        original_stage=Usd.Stage.Open(data['root_ref']['path']);require(original_stage,'Original root cannot open')
        purpose_root=data['root_ref'];purpose_receipt_ref=None;generated_purpose_refs=[]
        purpose_doc=dict(status='NOT_APPLICABLE_NO_AUTHOR_REGISTERED_DUPLICATE_PARTITIONS',
            source_dependencies_before=[],actual_exact_triangle_reconstruction=[],
            original_frozen_floor_profiles={p:fc.floor_profile(original_stage,p)
                for p in ('/World/Floor1','/World/Floor2')})
    # Original full dependencies stay immutable expected sources. New owned
    # purpose files are separate generated inputs, never disguised as old refs.
    for r in purpose_doc['source_dependencies_before']:
        require(r['path'] in authority,'Unregistered original purpose input: '+r['path'])
        checked(authority[r['path']])
    for r in generated_purpose_refs:checked(r)
    authority.update({r['path']:r for r in generated_purpose_refs})
    parent=Usd.Stage.Open(purpose_root['path']);require(parent,'Actual same-byte purpose root cannot open')
    require(UsdGeom.GetStageMetersPerUnit(parent)==1 and UsdGeom.GetStageUpAxis(parent)=='Z','Original metre/Z root')
    def close_sources(stage):
        for layer in stage.GetUsedLayers():
            if layer.anonymous:continue
            path=layer.realPath.split('[',1)[0]
            require(path and str(Path(path).absolute()) in authority,'Unregistered USD dependency: '+path)
            checked(authority[str(Path(path).absolute())])
        for r in source_asset_refs(stage):
            require(r['path'] in authority,'Unregistered shader/texture dependency: '+r['path']);checked(authority[r['path']])
    close_sources(parent)
    cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render','guide'],useExtentsHint=False)
    xcache=UsdGeom.XformCache()
    def aabb(prim):
        b=cache.ComputeWorldBound(prim).ComputeAlignedRange()
        require(not b.IsEmpty(),'Empty actual USD bound: '+str(prim.GetPath()))
        return list(b.GetMin()),list(b.GetMax())
    def prim_signature(stage,path):
        p=stage.GetPrimAtPath(path);require(p,'Source prim missing: '+path)
        return [(str(q.GetPath()),q.GetTypeName(),q.IsActive(),
                 str(UsdGeom.XformCache().GetLocalToWorldTransform(q)),
                 [(a.GetName(),str(a.Get()),[(t,str(a.Get(Usd.TimeCode(t)))) for t in a.GetTimeSamples()])
                  for a in q.GetAttributes()]) for q in Usd.PrimRange(p)]
    protected=['/World/Cut','/World/Stair','/World/FactoryStructure','/World/MezzanineRail','/World/FactoryLighting']
    protected=[p for p in protected if parent.GetPrimAtPath(p)]
    structural_before={p:prim_signature(parent,p) for p in protected}
    floors=[]
    for fi in (0,1):
        receipt=author['cut_meshes']['Floor'+str(fi+1)+'Slab']
        path=receipt['visual_path'];mesh=UsdGeom.Mesh(parent.GetPrimAtPath(path));require(mesh,'Actual authored slab visual missing')
        z=receipt['z'][1];matrix=xcache.GetLocalToWorldTransform(mesh.GetPrim())
        pts=[matrix.Transform(Gf.Vec3d(v)) for v in mesh.GetPointsAttr().Get()]
        triangles=[];cursor=0;indices=mesh.GetFaceVertexIndicesAttr().Get()
        for n in mesh.GetFaceVertexCountsAttr().Get():
            ids=list(indices[cursor:cursor+n]);cursor+=n
            if all(abs(pts[i][2]-z)<=1e-6 for i in ids):
                from shapely.geometry import Polygon
                polygon=Polygon([(pts[i][0],pts[i][1]) for i in ids])
                if polygon.area:triangles.append(polygon)
        poly=unary_union(triangles)
        require(poly.geom_type=='Polygon' and poly.is_valid,'Actual hole-cut slab not one Polygon')
        floors.append(dict(floor_id=fi,z_m=z,polygon=poly.__geo_interface__,actual_surface_path=path))
    old=[eq for inv in author['source_inventory'] for eq in inv['equipment_retained']]
    oldpaths=[e['actual_root_prim_path'] for e in old]
    require(len({e['canonical_asset_id'] for e in old})==len(old),'Original canonical duplicates require explicit review')
    for p in oldpaths:
        require(p.startswith(('/World/Floor1/Geometry/Factory','/World/Floor2/Geometry/Factory')),
                'Only explicitly registered owned original equipment may be replaced')
        require(parent.GetPrimAtPath(p),'Registered original equipment missing')
    obstacles=[]
    for p in fc.stage_prims_with_instance_proxies(parent):
        name=str(p.GetPath())
        fixed_structure=(p.IsA(UsdGeom.Gprim) and name.startswith('/World/FactoryStructure/'))
        if (fc.enabled_mesh_collider(p) or fixed_structure) and not any(name==v or name.startswith(v+'/') for v in oldpaths):
            lo,hi=aabb(p);obstacles.append(dict(reason='EXISTING_FIXED_GEOMETRY:'+name,
                polygon=box(lo[0],lo[1],hi[0],hi[1]).__geo_interface__,z_min=lo[2],z_max=hi[2]))
    for s in author['stairs']:
        points=s['world_path']
        require(len(points)>1,'Original stair path required')
        first=Gf.Vec2d(*(points[1][i]-points[0][i] for i in (0,1))).GetNormalized()
        last=Gf.Vec2d(*(points[-1][i]-points[-2][i] for i in (0,1))).GetNormalized()
        full=[[points[0][0]-first[0]*pipeline.CORRIDOR_M,points[0][1]-first[1]*pipeline.CORRIDOR_M,points[0][2]],
              *points,[points[-1][0]+last[0]*pipeline.CORRIDOR_M,points[-1][1]+last[1]*pipeline.CORRIDOR_M,points[-1][2]]]
        for aa,bb in zip(full,full[1:]):
            obstacles.append(dict(reason='ORIGINAL_STAIR_PATH:'+s['root'],
                polygon=LineString([aa[:2],bb[:2]]).buffer(pipeline.BODY_RADIUS_M).__geo_interface__,
                z_min=min(aa[2],bb[2]),z_max=max(aa[2],bb[2])+pipeline.BODY_HEIGHT_M))
        for point,direction in ((points[0],-first),(points[-1],last)):
            obstacles.append(dict(reason='ORIGINAL_LANDING_CORRIDOR:'+s['root'],
                polygon=pipeline.corridor_polygon(point,direction).__geo_interface__,
                z_min=point[2],z_max=point[2]+pipeline.BODY_HEIGHT_M))
    # Original 4m facade apertures and their cross-factory connecting aisle stay clear.
    width,depth=author['footprint_bounds_m'][2:]
    line=LineString([(width*.4+2,0),(width*.4+2,depth/2),
                     (width*.6+2,depth/2),(width*.6+2,depth)])
    obstacles.append(dict(reason='OWN_MAIN_FACADE_CONNECTING_AISLE_2M',
        polygon=line.buffer(1,cap_style=2,join_style=2).__geo_interface__,z_min=0,z_max=author['floor_to_floor_m']+pipeline.BODY_HEIGHT_M))
    screens={r['scene'].removesuffix('_ceiling'):r for r in screen['ranked_scenes']}
    catalogs={r['scene']:r for r in catalog['scenes']};pool=[];opened={}
    for s in sources:
        scene=s['scene'];require(catalogs[scene]['source']==s['source_ref'],'Catalog source binding differs')
        native=Usd.Stage.Open(s['source_ref']['path']);require(native,'Source USD cannot open')
        close_sources(native);require(UsdGeom.GetStageMetersPerUnit(native)==1 and UsdGeom.GetStageUpAxis(native)=='Z','Source metre/Z required; never rescale')
        metadata=load(s['metadata_ref']);nr=native.GetDefaultPrim();require(nr,'Original source default prim required for object metadata binding')
        native_prims=tuple(fc.stage_prims_with_instance_proxies(native))
        evidence={e['asset_id']:e for group in ('industrial','operational','warehouse')
                  for e in screens[scene]['evidence'].get(group,[])}
        for p in native.GetPrimAtPath(str(nr.GetPath())+'/Geometry').GetChildren():
            m=metadata['objects'].get(p.GetName());asset=m.get('asset_id') if m else None
            if asset not in evidence:continue
            allmeshes=fc._object_meshes(native_prims,str(p.GetPath()))
            allmeshes=[q for q in allmeshes if fc.leaf_member_kind(q) is None]
            guide=[q for q in allmeshes if fc._purpose(q)=='guide']
            expected=guide or [q for q in allmeshes if fc._purpose(q) in ('default','render')]
            require(expected,'Original expected collider representation absent: '+str(p.GetPath()))
            visual_lo,visual_hi=aabb(p);cb=[aabb(q) for q in expected]
            lo=[min([visual_lo[i],*[b[0][i] for b in cb]]) for i in range(3)]
            hi=[max([visual_hi[i],*[b[1][i] for b in cb]]) for i in range(3)]
            pool.append(dict(name=p.GetName(),canonical_asset_id=asset,source_scene_id=scene,
                source_ref=s['source_ref'],source_prim_path=str(p.GetPath()),bbox_min=lo,bbox_max=hi,
                actual_visual_bbox=[visual_lo,visual_hi],actual_expected_collider_bbox=[
                    [min(b[0][i] for b in cb) for i in range(3)],[max(b[1][i] for b in cb) for i in range(3)]],
                actual_expected_collider_mesh_paths=[str(q.GetPath()) for q in expected],
                source_original_bottom_z_m=lo[2],original_screen_evidence=evidence[asset],
                dimensions_evidence='ACTUAL_USD_VISUAL_AND_ORIGINAL_EXPECTED_COLLIDER_UNION'))
        opened[scene]=native
    plan=row_plan(floors,pool,author['tile_pitch_m'],author['grid_n'],obstacles)
    save(output/'ACTUAL_DENSITY_ROW_PLAN.json',plan)
    layer=Sdf.Layer.CreateNew(str(output/'density_overlay.usda'))
    layer.subLayerPaths=[purpose_root['path']];derived=Usd.Stage.Open(layer)
    derived.SetDefaultPrim(derived.GetPrimAtPath('/World'))
    UsdGeom.SetStageMetersPerUnit(derived,1.0);UsdGeom.SetStageUpAxis(derived,'Z')
    for p in oldpaths:derived.OverridePrim(p).SetActive(False)
    mappings={};inventory=[]
    for i,o in enumerate(plan['placements']):
        path='/World/Floor'+str(o['floor_id']+1)+'/Geometry/FactoryDensity_'+str(i).zfill(4)
        native=opened[o['source_scene_id']];source=native.GetPrimAtPath(o['source_prim_path'])
        dp=derived.DefinePrim(path,source.GetTypeName());dp.GetReferences().AddReference(o['source_ref']['path'],o['source_prim_path'])
        dp.SetInstanceable(False)
        while True:
            instances=[q for q in Usd.PrimRange(dp) if q.IsInstance()]
            if not instances:break
            for q in instances:derived.OverridePrim(str(q.GetPath())).SetInstanceable(False)
        angle=o['rotation_deg'];r=Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0,0,1),angle))
        corners=[r.Transform(Gf.Vec3d(x,y,z)) for x in (o['bbox_min'][0],o['bbox_max'][0])
                 for y in (o['bbox_min'][1],o['bbox_max'][1]) for z in (o['bbox_min'][2],o['bbox_max'][2])]
        offset=[o['world_bbox_min'][j]-min(p[j] for p in corners) for j in range(3)]
        matrix=UsdGeom.XformCache().GetLocalToWorldTransform(source)*r*Gf.Matrix4d().SetTranslate(Gf.Vec3d(*offset))
        xf=UsdGeom.Xformable(dp);xf.ClearXformOpOrder();xf.SetResetXformStack(True);xf.AddTransformOp().Set(matrix)
        for q in Usd.PrimRange(dp):q.RemoveAPI(UsdPhysics.RigidBodyAPI)
        mappings.setdefault((o['source_scene_id'],o['repetition_index']),{})[o['source_prim_path']]=path
        inventory.append(dict(o,actual_root_prim_path=path,actual_delta_translation=offset))
    layer.Save()
    class Mapping:
        def __init__(self,selected,prefix):self.selected=selected;self.prefix=prefix
        def path(self,p):
            s=str(p)
            for src,dst in self.selected.items():
                if s==src or s.startswith((src+'/',src+'.')):return dst+s[len(src):]
            return self.prefix+s
    material_proofs=[];material_views=[]
    for ci,((scene,repetition_index),selected) in enumerate(mappings.items()):
        # Anonymous selection view only. The original source layer is never saved.
        native=opened[scene];session=Sdf.Layer.CreateAnonymous('density_material_selection')
        view=Usd.Stage.Open(native.GetRootLayer(),session);view.SetEditTarget(session)
        # Root/session-only deinstancing makes selected collision opinions and
        # masking addressable; no source file or prototype shape is rewritten.
        while True:
            instances=[p for p in view.Traverse() if p.IsInstance()]
            if not instances:break
            for p in instances:view.OverridePrim(str(p.GetPath())).SetInstanceable(False)
        for p in list(Usd.PrimRange.Stage(view,Usd.TraverseInstanceProxies())):
            name=str(p.GetPath())
            if p.IsA(UsdGeom.Gprim) and not any(name==src or name.startswith(src+'/') for src in selected):
                view.OverridePrim(name).SetActive(False)
        mapping=Mapping(selected,'/World/OwnedDensityMaterialContexts/C'+str(ci).zfill(4))
        material_proofs.append(restore_material_context(view,derived,mapping,
                                  next(s['source_ref']['path'] for s in sources if s['scene']==scene)))
        material_views.append((view,mapping))
        # Compare the selected original (unmasked) bindings as well. A material
        # hidden by the session selection cannot make an absent binding pass.
        from pxr import UsdShade
        for src,dst in selected.items():
            for q in Usd.PrimRange(native.GetPrimAtPath(src),Usd.TraverseInstanceProxies()):
                if not (q.IsA(UsdGeom.Gprim) or q.IsA(UsdGeom.Subset)):continue
                actual=derived.GetPrimAtPath(dst+str(q.GetPath())[len(src):])
                require(actual,'Original selected geometry/subset lost')
                for purpose in (UsdShade.Tokens.allPurpose,UsdShade.Tokens.preview,UsdShade.Tokens.full):
                    original_mat=UsdShade.MaterialBindingAPI(q).ComputeBoundMaterial(purpose)[0]
                    actual_mat=UsdShade.MaterialBindingAPI(actual).ComputeBoundMaterial(purpose)[0]
                    require((mapping.path(original_mat.GetPath()) if original_mat else None)==
                            (str(actual_mat.GetPath()) if actual_mat else None),
                            'Original unmasked selected material binding lost')
    for fi in (0,1):
        expected,_=fc.expected_mesh_prims(derived,'/World/Floor'+str(fi+1));wanted={str(p.GetPath()) for p in expected}
        for record in (r for r in inventory if r['floor_id']==fi):
            for p in Usd.PrimRange(derived.GetPrimAtPath(record['actual_root_prim_path'])):
                p.RemoveAPI(UsdPhysics.RigidBodyAPI)
                if p.IsA(UsdGeom.Mesh):
                    if str(p.GetPath()) in wanted:
                        UsdPhysics.CollisionAPI.Apply(p).CreateCollisionEnabledAttr(True)
                        UsdPhysics.MeshCollisionAPI.Apply(p).CreateApproximationAttr('none')
                    elif p.HasAPI(UsdPhysics.CollisionAPI):
                        UsdPhysics.CollisionAPI(p).CreateCollisionEnabledAttr(False)
    layer.Save();reopened=Usd.Stage.Open(str(output/'density_overlay.usda'));actual=[]
    saved_material_proofs=[check_material_context(view,reopened,mapping) for view,mapping in material_views]
    finalcache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render','guide'],useExtentsHint=False)
    for record in inventory:
        b=finalcache.ComputeWorldBound(reopened.GetPrimAtPath(record['actual_root_prim_path'])).ComputeAlignedRange()
        lo=list(b.GetMin());hi=list(b.GetMax())
        # Float matrix readback only; not an altered scientific tolerance.
        require(max(abs(lo[i]-record['world_bbox_min'][i]) for i in range(3))<=1e-5 and
                max(abs(hi[i]-record['world_bbox_max'][i]) for i in range(3))<=1e-5,
                'Actual saved equipment bounds differ from planned collision/visual envelope')
        actual.append(dict(record,actual_saved_world_bbox_min=lo,actual_saved_world_bbox_max=hi))
    require({p:prim_signature(reopened,p) for p in protected}==structural_before,
            'Slab/stair/structure/rail signature changed; density scope only')
    profiles={p:fc.floor_profile(reopened,p) for p in ('/World/Floor1','/World/Floor2')}
    after=verify_vector_pass(before);require(before==after,'Actual source bytes changed')
    for r in generated_purpose_refs:checked(r)
    groups=[]
    for scene in sorted({record['source_scene_id']for record in actual}):
        s=next(s for s in sources if s['scene']==scene)
        groups.append(dict(scene=scene,source_ref=s['source_ref'],metadata_ref=s['metadata_ref'],
                           equipment_retained=[r for r in actual if r['source_scene_id']==scene]))
    counts_by_asset=verify_instance_policy(actual)
    SOURCE_STAT_CACHE.save(output/'ACTUAL_STAT_HASH_CACHE.json')
    shortfall=sum(r['shortfall'] for r in plan['floor_density'])
    receipt=dict(schema='Factory80OwnedN2DensityDerivedAuthoringV1',utc=datetime.now(timezone.utc).isoformat(),
        work_state='DENSITY_GEOMETRY_AUTHORED_NOT_VALIDATED',building=author['building'],attempt=author['attempt'],storeys=2,
        stage=ref(output/'density_overlay.usda'),original_root_ref=data['root_ref'],original_authoring_ref=data['authoring_ref'],
        actual_purpose_corrected_root_ref=purpose_root,actual_purpose_overlay_receipt_ref=purpose_receipt_ref,
        purpose_overlay_status=purpose_doc.get('status'),
        original_raw_floor_profiles_preserved=purpose_doc['original_frozen_floor_profiles'],
        samebyte_bca_purpose_proof=purpose_doc['actual_exact_triangle_reconstruction'],
        floor_density=plan['floor_density'],subset_shortfall=shortfall,
        source_inventory=groups,retained_equipment_asset_count=len(actual),
        independent_canonical_asset_count=len(counts_by_asset),instance_total=len(actual),
        max_instances_per_canonical_asset=max(counts_by_asset.values(),default=0),
        retained_duplicate_asset_count=len(actual)-len(counts_by_asset),
        same_building_scene_reuse_count=sum(max(0,n-1)for n in __import__('collections').Counter(r['source_scene_id']for r in actual).values()),replaced_original_equipment_paths=oldpaths,
        floor_collider_profiles=profiles,actual_material_preservation=material_proofs,
        actual_saved_reopened_material_preservation=saved_material_proofs,
        policy=POLICY,source_refs_before=before,source_refs_after=after,source_layers_sha_unchanged=True,
        geometry_changed=True,old_scientific_results_inherited=False,
        mandatory_fresh=['all_scene_dependencies','scratch','Standability_V1','G25','navigation','CPU_measurement_frames','StageD','visual'],
        frozen_science_or_sourceUSDZ_changed=False,
        cannot_support='Row/AABB placement and authored density do not prove body traversal, graph/G25/physics, industrial photorealism or acceptance. Subset shortfall says nothing about the confirmed full2279 library.')
    save(output/'AUTHORING.DENSITY.json',receipt)
    print(json.dumps(dict(receipt_ref=ref(output/'AUTHORING.DENSITY.json'),subset_shortfall=shortfall,
                         actual_count=len(actual),all_floor_profiles_pass=all(p['status']=='PASS' for p in profiles.values()),
                         scientific_gates='NOT EXECUTED')))
    return 1 if shortfall or any(p['status']!='PASS' for p in profiles.values()) else 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        error=traceback.format_exc();after=[];rehash_errors=[]
        if RESOURCE_ACQUIRED:
            current_by_path={};failure_by_path={}
            for r in SOURCE_BEFORE:
                path=r['path']
                if path not in current_by_path:
                    try:current_by_path[path]=ref(path)
                    except Exception:
                        current_by_path[path]=None;failure_by_path[path]=traceback.format_exc()
                after.append(current_by_path[path])
                if path in failure_by_path:rehash_errors.append(dict(source_ref=r,traceback=failure_by_path[path]))
        else:after=None
        try:
            if ACTIVE_OUTPUT is not None:
                save(ACTIVE_OUTPUT/'ACTUAL_ORDINARY_DENSITY_FAILURE.json',dict(
                    traceback=error,source_refs_before=SOURCE_BEFORE,source_refs_after=after,
                    source_rehash_errors=rehash_errors,
                    source_after_qualification='ACTUAL_REHASH_UNDER_OWN_CPU_LEASE' if RESOURCE_ACQUIRED else 'NOT_EXECUTED_CPU_LEASE_NOT_ACQUIRED',
                    source_bytes_unchanged=(not rehash_errors and SOURCE_BEFORE==after) if RESOURCE_ACQUIRED else None,
                    entire_task_stop=KNOWN_STOP,scientific_gates='NOT EXECUTED'))
        except Exception:error+='\nOWN failure receipt IO:\n'+traceback.format_exc()
        try:print(error,file=sys.stderr,flush=True)
        finally:raise SystemExit(2 if KNOWN_STOP else 1)
    finally:
        try:
            if RESOURCE_FD is not None:
                try:
                    import fcntl
                    if RESOURCE_ACQUIRED:fcntl.flock(RESOURCE_FD,fcntl.LOCK_UN)
                finally:os.close(RESOURCE_FD)
        finally:
            if KNOWN_STOP:raise SystemExit(2)
