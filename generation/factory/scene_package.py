"""Portable Factory80 collision USD assembled from unique source proxies.

Only each unique proxy/root is masked, composed, and flattened. The whole visual
scene is never opened. References share one cached geometry file per proxy/root;
native USD instanceability stays false until separately demonstrated in PhysX.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import shutil
import time

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils

VERSION = "factory80_collision_package@v1.2"
CACHE_FORMAT_VERSION = "factory80_collision_package@v1.1"
HASH_CACHE = {}


def sha(path):
    path=Path(path);st=path.stat();key=(str(path.resolve()),st.st_size,st.st_mtime_ns)
    if key not in HASH_CACHE:
        h=hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda:stream.read(8*1024**2),b""):h.update(block)
        HASH_CACHE[key]=h.hexdigest()
    return HASH_CACHE[key]


def write(path,value):
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+"\n",encoding="utf-8")


def enabled(prim):
    return prim.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False


def physical_schema(prim):
    return prim.GetTypeName().startswith(("Physics","Physx")) or any(str(s).startswith(("Physics","Physx")) for s in prim.GetAppliedSchemas())


def matrix(value):
    a=np.asarray(value,dtype=np.float64)
    if a.shape!=(4,4) or not np.isfinite(a).all():raise ValueError("Invalid finite4x4 source transform")
    if abs(np.linalg.det(a))<1e-14:raise ValueError("Singular source transform cannot be normalized")
    return Gf.Matrix4d(*a.reshape(-1).tolist())


def _descendants(root):
    return list(Usd.PrimRange(root,Usd.TraverseInstanceProxies()))


def _masked_source(path,root_path):
    mask=Usd.StagePopulationMask();mask.Add(root_path)
    stage=Usd.Stage.OpenMasked(str(path),mask)
    root=stage.GetPrimAtPath(root_path)
    if not root or not root.IsActive():raise ValueError("Source collision root missing/inactive: "+root_path)
    # Material bindings outside the selected subtree must be examined before a
    # reference arc would silently discard their out-of-scope targets.
    targets=set()
    for prim in _descendants(root):
        for rel in prim.GetRelationships():
            if rel.GetName().startswith(("physics:","physx","material:binding")):
                targets.update(t.GetPrimPath() for t in rel.GetTargets() if t.IsAbsolutePath() and not t.GetPrimPath().HasPrefix(root_path))
    if targets:
        for target in targets:mask.Add(target)
        stage.SetPopulationMask(mask)
    return stage


def _relative_path(path,root):
    return str(path.MakeRelativePath(root))


def _copy_resource(value,source_directory,asset_directory):
    if not value.path:return value
    resolved=Path(value.resolvedPath or value.path)
    if not resolved.is_absolute():resolved=source_directory/resolved
    if not resolved.is_file():raise FileNotFoundError("Referenced collision resource missing: "+str(resolved))
    resources=asset_directory/"resources";resources.mkdir(exist_ok=True)
    dest=resources/(sha(resolved)+resolved.suffix.lower())
    if not dest.exists():shutil.copyfile(resolved,dest)
    return Sdf.AssetPath("resources/"+dest.name)


def _root_bounds(stage,root):
    """Physical root-frame bounds; exact point bounds for mesh colliders."""
    xc=UsdGeom.XformCache();inverse=xc.GetLocalToWorldTransform(root).GetInverse()
    bbox=UsdGeom.BBoxCache(Usd.TimeCode.Default(),["default","render","proxy","guide"],useExtentsHint=False)
    lo=np.full(3,np.inf);hi=np.full(3,-np.inf);count=0;faces=0;vertices=0
    for prim in _descendants(root):
        if not enabled(prim):continue
        count+=1
        if prim.IsA(UsdGeom.Mesh):
            mesh=UsdGeom.Mesh(prim);points=np.asarray(mesh.GetPointsAttr().Get(),dtype=np.float64)
            if not len(points):raise ValueError("Enabled collision mesh has no vertices: "+str(prim.GetPath()))
            transform=np.asarray(xc.GetLocalToWorldTransform(prim)*inverse)
            points=points@transform[:3,:3]+transform[3,:3]
            lo=np.minimum(lo,points.min(0));hi=np.maximum(hi,points.max(0));vertices+=len(points)
            counts=mesh.GetFaceVertexCountsAttr().Get() or []
            faces+=sum(max(0,int(n)-2) for n in counts)
        elif prim.IsA(UsdGeom.Boundable):
            bound=bbox.ComputeRelativeBound(prim,root).ComputeAlignedRange()
            lo=np.minimum(lo,list(bound.GetMin()));hi=np.maximum(hi,list(bound.GetMax()))
        else:
            raise TypeError("Enabled collider is not a supported boundable prim: "+str(prim.GetPath()))
    if not count:return dict(enabled_colliders=0,root_frame_bounds_m=None,triangles=0,vertices=0)
    return dict(enabled_colliders=count,root_frame_bounds_m=[lo.tolist(),hi.tolist()],triangles=faces,vertices=vertices)


def _cache_key(proxy_sha,source_root):
    return hashlib.sha256((proxy_sha+"\n"+str(source_root)+"\n"+CACHE_FORMAT_VERSION).encode()).hexdigest()


def _load_cached_asset(proxy_path,source_root,assets_directory):
    proxy_sha=sha(proxy_path);key=_cache_key(proxy_sha,source_root);assets_directory=Path(assets_directory)
    target=assets_directory/(key+".usdc");meta_path=assets_directory/(key+".json")
    if not target.exists() and not meta_path.exists():return None
    if not target.exists() or not meta_path.exists():raise FileExistsError("Incomplete existing collision cache entry: "+key)
    meta=json.loads(meta_path.read_text())
    if meta["version"]!=CACHE_FORMAT_VERSION or meta["proxy_sha256"]!=proxy_sha or meta["source_root"]!=str(source_root):raise ValueError("Cached collision source identity mismatch")
    for dep in meta["source_layers"]:
        if sha(dep["path"])!=dep["sha256"]:raise ValueError("Cached collision dependency changed; use a fresh cache directory")
    if sha(target)!=meta["output_sha256"]:raise ValueError("Cached collision asset bytes changed")
    return meta


def _materialize_cached_asset(meta,cache_directory,assets_directory):
    """Copy the actual USD dependency closure into a self-contained package."""
    source=Path(cache_directory).resolve();destination=Path(assets_directory).resolve();destination.mkdir(parents=True,exist_ok=True)
    asset=source/meta["cached_file"]
    layers,resources,missing=UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(asset)))
    if missing:raise ValueError("Cached collision dependency missing: "+str(missing))
    paths={Path(layer.realPath).resolve() for layer in layers if not layer.anonymous}
    paths.update(Path(str(path)).resolve() for path in resources)
    paths.add(source/(meta["key"]+".json"))
    copied=0
    for path in sorted(paths):
        if not path.is_relative_to(source):raise ValueError("Cached collision dependency escapes cache directory: "+str(path))
        target=destination/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            if sha(target)!=sha(path):raise ValueError("Existing package asset differs from shared cache: "+str(target))
        else:
            shutil.copyfile(path,target);copied+=target.stat().st_size
            if sha(target)!=sha(path):raise ValueError("Materialized collision bytes differ")
    return copied


def build_cached_asset(proxy_path,source_root,assets_directory):
    """Copy one unique composed collision proxy, retaining source point arrays.

    Descendant resets are normalized only on this derived copy. For each reset
    node n its new local matrix is originalWorld(n)*originalWorld(parent)^-1.
    Therefore clearing the reset preserves its source-space world geometry.
    """
    proxy_path=Path(proxy_path).resolve();assets_directory=Path(assets_directory)
    assets_directory.mkdir(parents=True,exist_ok=True)
    source_root=Sdf.Path(source_root)
    proxy_sha=sha(proxy_path)
    key=_cache_key(proxy_sha,source_root)
    target=assets_directory/(key+".usdc");meta_path=assets_directory/(key+".json")
    existing=_load_cached_asset(proxy_path,source_root,assets_directory)
    if existing:return existing
    started=time.monotonic()
    source=_masked_source(proxy_path,source_root);original=source.GetPrimAtPath(source_root)
    root_xf=UsdGeom.Xformable(original)
    local=root_xf.GetLocalTransformation() if root_xf else Gf.Matrix4d(1)
    matrix(local)
    before=_root_bounds(source,original)
    source_layers=[dict(path=l.realPath,sha256=sha(l.realPath)) for l in source.GetUsedLayers() if not l.anonymous]
    external_materials={}
    for prim in _descendants(original):
        for rel in prim.GetRelationships():
            name=rel.GetName()
            if not name.startswith(("physics:","physx","material:binding")):continue
            for target_path in rel.GetTargets():
                target_prim=target_path.GetPrimPath()
                if target_prim.HasPrefix(source_root):continue
                dep=source.GetPrimAtPath(target_prim)
                if name.startswith("material:binding") and dep and dep.HasAPI(UsdPhysics.MaterialAPI):
                    external_materials[str(target_prim)]=str(source_root)+"/__PackagePhysics/Material_"+hashlib.sha256(str(target_prim).encode()).hexdigest()[:16]
                elif name.startswith(("physics:","physx")):
                    raise ValueError("Out-of-root nonmaterial physics relationship requires explicit source closure: "+str(prim.GetPath())+"."+name+" -> "+str(target_path))
    # Flatten only this masked, unique asset. Never flatten the visual factory.
    layer=source.Flatten();cached=Usd.Stage.Open(layer)
    # Editing instance proxies is forbidden by USD. Deinstance inside the one
    # unique asset only, preserving every composed piece, point and collider.
    while True:
        instances=[p for p in cached.Traverse() if p.IsInstance()]
        if not instances:break
        for prim in instances:prim.SetInstanceable(False)
    xc=UsdGeom.XformCache();reset_records=[]
    for prim in _descendants(cached.GetPrimAtPath(source_root)):
        xf=UsdGeom.Xformable(prim)
        if not xf or prim.GetPath()==source_root or not xf.GetResetXformStack():continue
        chain=prim
        while chain and not chain.IsPseudoRoot():
            x=UsdGeom.Xformable(chain)
            if x and x.GetTimeSamples():raise ValueError("Animated reset hierarchy is outside the static package contract: "+str(prim.GetPath()))
            chain=chain.GetParent()
        parent=prim.GetParent();world=xc.GetLocalToWorldTransform(prim);parent_world=xc.GetLocalToWorldTransform(parent)
        normalized=world*parent_world.GetInverse()
        reset_records.append((prim.GetPath(),normalized))
    for path,normalized in reset_records:
        xf=UsdGeom.Xformable(cached.GetPrimAtPath(path));xf.MakeMatrixXform().Set(normalized);xf.SetResetXformStack(False)
    # Relocate physics materials into the referenced subtree, then remap their
    # derived relationships. Otherwise AddReference(file,root) drops the target.
    for old,new in external_materials.items():
        UsdGeom.Scope.Define(cached,str(source_root)+"/__PackagePhysics")
        if not Sdf.CopySpec(layer,Sdf.Path(old),layer,Sdf.Path(new)):raise RuntimeError("Physics material copy failed")
    collision_paths={str(p.GetPath()) for p in _descendants(cached.GetPrimAtPath(source_root)) if p.HasAPI(UsdPhysics.CollisionAPI)}
    keep=set()
    for prim in list(cached.Traverse()):
        path=str(prim.GetPath())
        if path in collision_paths or (prim.GetPath().HasPrefix(source_root) and physical_schema(prim)):
            while prim and not prim.IsPseudoRoot():keep.add(str(prim.GetPath()));prim=prim.GetParent()
    for prim in sorted(list(cached.Traverse()),key=lambda p:len(str(p.GetPath())),reverse=True):
        path=str(prim.GetPath())
        if path not in keep:
            cached.RemovePrim(prim.GetPath());continue
        if path not in collision_paths and prim.IsA(UsdGeom.Gprim):
            prim.SetTypeName("Xform")
            for attr in list(prim.GetAttributes()):
                name=attr.GetName()
                if name not in ("xformOpOrder","visibility","purpose") and not name.startswith(("xformOp:","physics:","physx")):
                    prim.RemoveProperty(name)
        for rel in list(prim.GetRelationships()):
            targets=[];remove=False
            for target_path in rel.GetTargets():
                target_prim=str(target_path.GetPrimPath());new=target_path
                for old,dest in external_materials.items():
                    if target_path.GetPrimPath().HasPrefix(old):new=target_path.ReplacePrefix(old,dest);break
                if rel.GetName().startswith("material:binding") and str(new.GetPrimPath()) not in keep:remove=True
                targets.append(new)
            if remove:prim.RemoveProperty(rel.GetName())
            elif targets!=list(rel.GetTargets()):rel.SetTargets(targets)
        for attr in list(prim.GetAttributes()):
            name=attr.GetName()
            if prim.HasAPI(UsdPhysics.MaterialAPI) and name.startswith(("outputs:","inputs:")):
                prim.RemoveProperty(name);continue
            if attr.GetTypeName() not in (Sdf.ValueTypeNames.Asset,Sdf.ValueTypeNames.AssetArray):continue
            samples=[Usd.TimeCode.Default(),*[Usd.TimeCode(t) for t in attr.GetTimeSamples()]]
            for sample in samples:
                value=attr.Get(sample)
                if value is None:continue
                if isinstance(value,Sdf.AssetPath):value=_copy_resource(value,proxy_path.parent,assets_directory)
                else:value=[_copy_resource(v,proxy_path.parent,assets_directory) for v in value]
                attr.Set(value,sample)
    cached_root=cached.GetPrimAtPath(source_root)
    if not cached_root:raise ValueError("Collision root contains no retained physical data")
    after=_root_bounds(cached,cached_root)
    if after["enabled_colliders"]!=before["enabled_colliders"] or after["triangles"]!=before["triangles"] or after["vertices"]!=before["vertices"]:
        raise ValueError("Unique-asset collider/topology totals changed during pure export")
    layer.Export(str(target))
    meta=dict(version=CACHE_FORMAT_VERSION,key=key,proxy_path=str(proxy_path),proxy_sha256=proxy_sha,source_root=str(source_root),
        source_root_local_matrix=np.asarray(local).tolist(),source_root_resets=bool(root_xf and root_xf.GetResetXformStack()),
        source_layers=source_layers,normalized_descendant_reset_paths=[str(p) for p,m in reset_records],
        relocated_physics_materials=external_materials,cached_file=target.name,output_sha256=sha(target),bytes=target.stat().st_size,
        elapsed_s=time.monotonic()-started,**before)
    write(meta_path,meta)
    del cached,source,layer;gc.collect()
    return meta


def _portable_check(output):
    output=Path(output).resolve();root=output.parent
    layers,assets,missing=UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(output)))
    if missing:raise ValueError("Package has missing dependencies: "+str(missing))
    for layer in layers:
        if layer.anonymous:continue
        path=Path(layer.realPath).resolve()
        if not path.is_relative_to(root):raise ValueError("Package layer escapes its directory: "+str(path))
        for reference in layer.GetExternalReferences():
            if Path(reference).is_absolute():raise ValueError("Absolute package composition reference: "+reference)
    for asset in assets:
        path=Path(str(asset)).resolve()
        if not path.is_relative_to(root):raise ValueError("Package asset escapes its directory: "+str(path))
    return dict(layer_count=len(layers),asset_count=len(assets),missing_dependencies=[])


def export_collision_package(manifest,output,cache_dir=None):
    """Build a portable collision file from collision_sources; return statistics.

    M is actual source-child world matrix. A reference retains source-root local
    R but drops source ancestors, so the reset wrapper is W=R^-1*M(row vectors).
    """
    started=time.monotonic()
    if isinstance(manifest,(str,Path)):
        manifest=json.loads(Path(manifest).read_text())
    output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise FileExistsError(output)
    assets=output.parent/"assets";assets.mkdir(exist_ok=True)
    stage=Usd.Stage.CreateNew(str(output));UsdGeom.SetStageMetersPerUnit(stage,1.);UsdGeom.SetStageUpAxis(stage,"Z")
    default=UsdGeom.Xform.Define(stage,"/World");stage.SetDefaultPrim(default.GetPrim())
    cache={};sources=[];counts=0;naive_bytes=0;used={};wrapper_matrices={}
    cache_stats=dict(builder_calls=0,shared_cache_hits=0,shared_cache_misses=0,copied_bytes=0)
    bounds_lo=np.full(3,np.inf);bounds_hi=np.full(3,-np.inf)
    for index,row in enumerate(manifest["collision_sources"]):
        proxy_path=row["proxy_path"];root_path=row["proxy_root_path"]
        if isinstance(proxy_path,dict):proxy_path=proxy_path["path"]
        key=(sha(proxy_path),root_path)
        if key not in cache:
            directory=Path(cache_dir).resolve() if cache_dir is not None else assets
            meta=_load_cached_asset(proxy_path,root_path,directory)
            if meta is None:
                cache_stats["builder_calls"]+=1
                if cache_dir is not None:cache_stats["shared_cache_misses"]+=1
                meta=build_cached_asset(proxy_path,root_path,directory)
            elif cache_dir is not None:cache_stats["shared_cache_hits"]+=1
            if cache_dir is not None:cache_stats["copied_bytes"]+=_materialize_cached_asset(meta,directory,assets)
            cache[key]=meta
        meta=cache[key];native=matrix(meta["source_root_local_matrix"]);world=matrix(row["world_transform"])
        wrapper_matrix=native.GetInverse()*world
        path=row["instance_root_path"]
        if path in wrapper_matrices and not np.allclose(wrapper_matrices[path],np.asarray(wrapper_matrix),rtol=0,atol=1e-10):
            raise ValueError("Multiple collision sources at one actor require the same wrapper frame: "+path)
        wrapper_matrices[path]=np.asarray(wrapper_matrix)
        wrapper=UsdGeom.Xform.Define(stage,path);wrapper.MakeMatrixXform().Set(wrapper_matrix);wrapper.SetResetXformStack(True)
        number=used.get(path,0);used[path]=number+1
        child_path=path+("/CollisionSource" if number==0 else f"/CollisionSource_{number:04d}")
        child=stage.DefinePrim(child_path)
        child.GetReferences().AddReference("assets/"+meta["cached_file"],root_path)
        child.SetInstanceable(False)
        xf=UsdGeom.Xformable(child)
        if xf and xf.GetResetXformStack():xf.SetResetXformStack(False)
        current=np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(child))
        if not np.allclose(current,np.asarray(world),rtol=0,atol=1e-10):raise ValueError("Referenced root world pose differs from manifest: "+child_path)
        counts+=meta["enabled_colliders"];naive_bytes+=meta["bytes"]
        if meta["root_frame_bounds_m"]:
            lo,hi=np.asarray(meta["root_frame_bounds_m"])
            corners=np.array([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
            m=np.asarray(world);corners=corners@m[:3,:3]+m[3,:3]
            bounds_lo=np.minimum(bounds_lo,corners.min(0));bounds_hi=np.maximum(bounds_hi,corners.max(0))
        sources.append(dict(instance_root_path=path,export_root_path=child_path,source_root=root_path,asset_file="assets/"+meta["cached_file"],
            proxy_sha256=meta["proxy_sha256"],enabled_colliders=meta["enabled_colliders"],world_transform=np.asarray(world).tolist(),wrapper_transform=np.asarray(wrapper_matrix).tolist()))
    if not counts:raise ValueError("Collision package has no enabled colliders")
    stage.GetRootLayer().Save()
    actual=sum(enabled(p) for p in stage.Traverse(Usd.TraverseInstanceProxies()))
    if actual!=counts:raise ValueError(f"Composed collider count mismatch: {actual} != {counts}")
    portability=_portable_check(output)
    result=dict(version=VERSION,cache_format_version=CACHE_FORMAT_VERSION,shared_cache_directory=str(Path(cache_dir).resolve()) if cache_dir is not None else None,cache_stats=cache_stats,root=str(output),root_sha256=sha(output),actual_enabled_colliders=counts,
        bounds_min_m=bounds_lo.tolist(),bounds_max_m=bounds_hi.tolist(),bounds_mode="union of transformed physical root-frame point AABBs; conservative for non-axis-aligned rotations",
        native_instanceable=False,source_count=len(sources),unique_assets=len(cache),collision_sources=sources,
        unique_asset_bytes=sum(v["bytes"] for v in cache.values()),naive_repeated_asset_bytes=naive_bytes,
        total_package_bytes=sum(p.stat().st_size for p in output.parent.rglob("*") if p.is_file()),
        assets=[v for v in cache.values()],portability=portability,elapsed_s=time.monotonic()-started,
        peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        qualification="CPU_AUTHORING_ONLY_C1_C5_NOT_EXECUTED")
    write(output.with_name(output.stem+"_PACKAGE.json"),result)
    return result


def _prebuild_init():
    import multiprocessing
    Path('/proc/self/oom_score_adj').write_text('1000')
    available=sorted(os.sched_getaffinity(0))[:16];index=(multiprocessing.current_process()._identity[0]-1)%4
    os.sched_setaffinity(0,available[index*4:(index+1)*4] or available[:4])
    from pxr import Work
    Work.SetConcurrencyLimit(4)


def _prebuild_one(payload):
    proxy,root,expected,cache_dir=payload
    if sha(proxy)!=expected:raise ValueError('Prebuild source changed from actual inventory: '+proxy)
    cached=_load_cached_asset(proxy,root,cache_dir)
    meta=cached if cached is not None else build_cached_asset(proxy,root,cache_dir)
    return dict(key=meta['key'],proxy_path=proxy,source_root=root,proxy_sha256=expected,cache_hit=cached is not None,
        cached_file=meta['cached_file'],output_sha256=meta['output_sha256'],bytes=meta['bytes'],enabled_colliders=meta['enabled_colliders'],
        elapsed_s=meta['elapsed_s'],peak_worker_process_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024)


def prebuild_assets(inventory_path,cache_dir,workers=4):
    """Cache unchanged inventoried source proxies, never build new geometry."""
    from concurrent.futures import ProcessPoolExecutor,as_completed
    started=time.monotonic();data=json.loads(Path(inventory_path).read_text(encoding='utf-8-sig'));unique={}
    for row in data['sources']:
        kinds=set(row.get('kinds',['retained_shell'] if 'proxy_root_path' in row else []))
        if not kinds or not kinds.issubset({'equipment','LED','retained_shell'}):raise ValueError('Only unchanged existing equipment/LED/retained-shell source proxies are prebuilt')
        proxy=Path(row['proxy_path']).resolve();st=proxy.stat();expected=row['actual_sha256']
        if st.st_size==row.get('proxy_file_size_at_stat',row.get('proxy_file_size')) and st.st_mtime_ns==row.get('proxy_file_mtime_ns'):
            HASH_CACHE[(str(proxy),st.st_size,st.st_mtime_ns)]=expected
        if sha(proxy)!=expected:raise ValueError('Actual source inventory no longer matches')
        root=row.get('resolved_proxy_root_path') or row['proxy_root_path'];unique.setdefault((expected,root),(str(proxy),root,expected,str(cache_dir)))
    results=[]
    with ProcessPoolExecutor(max_workers=workers,initializer=_prebuild_init) as pool:
        futures=[pool.submit(_prebuild_one,payload) for payload in unique.values()]
        for future in as_completed(futures):results.append(future.result())
    result=dict(version=VERSION,cache_format_version=CACHE_FORMAT_VERSION,source_code_sha256=sha(Path(__file__)),inventory_path=str(inventory_path),inventory_sha256=sha(inventory_path),
        cache_directory=str(cache_dir),input_source_paths=len(data['sources']),unique_sha_root_identities=len(unique),completed_entries=len(results),
        actual_cached_usd_bytes=sum(r['bytes'] for r in results),cache_hits=sum(r['cache_hit'] for r in results),elapsed_s=time.monotonic()-started,
        peak_worker_process_memory_bytes=max((r['peak_worker_process_memory_bytes'] for r in results),default=0),items=results,
        scope='Pure USD byte/transform-preserving existing-source cache only; no house assembly, new stair physics, qualification or C1-C5')
    Path(cache_dir).mkdir(parents=True,exist_ok=True);write(Path(cache_dir)/('PREBUILD_RESULT_'+result['inventory_sha256'][:12]+'.json'),result);write(Path(cache_dir)/'PREBUILD_RESULT.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",type=Path)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--cache-dir",type=Path)
    parser.add_argument("--prebuild-inventory",type=Path)
    parser.add_argument("--workers",type=int,default=4,choices=range(1,5))
    args=parser.parse_args()
    Path("/proc/self/oom_score_adj").write_text("1000")
    if args.prebuild_inventory:
        if not args.cache_dir or args.manifest or args.output:parser.error('prebuild requires --cache-dir and no manifest/output')
        result=prebuild_assets(args.prebuild_inventory,args.cache_dir,args.workers)
        print(json.dumps({k:v for k,v in result.items() if k!='items'}))
    else:
        if not args.manifest or not args.output:parser.error('export requires --manifest and --output')
        result=export_collision_package(args.manifest,args.output,args.cache_dir)
        print(json.dumps({k:result[k] for k in ("version","actual_enabled_colliders","source_count","unique_assets","unique_asset_bytes","naive_repeated_asset_bytes","total_package_bytes","elapsed_s","peak_memory_bytes","qualification")}))


if __name__=="__main__":main()
