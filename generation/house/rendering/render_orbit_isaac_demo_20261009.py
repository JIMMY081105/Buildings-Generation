"""Render-only Isaac session: a turntable orbit using the proven 01_overview framing.

Demo video only. No physics, no geometry gate, no layer is saved: the Floor2
cutaway and the lights are authored into the session layer of a transient stage.
"""
import argparse,json,sys,math,asyncio,time,subprocess
from pathlib import Path
ap=argparse.ArgumentParser()
ap.add_argument('--preparation',type=Path,required=True)
ap.add_argument('--output',type=Path,required=True)
ap.add_argument('--frames',type=int,default=120)
ap.add_argument('--radius',type=float,default=15.56)
ap.add_argument('--height-offset',type=float,default=9.0)
ap.add_argument('--total-spp',type=int,default=120)
ap.add_argument('--settle',type=int,default=180)
ap.add_argument('--start',type=int,default=0)
ap.add_argument('--end',type=int,default=-1)
args,_=ap.parse_known_args()
W=Path('/cephfs/shared/yixing/outputs/diversity54_20260908');sys.path.insert(0,str(W/'scripts'))
from evidence_common import ref,read_json,write_new,utc_now
prep=read_json(args.preparation);assert ref(prep['stage']['path'])==prep['stage']
plan=read_json(W/'SIGNATURE_PLAN_CAP8_R8_TRACKS.json')
source=next(r['source'] for r in plan['rows'] if r['building']==prep['building'])
br=source['source_build_report'];b=read_json(br['path'])
from isaacsim import SimulationApp
app=SimulationApp({'headless':True,'renderer':'RayTracedLighting','width':1280,'height':720,'active_gpu':0,'physics_gpu':0,'multi_gpu':False})
import carb,omni.usd
from omni.kit.viewport.utility import capture_viewport_to_file,get_active_viewport
from pxr import Gf,Usd,UsdGeom,UsdLux
def updates(n):
 for _ in range(n):app.update()
def look_at(eye,target):
 fwd=(target-eye).GetNormalized();up=Gf.Vec3d(0,0,1)
 if abs(Gf.Dot(fwd,up))>.999:up=Gf.Vec3d(0,1,0)
 right=Gf.Cross(fwd,up).GetNormalized();tu=Gf.Cross(right,fwd);m=Gf.Matrix4d(1)
 m.SetRow3(0,right);m.SetRow3(1,tu);m.SetRow3(2,-fwd);m.SetRow3(3,eye);return m
def rotate(p,degrees):
 x,y=float(p[0]),float(p[1]);return {0:(x,y),90:(-y,x),180:(-x,-y),270:(y,-x)}[degrees%360]
