"""Factory geometry authoring. All source edits are stronger root-layer opinions.

No checker predicate is patched by this script. Results are authoring receipts;
the independent frozen pipeline must still measure this stage.
"""
import argparse, hashlib, json, math, sys, time
import numpy as np
import trimesh
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, UsdLux
from shapely.geometry import box, LineString
from shapely.geometry.polygon import orient
from shapely.ops import unary_union, triangulate

W=Path('/cephfs/shared/yixing/outputs/factory80_20261011')
F=Path('/cephfs/shared/yixing/outputs/factory10')
OLD=Path('/cephfs/shared/yixing/outputs/diversity54_20260908')
sys.path.insert(0,str(OLD/'evidence/frozen_sources/pipeline'))
from floor_collider_contract import floor_profile, expected_mesh_prims

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as s:
        for b in iter(lambda:s.read(1048576),b''):h.update(b)
    return h.hexdigest()

def material(stage,path,rgb,metal=0.0):
    m=UsdShade.Material.Define(stage,path);s=UsdShade.Shader.Define(stage,path+'/Preview')
    s.CreateIdAttr('UsdPreviewSurface');s.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*rgb))
    s.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metal)
    s.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(.48)
    m.CreateSurfaceOutput().ConnectToSource(s.ConnectableAPI(),'surface');return m

def static_mesh(mesh):
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim()).CreateCollisionEnabledAttr(True)
    UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr('none')
    mesh.GetPrim().RemoveAPI(UsdPhysics.RigidBodyAPI)

