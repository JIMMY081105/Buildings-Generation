"""Measure CPU build report fields on the authored factory, using frozen helpers."""
import argparse, hashlib, json, sys
from pathlib import Path
import numpy as np
from pxr import Usd, UsdGeom
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
OLD=Path('/cephfs/shared/yixing/outputs/diversity54_20260908/evidence/frozen_sources/pipeline')
sys.path.insert(0,str(OLD))
from build_building import world_tris, tri_box_overlap, unit_xy
from pipeline_contract import corridor_polygon, walk_prism_obstacle_hits, BODY_HEIGHT_M, BODY_RADIUS_M, CORRIDOR_M

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def top_polygon(stage,path,z):
    prim=stage.GetPrimAtPath(path)
    if not prim or not prim.IsA(UsdGeom.Mesh):
        raise ValueError('Actual authored floor surface is not a Mesh: '+path)
    ts=world_tris(prim)
    faces=[Polygon(t[:,:2]) for t in ts if np.max(np.abs(t[:,2]-z))<1e-5]
    surface=unary_union([p for p in faces if p.area>1e-8])
    if surface.is_empty or surface.area<=0:
        raise ValueError('Actual authored floor has no measured top surface: '+path)
    return surface

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--authoring',type=Path,required=True)
    ap.add_argument('--measurement-output',type=Path);a=ap.parse_args()
    receipt=json.loads(a.authoring.read_text());out=a.measurement_output or a.authoring.parent
    if sha(receipt['stage']['path'])!=receipt['stage']['sha256']:
        raise ValueError('Actual authoring source SHA differs before input measurement')
    if a.measurement_output:
        if not out.resolve().is_relative_to(a.authoring.parent.resolve()) or out.resolve()==a.authoring.parent.resolve():
            raise ValueError('Measurement repair output must be a fresh child of the actual candidate directory')
        out.mkdir(parents=False,exist_ok=False)
    stage=Usd.Stage.Open(receipt['stage']['path']);h=receipt['floor_to_floor_m']
    bc=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default'],useExtentsHint=False)
    floor_paths=[receipt['cut_meshes'][key].get('visual_path',receipt['cut_meshes'][key]['path']) for key in ('Floor1Slab','Floor2Slab')]
    floors=[top_polygon(stage,path,z) for path,z in zip(floor_paths,(0,h))]
    floor_audit=[{'actual_mesh_path':path,'actual_mesh_type':stage.GetPrimAtPath(path).GetTypeName(),
                  'actual_top_surface_area_m2':float(poly.area)} for path,poly in zip(floor_paths,floors)]
    scans=[]
    for level in (0,1):
        obstacles=[];wallpolys=[]
        for fr in (f'/World/Floor{level+1}','/World/FactoryStructure/Columns','/World/MezzanineRail'):
            root=stage.GetPrimAtPath(fr)
            if not root:continue
            for prim in Usd.PrimRange(root):
                if not prim.IsA(UsdGeom.Mesh) or not prim.IsActive():continue
                if UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':continue
                if UsdGeom.Imageable(prim).ComputePurpose()=='guide':continue
                rr=bc.ComputeWorldBound(prim).ComputeAlignedRange()
                if rr.IsEmpty():continue
                lo=np.array(rr.GetMin());hi=np.array(rr.GetMax());lo[2]-=level*h;hi[2]-=level*h
                if hi[2]<=0 or lo[2]>=BODY_HEIGHT_M:continue
                rec={'prim':str(prim.GetPath()),'box':[float(v) for v in np.concatenate([lo,hi])]}
                if prim.GetName().startswith('wall_'):
                    wallpolys.append(box(lo[0],lo[1],hi[0],hi[1]))
                else:obstacles.append(rec)
        scans.append({'room_union':floors[level],'wall_union':unary_union(wallpolys) if wallpolys else None,'obstacle_boxes':obstacles})
    manifest=[]
    unchanged=all(sha(i['source_ref']['path'])==i['source_sha256'] for i in receipt['source_inventory'])
    for si,st in enumerate(receipt['stairs']):
        key=f'S{si+1:02d}';branch='/World/Stair/world' if si==0 else st['root']+'/world'
        root=stage.GetPrimAtPath(branch)
        if not root:raise ValueError('Actual native stair branch absent: '+branch)
        tr=json.loads(Path(st['traversal_path']).read_text());pts=st['world_path'];hole=st['opening_rect'];last=pts[-1];prev=pts[-2]
        interface=st['final_riser_interface_world_xy_m'];ex,ey=last[0]-prev[0],last[1]-prev[1]
        en_dir=unit_xy(pts[0],pts[1]);ex_dir=unit_xy(prev,last)
        approach=corridor_polygon(pts[0],(-en_dir[0],-en_dir[1]));egress=corridor_polygon(last,ex_dir)
        reports=[]
        for shape,scan in zip((approach,egress),scans):
            wall=bool(scan['wall_union'] is not None and shape.intersects(scan['wall_union']))
            hits=walk_prism_obstacle_hits(shape,scan)
            reports.append({'bounds':list(shape.bounds),'room_contains':bool(scan['room_union'].contains(shape)),
                            'wall_intersects':wall,'furniture_hits':hits})
        tris=[]
        for q in Usd.PrimRange(root):
            if q.IsA(UsdGeom.Mesh) and q.IsActive() and UsdGeom.Imageable(q).ComputeVisibility()!='invisible':
                ts=world_tris(q)
                if len(ts):tris.append(ts)
        tris=np.concatenate(tris) if tris else np.zeros((0,3,3))
        if not len(tris):raise ValueError('No real stair triangles')
        sr=bc.ComputeWorldBound(root).ComputeAlignedRange();smn=np.array(sr.GetMin());smx=np.array(sr.GetMax())
        wall_hits=[];furn_hits=[];tested=0
        for fr in ('/World/Floor1','/World/Floor2','/World/FactoryStructure/Columns','/World/MezzanineRail'):
            rr=stage.GetPrimAtPath(fr)
            if not rr:continue
            for prim in Usd.PrimRange(rr):
                if not prim.IsA(UsdGeom.Mesh) or not prim.IsActive():continue
                name=prim.GetName();iswall=name.startswith('wall_') and '_visual' in name
                arch=name.startswith(('wall_','room_','ceiling_')) or name=='floor'
                furn=not arch and '_collider' not in name
                if not (iswall or furn):continue
                if UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':continue
                rng=bc.ComputeWorldBound(prim).ComputeAlignedRange()
                if rng.IsEmpty():continue
                lo=np.array(rng.GetMin());hi=np.array(rng.GetMax())
                if np.any(lo>smx) or np.any(hi<smn):continue
                tested+=1;hit=tri_box_overlap(tris,lo+.005,hi-.005)
                if hit.any():(wall_hits if iswall else furn_hits).append({'prim':str(prim.GetPath()),'triangles_intersecting':int(hit.sum()),'box':list(map(float,np.concatenate([lo,hi])))})
        b={**st,'building':receipt['building'],'cut_meshes':receipt['cut_meshes'],'deactivated':receipt['deactivated'],
           'floor_to_floor_m':h,'sources_unmodified':unchanged and sha(st['stage'])==st['stage_sha256'],
           'height_error_m':abs(max(float(p[2]) for p in tr['points_xyz_m'])-h),
           'upper_floor_is_final_landing':bool(tr.get('upper_floor_is_final_landing',False)),
           'QA_opening_exit_aligned_to_final_riser':abs((hole[0] if ex<0 else hole[2])-interface[0])<=1e-6 if abs(ex)>=abs(ey) else abs((hole[1] if ey<0 else hole[3])-interface[1])<=1e-6,
           'QA_final_landing_is_on_floor_side_of_opening':(last[0]<hole[0] if ex<0 else last[0]>hole[2]) if abs(ex)>=abs(ey) else (last[1]<hole[1] if ey<0 else last[1]>hole[3]),
           'QA_approach_corridor_clear':reports[0]['room_contains'] and not reports[0]['wall_intersects'] and not reports[0]['furniture_hits'],
           'QA_egress_corridor_clear':reports[1]['room_contains'] and not reports[1]['wall_intersects'] and not reports[1]['furniture_hits'],
           'approach_corridor_audit':reports[0],'egress_corridor_audit':reports[1],
           'actual_floor_surface_mesh_readback':floor_audit,
           'clip_wall_intersections':wall_hits,'clip_furniture_intersections':furn_hits,'clip_boxes_tested':tested,
           'QA_no_clipping_walls':not wall_hits,'QA_no_clipping_furniture':not furn_hits,
           'evidence_kind':'ActualRootUSD_measured_CPU_build_inputs_not_Isaac_PASS',
           'frozen_helpers':{f:sha(OLD/f) for f in ('build_building.py','pipeline_contract.py')}}
        d=out/'isaac'/key;d.mkdir(parents=True,exist_ok=True)
        p=d/'BUILD_INPUT.json'
        with p.open('x') as stream:json.dump(b,stream,indent=2)
        manifest.append({'stair_id':key,'prim_path':branch,'build_report':str(p),'traversal_path':st['traversal_path'],
                         'isaac_report':str(d/'reports/isaac_validation.json')})
        print(key,{k:v for k,v in b.items() if k.startswith('QA_')},flush=True)
    if sha(receipt['stage']['path'])!=receipt['stage']['sha256']:
        raise ValueError('Actual authoring source SHA differs after input measurement')
    with (out/'STAIRS_MANIFEST.json').open('x') as stream:
        json.dump({'schema':'Factory80MeasuredStairsManifestV1','building':receipt['building'],'stairs':manifest},stream,indent=2)

if __name__=='__main__':main()
