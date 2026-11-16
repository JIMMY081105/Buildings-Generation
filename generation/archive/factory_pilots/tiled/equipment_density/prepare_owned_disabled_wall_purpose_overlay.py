"""OWN N2 thin root opinion for explicitly registered disabled duplicate walls.

Original A source, USDZ, native layers, geometry and historical AUTHORING stay
unchanged. Only six registered OWN disabled guide parents get purpose=proxy;
their retained guide collider children remain identical physical surfaces.
"""
import argparse,copy,hashlib,importlib.util,json,re,sys
from pathlib import Path
import numpy as np
from pxr import Usd,UsdGeom,UsdPhysics,UsdShade

FROZEN_SOURCES={'floor_collider_contract':'5d46aa2c771d9326599980d6baba2d4bbb559f17b4930cce4d27a22952f2d225',
    'door_collision_policy':'1667de2a0b553513bc5845e93880d95e806bda647db795bde4364af9e11008bf'}

def ref(path):
    p=Path(path).resolve();raw=p.read_bytes();return {'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}

def verify(record):
    if ref(record['path'])!=record:raise ValueError('Source immutability binding changed: '+record['path'])

def save(path,doc):
    with Path(path).open('x',encoding='utf-8')as f:json.dump(doc,f,indent=2,ensure_ascii=False,allow_nan=False);f.write('\n')
    return ref(path)

def original_contract(floor_source,door_source):
    paths={'door_collision_policy':Path(door_source).resolve(),'floor_collider_contract':Path(floor_source).resolve()}
    pins={name:ref(p)for name,p in paths.items()}
    for name,p in paths.items():
        if pins[name]['sha256']!=FROZEN_SOURCES[name]:raise ValueError('Original frozen source differs: '+name)
        spec=importlib.util.spec_from_file_location(name,p);module=importlib.util.module_from_spec(spec)
        sys.modules[name]=module;spec.loader.exec_module(module)
    return sys.modules['floor_collider_contract'],pins

def triangles(mesh):
    points=np.asarray(mesh.GetPointsAttr().Get(),dtype=np.float32);faces=[];cursor=0
    indices=mesh.GetFaceVertexIndicesAttr().Get()
    for count in mesh.GetFaceVertexCountsAttr().Get():
        face=list(indices[cursor:cursor+count]);cursor+=count
        faces.extend((face[0],face[j],face[j+1])for j in range(1,count-1))
    return points[np.asarray(faces,dtype=int)]

def mesh_state(stage,path,require_material=False):
    prim=stage.GetPrimAtPath(path);mesh=UsdGeom.Mesh(prim)
    if not mesh:raise ValueError('Explicit registered OWN mesh is missing: '+path)
    h=hashlib.sha256()
    for values in (np.asarray(mesh.GetPointsAttr().Get(),dtype='<f4'),np.asarray(mesh.GetFaceVertexCountsAttr().Get(),dtype='<i8'),
        np.asarray(mesh.GetFaceVertexIndicesAttr().Get(),dtype='<i8'),np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(prim),dtype='<f8')):
        h.update(json.dumps(values.shape).encode());h.update(values.tobytes())
    material=UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
    if require_material and not material:raise ValueError('Original source-bound material was lost: '+path)
    return {'path':path,'geometry_and_world_pose_sha256':h.hexdigest(),'purpose':str(UsdGeom.Imageable(prim).ComputePurpose()),
        'visibility':str(UsdGeom.Imageable(prim).ComputeVisibility()),'bound_material':str(material.GetPath())if material else None,
        'has_collision_api':prim.HasAPI(UsdPhysics.CollisionAPI),
        'collision_enabled':UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()if prim.HasAPI(UsdPhysics.CollisionAPI)else None,
        'approximation':str(UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get()or'none'),
        'rigid_body_api':prim.HasAPI(UsdPhysics.RigidBodyAPI)}

def source_refs(stage,author_path,author):
    paths={str(Path(author_path).resolve()),author['stage']['path'],str(Path(__file__).resolve())}
    for layer in stage.GetUsedLayers():
        if not layer.anonymous:paths.add(str(Path(layer.realPath.split('[',1)[0]).resolve()))
    for record in author.get('source_immutability_refs',[]):verify(record);paths.add(record['path'])
    for row in author.get('source_inventory',[]):
        for key in ('source_ref','source_metadata_ref'):
            r=row.get(key)
            if r:
                if 'bytes'in r:verify(r)
                elif ref(r['path'])['sha256']!=r['sha256']:raise ValueError('Source inventory metadata SHA changed')
                paths.add(str(Path(r['path']).resolve()))
    for stair in author['stairs']:
        for key in ('stage','metrics','traversal_path'):
            if stair.get(key):paths.add(str(Path(stair[key]).resolve()))
    return [ref(p)for p in sorted(paths)]

