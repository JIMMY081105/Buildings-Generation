"""RTX pilot stills and 120-frame cutaway orbit; no validation or saved edits."""
import argparse,asyncio,hashlib,json,math,subprocess,time
from pathlib import Path
ap=argparse.ArgumentParser();ap.add_argument('--authoring',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
ap.add_argument('--frames',type=int,default=120);args,_=ap.parse_known_args()
receipt=json.loads(args.authoring.read_text());sp=Path(receipt['stage']['path']);before=hashlib.sha256(sp.read_bytes()).hexdigest()
assert before==receipt['stage']['sha256'];assert int(receipt['building'].rsplit('_',1)[1])<=6
from isaacsim import SimulationApp
app=SimulationApp({'headless':True,'renderer':'RayTracedLighting','width':1280,'height':720,'active_gpu':0,'physics_gpu':0,'multi_gpu':False})
import carb,omni.usd
from omni.kit.viewport.utility import capture_viewport_to_file,get_active_viewport
from pxr import Gf,Usd,UsdGeom,UsdLux
def updates(n):
    for _ in range(n):app.update()
def look(eye,target):
    f=(target-eye).GetNormalized();up=Gf.Vec3d(0,0,1)
    if abs(Gf.Dot(f,up))>.999:up=Gf.Vec3d(0,1,0)
    right=Gf.Cross(f,up).GetNormalized();tu=Gf.Cross(right,f);m=Gf.Matrix4d(1)
    m.SetRow3(0,right);m.SetRow3(1,tu);m.SetRow3(2,-f);m.SetRow3(3,eye);return m
result={'building':receipt['building'],'stage':receipt['stage'],'images_actually_viewed':False,
        'render_only':True,'formal_acceptance_added':False,'physics_simulation_executed':False,
        'cannot_support':'RTX静态图与演示运镜不证明冻结门禁、可走面积、真实人形运动策略或正式验收。'}
try:
    (args.output/'frames').mkdir(parents=True,exist_ok=True)
    ctx=omni.usd.get_context();assert ctx.open_stage(str(sp))
    while ctx.get_stage_loading_status()[2]:updates(1)
    updates(250);stage=ctx.get_stage();stage.SetEditTarget(stage.GetSessionLayer())
    for p in stage.Traverse():
        s=str(p.GetPath())
        if p.IsA(UsdGeom.Mesh) and ('/collision/' in s or '_collision' in p.GetName() or 'MeshCollider' in p.GetName()):
            UsdGeom.Imageable(p).MakeInvisible()
    width,height=receipt['footprint_bounds_m'][2:];h=receipt['floor_to_floor_m'];cx=width/2;cy=height/2
    cam=UsdGeom.Camera.Define(stage,'/Factory80Review/Cam');cam.CreateFocalLengthAttr(24)
    cam.CreateClippingRangeAttr(Gf.Vec2f(.05,5000));op=UsdGeom.Xformable(cam.GetPrim()).AddTransformOp()
    vp=get_active_viewport();vp.camera_path=cam.GetPath();vp.resolution=(1280,720)
    settings=carb.settings.get_settings();settings.set_string('/rtx/rendermode','PathTracing')
    settings.set_int('/rtx/pathtracing/spp',32);settings.set_int('/rtx/pathtracing/totalSpp',120);settings.set_int('/rtx/pathtracing/maxBounces',6)
    settings.set_bool('/app/viewport/grid/enabled',False);settings.set_bool('/app/viewport/outline/enabled',False)
    def capture(name,eye,target):
        op.Set(look(Gf.Vec3d(*eye),Gf.Vec3d(*target)));updates(180)
        path=args.output/name
        task=asyncio.ensure_future(capture_viewport_to_file(vp,file_path=str(path.resolve())).wait_for_result())
        while not task.done():updates(1)
        task.result();last=-1;stable=0;deadline=time.time()+240
        while stable<3:
            if time.time()>deadline:raise TimeoutError('Image did not settle '+str(path))
            updates(1);size=path.stat().st_size if path.exists() else 0
            stable=stable+1 if size>0 and size==last else 0;last=size
        return {'file':str(path),'bytes':size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'eye':eye,'target':target}
    updates(400);views=[]
    views.append(capture('01_exterior.png',[cx-width*.85,cy-height*.85,max(width,height)*.8+9],[cx,cy,4]))
    # Explicit visual cutaway in session layer. Geometry/colliders on disk stay intact.
    roof=stage.GetPrimAtPath('/World/FactoryStructure/Roof');UsdGeom.Imageable(roof).MakeInvisible()
    wall_roots=[r['path'] for r in receipt['structure'] if r['type']=='perimeter_wall']
    walls=[p for root in wall_roots for p in Usd.PrimRange(stage.GetPrimAtPath(root))
           if p.IsA(UsdGeom.Mesh) and '_collision' not in p.GetName()]
    if len(walls)!=len(wall_roots):raise ValueError('Actual perimeter visual mesh inventory differs from authoring receipt')
    for p in walls:UsdGeom.Imageable(p).MakeInvisible()
    views.append(capture('02_factory_cutaway.png',[cx-width*.8,cy-height*.8,max(width,height)*.68+9],[cx,cy,3]))
    views.append(capture('03_topdown_cutaway.png',[cx,cy,max(width,height)*1.45],[cx,cy,0]))
    for p in walls:UsdGeom.Imageable(p).MakeVisible()
    views.append(capture('04_ground_aisle.png',[width*.5,height*.55,1.65],[width*.5,height*.25,1.65]))
    for st in receipt['stairs']:
        pts=st['world_path'];last=pts[-1];dx=last[0]-pts[-2][0];dy=last[1]-pts[-2][1];length=math.hypot(dx,dy);dx/=length;dy/=length
        si=st['index']+1
        views.append(capture(f'05_S{si:02d}_arrival.png',[last[0]+dx*.25,last[1]+dy*.25,h+1.65],[last[0]+dx*3,last[1]+dy*3,h+1.55]))
        mid=pts[len(pts)//2];views.append(capture(f'06_S{si:02d}_actual_colour.png',[mid[0]+7,mid[1]+1,h*.8],[mid[0],mid[1],h*.5]))
    for p in walls:UsdGeom.Imageable(p).MakeInvisible()
    radius=math.hypot(width,height)*.85;frames=[]
    for i in range(args.frames):
        angle=2*math.pi*i/max(1,args.frames-1)
        frames.append(capture(f'frames/f{i:04d}.png',[cx+radius*math.cos(angle),cy+radius*math.sin(angle),max(width,height)*.65+9],[cx,cy,3]))
        print(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'captured',i,flush=True)
    device=subprocess.check_output(['nvidia-smi','--query-gpu=name,uuid,memory.total','--format=csv,noheader'],text=True).strip()
    result.update({'status':'RTX_CAPTURED_NOT_VISUALLY_REVIEWED','stills':views,'frames':frames,'frame_count':len(frames),
                   'orbit_degrees':360,'orbit_first_angle_radians':0.0,'orbit_last_angle_radians':2*math.pi,
                   'orbit_radius_m':radius,'session_cutaway_hidden':['/World/FactoryStructure/Roof',*[str(p.GetPath()) for p in walls]],
                   'device_query':device,'requested_activeGpu':0,'requested_cudaDevice':0,
                   'startup_log_device_confirmation_required':True,'source_sha256_after':hashlib.sha256(sp.read_bytes()).hexdigest()})
    assert result['source_sha256_after']==before
    (args.output/'RTX_RENDER_RESULT.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
except Exception as exc:
    import traceback
    result.update({'status':'FAIL','error':repr(exc),'traceback':traceback.format_exc()})
    args.output.mkdir(parents=True,exist_ok=True);(args.output/'RTX_RENDER_RESULT.json').write_text(json.dumps(result,indent=2,ensure_ascii=False));raise
finally:app.close()
