"""Independent triangle-based floor/placement audit; diagnostic plot is not a building render."""
from pathlib import Path
import json,hashlib,datetime
import numpy as np
from shapely.geometry import Polygon,Point,box
from shapely.ops import unary_union
from pxr import Usd,UsdGeom,UsdPhysics,Sdf,Gf
P=Path('/cephfs/shared/yixing/outputs/factory80_20261011/pipeline');R=P.parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();ref=lambda p:dict(path=str(p),sha256=sha(p));read=lambda p:json.loads(Path(p).read_text())

def mesh_arrays(prim):
    assert prim and prim.IsActive() and prim.IsA(UsdGeom.Mesh),'Actual audit mesh missing'
    mesh=UsdGeom.Mesh(prim);matrix=UsdGeom.XformCache().GetLocalToWorldTransform(prim)
    v=np.asarray([tuple(matrix.Transform(Gf.Vec3d(float(x[0]),float(x[1]),float(x[2])))) for x in mesh.GetPointsAttr().Get()]);faces=[];offset=0
    for n in mesh.GetFaceVertexCountsAttr().Get():
        face=mesh.GetFaceVertexIndicesAttr().Get()[offset:offset+n];offset+=n
        for k in range(1,n-1):faces.append([face[0],face[k],face[k+1]])
    assert len(v) and faces and np.isfinite(v).all(),'Actual audit mesh empty/nonfinite'
    return v,np.asarray(faces,dtype=np.int32)

def top_union(prim):
    vertices,faces=mesh_arrays(prim);tri=vertices[faces];normal=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);z=float(vertices[:,2].max())
    chosen=tri[(np.max(abs(tri[:,:,2]-z),axis=1)<1e-6)&(normal[:,2]>0)]
    shapes=[Polygon(t[:,:2]) for t in chosen];assert shapes and all(x.is_valid and x.area>0 for x in shapes)
    return unary_union(shapes)

def enabled(prim):return prim.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False

def audit(stage,receipt,layout,inspection,cfg):
    ground=top_union(stage.GetPrimAtPath('/World/Cut/Floor1Slab/Visual'));visual=top_union(stage.GetPrimAtPath('/World/Cut/Floor2Slab/Visual'));prim=stage.GetPrimAtPath('/World/Cut/Floor2Slab/Collision');assert enabled(prim),'Actual slab collision disabled';physical=top_union(prim)
    assert UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get()=='none','Slab approximation would close native holes'
    assert abs(ground.area-inspection['slabs']['/World/Cut/Floor1Slab/Visual']['actual_top_area_m2'])<1e-6
    assert abs(physical.area/ground.area-cfg['sample_mezz_stair_integration_v3']['slab_net_fraction'])<1e-6
    assert visual.symmetric_difference(physical).area<1e-8,'Independent actual visual/collision floor mismatch'
    structural=[]
    for item in inspection['structure']:
        prim=stage.GetPrimAtPath(item['path']);assert prim and prim.IsDefined() and prim.IsActive(),'Source structural coverage missing';structural.append(item['path'])
    rows=[]
    for stair in layout['stairs']:
        root=stage.GetPrimAtPath(stair['root']);assert root and root.IsDefined() and root.IsActive(),'Native root missing'
        native=Usd.Stage.Open(stair['asset_stage']['path']);original=sum(enabled(x) for x in native.Traverse());actual=sum(enabled(x) for x in Usd.PrimRange(root));assert actual==original,'Native physical object count differs'
        tread=next(p for p in Usd.PrimRange(root) if p.GetName()=='integrated_tread_collision');vertices,faces=mesh_arrays(tread)
        actual_tread_top=top_union(tread);arrival=stair['world_path'][-1];assert physical.covers(Point(arrival[:2])),'Native arrival unsupported'
        hole=box(*stair['opening_rect']);assert physical.intersection(hole.buffer(-2e-5)).area<1e-8,'Independent actual opening sealed'
        assert physical.buffer(2e-5).covers(Polygon(stair['upper_forward_landing_patch_xy'])),'Independent forward landing unsupported'
        rows.append(dict(id=stair['id'],source_colliders=original,placed_colliders=actual,highest_actual_tread_z_m=float(vertices[:,2].max()),arrival_xyz_m=arrival,arrival_supported=True,opening_void_area_m2=hole.area,opening_bay_xy=stair['opening_bay_xy'],rotation_deg=stair['rotation_deg'],seed=stair['seed'],actual_tread_triangle_count=len(faces),actual_highest_tread_xy_polygon=actual_tread_top.__geo_interface__))
    assert sum(enabled(p) for p in stage.Traverse())==receipt['positive_readback']['actual_enabled_colliders']
    return dict(actual_ground_area_m2=ground.area,actual_new_upper_area_m2=physical.area,actual_new_mezzanine_fraction=physical.area/ground.area,actual_two_floor_area_m2=ground.area+physical.area,source_structural_objects_present=len(structural),actual_enabled_colliders=sum(enabled(p) for p in stage.Traverse()),stairs=rows)