def registered_parents(stage,author):
    records=author.get('synthetic_wall_collision_partitions',[])
    if len(records)!=6:raise ValueError('Thin A overlay needs exactly six explicitly registered OWN wall partitions')
    parents=[];children=[];proof=[]
    for index,record in enumerate(records):
        path=record['original_surface_path'];expected=f'/World/Floor1/Geometry/wall_0_{index}/wall_0_{index}_collision_0'
        if path!=expected or record.get('scope')!='Owned synthesized perimeter wall collision surface only':
            raise ValueError('Refusing a surface outside the explicitly registered OWN six-wall namespace')
        if record.get('original_surface_collision_enabled')is not False or record.get('original_voxelizer_or_gate_modified')is not False:
            raise ValueError('Explicit OWN duplicate parent was not registered as disabled unchanged surface')
        state=mesh_state(stage,path,require_material=True)
        if state['purpose']!='guide'or state['collision_enabled']is not False or not state['has_collision_api']or state['rigid_body_api']:
            raise ValueError('Purpose overlay refuses an enabled/non-guide/non-static original parent')
        parts=record['triangle_surface_partition'];rows=parts['parts']
        if not rows or parts.get('exact_original_surface_preserved')is not True:raise ValueError('No registered exact OWN child surface partitions')
        parent_tri=triangles(UsdGeom.Mesh(stage.GetPrimAtPath(path)));reconstructed=np.empty_like(parent_tri);indices=[]
        if hashlib.sha256(parent_tri.tobytes()).hexdigest()!=parts['original_expanded_triangles_sha256']:
            raise ValueError('Actual original disabled parent triangles differ from historical authoring')
        for item in rows:
            child=item['path']
            if not re.fullmatch(re.escape(path)+r'/CollisionTiles/wall_collision_T\d{3}',child):
                raise ValueError('Refusing a child outside registered OWN wall collision tiles')
            child_state=mesh_state(stage,child,require_material=True)
            if child_state['purpose']!='guide'or child_state['collision_enabled']is not True or child_state['approximation']!='none'or child_state['rigid_body_api']:
                raise ValueError('Original enabled guide child static/exact collider contract is broken')
            tri=triangles(UsdGeom.Mesh(stage.GetPrimAtPath(child)));ids=item['original_wall_triangle_indices']
            if len(ids)!=len(tri)or len(tri)!=item['actual_triangle_count']:raise ValueError('Actual child partition count differs from registered triangles')
            reconstructed[ids]=tri;indices.extend(ids);children.append(child_state)
        if sorted(indices)!=list(range(len(parent_tri)))or not np.array_equal(reconstructed,parent_tri):
            raise ValueError('Actual retained children do not exactly reconstruct original surface')
        if hashlib.sha256(reconstructed.tobytes()).hexdigest()!=parts['reconstructed_collision_triangles_sha256']:
            raise ValueError('Actual child surface SHA differs from historical authoring')
        parents.append(state);proof.append({'parent':path,'actual_original_triangle_sha256':hashlib.sha256(parent_tri.tobytes()).hexdigest(),
            'exact_child_reconstruction':True,'child_count':len(rows)})
    return parents,children,proof

