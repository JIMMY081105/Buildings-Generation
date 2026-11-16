"""One admitted session captures all required photos from the exact visual USD."""
from pathlib import Path
import os,sys,json,hashlib,traceback,subprocess
Path('/proc/self/oom_score_adj').write_text('1000');os.environ['OMNI_KIT_ACCEPT_EULA']='YES';os.environ['ACCEPT_EULA']='Y'
subprocess.run(['/cephfs/shared/yixing/outputs/diversity54_20260908/runtime/infinigen_staircase_poc/env/bin/python3',str(Path(__file__).parent/'validate_daylight.py'),sys.argv[1]],check=True)
if (Path(sys.argv[1])/'PRE_CAPTURE24.json').exists():subprocess.run(['/cephfs/shared/yixing/outputs/diversity54_20260908/runtime/infinigen_staircase_poc/env/bin/python3',str(Path(__file__).parent/'validate_camera_joint.py'),sys.argv[1]],check=True)
d=Path(sys.argv[1]);assert (d/'SCENE_ASSET_BINDING.json').is_file(),'MISSING_COMPLETE_SCENE_ASSET_BINDING';plan=json.loads((d/'PHOTO_PLAN.json').read_text());stage=d/'scene/museum.usdc';model=json.loads((d/'RAW_MODEL.json').read_text());assert hashlib.sha256((d/'RAW_MODEL.json').read_bytes()).hexdigest()==plan['raw_source_sha256'];run_name=os.environ.get('M29_PHOTO_RUN','photos');assert run_name.startswith('photos') and Path(run_name).name==run_name and '..' not in run_name;out=d/run_name;out.mkdir(exist_ok=False);sys.argv=[sys.argv[0],'--/renderer/activeGpu=0','--/physics/cudaDevice=0','--/app/useFabricSceneDelegate=false','--/exts/isaacsim.core.throttling/enable_async=false','--/app/asyncRendering=false','--/log/file='+str(out/'ISAAC_NATIVE.log'),'--/log/level=info']
from isaacsim import SimulationApp
app=SimulationApp({'headless':True,'fast_shutdown':True,'renderer':'PathTracing','width':1920,'height':1080,'active_gpu':0,'physics_gpu':0,'multi_gpu':False,'samples_per_pixel_per_frame':32,'denoiser':True,'max_bounces':8},experience='')
try:
 selected=[]
 for line in (out/'ISAAC_NATIVE.log').read_text(errors='replace').splitlines():
  cols=[x.strip() for x in line.split('|')]
  if 'Active GPU:' in line and line.rsplit('Active GPU:',1)[1].strip()=='NVIDIA A40':selected.append(line)
  if line.lstrip().startswith('|') and len(cols)>3 and cols[1]=='0' and cols[2]=='NVIDIA A40' and 'Yes' in cols[3]:selected.append(line)
 assert selected,'ACTUAL_A40_RENDERER_NOT_VERIFIED';(out/'SELECTED_GPU.txt').write_text('\n'.join(selected)+'\n')
 from render_hdr import run
 w,h=model['building_xy_m'];result=run(app,{'stage':str(stage),'output':str(out),'views':plan['views'],'building_bounds':[[-w/2-7,-h/2-7,-.8],[w/2+7,h/2+7,model['floor_height_m']*len(model['floors'])+5]]});assert len(result['frames'])==12;assert all(x['building_exposure']['status']=='PASS' for x in result['frames']);result['selected_gpu_evidence']=selected;result['status']='COMPLETE';result['visual_review_status']='PENDING_EACH_IMAGE_REVIEW';result['worker_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();(out/'RESULT.json').write_text(json.dumps(result,indent=2));selection={'directory':str(out),'photos_sha256':hashlib.sha256((out/'PHOTOS.json').read_bytes()).hexdigest(),'capture_result_sha256':hashlib.sha256((out/'RESULT.json').read_bytes()).hexdigest(),'source_visual_sha256':result['source_stage_sha256'],'native_run_tag':Path(__file__).parent.parent.name,'status':'CAPTURED_PENDING_VISUAL_REVIEW'};(d/'PHOTOS_SELECTED.json').write_text(json.dumps(selection,indent=2));print('BUILDING_RTX_CAPTURE_COMPLETE',len(result['frames']),flush=True)
except BaseException as e:
 (out/'RESULT.json').write_text(json.dumps({'status':'ERROR','error':repr(e),'traceback':traceback.format_exc()},indent=2));raise
finally:app.close()
