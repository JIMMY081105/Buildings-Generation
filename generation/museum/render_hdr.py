"""Native RTX PathTracing -> floating HdrColor -> common exposure/tone curve.
Original HDR and depth are retained on CephFS; final PNGs never start from LDR.
"""
from pathlib import Path
import asyncio,datetime,hashlib,json,math,time
import numpy as np
from PIL import Image
SOURCE_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
def run(app,job):
 import carb,omni.usd,omni.replicator.core as rep,omni.timeline
 from pxr import Usd,UsdGeom,UsdShade,UsdLux,Sdf,Gf
 source=Path(job['stage']);D=Path(job['output']);D.mkdir(exist_ok=True,parents=True);sha=hashlib.sha256(source.read_bytes()).hexdigest()
 binding_path=source.parent.parent/'SCENE_ASSET_BINDING.json';asset_binding=json.loads(binding_path.read_text()) if binding_path.exists() else None
 def verify_assets():
  if asset_binding:
   for name,digest in asset_binding['files_sha256'].items():assert hashlib.sha256((source.parent.parent/name).read_bytes()).hexdigest()==digest,'RENDER_SOURCE_ASSET_CHANGED:'+name
 verify_assets()
 ctx=omni.usd.get_context();omni.timeline.get_timeline_interface().stop();assert ctx.open_stage(str(source));deadline=time.monotonic()+900
 while ctx.get_stage_loading_status()[2]:assert time.monotonic()<deadline,'LOAD_TIMEOUT';app.update()
 stage=ctx.get_stage();stage.SetEditTarget(stage.GetSessionLayer());settings=carb.settings.get_settings();settings.set_bool('/app/useFabricSceneDelegate',False)
 wrappers=[];byproto={}
 for p in list(stage.Traverse()):
  if not p.IsInstance():continue
  refs=p.GetMetadata('references');items=refs.GetAppliedItems() if refs else []
  if len(items)!=1 or items[0].assetPath or not str(items[0].primPath).startswith('/MuseumPrototypes/'):continue
  key=str(items[0].primPath)
  if key not in byproto:
   layer=Sdf.Layer.CreateAnonymous('Museum29_material_wrapper');ws=Usd.Stage.Open(layer);root=UsdGeom.Xform.Define(ws,'/Wrapped');ws.SetDefaultPrim(root.GetPrim());root.GetPrim().GetReferences().AddReference(str(source),items[0].primPath);wrappers.append(layer);byproto[key]=layer
  p.GetReferences().SetReferences([Sdf.Reference(byproto[key].identifier,'/Wrapped')]);p.SetInstanceable(True)
 base={str(p.GetPath()):str(UsdGeom.Imageable(p).GetVisibilityAttr().Get() or 'inherited') for p in stage.Traverse() if p.IsA(UsdGeom.Imageable)}
 def mat(name,color):
  p='/World/M29CollisionLooks/'+name;m=UsdShade.Material.Define(stage,p);s=UsdShade.Shader.Define(stage,p+'/Shader');s.CreateIdAttr('UsdPreviewSurface');s.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color));s.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(.75);m.CreateSurfaceOutput().ConnectToSource(s.ConnectableAPI(),'surface');return m
 graymats={'stair':mat('StairOrange',(.80,.26,.045)),'floor':mat('FloorBlue',(.08,.32,.68)),'other':mat('OtherGray',(.38,.42,.45))}
 tone_mats31=[mat('TreadOrangeLight31',(.80,.26,.045)),mat('TreadOrangeDark31',(.36,.095,.012))]
 def cp_geom31():
  h=hashlib.sha256()
  for p in stage.Traverse():
   if not p.IsA(UsdGeom.Mesh) or not str(p.GetPath()).startswith('/World/Physics'):continue
   m=UsdGeom.Mesh(p);h.update((str(p.GetPath())+str(m.GetPointsAttr().Get())+str(m.GetFaceVertexCountsAttr().Get())+str(m.GetFaceVertexIndicesAttr().Get())+str(UsdGeom.XformCache().GetLocalToWorldTransform(p))+str(UsdGeom.Imageable(p).GetVisibilityAttr().Get())).encode())
  return h.hexdigest()
 if job.get('calibration_fixture'):
  light=UsdLux.DomeLight.Define(stage,'/World/M29CalibrationSky');light.CreateIntensityAttr(800);light.CreateColorAttr(Gf.Vec3f(.8,.86,1))
  light=UsdLux.DistantLight.Define(stage,'/World/M29CalibrationSun');light.CreateIntensityAttr(1800);light.CreateAngleAttr(1);UsdGeom.Xformable(light).AddRotateXYZOp().Set(Gf.Vec3f(25,-35,-30))
 cfg={'/rtx/rendermode':'PathTracing','/rtx/pathtracing/spp':32,'/rtx/pathtracing/totalSpp':256,'/rtx/pathtracing/maxBounces':8,'/rtx/pathtracing/optixDenoiser/enabled':True,'/rtx/post/histogram/enabled':False,'/rtx/post/lensFlares/enabled':False,'/rtx/post/bloom/enabled':False,'/rtx/post/glare/enabled':False,'/rtx/post/dof/enabled':False,'/app/viewport/grid/enabled':False,'/app/viewport/outline/enabled':False}
 cfg.update({'/exts/isaacsim.core.throttling/enable_async':False,'/exts/isaacsim.core.throttling/enable_manualmode':False,'/app/asyncRendering':False,'/app/asyncRenderingLowLatency':False,'/rtx/ecoMode/enabled':False})
 # ADJUST10: support both Kit107 light-visibility controls and newer ghost-factor names.
 cfg.update({'/rtx/raytracing/showLights':2,'/rtx/raytracing/invisLightReflectionsGhostFactor':1.,'/rtx/raytracing/invisLightRefractionsGhostFactor':1.,'/rtx/raytracing/invisLightReflectionsRoughnessThreshold':1.,'/rtx/raytracing/invisLightRoughnessThreshold':1.})
 for k,v in cfg.items():settings.set(k,v)
 rep.orchestrator.set_capture_on_play(False)
 cam=UsdGeom.Camera.Define(stage,'/World/M29Cameras/ActiveCapture');cam.CreateFStopAttr(0.0)
 product=rep.create.render_product(str(cam.GetPath()),(1920,1080),force_new=True,name='M29NativeHDR')
 hdr=rep.AnnotatorRegistry.get_annotator('HdrColor',device='cpu',do_array_copy=True);depth=rep.AnnotatorRegistry.get_annotator('distance_to_image_plane',device='cpu',do_array_copy=True)
 hdr.attach(product);depth.attach(product)
 def finish_view(view,p,record):
  data=np.load(p);A=data['rgb'];Z=data['depth'];valid=np.isfinite(Z)&(Z>.05)&(Z<500);Y=np.maximum(A,0)@np.array([.2126,.7152,.0722],dtype=np.float32)
  mask=valid.copy();bounds=job.get('building_bounds')
  if bounds:
   yy,xx=np.mgrid[:1080,:1920];dep=np.where(valid,Z,0);camloc=np.stack([(xx+.5-960)/1920*36/record['focal_length_mm']*dep,-(yy+.5-540)/1080*20.25/record['focal_length_mm']*dep,-dep,np.ones_like(dep)],axis=-1);world=camloc@np.asarray(record['camera_matrix']);lo=np.asarray(bounds[0]);hi=np.asarray(bounds[1]);mask&=((world[:,:,:3]>=lo)&(world[:,:,:3]<=hi)).all(2)
  assert mask.sum()>10000,'NO_ACTUAL_BUILDING_PIXELS'
  lum=Y[mask];positive=lum[lum>1e-7];assert len(positive)>10000
  median_target=float(view.get('linear_median_target',.18));gain=float(job.get('exposure_gain',median_target/max(float(np.median(positive)),1e-7)));gain=min(gain,10/max(float(np.quantile(positive,.98)),1e-7))
  X=np.maximum(A,0)*gain;L=X/(1+X);S=np.where(L<=.0031308,12.92*L,1.055*np.power(L,1/2.4)-.055);rgb=np.clip(np.round(S*255),0,255).astype(np.uint8)
  image=D/(view['id']+'.png');Image.fromarray(rgb).save(image);im=Image.fromarray(rgb);im.thumbnail((1600,1600));quality=88
  while True:
   im.save(D/(view['id']+'.jpg'),quality=quality,optimize=True)
   if (D/(view['id']+'.jpg')).stat().st_size<=600000:break
   quality-=5;assert quality>20
  clipped=float(((rgb.astype(float)@np.array([.2126,.7152,.0722]))[mask]>=250).mean());record.update({'image':str(image),'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'linear_exposure_gain':gain,'tone_curve':'Reinhard RGB x/(1+x), then IEC sRGB OETF, then round to uint8; no LDR brightening','building_exposure':{'pixels':int(mask.sum()),'brightness_ge250_fraction':clipped,'threshold':.02,'status':'PASS' if clipped<=.02 else 'FAIL'},'status':'PENDING_VISUAL_REVIEW','visual_review':None})
  from image_gate import check_image
  record['camera_fstop']=0.0;record['linear_median_target']=median_target;record['image_quality']=check_image(image,collision=bool(view.get('collision',False)))
  (D/(view['id']+'.json')).write_text(json.dumps(record,indent=2))
  assert record['image_quality']['status']=='PASS','FORMAL_PHOTO_BLACK_BLANK_OR_BLURRED_REJECTED:'+view['id']
  return record
 rows=[]
 try:
  capture_order=sorted(job['views'],key=lambda view:({5:0,11:1,12:2}.get(int(view.get('number',0)),3),view['id'])) if not job.get('calibration_fixture') else job['views']
  for view in capture_order:
   name=view['id'];assert '/' not in name and '..' not in name
   gray=view.get('collision',False)
   for path,val in base.items():
    p=stage.GetPrimAtPath(path)
    if not p or p.IsInstanceProxy():continue
    im=UsdGeom.Imageable(p);im.CreateVisibilityAttr().Set(val)
    if job.get('calibration_fixture'):pass
    elif path.startswith('/World/Physics'):im.CreateVisibilityAttr().Set('inherited' if gray else 'invisible');im.CreatePurposeAttr().Set('default' if gray else 'guide')
    elif gray and (p.IsA(UsdGeom.Mesh) or p.IsInstance()):im.CreateVisibilityAttr().Set('invisible')
    if p.IsA(UsdGeom.Mesh) and any(p.GetName().startswith(x) for x in view.get('hide_prefixes',[])):im.CreateVisibilityAttr().Set('invisible')
    if view.get('isolate_stair_slabs') and p.IsA(UsdGeom.Mesh) and not ('Stair' in path or p.GetName().startswith(('Floor','Landing','Bridge'))):im.CreateVisibilityAttr().Set('invisible')
   tone_rows31=[];cp_before31=cp_geom31() if gray and view.get('collision_tread_tone_variation31') else None
   if gray or job.get('calibration_fixture'):
    for p in stage.Traverse():
     if not p.IsA(UsdGeom.Mesh):continue
     if not job.get('calibration_fixture') and not str(p.GetPath()).startswith('/World/Physics'):continue
     n=p.GetName();role=p.GetAttribute('museum:role').Get() if p.HasAttribute('museum:role') else None
     guard=any(x in n for x in ['Handrail','Rail','Baluster','Post','Guard','Newel'])
     key='other' if role in ['wall','stair_wall','guard','handrail','railing','newel'] or guard else 'floor' if role=='floor' or n.startswith('Floor') else 'stair' if role in ['stair','tread','stair_beam'] or any(x in n for x in ['Landing','Platform','Bridge','StructuralBeam','Stringer']) else 'other'
     assert not (key=='stair' and (guard or role in ['wall','stair_wall'])),'ORANGE_WALL_OR_RAIL_FORBIDDEN_ADJUST06'
     material31=graymats[key]
     if gray and view.get('collision_tread_tone_variation31') and role=='tread':
      import re
      match=re.search(r'_Tread(\d+)',n);assert match,'ACTUAL_TREAD_INDEX_REQUIRED_FOR_TONE31';parity=int(match.group(1))%2;material31=tone_mats31[parity];tone_rows31.append({'name':n,'actual_role':role,'tread_index':int(match.group(1)),'material':str(material31.GetPath())})
     UsdShade.MaterialBindingAPI.Apply(p).Bind(material31,bindingStrength=UsdShade.Tokens.strongerThanDescendants)
   if cp_before31:assert cp_geom31()==cp_before31 and tone_rows31,'COLLISION_TONE31_MUST_NOT_CHANGE_GEOMETRY_OR_VISIBILITY'

   cam.CreateFocalLengthAttr(float(view.get('lens',24)));cam.CreateHorizontalApertureAttr(36);cam.CreateVerticalApertureAttr(20.25);cam.CreateClippingRangeAttr(Gf.Vec2f(.05,500));matrix=Gf.Matrix4d().SetLookAt(Gf.Vec3d(*view['eye']),Gf.Vec3d(*view['target']),Gf.Vec3d(0,0,1)).GetInverse();cam.MakeMatrixXform().Set(matrix)
   cam.CreateFStopAttr(0.0);assert cam.GetFStopAttr().Get()==0.0
   visible={'physics':0,'visual':0}
   for p in stage.Traverse(Usd.TraverseInstanceProxies()):
    if p.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(p).ComputeVisibility()!='invisible' and str(UsdGeom.Imageable(p).ComputePurpose()) in ['default','render']:
     visible['physics' if str(p.GetPath()).startswith('/World/Physics') or job.get('calibration_fixture') else 'visual']+=1
   if gray:assert visible['physics']>0 and visible['visual']==0,'COLLISION_VIEW_HAS_VISUAL_GEOMETRY'
   for _ in range(10):app.update()
   attempts=[];deadline=time.monotonic()+900
   for attempt in range(1,9):
    assert settings.get('/app/asyncRendering') is False,'ASYNC_RENDERING_REENABLED'
    future=asyncio.ensure_future(rep.orchestrator.step_async(rt_subframes=8,delta_time=0.,pause_timeline=True,wait_for_render=True))
    while not future.done():assert time.monotonic()<deadline,'RTX_TIMEOUT';app.update()
    future.result()
    value=hdr.get_data(device='cpu',do_array_copy=True,use_legacy_structure=True);value=value.get('data') if isinstance(value,dict) else value;A=np.asarray(value)
    value=depth.get_data(device='cpu',do_array_copy=True,use_legacy_structure=True);Z=np.asarray(value.get('data') if isinstance(value,dict) else value).squeeze().copy()
    info={'attempt':attempt,'shape':list(A.shape),'dtype':str(A.dtype),'size':int(A.size),'depth_shape':list(Z.shape),'async_rendering':settings.get('/app/asyncRendering'),'finite_fraction':float(np.isfinite(A).mean()) if A.size and np.issubdtype(A.dtype,np.number) else None,'minimum':float(np.nanmin(A)) if A.size and np.issubdtype(A.dtype,np.number) else None,'maximum':float(np.nanmax(A)) if A.size and np.issubdtype(A.dtype,np.number) else None}
    attempts.append(info);(D/(name+'_HDR_BUFFER_METADATA.json')).write_text(json.dumps({'render_product_path':str(product.path),'attempts':attempts},indent=2));print('HDR_BUFFER_METADATA',name,info,flush=True)
    if A.size:assert np.issubdtype(A.dtype,np.floating),'HDR_MUST_BE_NATIVE_FLOAT_NOT_LDR_BYTES'
    if A.ndim!=3 or A.shape[:2]!=(1080,1920) or A.shape[2]<3 or Z.shape!=(1080,1920):continue
    assert np.isfinite(A[:,:,:3]).all(),'INVALID_RGB_HDR_VALUES'
    valid=np.isfinite(Z)&(Z>.05)&(Z<500)
    if valid.sum()>10000 and A[:,:,:3][valid].std(dtype=np.float32)>1e-6:break
   else:raise RuntimeError('NATIVE_HDR_NOT_READY_AFTER_8_ACTUAL_CAPTURES')
   native_dtype=str(A.dtype);A=A[:,:,:3].astype(np.float32).copy()
   hdrpath=D/(name+'_LINEAR_HDR.npz');np.savez_compressed(hdrpath,rgb=A,depth=Z)
   record={'id':name,'source_stage':str(source),'source_stage_sha256':sha,'renderer_handler_sha256':SOURCE_SHA,'renderer':settings.get('/rtx/rendermode'),'settings':{k:settings.get(k) for k in cfg},'camera_path':str(cam.GetPath()),'camera_matrix':np.asarray(matrix).tolist(),'focal_length_mm':cam.GetFocalLengthAttr().Get(),'horizontal_aperture':36,'vertical_aperture':20.25,'resolution':[1920,1080],'hdr_annotator':'HdrColor','native_hdr_dtype':native_dtype,'stored_hdr_dtype':str(A.dtype),'hdr_file':str(hdrpath),'hdr_sha256':hashlib.sha256(hdrpath.read_bytes()).hexdigest(),'native_geometry_pixels':int(valid.sum()),'actual_visible_meshes':visible,'collision':gray,'eye':view['eye'],'target':view['target'],'hide_prefixes':view.get('hide_prefixes',[]),'status':'HDR_CAPTURED_PENDING_EXPOSURE_AND_VISUAL_REVIEW'}
   if cp_before31:record['collision_tread_tone31']={'before_geometry_visibility_sha256':cp_before31,'after_geometry_visibility_sha256':cp_geom31(),'actual_tread_bindings':tone_rows31,'scope':'Alternating two orange debug material tones on actual CP tread meshes only; retained geometry/visibility and floor blue/guard gray. No fake decorative strip in collision view.'}
   record.update({k:view[k] for k in ['actual_gallery_review_required','projected_gallery_candidates','planned_principal_exhibits','gallery_layout_sha256','floor_indices'] if k in view});record.update({'render_product_path':str(product.path),'capture_attempts':attempts});print('HDR_CAPTURED',name,float(A.max()),flush=True);rows.append(finish_view(view,hdrpath,record));(D/'CAPTURE_PROGRESS.json').write_text(json.dumps({'status':'PARTIAL_CAPTURE','frames':rows},indent=2))
  rows.sort(key=lambda row:row['id'])
  assert len({x['sha256'] for x in rows})==len(rows),'DUPLICATE_PHOTO_HASHES';assert hashlib.sha256(source.read_bytes()).hexdigest()==sha;verify_assets()
  if asset_binding:
   for row in rows:row['source_asset_binding_sha256']=hashlib.sha256(binding_path.read_bytes()).hexdigest()
  result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'CAPTURED_PENDING_VISUAL_REVIEW','source_stage_sha256':sha,'frames':rows,'native_hdr_source':True,'actual_renderer':settings.get('/rtx/rendermode'),'source_unchanged':True};(D/'PHOTOS.json').write_text(json.dumps(result,indent=2));return result
 finally:hdr.detach();depth.detach();product.destroy();ctx.close_stage()
