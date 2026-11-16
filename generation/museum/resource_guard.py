"""Museum29 heavy-process admission and 1 Hz group/cgroup watchdog.
The protected identity is a process born here in a fresh session. No existing
process is signalled. Peak values are historical maxima, including failures.
"""
import argparse, datetime, fcntl, json, os, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from resource_admission_protected import evaluate_for_guard, live_oom_kill, selftest as selftest104
from resource_admission_small_tasks import selftest as selftest100

ROOT=Path('/cephfs/shared/yixing/outputs/museum29_20261107')
GiB=2**30
LIMIT=50*GiB

def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(p,x):
    q=p.with_suffix(p.suffix+'.tmp');q.write_text(json.dumps(x,indent=2));q.replace(p)
def identity(pid):
    try:
        f=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return {'pid':int(pid),'state':f[0],'ppid':int(f[1]),'pgid':int(f[2]),'start_ticks':f[19],'rss':int(f[21])*os.sysconf('SC_PAGE_SIZE')}
    except (OSError,IndexError,ValueError):return None
def total_rss():
    rows=dict(s.split() for s in Path('/sys/fs/cgroup/memory/memory.stat').read_text().splitlines())
    return int(rows['total_rss'])
def gpu():
    s=subprocess.check_output(['nvidia-smi','--query-gpu=memory.free,memory.used','--format=csv,noheader,nounits'],text=True,timeout=5)
    f,u=map(int,s.splitlines()[0].split(','));return f,u
def peaks():
    p=ROOT/'evidence/RESOURCE_PEAKS.json'
    return json.loads(p.read_text()) if p.exists() else {'blender':None,'isaac_c5':None,'isaac_rtx':None}
def estimate(k,p):
    policy22=ROOT/'evidence/RESOURCE_ADMISSION22.json'
    if policy22.exists():return float(json.loads(policy22.read_text())['admission_peaks_gib']['blender_building' if k=='blender' else k])
    policy=ROOT/'evidence/RESOURCE_ADMISSION20.json'
    if policy.exists():return float(json.loads(policy.read_text())['admission_peaks_gib'][k])
    calibration=ROOT/'evidence/RESOURCE_CALIBRATION.json'
    valid=json.loads(calibration.read_text()).get(k,False) if calibration.exists() else False
    observed=p.get(k) or 0
    reference=ROOT/'evidence/HISTORICAL_REFERENCE_PEAKS.json'
    history=json.loads(reference.read_text()).get(k,0) if reference.exists() else 0
    observed=max(observed,history)
    return observed if valid else max(observed,16. if k=='blender' else 25.)

def raise_admission20(kind,observed,normal_formal=False,source_tag=None):
    path=ROOT/'evidence/RESOURCE_ADMISSION22.json'
    if not path.exists():path=ROOT/'evidence/RESOURCE_ADMISSION20.json'
    if not path.exists():return
    policy=json.loads(path.read_text());old=float(policy['admission_peaks_gib'][kind]);value=observed if kind.startswith('blender') else max(12.,observed*1.25)
    if value>old:
        policy['admission_peaks_gib'][kind]=value;policy.setdefault('task_group_limits_gib',{})[kind]=max(1.5*value,value+6);policy['updated_utc']=utc();policy.setdefault('increases',[]).append({'utc':utc(),'kind':kind,'prior_admission_peak_gib':old,'new_admission_peak_gib':value,'measured_peak_gib':observed,'normal_formal_completed':normal_formal,'source_tag':source_tag});save(path,policy)

def observe_oom22(tag):
    path=ROOT/'evidence/RESOURCE_OOM22.json'
    if not path.exists():return None
    state=json.loads(path.read_text());rows=dict(x.split() for x in Path('/sys/fs/cgroup/memory/memory.oom_control').read_text().splitlines());count=int(rows['oom_kill']);prior=state['last_oom_kill'];record={'utc':utc(),'tag':tag,'prior_oom_kill':prior,'current_oom_kill':count,'increase':count-prior}
    if count>prior:save(ROOT/'evidence/RESOURCE_OOM22_DISPATCH_BLOCK.json',{**record,'status':'DISPATCH_BLOCKED_AWAIT_NEW_CONTROL','own_jobs_at_observation':running(),'scope':'OOM count increased; stop all new dispatch, inspect other long-lived jobs and await supervisor control.'})
    state.update(last_oom_kill=count,last_read_utc=record['utc'],last_tag=tag);save(path,state);return record
def running():
    ans=[]
    for f in (ROOT/'logs').glob('*_PROCESS.json'):
        try:
            r=json.loads(f.read_text());now=identity(r['pid'])
            if now and now['start_ticks']==r['start_ticks'] and now['state']!='Z':ans.append(r)
        except (OSError,KeyError,ValueError):pass
    return ans
