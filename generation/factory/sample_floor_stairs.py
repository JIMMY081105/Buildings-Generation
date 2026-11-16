"""Author two sample floors and five independently generated stairs; no equipment/gate claims."""
from pathlib import Path
import argparse, json, hashlib, math, datetime, copy
from collections import Counter
import numpy as np
from shapely.geometry import box, Point, Polygon
from shapely.ops import unary_union
from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, UsdUtils, Sdf, Gf, Vt
from retain_architecture import canonical
P=Path('/cephfs/shared/yixing/outputs/factory80_20261011/pipeline');R=P.parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
ref=lambda p:dict(path=str(p),sha256=sha(p))
read=lambda p:json.loads(Path(p).read_text())

def write(path,value):
    with Path(path).open('x') as stream:json.dump(value,stream,indent=2,allow_nan=False)

def enabled(prim):
    return prim.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False

def world_vertices(prim,cache):
    m=UsdGeom.Mesh(prim);mat=cache.GetLocalToWorldTransform(prim)
    return np.asarray([tuple(mat.Transform(Gf.Vec3d(*map(float,x)))) for x in m.GetPointsAttr().Get()])

def rotation(angle):
    return [np.array([[1,0],[0,1]]),np.array([[0,-1],[1,0]]),np.array([[-1,0],[0,-1]]),np.array([[0,1],[-1,0]])][int(angle//90)]

def rank(cfg,seed,*parts):
    s=cfg['seed_policy_v3'];text='/'.join(map(str,[s['seed_namespace'],s['base_seed'],seed,*parts]))
    return int.from_bytes(hashlib.sha256(text.encode()).digest(),'big')

def transform(points,rot,offset):
    p=np.asarray(points,dtype=float).copy();p[:,:2]=p[:,:2]@rot.T+np.asarray(offset)[:2];p[:,2]+=offset[2]
    return p

def rect_corners(bounds):
    x0,y0,x1,y1=bounds
    return np.array([[x0,y0],[x1,y0],[x1,y1],[x0,y1]])

def transformed_bounds(lo,hi,rot,offset):
    q=rect_corners([*lo[:2],*hi[:2]])@rot.T+offset[:2]
    return [*q.min(0),*q.max(0)]

def corridors(W,H,edge,depth,pol):
    lower=box(0,0,W,H);upper=box(0,0,W,depth) if edge=='y_min' else box(0,H-depth,W,H)
    main=pol['main_aisle_width_m'];cross=pol['cross_aisle_width_m'];offset=pol['cross_aisle_boundary_offset_m']
    vx=W/2+5.;gy=H-5.25 if edge=='y_min' else 5.25;uy=5.25 if edge=='y_min' else H-5.25
    result=[]
    for floor,poly,y in [('Floor1',lower,gy),('Floor2',upper,uy)]:
        x0,y0,x1,y1=poly.bounds
        result.extend([dict(floor=floor,kind='main_X',bounds=[x0,y-main/2,x1,y+main/2]),dict(floor=floor,kind='main_Y',bounds=[vx-main/2,y0,vx+main/2,y1])])
        for i in range(1,round(W/20)):
            x=20*i+offset;result.append(dict(floor=floor,kind='cross_Y',bounds=[x-cross/2,y0,x+cross/2,y1]))
        for j in range(1,round(H/21)):
            y=21*j+offset;strip=box(x0,y-cross/2,x1,y+cross/2)
            if poly.covers(strip):result.append(dict(floor=floor,kind='cross_X',bounds=list(strip.bounds)))
    return result

def make_plan(item,cfg,pol,seed):
    W,H=item['ground']['actual_xy_bounds'][2:];assert item['ground']['actual_xy_bounds'][:2]==[0.,0.]
    assert abs(W*H-item['ground']['actual_top_area_m2'])<1e-6
    bid=item['building'];edge=['y_min','y_max'][rank(cfg,seed,bid,'mezz_edge')%2];minimum_depth=H*pol['slab_net_fraction']
    provisional=box(0,0,W,minimum_depth) if edge=='y_min' else box(0,H-minimum_depth,W,H)
    reserved=corridors(W,H,edge,minimum_depth,pol);res_polys=[box(*x['bounds']) for x in reserved]
    cols=[box(*c['min'][:2],*c['max'][:2]) for c in item['columns']]
    placed=[];used_bay_x=set();selection_counts=[]
    for stair in item['stairs']:
        choices=[]
        for i in range(round(W/10)):
            for j in range(math.floor(minimum_depth/10.5)):
                for angle in pol['placement_candidate_rotations_deg']:
                    choices.append((rank(cfg,seed,bid,stair['stair_index'],i,j,angle),i,j,angle))
        rejected=Counter();chosen=None
        for _,i,j,angle in sorted(choices):
            center=np.array([5+10*i,5.25+10.5*j]);center[1]=center[1] if edge=='y_min' else H-center[1]
            bay_x=int(center[0]//20)
            if pol['distinct_bay_columns_for_sample_stairs'] and bay_x in used_bay_x:rejected['bay_column_already_used']+=1;continue
            rot=rotation(angle);lo=np.array(stair['full_bounds_min']);hi=np.array(stair['full_bounds_max']);offset=np.r_[center-rot@((lo[:2]+hi[:2])/2),0.]
            bounds=transformed_bounds(lo,hi,rot,offset);foot=box(*bounds)
            if not provisional.buffer(-pol['minimum_full_stair_bbox_to_perimeter_m']).covers(foot):rejected['outside_provisional_upper_strip']+=1;continue
            if any(foot.intersects(c) for c in res_polys):rejected['reserved_aisle']+=1;continue
            if any(foot.distance(c)<pol['minimum_full_stair_bbox_to_actual_column_m'] for c in cols):rejected['actual_column']+=1;continue
            if any(foot.distance(box(*p['actual_full_stair_bounds_xy']))<pol['minimum_stair_bbox_separation_m'] for p in placed):rejected['other_stair']+=1;continue
            qlo=stair['tread_exit_frame_min'];qhi=stair['tread_exit_frame_max'];ql=qlo[0]-pol['opening_lateral_extra_each_side_m'];qh=qhi[0]+pol['opening_lateral_extra_each_side_m'];al=qlo[1]-pol['opening_rear_extra_m'];ah=pol['opening_forward_end_relative_to_final_riser_m']
            iface=np.array(stair['floor_integration']['final_riser_interface_xy_m']);axis=np.array(stair['ascent_xy']);side=np.array(stair['side_xy'])
            local=iface+np.array([[ql,al],[qh,al],[qh,ah],[ql,ah]])@np.stack([side,axis])
            world=local@rot.T+offset[:2];opening=box(*world.min(0),*world.max(0))
            if not provisional.covers(opening) or any(opening.intersects(c) for c in res_polys):rejected['opening_or_aisle']+=1;continue
            pts=transform(stair['path_points'],rot,offset);interface=iface@rot.T+offset[:2];ascent=axis@rot.T;cross=side@rot.T
            native=next(x for b in read(pol['prelayout_inputs']['path'])['items'] if b['building']==bid for x in b['current_native_stairs'] if x['stair_index']==stair['stair_index'])
            width=native['native_dimensions']['staircase_width_m'];length=pol['upper_forward_landing_patch_length_m']
            patch=Polygon(interface+np.array([[-width/2,0.005],[width/2,0.005],[width/2,length],[-width/2,length]])@np.stack([cross,ascent]))
            if not provisional.covers(patch) or patch.intersection(opening).area>1e-8:rejected['unsupported_forward_landing']+=1;continue
            prior_holes=[box(*p['opening_rect']) for p in placed]
            if any(opening.intersects(h) or patch.intersects(h) for h in prior_holes):rejected['other_opening']+=1;continue
            if any(opening.intersects(Polygon(p['upper_forward_landing_patch_xy'])) for p in placed):rejected['other_landing']+=1;continue
            first=np.array(stair['path_points'][0]);first_next=np.array(stair['path_points'][1]);initial_axis=first_next[:2]-first[:2];initial_axis/=np.linalg.norm(initial_axis)
            initial=first.copy();initial[:2]-=initial_axis*native['native_dimensions']['tread_depth_m']/2;initial[2]=0.;entry=transform([initial],rot,offset)[0]
            chosen=dict(id=stair['id'],stair_index=stair['stair_index'],family=stair['family'],seed=stair['seed'],root=f"/World/Stair/S{stair['stair_index']:02d}",native_reference_root=f"/World/Stair/S{stair['stair_index']:02d}/Native",asset_stage=stair['asset_stage'],metrics=stair['metrics'],traversal=stair['traversal'],native_dimensions=native['native_dimensions'],rotation_deg=angle,offset_m=offset.tolist(),opening_bay_xy=[bay_x,int(center[1]//21)],selected_column_half_cell=[i,j],actual_full_stair_bounds_xy=bounds,
                opening_rect=list(opening.bounds),opening_exit_frame_bounds=[ql,al,qh,ah],final_riser_interface_world_xy_m=interface.tolist(),ascent_world_xy=ascent.tolist(),world_path=pts.tolist(),entry_floor_xyz_m=entry.tolist(),exit_floor_xyz_m=pts[-1].tolist(),
                equipment_keepouts=[dict(floor='Floor1',center_xy=entry[:2].tolist(),radius_m=pol['entry_exit_equipment_keepout_radius_m']),dict(floor='Floor2',center_xy=pts[-1,:2].tolist(),radius_m=pol['entry_exit_equipment_keepout_radius_m'])],upper_forward_landing_patch_xy=np.asarray(patch.exterior.coords).tolist(),prior_native_failure_budgets_preserved=True)
            used_bay_x.add(bay_x);break
        assert chosen is not None,'No preregistered collision/aisle-safe sample placement: '+stair['id']+' '+str(dict(rejected))
        placed.append(chosen);selection_counts.append(dict(id=stair['id'],spatial_candidates_rejected_before_first_layout=dict(rejected)))
    holes=unary_union([box(*s['opening_rect']) for s in placed]);depth=(pol['slab_net_fraction']*W*H+holes.area)/W
    slab=box(0,0,W,depth) if edge=='y_min' else box(0,H-depth,W,H);net=slab.difference(holes)
    assert net.geom_type=='Polygon' and net.is_valid and len(net.interiors)==len(placed)
    actual_corridors=corridors(W,H,edge,depth,pol)
    for aisle in actual_corridors:
        poly=box(*aisle['bounds']);assert (box(0,0,W,H) if aisle['floor']=='Floor1' else net).covers(poly),'Reserved aisle not supported'
        assert all(not box(*s['actual_full_stair_bounds_xy']).intersects(poly) for s in placed),'Stair intersects actual final aisle'
    return dict(schema='Factory80NewMezzanineStairLayoutV1',building=bid,building_seed=seed,layout_attempt=0,strip_edge=edge,gross_slab_rect=list(slab.bounds),new_opening_union_area_m2=holes.area,target_net_slab_area_m2=net.area,ground_area_m2=W*H,slab_z_bounds_m=[item['historical_upper_z_range'][0],pol['slab_top_z_m']],stairs=placed,reserved_aisles=actual_corridors,spatial_search_diagnostics=selection_counts,equipment_layout='NOT_AUTHORED',actual_asset_style_bundle='NOT_ASSIGNED')

def floor_mesh(plan,pol):
    outer=plan['gross_slab_rect'];holes=[s['opening_rect'] for s in plan['stairs']]
    def nodes(axis):
        raw=sorted(set([outer[axis],outer[axis+2]]+[v for h in holes for v in [h[axis],h[axis+2]]]))
        out=[]
        for a,b in zip(raw,raw[1:]):
            n=math.ceil((b-a)/pol['maximum_slab_grid_interval_m']);out.extend(a+(b-a)*i/n for i in range(n))
        return np.array(out+[raw[-1]],dtype=np.float32)
    xs,ys=nodes(0),nodes(1);nx,ny=len(xs)-1,len(ys)-1;assert np.all(np.diff(xs)>0) and np.all(np.diff(ys)>0)
    occupied=np.ones((nx,ny),bool)
    for i in range(nx):
        for j in range(ny):
            x,y=(float(xs[i])+float(xs[i+1]))/2,(float(ys[j])+float(ys[j+1]))/2
            occupied[i,j]=not any(h[0]<x<h[2] and h[1]<y<h[3] for h in holes)
    points=[];lookup={};faces=[]
    def vertex(i,j,k):
        key=(i,j,k)
        if key not in lookup:lookup[key]=len(points);points.append([float(xs[i]),float(ys[j]),plan['slab_z_bounds_m'][k]])
        return lookup[key]
    for i,j in zip(*np.nonzero(occupied)):
        a,b,c,d=(vertex(i,j,1),vertex(i+1,j,1),vertex(i+1,j+1,1),vertex(i,j+1,1));aa,bb,cc,dd=(vertex(i,j,0),vertex(i+1,j,0),vertex(i+1,j+1,0),vertex(i,j+1,0))
        faces.extend([[a,b,c,d],[aa,dd,cc,bb]])
        if j==0 or not occupied[i,j-1]:faces.append([aa,bb,b,a])
        if i==nx-1 or not occupied[i+1,j]:faces.append([bb,cc,c,b])
        if j==ny-1 or not occupied[i,j+1]:faces.append([cc,dd,d,c])
        if i==0 or not occupied[i-1,j]:faces.append([dd,aa,a,d])
    return np.asarray(points,dtype=np.float32),[4]*len(faces),np.asarray(faces,dtype=np.int32).reshape(-1).tolist()

def define_mesh(stage,path,v,counts,indices,collision):
    m=UsdGeom.Mesh.Define(stage,path);m.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(v));m.CreateFaceVertexCountsAttr(counts);m.CreateFaceVertexIndicesAttr(indices);m.CreateSubdivisionSchemeAttr('none');m.CreateDoubleSidedAttr(True);m.CreateExtentAttr([Gf.Vec3f(*map(float,v.min(0))),Gf.Vec3f(*map(float,v.max(0)))])
    if collision:
        m.CreatePurposeAttr('proxy');UsdPhysics.CollisionAPI.Apply(m.GetPrim()).CreateCollisionEnabledAttr(True);UsdPhysics.MeshCollisionAPI.Apply(m.GetPrim()).CreateApproximationAttr('none')
    return m

def mesh_top(prim):
    assert prim and prim.IsActive() and prim.IsA(UsdGeom.Mesh),'Actual slab mesh missing'
    mesh=UsdGeom.Mesh(prim);v=world_vertices(prim,UsdGeom.XformCache());counts=list(mesh.GetFaceVertexCountsAttr().Get());indices=list(mesh.GetFaceVertexIndicesAttr().Get());assert len(v) and counts and sum(counts)==len(indices),'Actual slab mesh empty'
    top=float(v[:,2].max());polys=[];off=0;edges=Counter();volume=0.
    for n in counts:
        ids=indices[off:off+n];off+=n;face=v[ids]
        for a,b in zip(ids,ids[1:]+ids[:1]):edges[tuple(sorted([a,b]))]+=1
        for i in range(1,n-1):volume+=float(np.dot(face[0],np.cross(face[i],face[i+1])))/6
        if np.ptp(face[:,2])<1e-6 and abs(face[0,2]-top)<1e-6:
            poly=Polygon(face[:,:2]);assert poly.is_valid and poly.area>0;polys.append(poly)
    assert polys,'Actual slab has no top faces'
    union=unary_union(polys)
    assert all(c==2 for c in edges.values()),'Slab mesh is not closed two-face-per-edge'
    assert abs(sum(p.area for p in polys)-union.area)<1e-5,'Overlapping actual slab top faces'
    assert volume>0,'Slab winding is not outward'
    return union,dict(top_z_m=top,bottom_z_m=float(v[:,2].min()),actual_top_union_area_m2=union.area,vertices=len(v),faces=len(counts),signed_volume_m3=volume,all_edges_have_two_faces=True)

def verify(stage,base,plan,cfg,pol):
    tol=pol['geometry_readback_tolerance_m'];xc=UsdGeom.XformCache();bc=UsdGeom.XformCache()
    assert UsdGeom.GetStageMetersPerUnit(stage)==1 and UsdGeom.GetStageUpAxis(stage)=='Z'
    for prim in base.Traverse():
        other=stage.GetPrimAtPath(prim.GetPath());assert other and other.IsActive() and other.IsDefined(),'Retained architecture missing: '+str(prim.GetPath())
        assert prim.GetTypeName()==other.GetTypeName() and prim.GetAppliedSchemas()==other.GetAppliedSchemas(),'Retained schema changed'
        for attr in prim.GetAttributes():
            b=other.GetAttribute(attr.GetName());assert b and canonical(attr.Get())==canonical(b.Get()) and attr.GetConnections()==b.GetConnections(),'Retained architecture attributes changed'
        assert {r.GetName():list(r.GetTargets()) for r in prim.GetRelationships()}=={r.GetName():list(r.GetTargets()) for r in other.GetRelationships()},'Retained relationships changed'
        if prim.IsA(UsdGeom.Xformable):assert canonical(bc.GetLocalToWorldTransform(prim))==canonical(xc.GetLocalToWorldTransform(other)),'Retained world transform changed'
    visual=stage.GetPrimAtPath('/World/Cut/Floor2Slab/Visual');collision=stage.GetPrimAtPath('/World/Cut/Floor2Slab/Collision')
    assert collision and enabled(collision),'Actual upper slab collision missing/disabled'
    upper,vr=mesh_top(visual);physical,cr=mesh_top(collision)
    holes=unary_union([box(*s['opening_rect']) for s in plan['stairs']]);expected=box(*plan['gross_slab_rect']).difference(holes)
    for s in plan['stairs']:
        inset=box(*s['opening_rect']).buffer(-tol)
        assert upper.intersection(inset).area<1e-8 and physical.intersection(inset).area<1e-8,'Actual opening blocked: '+s['id']
    assert upper.symmetric_difference(physical).area<1e-8,'Actual visual/collision slab mismatch'
    assert upper.symmetric_difference(expected).area<tol*sum(expected.bounds[2:])*4,'Actual upper outline differs from registered plan'
    ratio=physical.area/plan['ground_area_m2'];assert cfg['physical_v3']['mezzanine_coverage_fraction'][0]<=ratio<=cfg['physical_v3']['mezzanine_coverage_fraction'][1]
    assert abs(ratio-pol['slab_net_fraction'])<=pol['net_fraction_measurement_tolerance'],'Actual net upper fraction differs from registered target'
    assert abs(vr['top_z_m']-pol['slab_top_z_m'])<tol and abs(vr['bottom_z_m']-plan['slab_z_bounds_m'][0])<tol
    assert not collision.HasAPI(UsdPhysics.RigidBodyAPI) and UsdPhysics.MeshCollisionAPI(collision).GetApproximationAttr().Get()=='none'
    m0,m1=UsdGeom.Mesh(visual),UsdGeom.Mesh(collision)
    for get in ['GetPointsAttr','GetFaceVertexCountsAttr','GetFaceVertexIndicesAttr']:
        assert canonical(getattr(m0,get)().Get())==canonical(getattr(m1,get)().Get()),'Actual visual/collision mesh array mismatch'
    source_records=[]
    for stair in plan['stairs']:
        source=Usd.Stage.Open(stair['asset_stage']['path']);rot=rotation(stair['rotation_deg']);offset=np.array(stair['offset_m']);sxc=UsdGeom.XformCache();colliders=0;maximum_error=0.;meshes=0
        for old in source.Traverse():
            path=stair['native_reference_root']+str(old.GetPath()).removeprefix('/Asset');new=stage.GetPrimAtPath(path)
            assert new and new.IsActive()==old.IsActive() and new.GetAppliedSchemas()==old.GetAppliedSchemas(),'Native prim/schema coverage mismatch: '+path
            if old.HasAPI(UsdPhysics.RigidBodyAPI):assert UsdPhysics.RigidBodyAPI(old).GetRigidBodyEnabledAttr().Get() is False,'Unexpected enabled native rigid body'
            if not old.IsA(UsdGeom.Mesh):continue
            meshes+=1;assert old.GetTypeName()==new.GetTypeName()
            for attr in old.GetAttributes():
                if attr.GetName() in ['extent','points','normals','faceVertexCounts','faceVertexIndices'] or attr.GetName().startswith('primvars:'):
                    assert canonical(attr.Get())==canonical(new.GetAttribute(attr.GetName()).Get()),'Native local geometry/topology/UV changed: '+path
            expected_world=transform(world_vertices(old,sxc),rot,offset);actual=world_vertices(new,xc);assert expected_world.shape==actual.shape
            error=float(np.max(abs(expected_world-actual)));maximum_error=max(maximum_error,error)
            assert np.isfinite(actual).all() and error<tol,'Native placed world geometry mismatch: '+path
            assert enabled(old)==enabled(new),'Native collision enabled state changed'
            colliders+=enabled(new)
        for point in stair['world_path'][:-1]:
            if point[2]+cfg['standability_pinned']['body_height_m']>=cr['bottom_z_m'] and point[2]<vr['top_z_m']-tol:
                assert not physical.covers(Point(*point[:2])),'Actual new slab blocks a high native traversal point'
        end=stair['world_path'][-1];assert physical.covers(Point(*end[:2])),'Actual final native landing lacks upper support'
        assert physical.buffer(tol).covers(Polygon(stair['upper_forward_landing_patch_xy'])),'Actual forward landing patch lacks support'
        source_records.append(dict(id=stair['id'],source=stair['asset_stage'],placed_root=stair['root'],mesh_count=meshes,enabled_colliders=colliders,maximum_world_pose_error_m=maximum_error,local_geometry_topology_normals_uv_exactly_preserved=True,actual_last_native_point_on_upper_slab=True,high_native_path_points_not_blocked_by_new_slab=True))
    for aisle in plan['reserved_aisles']:
        poly=box(*aisle['bounds'])
        if aisle['floor']=='Floor2':assert physical.buffer(tol).covers(poly),'Actual upper aisle lost support'
        for stair in plan['stairs']:assert not box(*stair['actual_full_stair_bounds_xy']).intersects(poly),'Actual stair bbox intersects reserved aisle'
    actual_colliders=sum(enabled(p) for p in stage.Traverse());expected_colliders=sum(enabled(p) for p in base.Traverse())+1+sum(x['enabled_colliders'] for x in source_records)
    assert actual_colliders==expected_colliders,'Unexpected scene collider count'
    return dict(upper_visual=vr,upper_collision=cr,actual_ground_top_area_m2=plan['ground_area_m2'],actual_net_upper_fraction=ratio,actual_two_floor_area_m2=plan['ground_area_m2']+physical.area,repeat_cap=8*math.ceil((plan['ground_area_m2']+physical.area)/5500),expected_static_colliders=expected_colliders,actual_enabled_colliders=actual_colliders,retained_architecture_unchanged=True,source_stair_checks=source_records,reserved_aisles_supported_and_not_obstructed_by_stair_bbox=True,equipment_3m_keepouts='REGISTERED_ACTUAL_ENDPOINTS_NO_EQUIPMENT_AUTHORED',whole_building_gates='NOT_EXECUTED',native_physics='NOT_EXECUTED')

def clone(stage):
    source=stage.GetRootLayer();layer=Sdf.Layer.CreateAnonymous();layer.TransferContent(source);layer.subLayerPaths=[Sdf.ComputeAssetPathRelativeToLayer(source,p) for p in source.subLayerPaths];return Usd.Stage.Open(layer)

def controls(stage,base,plan,cfg,pol):
    verify(clone(stage),base,plan,cfg,pol)  # Prove the negative scene composes the actual positive before mutation.
    results=[]
    cases=[('opening_sealed','Actual opening blocked'),('upper_collision_disabled','Actual upper slab collision missing'),('native_stair_pose_moved','Native placed world geometry mismatch'),('retained_wall_deactivated','Retained architecture missing'),('upper_visual_vertex_moved','Actual visual/collision')]
    for name,fragment in cases:
        negative=clone(stage);negative.SetEditTarget(negative.GetSessionLayer())
        if name=='opening_sealed':
            # Replace both actual upper surfaces with a closed gross rectangle, sealing every hole.
            full=copy.deepcopy(plan);full['stairs']=[];v,c,i=floor_mesh(full,pol)
            for path in ['/World/Cut/Floor2Slab/Visual','/World/Cut/Floor2Slab/Collision']:
                mesh=UsdGeom.Mesh(negative.GetPrimAtPath(path));mesh.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(v));mesh.GetFaceVertexCountsAttr().Set(c);mesh.GetFaceVertexIndicesAttr().Set(i)
        elif name=='upper_collision_disabled':UsdPhysics.CollisionAPI(negative.GetPrimAtPath('/World/Cut/Floor2Slab/Collision')).GetCollisionEnabledAttr().Set(False)
        elif name=='native_stair_pose_moved':
            root=plan['stairs'][0]['root'];prim=negative.GetPrimAtPath(root);op=UsdGeom.Xformable(prim).GetOrderedXformOps()[0];p=op.Get();op.Set(Gf.Vec3d(p[0]+0.02,p[1],p[2]))
        elif name=='retained_wall_deactivated':
            wall=next(p for p in base.Traverse() if p.GetName()=='wall_0_0');negative.OverridePrim(wall.GetPath()).SetActive(False)
        else:
            mesh=UsdGeom.Mesh(negative.GetPrimAtPath('/World/Cut/Floor2Slab/Visual'));p=mesh.GetPointsAttr().Get();p[0]=Gf.Vec3f(p[0][0]+0.02,p[0][1],p[0][2]);mesh.GetPointsAttr().Set(p)
        try:verify(negative,base,plan,cfg,pol)
        except AssertionError as error:
            # A shifted surface can first fail closed topology/winding; still require a geometry failure.
            if name=='upper_visual_vertex_moved':assert any(s in str(error) for s in ['Actual visual/collision','Slab','Actual opening blocked','Actual upper outline','Overlapping actual slab top faces']),str(error)
            else:assert fragment in str(error),'Negative rejected for unrelated reason: '+str(error)
            results.append(dict(control=name,status='FAIL_AS_REQUIRED',rejection=str(error)))
        else:
            stop=R/'CRITICAL_STOP_V3_MEZZ_STAIR_NEGATIVE.json';write(stop,dict(status='ENTIRE_TASK_STOP_REQUIRED',building=plan['building'],control=name));raise RuntimeError('Actually executed new mezz/stair negative escaped')
    return results

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--building',required=True);a=ap.parse_args();config_ref=ref(P/'config.yaml');cfg=read(P/'config.yaml');pol=cfg['sample_mezz_stair_integration_v3'];assert a.building in pol['scope_buildings'] and not cfg['approval']['batch_release']
    known=read(P/'evidence/G13_MEASURED_SEAL_FULL_QUALIFICATION_V1/STOP_RESOLUTION.json')['old_stop_files_preserved']
    for folder in [R,P]:
        for stop in folder.glob('CRITICAL_STOP*.json'):assert str(stop) in known and sha(stop)==known[str(stop)],'Unresolved global stop'
    assert sha('/cephfs/shared/yixing/outputs/diversity54_20260908/scripts/generate_staircase_diversity_v4.py')=='eac63625979db761d21f06bf25a0fdce5b953ea33af0a200feb2e29e2f0211ad'
    reg=read(P/'evidence/V3_MEZZ_STAIR_POLICY_V4/REGISTRATION_RECEIPT.json');assert reg['canonical_config']==config_ref
    prior=read(reg['previous_config']['path']);check=copy.deepcopy(cfg);del check['sample_mezz_stair_integration_v3'];prior.pop('sample_mezz_stair_integration_v3');assert check==prior
    for source in [pol['actual_geometry_inputs'],pol['prelayout_inputs']]:assert ref(source['path'])==source
    geometry=next(x for x in read(pol['actual_geometry_inputs']['path'])['items'] if x['building']==a.building)
    pre=next(x for x in read(pol['prelayout_inputs']['path'])['items'] if x['building']==a.building)
    bindings=[geometry['retained_architecture'],pre['actual_source_inspection'],pre['retained_architecture_readback']]
    for stair in geometry['stairs']:bindings.extend([stair['asset_stage'],stair['metrics'],stair['traversal']])
    assert all(ref(x['path'])==x for x in bindings)
    dependency_files={}
    for source in [geometry['retained_architecture']]+[s['asset_stage'] for s in geometry['stairs']]:
        layers,assets,missing=UsdUtils.ComputeAllDependencies(Sdf.AssetPath(source['path']));assert not missing
        for layer in layers:
            if layer.anonymous:continue
            path=Path(layer.realPath.split('[',1)[0]);assert path.is_file();dependency_files[str(path)]=sha(path)
        for asset in assets:
            path=Path(str(asset));assert path.is_file(),'Resource dependency is not a real file: '+str(path);dependency_files[str(path)]=sha(path)
    seed=read(R/'runs'/a.building/'STATUS.json')['seed'];plan=make_plan(geometry,cfg,pol,seed)
    assert plan==make_plan(geometry,cfg,pol,seed),'Same seed did not reproduce identical actual plan'
    out=R/'runs'/a.building/'v3_sample'/pol['output_subdirectory'];root=out/pol['root_filename'];layerpath=out/'integration.usda'
    if out.exists():
        assert a.building=='004' and root.exists(),'Unexpected partial output directory'
        assert all(ref(x['path'])==x for x in reg['unchanged_authored_geometry_and_records']), 'Previously authored geometry changed'
        old_layout=read(out/'LAYOUT.json');assert all(old_layout[k]==v for k,v in plan.items()),'Control construction fix changed deterministic layout'
    else:
        out.mkdir(parents=True,exist_ok=False)
        write(out/'LAYOUT.json',dict(canonical_config=config_ref,policy_registration=ref(P/'evidence/V3_MEZZ_STAIR_POLICY_V4/REGISTRATION_RECEIPT.json'),**plan))
        layerpath=out/'integration.usda';layer=Sdf.Layer.CreateNew(str(layerpath));st=Usd.Stage.Open(layer);UsdGeom.SetStageMetersPerUnit(st,1.0);UsdGeom.SetStageUpAxis(st,'Z');w=UsdGeom.Xform.Define(st,'/World');st.SetDefaultPrim(w.GetPrim())
        v,c,i=floor_mesh(plan,pol);visual=define_mesh(st,'/World/Cut/Floor2Slab/Visual',v,c,i,False);define_mesh(st,'/World/Cut/Floor2Slab/Collision',v,c,i,True)
        material=UsdShade.Material.Define(st,'/World/V3MezzLooks/Concrete');shader=UsdShade.Shader.Define(st,str(material.GetPath())+'/Surface');shader.CreateIdAttr('UsdPreviewSurface')
        shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*pol['material']['diffuseColor']));shader.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(pol['material']['roughness']);shader.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(pol['material']['metallic']);material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),'surface');UsdShade.MaterialBindingAPI.Apply(visual.GetPrim()).Bind(material)
        for stair in plan['stairs']:
            xf=UsdGeom.Xform.Define(st,stair['root']);xf.SetResetXformStack(True);xf.AddTranslateOp().Set(Gf.Vec3d(*stair['offset_m']));xf.AddRotateZOp().Set(float(stair['rotation_deg']))
            native=st.DefinePrim(stair['native_reference_root']);native.GetReferences().AddReference(stair['asset_stage']['path'],'/Asset')
            xf.GetPrim().CreateAttribute('factory80:independentNativeSeed',Sdf.ValueTypeNames.Int,custom=True).Set(stair['seed'])
            write(out/(stair['id']+'_WORLD_TRAVERSAL.json'),dict(schema='NativeTraversalPlacedRigidlyV1',points_xyz_m=stair['world_path'],original_traversal=stair['traversal'],stair_root=stair['root'],original_geometry_and_path_unchanged=True))
        layer.Save();root=out/pol['root_filename'];rootlayer=Sdf.Layer.CreateNew(str(root));rootlayer.subLayerPaths=['integration.usda',geometry['retained_architecture']['path']];rootlayer.defaultPrim='World';rootlayer.pseudoRoot.SetInfo('metersPerUnit',1.0);rootlayer.pseudoRoot.SetInfo('upAxis','Z');rootlayer.Save()
    candidate=Usd.Stage.Open(str(root));base=Usd.Stage.Open(geometry['retained_architecture']['path']);positive=verify(candidate,base,plan,cfg,pol);negative=controls(candidate,base,plan,cfg,pol)
    _,assets,missing=UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(root)));assert not missing,'New root resource dependencies missing'
    assert all(ref(x['path'])==x for x in bindings) and ref(P/'config.yaml')==config_ref
    assert all(sha(path)==digest for path,digest in dependency_files.items()),'Underlying source/resource changed during authoring'
    receipt=dict(status='NEW_MEZZANINE_OPENINGS_AND_NATIVE_STAIRS_AUTHORED_SOURCE_BOUND_READBACK_PASS_COMPLETE_BUILDING_PENDING',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),building=a.building,canonical_config=config_ref,root=ref(root),integration_layer=ref(layerpath),actual_layout=ref(out/'LAYOUT.json'),retained_architecture=geometry['retained_architecture'],actual_source_bindings=bindings,unchanged_source_and_resource_files=dependency_files,positive_readback=positive,actual_negative_controls=negative,missing_dependencies=[],resource_dependency_count=len(assets),same_seed_plan_exact_replay=True,prior_failure_budgets_reset=False,geometry_derived_scratch_graph_and_building_gates='REBUILD_REQUIRED_NOT_EXECUTED',equipment_layout='NOT_AUTHORED',complete_sample_views='NOT_EXECUTED',sample_quick_checks='NOT_EXECUTED',native_physics='NOT_EXECUTED',batch_release=False)
    write(out/pol['receipt_filename'],receipt)
    print(json.dumps(dict(status=receipt['status'],building=a.building,readback=ref(out/pol['receipt_filename']),actual_net_area_m2=positive['upper_collision']['actual_top_union_area_m2'],actual_fraction=positive['actual_net_upper_fraction'],placed_stairs=len(plan['stairs']),actual_colliders=positive['actual_enabled_colliders'],controls_rejected=len(negative))),flush=True)
if __name__=='__main__':main()
