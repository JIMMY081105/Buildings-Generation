"""Unattended L2 CPU preparation, then GPU capture and source-bound grading."""
import argparse,json,os,subprocess,sys,time,traceback
from pathlib import Path
W=Path('/cephfs/shared/yixing/outputs/factory80_20261011');I=W/'work/P1_IMPLEMENTATION_20261015'
HERE=Path(__file__).resolve().parent;R3=W/'scripts/cpu_all_serial_20261015_R3'
sys.path[:0]=[str(I),str(R3),str(W/'scripts/current_post_native_cpu_20261015_R2')]
from owned_memory_guard import self_oom_priority,dump
from cpu_all_serial import read,require,actual_terminal,stop,issue,SHARED_STOP
from stat_hash_cache import StatHashCache
from current_g8_g13_serial import live,stopped_gpu
MAIN='/cephfs/shared/yixing/envs/main/bin/python'
CPU=W/'work/CPU_ALL_29_SERIAL_20261015_R3';VAR=W/'work/CURRENT_R5_P8_CPU_SERIAL_20261015'
FRAMES=W/'work/ALL_CURRENT_FRAMER2_SERIAL_20261015'
PREFIX=W/'work/GPU_ORDERED_USER_BATCH_20261015_R2';TAIL=W/'work/GPU_CURRENT_ORDERED_TAIL_20261015_R2'


