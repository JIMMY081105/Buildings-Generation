"""Source-declared native collision semantics; reuse immutable geometry arrays.

Only actual authored USD factory80:proxyType attributes supply a native type.
The metadata sidecar is bound to source SHA, resolved root, and source prim.
Unannotated aggregate SURFACE spools retain their original representation.
"""
from pathlib import Path
import hashlib,json,os
from voxel_extract import proxy_geometry,cached_sha,scene_spool as geometry_spool

def native_proxy_types(source,geometry,cache_dir):
    from pxr import Usd,UsdPhysics,Sdf
    digest=geometry['source_sha256'];root=geometry['proxy_root_path']
    key=hashlib.sha256(('native_proxy_type_v1\0'+digest+'\0'+root).encode()).hexdigest()
    folder=Path(cache_dir)/'source_semantics_v1';folder.mkdir(parents=True,exist_ok=True);target=folder/(key+'.json')
    if target.exists():
        doc=json.loads(target.read_text())
        if doc['source_sha256']!=digest or doc['proxy_root_path']!=root:raise ValueError('Native source type cache identity mismatch')
        return doc
    mask=Usd.StagePopulationMask();mask.Add(Sdf.Path(root));stage=Usd.Stage.OpenMasked(str(source['proxy_path']),mask)
    if not stage:raise ValueError('Cannot read authored native source types')
    types={};approximations={}
    for row in geometry['records']:
        prim=stage.GetPrimAtPath(row['path'])
        if not prim or not prim.HasAPI(UsdPhysics.CollisionAPI):raise ValueError('Cached collider source prim missing for type conversion')
        value=prim.GetAttribute('factory80:proxyType').Get();types[row['path']]=str(value) if value is not None else None
        approximations[row['path']]=row['approximation']
    doc=dict(schema='Factory80AuthoredNativeProxyTypeV1',source_sha256=digest,proxy_root_path=root,types_by_source_prim=types,approximations_by_source_prim=approximations)
    temporary=target.with_suffix('.tmp.'+str(os.getpid()));temporary.write_text(json.dumps(doc,indent=2));os.replace(temporary,target)
    return doc

def scene_spool(manifest,cache_dir):
    spool=geometry_spool(manifest,cache_dir);semantics={};bindings=[]
    for source in manifest['collision_sources']:
        if not (source.get('stair_id') or source.get('category')=='stair' or source.get('kind')=='stair'):continue
        geometry=proxy_geometry(source,cache_dir);doc=native_proxy_types(source,geometry,cache_dir)
        semantics[source['instance_root_path']]=doc
        bindings.append(dict(instance_root_path=source['instance_root_path'],source_sha256=doc['source_sha256'],proxy_root_path=doc['proxy_root_path'],type_schema=doc['schema']))
    for row in spool['records']:
        if not row['stair']:continue
        doc=semantics[row['instance_root_path']]
        row['native_proxy_type']=doc['types_by_source_prim'][row['path']]
        row['native_type_source_sha256']=doc['source_sha256']
        row['native_type_source_root']=doc['proxy_root_path']
        row['native_type_source_prim']=row['path']
    spool['native_type_source_bindings']=bindings
    return spool
