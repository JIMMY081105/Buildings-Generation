"""Approved operational overlay on immutable Round4 plan3; no scene/check/render algorithm."""
import copy,datetime,hashlib,json
from pathlib import Path
ORDER=('057','067','027','060','054')
POLICY_NAME='approved_revision_policy.json'
def read(path):return json.loads(Path(path).read_text())
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def reference(path):return dict(path=str(path),sha256=digest(path))
def bound(row):
    assert digest(row['path'])==row['sha256'],('Revision bound source changed',row)
    return read(row['path'])
def load_policy(root,path=None):
    root=Path(root);p=Path(path) if path else root/'pipeline/v4'/POLICY_NAME;policy=read(p)
    assert policy['schema']=='Factory80ApprovedRenderC2OperationalRevisionV1'
    assert policy['execution_order']==list(ORDER) and set(policy['houses'])==set(ORDER)
    assert policy['cohort_plan']['sha256']=='57c651f17d3dfbf81b064c0ee60d2e0e908b55034c1f28d352a57c2728d97083';original_plan=bound(policy['cohort_plan'])
    assert len(original_plan['source_bindings'])==119
    assert all(digest(row['path'])==row['sha256'] for row in original_plan['source_bindings']),'Original119 source bindings changed'
    qualification=bound(policy['promotion_qualification']);assert qualification['status']=='PASS_QUALIFIED_FOR_APPROVED_PRODUCTION_MIGRATION_NOT_YET_DEPLOYED'
    rgb=bound(qualification['RGB_proof']);assert rgb['status']=='PASS' and rgb['authorized_round']==2 and rgb['SDK']['actual_exit_code']==0 and rgb['SDK']['actual_peak_RSS_bytes']<=19.7*1024**3
    assert len(rgb['comparisons'])==6 and all(r['camera_readback_exact_equal'] and r['MAE_255']<=4 and r['all_sliding_64x64_max_MAE_255']<=32 for r in rgb['comparisons'])
    assert qualification['C5_semantic_parity']['whole_file_equal_after_only_RGB_import_and_metadata_version_normalization']
    qualified_sources=bound(qualification['source_freeze'])['source_files']
    for name in ('isaac_native_c5.py','rtx_render.py','rgb_instance_wrappers.py'):
        matches=[value for path,value in qualified_sources.items() if Path(path).name==name]
        assert matches==[policy['qualified_scientific_tools'][name]],('RGB qualified source differs',name)
    gpu=bound(policy['C2_GPU_qualification']);assert gpu['status']=='PASS' and gpu['negative']['status']=='FAIL'
    assert policy['qualified_scientific_tools']['gpu_checks.py']==gpu['new_source']['sha256']
    assert policy['qualified_scientific_tools']['isaac_native_c5.py']==qualification['C5_semantic_parity']['qualified_native8']['sha256']
    snapshot=bound(policy['preparation_snapshot']);assert snapshot['execution_order']==list(ORDER)
    assert snapshot['T0_UTC']==policy['T0_UTC']=='2026-10-31T01:23:00Z'
    assert policy['time_rules']==dict(record_only_s=3600,hard_stop_s=10800)
    assert policy['resource_policy']['sha256']=='97580079156f5a66c1f20ac4fa05d36a85169ebab25733b37932bc8a1d7e18d3'
    assert digest(policy['resource_policy']['path'])==policy['resource_policy']['sha256']
    for bid,row in policy['houses'].items():
        original=snapshot['houses'][bid]
        assert row==original and Path(row['new_directory']).resolve()==(root/'runs'/bid/'v4/round4_v2').resolve()
        assert row['resource_epoch_start_retries']==0 and row['carry_C4_alternatives']==0
        assert row['carry_scientific_repairs']==row['historical_scientific_repairs']-row['scientific_refund']
        assert row['scientific_refund']==(1 if bid=='057' else 0)
        for source in row['history'].values():bound(source)
        if row['source_frozen_input']:
            frozen=bound(row['source_frozen_input']);assert frozen==row['frozen_input']
            assert all(digest(path)==value for path,value in frozen['source_files'].items())
    policy['_reference']=reference(p);return policy
def job_overlay(job,policy):
    result=copy.deepcopy(job);bid=result['building']
    if bid not in policy['houses']:return result
    row=policy['houses'][bid];assert result['house_directory']==row['old_directory']
    result['house_directory']=row['new_directory'];result['job_id']=bid+'_round4_v2'
    result['operational_revision']=policy['_reference'];result['historical_house_directory']=row['old_directory']
    if row['frozen_input']:
        assert not result.get('initial_geometry_binding'),'Carryover cannot alter a geometry-binding job'
        result['initial_physics_directory']=row['frozen_input']['P']
    return result
def carry_row(building,policy):
    row=policy['houses'][building]
    return dict(P=Path(row['frozen_input']['P']) if row['frozen_input'] else None,used=set(row['carry_layout_seed_indices_used']),repairs=row['carry_scientific_repairs'],alt=row['carry_C4_alternatives'],roles_root=Path(row['frozen_input']['roles_root']) if row['frozen_input'] and row['frozen_input'].get('roles_root') else None)
def photo_oom_classification(event,worker_run):
    """Only actual C1-C5 PASS + native photos job + owned kernel OOM qualifies."""
    evidence=event.get('OOM_evidence') or {}
    if not evidence.get('confirmed') or evidence.get('cgroup_oom_kill_delta',0)<1:return None
    clean=event.get('clean_directory')
    if not clean:return None
    clean=Path(clean);raw=clean/'checks/CHECK_RESULT.json';c5_path=clean/'c5/C5.json';photos_path=clean/'photos/PHOTOS.json'
    if not raw.is_file() or not c5_path.is_file():return None
    checks=read(raw).get('checks',{});c5=read(c5_path)
    if any(checks.get('C'+str(i),{}).get('status')!='PASS' for i in range(1,5)) or c5.get('status')!='PASS':return None
    if photos_path.is_file() and read(photos_path).get('status')=='PASS':return None
    session=c5.get('resident_session') or {};killed=evidence.get('killed_owned_processes',[]);owner_path=Path(worker_run)/'NATIVE_OWNER.json'
    if not owner_path.is_file():return None
    owner=read(owner_path)
    if owner.get('pid')!=session.get('pid') or str(owner.get('birth'))!=str(session.get('birth')):return None
    if not any(row.get('exit_code')==-9 for row in killed):return None
    log=Path(worker_run)/'NATIVE_STDOUT.log'
    if not log.is_file():return None
    started=[]
    for line in log.read_text(errors='replace').splitlines():
        if line.startswith('FACTORY80_JOB_STARTED '):started.append(json.loads(line[len('FACTORY80_JOB_STARTED '):]))
    if not started or started[-1].get('mode')!='photos' or started[-1].get('job_id')!=clean.parent.parent.parent.name+'_'+clean.name+'_photos':return None
    return dict(status='PHOTO_OOM_PENDING_FIX',clean_directory=str(clean),C1_C5_status='PASS_ALL',raw_checks=reference(raw),C5=reference(c5_path),native_log_at_stop=reference(log),scientific_budget_charged=False,resource_retry_budget_charged=False,same_input_retry_forbidden=True)
def mark_photo_pending(state,event,classification):
    state=copy.deepcopy(state);state.pop('active_attempt',None)
    state.setdefault('photo_oom_history',[]).append(dict(event=event,classification=classification))
    state['status']='PHOTO_OOM_PENDING_FIX';state['same_input_retry_forbidden']=True
    state['resource_retries_used']=len(state.get('attempts',[]))
    return state
