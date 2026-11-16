"""Author a source-bound furnished candidate; validation/delivery stay separate."""
from pathlib import Path
import os,sys,json,hashlib,time,shutil,collections
Path('/proc/self/oom_score_adj').write_text('1000');os.environ['OMP_NUM_THREADS']='8'
import bpy
from PIL import Image
from blender_common import Builder
from museum_style import materials,architecture
from museum_exhibits import gallery
from image_gate import check_image
r=Path('/cephfs/shared/yixing/outputs/museum29_20261107');bid=sys.argv[1];modelcandidate=sys.argv[2] if len(sys.argv)>2 else 'model_candidate001';version=sys.argv[3] if len(sys.argv)>3 else 'furnished_candidate001';source=r/'buildings'/bid/modelcandidate/'RAW_MODEL.json';model=json.loads(source.read_text());spec=model['spec'];out=r/'buildings'/bid/version;out.mkdir(exist_ok=False)
code=out/'source_code';code.mkdir();hashes={}
for p in Path(__file__).parent.glob('*.py'):
 shutil.copyfile(p,code/p.name);hashes[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
B=Builder(spec,out);materials(B);room=architecture(B,model)
if spec['style_family']=='marine':
 from museum_marine import marine
 exhibits=marine(B,model,room)
elif spec['style_family']=='glasshouse':
 if spec.get('glasshouse22'):
  from museum_tropical_plants import exhibition
 elif spec.get('glasshouse19'):
  from museum_tropical import exhibition
 else:
  from museum_glasshouse import exhibition
 exhibits=exhibition(B,model,room)
elif bid in ['M01','M03','M10','M12','M13','M14','M18']:exhibits=gallery(B,model,room)
elif spec['style_family']=='east_asian' and spec.get('east_asian13'):
 from museum_east_asian import exhibition
 exhibits=exhibition(B,model,room)
elif bid=='M07' and spec.get('gothic14'):
 from museum_gothic import exhibition
 exhibits=exhibition(B,model,room)
elif bid=='M17':
 from museum_deco import curios
 exhibits=curios(B,model,room)
elif bid=='M20' and spec.get('nordic28'):
 from museum_nordic import exhibition
 exhibits=exhibition(B,model,room)
elif bid=='M11':
 from museum_contemporary_displays import aviation
 exhibits=aviation(B,model,room)
elif bid=='M09':
 from museum_industrial_groups import exhibition
 exhibits=exhibition(B,model,room)
else:
 from museum_themes import objects
 exhibits=objects(B,model,room)
B.save_metadata();(out/'spec.json').write_text(json.dumps({**spec,'exhibition':{**spec['exhibition'],'asset_selection_status':'BOUND_TO_LAYOUT_AND_LICENSES','items':B.layout}},ensure_ascii=False,indent=2));shutil.copyfile(source,out/'RAW_MODEL.json')
counts=collections.Counter(row.get('floor') for row in B.layout if row.get('kind') in ['painting','exhibit']);assert all(counts[level]>=max(15,spec['exhibition'].get('min_exhibits_per_floor',15)) for level in range(1,spec['floors']+1)),'EXHIBIT_MINIMUM_NOT_MET'
w,h=room['size'];H=spec['floor_height_m'];form=spec['stair']['form']
if bid=='M04':views=[('01_exterior_preview',(w*.60,-h*1.85,H*len(model['floors'])*1.02),(0,-h*.10,(H*len(model['floors'])+5.8)/2),30),('03_stair_preview',(0,-10,1.7),(0,1,6.3),12),('04_glass_roof_preview',(8,-6,1.7),(3.5,0,H*3.4),14),('05_stair_slab_preview',(9.5,3.8,H+2.5),(6.8,5.3,H+.02),24)]
elif bid=='M09':views=[('01_exterior_preview',(w*.5,-h*1.5,H*1.9),(0,-h*.15,H*1.3),28),('03_stair_preview',(10,-11,2.1),(-2,-1,3.8),18),('05_stair_slab_preview',(-7,-7,H+2.2),(-3,-3,H),25)]
elif bid=='M26':views=[('01_exterior_preview',(w*1.15,-h*1.35,H*3.8),(0,0,H*1.35),32),('03_stair_preview',(4.2,-3,1.7),(0,0,3.3),11),('05_stair_slab_preview',(-7,-7,H+2.2),(-3,-3,H),25),('06_gallery_preview',(9.45,-14.13,1.6),(7.5,-11.22,1.6),23)]
elif form=='I':views=[('01_exterior_preview',(w*1.25,-h*1.45,H*4.2),(0,-2,H*1.3),32),('03_stair_preview',(0,-11,2.4),(0,0,3.2),18),('04_ceiling_preview',(12,-4,1.7),(9,5,H-.4),20),('05_stair_slab_preview',(9.5,3.8,H+2.5),(6.8,5.3,H+.02),24),('06_gallery_preview',(w*.42,-h*.40,1.65),(w*.28,-10.8,1.6),23)]
elif form=='C':views=[('01_exterior_preview',(w*.9,-h*1.1,H*3),(0,0,H*1.3),28),('03_stair_preview',(-7,8,3),(.5,-1,H),20),('05_stair_slab_preview',(-1,8,H+2.2),(2.38,3.6,H),25),('06_gallery_preview',(w*.42,-h*.37,1.65),(w*.28,-h*.25,1.6),23)]
else:views=[('01_exterior_preview',(w*.9,-h*1.1,H*3.2),(0,0,H*1.4),28),('03_stair_preview',(5,-5,2),(-.5,0,H),18),('05_stair_slab_preview',(-7,-7,H+2.2),(-3,-3,H),25),('06_gallery_preview',(w*.38,-h*.33,1.65),(w*.28,-h*.25,1.6),23)]
if model['stair_placements'][0]['origin'][0]==-9.5:
 import numpy as np
 e=model['exits'][0];direction=np.array(e['direction_xy']);end=np.array(e['start_xy'])+direction*e['landing_length_m'];side=np.array([-direction[1],direction[0]]);eye=(*tuple(end+direction*2+side*2),e['floor_z_m']+3.);target=(*tuple(end-direction*.7),e['floor_z_m']+.03)
 views=[v for v in views if not v[0].startswith(('03_','05_'))]+[('03_stair_preview',(-2,-10,2.2),(-11,-1,H*.75),18),('05_stair_slab_preview',eye,target,24)]
if bid=='M22' and spec.get('east_asian13'):views=[v for v in views if not v[0].startswith('01_')]+[('01_exterior_preview',(w*.35,-h*.5,H*spec['floors']*4.3),(5.5,0,H*spec['floors']*.65),28)]
if bid=='M07' and spec.get('gothic14'):views=[v for v in views if not v[0].startswith('01_')]+[('01_exterior_preview',(w*1.1,-h*1.4,H*spec['floors']*1.7),(0,0,H*spec['floors']*.72),30),('02_cloister_courtyard_preview',(1,-3.8,2.0),(8,3.8,2.3),14)]
if bid in ['M07','M08']:
 import numpy as np
 e=model['exits'][0];start=np.array(e['start_xy']);direction=np.array(e['direction_xy']);side=np.array([-direction[1],direction[0]]);views=[v for v in views if not v[0].startswith('05_')]+[('05_stair_slab_preview',(*tuple(start+direction*3.4+side*.65),e['floor_z_m']+1.80),(*tuple(start-direction*.45),e['floor_z_m']-.12),22)]
if bid!='M01':
 if bid=='M02' and spec.get('guard_repair16'):
  from museum_classical import connection_view
  eye,target,lens=connection_view(model);views=[v for v in views if not v[0].startswith('05_')]+[('05_stair_slab_preview',eye,target,lens)]
 if spec.get('gothic18'):
  from museum_gothic_towers import connection_view
  eye,target,lens=connection_view(model);views=[v for v in views if not v[0].startswith('05_')]+[('05_stair_slab_preview',eye,target,lens)]
 from gallery_planning import gallery_views
 planned=[{'number':n} for n in [6,7,8]];gallery_views(out,model,planned);views=[q for q in views if not q[0].startswith('06_')]+[(f"{q['number']:02}_gallery_preview",q['eye'],q['target'],q['lens']) for q in planned]
if spec.get('glasshouse22'):
 from museum_tropical_plants import connection_view
 eye,target,lens=connection_view(model);views=[v for v in views if not v[0].startswith(('03_','05_'))]+[('03_stair_preview',(-7,-8,2.3),(-14,0,6),18),('05_stair_slab_preview',eye,target,lens)] if bid=='M28' else [v for v in views if not v[0].startswith('05_')]+[('05_stair_slab_preview',eye,target,lens)]
from exterior_camera import author as exterior16
e16=exterior16(B,model);views=[(name,e16['eye'],e16['target'],e16['lens']) if name.startswith('01_') else (name,eye,target,lens) for name,eye,target,lens in views]
cameras=[B.camera(name,eye,target,lens) for name,eye,target,lens in views];B.scene.render.engine='CYCLES';B.scene.cycles.samples=24;B.scene.cycles.use_denoising=True;B.scene.cycles.device='CPU';B.scene.render.resolution_x=1600;B.scene.render.resolution_y=900;B.scene.render.resolution_percentage=100;B.scene.render.image_settings.file_format='PNG';B.scene.view_settings.view_transform='AgX';bpy.ops.wm.save_as_mainfile(filepath=str(out/'museum.blend'))
records=[];quality_records=[]
for camera in cameras:
 B.scene.camera=camera;B.scene.render.filepath=str(out/(camera.name+'.png'));bpy.ops.render.render(write_still=True);im=Image.open(B.scene.render.filepath).convert('RGB');quality=87
 while True:
  im.save(out/(camera.name+'.jpg'),quality=quality,optimize=True)
  if (out/(camera.name+'.jpg')).stat().st_size<=600000:break
  quality-=5;assert quality>20
 qa=check_image(out/(camera.name+'.jpg'));quality_records.append({'image':camera.name+'.jpg',**qa});(out/'PREVIEW_VALIDATION.json').write_text(json.dumps(quality_records,indent=2));assert qa['status']=='PASS','PREVIEW_BLACK_BLANK_OR_BLURRED_REJECTED:'+camera.name
 records.append({'id':camera.name,'camera_matrix':[list(x) for x in camera.matrix_world],'eye':list(camera.location),'focal_length_mm':camera.data.lens,'preview':str(out/(camera.name+'.jpg')),'renderer':'Blender Cycles CPU intermediate ONLY'})
(out/'AUTHORING_RECEIPT.json').write_text(json.dumps({'building':bid,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'raw_source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'blend_sha256':hashlib.sha256((out/'museum.blend').read_bytes()).hexdigest(),'authoring_source_sha256':hashes,'status':'FURNISHED_CANDIDATE_CHECKS_NOT_EXECUTED','exhibits_per_floor':dict(counts),'exhibition_summary':exhibits,'views':records},indent=2));print('FURNISHED_CANDIDATE_COMPLETE',bid,str(out),flush=True)
# Standalone bpy wheels can segfault in interpreter teardown after valid save.
# Exit only after every render, quality assertion and source-bound receipt.
sys.stdout.flush();sys.stderr.flush();os._exit(0)