def main():
    a=argparse.ArgumentParser();a.add_argument('--class',dest='kind',choices=['blender_building','blender_heavy_factory','isaac_c5','isaac_rtx'],required=True)
    a.add_argument('--self-test104',choices=['protected_ok','missing24730','missing2456'])
    a.add_argument('--self-test100',choices=['small056_unstable','large32_unstable','isaac_unstable','unknownB_unstable','smallOOM_unstable','smallProtected_unstable']);a.add_argument('--tag',required=True);a.add_argument('--wait-seconds',type=int,default=0);a.add_argument('--success-marker');a.add_argument('--source-code-dir');a.add_argument('--self-test22',action='store_true');a.add_argument('--calibrate-factory');a.add_argument('--execute-adjustment',action='append',default=[]);a.add_argument('cmd',nargs=argparse.REMAINDER);q=a.parse_args()
    if q.self_test104:return selftest104(ROOT,q.kind,q.self_test104,q.tag)
    if q.self_test100:return selftest100(ROOT,q.kind,q.self_test100,q.tag)
    cmd=q.cmd[1:] if q.cmd and q.cmd[0]=='--' else q.cmd
    assert cmd and '/' not in q.tag and '..' not in q.tag
    assert q.kind!='blender_heavy_factory' and not any(Path(x).name=='prepare_tropical_plants.py' for x in cmd),'ADJUST22_TROPICAL_FACTORY_DISABLED'
    if q.self_test22:assert q.kind=='blender_building' and any(Path(x).name=='resource_watchdog_probe.py' for x in cmd),'SELF_TEST22_ONLY_AUTHORIZED_PAYLOAD'
    if q.calibrate_factory:
        assert q.kind=='blender_building';bans=ROOT/'evidence/FACTORY22_DISABLED_TYPES.json';assert not bans.exists() or q.calibrate_factory not in json.loads(bans.read_text()),'FACTORY_TYPE_PREVIOUSLY_KILLED_DISABLED'
    Path('/proc/self/oom_score_adj').write_text('1000')
    logdir=ROOT/'logs';logdir.mkdir(exist_ok=True)
    from resource_source_audit import scan as scan_source65
    source_dir=Path(q.source_code_dir)if q.source_code_dir else ROOT/'runs'/q.tag/'source_code'
    try:
        source65=scan_source65(source_dir,cmd,ROOT);save(logdir/(q.tag+'_SOURCE65_SCAN.json'),source65)
    except (OSError,ValueError,KeyError,AssertionError)as error:
        rejected={'utc':utc(),'tag':q.tag,'kind':q.kind,'status':'REJECTED_INVALID_SOURCE65_BINDING','exit_code':78,'runtime_exit_code':None,'scientific_process_started':False,'reason':str(error)};save(logdir/(q.tag+'_RESULT.json'),rejected);print(json.dumps(rejected),flush=True);return 78
    if q.kind.startswith('blender')and source65['gpu_hit_count']:
        rejected={'utc':utc(),'tag':q.tag,'kind':q.kind,'status':'REJECTED_GPU_SOURCE_IN_CPU_CLASS','exit_code':78,'runtime_exit_code':None,'scientific_process_started':False,'source65_scan_sha256':__import__('hashlib').sha256((logdir/(q.tag+'_SOURCE65_SCAN.json')).read_bytes()).hexdigest(),'gpu_hit_count':source65['gpu_hit_count'],'instruction':'Useisaacresourceclass: memory>=20GiBandGPUfree>=12000MiB. CPUclasscannotadmitanyrun/source_codeGPUimports/CUDAcalls.'};save(logdir/(q.tag+'_RESULT.json'),rejected);print(json.dumps(rejected),flush=True);return 78
    lock=(ROOT/'evidence/ADMISSION.lock').open('a+')
    startwait=time.monotonic();waitpath=logdir/(q.tag+'_WAIT.json')
    while True:
        fcntl.flock(lock,fcntl.LOCK_EX)
        p=peaks();other=running();rss=total_rss();free_gpu,used_gpu=gpu();oom_observation=observe_oom22('ADMISSION:'+q.tag);oom_blocked=(ROOT/'evidence/RESOURCE_OOM22_DISPATCH_BLOCK.json').exists()
        # Requested concurrent classes plus reserve. Existing jobs are already
        # included in cgroup RSS, so this is deliberately conservative.
        need=estimate(q.kind,p)+8+sum(estimate(x['kind'],p) for x in other)
        cooldown=ROOT/'evidence/RESOURCE_COOLDOWN.json'
        cooling=cooldown.exists() and datetime.datetime.fromisoformat(json.loads(cooldown.read_text())['not_before_utc'])>datetime.datetime.now(datetime.timezone.utc)
        production=True  # All admitted heavy work honors current controls.
        control_ok=True
        if production:
            try:
                control=json.loads((ROOT/'evidence/CONTROL_CHECK.json').read_text());age=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(control['last_local_check_utc'])).total_seconds();pending=control['pending'];adjusting=q.execute_adjustment and pending==q.execute_adjustment and control.get('executing_adjustments')==q.execute_adjustment;control_ok=age<=1200 and (not pending or adjusting)
            except (OSError,KeyError,ValueError):control_ok=False
        same_isaac=q.kind.startswith('isaac') and any(x['kind'].startswith('isaac') for x in other)
        calibration_alone=not(q.calibrate_factory or q.self_test22) or not other
        ad98=evaluate_for_guard(ROOT,utc(),other,q.kind,cmd,source65)
        ok=LIMIT-rss>=need*GiB and not same_isaac and not cooling and not oom_blocked and calibration_alone and control_ok and (not q.kind.startswith('isaac') or free_gpu>=12000) and ad98['admission_allowed']
        admission={'utc':utc(),'kind':q.kind,'total_rss_gib':rss/GiB,'free_gib':50-rss/GiB,'required_free_gib':need,'free_gpu_mib':free_gpu,'own_active_jobs':other,'admitted':ok,'wait_seconds':time.monotonic()-startwait,'oom22_observation':oom_observation,'oom22_dispatch_blocked':oom_blocked,'AD98_enforcement':ad98,'source65_scan_status':source65['status'],'source65_scan_sha256':__import__('hashlib').sha256((logdir/(q.tag+'_SOURCE65_SCAN.json')).read_bytes()).hexdigest()}
        save(waitpath,admission)
        if ok:break
        fcntl.flock(lock,fcntl.LOCK_UN)
        if time.monotonic()-startwait>=q.wait_seconds:print(json.dumps(admission),flush=True);return 75
        time.sleep(min(300,max(1,q.wait_seconds-(time.monotonic()-startwait))))
    with (logdir/(q.tag+'.log')).open('a',buffering=1) as log:
        child=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        ident=identity(child.pid);assert ident and ident['pgid']==child.pid
        launch_peak=estimate(q.kind,p);task_limit=max(1.5*launch_peak,launch_peak+6);rec={'tag':q.tag,'kind':q.kind,'pid':child.pid,'start_ticks':ident['start_ticks'],'started_utc':utc(),'command':cmd,'guard_pid':os.getpid(),'admission':admission,'task_group_limit_gib':task_limit,'sampling_interval_seconds':.5,'self_test22':q.self_test22,'calibrate_factory':q.calibrate_factory}
        save(logdir/(q.tag+'_PROCESS.json'),rec)
        fcntl.flock(lock,fcntl.LOCK_UN)
        peak=0;containerpeak=0;devicepeak=0;own_gpu_peak=0;reason=None;lastgpu=0;admission_peak=estimate(q.kind,peaks())
        with (logdir/(q.tag+'_SAMPLES.jsonl')).open('a',buffering=1) as samples:
            while child.poll() is None:
                group=[]
                for proc in Path('/proc').iterdir():
                    if proc.name.isdigit():
                        item=identity(proc.name)
                        if item and item['pgid']==child.pid:group.append(item)
                rss=sum(i['rss'] for i in group);total=total_rss();peak=max(peak,rss);containerpeak=max(containerpeak,total)
                if not(q.self_test22 or q.calibrate_factory) and peak/GiB>admission_peak:
                    fcntl.flock(lock,fcntl.LOCK_EX);raise_admission20(q.kind,peak/GiB,source_tag=q.tag);admission_peak=estimate(q.kind,peaks());fcntl.flock(lock,fcntl.LOCK_UN)
                if time.monotonic()-lastgpu>=5:
                    lastgpu=time.monotonic()
                    try:
                        free_gpu,used_gpu=gpu();devicepeak=max(devicepeak,used_gpu)
                        apps=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,used_memory','--format=csv,noheader,nounits'],text=True,timeout=5)
                        ids={i['pid'] for i in group};matched=[int(s.split(',')[1]) for s in apps.splitlines() if s.split(',')[0].strip().isdigit() and int(s.split(',')[0]) in ids];own_gpu=sum(matched) if matched else None
                        own_gpu_peak=max(own_gpu_peak,own_gpu or 0)
                    except (OSError,ValueError,subprocess.SubprocessError):used_gpu=None;own_gpu=None
                record={'utc':utc(),'group_rss_gib':rss/GiB,'group_peak_gib':peak/GiB,'container_total_rss_gib':total/GiB,'container_peak_gib':containerpeak/GiB,'device_used_mib':used_gpu,'own_gpu_mib':own_gpu,'members':[{k:v for k,v in i.items() if k!='rss'} for i in group]}
                samples.write(json.dumps(record)+'\n');save(logdir/(q.tag+'_RESOURCE.json'),{**rec,**record})
                ad98_oom=live_oom_kill(ROOT)
                if total>=46*GiB or rss>task_limit*GiB or ad98_oom is not None:
                    if ad98_oom is not None:observe_oom22('LIVE_AD98:'+q.tag)
                    reason='OOM_KILL_INCREMENT_DURING_OWN_HEAVY_AD98' if ad98_oom is not None else 'CONTAINER_TOTAL_RSS_GE_46_GIB' if total>=46*GiB else 'OWN_GROUP_RSS_GT_CLASS_CEILING22';now=identity(child.pid)
                    if now and now['start_ticks']==rec['start_ticks'] and now['pgid']==child.pid:
                        os.killpg(child.pid,signal.SIGTERM)
                        try:child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            now=identity(child.pid)
                            if now and now['start_ticks']==rec['start_ticks']:os.killpg(child.pid,signal.SIGKILL)
                    break
                time.sleep(.5)
        runtime_code=child.wait();code=runtime_code;marker_status=None
        if q.kind.startswith('isaac'):
            if q.success_marker:
                try:
                    marker=json.loads(Path(q.success_marker).read_text());marker_status=marker.get('calibration_status') or marker.get('status')
                    if marker_status not in ['COMPLETE','PASS']:code=2
                except (OSError,ValueError):marker_status='MISSING_OR_INVALID';code=2
            else:marker_status='NOT_CONFIGURED';code=2 if runtime_code==0 else runtime_code
        result={**rec,'ended_utc':utc(),'runtime_exit_code':runtime_code,'exit_code':code,'success_marker_status':marker_status,'peak_group_rss_gib':peak/GiB,'container_peak_rss_gib':containerpeak/GiB,'own_gpu_peak_mib':own_gpu_peak or None,'gpu_accounting_note':'Unmatched NVML PIDs may be in another PID namespace; null is unverified, not zero. Device peak includes other users.','device_peak_used_mib':devicepeak,'guard_stop_reason':reason,'retry_not_before_utc':(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(minutes=10)).isoformat() if reason=='CONTAINER_TOTAL_RSS_GE_46_GIB' else None}
        save(logdir/(q.tag+'_RESULT.json'),result)
        fcntl.flock(lock,fcntl.LOCK_EX);p=peaks()
        if not(q.self_test22 or q.calibrate_factory):p[q.kind]=max(p.get(q.kind) or 0,peak/GiB);save(ROOT/'evidence/RESOURCE_PEAKS.json',p)
        formal=q.kind.startswith('isaac') and code==runtime_code==0 and marker_status in ['PASS','COMPLETE']
        if not(q.self_test22 or q.calibrate_factory) and (q.kind.startswith('blender') or formal):raise_admission20(q.kind,peak/GiB,normal_formal=formal,source_tag=q.tag)
        if reason=='CONTAINER_TOTAL_RSS_GE_46_GIB':save(ROOT/'evidence/RESOURCE_COOLDOWN.json',{'not_before_utc':result['retry_not_before_utc'],'reason':reason})
        if q.calibrate_factory and reason:
            bans=ROOT/'evidence/FACTORY22_DISABLED_TYPES.json';state=json.loads(bans.read_text()) if bans.exists() else {};state[q.calibrate_factory]={'utc':utc(),'tag':q.tag,'reason':reason};save(bans,state)
        result['oom22_observation_after_exit']=observe_oom22('EXIT:'+q.tag);result['calibration_excluded_reason']='ADJUST22_CONTROLLED_RUNAWAY_TEST' if q.self_test22 else 'SINGLE_FACTORY_CALIBRATION_SEPARATE_TASK_TYPE' if q.calibrate_factory else None;save(logdir/(q.tag+'_RESULT.json'),result)
        if code==0:
            calibration=ROOT/'evidence/RESOURCE_CALIBRATION.json';cal=json.loads(calibration.read_text()) if calibration.exists() else {};cal[q.kind]=True;save(calibration,cal)
        fcntl.flock(lock,fcntl.LOCK_UN)
        print(json.dumps(result),flush=True)
        return code if code>=0 else 128-code
if __name__=='__main__':sys.exit(main())
