"""Deterministic layout-geometry camera planning; never imports the render SDK."""
from pathlib import Path
import argparse
import hashlib
import json
import math
import time
import numpy as np
from shapely.geometry import Point, box, shape

VERSION = 'camera_plan@v1'
VIEW_IDS = ['exterior_01', 'exterior_02_cutaway', 'ground_01', 'ground_02', 'mezzanine_01', 'stair_01']
RULES = dict(
    schema='Factory80CameraRulesBatchV1', version=VERSION,
    view_ids=VIEW_IDS, gray_view_id='collision_gray', gray_camera_from='exterior_02_cutaway',
    resolution=[1280, 720], focal_length_mm=24., horizontal_aperture_mm=20.955,
    clipping_range_m=[.05, 5000.], fit_frame_fraction=.88,
    exterior_directions=[[1., -1., .65], [-1., -1., .95]],
    interior_grid_n=9, aisle_samples=5, camera_xy_clearance_m=.45,
    near_equipment_exclusion_m=1.2, interior_max_targets=96,
    interior_eye_above_obstacle_m=.65, ceiling_clearance_m=.35,
    interior_eye_min_height_m=1.75, target_height_cap_m=1.5,
    interior_selection='max visible projected equipment AABB centers; then camera-target XY separation; stable grid order',
    interior_visibility='conservative equipment+retained-structure AABB segment tests and actual upper-slab intersection only; no pixels/appearance/photometry',
    support_regions=['double_height or Floor1', 'under_mezz or Floor1', 'net Floor2'],
    interior_cutaway='none; retain roof, walls, upper slab and all equipment',
    stair_id='S01', stair_direction='negative first world_path ascent plus 0.85 right side plus 0.65 up; fit whole actual stair bbox',
    stair_visibility='roof+perimeter+Floor2Slab; hide non-S01 equipment whose padded AABB intersects eye-to-S01 bbox center/corner segments',
    stair_occluder_padding_m=.2, stair_target_inset_fraction=.12,
    protected_prefix='/World/Stair/S01',
    visibility_changes='session layer only; exact actual hidden prim paths recorded per photo',
    image_scoring='NOT_EXECUTED', geometry_edits='NOT_EXECUTED',
    post_freeze_adjustment='NONE; same rules for all29; no image-driven camera adjustment')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def corners(bounds):
    lo, hi = np.asarray(bounds, dtype=float)
    return np.array([[x, y, z] for x in [lo[0], hi[0]] for y in [lo[1], hi[1]] for z in [lo[2], hi[2]]])


def ray_hits(eye, targets, bounds, padding=0.):
    """Finite-segment vs closed AABBs; result [targets, boxes]."""
    bounds=np.asarray(bounds,dtype=float)
    if not len(bounds): return np.zeros((len(targets), 0),dtype=bool)
    eye=np.asarray(eye,dtype=float);d=np.asarray(targets,dtype=float)-eye
    parallel=np.abs(d)<1e-12
    safe=np.where(parallel,1.,d)
    t0=(bounds[None,:,0,:]-padding-eye)/safe[:,None,:]
    t1=(bounds[None,:,1,:]+padding-eye)/safe[:,None,:]
    low=np.where(parallel[:,None,:],-np.inf,np.minimum(t0,t1))
    high=np.where(parallel[:,None,:],np.inf,np.maximum(t0,t1))
    outside=parallel[:,None,:] & ((eye<bounds[None,:,0,:]-padding)|(eye>bounds[None,:,1,:]+padding))
    return (~outside.any(axis=2)) & (np.maximum(low.max(axis=2),1e-6)<np.minimum(high.min(axis=2),1.-1e-6))


def frame_basis(eye,target):
    f=np.asarray(target)-np.asarray(eye);f=f/np.linalg.norm(f)
    r=np.cross(f,[0.,0.,1.]);r=r/np.linalg.norm(r)
    return r,np.cross(r,f),f


