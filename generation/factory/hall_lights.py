"""Reuse frozen Factory80 LED fixtures and calibrated lights on actual domains.

Default mode preserves the old instance counts and moves only lower XY poses.
The explicit frozen-bay-template mode repeats eight actual 001 fixture/light
templates over real full bays. Both modes preserve source geometry and light
properties; neither performs rendering or physics/visual qualification.
"""
from pathlib import Path
import argparse, copy, hashlib, json, math, os, resource, time
import numpy as np
from shapely.geometry import shape, box, Polygon
from pxr import Usd, UsdGeom, UsdLux, UsdPhysics, Gf

HASHES={}
def digest(path):
    path=Path(path);st=path.stat();key=(str(path.resolve()),st.st_size,st.st_mtime_ns)
    if key not in HASHES:
        h=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(4*1024**2),b''):h.update(chunk)
        HASHES[key]=h.hexdigest()
    return HASHES[key]

def ref(path):return dict(path=str(Path(path).resolve()),sha256=digest(path))
def write(path,value):
    with Path(path).open('x') as f:json.dump(value,f,indent=2,allow_nan=False)
def pathof(value):return value['path'] if isinstance(value,dict) else value
def enabled(p):return p.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False

def light_payload(stage):
    result={}
    for prim in stage.Traverse():
        if not prim.HasAPI(UsdLux.LightAPI):continue
        attrs={}
        for attr in prim.GetAttributes():
            name=attr.GetName()
            if name=='xformOpOrder' or name.startswith('xformOp:'):continue
            times=attr.GetTimeSamples()
            attrs[name]=dict(type=str(attr.GetTypeName()),value=repr(attr.Get()),time_samples=[(t,repr(attr.Get(t))) for t in times])
        result[str(prim.GetPath())]=attrs
    return result

def collision_bounds(stage):
    bbox=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','proxy','guide','render'],useExtentsHint=False)
    low=np.full(3,np.inf);high=-low;count=0
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not enabled(prim):continue
        b=bbox.ComputeWorldBound(prim).ComputeAlignedRange();low=np.minimum(low,np.array(b.GetMin()));high=np.maximum(high,np.array(b.GetMax()));count+=1
    if not count:raise ValueError('Frozen LED source has no enabled collider')
    return low,high,count

def rotated_bounds(low,high,yaw):
    angle=math.radians(yaw);c,s=math.cos(angle),math.sin(angle)
    points=np.array([[x,y,z] for x in [low[0],high[0]] for y in [low[1],high[1]] for z in [low[2],high[2]]])
    matrix=np.array([[c,s,0],[-s,c,0],[0,0,1]])
    world=points@matrix;return world.min(0),world.max(0)


