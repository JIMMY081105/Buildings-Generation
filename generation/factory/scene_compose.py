"""Author a v4 factory from retained architecture, placed assets and native stairs."""
from pathlib import Path
import argparse
import collections
import hashlib
import json
import math
import os
import resource
import sys
import time
import numpy as np
from shapely.geometry import Point, box, shape, mapping
from shapely.ops import unary_union
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, Vt

P = Path('/cephfs/shared/yixing/outputs/factory80_20261011/pipeline')
sys.path.insert(0, str(P))
from sample_floor_stairs import define_mesh
from isaac_capsule_query import digest
from scene_package import export_collision_package


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def file_path(value):
    if isinstance(value, dict):
        value = value.get('path') or value.get('server_path') or value.get('relative_path')
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError('Asset reference has no usable path')
    path = Path(value)
    if not path.is_absolute():
        path = P.parent / path
    return str(path.resolve())


def enabled(prim):
    return prim.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False


def count(prim):
    return sum(enabled(p) for p in Usd.PrimRange(prim, Usd.TraverseInstanceProxies()))


def retain_shell(source, output):
    """Select existing shell prims and material dependencies without remeshing."""
    source=Path(source);output=Path(output)
    probe=Usd.StagePopulationMask()
    probe.Add('/World/FactoryStructure');probe.Add('/World/Cut/Floor1Slab')
    probe.Add('/World/Floor1/Geometry')
    stage=Usd.Stage.OpenMasked(str(source),probe);assert stage
    prefixes=['/World/FactoryStructure','/World/Cut/Floor1Slab']
    geometry=stage.GetPrimAtPath('/World/Floor1/Geometry')
    assert geometry, 'SOURCE_PERIMETER_GEOMETRY_MISSING'
    walls=[str(p.GetPath()) for p in geometry.GetChildren() if p.GetName().startswith('wall_')]
    assert walls, 'SOURCE_PERIMETER_WALLS_MISSING'
    prefixes+=walls
    del stage
    for _ in range(16):
        mask=Usd.StagePopulationMask()
        for path in prefixes:mask.Add(path)
        stage=Usd.Stage.OpenMasked(str(source),mask)
        targets=set()
        for p in stage.Traverse():
            for rel in p.GetRelationships():
                if rel.GetName().startswith('material:binding'):
                    targets.update(str(t.GetPrimPath()) for t in rel.GetTargets())
            for attr in p.GetAttributes():
                targets.update(str(t.GetPrimPath()) for t in attr.GetConnections())
        missing=[path for path in targets if not any(path==pre or path.startswith(pre+'/') for pre in prefixes)]
        if not missing:break
        assert all(path not in ('/','/World') for path in missing)
        prefixes+=sorted(missing)
    else:raise RuntimeError('SHELL_MATERIAL_CONNECTIONS_DID_NOT_RESOLVE')
    assert stage.GetPrimAtPath('/World/FactoryStructure') and stage.GetPrimAtPath('/World/Cut/Floor1Slab')
    assert not stage.GetPrimAtPath('/World/Stair') and not stage.GetPrimAtPath('/World/Cut/Floor2Slab')
    output.parent.mkdir(parents=True,exist_ok=True)
    assert not output.exists()
    assert stage.Flatten().Export(str(output))
    return dict(source=str(source),retained_shell=str(output),wall_roots=walls,
                selected_prefixes=prefixes,geometry_generation='NOT_EXECUTED_EXISTING_PRIMS_ONLY')


def prepare_shells(plan_path):
    plan=json.loads(Path(plan_path).read_text());rows=plan['shell_sources']
    results=[]
    for row in rows:
        source=Path(row['source_root_for_structure'])
        existing=row.get('retained_architecture_exists',False)
        if existing:
            results.append(dict(building=row['building'],source=str(source),retained_shell=str(source),status='REUSED_EXISTING'))
            continue
        output=P.parent/'runs'/row['building']/'v4/retained_shell.usda'
        if output.exists():
            results.append(dict(building=row['building'],source=str(source),retained_shell=str(output),status='ALREADY_PREPARED'))
            continue
        result=retain_shell(source,output);result.update(building=row['building'],status='EXISTING_SHELL_SELECTED')
        write(output.with_suffix('.json'),result);results.append(result)
        print(json.dumps(result),flush=True)
    write(P/'v4/RETAINED_SHELLS.json',dict(buildings=results))


