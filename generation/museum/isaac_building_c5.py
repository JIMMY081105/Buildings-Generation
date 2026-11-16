"""One source-bound full-building native C5 session; completion precedes shutdown."""
from pathlib import Path
import os,sys,json,hashlib,datetime,traceback
Path('/proc/self/oom_score_adj').write_text('1000');os.environ['OMNI_KIT_ACCEPT_EULA']='YES';os.environ['ACCEPT_EULA']='Y'
r=Path('/cephfs/shared/yixing/outputs/museum29_20261107');task=json.loads(Path(sys.argv[1]).read_text());d=r/task['output'];d.mkdir(exist_ok=True);assert hashlib.sha256(Path(task['stage']).read_bytes()).hexdigest()==task['source_collision_sha256'];assert hashlib.sha256(Path(task['spawns']).read_bytes()).hexdigest()==task['spawns_sha256'];sys.argv=[sys.argv[0],'--/renderer/activeGpu=0','--/physics/cudaDevice=0','--/app/useFabricSceneDelegate=false','--/log/file='+str(d/'ISAAC_NATIVE.log'),'--/log/level=info']
from isaacsim import SimulationApp
app=SimulationApp({'headless':True,'fast_shutdown':True,'renderer':'RayTracedLighting','width':640,'height':480,'active_gpu':0,'physics_gpu':0,'multi_gpu':False},experience='')
try:
 selected=[]
 for line in (d/'ISAAC_NATIVE.log').read_text(errors='replace').splitlines():
  cols=[x.strip() for x in line.split('|')]
  if 'Active GPU:' in line and line.rsplit('Active GPU:',1)[1].strip()=='NVIDIA A40':selected.append(line)
  if line.lstrip().startswith('|') and len(cols)>3 and cols[1]=='0' and cols[2]=='NVIDIA A40' and 'Yes' in cols[3]:selected.append(line)
 assert selected,'ACTUAL_A40_DEVICE_NOT_VERIFIED';(d/'SELECTED_GPU.txt').write_text('\n'.join(selected)+'\n')
 from native_c5 import run
 result=run(app,task);result['selected_gpu_evidence']=selected;result['task']=task;result['worker_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();(d/'RESULT.json').write_text(json.dumps(result,indent=2));print('BUILDING_C5_COMPLETE',result['status'],flush=True)
except BaseException as e:
 (d/'RESULT.json').write_text(json.dumps({'status':'ERROR','error':repr(e),'traceback':traceback.format_exc()},indent=2));raise
finally:app.close()
