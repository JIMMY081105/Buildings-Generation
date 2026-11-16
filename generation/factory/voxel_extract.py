"""One USD extraction per proxy SHA, then instance transforms and CUDA raster.

Collision sources are emitted by scene_v1.py, from the actual composed stage.
Visual surfaces are not audited. Cached geometry remains source-precise.
"""
from pathlib import Path
import hashlib,json,os,time
import numpy as np

def cached_sha(path,cache_dir):
    path=Path(path);s=path.stat();key=json.dumps([str(path.resolve()),s.st_size,s.st_mtime_ns])
    db=Path(cache_dir)/'VOXEL_SHA_CACHE.json';doc=json.loads(db.read_text()) if db.exists() else {}
    if key not in doc:
        h=hashlib.sha256()
        with path.open('rb') as f:
            for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
        doc[key]=h.hexdigest();tmp=db.with_suffix('.tmp.'+str(os.getpid()));tmp.write_text(json.dumps(doc));os.replace(tmp,db)
    return doc[key]

def proxy_geometry(source,cache_dir):
    from pxr import Usd,UsdGeom,UsdPhysics,Sdf
    cache_dir=Path(cache_dir);cache_dir.mkdir(parents=True,exist_ok=True)
    path=Path(source['proxy_path']);source_hash=cached_sha(path,cache_dir)
    root_path=source.get('proxy_root_path')
    key=hashlib.sha256(('cache_schema2\0'+source_hash+'\0'+str(root_path)).encode()).hexdigest()
    target=cache_dir/key;metadata=target/'GEOMETRY.json'
    if metadata.exists():return json.loads(metadata.read_text())
    if root_path:
        mask=Usd.StagePopulationMask();mask.Add(Sdf.Path(root_path));stage=Usd.Stage.OpenMasked(str(path),mask)
    else:stage=Usd.Stage.Open(str(path))
    if not stage:raise ValueError('Cannot open collision source '+str(path))
    root=stage.GetPrimAtPath(root_path) if root_path else stage.GetDefaultPrim()
    if not root:raise ValueError('Collision source root missing '+str(root_path))
    root_path=str(root.GetPath());units=float(UsdGeom.GetStageMetersPerUnit(stage));xc=UsdGeom.XformCache(Usd.TimeCode.Default())
    inv=np.linalg.inv(np.array(xc.GetLocalToWorldTransform(root),dtype=np.float64));rows=[];lower=np.full(3,np.inf);upper=-lower
    target.mkdir(parents=True,exist_ok=False)
    with (target/'vertices.bin').open('xb') as vs,(target/'faces.bin').open('xb') as fs:
        for prim in Usd.PrimRange(root,Usd.TraverseInstanceProxies()):
            if not prim.HasAPI(UsdPhysics.CollisionAPI) or UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:continue
            if prim.IsA(UsdGeom.Mesh):
                mesh=UsdGeom.Mesh(prim);v=np.asarray(mesh.GetPointsAttr().Get(),dtype=np.float64);counts=mesh.GetFaceVertexCountsAttr().Get();indices=mesh.GetFaceVertexIndicesAttr().Get();faces=[];offset=0
                for n in counts:
                    faces.extend((indices[offset],indices[offset+j],indices[offset+j+1]) for j in range(1,n-1));offset+=n
                f=np.asarray(faces,dtype=np.int64)
            elif prim.IsA(UsdGeom.Cube):
                size=float(UsdGeom.Cube(prim).GetSizeAttr().Get());v=np.array([[x,y,z] for x in [-1,1] for y in [-1,1] for z in [-1,1]],dtype=np.float64)*size/2
                f=np.array([[0,1,3],[0,3,2],[4,6,7],[4,7,5],[0,4,5],[0,5,1],[2,3,7],[2,7,6],[0,2,6],[0,6,4],[1,5,7],[1,7,3]],dtype=np.int64)
            else:raise ValueError('Unsupported exact collider primitive '+str(prim.GetPath())+' '+prim.GetTypeName())
            matrix=np.array(xc.GetLocalToWorldTransform(prim),dtype=np.float64)@inv
            v=(np.c_[v,np.ones(len(v))]@matrix)[:,:3]*units
            if not len(v) or not len(f):raise ValueError('Empty enabled collision geometry')
            approximation=str(UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get() or 'none') if prim.IsA(UsdGeom.Mesh) else 'box'
            row=dict(path=str(prim.GetPath()),approximation=approximation,face_count=len(f),vertices=dict(path=str(target/'vertices.bin'),dtype='<f8',shape=list(v.shape),byte_offset=vs.tell()),faces=dict(path=str(target/'faces.bin'),dtype='<i8',shape=list(f.shape),byte_offset=fs.tell()))
            if str(prim.GetAttribute('factory80:proxyType').Get() or '')=='exact_box':
                unique=np.unique(v,axis=0);lo=unique.min(0);hi=unique.max(0)
                expected=np.array([[x,y,z] for x in [lo[0],hi[0]] for y in [lo[1],hi[1]] for z in [lo[2],hi[2]]])
                if len(unique)!=8 or any(np.linalg.norm(expected-p,axis=1).min()>1e-6 for p in unique):raise ValueError('Authored exact_box is not eight axis-aligned corners')
                row['solid_box_corners_m']=unique.tolist()
            vs.write(v.astype('<f8',copy=False).tobytes());fs.write(f.astype('<i8',copy=False).tobytes());rows.append(row);lower=np.minimum(lower,v.min(0));upper=np.maximum(upper,v.max(0))
    if not rows:raise ValueError('Collision source has no enabled colliders')
    result=dict(source_sha256=source_hash,proxy_root_path=root_path,records=rows,bounds_min_m=lower.tolist(),bounds_max_m=upper.tolist(),enabled_colliders=len(rows))
    metadata.write_text(json.dumps(result));return result

def scene_spool(manifest,cache_dir):
    records=[];low=np.full(3,np.inf);high=-low
    for source in manifest['collision_sources']:
        geo=proxy_geometry(source,cache_dir);matrix=np.asarray(source['world_transform'],dtype=np.float64)
        lo=np.array(geo['bounds_min_m']);hi=np.array(geo['bounds_max_m']);corners=np.array([[x,y,z,1] for x in [lo[0],hi[0]] for y in [lo[1],hi[1]] for z in [lo[2],hi[2]]])
        world=(corners@matrix)[:,:3];low=np.minimum(low,world.min(0));high=np.maximum(high,world.max(0))
        sid=source.get('stair_id');is_stair=bool(sid or source.get('category')=='stair' or source.get('kind')=='stair')
        for row in geo['records']:
            records.append(dict(**row,world_transform=matrix.tolist(),instance_root_path=source['instance_root_path'],stair=is_stair,stair_id=sid if sid else (source['instance_root_path'] if is_stair else None)))
    return dict(records=records,bounds_min_m=low.tolist(),bounds_max_m=high.tolist())
