"""Execute one authorized pilot candidate; source authoring, original Isaac, RTX."""
import argparse,fcntl,json,os,signal,subprocess,sys,time,hashlib
from pathlib import Path
from datetime import datetime,timezone
W=Path('/cephfs/shared/yixing/outputs/factory80_20261011');S=W/'scripts'
CPU='/cephfs/shared/yixing/envs/main/bin/python'
ISAAC='/cephfs/shared/yixing/envs/isaac_prod/env/bin/python3'
FROZEN=Path('/cephfs/shared/yixing/outputs/diversity54_20260908/evidence/frozen_sources/pipeline')
def stamp():return datetime.now(timezone.utc).isoformat()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(value,f,indent=2,ensure_ascii=False)
def file_ref(path):
    raw=path.read_bytes()
    return {'path':str(path),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def select_actual_assets(a,base_logs):
    """Unstarted retry schemes require preregistered, measured new signatures.

    Historical schemes retain their original inputs. Same-scheme tool retries
    and resumptions use an immutable selection receipt once it exists.
    """
    selection_path=base_logs/'ASSET_SELECTION.json'
    selected=json.loads(selection_path.read_text()) if selection_path.is_file() else None
    historical_started=(base_logs/'01_author.COMMAND.json').is_file()
    required=a.attempt>=2 and a.building!='factory80_001' and not historical_started
    legacy=W/'assets'/a.building/('STAIR_ASSETS_PORTABLE_SEED_REPAIR_FINAL.json' if a.building=='factory80_006' else 'STAIR_ASSETS_PORTABLE_FINAL.json')
    while True:
        critical()
        try:
            tiling=json.loads((W/'TILING_PLAN.json').read_text())
            planned=next(r for r in tiling['rows'] if r['building']==a.building)['planned_stair_count']
            preregistration=None;retry_row=None
            if selected:
                assert selected['building']==a.building and selected['attempt']==a.attempt
                assets=Path(selected['actual_manifest']['path'])
                assert file_ref(assets)==selected['actual_manifest'],'Selected actual native manifest changed'
                retry_row=selected.get('preregistered_row')
                preregistration=selected.get('preregistered_plan')
                if preregistration:assert file_ref(Path(preregistration['path']))==preregistration,'Preregistered retry plan changed'
            elif required:
                retry_path=W/'SIGNATURE_RETRY_PLAN_FACTORY80.json'
                retry_plan=json.loads(retry_path.read_text())
                assert retry_plan['schema']=='Factory80PilotPreRegisteredSignatureRetryPlanV1'
                matches=[r for r in retry_plan['rows'] if r['building']==a.building and r['attempt']==a.attempt]
                assert len(matches)==1,'A unique preregistered retry signature is required'
                retry_row=matches[0];assert retry_row['stair_count']==planned
                assets=Path(retry_row['asset_manifest'])
                assert assets==W/'assets'/a.building/f'scheme_{a.attempt:02d}'/'STAIR_ASSETS_PORTABLE_RETRY_FINAL.json'
                preregistration=file_ref(retry_path)
            else:assets=legacy
            asset_doc=json.loads(assets.read_text())
            ready='pairing_revision' in tiling and asset_doc['building']==a.building and asset_doc.get('actual_asset_count')==planned and len(asset_doc['rows'])==planned
            if ready and retry_row:
                assert all(r['actual_signature']==retry_row['planned_signature'] for r in asset_doc['rows']),'Measured retry signature differs from its preregistration'
                baseline=json.loads(legacy.read_text())['rows'][0]['actual_signature']
                proposed=retry_row['planned_signature']
                assert any(proposed[k]!=baseline[k] for k in ('support_type','handrail_type')),'Retry did not change the actual structural signature'
                assert all(proposed[k]==baseline[k] for k in ('staircase_family','tread_material','dominant_palette')),'Retry changed a preserved family/material/palette assignment'
                assert len({r['seed'] for r in asset_doc['rows']})==planned,'Each actual stair needs an independent seed'
                pools=retry_row.get('native_seed_pools')
                if not pools and retry_row.get('native_seed_candidates'):
                    candidates=retry_row['native_seed_candidates']
                    assert len(candidates)==planned and all(isinstance(p,list) and all(isinstance(v,int) for v in p) for p in candidates)
                    pools=[{'stair_index':i+1,'candidate_seeds':p} for i,p in enumerate(candidates)]
                if pools:
                    by_index={p['stair_index']:p['candidate_seeds'] for p in pools}
                    assert len(by_index)==planned and all(r['seed'] in by_index[r['stair_index']] for r in asset_doc['rows']),'Actual seed outside its preregistered slot pool'
                else:assert sorted(r['seed'] for r in asset_doc['rows'])==sorted(retry_row['native_seeds'])
            if not ready:pause(15);continue
            if not selected:
                selected={'utc':stamp(),'building':a.building,'attempt':a.attempt,
                          'selection_kind':'PRE_REGISTERED_ACTUAL_SIGNATURE_RETRY' if retry_row else ('PRESERVED_HISTORICAL_SCHEME_INPUT' if historical_started else 'INITIAL_NATIVE_INPUT'),
                          'actual_manifest':file_ref(assets),'preregistered_plan':preregistration,'preregistered_row':retry_row,
                          'author_command_already_existed':historical_started,
                          'cannot_support':'Input selection and actual manifest fields do not establish building gates, visual acceptance or humanoid locomotion.'}
                write(selection_path,selected)
            return assets
        except (FileNotFoundError,json.JSONDecodeError):pause(15)
def critical():
    found=list(W.glob('CRITICAL_STOP*.json'))
    if found:raise SystemExit('CRITICAL_STOP: '+','.join(map(str,found)))
def pause(seconds):
    for _ in range(seconds):critical();time.sleep(1)
def command(args):
    try:
        r=subprocess.run(args,capture_output=True,text=True,timeout=25)
        return {'command':args,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
    except Exception as e:return {'command':args,'returncode':None,'error':repr(e)}
def gpu_free(log,one_probe=False):
    while True:
        critical();q=command(['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader'])
        hold=command(['bash','/cephfs/shared/yixing/bin/a40_autohold.sh','status']);others=[]
        for p in Path('/proc').iterdir():
            if not p.name.isdigit() or int(p.name)==os.getpid():continue
            try:
                comm=(p/'comm').read_text().strip();argv=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
                gpu_startup=('/isaac_prod/env/bin/python' in argv or 'validate_building_isaac.py' in argv
                             or 'render_factory80_isaac.py' in argv or ' -m isaacsim' in argv
                             or '--bake-device gpu' in argv or comm.startswith('kit'))
                if comm.startswith(('python','kit')) and argv and (gpu_startup or str(W) not in argv):
                    others.append({'pid':int(p.name),'command':argv})
            except (FileNotFoundError,PermissionError,ProcessLookupError):pass
        allowed=q['returncode']==0 and not q.get('stdout','').strip() and hold['returncode']==0 and 'hold:    not running' in hold.get('stdout','') and not others
        record={'utc':stamp(),'compute_apps':q,'autohold':hold,'unrecognized_python_or_kit_startup':others,'allowed':allowed,
                'note':'A successful empty compute-app query plus startup process inspection; 0MiB/0% alone is not used.'}
        name=log/('GPU_PREFLIGHT_'+str(time.time_ns())+'.json');write(name,record)
        if one_probe:return allowed
        if allowed:return
        print('GPU_WAIT_SHARED_CARD',stamp(),flush=True);pause(600)
def belongs_to(pid,owned):
    seen=set()
    while pid>1 and pid not in seen:
        if pid==owned:return True
        seen.add(pid)
        try:
            pid=int(next(s.split(':',1)[1] for s in Path(f'/proc/{pid}/status').read_text().splitlines() if s.startswith('PPid:')))
        except (OSError,StopIteration,ValueError):return False
    return False
def foreign_gpu_startups(owned):
    found=[]
    for d in Path('/proc').iterdir():
        if not d.name.isdigit() or belongs_to(int(d.name),owned):continue
        try:
            comm=(d/'comm').read_text().strip();argv=(d/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
            if comm.startswith(('python','kit')) and ('/isaac_prod/env/bin/python' in argv or 'validate_building_isaac.py' in argv or 'render_factory80_isaac.py' in argv or ' -m isaacsim' in argv or '--bake-device gpu' in argv or comm.startswith('kit')):
                found.append({'pid':int(d.name),'command':argv})
        except (OSError,ProcessLookupError):pass
    return found
def nvml_host_namespace_proven():
    try:
        d=json.loads((W/'evidence/NVML_PID_NAMESPACE_ACTUAL_PROBE.json').read_text())
        return d['status']=='MEASURED' and all(d['checks'].values())
    except (OSError,KeyError,json.JSONDecodeError):return False
def own_cpu_ram_reservation():
    path=W/'work/gate_audit/CPU_RAM_EXCLUSIVE_LEASE.json'
    try:doc=json.loads(path.read_text())
    except FileNotFoundError:return False
    except (OSError,json.JSONDecodeError):return True
    return doc.get('status') in ('WAITING_FOR_OWN_KIT_EXIT','ACTUAL_EXCLUSIVE_CPU_RETRY_RUNNING')

def actual_cpu_tool_exhaustion(root):
    from factory80_whitelist_actual_tool_failure_reader import actual_cpu_tool_failure
    return actual_cpu_tool_failure(root)

def preserve_prior_cpu_tool_terminal(base_logs,proof):
    path=base_logs/'CANDIDATE_TERMINAL.json'
    if not path.exists():return None
    before=file_ref(path);previous=json.loads(path.read_text())
    assert previous['building']==proof['building'] and previous['attempt']==proof['attempt']
    assert previous['status'] in ('INPUT_MEASUREMENT_FAILED','CPU_TOOL_FAILED_RETRY_NEXT_SCHEME')
    assert previous.get('entire_task_stop') is False
    saved=path.with_name('CANDIDATE_TERMINAL.cpu_tool_failure_history_'+str(time.time_ns())+'.json')
    path.rename(saved)
    assert file_ref(saved)['sha256']==before['sha256']
    return {'original':before,'preserved':file_ref(saved),'actual_previous_terminal':previous}

def preserve_rtx_outputs(root,logs,label):
    path=root/'rtx_review'
    if not path.exists():return None
    assert path.resolve().parent==root.resolve() and root.resolve().is_relative_to((W/'buildings').resolve())
    records=[{'relative_path':str(p.relative_to(path)),**file_ref(p)} for p in sorted(path.rglob('*')) if p.is_file()]
    saved=path.with_name('.rtx_review_'+label+'_'+str(time.time_ns()))
    assert not saved.exists();path.rename(saved)
    result={'utc':stamp(),'source_stage':file_ref(root/'root.usda'),'preserved_directory':str(saved),
            'actual_files_before_move':records,'candidate_scheme_changed':False,'source_modified':False}
    write(logs/('RTX_OUTPUT_PRESERVATION_'+str(time.time_ns())+'.json'),result)
    return result

def verify_completed_native_for_rtx_resume(root,base_logs,manifest,manifest_path=None,summary_path=None):
    rows=manifest['stairs']
    author=json.loads((root/'AUTHORING.json').read_text())
    count=author['stair_count']
    assert type(count) is int and count>=1
    ids={f'S{i:02d}' for i in range(1,count+1)}
    assert len(rows)==count and {r['stair_id'] for r in rows}==ids
    assert author['building']==root.parent.name and author['stage']==file_ref(root/'root.usda')
    # Read every actual report before the summary, so incomplete execution is
    # explicitly rejected even when no aggregation has yet been written.
    reports={r['stair_id']:json.loads(Path(r['isaac_report']).read_text()) for r in rows}
    manifest_path=manifest_path or root/'STAIRS_MANIFEST.json'
    summary_path=summary_path or root/'STAGE_D_MACHINE_SUMMARY.json'
    summary=json.loads(summary_path.read_text())
    assert summary['schema']=='Factory80PerBuildingStageDMachineSummaryV1'
    assert summary['building']==author['building']
    assert summary['source_stage']==file_ref(root/'root.usda')
    assert summary['authoring']==file_ref(root/'AUTHORING.json')
    assert summary['stairs_manifest']==file_ref(manifest_path)
    assert summary['frozen_validator']==file_ref(FROZEN/'validate_building_isaac.py')
    from factory80_stage_d_aggregate_v1 import native_gate_names
    names=native_gate_names()
    assert len(names)==16 and summary['native_gate_count']==16
    assert summary['native_gate_names']==names
    summary_rows=summary['stairs']
    assert len(summary_rows)==count and {r['stair_id'] for r in summary_rows}==ids
    bound={r['stair_id']:r for r in summary_rows}
    proof=[]
    for st in rows:
        report_path=Path(st['isaac_report']);report=reports[st['stair_id']]
        gates=report['acceptance_gates'];assert set(gates)==set(names) and all(type(v) is bool for v in gates.values())
        measured=bound[st['stair_id']]
        assert measured['native_gate_schema_exact'] is True
        assert measured['native_acceptance_gates']==gates
        assert measured['isaac_report']==file_ref(report_path)
        assert measured['build_report']==file_ref(Path(st['build_report']))
        assert measured['traversal_path']==file_ref(Path(st['traversal_path']))
        expected={'--stage':str(root/'root.usda'),'--build-report':st['build_report'],
                  '--stair-path':st['traversal_path'],'--report':st['isaac_report']}
        actual=[]
        for p in base_logs.rglob('03_isaac_'+st['stair_id']+'_r*.RESULT.json'):
            result=json.loads(p.read_text());command=result.get('command',[])
            if result.get('executed') is not True or type(result.get('returncode')) is not int or result['returncode']!=0:continue
            if len(command)<2 or command[1]!=str(FROZEN/'validate_building_isaac.py'):continue
            if all(flag in command and command[command.index(flag)+1]==value for flag,value in expected.items()):actual.append(file_ref(p))
        assert actual,'Native RTX resume lacks a completed original execution: '+st['stair_id']
        proof.append({'stair_id':st['stair_id'],'actual_original_execution_results':actual,
                      'actual_original_report':file_ref(report_path),'actual_build_input':file_ref(Path(st['build_report'])),
                      'actual_native_traversal':file_ref(Path(st['traversal_path'])),'original_acceptance_gates':gates})
    return proof

def actual_initial_native_inputs(root):
    path=root/'OWN_INITIAL_NATIVE_INPUT_SELECTION.json'
    if not path.is_file():return None
    d=json.loads(path.read_text())
    assert d['schema']=='Factory80CorrectedUnstartedNativeInputSelectionV1'
    assert d['new_scheme'] is False and d['native_checks_already_started'] is False
    for key in ('stage','authoring','original_stairs_manifest','confirmed_measurement_bug_evidence','new_stairs_manifest','actual_prepare_result'):
        assert file_ref(Path(d[key]['path']))==d[key], 'Actual corrected preparation binding changed: '+key
    assert d['stage']==file_ref(root/'root.usda') and d['authoring']==file_ref(root/'AUTHORING.json')
    for row in d['original_build_inputs']:assert file_ref(Path(row['build_input']['path']))==row['build_input']
    result=json.loads(Path(d['actual_prepare_result']['path']).read_text())
    assert result['executed'] is True and type(result['returncode']) is int and result['returncode']==0
    command=result['command']
    assert command[1]==str(S/'factory80_prepare_isaac_inputs.py') and command[command.index('--authoring')+1]==str(root/'AUTHORING.json')
    manifest_path=Path(d['new_stairs_manifest']['path'])
    assert manifest_path.resolve().is_relative_to(root.resolve()) and manifest_path.parent!=root
    assert command[command.index('--measurement-output')+1]==str(manifest_path.parent)
    old=json.loads(Path(d['original_stairs_manifest']['path']).read_text())['stairs']
    new=json.loads(manifest_path.read_text())['stairs']
    assert len(old)==len(new)==json.loads((root/'AUTHORING.json').read_text())['stair_count']
    for row in new:
        previous=next(st for st in old if st['stair_id']==row['stair_id'])
        assert all(previous[key]==row[key] for key in ('stair_id','prim_path','traversal_path'))
    return manifest_path

def prepare_unstarted_native_inputs(root,base_logs,logs):
    """Correct actual owned measurement before any original native invocation."""
    assert not list(base_logs.rglob('03_isaac_*.COMMAND.json')), 'Original native invocation already started; use explicit all-M measurement repair instead'
    assert not (root/'OWN_INITIAL_NATIVE_INPUT_SELECTION.json').exists()
    diagnosis=W/'evidence/OWN_ISAAC_INPUT_FLOOR_SCHEMA_READONLY_PILOT_SCOPE.json'
    original_path=root/'STAIRS_MANIFEST.json';old=json.loads(original_path.read_text())
    before={key:file_ref(path) for key,path in [('stage',root/'root.usda'),('authoring',root/'AUTHORING.json'),
            ('original_stairs_manifest',original_path),('confirmed_measurement_bug_evidence',diagnosis)]}
    original_builds=[{'stair_id':row['stair_id'],'build_input':file_ref(Path(row['build_report']))} for row in old['stairs']]
    from factory80_repair_native_measurement import cpu_prepare,PREPARE_SHA
    assert file_ref(S/'factory80_prepare_isaac_inputs.py')['sha256']==PREPARE_SHA
    for retry in range(1,4):
        fresh=root/('native_initial_input_repair_'+str(time.time_ns())+f'_tool_{retry:02d}')
        prefix='02_corrected_prepare_r'+str(retry)
        rc=cpu_prepare([CPU,str(S/'factory80_prepare_isaac_inputs.py'),'--authoring',str(root/'AUTHORING.json'),
                        '--measurement-output',str(fresh)],logs,prefix)
        assert all(file_ref(Path(value['path']))==value for value in before.values())
        if rc==0:
            choice={'schema':'Factory80CorrectedUnstartedNativeInputSelectionV1','utc':stamp(),**before,
                'original_build_inputs':original_builds,'new_stairs_manifest':file_ref(fresh/'STAIRS_MANIFEST.json'),
                'actual_prepare_result':file_ref(logs/(prefix+'.RESULT.json')),
                'actual_prepare_source':file_ref(S/'factory80_prepare_isaac_inputs.py'),
                'new_scheme':False,'native_checks_already_started':False,
                'cannot_support':'Actual owned preparation completed; original native gate execution and visual acceptance still require separate evidence.'}
            write(root/'OWN_INITIAL_NATIVE_INPUT_SELECTION.json',choice)
            return actual_initial_native_inputs(root)
        if retry<3:pause(10*retry)
    raise RuntimeError('Three actual corrected owned input preparation calls failed; repair own runner and preserve partials')

def run(name,args,logs,gpu=False):
    if not gpu:
        if name not in ('01_author','02_prepare_cpu'):return run_body(name,args,logs,False)
        cpupath=W/'work/gate_audit/CPU_HEAVY.lock';cpupath.parent.mkdir(parents=True,exist_ok=True)
        with cpupath.open('a') as cpu_lease:
            while True:
                critical()
                try:fcntl.flock(cpu_lease,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:pause(15)
            try:return run_body(name,args,logs,False)
            finally:fcntl.flock(cpu_lease,fcntl.LOCK_UN)
    leasepath=W/'work/OWNED_GPU_SERIAL.lock';leasepath.parent.mkdir(parents=True,exist_ok=True)
    yielded=0
    while True:
        # Wait for the shared card outside the lease needed by our CPU owner.
        # The original preflight is repeated once after acquiring the lease.
        gpu_free(logs)
        launch_allowed=False
        with leasepath.open('a') as lease:
            while True:
                critical()
                if own_cpu_ram_reservation():pause(15);continue
                try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:pause(15);continue
                if own_cpu_ram_reservation():
                    fcntl.flock(lease,fcntl.LOCK_UN);pause(15);continue
                break
            try:
                launch_allowed=gpu_free(logs,one_probe=True)
                if launch_allowed:
                    label=name if not yielded else name+f'_shared_yield_{yielded}'
                    rc=run_body(label,args,logs,True,gpu_preflight_complete=True)
            finally:fcntl.flock(lease,fcntl.LOCK_UN)
        if not launch_allowed:
            print('GPU_WAIT_SHARED_CARD',stamp(),flush=True);pause(600);continue
        if rc!=66:return rc
        yielded+=1
        # The original child has exited, and the lease is already released.
        # Keep its partial files before another lock-free shared-card wait.
        for flag in ('--out-dir','--output','--report'):
            if flag not in args:continue
            p=Path(args[args.index(flag)+1])
            if p.exists():p.rename(p.with_name(p.name+f'.shared_gpu_yield_{yielded}'))
def run_body(name,args,logs,gpu=False,gpu_preflight_complete=False):
    critical()
    if gpu and not gpu_preflight_complete:gpu_free(logs)
    env=dict(os.environ,PYTHONUNBUFFERED='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='4',CUDA_VISIBLE_DEVICES='0' if gpu else '')
    env['PYTHONPATH']=str(FROZEN)+':'+str(S)+':'+env.get('PYTHONPATH','')
    if gpu:
        env.update(ACCEPT_EULA='Y',OMNI_KIT_ACCEPT_EULA='YES',HF_HUB_OFFLINE='1')
        env['LD_PRELOAD']='/cephfs/shared/yixing/envs/main/lib/libstdc++.so.6'+(':'+env['LD_PRELOAD'] if env.get('LD_PRELOAD') else '')
        for variable,folder in [('XDG_CACHE_HOME','cache'),('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_RUNTIME_DIR','runtime')]:
            d=W/'work/isaac_runtime'/folder;d.mkdir(parents=True,exist_ok=True)
            if variable=='XDG_RUNTIME_DIR':d.chmod(0o700)
            env[variable]=str(d)
    with (logs/(name+'.stdout')).open('x') as out,(logs/(name+'.stderr')).open('x') as err:
        p=subprocess.Popen(args,cwd=W,env=env,stdout=out,stderr=err,start_new_session=True)
        write(logs/(name+'.COMMAND.json'),{'utc':stamp(),'command':args,'pid':p.pid,'own_process_group':p.pid,'gpu':gpu})
        monitored=0;yield_record=None
        while p.poll() is None:
            try:critical()
            except SystemExit:
                if os.getpgid(p.pid)==p.pid:os.killpg(p.pid,signal.SIGTERM)
                p.wait();raise
            if gpu and time.monotonic()-monitored>=15:
                sample=command(['nvidia-smi','--query-gpu=name,memory.used,memory.free,utilization.gpu','--format=csv,noheader'])
                apps=command(['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader'])
                with (logs/(name+'.GPU_MONITOR.jsonl')).open('a') as f:f.write(json.dumps({'utc':stamp(),'gpu':sample,'compute_apps':apps,'owned_child_pid':p.pid})+'\n')
                foreign=[];unresolved=[];namespace_proven=nvml_host_namespace_proven()
                if apps['returncode']==0:
                    for line in apps.get('stdout','').splitlines():
                        try:pid=int(line.split(',',1)[0].strip())
                        except ValueError:continue
                        if namespace_proven:
                            # NVML IDs from the host cannot be compared with
                            # container-local PPid numbers. Retain uncertainty.
                            unresolved.append({'pid':pid,'compute_app_record':line,'ownership':'UNRESOLVED_HOST_PID'})
                        elif not belongs_to(pid,p.pid):foreign.append({'pid':pid,'compute_app_record':line})
                startups=foreign_gpu_startups(p.pid)
                with (logs/(name+'.GPU_OWNERSHIP.jsonl')).open('a') as f:
                    f.write(json.dumps({'utc':stamp(),'actual_namespace_probe_passed':namespace_proven,
                        'unresolved_nvml_apps':unresolved,'visible_foreign_startups':startups,
                        'policy':'Successful empty preflight required. One unresolved host PID after own launch is retained as unresolved; competing contexts or visible foreign startups yield only our group.'})+'\n')
                if apps['returncode']!=0 or foreign or startups or len(unresolved)>1:
                    yield_record={'utc':stamp(),'compute_query':apps,'foreign_compute_apps':foreign,'foreign_gpu_startups':startups,
                                  'action':'Yield shared GPU by terminating only the verified owned process group; preserve partial output and wait for successful idle checks.'}
                    if os.getpgid(p.pid)==p.pid:os.killpg(p.pid,signal.SIGTERM)
                    else:raise RuntimeError('Owned process group identity changed; refuse signalling')
                    p.wait();break
                monitored=time.monotonic()
            time.sleep(2)
        actual_parent_wait_returncode=p.wait()
        rc=66 if yield_record is not None else actual_parent_wait_returncode
    write(logs/(name+'.RESULT.json'),{'utc':stamp(),'returncode':rc,'executed':True,'command':args,
        'actual_child_pid':p.pid,'actual_parent_wait_completed':True,
        'actual_parent_wait_returncode':actual_parent_wait_returncode,
        'shared_gpu_yield_control_code':66 if yield_record is not None else None})
    if yield_record is not None:write(logs/(name+'.SHARED_GPU_YIELD.json'),yield_record)
    if rc==80:raise SystemExit(80)
    return rc
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--building',required=True);ap.add_argument('--attempt',type=int,required=True);ap.add_argument('--resume-after-inputs',action='store_true');ap.add_argument('--resume-rtx',action='store_true');ap.add_argument('--retry-author-tool',type=int,choices=(1,2));ap.add_argument('--reprepare-before-native',action='store_true');a=ap.parse_args()
    assert sum(bool(v) for v in (a.resume_after_inputs,a.resume_rtx,a.retry_author_tool))<=1
    resume_inputs=a.resume_after_inputs or a.resume_rtx
    assert not a.reprepare_before_native or a.resume_after_inputs
    assert a.building.startswith('factory80_') and 1<=int(a.building.rsplit('_',1)[1])<=6 and 1<=a.attempt<=3
    root=W/'buildings'/a.building/f'attempt_{a.attempt:02d}';base_logs=W/'logs'/a.building/f'attempt_{a.attempt:02d}'
    logs=base_logs/('author_tool_retry_'+str(a.retry_author_tool)) if a.retry_author_tool else base_logs/(('resume_rtx_' if a.resume_rtx else 'resume_')+str(time.time_ns())) if resume_inputs else base_logs
    logs.mkdir(parents=True,exist_ok=True)
    assets=select_actual_assets(a,base_logs)
    if a.retry_author_tool:
        terminal=base_logs/'CANDIDATE_TERMINAL.json';previous=json.loads(terminal.read_text())
        assert previous['status']=='AUTHORING_FAILED' and not (root/'AUTHORING.json').exists()
        assert root.resolve().parent==(W/'buildings'/a.building).resolve()
        saved=root.with_name(root.name+f'.author_tool_error_{a.retry_author_tool}')
        assert not saved.exists()
        actual_files=[]
        for p in root.rglob('*'):
            if p.is_file():
                b=p.read_bytes();actual_files.append({'relative_path':str(p.relative_to(root)),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
        root.rename(saved)
        terminal.rename(terminal.with_name(f'CANDIDATE_TERMINAL.author_tool_error_{a.retry_author_tool}.json'))
        write(logs/'RETRY_AUTHOR_TOOL_INPUT.json',{'utc':stamp(),'same_building_scheme':a.attempt,'prior_terminal':previous,
           'preserved_partial_directory':str(saved),'preserved_actual_files':actual_files,'source_assets_modified':False})
        pause(10*a.retry_author_tool)
    if resume_inputs:
        author=json.loads((root/'AUTHORING.json').read_text());raw=(root/'root.usda').read_bytes()
        expected=author['stage']
        assert hashlib.sha256(raw).hexdigest()==expected['sha256'] and len(raw)==expected['bytes']
        assert (root/'STAIRS_MANIFEST.json').is_file()
        write(logs/'RESUME_INPUTS_VERIFIED.json',{'utc':stamp(),'root_stage':expected,'authoring_repeated':False,'candidate_scheme_changed':False})
        rc=0
    else:rc=run('01_author', [CPU,str(S/'build_factory80_overlay.py'),'--building',a.building,'--attempt',str(a.attempt),'--stair-assets',str(assets)],logs)
    if rc:
        write(base_logs/'CANDIDATE_TERMINAL.json',{'building':a.building,'attempt':a.attempt,'status':'AUTHORING_FAILED','work_completion':'PARTIAL','verification':'NOT_EXECUTED','utc':stamp()});return rc
    rc=0 if resume_inputs else run('02_prepare_cpu',[CPU,str(S/'factory80_prepare_isaac_inputs.py'),'--authoring',str(root/'AUTHORING.json')],logs)
    if rc:
        write(base_logs/'CANDIDATE_TERMINAL.json',{'building':a.building,'attempt':a.attempt,'status':'INPUT_MEASUREMENT_FAILED','work_completion':'GEOMETRY_AUTHORED','verification':'NOT_EXECUTED','utc':stamp()});return rc
    manifest_path=actual_initial_native_inputs(root) or root/'STAIRS_MANIFEST.json'
    if a.reprepare_before_native and manifest_path==root/'STAIRS_MANIFEST.json':
        manifest_path=prepare_unstarted_native_inputs(root,base_logs,logs)
    manifest=json.loads(manifest_path.read_text());summary_path=manifest_path.parent/'STAGE_D_MACHINE_SUMMARY.json'
    # The CPU worker actually runs pinned connectivity before spending GPU
    # time on a scheme that must be replaced. The final allowed scheme still
    # gets honest native/RTX evidence even if it fails, for the pilot report.
    geometry_path=root/'cpu_geometry/OBJECTIVE_TILING_QUALITY.json'
    # A partial tool/OOM receipt supplies no completed geometric verdict.
    # The CPU owner preserves it and repairs/retries the same input scheme.
    tool_failure_proof=None
    while True:
        try:geometry=json.loads(geometry_path.read_text())
        except (FileNotFoundError,json.JSONDecodeError):geometry={}
        if geometry.get('work_completion')=='MEASURED':break
        exhausted=actual_cpu_tool_exhaustion(root)
        if exhausted:
            assert exhausted['schema']=='Factory80ActualOrdinaryCpuToolFailureReviewV1'
            assert exhausted['status'] in ('ACTUAL_TOOL_ATTEMPTS_EXHAUSTED','ACTUAL_REPEATED_ORDINARY_TOOL_FAILURES')
            assert exhausted['source_stage']==file_ref(root/'root.usda')
            assert exhausted['authoring']==file_ref(root/'AUTHORING.json')
            assert exhausted['work_completion']=='PARTIAL' and exhausted['entire_task_stop'] is False
            if a.attempt<3:
                assert exhausted['eligible_for_predeclared_next_scheme'] is True
                prior=preserve_prior_cpu_tool_terminal(base_logs,exhausted)
                write(base_logs/'CANDIDATE_TERMINAL.json',{'utc':stamp(),'building':a.building,'attempt':a.attempt,
                    'status':'CPU_TOOL_FAILED_RETRY_NEXT_SCHEME','work_completion':'GEOMETRY_AUTHORED_ACTUAL_CPU_TOOL_FAILURE',
                    'verification':'NO_COMPLETE_SOURCE_BOUND_VOXEL_VERDICT','actual_cpu_tool_failure_review':exhausted,
                    'actual_previous_terminal_preservation':prior,
                    'entire_task_stop':False,'formal_acceptance':'NOT EXECUTED',
                    'cannot_support':'原CPU工具失败仅证明实际工具耗尽；V1与负控未完成，不提供门禁判定。继续预登记下一配对与新实测签名。'})
                return 1
            assert exhausted['status']=='ACTUAL_TOOL_ATTEMPTS_EXHAUSTED' and exhausted['eligible_for_final_native_rtx_attempt'] is True
            tool_failure_proof=exhausted
            prior=preserve_prior_cpu_tool_terminal(base_logs,exhausted)
            write(logs/'CPU_ORDINARY_TOOL_FAILURE_NATIVE_RTX_SELECTION.json',{'utc':stamp(),
                'execution':'SOURCE_BOUND_FINAL_SCHEME_NATIVE_RTX_CONTINUATION_SELECTED',
                'source_stage':file_ref(root/'root.usda'),'authoring':file_ref(root/'AUTHORING.json'),
                'actual_ordinary_cpu_tool_failure':exhausted,'actual_previous_terminal_preservation':prior,
                'native_rtx_execution_at_selection':'NOT EXECUTED','candidate_scheme_changed':False,
                'cannot_support':'保留PARTIAL与未测V1；仅选择继续真实原native/RTX调用，不声明门禁或媒体已经执行。'})
            break
        pause(15)
    expected_stage=json.loads((root/'AUTHORING.json').read_text())['stage']
    if geometry.get('stage',{}).get('sha256')!=expected_stage['sha256']:
        write(base_logs/'CANDIDATE_TERMINAL.json',{'utc':stamp(),'building':a.building,'attempt':a.attempt,
          'status':'CPU_INPUT_MEASUREMENT_UNVERIFIED','work_completion':'GEOMETRY_AUTHORED',
          'verification':'REPORT_SOURCE_BINDING_MISSING_OR_MISMATCHED','expected_stage':expected_stage,
          'entire_task_stop':False,'formal_acceptance':'NOT EXECUTED',
          'cannot_support':'缺失或错配的输入绑定不能支持任何候选门禁结论。'})
        return 1
    if geometry['status']!='PASS' and a.attempt<3:
        write(base_logs/'CANDIDATE_TERMINAL.json',{'utc':stamp(),'building':a.building,'attempt':a.attempt,
          'status':'CPU_GEOMETRY_FAILED_RETRY_NEXT_SCHEME','work_completion':'GEOMETRY_AUTHORED_AND_MEASURED',
          'verification':'ACTUAL_CPU_GEOMETRY_FAIL; ISAAC_AND_RTX_NOT_EXECUTED',
          'entire_task_stop':False,'formal_acceptance':'NOT EXECUTED',
          'cannot_support':'普通候选失败继续下一方案；不把未执行的Isaac、RTX或负控填成PASS/FAIL。'})
        return 1
    if a.resume_rtx:
        terminal=base_logs/'CANDIDATE_TERMINAL.json';previous=json.loads(terminal.read_text())
        assert previous['status']=='RTX_TOOL_FAILED'
        proof=verify_completed_native_for_rtx_resume(root,base_logs,manifest,manifest_path,summary_path)
        terminal.rename(terminal.with_name('CANDIDATE_TERMINAL.rtx_tool_error_'+str(time.time_ns())+'.json'))
        preserve_rtx_outputs(root,logs,'before_same_input_resume')
        write(logs/'RESUME_RTX_INPUTS_VERIFIED.json',{'utc':stamp(),'source_stage':file_ref(root/'root.usda'),
            'authoring':file_ref(root/'AUTHORING.json'),'stairs_manifest':file_ref(manifest_path),
            'actual_original_native_proofs':proof,'stage_d_summary':file_ref(summary_path),
            'authoring_repeated':False,'isaac_validation_repeated':False,'candidate_scheme_changed':False,
            'executor':file_ref(Path(__file__)),'renderer':file_ref(S/'render_factory80_isaac.py')})
    native_tool_results=[]
    for st in ([] if a.resume_rtx else manifest['stairs']):
        d=Path(st['build_report']).parent;d.mkdir(parents=True,exist_ok=True)
        args=[ISAAC,str(FROZEN/'validate_building_isaac.py'),'--stage',str(root/'root.usda'),
              '--build-report',st['build_report'],'--stair-path',st['traversal_path'],'--out-dir',str(d/'renders'),
              '--report',st['isaac_report'],'--/renderer/activeGpu=0','--/physics/cudaDevice=0']
        for retry in range(1,4):
            rc=run('03_isaac_'+st['stair_id']+f'_r{retry}',args,logs,gpu=True)
            if rc==0:break
            # Original validator writes semantic FAIL with exit0; nonzero is
            # a tooling error. Preserve each report before retrying tools.
            p=Path(st['isaac_report'])
            if p.exists():p.rename(p.with_name(f'isaac_validation_tool_error_r{retry}.json'))
            if retry<3:pause(10*retry)
        native_tool_results.append({'stair_id':st['stair_id'],'last_actual_tool_returncode':rc,
            'actual_original_report':file_ref(Path(st['isaac_report'])) if Path(st['isaac_report']).is_file() else None})
    aggregate_rc=None
    if not a.resume_rtx:aggregate_rc=run('04_aggregate',[sys.executable,str(S/'factory80_stage_d_aggregate_v1.py'),'--building',a.building,
                       '--authoring',str(root/'AUTHORING.json'),'--stairs-manifest',str(manifest_path),
                       '--output',str(summary_path)],logs)
    # Full-factory evidence is captured even for a failed candidate, clearly
    # labelled as render-only. Whitelist was measured on native assets first.
    renderdir=root/'rtx_review'
    render_args=[ISAAC,str(S/'render_factory80_isaac.py'),'--authoring',str(root/'AUTHORING.json'),
                 '--output',str(renderdir),'--frames','120','--/renderer/activeGpu=0','--/physics/cudaDevice=0']
    for retry in range(1,4):
        rc=run('05_factory_rtx_r'+str(retry),render_args,logs,gpu=True)
        if rc==0:break
        preserve_rtx_outputs(root,logs,'tool_error_r'+str(retry))
        if retry<3:pause(10*retry)
    write(base_logs/'CANDIDATE_TERMINAL.json',{'building':a.building,'attempt':a.attempt,'status':'EXECUTION_COMPLETED' if rc==0 else 'RTX_TOOL_FAILED',
           'work_completion':'SOURCE_BOUND_NATIVE_AND_RTX_CALLS_FINISHED','verification':'SEE_NATIVE_AND_CPU_REPORTS','images_actually_viewed':False,
           'actual_cpu_geometry_work_completion':geometry.get('work_completion'),
           'actual_ordinary_cpu_tool_failure':tool_failure_proof,'actual_native_tool_results':native_tool_results,
           'actual_aggregate_tool_returncode':aggregate_rc,
           'formal_acceptance':'NOT EXECUTED','utc':stamp()})
    return rc
if __name__=='__main__':raise SystemExit(main())