def cube(stage,path,lo,hi,mat,collision=True):
    x0,y0,z0=lo;x1,y1,z1=hi
    points=[(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),(x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
    indices=[0,3,2,1,4,5,6,7,0,1,5,4,1,2,6,5,2,3,7,6,3,0,4,7]
    m=UsdGeom.Mesh.Define(stage,path);m.CreatePointsAttr(points);m.CreateFaceVertexCountsAttr([4]*6)
    m.CreateFaceVertexIndicesAttr(indices);m.CreateSubdivisionSchemeAttr('none')
    UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
    if collision:static_mesh(m)
    return m

def beam(stage,path,start,end,width,mat):
    axis=Gf.Vec3d(*end)-Gf.Vec3d(*start);direction=axis.GetNormalized()
    ref=Gf.Vec3d(1,0,0) if abs(direction[0])<.9 else Gf.Vec3d(0,1,0)
    u=Gf.Cross(direction,ref).GetNormalized()*width/2;v=Gf.Cross(direction,u).GetNormalized()*width/2
    points=[]
    for center in (Gf.Vec3d(*start),Gf.Vec3d(*end)):
        points.extend(center+su*u+sv*v for su,sv in ((-1,-1),(1,-1),(1,1),(-1,1)))
    m=UsdGeom.Mesh.Define(stage,path);m.CreatePointsAttr(points);m.CreateFaceVertexCountsAttr([4]*6)
    m.CreateFaceVertexIndicesAttr([0,3,2,1,4,5,6,7,0,1,5,4,1,2,6,5,2,3,7,6,3,0,4,7])
    m.CreateSubdivisionSchemeAttr('none');UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
    return m

def slab(stage,path,poly,z,mat):
    # Current side quad order points outward for a clockwise exterior and
    # counterclockwise holes. Normalize authored ring winding explicitly.
    poly=orient(poly,sign=-1.0)
    pts=[];idx=[];counts=[]
    for t in triangulate(poly):
        if not poly.covers(t.representative_point()):continue
        # Constrained region test avoids spanning a stair hole.
        if t.difference(poly).area>1e-8:continue
        corners=list(t.exterior.coords)[:-1];base=len(pts)
        pts.extend((x,y,z) for x,y in corners);idx.extend(range(base,base+3));counts.append(3)
        base=len(pts);pts.extend((x,y,z-.12) for x,y in corners);idx.extend(reversed(range(base,base+3)));counts.append(3)
    polygons=[poly] if poly.geom_type=='Polygon' else list(poly.geoms)
    for polygon in polygons:
        for ring in [polygon.exterior,*polygon.interiors]:
            corners=list(ring.coords)
            for (x0,y0),(x1,y1) in zip(corners,corners[1:]):
                base=len(pts);pts.extend([(x0,y0,z),(x1,y1,z),(x1,y1,z-.12),(x0,y0,z-.12)])
                idx.extend(range(base,base+4));counts.append(4)
    if not pts:raise ValueError('Empty floor mesh')
    m=UsdGeom.Mesh.Define(stage,path);m.CreatePointsAttr(pts);m.CreateFaceVertexCountsAttr(counts)
    m.CreateFaceVertexIndicesAttr(idx);m.CreateSubdivisionSchemeAttr('none');m.CreateDoubleSidedAttr(True)
    UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat);static_mesh(m)
    return {'path':path,'z':[z-.12,z],'face_count':len(counts),'authoring_area_m2':poly.area}

def xy(p,rot):
    x,y=p[:2];r=math.radians(rot);return (x*math.cos(r)-y*math.sin(r),x*math.sin(r)+y*math.cos(r))

def refine_synthetic_collider(mesh,max_edge_m=10.0):
    """Geometry authoring only: coplanar subdivision before frozen rasterizing."""
    vertices=np.asarray(mesh.GetPointsAttr().Get(),dtype=np.float64)
    counts=mesh.GetFaceVertexCountsAttr().Get();indices=mesh.GetFaceVertexIndicesAttr().Get()
    faces=[];cursor=0
    for count in counts:
        polygon=list(indices[cursor:cursor+count]);cursor+=count
        faces.extend((polygon[0],polygon[j],polygon[j+1]) for j in range(1,count-1))
    before=trimesh.Trimesh(vertices=vertices,faces=np.asarray(faces),process=False)
    longest=float(before.edges_unique_length.max())
    if longest<=max_edge_m:return None
    refined_vertices,refined_faces=trimesh.remesh.subdivide_to_size(vertices,before.faces,max_edge=max_edge_m,max_iter=20)
    # Measure the actually representable USD point3f values, not just doubles.
    refined_vertices=np.asarray(refined_vertices,dtype=np.float32).astype(np.float64)
    after=trimesh.Trimesh(vertices=refined_vertices,faces=refined_faces,process=False)
    surface_error=0.0
    for start in range(0,len(refined_vertices),512):
        _,distance,_=trimesh.proximity.closest_point_naive(before,refined_vertices[start:start+512])
        surface_error=max(surface_error,float(distance.max()))
    bounds_error=float(np.abs(before.bounds-after.bounds).max())
    volume_error=abs(float(after.volume)-float(before.volume))
    if bounds_error>1e-5 or surface_error>1e-5 or volume_error>1e-7*max(1,abs(float(before.volume))):
        raise ValueError('Synthetic refinement altered measured shape: '+str(mesh.GetPath()))
    actual_longest=float(after.edges_unique_length.max())
    if actual_longest>max_edge_m+1e-5:raise ValueError('Synthetic refinement failed actual edge-size check')
    mesh.GetPointsAttr().Set([Gf.Vec3f(*(float(x) for x in v)) for v in refined_vertices])
    mesh.GetFaceVertexCountsAttr().Set([3]*len(refined_faces));mesh.GetFaceVertexIndicesAttr().Set(refined_faces.reshape(-1).tolist())
    return {'path':str(mesh.GetPath()),'authoring_reason':'Large synthetic faces exceed unchanged voxelizer subdivision budget; coplanar triangles preserve the authored surface',
            'before_vertices':len(vertices),'after_vertices':len(refined_vertices),'before_triangles':len(before.faces),'after_triangles':len(refined_faces),
            'before_max_edge_m':longest,'after_max_edge_m':actual_longest,'actual_bbox_error_m':bounds_error,
            'actual_surface_error_m':surface_error,'signed_volume_before_m3':float(before.volume),'signed_volume_after_m3':float(after.volume),
            'original_voxelizer_or_gate_modified':False}

def compound_slab(stage,path,poly,z,mat):
    """One visual surface; collision partitions retain exactly its triangles."""
    UsdGeom.Xform.Define(stage,path)
    visual_path=path+'/Visual'
    receipt=slab(stage,visual_path,poly,z,mat);visual=UsdGeom.Mesh(stage.GetPrimAtPath(visual_path))
    refinement=refine_synthetic_collider(visual)
    UsdPhysics.CollisionAPI(visual.GetPrim()).CreateCollisionEnabledAttr(False)
    vertices=np.asarray(visual.GetPointsAttr().Get(),dtype=np.float32)
    counts=visual.GetFaceVertexCountsAttr().Get();indices=visual.GetFaceVertexIndicesAttr().Get();faces=[];cursor=0
    for count in counts:
        polygon=list(indices[cursor:cursor+count]);cursor+=count
        faces.extend((polygon[0],polygon[j],polygon[j+1]) for j in range(1,count-1))
    faces=np.asarray(faces,dtype=np.int64);expanded=vertices[faces]
    buckets={}
    for i,centroid in enumerate(expanded.mean(axis=1)):
        key=tuple(np.floor(centroid[:2]/10.0).astype(int));buckets.setdefault(key,[]).append(i)
    reconstruction=np.empty_like(expanded);parts=[]
    for index,(key,face_ids) in enumerate(sorted(buckets.items())):
        selected=faces[face_ids];used,inverse=np.unique(selected.reshape(-1),return_inverse=True)
        pp=path+f'/CollisionTiles/slab_collision_T{index:03d}'
        mesh=UsdGeom.Mesh.Define(stage,pp);actual_vertices=vertices[used];actual_faces=inverse.reshape(-1,3)
        mesh.CreatePointsAttr([Gf.Vec3f(*(float(x) for x in v)) for v in actual_vertices]);mesh.CreateFaceVertexCountsAttr([3]*len(actual_faces))
        mesh.CreateFaceVertexIndicesAttr(actual_faces.reshape(-1).tolist());mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreatePurposeAttr('guide');UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat);static_mesh(mesh)
        reconstruction[face_ids]=actual_vertices[actual_faces]
        parts.append({'path':pp,'spatial_bucket_xy':list(map(int,key)),'actual_triangle_count':len(face_ids),
                      'original_visual_triangle_indices':face_ids,'actual_vertices':len(actual_vertices)})
    before_sha=hashlib.sha256(expanded.tobytes()).hexdigest();after_sha=hashlib.sha256(reconstruction.tobytes()).hexdigest()
    if before_sha!=after_sha or not np.array_equal(expanded,reconstruction):raise ValueError('Collision partition changed the visual surface')
    receipt.update(path=path,visual_path=visual_path,visual_collision_enabled=False,coplanar_refinement=refinement,
       triangle_surface_partition={'method':'Partition original triangles by 10m centroid buckets; no new internal faces',
          'actual_visual_triangles':len(faces),'actual_collision_triangles':sum(p['actual_triangle_count'] for p in parts),
          'original_expanded_triangles_sha256':before_sha,'reconstructed_collision_triangles_sha256':after_sha,
          'exact_original_surface_preserved':True,'parts':parts})
    return receipt