def fit(points, direction):
    points=np.asarray(points,dtype=float);target=(points.min(axis=0)+points.max(axis=0))/2
    direction=np.asarray(direction,dtype=float);direction/=np.linalg.norm(direction)
    r,u,f=frame_basis(target+direction,target)
    offset=points-target
    tx=RULES['horizontal_aperture_mm']/(2*RULES['focal_length_mm'])*RULES['fit_frame_fraction']
    ty=tx*720/1280
    distance=max(2.,float(np.max(np.maximum(np.abs(offset@r)/tx,np.abs(offset@u)/ty)-offset@f)))+.5
    return target+direction*distance,target


def equipment_rows(layout):
    rows=[]
    for i,row in enumerate(layout.get('actual_instances',layout.get('instances',[]))):
        bounds=np.asarray(row['world_union_bounds_m'],dtype=float)
        assert bounds.shape==(2,3) and np.isfinite(bounds).all()
        rows.append(dict(path=row.get('prim_path','/World/'+row['floor']+'/EquipmentV4/I%05d'%i),
                         bounds=bounds, floor=row['floor'], asset_id=row['asset_id']))
    return rows


def wall_roots(domains):
    result=set()
    for row in domains.get('static_collider_world_aabbs',[]):
        path=row['prim_path']
        if '/Geometry/wall_' in path:
            result.add(path.split('/Geometry/')[0]+'/Geometry/'+path.split('/Geometry/')[1].split('/')[0])
    assert result,'ACTUAL_PERIMETER_WALL_PATHS_MISSING'
    return sorted(result)


