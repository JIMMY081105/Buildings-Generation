"""Actual Isaac timeline observation for visually ambiguous pilot buildings.

Prepared observer only. It produces no acceptance gates or visual ratings.
All runtime changes live in the USD session layer and are never saved.
"""
import argparse,ast,asyncio,hashlib,json,math,time
from pathlib import Path
from datetime import datetime,timezone

ap=argparse.ArgumentParser();ap.add_argument('--authoring',type=Path,required=True)
ap.add_argument('--output',type=Path,required=True);args,_=ap.parse_known_args()
def ref(path):
    data=path.read_bytes();return {'path':str(path),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
author=json.loads(args.authoring.read_text());source=Path(author['stage']['path']);before=ref(source)
assert author['stage']==before and int(author['building'].rsplit('_',1)[1]) in range(1,7)
assert author['storeys']==2
count=author['stair_count'];stairs=author['stairs']
assert type(count) is int and count>=2 and len(stairs)==count
assert {r['index'] for r in stairs}==set(range(count))
for stair in stairs:
    points=stair['world_path'];assert len(points)>=2
    assert all(len(p)==3 and all(math.isfinite(v) for v in p) for p in points)
    assert math.hypot(points[-1][0]-points[-2][0],points[-1][1]-points[-2][1])>0
assert not args.output.exists();args.output.mkdir(parents=True)
result={'schema':'Factory80ActualIsaacVisualObservationV1','building':author['building'],
        'source_stage':before,'authoring':ref(args.authoring),'utc_started':datetime.now(timezone.utc).isoformat(),
        'observer_script':ref(Path(__file__)),
        'actual_simulation_executed':False,'actual_simulation_update_count':0,'images_actually_viewed':False,
        'formal_acceptance':'NOT EXECUTED','acceptance_gates_added':False,
        'cannot_support':'实际Isaac运行与观察只辅助主观厂房判语，不能证明冻结门禁通过、机器人运动能力或正式验收。'}
app=None;timeline=None;actual_advances=0
def updates(n):
    global actual_advances
    for _ in range(n):
        old=timeline.get_current_time();playing=timeline.is_playing();app.update()
        if playing and timeline.get_current_time()>old:actual_advances+=1
def look(eye,target):
    forward=(target-eye).GetNormalized();up=Gf.Vec3d(0,0,1)
    if abs(Gf.Dot(forward,up))>.999:up=Gf.Vec3d(0,1,0)
    right=Gf.Cross(forward,up).GetNormalized();m=Gf.Matrix4d(1)
    m.SetRow3(0,right);m.SetRow3(1,Gf.Cross(right,forward));m.SetRow3(2,-forward);m.SetRow3(3,eye)
    return m
try:
    from isaacsim import SimulationApp
    app=SimulationApp({'headless':True,'renderer':'RayTracedLighting','width':1280,'height':720,
                       'active_gpu':0,'physics_gpu':0,'multi_gpu':False})
    import omni.usd,omni.timeline
    from omni.kit.viewport.utility import capture_viewport_to_file,get_active_viewport
    from pxr import Gf,Usd,UsdGeom,UsdPhysics
    timeline=omni.timeline.get_timeline_interface()
    context=omni.usd.get_context();assert context.open_stage(str(source))
    while context.get_stage_loading_status()[2]:updates(1)
    updates(250);stage=context.get_stage();stage.SetEditTarget(stage.GetSessionLayer())
    # Execute the exact already-frozen static normalization function, without
    # importing the original validator's top-level SimulationApp/main code.
    frozen=Path('/cephfs/shared/yixing/outputs/diversity54_20260908/evidence/frozen_sources/pipeline/validate_building_isaac.py')
    from factory80_stage_d_aggregate_v1 import native_gate_names
    native_gate_names()  # Existing pinned-SHA check; no gates are recomputed.
    text=frozen.read_text();nodes=[n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='make_building_colliders_static']
    assert len(nodes)==1
    scope={'UsdPhysics':UsdPhysics,'Usd':Usd}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(frozen),'exec'),scope)
    normalization=scope['make_building_colliders_static'](stage)
    result['original_static_normalization_function_source']=ref(frozen)
    result['session_static_normalization_records']=normalization
    for prim in stage.Traverse():
        name=str(prim.GetPath())
        if prim.IsA(UsdGeom.Mesh) and ('/collision/' in name or '_collision' in prim.GetName() or 'MeshCollider' in prim.GetName()):
            UsdGeom.Imageable(prim).MakeInvisible()
    UsdPhysics.Scene.Define(stage,'/PhysicsScene').CreateGravityMagnitudeAttr(9.81)
    camera=UsdGeom.Camera.Define(stage,'/Factory80ActualObservation/Cam');camera.CreateFocalLengthAttr(24)
    camera.CreateClippingRangeAttr(Gf.Vec2f(.05,5000));op=UsdGeom.Xformable(camera.GetPrim()).AddTransformOp()
    vp=get_active_viewport();vp.camera_path=camera.GetPath();vp.resolution=(1280,720)
    updates(60);start=timeline.get_current_time();timeline.play();updates(240)
    assert timeline.is_playing() and timeline.get_current_time()>start and actual_advances>0
    def capture(name,eye,target):
        op.Set(look(Gf.Vec3d(*eye),Gf.Vec3d(*target)));updates(120)
        path=args.output/name
        job=asyncio.ensure_future(capture_viewport_to_file(vp,file_path=str(path.resolve())).wait_for_result())
        while not job.done():updates(1)
        job.result();last=-1;stable=0;deadline=time.time()+240
        while stable<3:
            if time.time()>deadline:raise TimeoutError('Actual observation image did not settle')
            updates(1);size=path.stat().st_size if path.is_file() else 0
            stable=stable+1 if size>0 and size==last else 0;last=size
        return {'file':str(path),'bytes':size,'sha256':ref(path)['sha256'],'eye':eye,'target':target,
                'actual_timeline_seconds_at_capture':timeline.get_current_time()}
    width,height=author['footprint_bounds_m'][2:];rise=author['floor_to_floor_m']
    views=[]
    for i,(x,y,tx,ty) in enumerate([(width*.25,height*.55,width*.75,height*.55),
                                  (width*.5,height*.75,width*.5,height*.25),
                                  (width*.75,height*.55,width*.25,height*.55)],1):
        views.append(capture(f'OBS{i:02d}_running_ground_aisle.png',[x,y,1.65],[tx,ty,1.65]))
    for stair in stairs:
        points=stair['world_path'];last=points[-1];previous=points[-2]
        dx=last[0]-previous[0];dy=last[1]-previous[1];length=math.hypot(dx,dy);dx/=length;dy/=length
        views.append(capture(f'OBS_S{stair["index"]+1:02d}_running_arrival.png',
                             [last[0]+dx*.25,last[1]+dy*.25,rise+1.65],
                             [last[0]+dx*3,last[1]+dy*3,rise+1.55]))
    end=timeline.get_current_time();timeline.stop();updates(30)
    assert ref(source)==before and end>start and actual_advances>0
    result.update(status='ACTUAL_SIMULATION_AND_CAPTURE_COMPLETED_NOT_VISUALLY_REVIEWED',
                  actual_simulation_executed=True,actual_simulation_update_count=actual_advances,
                  actual_timeline_start_seconds=start,actual_timeline_end_seconds=end,stills=views,
                  source_stage_after=ref(source),source_root_layer_unchanged=True,
                  source_unchanged_scope='root.usda layer bytes only; dependency immutability is separately checked by the selected native input readback',
                  utc_finished=datetime.now(timezone.utc).isoformat())
except Exception as exc:
    import traceback
    result.update(status='FAIL',error=repr(exc),traceback=traceback.format_exc(),
                  actual_simulation_update_count=actual_advances,source_stage_after=ref(source))
    raise
finally:
    close_error=None
    if app is not None:
        try:app.close();result['simulation_app_close_completed']=True
        except Exception as exc:
            close_error=exc;result.update(status='FAIL',simulation_app_close_completed=False,close_error=repr(exc))
    else:result['simulation_app_close_completed']='NOT EXECUTED; no constructed app returned'
    with (args.output/'ACTUAL_ISAAC_OBSERVATION.json').open('x') as stream:json.dump(result,stream,indent=2,ensure_ascii=False)
    if close_error is not None:raise close_error