def frozen_bay_templates(args,started,inputs,receipt,domains):
    """Repeat one actual complete bay; source primitives and attributes are frozen."""
    assert receipt['building']=='001','FROZEN_BAY_MODE_REQUIRES_ACTUAL_001_TEMPLATE_RECEIPT'
    source_rows=[row for row in receipt['actual_instances'] if row.get('role')=='visible_LED_fixture']
    source_paths={pathof(row['source_root']) for row in source_rows};assert len(source_paths)==1
    fixture_source=Path(next(iter(source_paths)));inputs['fixture_source']=ref(fixture_source)
    assert all(row['source_root']['sha256']==inputs['fixture_source']['sha256'] for row in source_rows),'FROZEN_LED_SOURCE_CHANGED'
    inputs['template_scene']=ref(pathof(receipt['root']))
    assert inputs['template_scene']==receipt['root'],'FROZEN_FIXTURE_RECEIPT_SCENE_CHANGED'
    led=Usd.Stage.Open(str(fixture_source));assert led and led.GetDefaultPrim()
    low,high,colliders=collision_bounds(led)
    assert colliders==20 and all(row['source_enabled_colliders']==20 and row.get('artistic_scale',1.)==1. for row in source_rows),'FROZEN_LED_SOURCE_CONTRACT_CHANGED'
    source_lights=Path(pathof(receipt['parent_root']));inputs['light_source']=ref(source_lights)
    assert inputs['light_source']==receipt['parent_root'],'FROZEN_LIGHT_SOURCE_CHANGED'
    mask=Usd.StagePopulationMask();mask.Add('/World/SampleLighting')
    old=Usd.Stage.OpenMasked(str(source_lights),mask);assert old
    before=light_payload(old);cache=UsdGeom.XformCache()
    pitchx,pitchy=20.,21.;quarters={}
    for row in source_rows:
        path=row['original_RectLight_path'];assert path in before
        position=np.array(cache.GetLocalToWorldTransform(old.GetPrimAtPath(path)))[3,:3]
        ix,iy=math.floor(position[0]/pitchx),math.floor(position[1]/pitchy)
        qx,qy=round((position[0]-ix*pitchx)/10.-.5),round((position[1]-iy*pitchy)/10.5-.5)
        assert qx in [0,1] and qy in [0,1]
        assert np.allclose(position[:2],[(ix+(qx+.5)/2)*pitchx,(iy+(qy+.5)/2)*pitchy],atol=1e-8,rtol=0),'SOURCE_LIGHT_NOT_ACTUAL_QUARTER_BAY'
        key=(row['domain'],ix,iy);quarters.setdefault(key,{})[(qx,qy)]=row
    groups=['ceiling_highbay','ceiling_under_mezz']
    candidates=sorted({(ix,iy) for group,ix,iy in quarters if all(len(quarters.get((g,ix,iy),{}))==4 for g in groups)})
    assert candidates,'NO_ACTUAL_COMPLETE_HIGH_AND_LOWER_TEMPLATE_BAY'
    source_bay=candidates[0];templates={g:quarters[(g,*source_bay)] for g in groups}
    ground=shape(domains['floors']['Floor1']['support_geometry'])
    gross=shape(domains['actual_mezzanine_gross_geometry']);net=shape(domains['floors']['Floor2']['support_geometry'])
    minx,miny,maxx,maxy=ground.bounds;assert abs(minx)<=1e-6 and abs(miny)<=1e-6
    assert abs(maxx/pitchx-round(maxx/pitchx))<1e-6 and abs(maxy/pitchy-round(maxy/pitchy))<1e-6,'GROUND_NOT_FULL_COLUMN_GRID'
    bays={g:[] for g in groups}
    for ix in range(round(maxx/pitchx)):
        for iy in range(round(maxy/pitchy)):
            bay=box(ix*pitchx,iy*pitchy,(ix+1)*pitchx,(iy+1)*pitchy)
            assert bay.difference(ground).area<=1e-6,'GROUND_HAS_INCOMPLETE_FULL_BAY'
            bays[groups[0]].append((ix,iy,bay))
            if bay.difference(gross).area<=1e-6:bays[groups[1]].append((ix,iy,bay))
    assert len(bays[groups[1]])>0 and abs(len(bays[groups[1]])*pitchx*pitchy-gross.area)<=1e-5,'MEZZANINE_NOT_WHOLE_BAYS'
    layer=args.output/'lightlayer.usda';new=Usd.Stage.CreateNew(str(layer))
    UsdGeom.SetStageMetersPerUnit(new,1.);UsdGeom.SetStageUpAxis(new,'Z')
    world=UsdGeom.Xform.Define(new,'/World');new.SetDefaultPrim(world.GetPrim())
    UsdGeom.Scope.Define(new,'/World/SampleLighting')
    expected_payload={};expected_world={};mappings=[]
    def clone_light(source_path,target_path,delta):
        source=old.GetPrimAtPath(source_path);assert source and source_path in before
        target=new.DefinePrim(target_path);target.GetReferences().AddReference(str(source_lights.resolve()),source_path)
        desired=np.array(cache.GetLocalToWorldTransform(source));desired[3,:3]+=delta
        parent=np.array(UsdGeom.XformCache().GetLocalToWorldTransform(target.GetParent()))
        local=desired@np.linalg.inv(parent);xf=UsdGeom.Xformable(target)
        xf.ClearXformOpOrder();xf.AddTransformOp(opSuffix='factory80_frozen_bay_xy').Set(Gf.Matrix4d(*local.reshape(-1).tolist()))
        expected_payload[target_path]=before[source_path];expected_world[target_path]=desired
        mappings.append(dict(source_stage=inputs['light_source'],source_prim=source_path,target_prim=target_path,
                             delta_xyz_m=delta.tolist(),pose_change='XY_ONLY' if np.any(delta) else 'WORLD_POSE_UNCHANGED'))
    fixtures=[];placements=[];occupied=[]
    offsets=sorted(((x*.5,y*.5) for x in range(-30,31) for y in range(-30,31)),key=lambda p:(p[0]*p[0]+p[1]*p[1],p[0],p[1]))
    slab_bottom=float(domains['upper_slab_bottom_z_m']);roof=float(domains['roof_z_m'])
    for group in groups:
        lower=group==groups[1]
        for ix,iy,bay in bays[group]:
            for qx in range(2):
                for qy in range(2):
                    template=templates[group][qx,qy];row=copy.deepcopy(template)
                    source_light=template['original_RectLight_path']
                    old_translation=np.array(template['translation_m'],dtype=np.float64)
                    local_low,local_high=rotated_bounds(low,high,float(row['yaw_deg']))
                    old_center=old_translation[:2]+(local_low[:2]+local_high[:2])/2
                    zlo=old_translation[2]+local_low[2];zhi=old_translation[2]+local_high[2]
                    assert zlo>=1.6 and zhi<(slab_bottom if lower else roof),'FROZEN_LED_VERTICAL_FIT_FAILED'
                    x,y=ix*pitchx+(qx+.5)*pitchx/2,iy*pitchy+(qy+.5)*pitchy/2
                    chosen=None
                    for dx,dy in offsets if lower else [(0.,0.)]:
                        translation=old_translation.copy()
                        if lower:translation[:2]+=np.array([x+dx,y+dy])-old_center
                        else:translation[:2]+=np.array([(ix-source_bay[0])*pitchx,(iy-source_bay[1])*pitchy])
                        footprint=box(*(translation[:2]+local_low[:2]),*(translation[:2]+local_high[:2]))
                        if not bay.covers(footprint) or (lower and not net.covers(footprint)):continue
                        if lower and any(footprint.intersection(other).area>1e-8 for other in occupied):continue
                        chosen=translation,footprint,dx,dy;break
                    if chosen is None:
                        write(args.output/'PLACEMENT_FAILED.json',dict(status='FAILED_NO_LEGAL_LOWER_LED_POSITION',source_light=source_light,bay=[ix,iy],quarter=[qx,qy],completed=placements,input_sha=inputs));raise SystemExit(1)
                    translation,footprint,dx,dy=chosen;delta=translation-old_translation
                    assert delta[2]==0.,'FROZEN_HEIGHT_CHANGED'
                    label='LowerLED' if lower else 'LED';name=f'{label}_B{ix:02d}_{iy:02d}_Q{qx}{qy}'
                    target='/World/SampleLighting/'+name;clone_light(source_light,target,delta)
                    row['translation_m']=translation.tolist();row['world_union_bounds_m']=[(translation+local_low).tolist(),(translation+local_high).tolist()]
                    row['prim_path']='/World/'+row['floor']+'/LightingFixturesV4/'+name
                    row['unit_id']='V4_'+name;row['original_RectLight_path']=target
                    row['template_original_RectLight_path']=source_light;row['template_actual_fixture_prim']=template['prim_path']
                    row['template_actual_fixture_scene']=inputs['template_scene'];row['template_bay']=list(source_bay);row['target_bay']=[ix,iy];row['target_quarter']=[qx,qy]
                    row['original_light_unchanged']=False;row['photometric_inputs_unchanged']=True;row['pose_change']='FROZEN_TEMPLATE_XY_ONLY'
                    row.pop('actual_USD_wrapper_matrix',None)
                    row['expected_colliders']=colliders;row['collision_proxy']=inputs['fixture_source'];row['proxy_root_path']=str(led.GetDefaultPrim().GetPath());row['asset_proxy_sha256']=inputs['fixture_source']['sha256']
                    fixtures.append(row)
                    if lower:
                        occupied.append(footprint)
                        placements.append(dict(light=target,source_light=source_light,bay=[ix,iy],quarter=[qx,qy],requested_center_xy_m=[x,y],actual_fixture_center_xy_m=(translation[:2]+(local_low[:2]+local_high[:2])/2).tolist(),adjustment_xy_m=[dx,dy],delta_xy_m=delta[:2].tolist(),fixture_collision_footprint_m2=footprint.area,net_upper_covers_fixture=True,fixture_bottom_z_m=zlo,fixture_top_z_m=zhi,slab_bottom_z_m=slab_bottom,slab_clearance_m=slab_bottom-zhi))
    domes=[str(p.GetPath()) for p in old.Traverse() if p.IsA(UsdLux.DomeLight)]
    assert len(domes)==1,'EXPECTED_ONE_ACTUAL_FROZEN_DOME'
    clone_light(domes[0],'/World/SampleLighting/NeutralDome',np.zeros(3))
    new.GetRootLayer().Save();reopened=Usd.Stage.Open(str(layer));assert reopened
    actual_payload=light_payload(reopened);assert actual_payload==expected_payload,'FROZEN_CLONE_LIGHT_ATTRIBUTES_CHANGED'
    now=UsdGeom.XformCache()
    pose_error=max(float(np.abs(expected-np.array(now.GetLocalToWorldTransform(reopened.GetPrimAtPath(path)))).max()) for path,expected in expected_world.items())
    assert pose_error<=1e-10,'FROZEN_CLONE_WORLD_POSE_CHANGED'
    assert inputs['receipt']==ref(args.receipt) and inputs['domains']==ref(args.domains),'BOUND_INPUT_CHANGED_DURING_LIGHT_PLACEMENT'
    for key in ['fixture_source','template_scene','light_source']:assert inputs[key]==ref(inputs[key]['path']),'FROZEN_TEMPLATE_SOURCE_CHANGED'
    high_count=4*len(bays[groups[0]]);lower_count=4*len(bays[groups[1]])
    assert len(fixtures)==high_count+lower_count and len(actual_payload)==len(fixtures)+1
    metadata=dict(tool='lights@v3',building=domains['building'],status='FROZEN_BAY_TEMPLATES_AUTHORED_NOT_RENDERED_OR_PHYSICS_CHECKED',placement_mode='frozen-bay-template',
        instances=fixtures,retained_components=[dict(prim_path='/World/SampleLighting',source_stage=ref(layer),source_prim='/World/SampleLighting')],
        high_fixture_count=high_count,lower_fixture_count=lower_count,light_prim_count=len(actual_payload),full_ground_bays=[[x,y] for x,y,_ in bays[groups[0]]],full_mezz_bays=[[x,y] for x,y,_ in bays[groups[1]]],lower_placements=placements,
        frozen_template_source_building='001',frozen_template_bay=list(source_bay),frozen_fixture_template_count=8,
        source_fixture_receipt_count=len(source_rows),new_output_fixture_count=len(fixtures),output_counts_are_new_template_instances_not_old_receipt_counts=True,
        cloned_light_source_mapping=mappings,template_non_transform_payloads={path:before[path] for path in sorted({m['source_prim'] for m in mappings})},
        all_light_photometric_attributes_unchanged=True,light_expected_world_pose_max_error_m=pose_error,fixture_geometry_material_scale_and_height_unchanged=True,
        input_sha=inputs,output_sha=dict(lightlayer=digest(layer)),exit_code=0,peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,elapsed_s=time.monotonic()-started,
        GPU='NOT_EXECUTED',registration='NOT_EXECUTED',requalification='NOT_EXECUTED',formal_acceptance='NOT_EXECUTED',visual_gate='NOT_EXECUTED_SOURCE_ATTRIBUTE_AND_PLACEMENT_READBACK_ONLY')
    write(args.output/'LIGHTING.json',metadata)
    print(json.dumps({k:metadata[k] for k in ['tool','placement_mode','status','high_fixture_count','lower_fixture_count','light_prim_count','light_expected_world_pose_max_error_m','elapsed_s','exit_code']}),flush=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--receipt',type=Path,required=True);ap.add_argument('--domains',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--placement-mode',choices=['existing-count','frozen-bay-template'],default='existing-count')
    args=ap.parse_args();started=time.monotonic();Path('/proc/self/oom_score_adj').write_text('1000')
    args.output.mkdir(parents=True,exist_ok=False)
    inputs=dict(receipt=ref(args.receipt),domains=ref(args.domains),tool=ref(__file__))
    receipt=json.loads(args.receipt.read_text());domains=json.loads(args.domains.read_text())
    if args.placement_mode=='frozen-bay-template':
        frozen_bay_templates(args,started,inputs,receipt,domains);return
    fixtures=[copy.deepcopy(row) for row in receipt['actual_instances'] if row.get('role')=='visible_LED_fixture']
    lower=sorted([row for row in fixtures if row['domain']=='ceiling_under_mezz'],key=lambda row:row['original_RectLight_path'])
    upper=[row for row in fixtures if row['domain']=='ceiling_highbay']
    assert len(fixtures)==len(lower)+len(upper) and len(lower)>0 and len(lower)%4==0
    source_paths={pathof(row['source_root']) for row in fixtures};assert len(source_paths)==1
    fixture_source=Path(next(iter(source_paths)));inputs['fixture_source']=ref(fixture_source)
    assert all(row['source_root']['sha256']==inputs['fixture_source']['sha256'] for row in fixtures),'FROZEN_LED_SOURCE_CHANGED'
    led=Usd.Stage.Open(str(fixture_source));assert led and led.GetDefaultPrim()
    low,high,colliders=collision_bounds(led)
    assert colliders==20 and all(row['source_enabled_colliders']==20 for row in fixtures),'FROZEN_LED_COLLIDER_COUNT_CHANGED'
    source_lights=Path(pathof(receipt['parent_root']));inputs['light_source']=ref(source_lights)
    assert inputs['light_source']['sha256']==receipt['parent_root']['sha256'],'FROZEN_LIGHT_SOURCE_CHANGED'
    mask=Usd.StagePopulationMask();mask.Add('/World/SampleLighting');old=Usd.Stage.OpenMasked(str(source_lights),mask)
    light_root=old.GetPrimAtPath('/World/SampleLighting');assert light_root
    old_cache=UsdGeom.XformCache()
    assert np.allclose(np.array(old_cache.GetLocalToWorldTransform(light_root.GetParent())),np.eye(4),atol=1e-12),'NONIDENTITY_LIGHT_PARENT_REQUIRES_EXPLICIT_FRAME_ADAPTER'
    before=light_payload(old);assert before
    for row in fixtures:assert row['original_RectLight_path'] in before
    gross=shape(domains['actual_mezzanine_gross_geometry']);net=shape(domains['floors']['Floor2']['support_geometry'])
    slab_bottom=float(domains['upper_slab_bottom_z_m'])
    minx,miny,maxx,maxy=shape(domains['floors']['Floor1']['support_geometry']).bounds
    assert abs(minx)<=1e-6 and abs(miny)<=1e-6
    pitchx,pitchy=20.,21.;bays=[]
    for ix in range(math.ceil(maxx/pitchx)):
        for iy in range(math.ceil(maxy/pitchy)):
            bay=box(ix*pitchx,iy*pitchy,(ix+1)*pitchx,(iy+1)*pitchy)
            if bay.difference(gross).area<=1e-6:bays.append((ix,iy,bay))
    assert len(bays)*4==len(lower),f'ACTUAL_FULL_MEZZ_BAYS_DO_NOT_MATCH_REUSED_LOWER_COUNT:{len(bays)}*4!={len(lower)}'
    targets=[(ix,iy,qx,qy,ix*pitchx+(qx+.5)*pitchx/2,iy*pitchy+(qy+.5)*pitchy/2,bay) for ix,iy,bay in bays for qx in range(2) for qy in range(2)]
    placements=[];occupied=[];moves={}
    offsets=sorted(((x*.5,y*.5) for x in range(-30,31) for y in range(-30,31)),key=lambda p:(p[0]*p[0]+p[1]*p[1],p[0],p[1]))
    for row,target in zip(lower,targets):
        ix,iy,qx,qy,x,y,bay=target;local_low,local_high=rotated_bounds(low,high,float(row['yaw_deg']))
        old_translation=np.array(row['translation_m'],dtype=np.float64);old_center=old_translation[:2]+(local_low[:2]+local_high[:2])/2
        zlo=old_translation[2]+local_low[2];zhi=old_translation[2]+local_high[2]
        assert zlo>=1.6 and zhi<slab_bottom,'FROZEN_LOWER_LED_VERTICAL_FIT_FAILED'
        chosen=None
        for dx,dy in offsets:
            center=np.array([x+dx,y+dy]);translation=old_translation.copy();translation[:2]+=center-old_center
            footprint=box(*(translation[:2]+local_low[:2]),*(translation[:2]+local_high[:2]))
            if not bay.covers(footprint) or not net.covers(footprint):continue
            if any(footprint.intersection(other).area>1e-8 for other in occupied):continue
            chosen=(translation,footprint,dx,dy);break
        if chosen is None:
            write(args.output/'PLACEMENT_FAILED.json',dict(status='FAILED_NO_LEGAL_LOWER_LED_POSITION',light=row['original_RectLight_path'],bay=[ix,iy],quarter=[qx,qy],completed=placements,input_sha=inputs));raise SystemExit(1)
        translation,footprint,dx,dy=chosen;delta=translation-old_translation;moves[row['original_RectLight_path']]=delta;occupied.append(footprint)
        row['translation_m']=translation.tolist();row['world_union_bounds_m']=[(translation+local_low).tolist(),(translation+local_high).tolist()]
        row['original_light_unchanged']=False;row['photometric_inputs_unchanged']=True;row['pose_change']='LOWER_XY_ONLY';row.pop('actual_USD_wrapper_matrix',None)
        placements.append(dict(light=row['original_RectLight_path'],bay=[ix,iy],quarter=[qx,qy],requested_center_xy_m=[x,y],actual_fixture_center_xy_m=(translation[:2]+(local_low[:2]+local_high[:2])/2).tolist(),adjustment_xy_m=[dx,dy],delta_xy_m=delta[:2].tolist(),fixture_collision_footprint_m2=footprint.area,net_upper_covers_fixture=True,fixture_bottom_z_m=zlo,fixture_top_z_m=zhi,slab_bottom_z_m=slab_bottom,slab_clearance_m=slab_bottom-zhi))
    # lower rows are original fixture dict objects; fixtures contains these same
    # objects, while upper rows have never had their position or material edited.
    layer=args.output/'lightlayer.usda';new=Usd.Stage.CreateNew(str(layer));UsdGeom.SetStageMetersPerUnit(new,1.);UsdGeom.SetStageUpAxis(new,'Z')
    world=UsdGeom.Xform.Define(new,'/World');new.SetDefaultPrim(world.GetPrim())
    new.DefinePrim('/World/SampleLighting',light_root.GetTypeName()).GetReferences().AddReference(str(source_lights.resolve()),'/World/SampleLighting')
    for path,delta in moves.items():
        old_prim=old.GetPrimAtPath(path);desired=np.array(old_cache.GetLocalToWorldTransform(old_prim));desired[3,:3]+=delta
        prim=new.OverridePrim(path);parent=np.array(UsdGeom.XformCache().GetLocalToWorldTransform(prim.GetParent()));local=desired@np.linalg.inv(parent)
        xf=UsdGeom.Xformable(prim);xf.ClearXformOpOrder();xf.AddTransformOp(opSuffix='factory80_v4_xy').Set(Gf.Matrix4d(*local.reshape(-1).tolist()))
    new.GetRootLayer().Save();after=light_payload(new);assert after==before,'FROZEN_LIGHT_ATTRIBUTES_CHANGED'
    now=UsdGeom.XformCache();high_error=0.;low_error=0.
    for row in fixtures:
        path=row['original_RectLight_path'];old_world=np.array(old_cache.GetLocalToWorldTransform(old.GetPrimAtPath(path)));new_world=np.array(now.GetLocalToWorldTransform(new.GetPrimAtPath(path)));
        expected=old_world.copy()
        if path in moves:expected[3,:3]+=moves[path];low_error=max(low_error,float(np.abs(expected-new_world).max()))
        else:high_error=max(high_error,float(np.abs(expected-new_world).max()))
    assert max(high_error,low_error)<=1e-10,'LIGHT_POSE_TRANSPORT_CHANGED'
    proxy_ref=ref(fixture_source)
    for row in fixtures:
        row['expected_colliders']=colliders;row['collision_proxy']=proxy_ref;row['proxy_root_path']=str(led.GetDefaultPrim().GetPath())
        row['asset_proxy_sha256']=proxy_ref['sha256']
    assert inputs['domains']==ref(args.domains),'DOMAINS_CHANGED_DURING_LIGHT_PLACEMENT'
    metadata=dict(tool='lights@v3',building=receipt['building'],status='REUSED_LIGHTING_AUTHORED_NOT_RENDERED_OR_PHYSICS_CHECKED',instances=fixtures,retained_components=[dict(prim_path='/World/SampleLighting',source_stage=ref(layer),source_prim='/World/SampleLighting')],high_fixture_count=len(upper),lower_fixture_count=len(lower),light_prim_count=len(before),full_mezz_bays=[[x,y] for x,y,_ in bays],lower_placements=placements,all_light_photometric_attributes_unchanged=True,high_light_world_pose_max_error_m=high_error,lower_light_expected_pose_max_error_m=low_error,input_sha=inputs,output_sha=dict(lightlayer=digest(layer)),exit_code=0,peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,elapsed_s=time.monotonic()-started,GPU='NOT_EXECUTED',registration='NOT_EXECUTED',requalification='NOT_EXECUTED',formal_acceptance='NOT_EXECUTED')
    write(args.output/'LIGHTING.json',metadata)
    print(json.dumps({k:metadata[k] for k in ['status','high_fixture_count','lower_fixture_count','light_prim_count','high_light_world_pose_max_error_m','lower_light_expected_pose_max_error_m','elapsed_s','exit_code']}),flush=True)
if __name__=='__main__':main()