def partition_synthetic_wall_collider(stage,mesh,mat,bucket_m=10.0):
    """Partition only an owned wall surface; retain all original triangles."""
    path=str(mesh.GetPath())
    if not path.startswith('/World/Floor1/Geometry/wall_0_') or '_collision_' not in path:
        raise ValueError('Wall partition is restricted to owned perimeter wall colliders')
    refinement=refine_synthetic_collider(mesh)
    vertices=np.asarray(mesh.GetPointsAttr().Get(),dtype=np.float32)
    counts=mesh.GetFaceVertexCountsAttr().Get();indices=mesh.GetFaceVertexIndicesAttr().Get()
    faces=[];cursor=0
    for count in counts:
        polygon=list(indices[cursor:cursor+count]);cursor+=count
        faces.extend((polygon[0],polygon[j],polygon[j+1]) for j in range(1,count-1))
    faces=np.asarray(faces,dtype=np.int64);expanded=vertices[faces]
    buckets={}
    for i,centroid in enumerate(expanded.mean(axis=1)):
        key=tuple(np.floor(centroid[:2]/bucket_m).astype(int));buckets.setdefault(key,[]).append(i)
    reconstruction=np.empty_like(expanded);parts=[]
    for index,(key,face_ids) in enumerate(sorted(buckets.items())):
        selected=faces[face_ids];used,inverse=np.unique(selected.reshape(-1),return_inverse=True)
        pp=path+f'/CollisionTiles/wall_collision_T{index:03d}'
        actual_vertices=vertices[used];actual_faces=inverse.reshape(-1,3)
        child=UsdGeom.Mesh.Define(stage,pp)
        child.CreatePointsAttr([Gf.Vec3f(*(float(x) for x in v)) for v in actual_vertices])
        child.CreateFaceVertexCountsAttr([3]*len(actual_faces));child.CreateFaceVertexIndicesAttr(actual_faces.reshape(-1).tolist())
        child.CreateSubdivisionSchemeAttr('none');child.CreatePurposeAttr('guide')
        UsdShade.MaterialBindingAPI.Apply(child.GetPrim()).Bind(mat);static_mesh(child)
        reconstruction[face_ids]=actual_vertices[actual_faces]
        parts.append({'path':pp,'spatial_bucket_xy':list(map(int,key)),
                      'original_wall_triangle_indices':face_ids,'actual_triangle_count':len(face_ids),
                      'actual_vertices':len(actual_vertices)})
    before_sha=hashlib.sha256(expanded.tobytes()).hexdigest();after_sha=hashlib.sha256(reconstruction.tobytes()).hexdigest()
    if before_sha!=after_sha or not np.array_equal(expanded,reconstruction):
        raise ValueError('Synthetic wall partition changed an original triangle')
    # The original guide mesh retains its complete authored surface. Its
    # colliders are its children; the separate beauty visual is unchanged.
    UsdPhysics.CollisionAPI(mesh.GetPrim()).CreateCollisionEnabledAttr(False)
    return {'original_surface_path':path,'original_surface_collision_enabled':False,
            'scope':'Owned synthesized perimeter wall collision surface only',
            'coplanar_refinement':refinement,
            'triangle_surface_partition':{'method':'Partition original refined triangles by 10m XY centroid buckets; no new internal faces',
                'actual_original_triangles':len(faces),'actual_collision_triangles':sum(p['actual_triangle_count'] for p in parts),
                'original_expanded_triangles_sha256':before_sha,'reconstructed_collision_triangles_sha256':after_sha,
                'exact_original_surface_preserved':True,'parts':parts},
            'original_voxelizer_or_gate_modified':False}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--building',required=True);ap.add_argument('--attempt',type=int,required=True)
    ap.add_argument('--stair-assets',type=Path,required=True);a=ap.parse_args()
    plan=json.loads((W/'TILING_PLAN.json').read_text());row=next(r for r in plan['rows'] if r['building']==a.building)
    assert row['storeys'] == 2, f"{a.building}: 本路径只支持 2 层，实测 storeys={row['storeys']}"
    scheme=next(r for r in row['schemes'] if r['attempt']==a.attempt)
    srcs=scheme['tiles'];assert len({r['scene'] for r in srcs})==len(srcs)
    assets=json.loads(a.stair_assets.read_text())['rows'];assert len(assets)==row['planned_stair_count']
    out=W/'buildings'/a.building/f'attempt_{a.attempt:02d}';out.mkdir(parents=True,exist_ok=False)
    stage=Usd.Stage.CreateNew(str(out/'root.usda'));root=UsdGeom.Xform.Define(stage,'/World').GetPrim();stage.SetDefaultPrim(root)
    UsdGeom.SetStageMetersPerUnit(stage,1.0);UsdGeom.SetStageUpAxis(stage,'Z')
    concrete=material(stage,'/World/Looks/UnifiedConcrete',(.32,.34,.35))
    steel=material(stage,'/World/Looks/FactorySteel',(.15,.19,.22),1)
    rail=material(stage,'/World/Looks/SafetyRail',(.92,.60,.03),1)
    screen_path=F/'source_catalog/combined184_screen_v2.json';screen=json.loads(screen_path.read_text())
    screens={r['scene'].removesuffix('_ceiling'):r for r in screen['ranked_scenes']}
    retained_assets=set();duplicates=[];inventory=[];deactivated=[];source_before={};n=row['grid_n'];px,py=row['tile_pitch_m'];h=row['floor_to_floor_m']
    for ti,r in enumerate(srcs):
        source=Path(r['source']['path']);assert sha(source)==r['source']['sha256']
        source_before[str(source)]=sha(source);native=Usd.Stage.Open(str(source));nr=native.GetDefaultPrim()
        ng=native.GetPrimAtPath(str(nr.GetPath())+'/Geometry');meta=json.loads(source.with_name('scene_metadata.json').read_text())
        industrial={e['asset_id'] for group in ('industrial','operational','warehouse') for e in screens[r['scene']]['evidence'].get(group,[])}
        if not industrial:raise ValueError('No original-screen industrial evidence: '+r['scene'])
        bbox=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render','guide'],useExtentsHint=False)
        keep=[];removed=[]
        for p in ng.GetChildren():
            rec=meta['objects'].get(p.GetName());asset=rec.get('asset_id') if rec else None
            if asset in industrial and UsdGeom.Imageable(p):
                b=bbox.ComputeWorldBound(p).ComputeAlignedRange()
                if b.IsEmpty():continue
                if asset in retained_assets:
                    duplicates.append({'scene':r['scene'],'object':p.GetName(),'asset_id':asset});removed.append(p.GetName());continue
                retained_assets.add(asset);keep.append((p,asset,b))
            else:removed.append(p.GetName())
        if not keep:raise ValueError('No unique industrial equipment remains in '+r['scene'])
        lo=[min(b.GetMin()[i] for _,_,b in keep) for i in range(3)]
        hi=[max(b.GetMax()[i] for _,_,b in keep) for i in range(3)]
        if hi[0]-lo[0]>px-2 or hi[1]-lo[1]>py-2:raise ValueError('Equipment would overlap a reserved cell aisle: '+r['scene'])
        x,y=r['cell'];dx=x*px+(px-(hi[0]-lo[0]))/2-lo[0];dy=y*py+(py-(hi[1]-lo[1]))/2-lo[1]
        dz=r['storey']*h
        path=f'/World/Floor{r["storey"]+1}/Geometry/Tile{ti:03d}'
        prim=UsdGeom.Xform.Define(stage,path).GetPrim();prim.GetReferences().AddReference(str(source),str(nr.GetPath()))
        xf=UsdGeom.Xformable(prim);xf.ClearXformOpOrder();xf.AddTranslateOp().Set(Gf.Vec3d(dx,dy,dz))
        for p in nr.GetChildren():
            # Material networks are source content, and must remain available
            # to the retained machinery. Remove other source scene wrappers.
            has_material=any(q.IsA(UsdShade.Material) or q.IsA(UsdShade.Shader) for q in Usd.PrimRange(p))
            if p.GetName()!='Geometry' and not has_material:
                stage.OverridePrim(path+'/'+p.GetName()).SetActive(False)
        for name in removed:
            target=path+'/Geometry/'+name;stage.OverridePrim(target).SetActive(False);deactivated.append(target)
        for p in list(Usd.PrimRange(prim)):
            p.RemoveAPI(UsdPhysics.RigidBodyAPI)
            if p.IsA(UsdGeom.Mesh) and p.HasAPI(UsdPhysics.CollisionAPI):
                UsdPhysics.MeshCollisionAPI.Apply(p).CreateApproximationAttr('none')
        inventory.append({'scene':r['scene'],'source_scene_id':r['scene'],'source_root':str(nr.GetPath()),
                          'source_path':str(source),'actual_tile_root_prim_path':path,
                          'source_sha256':r['source']['sha256'],'source_metadata_ref':{'path':str(source.with_name('scene_metadata.json')),'sha256':sha(source.with_name('scene_metadata.json'))},
                          'tile':r['cell'],'floor_id':r['storey'],'cell':r['cell'],'storey':r['storey'],'equipment_bbox_min':lo,'equipment_bbox_max':hi,
                          'translation':[dx,dy,dz],'equipment_retained':[{'name':p.GetName(),'asset_id':asset,'canonical_asset_id':asset,
                            'actual_root_prim_path':path+'/Geometry/'+p.GetName(),'source_scene_id':r['scene'],
                            'source_prim_path':str(p.GetPath()),'source_asset_references':[{'asset_path':q.assetPath,'prim_path':str(q.primPath)} for q in p.GetMetadata('references').GetAddedOrExplicitItems()] if p.GetMetadata('references') else []} for p,asset,b in keep],
                          'source_ref':r['source'],'removed_root_geometry_children':removed})
        print(a.building,'tile',ti,r['scene'],len(keep),'retained',flush=True)
        del native
    # Expose each actual equipment object directly beneath floor Geometry.
    # The unchanged floor contract chooses guide-vs-visual colliders per object;
    # grouping a whole scene into one object would lose visual-only equipment.
    # World pose/materials stay identical; disable the previous Tile instance.
    promoted=[]
    for inv in inventory:
        ev={e['asset_id']:e for g in ('industrial','operational','warehouse')
            for e in screens[inv['scene']]['evidence'].get(g,[])}
        for eq in inv['equipment_retained']:
            evidence=ev[eq['asset_id']]
            category=str(evidence.get('category','')).lower()
            object_type=str(evidence.get('object_type','')).lower()
            is_workbench=category=='workbench' or 'workbench' in object_type
            original=eq['actual_root_prim_path'];op=stage.GetPrimAtPath(original)
            matrix=UsdGeom.XformCache().GetLocalToWorldTransform(op)
            bindings=[]
            for q in Usd.PrimRange(op):
                if q.IsA(UsdGeom.Gprim):
                    bound=UsdShade.MaterialBindingAPI(q).ComputeBoundMaterial()[0]
                    if bound:bindings.append((str(q.GetPath())[len(original):],str(bound.GetPath())))
            prefix='FactoryWorkbench' if is_workbench else 'FactoryEquipment'
            tile_name=inv['actual_tile_root_prim_path'].rsplit('/',1)[1]
            dest=f'/World/Floor{inv["storey"]+1}/Geometry/{prefix}_{tile_name}_{eq["name"]}'
            dp=stage.DefinePrim(dest,op.GetTypeName())
            dp.GetReferences().AddReference(inv['source_ref']['path'],eq['source_prim_path'])
            xf=UsdGeom.Xformable(dp);xf.ClearXformOpOrder();xf.AddTransformOp().Set(matrix)
            for q in Usd.PrimRange(dp):q.RemoveAPI(UsdPhysics.RigidBodyAPI)
            for rel,matpath in bindings:
                mapped=dest+matpath[len(original):] if matpath.startswith(original+'/') else matpath
                mp=stage.GetPrimAtPath(mapped)
                if not mp or not mp.IsA(UsdShade.Material):raise ValueError('Workbench material lost: '+mapped)
                UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath(dest+rel)).Bind(UsdShade.Material(mp))
            stage.OverridePrim(original).SetActive(False);deactivated.append(original)
            eq['actual_root_prim_path']=dest
            promoted.append({'source_instance':original,'actual_instance':dest,'metadata_evidence':evidence,
                             'world_matrix_before':str(matrix),'world_matrix_after':str(UsdGeom.XformCache().GetLocalToWorldTransform(dp))})
    # Use the unchanged floor-object expectation to author missing exact colliders.
    for fr in ('/World/Floor1','/World/Floor2'):
        expected,_=expected_mesh_prims(stage,fr);expect={str(p.GetPath()) for p in expected}
        for p in Usd.PrimRange(stage.GetPrimAtPath(fr)):
            if p.IsA(UsdGeom.Mesh):
                if str(p.GetPath()) in expect:static_mesh(UsdGeom.Mesh(p))
                elif p.HasAPI(UsdPhysics.CollisionAPI):UsdPhysics.CollisionAPI(p).CreateCollisionEnabledAttr(False)
    lower=box(0,0,n*px,n*py)
    upper=unary_union([box(t['cell'][0]*px,t['cell'][1]*py,(t['cell'][0]+1)*px,(t['cell'][1]+1)*py) for t in srcs if t['storey']==1])
    stairs=[];holes=[]
    for si,asset in enumerate(assets):
        sp=Path(asset['stage']);metric=json.loads(Path(asset['metrics']).read_text());tr=json.loads(Path(asset['traversal_path']).read_text())
        pts=tr['points_xyz_m'];end=pts[-1]
        exit_local=[end[0]-pts[-2][0],end[1]-pts[-2][1]]
        if abs(exit_local[0])>=abs(exit_local[1]):rot=270 if exit_local[0]>=0 else 90
        else:rot=180 if exit_local[1]>=0 else 0
        # Enclosed stair wells in the full-width mezzanine band retain the
        # unchanged C3 four-side slab probes. Arrivals face the rear facade
        # with four metres of floor before it; equipment is cleared below.
        # Centre each shaft in a ten-metre column bay, with distribution along
        # the full facade. Aligning exactly on the column line would clip it.
        ideal_x=(si+1)*n*px/(len(assets)+1)
        bay_centres=[px/4+k*px/2 for k in range(2*n)]
        target=[min(bay_centres,key=lambda c:abs(c-ideal_x)),4.0,h]
        ex,ey=xy(end,rot);offset=[target[0]-ex,target[1]-ey,0]
        path='/World/Stair' if si==0 else f'/World/Stair/S{si+1:02d}'
        p=UsdGeom.Xform.Define(stage,path).GetPrim();p.GetReferences().AddReference(str(sp))
        xf=UsdGeom.Xformable(p);xf.ClearXformOpOrder()
        if si>0:xf.SetResetXformStack(True)
        translate=xf.AddTranslateOp();translate.Set(Gf.Vec3d(*offset));xf.AddRotateZOp().Set(rot)
        bc=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render','guide'],useExtentsHint=False);b=bc.ComputeWorldBound(p).ComputeAlignedRange()
        # Centre the actual shaft within the continuous mezzanine strip at
        # its X position, retaining at least four metres at the rear facade.
        # The L-shaped upper outline can offer 21 or 42 metres of depth.
        x0=b.GetMin()[0]-.35;x1=b.GetMax()[0]+.35;depth=0.0
        for candidate_depth in [py*k for k in range(1,n+1)]:
            if upper.covers(box(x0,0,x1,candidate_depth)):depth=candidate_depth
            else:break
        if depth==0:raise ValueError('No full-width mezzanine strip for actual shaft')
        forward_extent=b.GetMax()[1]-target[1]
        target_y=max(4.0,depth/2-forward_extent/2)
        offset[1]+=target_y-target[1];target[1]=target_y;translate.Set(Gf.Vec3d(*offset))
        bc=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render','guide'],useExtentsHint=False)
        b=bc.ComputeWorldBound(p).ComputeAlignedRange()
        # Exact opening stops at the final-riser interface; top landing remains supplied by upper slab.
        interface=tr['final_riser_interface_xy_m']
        assert interface==metric['floor_integration']['final_riser_interface_xy_m'],'Native metric/path interface mismatch'
        ix,iy=xy(interface,rot)
        ix+=offset[0];iy+=offset[1]
        rect=[b.GetMin()[0]-.35,iy,b.GetMax()[0]+.35,b.GetMax()[1]+.35]
        holes.append(box(*rect))
        for q in Usd.PrimRange(p):
            q.RemoveAPI(UsdPhysics.RigidBodyAPI)
            if q.IsA(UsdGeom.Mesh) and q.HasAPI(UsdPhysics.CollisionAPI):UsdPhysics.MeshCollisionAPI.Apply(q).CreateApproximationAttr('none')
        stairs.append({'index':si,'root':path,'stage':str(sp),'stage_sha256':sha(sp),'metrics':asset['metrics'],
                       'traversal_path':asset['traversal_path'],'stair_offset':offset,'stair_rotation_deg':rot,'opening_rect':rect,
                       'final_riser_interface_world_xy_m':[ix,iy],'target_arrival_xyz_m':target,
                       'placement_available_mezzanine_strip_depth_m':depth,'placement_rule':'Column-bay centre; native shaft centred within continuous mezzanine depth; at least 4m rear floor.',
                       'world_path':[[*tuple(c+offset[j] for j,c in enumerate(xy(v,rot))),v[2]] for v in pts]})
    upper=upper.difference(unary_union(holes));assert upper.geom_type=='Polygon','Mezzanine split by stair openings'
    slabs={'Floor1Slab':compound_slab(stage,'/World/Cut/Floor1Slab',lower,0,concrete),
           'Floor2Slab':compound_slab(stage,'/World/Cut/Floor2Slab',upper,h,concrete)}
    structure=[]
    # Outer panel walls are actual logical wall groups. Two wide facade openings
    # remain physical openings; no internal scene walls survive between tiles.
    wall_boxes=[((0,0,0),(n*px*.4,.12,9)),((n*px*.4+4,0,0),(n*px,.12,9)),
                ((0,n*py-.12,0),(n*px*.6,n*py,9)),((n*px*.6+4,n*py-.12,0),(n*px,n*py,9)),
                ((0,.12,0),(.12,n*py-.12,9)),((n*px-.12,.12,0),(n*px,n*py-.12,9))]
    wall_partitions=[]
    for wi,(lo,hi) in enumerate(wall_boxes):
        group=f'/World/Floor1/Geometry/wall_0_{wi}'
        cube(stage,group+f'/wall_0_{wi}_visual_0',lo,hi,steel,False)
        collider=cube(stage,group+f'/wall_0_{wi}_collision_0',lo,hi,steel)
        collider.CreatePurposeAttr('guide')
        wall_partitions.append(partition_synthetic_wall_collider(stage,collider,steel))
        structure.append({'type':'perimeter_wall','path':group,'position':list(lo)})
    for x in range(0,2*n+1):
        for y in range(0,2*n+1):
            cx=x*px/2;cy=y*py/2;path=f'/World/FactoryStructure/Columns/C{x:02d}_{y:02d}'
            cube(stage,path,(cx-.15,cy-.15,0),(cx+.15,cy+.15,row['roof_height_m']),steel)
            structure.append({'type':'column','path':path,'position':[cx,cy,0]})
    for x in range(0,2*n+1):
        cx=x*px/2;path=f'/World/FactoryStructure/Trusses/T{x:02d}'
        cube(stage,path,(cx-.12,0,8.3),(cx+.12,n*py,8.6),steel,False)
        structure.append({'type':'truss_chord','path':path,'position':[cx,0,8.3]})
        cube(stage,path+'_Upper',(cx-.12,0,8.9),(cx+.12,n*py,9.0),steel,False)
        structure.append({'type':'truss_chord','path':path+'_Upper','position':[cx,0,8.9]})
        for y in range(0,2*n):
            y0=y*py/2;y1=(y+1)*py/2;wp=path+f'_Web{y:02d}'
            start=[cx,y0,8.4 if y%2==0 else 8.95];end=[cx,y1,8.95 if y%2==0 else 8.4]
            beam(stage,wp,start,end,.10,steel)
            structure.append({'type':'truss_web','path':wp,'position':start,'end_position':end})
    roofpath='/World/FactoryStructure/Roof';cube(stage,roofpath,(0,0,9.0),(n*px,n*py,9.15),steel,False)
    structure.append({'type':'roof','path':roofpath,'position':[0,0,9]})
    dome=UsdLux.DomeLight.Define(stage,'/World/FactoryLighting/Dome')
    dome.CreateIntensityAttr(900.0);dome.CreateColorAttr(Gf.Vec3f(1.0,.94,.87))
    sun=UsdLux.DistantLight.Define(stage,'/World/FactoryLighting/Daylight')
    sun.CreateIntensityAttr(2800.0);sun.CreateAngleAttr(3.0)
    UsdGeom.Xformable(sun.GetPrim()).AddRotateXYZOp().Set(Gf.Vec3f(35,-25,30))
    # Mezzanine railing follows every external ring except stair arrival entrances.
    coords=list(upper.exterior.coords)
    for ri,((x0,y0),(x1,y1)) in enumerate(zip(coords,coords[1:])):
        length=math.hypot(x1-x0,y1-y0)
        if length<.2:continue
        for j in range(math.ceil(length/1.5)+1):
            t=min(1,j*1.5/length);x=x0+t*(x1-x0);y=y0+t*(y1-y0)
            if any(math.hypot(x-s['world_path'][-1][0],y-s['world_path'][-1][1])<.8 for s in stairs):continue
            cube(stage,f'/World/MezzanineRail/R{ri:03d}P{j:03d}',(x-.035,y-.035,h),(x+.035,y+.035,h+1.1),rail)
            t2=min(1,(j+1)*1.5/length);xx=x0+t2*(x1-x0);yy=y0+t2*(y1-y0)
            if math.hypot(xx-x,yy-y)<.05:continue
            if any(math.hypot((x+xx)/2-s['world_path'][-1][0],(y+yy)/2-s['world_path'][-1][1])<1.0 for s in stairs):continue
            for rz in (.55,1.05):
                m=beam(stage,f'/World/MezzanineRail/R{ri:03d}B{j:03d}H{int(rz*100)}',(x,y,h+rz),(xx,yy,h+rz),.055,rail)
                static_mesh(m)
    # Geometry authoring clears only equipment that physically intersects a
    # stair or its measured approach/egress envelope; evidence names each prim.
    from pipeline_contract import BODY_RADIUS_M, BODY_HEIGHT_M, CORRIDOR_M
    cleared=[];bc=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render','guide'],useExtentsHint=False)
    envelopes=[]
    for st in stairs:
        pth=st['world_path'];en=Gf.Vec2d(pth[1][0]-pth[0][0],pth[1][1]-pth[0][1]).GetNormalized()
        ex=Gf.Vec2d(pth[-1][0]-pth[-2][0],pth[-1][1]-pth[-2][1]).GetNormalized()
        full=[[pth[0][0]-en[0]*CORRIDOR_M,pth[0][1]-en[1]*CORRIDOR_M,0],*pth,
              [pth[-1][0]+ex[0]*CORRIDOR_M,pth[-1][1]+ex[1]*CORRIDOR_M,h]]
        for aa,bb in zip(full,full[1:]):
            envelopes.append((LineString([aa[:2],bb[:2]]).buffer(BODY_RADIUS_M),min(aa[2],bb[2]),max(aa[2],bb[2])+BODY_HEIGHT_M))
    for inv in inventory:
        kept=[]
        for eq in inv['equipment_retained']:
            pp=stage.GetPrimAtPath(eq['actual_root_prim_path']);bb=bc.ComputeWorldBound(pp).ComputeAlignedRange()
            objbox=box(bb.GetMin()[0],bb.GetMin()[1],bb.GetMax()[0],bb.GetMax()[1])
            clashes=[i for i,(area,z0,z1) in enumerate(envelopes) if bb.GetMax()[2]>z0 and bb.GetMin()[2]<z1 and objbox.intersects(area)]
            if clashes:
                stage.OverridePrim(eq['actual_root_prim_path']).SetActive(False);deactivated.append(eq['actual_root_prim_path'])
                cleared.append({'equipment':eq,'world_bbox_min':list(bb.GetMin()),'world_bbox_max':list(bb.GetMax()),'path_envelopes':clashes})
            else:kept.append(eq)
        inv['equipment_retained']=kept
    retained_assets={eq['canonical_asset_id'] for inv in inventory for eq in inv['equipment_retained']}
    profiles={r:floor_profile(stage,r) for r in ('/World/Floor1','/World/Floor2')}
    refined=[s['coplanar_refinement'] for s in slabs.values() if s.get('coplanar_refinement')]
    refined.extend(s['coplanar_refinement'] for s in wall_partitions if s.get('coplanar_refinement'))
    for p in stage.Traverse():
        name=str(p.GetPath())
        owned_synthetic=(name.startswith('/World/Cut/') or name.startswith('/World/Floor1/Geometry/wall_0_')
                         or name.startswith('/World/FactoryStructure/Columns/') or name.startswith('/World/MezzanineRail/'))
        if owned_synthetic and p.IsA(UsdGeom.Mesh) and p.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get():
            result=refine_synthetic_collider(UsdGeom.Mesh(p))
            if result:refined.append(result)
    stage.GetRootLayer().Save()
    for p,before in source_before.items():assert sha(Path(p))==before,'Source modified during authoring'
    counts=Counter(r['type'] for r in structure)
    receipt={'schema':'Factory80RootOverlayAuthoringV1','utc':datetime.now(timezone.utc).isoformat(),
             'building':a.building,'attempt':a.attempt,'work_state':'GEOMETRY_AUTHORED','verification_state':'FROZEN_GATES_NOT_EXECUTED',
             'stage':{'path':str(out/'root.usda'),'bytes':(out/'root.usda').stat().st_size,'sha256':sha(out/'root.usda')},'grid_n':n,'storeys':2,
             'floor_to_floor_m':h,'stair_count':len(stairs),'stairs':stairs,'deactivated':deactivated,'cut_meshes':slabs,
             'source_inventory':inventory,'source_layers_sha_unchanged':True,'same_building_scene_reuse_count':0,
             'retained_equipment_asset_count':len(retained_assets),'retained_duplicate_asset_count':0,
             'duplicate_instances_removed':duplicates,'equipment_cleared_from_actual_path_envelopes':cleared,
             'equipment_hierarchy_relocations':promoted,
             'structure_counts':dict(counts),'structure':structure,
             'structure_root_prim_paths':['/World/FactoryStructure/Columns','/World/FactoryStructure/Trusses','/World/FactoryStructure/Roof'],
             'authored_floor_area_m2':{'floor1':lower.area,'floor2':upper.area},'authored_mezzanine_coverage':upper.area/lower.area,
             'floor_collider_profiles':profiles,'floor_material_max_kinds':1,
             'synthetic_collider_coplanar_refinement':refined,
             'synthetic_wall_collision_partitions':wall_partitions,
             'floor_surface_prim_paths':[s['visual_path'] for s in slabs.values()],
             'tile_pitch_m':[px,py],'footprint_bounds_m':[0,0,n*px,n*py],
             'cannot_support':'根层写入和材质/构件收据不证明实测可走面积、连通性、G25、Isaac stage_d、图、容量、视觉工厂观感或人形攀爬。'}
    with (out/'AUTHORING.json').open('x') as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
    print('GEOMETRY_AUTHORED',a.building,str(out/'root.usda'),flush=True)

if __name__=='__main__':main()