def cloned(stage):
    source=stage.GetRootLayer();layer=Sdf.Layer.CreateAnonymous();layer.TransferContent(source);layer.subLayerPaths=[Sdf.ComputeAssetPathRelativeToLayer(source,p) for p in source.subLayerPaths]
    return Usd.Stage.Open(layer)

def diagnostic_plot(values,cfg,destination):
    output=[];pre=read(P/'evidence/V3_SAMPLE_PRELAYOUT_INPUTS.json')
    for receipt,layout,stage,row in values:
        inspection=read(R/'runs'/row['building']/'v3_sample/source_inspection/STAGE_INSPECTION.json');stairs=[]
        for stair in layout['stairs']:
            prim=next(p for p in Usd.PrimRange(stage.GetPrimAtPath(stair['root'])) if p.GetName()=='integrated_tread_collision');v,f=mesh_arrays(prim);tri=v[f];normal=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);top=tri[(np.ptp(tri[:,:,2],axis=1)<1e-6)&(normal[:,2]>0)]
            native=next(s for x in pre['items'] if x['building']==row['building'] for s in x['current_native_stairs'] if s['stair_index']==stair['stair_index'])
            stairs.append(dict(id=stair['id'],actual_world_top_triangles=top.tolist(),source_tread_mesh=str(prim.GetPath()),native_color_srgb=cfg['stairs']['colors_srgb'][native['color']],opening_rect=stair['opening_rect'],world_path=stair['world_path'],equipment_keepouts=stair['equipment_keepouts'],exit_floor_xyz_m=stair['exit_floor_xyz_m']))
        output.append(dict(building=row['building'],root=receipt['root'],ground_bounds=list(top_union(stage.GetPrimAtPath('/World/Cut/Floor1Slab/Visual')).bounds),gross_slab_rect=layout['gross_slab_rect'],reserved_aisles=layout['reserved_aisles'],columns=[x['position'] for x in inspection['structure'] if x['type']=='column'],stairs=stairs,actual_new_upper_area_m2=row['actual_new_upper_area_m2'],actual_new_mezzanine_fraction=row['actual_new_mezzanine_fraction']))
    with destination.open('x') as stream:json.dump(dict(status='ACTUAL_SOURCE_GEOMETRY_PROJECTION_INPUTS_NOT_A_RENDER',items=output),stream,indent=2)