def interior(view_id, floor, region, domain, rows, hide):
    z=float(domain['floors'][floor]['top_z_m']);ceil=float(domain['roof_z_m'])
    if floor=='Floor1' and view_id=='ground_02' and domain.get('upper_slab_bottom_z_m'):
        ceil=float(domain['upper_slab_bottom_z_m'])
    selected=[r for r in rows if r['floor']==floor and region.intersects(box(*r['bounds'][0,:2],*r['bounds'][1,:2]))]
    assert selected,'CAMERA_REGION_HAS_NO_ACTUAL_EQUIPMENT:'+view_id
    static=[r['bounds_xyz_m'] for r in domain.get('static_collider_world_aabbs',[])]
    allbounds=np.array([r['bounds'] for r in rows]+static,dtype=float)
    selected_bounds=np.array([r['bounds'] for r in selected])
    centers=selected_bounds.mean(axis=1)
    if len(centers)>RULES['interior_max_targets']:
        centers=centers[np.linspace(0,len(centers)-1,RULES['interior_max_targets'],dtype=int)]
    center=region.representative_point();target=np.mean(centers,axis=0)
    if not region.covers(Point(*target[:2])):target[:2]=[center.x,center.y]
    target[2]=z+min(RULES['target_height_cap_m'],max(.5,float(np.median(centers[:,2])-z)))
    eyez=min(ceil-RULES['ceiling_clearance_m'], max(z+RULES['interior_eye_min_height_m'],float(selected_bounds[:,1,2].max())+RULES['interior_eye_above_obstacle_m']))
    assert eyez>z+.5,'NO_CAMERA_HEIGHT_BELOW_ACTUAL_CEILING'
    lo,bo,hi,to=region.bounds;n=RULES['interior_grid_n'];points=[]
    for x in np.linspace(lo,hi,n+2)[1:-1]:
        for y in np.linspace(bo,to,n+2)[1:-1]:points.append((float(x),float(y)))
    for aisle in domain.get('reserved_aisles',[]):
        if aisle['floor']!=floor:continue
        a,b,c,d=aisle['bounds']
        for t in np.linspace(.1,.9,RULES['aisle_samples']):
            points.append((a+(c-a)*t,(b+d)/2));points.append(((a+c)/2,b+(d-b)*t))
    candidates=[];tx=RULES['horizontal_aperture_mm']/(2*RULES['focal_length_mm']);ty=tx*720/1280
    for index,(x,y) in enumerate(dict.fromkeys(points)):
        if not region.buffer(-.05).covers(Point(x,y)):continue
        eye=np.array([x,y,eyez]);delta=allbounds.copy()
        horizontal=np.maximum(np.maximum(delta[:,0,:2]-eye[:2],eye[:2]-delta[:,1,:2]),0.)
        distance=np.linalg.norm(horizontal,axis=1)
        overlaps_z=(delta[:,0,2]-.2<eyez)&(delta[:,1,2]+.2>eyez)
        if np.any(overlaps_z & (distance<RULES['near_equipment_exclusion_m'])):continue
        separation=np.linalg.norm(eye[:2]-target[:2])
        if separation<2.:continue
        r,u,f=frame_basis(eye,target);v=centers-eye;depth=v@f
        projected=(depth>.1)&(np.abs(v@r)<depth*tx*.92)&(np.abs(v@u)<depth*ty*.92)
        hits=ray_hits(eye,centers,allbounds)
        # A center's own enclosing box is its intended target, not an occluder.
        endpoints=(centers[:,None,:]>=allbounds[None,:,0,:]-1e-8).all(2)&(centers[:,None,:]<=allbounds[None,:,1,:]+1e-8).all(2)
        visible=projected&~(hits&~endpoints).any(axis=1)
        slabz=(float(domain['upper_slab_bottom_z_m'])+float(domain['floors']['Floor2']['top_z_m']))/2
        slab=shape(domain['floors']['Floor2']['support_geometry'])
        for j,c in enumerate(centers):
            if (eyez-slabz)*(c[2]-slabz)<0:
                xy=eye[:2]+(c[:2]-eye[:2])*(slabz-eyez)/(c[2]-eyez)
                if slab.covers(Point(*xy)):visible[j]=False
        score=(int(visible.sum()),float(separation),-index)
        candidates.append((score,eye,dict(actual_geometry_visible_target_centers=int(visible.sum()),target_center_count=len(centers),candidate_index=index,eye_supported=True)))
    assert candidates,'NO_SUPPORTED_CLEAR_CAMERA_CANDIDATE:'+view_id
    _,eye,evidence=max(candidates,key=lambda a:a[0])
    return dict(id=view_id,role={'ground_01':'ground_double_height','ground_02':'ground_under_mezz','mezzanine_01':'mezzanine_overview'}[view_id],
                eye=eye.tolist(),target=target.tolist(),cutaway=False,hide_prefixes=[],
                keep_prefixes=[],geometry_selection=evidence,support_floor=floor,
                actual_region_bounds_xy_m=list(region.bounds))


