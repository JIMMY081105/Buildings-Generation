"""M0 authoring preview only. This file never labels a furnished museum READY."""
from pathlib import Path
import os,sys,json,hashlib,time
Path('/proc/self/oom_score_adj').write_text('1000');os.environ['OMP_NUM_THREADS']='8'
import bpy
from PIL import Image
from blender_common import Builder
from museum_style import materials,architecture
from image_gate import check_image
r=Path('/cephfs/shared/yixing/outputs/museum29_20261107');bid=sys.argv[1];candidate=sys.argv[2] if len(sys.argv)>2 else 'model_candidate001';source=r/'buildings'/bid/candidate/'RAW_MODEL.json';model=json.loads(source.read_text());spec=model['spec'];out=r/'buildings'/bid/candidate/'shell_appearance001';out.mkdir(exist_ok=False);source_dir=Path(__file__).parent;source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source_dir.glob('*.py')}
B=Builder(spec,out);materials(B);room=architecture(B,model)
w,h=room['size'];H=spec['floor_height_m'];form=spec['stair']['form']
if form=='I':views=[('01_shell_exterior',(w*1.25,-h*1.45,H*4.2),(0,-2,H*1.3),32),('03_shell_stair',(0,-11,2.4),(0,-.2,3.5),18),('04_shell_ceiling',(10,-11,1.7),(10,0,H*1.15),20),('05_shell_stair_slab',(8,-9.5,H+2.2),(3.5,-5.8,H),24)]
elif form=='C':views=[('01_shell_exterior',(w*.9,-h*1.1,H*3),(0,0,H*1.3),28),('03_shell_stair',(-7,8,3),(.5,-1,H),20),('05_shell_stair_slab',(-1,8,H+2.2),(2.38,3.6,H),25)]
else:views=[('01_shell_exterior',(w*.9,-h*1.1,H*3.2),(0,0,H*1.4),28),('03_shell_stair',(5,-5,2),(-.5,.0,H),18),('05_shell_stair_slab',(-7,-7,H+2.2),(-3,-3,H),25)]
cameras=[]
for name,eye,target,lens in views:cameras.append(B.camera(name,eye,target,lens))
B.save_metadata();B.scene.render.engine='CYCLES';B.scene.cycles.samples=24;B.scene.cycles.use_denoising=True;B.scene.cycles.device='CPU';B.scene.render.resolution_x=1600;B.scene.render.resolution_y=900;B.scene.render.resolution_percentage=100;B.scene.render.image_settings.file_format='PNG';B.scene.view_settings.view_transform='AgX'
bpy.ops.wm.save_as_mainfile(filepath=str(out/'shell.blend'))
records=[];quality_records=[]
for camera in cameras:
 B.scene.camera=camera;B.scene.render.filepath=str(out/(camera.name+'.png'));bpy.ops.render.render(write_still=True);im=Image.open(B.scene.render.filepath).convert('RGB');quality=87
 while True:
  im.save(out/(camera.name+'.jpg'),quality=quality,optimize=True)
  if (out/(camera.name+'.jpg')).stat().st_size<=600000:break
  quality-=5
 qa=check_image(out/(camera.name+'.jpg'));quality_records.append({'image':camera.name+'.jpg',**qa});(out/'PREVIEW_VALIDATION.json').write_text(json.dumps(quality_records,indent=2));assert qa['status']=='PASS','PREVIEW_BLACK_OR_BLANK_REJECTED:'+camera.name
 records.append({'id':camera.name,'camera_matrix':[list(x) for x in camera.matrix_world],'eye':list(camera.location),'focal_length_mm':camera.data.lens,'preview':str(out/(camera.name+'.jpg')),'renderer':'Blender Cycles CPU intermediate preview ONLY'})
receipt={'building':bid,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'raw_source':str(source),'raw_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'status':'SHELL_PREVIEW_ONLY_FURNISHING_AND_CHECKS_PENDING','views':records,'authoring_source_sha256':source_hashes,'source_directory':str(source_dir)}
(out/'RECEIPT.json').write_text(json.dumps(receipt,indent=2));print('SHELL_PREVIEW_COMPLETE',bid,str(out),flush=True)