def slab_mesh(polygon, zlo, zhi, interval=2.):
    """Extend the existing orthogonal opening grid to any union of bay cells."""
    pieces = list(polygon.geoms) if polygon.geom_type == 'MultiPolygon' else [polygon]
    coords = [c for p in pieces for ring in [p.exterior, *p.interiors] for c in ring.coords]
    def axis(k):
        values = sorted(set(float(c[k]) for c in coords))
        return [a+(b-a)*i/math.ceil((b-a)/interval)
                for a,b in zip(values, values[1:])
                for i in range(math.ceil((b-a)/interval))]+[values[-1]]
    xs,ys=axis(0),axis(1)
    nx,ny=len(xs)-1,len(ys)-1
    occupied=np.asarray([[polygon.covers(Point((xs[i]+xs[i+1])/2,(ys[j]+ys[j+1])/2))
                          for j in range(ny)] for i in range(nx)])
    vertices=[]; ids={}; faces=[]
    def vertex(i,j,k):
        key=(i,j,k)
        if key not in ids:
            ids[key]=len(vertices);vertices.append([xs[i],ys[j],[zlo,zhi][k]])
        return ids[key]
    for i,j in zip(*np.nonzero(occupied)):
        a,b,c,d=(vertex(i,j,1),vertex(i+1,j,1),vertex(i+1,j+1,1),vertex(i,j+1,1))
        aa,bb,cc,dd=(vertex(i,j,0),vertex(i+1,j,0),vertex(i+1,j+1,0),vertex(i,j+1,0))
        faces += [[a,b,c,d],[aa,dd,cc,bb]]
        if j==0 or not occupied[i,j-1]:faces.append([aa,bb,b,a])
        if i==nx-1 or not occupied[i+1,j]:faces.append([bb,cc,c,b])
        if j==ny-1 or not occupied[i,j+1]:faces.append([cc,dd,d,c])
        if i==0 or not occupied[i-1,j]:faces.append([dd,aa,a,d])
    return np.asarray(vertices,dtype=np.float32),[4]*len(faces),np.asarray(faces,dtype=np.int32).reshape(-1).tolist()