def main():
    cfg=read(P/'config.yaml');config_ref=ref(P/'config.yaml');assert cfg['sample_mezz_stair_integration_v3']['tool_version'].startswith('v3_mezz_stair_integration@v4') and not cfg['approval']['batch_release']
    frozen=read(P/'evidence/G13_MEASURED_SEAL_FULL_QUALIFICATION_V1/QUALIFICATION.json')['original_frozen_source_before'];assert len(frozen)==26 and all(sha(p)==h for p,h in frozen.items()),'Original frozen closure changed'
    rows=[];values=[];control_results=[];bindings=[]
    for bid in ['004','003']:
        folder=R/'runs'/bid/'v3_sample/mezz_stair_integration';receipt=read(folder/'AUTHORING_READBACK.json');layout=read(folder/'LAYOUT.json');inspection=read(R/'runs'/bid/'v3_sample/source_inspection/STAGE_INSPECTION.json')
        assert receipt['canonical_config']==config_ref and ref(receipt['root']['path'])==receipt['root'] and ref(receipt['actual_layout']['path'])==receipt['actual_layout']
        assert len(receipt['actual_negative_controls'])==5 and all(x['status']=='FAIL_AS_REQUIRED' for x in receipt['actual_negative_controls'])
        assert read(P/'evidence/owned_tasks'/('v3_sample'+bid+'_new_mezz_stairs_v4')/'RESULT.json')['exit_code']==0
        stage=Usd.Stage.Open(receipt['root']['path']);result=audit(stage,receipt,layout,inspection,cfg);result.update(building=bid,authoring_readback=ref(folder/'AUTHORING_READBACK.json'),root=receipt['root'],actual_layout=receipt['actual_layout']);rows.append(result);values.append((receipt,layout,stage,result));bindings.extend([receipt['root'],receipt['actual_layout'],ref(folder/'AUTHORING_READBACK.json')])
        for kind,fragment in [('actual_all_native_stairs_deactivated','Native root missing'),('actual_slab_collision_convexified','Slab approximation')]:
            candidate=cloned(stage);candidate.SetEditTarget(candidate.GetSessionLayer())
            if kind=='actual_all_native_stairs_deactivated':candidate.OverridePrim('/World/Stair').SetActive(False)
            else:UsdPhysics.MeshCollisionAPI(candidate.GetPrimAtPath('/World/Cut/Floor2Slab/Collision')).GetApproximationAttr().Set('convexHull')
            try:audit(candidate,receipt,layout,inspection,cfg)
            except AssertionError as error:
                assert fragment in str(error),'Independent guard rejected for unrelated reason';control_results.append(dict(building=bid,control=kind,status='FAIL_AS_REQUIRED',rejection=str(error),scope='Actual USD hierarchy/schema guard; not native PhysX or frozen G13'))
            else:
                file=R/'CRITICAL_STOP_V3_MEZZ_INDEPENDENT_NEGATIVE.json'
                with file.open('x') as stream:json.dump(dict(status='ENTIRE_TASK_STOP_REQUIRED',building=bid,control=kind),stream)
                raise RuntimeError('Independent actual USD guard escaped')
    destination=P/'evidence/V3_SAMPLE_NEW_MEZZ_STAIR_PROJECTION_INPUTS.json';assert not destination.exists();diagnostic_plot(values,cfg,destination)
    assert all(ref(x['path'])==x for x in bindings) and all(sha(p)==h for p,h in frozen.items()) and ref(P/'config.yaml')==config_ref
    output=P/'evidence/V3_MEZZ_STAIR_INDEPENDENT_AUDIT.json';assert not output.exists()
    value=dict(status='INDEPENDENT_ACTUAL_TRIANGLE_AREA_STRUCTURE_ARRIVAL_AND_NATIVE_COLLIDER_AUDIT_PASS',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),canonical_config=config_ref,independent_of_author_geometry_functions=True,producer_version="v3_mezz_independent_audit@v2-numerical-data-only",items=rows,actual_USD_guard_controls=control_results,all26_frozen_sources_exact_unchanged=frozen,diagnostic_measured_projection_data=ref(destination),projection_is_final_building_render=False,whole_building_gates='NOT_EXECUTED',native_physics='NOT_EXECUTED',equipment='NOT_AUTHORED',batch_release=False)
    with output.open('x') as stream:json.dump(value,stream,indent=2)
    print(json.dumps(dict(status=value['status'],audit=ref(output),diagnostic=ref(destination))),flush=True)
if __name__=='__main__':main()
