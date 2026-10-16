"""One original L2 capture; direct parent owns exact GPU lease, no retries."""
import argparse,fcntl,json,os,subprocess,sys
from pathlib import Path
from current_l2_cpu_inputs import child,read,require,stop,MAIN,W,I,HERE
from owned_memory_guard import dump,self_oom_priority
from stat_hash_cache import StatHashCache
from actual_vulkan_device import qualify_actual_vulkan
from current_l2_inputs_contract import completed_capture


def pause_without_process_signals(D):
    for session in ['a40_hb','a40_autohold']:
        r=subprocess.run(['tmux','has-session','-t',session],capture_output=True,text=True)
        require(r.returncode==1,'Expected absent own hold/watcher session before pause')
    matches=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:cmd=proc.joinpath('cmdline').read_bytes().split(b'\0')
        except FileNotFoundError:continue
        if b'/cephfs/shared/yixing/bin/a40_heartbeat.py'in cmd:matches.append(int(proc.name))
    require(not matches,'Heartbeat process present: do not signal unknown ownership')
    env={k:v for k,v in os.environ.items()if not k.startswith('A40_')}
    env.update(A40_HEARTBEAT_PAT='/cephfs/shared/yixing/bin/a40_heartbeat[.]py',
        A40_HOLD_SESSION='a40_hb',A40_AUTOHOLD_SESSION='a40_autohold',
        A40_AUTOHOLD_STATE='/cephfs/shared/yixing/outputs/a40_autohold')
    r=subprocess.run(['bash','/cephfs/shared/yixing/bin/a40_autohold.sh','pause'],env=env,capture_output=True,text=True)
    p=Path(env['A40_AUTOHOLD_STATE'])/'pause'
    dump(D/'ACTUAL_PRE_GPU_PAUSE.json',dict(actual_signed_returncode=r.returncode,stdout=r.stdout,stderr=r.stderr,
        heartbeat_matches=matches,pause_file_present=p.exists(),no_keeper_start_resume=True))
    require(r.returncode==0 and p.is_file(),'Explicit pause marker not confirmed')


def main():
    self_oom_priority();p=argparse.ArgumentParser(allow_abbrev=False)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--expected-input-sha256',required=True)
    p.add_argument('--expected-index-sha256',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    C=StatHashCache([W/'work/P1_HASH_CACHE_20261015.json']);ix=C.ref(HERE/'SOURCE_INDEX.json')
    require(ix['sha256']==a.expected_index_sha256,'Current L2 capture source index differs');idx=read(ix['path'])
    before=[ix,*idx['runtime_refs'],*idx['dependency_refs']]
    for r in before:require(C.ref(r['path'])==r,'Current L2 protected source changed')
    ir=C.ref(a.input);require(ir['sha256']==a.expected_input_sha256,'Current L2 CPU finite changed');inp=read(a.input)
    require(inp['source_unchanged']is True and inp['source_refs_before']==inp['source_refs_after'],'Clean current CPU finite required')
    before += [ir,*inp['source_refs_before']]
    for r in before:require(C.ref(r['path'])==r,'Current L2 source changed before capture')
    require(not stop(),'Existing STOP');D=a.output.resolve();require(D.is_relative_to(W/'work')and not D.exists(),'Fresh L2 GPU output required');D.mkdir()
    lock=os.open(W/'work/OWNED_GPU_SERIAL.lock',os.O_RDWR|os.O_NOFOLLOW)
    try:
        st=os.fstat(lock);require((st.st_dev,st.st_ino)==(55,1100794192204),'Exact registered GPU lease required')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        pause_without_process_signals(D)
        cap=Path(idx['original_capture_index_ref']['path']).parent
        argv=[idx['ISAAC_python'],'-u',str(cap/'render_r4_factory80_isaac.py'),'--execute',
            '--source-index',str(cap/'SOURCE_INDEX.json'),'--expected-source-index-sha256',idx['original_capture_index_ref']['sha256'],
            '--building',inp['building'],'--r4-result',inp['r4_result_ref']['path'],'--expected-r4-result-sha256',inp['r4_result_ref']['sha256'],
            '--r4-parent',inp['r4_parent_ref']['path'],'--expected-r4-parent-sha256',inp['r4_parent_ref']['sha256'],
            '--profile',inp['profile_ref']['path'],'--pose-plan',inp['pose_plan_ref']['path'],'--expected-pose-plan-sha256',inp['pose_plan_ref']['sha256'],
            '--output',str(D/'CAPTURE'),'--mode','both','--frames','120','--raw-aov','--capture-timeout','600']
        # Reviewed capture itself requalifies five empty-card samples immediately
        # before SDK startup, after this payload acquired the global heavy lease.
        code=child(D,'RENDER',argv,(lock,))
    finally:
        os.close(lock)
    wait=read(D/'RENDER.TRUE_WAIT.json');rp=D/'CAPTURE/ACTUAL_R4_RENDER_CAPTURE_RESULT.json';result=read(rp)if rp.exists()else{}
    frames=result.get('stills',[])+result.get('orbit_frames',[]);text=(D/'RENDER.stdout').read_text(errors='replace');err=(D/'RENDER.stderr').read_text(errors='replace')
    try:device=qualify_actual_vulkan(text)
    except ValueError as e:device=dict(status='UNVERIFIED',reason=str(e))
    captured=completed_capture(result,wait,inp['expected_poses'],device,read(idx['L2_profile_ref']['path'])['required_settings'])
    diagnostic=(text+err+result.get('actual_original_traceback','')).lower()
    attention=code in(-9,137)or any(x in diagnostic for x in ['cuda out of memory','cuda_error_out_of_memory',
        'gpu not empty','gpu query failed','identity challenge inconclusive'])
    after=[C.ref(r['path'])for r in before];require(before==after,'Current L2 capture protected source changed')
    dump(D/'FINITE_RESULT.json',dict(schema='Factory80CurrentL2GPUCaptureV1',building=inp['building'],
        current_root_ref=inp['current_root_ref'],CPU_inputs_ref=ir,actual_renderer_wait_ref=C.ref(D/'RENDER.TRUE_WAIT.json'),
        actual_capture_result_ref=C.ref(rp)if rp.exists()else None,actual_render_device=device,
        capture_completed=captured,expected_poses=inp['expected_poses'],actual_poses=len(frames),
        GPU_resource_attention=attention,automatic_retry=False,source_refs_before=before,source_refs_after=after,
        source_unchanged=True,human_visual_approval=False,READY='NOT_INFERRED'))
    C.save(D/'ACTUAL_STAT_HASH_CACHE.json');return 0 if captured else 1


if __name__=='__main__':raise SystemExit(main())