def collision_export(stage, output):
    """Keep every enabled collider and its exact authored physical geometry."""
    while True:
        instances=[p for p in stage.Traverse() if p.IsInstance()]
        if not instances:break
        for p in instances:p.SetInstanceable(False)
    physical={str(p.GetPath()) for p in stage.Traverse() if enabled(p)}
    keep=set()
    for p in stage.Traverse():
        if str(p.GetPath()) in physical or p.IsA(UsdPhysics.Scene) or p.HasAPI(UsdPhysics.MaterialAPI):
            while p and not p.IsPseudoRoot():
                keep.add(str(p.GetPath()));p=p.GetParent()
    collision=Usd.Stage.Open(stage.Flatten())
    for p in sorted(list(collision.Traverse()),key=lambda p:len(str(p.GetPath())),reverse=True):
        path=str(p.GetPath())
        if path not in keep:
            collision.RemovePrim(p.GetPath());continue
        if path not in physical and p.IsA(UsdGeom.Gprim):
            p.SetTypeName('Xform')
            for name in ['points','faceVertexCounts','faceVertexIndices','holeIndices','subdivisionScheme','orientation','doubleSided','axis','radius','height','size','extent','normals','primvars:displayColor','primvars:displayOpacity']:
                p.RemoveProperty(name)
        for rel in list(p.GetRelationships()):
            if rel.GetName().startswith('material:binding') and any(str(t.GetPrimPath()) not in keep for t in rel.GetTargets()):
                p.RemoveProperty(rel.GetName())
    assert collision.GetRootLayer().Export(str(output))
    bounds=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','proxy','guide','render'],useExtentsHint=False).ComputeWorldBound(collision.GetPseudoRoot()).ComputeAlignedRange()
    return len(physical),list(map(float,bounds.GetMin())),list(map(float,bounds.GetMax()))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--layout',type=Path)
    ap.add_argument('--stairs',type=Path)
    ap.add_argument('--shell',type=Path)
    ap.add_argument('--out',type=Path)
    ap.add_argument('--lighting',type=Path)
    ap.add_argument('--prepare-shells',type=Path)
    args=ap.parse_args()
    Path('/proc/self/oom_score_adj').write_text('1000')
    if args.prepare_shells:
        prepare_shells(args.prepare_shells);return
    assert args.layout and args.stairs and args.shell and args.out
    started=time.monotonic()
    layout=json.loads(args.layout.read_text()); stairs=json.loads(args.stairs.read_text())
    for record in (layout, stairs):
        assert record.get('layout_only') is not True, 'LAYOUT_ONLY_INPUT_HAS_NO_PHYSICS_CANDIDATE'
        assert record.get('collision_qualification') not in (
            'PENDING_USER_EXCEPTION_NOT_READY', 'FOR_LAYOUT_ONLY_NO_PHYSICS_CANDIDATE'), 'STAIR_COLLISION_REPRESENTATION_NOT_APPROVED'
    for row in stairs['stairs']:
        assert row.get('layout_only') is not True, 'LAYOUT_ONLY_STAIR_HAS_NO_PHYSICS_CANDIDATE'
        assert row.get('collision_qualification') not in (
            'PENDING_USER_EXCEPTION_NOT_READY', 'FOR_LAYOUT_ONLY_NO_PHYSICS_CANDIDATE'), 'STAIR_COLLISION_REPRESENTATION_NOT_APPROVED'
    args.out.mkdir(parents=True,exist_ok=True)
    root=args.out/'root.usda';assert not root.exists()
    stage=Usd.Stage.CreateNew(str(root));UsdGeom.SetStageMetersPerUnit(stage,1.)
    UsdGeom.SetStageUpAxis(stage,'Z');stage.GetRootLayer().subLayerPaths=[str(args.shell.resolve())]
    stage.SetDefaultPrim(stage.GetPrimAtPath('/World'))
    domain_path=Path(file_path(layout['spatial_domains'])) if 'spatial_domains' in layout else args.layout.parent/'SPATIAL_DOMAINS.json'
    domains=json.loads(domain_path.read_text())
    upper=shape(domains['floors']['Floor2']['support_geometry'])
    vertices,counts,indices=slab_mesh(upper,domains['upper_slab_bottom_z_m'],4.)
    define_mesh(stage,'/World/Cut/Floor2Slab/Visual',vertices,counts,indices,False)
    define_mesh(stage,'/World/Cut/Floor2Slab/Collision',vertices,counts,indices,True)
    inventory=dict(instances=[],slabs=[],stairs=[])
    collision_sources=[dict(instance_root_path='/World',composed_proxy_root_path='/World',proxy_path=str(args.shell.resolve()),
                           proxy_root_path='/World',world_transform=np.eye(4).tolist())]
    slab_file=args.out/'slab_collision.usdc'
    slab_stage=Usd.Stage.CreateNew(str(slab_file))
    UsdGeom.SetStageMetersPerUnit(slab_stage,1.);UsdGeom.SetStageUpAxis(slab_stage,'Z')
    UsdGeom.Xform.Define(slab_stage,'/World')
    define_mesh(slab_stage,'/World/Cut/Floor2Slab/Collision',vertices,counts,indices,True)
    slab_stage.GetRootLayer().Save()
    collision_sources.append(dict(instance_root_path='/World/Cut/Floor2Slab',
        composed_proxy_root_path='/World/Cut/Floor2Slab',
        proxy_path=str(slab_file),proxy_root_path='/World/Cut/Floor2Slab',world_transform=np.eye(4).tolist()))
    for path in ['/World/Cut/Floor1Slab','/World/Cut/Floor2Slab']:
        prim=stage.GetPrimAtPath(path);assert prim
        expected=1 if path.endswith('Floor2Slab') else count(prim)
        inventory['slabs'].append(dict(prim_path=path,expected_colliders=expected))
    sources={}
    def source(path):
        expected_sha=path.get('sha256') if isinstance(path,dict) else None
        path=file_path(path)
        if expected_sha:
            assert digest(path)==expected_sha, 'BOUND_COLLISION_OR_ASSET_SOURCE_CHANGED:'+path
        if path not in sources:
            st=Usd.Stage.Open(path);assert st and st.GetDefaultPrim()
            sources[path]=(str(st.GetDefaultPrim().GetPath()),count(st.GetDefaultPrim()))
        return sources[path]
    lighting=json.loads(args.lighting.read_text()) if args.lighting else {}
    rows=list(layout.get('actual_instances',layout.get('instances',[])))+lighting.get('instances',[])
    assert rows
    for index,row in enumerate(rows):
        path=row.get('prim_path','/World/'+row['floor']+'/EquipmentV4/I%05d'%index)
        asset=file_path(row['source_root']); default,source_expected=source(row['source_root'])
        proxy=row.get('collision_proxy',row.get('proxy',row['source_root']))
        proxy_default_detected,proxy_expected=source(proxy)
        expected=row.get('expected_colliders',row.get('enabled_static_colliders',proxy_expected))
        wrapper=UsdGeom.Xform.Define(stage,path);wrapper.SetResetXformStack(True)
        wrapper.AddTranslateOp().Set(Gf.Vec3d(*row['translation_m']))
        wrapper.AddRotateXYZOp().Set(Gf.Vec3f(*row.get('rotation_xyz_deg',[0.,0.,row.get('yaw_deg',0.)])))
        assert row.get('artistic_scale',1.)==1.
        child=stage.DefinePrim(path+'/SourceAsset');child.GetReferences().AddReference(asset,default)
        for key,value in [('assetId',row['asset_id']),('class',row['asset_class']),('recipeRole',row['role'])]:
            wrapper.GetPrim().CreateAttribute('factory80:'+key,Sdf.ValueTypeNames.String).Set(value)
        for prim in Usd.PrimRange(wrapper.GetPrim(),Usd.TraverseInstanceProxies()):
            if prim.HasAPI(UsdLux.LightAPI):
                assert not prim.IsInstanceProxy(), 'IMPORTED_LIGHT_REQUIRES_LOCAL_INSTANCE_EDIT'
                UsdLux.LightAPI(prim).CreateIntensityAttr(0.)
        inventory['instances'].append(dict(prim_path=path,expected_colliders=expected,
            asset_proxy_sha256=row.get('asset_proxy_sha256') or digest(file_path(proxy)),asset_id=row['asset_id'],asset_class=row['asset_class']))
        proxy_default=row.get('proxy_root_path')
        if isinstance(proxy,dict):proxy_default=proxy.get('default_prim',proxy_default)
        collision_sources.append(dict(instance_root_path=path,proxy_path=file_path(proxy),
            composed_proxy_root_path=str(child.GetPath()),
            proxy_root_path=proxy_default or proxy_default_detected,
            asset_class=row['asset_class'],
            world_transform=np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(child)).tolist()))
    for row in stairs['stairs']:
        path=row.get('prim_path','/World/Stair/'+row['id'])
        asset=file_path(row['asset_stage']);default,visual_colliders=source(row['asset_stage'])
        stair_proxy=row.get('collision_proxy',row['asset_stage'])
        proxy_default,expected=source(stair_proxy)
        wrapper=UsdGeom.Xform.Define(stage,path);wrapper.SetResetXformStack(True)
        wrapper.AddTranslateOp().Set(Gf.Vec3d(*row['offset_m']))
        wrapper.AddRotateZOp().Set(float(row['rotation_deg']))
        native=stage.DefinePrim(path+'/Native');native.GetReferences().AddReference(asset,default)
        physical_reference=native
        if visual_colliders==0:
            physical_reference=stage.DefinePrim(path+'/CollisionSource')
            physical_reference.GetReferences().AddReference(file_path(stair_proxy),proxy_default)
        inventory['stairs'].append(dict(prim_path=path,expected_colliders=expected,
                                       stair_id=row['id']))
        collision_sources.append(dict(instance_root_path=path,proxy_path=file_path(stair_proxy),proxy_root_path=proxy_default,
            composed_proxy_root_path=str(physical_reference.GetPath()),
            stair_id=row['id'],category='stair',
            world_transform=np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(physical_reference)).tolist()))
    for row in layout.get('retained_components',[])+lighting.get('retained_components',[]):
        stage.DefinePrim(row['prim_path']).GetReferences().AddReference(file_path(row['source_stage']),row['source_prim'])
        if count(stage.GetPrimAtPath(row['prim_path'])):
            collision_sources.append(dict(instance_root_path=row['prim_path'],proxy_path=file_path(row['source_stage']),
                composed_proxy_root_path=str(stage.GetPrimAtPath(row['prim_path']).GetPath()),
                proxy_root_path=row['source_prim'],world_transform=np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(row['prim_path']))).tolist()))
    stage.GetRootLayer().Save()
    for group in inventory.values():
        for row in group:
            row['enabled_colliders']=count(stage.GetPrimAtPath(row['prim_path']))
    inventory['actual_enabled_colliders']=sum(enabled(p) for p in stage.Traverse(Usd.TraverseInstanceProxies()))
    inventory['root']=str(root.resolve())
    inventory['collision_sources']=collision_sources
    write(args.out/'COLLISION_MANIFEST.json',inventory)
    package=args.out/'collision_package';package.mkdir(exist_ok=False)
    exported=export_collision_package(inventory,package/'collision_only.usdc',cache_dir=P/'v4/package_asset_cache')
    physical_count=exported['actual_enabled_colliders']
    lo,hi=exported['bounds_min_m'],exported['bounds_max_m']
    assert physical_count==inventory['actual_enabled_colliders'], 'COLLISION_PACKAGE_COUNT_DIFFERS_FROM_ACTUAL_ROOT'
    repo=Path('/cephfs/shared/yixing/code/Scenesmith-code-study/ProtoMotions-main')
    os.environ.setdefault('MPLBACKEND','Agg')
    os.environ.setdefault('MPLCONFIGDIR',str(P/'v4/deps_cache/matplotlib'))
    sys.path.insert(0,str(P/'v4/deps'))
    sys.path.insert(0,str(repo))
    from protomotions.components.scene_lib import SceneLib,Scene,MeshSceneObject,ObjectOptions
    obj=MeshSceneObject(object_path=str(package/'collision_only.usdc'),translation=(0.,0.,0.),
        rotation=(0.,0.,0.,1.),object_dims=(lo[0],hi[0],lo[1],hi[1],lo[2],hi[2]),
        scale=(1.,1.,1.),options=ObjectOptions(fix_base_link=True,vhacd_enabled=False))
    SceneLib.save_scenes_to_file([Scene(objects=[obj])],str(package/'scenes.pt'),asset_root=str(package.parent))
    write(package/'SCENELIB_CONFIG.json',dict(_target_='protomotions.components.scene_lib.SceneLib',_convert_='object',
        config=dict(_target_='protomotions.components.scene_lib.SceneLibConfig',
                    scene_file='collision_package/scenes.pt',num_objects_per_env=1,pointcloud_samples_per_object=None,
                    mesh_collision_approximation=None),num_envs=1,device='cpu',terrain=None))
    write(package/'PACKAGE.json',dict(building=layout['building'],collision_file='collision_only.usdc',
        scene_file='scenes.pt',enabled_colliders=physical_count,coordinate_frame='Original world metres, Z-up, scale1',
        checks='NOT_EXECUTED',training='NOT_EXECUTED',formal_acceptance='NOT_EXECUTED'))
    (package/'README.txt').write_text('''Use the installed ProtoMotions SceneLib with an absolute scene_file path.
The saved mesh path includes collision_package/ and matches the native loader
default asset_root=parent(parent(scene_file)). Preserve the collision_package
directory name when moving the package. SCENELIB_CONFIG.json uses nested Hydra
targets so SceneLibConfig supplies the installed runtime defaults. Its relative
scene_file is resolved from the directory containing collision_package/.

Equivalent direct Python loading from any working directory:

    import json
    from pathlib import Path
    from protomotions.components.scene_lib import SceneLib, SceneLibConfig
    package_dir = Path('/path/to/collision_package').resolve()
    spec = json.loads((package_dir / 'SCENELIB_CONFIG.json').read_text())
    values = dict(spec['config'])
    values.pop('_target_')
    values['scene_file'] = str(package_dir / 'scenes.pt')
    library = SceneLib(SceneLibConfig(**values), num_envs=1, device='cpu', terrain=None)

No training or formal acceptance has been performed.
''')
    write(args.out/'ASSEMBLY.json',dict(tool='scene@v3',building=layout['building'],root=str(root),
          collision_package=str(package),elapsed_s=time.monotonic()-started,
          peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
          enabled_colliders=inventory['actual_enabled_colliders'],checks='NOT_EXECUTED',photos='NOT_EXECUTED'))


if __name__=='__main__':main()