def make_plan(layout, domains, stairs, bindings, rules_sha):
    started=time.monotonic();rows=equipment_rows(layout)
    ground=shape(domains['floors']['Floor1']['support_geometry']);upper=shape(domains['floors']['Floor2']['support_geometry'])
    roof=float(domains['roof_z_m']);z0=float(domains['floors']['Floor1']['top_z_m'])
    b=ground.bounds;worldbox=[[b[0],b[1],z0],[b[2],b[3],roof]]
    cuts=['/World/FactoryStructure/Roof']+wall_roots(domains)
    views=[]
    for i,direction in enumerate(RULES['exterior_directions']):
        eye,target=fit(corners(worldbox),direction)
        views.append(dict(id=VIEW_IDS[i],role='exterior' if i==0 else 'exterior_cutaway',eye=eye.tolist(),target=target.tolist(),cutaway=bool(i),hide_prefixes=cuts if i else [],keep_prefixes=[]))
    def region(name,fallback):
        data=domains.get('regions',{}).get(name,{}).get('geometry')
        p=shape(data) if data else fallback
        return p if not p.is_empty else fallback
    views.append(interior('ground_01','Floor1',region('double_height',ground),domains,rows,cuts))
    views.append(interior('ground_02','Floor1',region('under_mezz',ground),domains,rows,cuts))
    views.append(interior('mezzanine_01','Floor2',upper,domains,rows,cuts))
    s=next(r for r in stairs['stairs'] if r['id']=='S01');local=np.asarray(s['native_actual_bounds_m']);angle=math.radians(float(s['rotation_deg']))
    rot=np.array([[math.cos(angle),-math.sin(angle),0.],[math.sin(angle),math.cos(angle),0.],[0.,0.,1.]])
    pts=corners(local)@rot.T+np.asarray(s['offset_m']);path=np.asarray(s['world_path']);ascent=path[1,:2]-path[0,:2];ascent/=np.linalg.norm(ascent)
    right=np.array([ascent[1],-ascent[0]]);direction=np.r_[-ascent+.85*right,.65]
    eye,target=fit(pts,direction)
    inset=RULES['stair_target_inset_fraction'];center=pts.mean(axis=0)
    probes=np.vstack([center,center+(pts-center)*(1-inset)])
    hit=ray_hits(eye,probes,[r['bounds'] for r in rows],RULES['stair_occluder_padding_m']).any(axis=0)
    hidden=[r['path'] for r,h in zip(rows,hit) if h and not r['path'].startswith('/World/Stair/S01')]
    views.append(dict(id='stair_01',role='S01_solid_flight_and_handrail',eye=eye.tolist(),target=target.tolist(),cutaway=True,
        hide_prefixes=cuts+['/World/Cut/Floor2Slab']+sorted(hidden),keep_prefixes=[s.get('prim_path','/World/Stair/S01')],
        stair_id='S01',actual_stair_bounds_m=[pts.min(axis=0).tolist(),pts.max(axis=0).tolist()],
        occlusion_rule='finite eye-to-bbox center/8 inset-corner segments vs actual equipment AABBs padded .2m',hidden_equipment_count=len(hidden)))
    assert [v['id'] for v in views]==VIEW_IDS
    assert all(np.isfinite(v['eye']+v['target']).all() for v in views)
    return dict(schema='Factory80AutomaticCameraPlanV1',tool=VERSION,building=layout['building'],
        camera_rules_sha256=rules_sha,source_sha256=digest(__file__),input_sha256=bindings,
        resolution=RULES['resolution'],intrinsics={k:RULES[k] for k in ['focal_length_mm','horizontal_aperture_mm','clipping_range_m']},
        views=views,collision_gray=dict(camera_from='exterior_02_cutaway',hide_prefixes=cuts),
        image_scoring='NOT_EXECUTED',rendering='NOT_EXECUTED',elapsed_s=time.monotonic()-started)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--write-rules',type=Path);ap.add_argument('--rules',type=Path)
    ap.add_argument('--layout',type=Path);ap.add_argument('--domains',type=Path);ap.add_argument('--stairs',type=Path);ap.add_argument('--out',type=Path)
    args=ap.parse_args()
    if args.write_rules:
        with args.write_rules.open('x',encoding='utf8') as f:json.dump(RULES,f,indent=2,ensure_ascii=False)
        print(json.dumps(dict(camera_rules=str(args.write_rules),sha256=digest(args.write_rules))));return
    assert args.rules and args.layout and args.domains and args.stairs and args.out
    assert json.loads(args.rules.read_text())==RULES,'FROZEN_CAMERA_RULES_DIFFER_FROM_TOOL'
    paths=[args.layout,args.domains,args.stairs]
    result=make_plan(*(json.loads(p.read_text()) for p in paths),{str(p.resolve()):digest(p) for p in paths},digest(args.rules))
    with args.out.open('x',encoding='utf8') as f:json.dump(result,f,indent=2,ensure_ascii=False,allow_nan=False)
    print(json.dumps(dict(output=str(args.out),sha256=digest(args.out),views=VIEW_IDS,elapsed_s=result['elapsed_s'])))


if __name__=='__main__':main()