def main():
    self_oom_priority();p=argparse.ArgumentParser(allow_abbrev=False)
    p.add_argument('--expected-index-sha256',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    C=StatHashCache([W/'work/P1_HASH_CACHE_20261015.json']);ix=C.ref(HERE/'SOURCE_INDEX.json')
    require(ix['sha256']==a.expected_index_sha256,'Current all L2 queue source index differs');idx=read(ix['path'])
    own=[ix,*idx['runtime_refs'],*idx['dependency_refs']]
    for r in own:require(C.ref(r['path'])==r,'Current L2 queue source changed')
    D=a.output.resolve();require(D.is_relative_to(W/'work')and not D.exists(),'Fresh all L2 output required');D.mkdir()
    order=['factory80_'+s for s in ['001','002','003','004','011','027']]+[b for b in idx['authorized_29']if b not in ['factory80_'+s for s in ['001','002','003','004','011','027']]]+['VARIANT_P8','VARIANT_R5']
    dump(D/'BEGIN.json',dict(own_pid=os.getpid(),source_index_ref=ix,order=order,
        GPU_after='Actual original ordered prefix and current tail completed without resource-attention stop',
        single_global_heavy_job=True,fixed_EV_minus1=True,automatic_GPU_retry=False))
    def log_issue(B,title,value,tag):
        L=B/'LEDGER';L.mkdir();issue(L,title,json.dumps(value,ensure_ascii=False),tag)
    def run(B,script,input_ref,peak,cpu,gpu):
        require(not stop(),'STOP before next L2 child')
        argv=[MAIN,'-u',str(HERE/script),'--input',input_ref['path'],'--expected-input-sha256',input_ref['sha256'],
            '--expected-index-sha256',ix['sha256'],'--output',str(B/'SUBJECT')]
        plan=dict(schema='Factory80OwnedSingleMemoryGuardedJobV1',argv=argv,expected_peak_bytes=peak,
            expected_peak_basis='L2 reuse metadata2GiB/new original author12GiB/SDK16GiB/image2GiB; corrected memory counters plus8GiB reserve, own1000/RSSwatchdog/global single-heavy lease.',
            take_cpu_lease=cpu,GPU_job=gpu,protected_refs=[*own,input_ref],environment=dict(
                CUDA_VISIBLE_DEVICES='0'if gpu else'',PXR_WORK_THREAD_LIMIT='2',MALLOC_ARENA_MAX='1',
                OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='2',MKL_NUM_THREADS='1'))
        dump(B/'JOB_PLAN.json',plan);pr=C.ref(B/'JOB_PLAN.json');args=[MAIN,'-u',str(I/'run_guarded_job.py'),
            '--plan',pr['path'],'--expected-plan-sha256',pr['sha256'],'--output',str(B/'GUARDED')]
        with(B/'stdout.log').open('xb')as out,(B/'stderr.log').open('xb')as err:
            ch=subprocess.Popen(args,stdout=out,stderr=err);dump(B/'DISPATCH_BEGIN.json',dict(child_pid=ch.pid,argv=args));code=ch.wait()
        dump(B/'DISPATCH_TRUE_WAIT.json',dict(actual_child_pid=ch.pid,argv=args,actual_signed_returncode=code,actual_wait_completed=True))
        t=actual_terminal(B/'GUARDED');require(t is not None,'Unknown actual L2 wait: no next job')
        if stop()or t.get('actual_signed_returncode')==80:
            if not SHARED_STOP.exists():dump(SHARED_STOP,dict(entire_task_stop=True,reason='L2 actual negative escape',terminal=t))
            raise SystemExit(80)
        rp=B/'SUBJECT/FINITE_RESULT.json';return t,C.ref(rp)if rp.exists()else None
    prepared=[];old=W/'work/ALL_CURRENT_L2_SERIAL_20261015_R1'
    for label in order:
        require(not stop(),'Existing STOP during reused L2 CPU preparation')
        B=D/label;B.mkdir();inp=None
        if label=='VARIANT_R5':
            owner=W/'work/CURRENT_R5_BINDING_REPAIR_SERIAL_20261015_R1';fb=owner/'FRAME';rowfile=fb/'ROW_COMPLETE.json'
            while not rowfile.exists():
                require(not stop(),'Existing STOP while waiting supplemental R5 Frame')
                if(owner/'FINITE_RESULT.json').exists()or not live(owner,'current_r5_binding_repair.py'):break
                time.sleep(30)
            fr=read(rowfile)if rowfile.exists()else{};ft=actual_terminal(fb/'GUARDED')if(fb/'GUARDED').exists()else None
            fp=read(fr['result_ref']['path'])if fr.get('result_ref')else{}
            if not ft or ft['code']!=0 or not fp.get('frame_manifest_ref'):
                rec=dict(status='BLOCKED_MISSING_ACTUAL_SUPPLEMENTAL_R5_FRAME',actual_terminal=ft,actual_frame_row=fr)
                dump(B/'CPU_ROW_COMPLETE.json',rec);log_issue(B,'R5 L2 supplemental frame unavailable',rec,'L2_R2_R5_FRAME');continue
            wp=owner/'CPU/SUBJECT/ACTUAL_VARIANT_WORLD_INPUT.json';inp=dict(world_input_ref=C.ref(wp),frame_guard=str(fb/'GUARDED'),frame_result_ref=fr['result_ref'],frame_manifest_ref=fp['frame_manifest_ref'])
            script='current_l2_cpu_inputs.py';peak=12*1024**3;cpu=False
        else:
            rowfile=old/label/'CPU_ROW_COMPLETE.json'
            while not rowfile.exists():
                require(not stop(),'Existing STOP while reusing original CPU author')
                if(old/'CPU_PREPARATION_COMPLETE.json').exists()or not live(old,'current_l2_serial.py'):break
                time.sleep(30)
            fr=read(rowfile)if rowfile.exists()else{};base=old/label/'CPU';ft=actual_terminal(base/'GUARDED')if(base/'GUARDED').exists()else None
            if not ft or ft['code']!=0 or not fr.get('result_ref'):
                rec=dict(status='BLOCKED_ACTUAL_ORIGINAL_L2_CPU_NOT_COMPLETED',actual_terminal=ft,actual_CPU_row=fr)
                dump(B/'CPU_ROW_COMPLETE.json',rec);log_issue(B,label+' original L2 CPU unavailable',rec,'L2_R2_CPU_'+label);continue
            require(not(old/label/'GPU/GUARDED/BEGIN.json').exists(),'An original GPU invocation already exists; do not duplicate it')
            inp=dict(original_CPU_guard=str(base/'GUARDED'),original_CPU_result_ref=fr['result_ref'])
            script='reuse_current_l2_cpu_metadata.py';peak=2*1024**3;cpu=True
        dump(B/'CPU_INPUT.json',inp);P=B/'CPU';P.mkdir();t,rp=run(P,script,C.ref(B/'CPU_INPUT.json'),peak,cpu,False)
        rec=dict(label=label,terminal=t,result_ref=rp,fresh_CPU_author_invoked_for_corrected_R5_frame=label=='VARIANT_R5')
        dump(B/'CPU_ROW_COMPLETE.json',rec)
        if t['code']==0 and rp:prepared.append(rec)
        else:log_issue(B,label+' new source-bound L2 CPU inputs ordinary failure',rec,'L2_R2_PREP_'+label)
    dump(D/'CPU_PREPARATION_COMPLETE.json',dict(prepared=prepared,all31_accounted=True))
    for upstream,source in [(PREFIX,'gpu_ordered_batch.py'),(TAIL,'gpu_current_tail.py')]:
        while not(upstream/'FINITE_RESULT.json').exists():
            require(not stop(),'Existing STOP while waiting earlier GPU phases')
            if stopped_gpu(upstream)or not live(upstream,source):
                rec=dict(status='BLOCKED_PRIOR_GPU_RESOURCE_ATTENTION_OR_UNCONFIRMED_END',required_file=str(upstream/'FINITE_RESULT.json'),
                    automatic_retry=False,prepared_CPU_rows=len(prepared))
                dump(D/'BLOCKED_PRIOR_GPU.json',rec);L=D/'PRIOR_LEDGER';L.mkdir();issue(L,'L2 GPU dependency blocked',json.dumps(rec),'L2_PRIOR_GPU');return 1
            time.sleep(30)
    results=[]
    for r in prepared:
        label=r['label'];B=D/label;G=B/'GPU';G.mkdir()
        t,rp=run(G,'current_l2_gpu_capture.py',r['result_ref'],16*1024**3,False,True)
        value=read(rp['path'])if rp else{};rec=dict(label=label,CPU_result_ref=r['result_ref'],GPU_terminal=t,GPU_result_ref=rp)
        diagnostics=''.join(p.read_text(errors='replace')for p in [G/'GUARDED/stdout.log',G/'GUARDED/stderr.log']if p.exists()).lower()
        resource_stop=value.get('GPU_resource_attention')or t.get('own_RSS_budget_terminated')or t.get('own_watchdog_failure')or t.get('actual_signed_returncode')in(-9,137)
        if resource_stop or any(s in diagnostics for s in ['cuda out of memory','cuda_error_out_of_memory']):
            dump(B/'ROW_COMPLETE.json',rec);dump(D/'NO_GPU_AUTOMATIC_RETRY.json',dict(actual_row=rec,automatic_retry=False))
            L=B/'GPU_RESOURCE_LEDGER';L.mkdir();issue(L,label+' L2 GPU resource attention',json.dumps(rec),'L2_GPU_RESOURCE_'+label);return 1
        if t['code']==0 and value.get('capture_completed'):
            Q=B/'IMAGE';Q.mkdir();gt,gr=run(Q,'current_l2_image_checks.py',rp,2*1024**3,True,False)
            rec.update(image_terminal=gt,image_result_ref=gr)
            if gr:
                im=read(gr['path']);rec['image_summary']=im['image_summary'];rec['photographic_controls_status']=im['photographic_controls']['status']
                if im['photographic_controls']['status'].startswith('NOT_EXECUTABLE'):
                    L=B/'CONTROL_LEDGER';L.mkdir();issue(L,label+' NV5 real L2 qualified carrier unavailable',im['photographic_controls']['status'],'L2_NV5_'+label)
            if gt['code']!=0:
                L=B/'GRADE_LEDGER';L.mkdir();issue(L,label+' L2 image grade ordinary failure',json.dumps(rec),'L2_GRADE_'+label)
        else:
            L=B/'GPU_FAILURE_LEDGER';L.mkdir();issue(L,label+' L2 capture ordinary failure',json.dumps(rec),'L2_GPU_'+label)
        dump(B/'ROW_COMPLETE.json',rec);results.append(rec)
        dump(D/('PROGRESS_'+str(len(results)).zfill(2)+'.json'),dict(rows=results))
    dump(D/'FINITE_RESULT.json',dict(rows=results,all31_CPU_rows_accounted=True,prepared_count=len(prepared),
        human_view03_CLASH='PENDING_USER',whole_READY='NOT_INFERRED',GPU_keeper_resumed=False,entire_task_stop=False))


if __name__=='__main__':raise SystemExit(main())