def prepare(author_path,expected_author_sha,floor_source,door_source,output):
    author_path=Path(author_path).resolve();original_author_ref=ref(author_path)
    if original_author_ref['sha256']!=expected_author_sha:raise ValueError('Original authoring source differs from explicitly expected bytes')
    author=json.loads(author_path.read_bytes())
    if author.get('schema')!='Factory80RootOverlayAuthoringV1'or type(author.get('storeys'))is not int or author['storeys']!=2:
        raise ValueError('This thin OWN overlay only accepts original A two-storey authoring')
    verify(author['stage']);source=Usd.Stage.Open(author['stage']['path'])
    if not source or str(source.GetDefaultPrim().GetPath())!='/World':raise ValueError('Actual original world/default prim is missing')
    if UsdGeom.GetStageMetersPerUnit(source)!=1. or UsdGeom.GetStageUpAxis(source)!='Z':raise ValueError('Original physical stage units/axis are not1m/Z')
    contract,pins=original_contract(floor_source,door_source);deps=[*source_refs(source,author_path,author),*pins.values()]
    parents,children,proof=registered_parents(source,author)
    all_mesh_before={str(p.GetPath()):mesh_state(source,str(p.GetPath()))for p in source.Traverse()if p.IsA(UsdGeom.Mesh)}
    original_profiles={p:contract.floor_profile(source,p)for p in ('/World/Floor1','/World/Floor2')}
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False);target=output/'ROOT_DISABLED_DUPLICATE_PURPOSE_ONLY.usda'
    stage=Usd.Stage.CreateNew(str(target));stage.GetRootLayer().subLayerPaths=[Path(author['stage']['path']).resolve().as_posix()]
    stage.SetDefaultPrim(stage.GetPrimAtPath('/World'));UsdGeom.SetStageMetersPerUnit(stage,1.);UsdGeom.SetStageUpAxis(stage,'Z')
    for state in parents:UsdGeom.Imageable(stage.OverridePrim(state['path'])).CreatePurposeAttr('proxy')
    stage.GetRootLayer().Save();stage=Usd.Stage.Open(str(target))
    after={str(p.GetPath()):mesh_state(stage,str(p.GetPath()))for p in stage.Traverse()if p.IsA(UsdGeom.Mesh)}
    if set(after)!=set(all_mesh_before):raise ValueError('Thin root overlay added or removed actual mesh prims')
    parent_paths={r['path']for r in parents}
    for path,before in all_mesh_before.items():
        expected=dict(before)
        if path in parent_paths:expected['purpose']='proxy'
        if after[path]!=expected:raise ValueError('Thin root overlay changed source geometry/API/material/pose/other purpose: '+path)
    properties=[]
    stage.GetRootLayer().Traverse('/',lambda p:properties.append(str(p))if p.IsPropertyPath()else None)
    if set(properties)!={p+'.purpose'for p in parent_paths}:raise ValueError('Fresh root layer wrote attributes beyond six OWN purpose opinions')
    profiles={p:contract.floor_profile(stage,p)for p in ('/World/Floor1','/World/Floor2')}
    for record in deps:verify(record)
    doc={'schema':'Factory80OwnDisabledDuplicatePurposeOverlayV1','status':'OWN_THIN_OVERLAY_AUTHORED',
        'original_authoring_ref':original_author_ref,'original_stage_ref':author['stage'],'derived_stage':ref(target),
        'allowed_delta':'Exactly6 AUTHORING-registered OWN disabled guide duplicate parents:purpose proxy; enabled guide children untouched',
        'actual_root_authored_property_paths':properties,'actual_parent_before':parents,'actual_parent_after':[after[r['path']]for r in parents],
        'actual_retained_child_states_unchanged':children,'actual_exact_triangle_reconstruction':proof,
        'original_frozen_floor_profiles':original_profiles,'actual_derived_original_frozen_floor_profiles':profiles,
        'original_frozen_contract_sources':pins,'source_dependencies_before':deps,'source_dependencies_after':[ref(r['path'])for r in deps],
        'original_A_source_native_USDZ_historical_AUTHORING_modified':False,'enabled_colliders_geometry_pose_API_material_changed':False,
        'actual_original_G25_Standability_StageD_RTX':'NOT EXECUTED','ordinary_remaining_floor_profile_failure':'Record and continue candidate schemes; never bypass original FAIL',
        'cannot_support':'Classification-only root opinion and original CPU collider contract readback do not prove real Factory80 candidate/RTX acceptance.'}
    overlay_ref=save(output/'ACTUAL_OWN_PURPOSE_OVERLAY_RECEIPT.json',doc)
    derived=copy.deepcopy(author);derived.update(schema='Factory80OwnDerivedDisabledDuplicatePurposeAuthoringV1',stage=ref(target),
        original_historical_authoring_ref=original_author_ref,original_source_stage_ref=author['stage'],
        derived_purpose_overlay_receipt_ref=overlay_ref,floor_collider_profiles=profiles)
    derived['source_immutability_refs']=[*author.get('source_immutability_refs',[]),*deps]
    derived_ref=save(output/'AUTHORING.DERIVED_PURPOSE_OVERLAY.json',derived)
    return doc,overlay_ref,derived_ref

def main():
    p=argparse.ArgumentParser(description=__doc__,allow_abbrev=False);p.add_argument('--authoring',type=Path,required=True)
    p.add_argument('--expected-authoring-sha',required=True);p.add_argument('--floor-contract-source',type=Path,required=True)
    p.add_argument('--door-policy-source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    doc,r,d=prepare(a.authoring,a.expected_authoring_sha,a.floor_contract_source,a.door_policy_source,a.output)
    print(json.dumps({'receipt':r,'derived_authoring':d,'actual_original_floor_profile_statuses':{k:v['status']for k,v in doc['actual_derived_original_frozen_floor_profiles'].items()}}));return 0

if __name__=='__main__':raise SystemExit(main())