try:
 (args.output/'frames').mkdir(parents=True,exist_ok=True)
 ctx=omni.usd.get_context();assert ctx.open_stage(prep['stage']['path'])
 while ctx.get_stage_loading_status()[2]:updates(1)
 updates(250);stage=ctx.get_stage();stage.SetEditTarget(stage.GetSessionLayer())
 for p in stage.Traverse():
  s=str(p.GetPath())
  if p.IsA(UsdGeom.Mesh) and ('/collision/' in s or 'MeshCollider' in p.GetName() or '_collision' in p.GetName() or p.GetName().startswith('Floor2CollisionPlane')):UsdGeom.Imageable(p).MakeInvisible()
 hole=b['opening_rect'];cx=(hole[0]+hole[2])/2;cy=(hole[1]+hole[3])/2
 f2f=float(b['floor_to_floor_m']);rot=int(b.get('stair_rotation_deg',0))
 def around(dx,dy,z):
  x,y=rotate((dx,dy),rot);return Gf.Vec3d(cx+x,cy+y,z)
 dome=UsdLux.DomeLight.Define(stage,'/Cap8Review/Dome');dome.CreateIntensityAttr(1000);dome.CreateColorAttr(Gf.Vec3f(.5,.5,.5))
 key=UsdLux.DistantLight.Define(stage,'/Cap8Review/Key');key.CreateIntensityAttr(2400);key.CreateColorAttr(Gf.Vec3f(1));key.CreateAngleAttr(2)
 UsdGeom.Xformable(key.GetPrim()).AddRotateXYZOp().Set(Gf.Vec3f(-48,0,32))
 fill=UsdLux.SphereLight.Define(stage,'/Cap8Review/Fill');fill.CreateIntensityAttr(90000);fill.CreateColorAttr(Gf.Vec3f(1));fill.CreateRadiusAttr(.5)
 UsdGeom.Xformable(fill.GetPrim()).AddTranslateOp().Set(Gf.Vec3d(cx,cy,f2f-.5))
 cam=UsdGeom.Camera.Define(stage,'/Cap8Review/Cam');cam.CreateFocalLengthAttr(24);cam.CreateClippingRangeAttr(Gf.Vec2f(.05,5000))
 op=UsdGeom.Xformable(cam.GetPrim()).AddTransformOp()
 vp=get_active_viewport();vp.camera_path=cam.GetPath();vp.resolution=(1280,720)
 st=carb.settings.get_settings();st.set_string('/rtx/rendermode','PathTracing')
 st.set_bool('/app/viewport/grid/enabled',False);st.set_bool('/app/viewport/outline/enabled',False);st.set_int('/rtx/pathtracing/spp',32);st.set_int('/rtx/pathtracing/totalSpp',args.total_spp);st.set_int('/rtx/pathtracing/maxBounces',6)
 # the 01_overview cutaway: drop Floor2 walls and ceilings so the interior reads from every angle
 def hidden(root,prefixes):
  rp=stage.GetPrimAtPath(root)
  if not rp:return 0
  n=0
  for p in Usd.PrimRange(rp):
   if any(p.GetName().startswith(x) for x in prefixes) and UsdGeom.Imageable(p):UsdGeom.Imageable(p).MakeInvisible();n+=1
  return n
 cut=hidden('/World/Floor2',('wall_','ceiling_'))
 target=Gf.Vec3d(cx,cy,f2f*.5)
 updates(400)
 lo=args.start;hi=args.frames if args.end<0 else args.end
 for i in range(lo,hi):
  ang=2*math.pi*(i/args.frames)
  eye=around(args.radius*math.cos(ang),args.radius*math.sin(ang),f2f+args.height_offset)
  op.Set(look_at(eye,target));updates(args.settle)
  image=args.output/'frames'/('f%04d.png'%i)
  hh=capture_viewport_to_file(vp,file_path=str(image.resolve()));t=asyncio.ensure_future(hh.wait_for_result())
  while not t.done():updates(1)
  t.result()
  last=None;stable=0
  while stable<3:
   updates(1);size=image.stat().st_size if image.exists() else 0
   stable=stable+1 if size>0 and size==last else 0;last=size;time.sleep(.05)
  print(utc_now(),'frame',i,'captured',flush=True)
 write_new(args.output/'ORBIT_ISAAC.json',{'building':prep['building'],'stage':prep['stage'],
   'frames':args.frames,'radius_m':args.radius,'height_offset_m':args.height_offset,
   'total_spp':args.total_spp,'floor2_prims_hidden_for_cutaway':cut,
   'camera_frame':'01_overview geometry swept through 360 degrees',
   'device_query':subprocess.check_output(['nvidia-smi','--query-gpu=name,uuid','--format=csv,noheader'],text=True).strip(),
   'demo_video_only':True,'physics_simulation_executed':False,'geometry_gates_executed':False,
   'geometry_remeasured':False,'formal_acceptance_added':False,'stage_modified':False,
   'cannot_support':'演示视频；不支持 READY、几何门禁通过或正式验收。'})
 print('ORBIT_DONE',flush=True)
finally:app.close()
