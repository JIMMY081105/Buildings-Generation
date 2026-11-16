"""Explicit-house Round4 remote-only pack; full CPU review precedes canonical release."""
from pathlib import Path
import argparse, datetime, hashlib, json, os, shutil, subprocess, time, uuid, zipfile

def stamp():return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
_DIGEST_CACHE={}
def digest(path):
    path=Path(path);before=path.stat();key=(str(path.resolve()),before.st_size,before.st_mtime_ns)
    cached=_DIGEST_CACHE.get(key)
    if cached and cached[0]==(before.st_dev,before.st_ino):return cached[1]
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024**2),b''):h.update(block)
    after=path.stat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('Source changed during SHA256: '+str(path))
    value=h.hexdigest();_DIGEST_CACHE[key]=((before.st_dev,before.st_ino),value);return value
def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.tmp.'+str(os.getpid()))
    temp.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');os.replace(temp,path)
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))

OLD001_PILOT_SHA='2c51b9e92dcc6145d303ce727c1778fdaf72b166ad6c8cc66b6eebd5d98d9ab9'

def verified_reference(value,label):
    if not isinstance(value,dict) or not value.get('path') or not value.get('sha256'):raise ValueError(label+' requires actual path/SHA')
    path=Path(value['path'])
    if not path.is_file() or digest(path)!=value['sha256']:raise ValueError(label+' bytes differ')
    return path

def reference_path(value):
    if isinstance(value,dict):value=value.get('path')
    if not isinstance(value,str) or not value:raise ValueError('Metadata reference has no path')
    return value


def active_receipt_plan(photos,clean_e2e):
    """Select actual source references without filesystem writes or a READY declaration."""
    camera=reference_path(photos['automatic_camera_plan']);camera_sha=photos['input_sha']['cameras']
    entries=[dict(source_path=camera,archive_path='receipts/active/CAMERA_PLAN.json',expected_sha256=camera_sha,binding='PHOTOS.input_sha.cameras')];found=set()
    for source,expected in clean_e2e['input_sha256'].items():
        name=Path(source).name
        if name in found:raise ValueError('Duplicate active input basename: '+name)
        found.add(name);entries.append(dict(source_path=source,archive_path='receipts/active/inputs/'+name,expected_sha256=expected,binding='CLEAN_E2E_RESULT.input_sha256'))
    if not {'LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json'}<=found:raise ValueError('Actual clean input bindings missing')
    return entries


def pack_admission(root,bid,house,cohort_plan_path):
    """Use the same explicit Round4 cohort and SHA-bound prior as report4."""
    from report_batch_legacy import load_cohort_plan,current_cohort,house_evidence
    plan=load_cohort_plan(root,cohort_plan_path);cohort=current_cohort(root,bid,plan)
    if cohort['kind']=='PRIOR_FROZEN_READY':raise ValueError('Frozen old READY is not a new Round4 pack target')
    if Path(cohort['directory']).resolve()!=Path(house).resolve():raise ValueError('Explicit house differs from current authorized cohort')
    evidence=house_evidence(root,bid,house,cohort)
    if not evidence['READY_V4']:raise ValueError('Current cohort is not actual READY with valid strictest-tier/config evidence')
    return evidence


def native_c5_receipt_entries(clean,checks):
    """Preserve the final native5 canonical C5 and its SHA-bound raw query."""
    canonical=Path(clean)/'c5/C5.json';doc=read(canonical)
    checks=checks.get('checks',checks)
    if doc.get('status')!='PASS' or checks.get('C5')!=doc:raise ValueError('Final native C5 bytes/data differ from aggregate checks')
    raw_ref=doc.get('raw_query_or_capture_result');raw=verified_reference(raw_ref,'Final native raw C5')
    if raw.name!='C5.json' or not raw.resolve().is_relative_to(canonical.parent.resolve()):raise ValueError('Final raw C5 reference is outside its actual native job')
    bindings=[dict(path=str(canonical),sha256=digest(canonical),archive_path='receipts/clean/c5/C5.json'),
        dict(raw_ref,archive_path='receipts/clean/'+raw.resolve().relative_to(Path(clean).resolve()).as_posix())]
    return [(Path(row['path']),row['archive_path']) for row in bindings],bindings


def actual_evidence_entries(house,clean,evidence):
    """Bind small evidence files to the completed run; preserve all raw sources."""
    photos=read(house/'photos/PHOTOS.json');clean_e2e=read(clean/'CLEAN_E2E_RESULT.json')
    if clean_e2e!=evidence['e2e']:raise ValueError('Canonical E2E differs from final clean result')
    planned=active_receipt_plan(photos,clean_e2e);entries=[];bindings=[]
    for row in planned:
        path=Path(row['source_path']);actual=digest(path)
        if actual!=row['expected_sha256']:raise ValueError('Active metadata differs from run SHA: '+str(path))
        entries.append((path,row['archive_path']));bindings.append(dict(**row,actual_sha256=actual))
    inputs={Path(r['source_path']).name:r for r in planned if r['archive_path'].startswith('receipts/active/inputs/')}
    layout=read(inputs['LAYOUT.json']['source_path']);stairs=read(inputs['stairs_spec.json']['source_path'])
    selection=dict(schema='Factory80Round4ActivePackagingEvidenceV7',active_bindings=bindings,
        actual_layout_attempt=layout.get('layout_attempt'),actual_building_seed=layout.get('seed'),
        actual_stairs=[{k:s.get(k) for k in ('id','kind','seed','offset_m','rotation_deg')} for s in stairs['stairs']],
        C4_alternatives_used=evidence['status'].get('C4_alternatives_used',0),face_roles=[],old_failures=[],repair_references=[])
    native_entries,native_bindings=native_c5_receipt_entries(clean,evidence['checks'])
    entries+=native_entries;selection['native_c5_bindings']=native_bindings
    config_ref=evidence['status'].get('config')
    config_path=verified_reference(config_ref,'STATUS actual config')
    if config_ref['sha256']!=evidence['config_sha256'] or config_ref['sha256']!=clean_e2e['config_sha256']:raise ValueError('Actual config/E2E binding differs')
    entries.append((config_path,'receipts/active/CONFIG.json'));selection['config']=dict(config_ref,archive_path='receipts/active/CONFIG.json')
    for name in ('REPAIR_STATE_V3.json','REPAIR_STATE_V4.json','REPAIR_BUDGET.json'):
        path=house/name
        if not path.is_file():continue
        state=read(path);entries.append((path,'receipts/repair/'+name))
        selection['repair_references'].append(dict(path=str(path),sha256=digest(path),archive_path='receipts/repair/'+name))
        if name.startswith('REPAIR_STATE_'):
            accepted=Path(state['accepted_input_directory'])
            for input_name,expected in state['accepted_input_sha256'].items():
                if digest(accepted/input_name)!=expected:raise ValueError('Actual accepted repair input changed '+input_name)
            if accepted.resolve()!=Path(inputs['LAYOUT.json']['source_path']).parent.resolve():raise ValueError('Repair state does not identify final clean inputs')
            selection['actual_repair_state']=state
    tier_ref=evidence['status'].get('tier_selection')
    if tier_ref:
        path=verified_reference(tier_ref,'Actual tier selection');entries.append((path,'receipts/active/TIER_SELECTION.json'))
        selection['tier_selection']=dict(tier_ref,archive_path='receipts/active/TIER_SELECTION.json')
        tiers=read(path)
        for row in tiers.get('tiers',[]):
            for key,basename in (('precompute_result','PRECOMPUTE_RESULT.json'),('config','CONFIG.json')):
                source=verified_reference(row[key],'Tier '+row['tier']+' '+key);name='receipts/layout_tiers/'+row['tier']+'/'+basename
                entries.append((source,name));selection['repair_references'].append(dict(row[key],archive_path=name))
        if tiers.get('original_exclusion_source'):
            source=verified_reference(tiers['original_exclusion_source'],'Historical original exclusion');name='receipts/history/ORIGINAL_BATCH_STATUS.json'
            entries.append((source,name));selection['repair_references'].append(dict(tiers['original_exclusion_source'],archive_path=name))
    for folder in ('raw_before_alternative','raw_before_checks','pre_alternative_metadata'):
        directory=house/folder
        if directory.is_dir():
            for path in sorted(directory.rglob('*.json')):
                name='receipts/repair/'+folder+'/'+path.relative_to(directory).as_posix()
                entries.append((path,name));selection['repair_references'].append(dict(path=str(path),sha256=digest(path),archive_path=name))
    initial=house/'CAMERA_PLAN.json'
    if initial.is_file():
        entries.append((initial,'receipts/initial/CAMERA_PLAN.json'))
        selection['initial_camera_plan']=dict(path=str(initial),sha256=digest(initial),used_for_final_images=initial.resolve()==Path(planned[0]['source_path']).resolve())
    alt=bool(evidence['status'].get('C4_alternatives_used')) or bool(layout.get('C4_alternative_index'))
    if alt:
        effective_ref=layout.get('effective_plan')
        effective=Path(reference_path(effective_ref)) if effective_ref else house/'c4_alternative_1/EFFECTIVE_BUILDING_PLAN.json'
        if isinstance(effective_ref,dict) and effective_ref.get('sha256') and digest(effective)!=effective_ref['sha256']:raise ValueError('Effective plan reference changed')
        alternative=effective.parent;effective_doc=read(effective)
        for name in ('EFFECTIVE_BUILDING_PLAN.json','C4_ALTERNATIVE_RECEIPT.json','C4_ALTERNATIVE_BUDGET.json'):
            path=alternative/name
            if not path.is_file():raise ValueError('Missing C4 alternative evidence: '+str(path))
            entries.append((path,'receipts/c4_alternative/'+name))
            selection['repair_references'].append(dict(path=str(path),sha256=digest(path),archive_path='receipts/c4_alternative/'+name))
        for name in ('ALTERNATIVE_STARTED.json','LAYOUT_INDEX_INVOKED.json'):
            if (alternative/name).is_file():entries.append((alternative/name,'receipts/c4_alternative/'+name))
        role_rows=read(alternative/'C4_ALTERNATIVE_RECEIPT.json').get('native_face_role_bindings',[])
        if not role_rows:raise ValueError('C4 alternative role-source index missing')
        for role in role_rows:
            sid=role['stair_id'];path=Path(role['path']);actual=digest(path);target=role['actual_target']
            if actual!=target['sha256']:raise ValueError('Donor face-role bytes changed: '+sid)
            name='receipts/c4_alternative/face_roles/'+sid+'_NATIVE_FACE_ROLES.json';entries.append((path,name))
            selection['face_roles'].append(dict(stair_id=sid,archive_path=name,path=str(path),resolved_source=str(path.resolve()),sha256=actual,actual_target=target,source_blend=role.get('source_blend'),source_blend_bytes_rehashed=False))
        selection['effective_plan']=dict(path=str(effective),sha256=digest(effective),actual_stair_source_history=effective_doc.get('stair_source_history',[]),original_failed_checks=effective_doc.get('original_failed_checks'))
        for key in ('prior_budget','original_failed_checks'):
            ref=effective_doc.get(key)
            if ref:
                path=verified_reference(ref,'Alternative '+key);name='receipts/c4_alternative/prior/'+key+'_'+path.name
                entries.append((path,name));selection['repair_references'].append(dict(ref,archive_path=name))
        failed_ref=effective_doc.get('original_failed_checks')
        if failed_ref:
            failed_path=Path(reference_path(failed_ref));actual=digest(failed_path)
            if isinstance(failed_ref,dict) and failed_ref.get('sha256')!=actual:raise ValueError('Original C4 failure reference changed')
            old_checks=read(failed_path);old_checks=old_checks.get('checks',old_checks)
            selection['c4_original_failure']=dict(source=failed_ref,actual_sha256=actual,gate_statuses={k:v.get('status') for k,v in old_checks.items() if k in ('C1','C2','C3','C4','C5') and isinstance(v,dict)},missing_C5_is_not_execution=True)
    for old in sorted(house.glob('clean_*')):
        if not old.is_dir() or old.resolve()==clean.resolve():continue
        for path in sorted(old.rglob('*.json')):
            relative=path.relative_to(old)
            if relative.parts[0] in ('execution','checks') or relative.as_posix() in ('CLEAN_E2E_START.json','CLEAN_E2E_RESULT.json','CHECKS.json'):
                name='receipts/history/'+old.name+'/'+relative.as_posix()
                entries.append((path,name));selection['repair_references'].append(dict(path=str(path),sha256=digest(path),archive_path=name))
        if not (old/'CLEAN_E2E_RESULT.json').is_file():
            selection['old_failures'].append(dict(clean_directory=str(old),status='TECHNICAL_UNFINISHED_NO_RESULT',execution_extent='PARTIAL',actual_C5_status='UNKNOWN_NO_COMPLETE_RESULT',planned_list_is_not_execution_evidence=True))
            continue
        result=read(old/'CLEAN_E2E_RESULT.json')
        if result.get('status') not in ('FAIL','TIME_LIMIT','STOPPED'):continue
        checks_path=old/'CHECKS.json';checks=read(checks_path) if checks_path.is_file() else {}
        if 'checks' in checks:checks=checks['checks']
        executed=[r['name'] for r in result.get('stages',[]) if isinstance(r,dict) and 'name' in r]
        c5_status=checks.get('C5',{}).get('status','NOT_EXECUTED');photo_count=result.get('photos_count')
        partial=c5_status=='NOT_EXECUTED' or not photo_count or 'photos' not in executed
        selection['old_failures'].append(dict(clean_directory=str(old),result_path=str(old/'CLEAN_E2E_RESULT.json'),result_sha256=digest(old/'CLEAN_E2E_RESULT.json'),status=result['status'],execution_extent='PARTIAL' if partial else 'ALL_MAIN_STAGES_RECORDED',actual_stage_names=executed,actual_C5_status=c5_status,actual_photos_count=photo_count,historical_planned_included_stages=result.get('included_stages'),planned_list_is_not_execution_evidence=True,elapsed_s=result.get('elapsed_s'),input_sha256=result.get('input_sha256'),checks_path=str(checks_path),checks_sha256=digest(checks_path) if checks_path.is_file() else None,gate_statuses={k:v.get('status') for k,v in checks.items() if k in ('C1','C2','C3','C4','C5') and isinstance(v,dict)}))
    return entries,selection


def pack(args):
    from report_batch_legacy import building_id
    bid=building_id(args.building);root=args.project_root;house=args.house_directory
    evidence=pack_admission(root,bid,house,args.cohort_plan);cohort_plan=read(args.cohort_plan);cohort_ref=dict(path=str(args.cohort_plan),sha256=digest(args.cohort_plan));delivery=cohort_plan['delivery']
    if args.cpu_core is not None and hasattr(os,'sched_setaffinity'):os.sched_setaffinity(0,{args.cpu_core})
    if Path('/proc/self/oom_score_adj').is_file():
        Path('/proc/self/oom_score_adj').write_text('1000')
        if int(Path('/proc/self/oom_score_adj').read_text(encoding='utf-8'))!=1000:raise ValueError('Pack oom_score_adj readback differs')
    os.environ.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PXR_WORK_THREAD_LIMIT='1')
    package=args.package_dir or house/'collision_package'
    if not package.is_dir() or not (package/'collision_only.usdc').is_file():raise ValueError('Actual collision package is missing')
    out=house/'delivery';out.mkdir(exist_ok=True);token=stamp();archive=out/(bid+'_'+house.name+'_pack_v7_'+token+'.zip');temp=archive.with_suffix('.part.zip');packaging_sha=digest(Path(__file__))
    previous_receipt=read(out/'DELIVERY_RECEIPT.json') if (out/'DELIVERY_RECEIPT.json').is_file() else None
    if previous_receipt and (previous_receipt.get('status')!='PACKED' or previous_receipt.get('building')!=bid):raise ValueError('Previous pack receipt is not complete/current building')
    previous_manifest=None
    if previous_receipt:
        if digest(previous_receipt['zip_path'])!=previous_receipt['zip_sha256']:raise ValueError('Previous archive changed')
        with zipfile.ZipFile(previous_receipt['zip_path']) as prior:previous_manifest=json.loads(prior.read('DELIVERY_MANIFEST.json'))
    protected=lambda n:n.startswith(('collision_package/','photos/')) or n in ('CHECKS.json','STATUS.json','E2E_RESULT.json')
    previous_payload={r['archive_path']:r['sha256'] for r in previous_manifest['files'] if protected(r['archive_path'])} if previous_manifest else None
    entries=[(args.cohort_plan,'receipts/active/ROUND4_PLAN.json')]
    for folder,prefix in ((package,'collision_package'),(house/'photos','photos')):
        for current,dirs,names in os.walk(folder,followlinks=False):
            for name in sorted(names):
                path=Path(current)/name
                if path.is_file():entries.append((path,str(Path(prefix)/path.relative_to(folder)).replace('\\','/')))
    for name in ('CHECKS.json','STATUS.json','E2E_RESULT.json'):entries.append((house/name,name))
    clean=Path(evidence['status'].get('clean_directory',house))
    if not clean.is_absolute():clean=house/clean
    if not clean.resolve().is_relative_to(house.resolve()):raise ValueError('Current clean receipt directory escapes this batch house')
    receipt_folders=[(house/'execution','execution'),(house/'receipts','receipts')]
    if clean.resolve()!=house.resolve():receipt_folders.append((clean/'execution','clean_execution'))
    for folder,prefix in receipt_folders:
        if folder.is_dir():
            for path in sorted(folder.rglob('*.json')):entries.append((path,'receipts/'+prefix+'/'+path.relative_to(folder).as_posix()))
    for name in ('COLLISION_MANIFEST.json','ASSEMBLY.json','E2E_START.json'):
        if (house/name).is_file():entries.append((house/name,'receipts/'+name))
    if clean.resolve()!=house.resolve():
        for name in ('COLLISION_MANIFEST.json','ASSEMBLY.json','CLEAN_E2E_START.json','CLEAN_E2E_RESULT.json'):
            if (clean/name).is_file():entries.append((clean/name,'receipts/clean/'+name))
    for name in ('CHECK_RESULT.json','CHECKS.json','SPAWN_POINTS.json'):
        path=clean/'checks'/name
        if not path.is_file():raise ValueError('Missing actual final checks evidence '+str(path))
        entries.append((path,'receipts/clean/checks/'+name))
    active_entries,selection=actual_evidence_entries(house,clean,evidence);entries+=active_entries
    sdk_ref=evidence['status']['SDK_peak_RSS'];sdk=read(verified_reference(sdk_ref,'Per-house SDK RSS'))
    entries.append((Path(sdk_ref['path']),'receipts/resource/SDK_RSS.json'))
    for i,row in enumerate(sdk['instances']):
        source=verified_reference(row,'SDK actual exit/RSS');entries.append((source,f'receipts/resource/SDK_EXIT_{i:02d}.json'))
        if row.get('SESSION'):
            session=verified_reference(row['SESSION'],'SDK final SESSION');entries.append((session,f'receipts/resource/SESSION_{i:02d}.json'))
    worker=Path(evidence['status']['resident_session']).parent
    for label,source in [('WORKER_OWNER',worker/'OWNER.json'),('WORKER_PRIORITY',worker/'OWNERSHIP_PRIORITY_JOURNAL.json')]:
        snapshot=out/(label+'_'+token+'.json');snapshot.write_bytes(source.read_bytes());entries.append((snapshot,'receipts/resource/'+label+'.json'))
    wowner=read(worker/'OWNER.json');supervisor=Path(wowner['supervisor_run'])/'OWNER.json'
    entries.append((supervisor,'receipts/resource/SUPERVISOR_OWNER.json'))
    state=house/'RESOURCE_RETRY_STATE.json'
    if state.exists():
        snapshot=out/('RESOURCE_RETRY_STATE_'+token+'.json');snapshot.write_bytes(state.read_bytes());entries.append((snapshot,'receipts/resource/RESOURCE_RETRY_STATE_AT_PACK.json'))
    priority=out/('PACK_PRIORITY_'+token+'.json')
    priority_doc=dict(pid=os.getpid(),birth=Path('/proc/self/stat').read_text(encoding='utf-8').rsplit(') ',1)[1].split()[19],oom_score_adj_readback=int(Path('/proc/self/oom_score_adj').read_text(encoding='utf-8')))
    priority.write_text(json.dumps(priority_doc))
    entries.append((priority,'receipts/resource/PACK_PRIORITY.json'))
    selection['SDK_peak_RSS']=sdk_ref

    if previous_payload is not None and {name for _,name in entries if protected(name)}!=set(previous_payload):raise ValueError('Protected payload file set differs from preceding archive')
    expected_sha=dict(previous_payload or {});expected_sha['receipts/active/ROUND4_PLAN.json']=cohort_ref['sha256']
    expected_sha.update({r['archive_path']:r['expected_sha256'] for r in selection['active_bindings']})
    expected_sha.update({r['archive_path']:r['sha256'] for r in selection['face_roles']})
    expected_sha.update({r['archive_path']:r['sha256'] for r in selection['native_c5_bindings']})
    expected_sha['receipts/active/CONFIG.json']=selection['config']['sha256']
    expected_sha.update({r['archive_path']:r['sha256'] for r in selection['repair_references']})
    if selection.get('tier_selection'):expected_sha['receipts/active/TIER_SELECTION.json']=selection['tier_selection']['sha256']
    previous_archive=dict(path=previous_receipt.get('versioned_archive_path',previous_receipt['zip_path']),sha256=previous_receipt['zip_sha256'],receipt_path=str(out/('DELIVERY_RECEIPT.before_'+token+'.json')),receipt_sha256=digest(out/'DELIVERY_RECEIPT.json')) if previous_receipt else None
    selection['unchanged_payload_bound_to_previous_archive']=previous_archive
    if len({name for _,name in entries})!=len(entries):raise ValueError('Duplicate ZIP paths')
    manifest=[];began=time.monotonic()
    try:
        with zipfile.ZipFile(temp,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=args.compresslevel,allowZip64=True) as target:
            for path,name in entries:
                before=path.stat();hasher=hashlib.sha256()
                with path.open('rb') as source,target.open(name,'w',force_zip64=True) as dest:
                    for block in iter(lambda:source.read(4*1024**2),b''):hasher.update(block);dest.write(block)
                after=path.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed during packaging: '+str(path))
                if name in expected_sha and hasher.hexdigest()!=expected_sha[name]:raise ValueError('Protected or bound evidence bytes differ: '+name)
                manifest.append(dict(archive_path=name,source_path=str(path),bytes=after.st_size,sha256=hasher.hexdigest()))
            selection_bytes=(json.dumps(selection,indent=2,ensure_ascii=False)+'\n').encode('utf-8');selection_name='receipts/ACTIVE_EVIDENCE_INDEX.json'
            target.writestr(selection_name,selection_bytes)
            manifest.append(dict(archive_path=selection_name,source_path=None,generated_from='SHA-bound actual run metadata; no payload modification',bytes=len(selection_bytes),sha256=hashlib.sha256(selection_bytes).hexdigest()))
            target.writestr('DELIVERY_MANIFEST.json',json.dumps(dict(building=bid,batch=house.name,phase=evidence['status'].get('phase'),layout_tier=evidence['status'].get('layout_tier'),source_directory=str(house),created_utc=utc(),files=manifest,cohort_plan=cohort_ref,delivery=delivery,
                config=evidence['status']['config'],config_sha256=evidence['config_sha256'],source_tool_shas=evidence['source_tool_shas'],packaging_tool_sha256=packaging_sha,packaging_tool='deliver_batch_legacy.py',previous_archive=previous_archive,E2E_elapsed_s=evidence['elapsed_s']),indent=2))
        os.replace(temp,archive)
        archive_stat=archive.stat();archive_sha=digest(archive)
        from archive_review_legacy import review_archive
        review=review_archive(root,house,args.cohort_plan,archive,verified_archive_sha=archive_sha,output_directory=out/('review_'+token))
        if review.get('status')!='PASS' or review.get('zip_sha256')!=archive_sha:raise ValueError('Actual pre-release remote archive review failed')
        review_path=out/('ARCHIVE_REVIEW_'+token+'.json');atomic_json(review_path,review)
        review_reference=dict(path=str(review_path),sha256=digest(review_path))
        release=Path(delivery['remote_directory'])/(bid+'.zip');release.parent.mkdir(parents=True,exist_ok=True)
        previous_release=None
        if release.exists():
            before=digest(release);saved=preserve(release,expected_sha=before)
            previous_release=dict(path=str(saved),sha256=before,preserved_utc=utc(),is_old001_pilot=(bid=='001' and before==OLD001_PILOT_SHA),path_is_time_specific_snapshot=True)
        release_temp=release.with_name('.'+bid+'_'+token+'.part.zip')
        try:os.link(archive,release_temp)
        except OSError:
            shutil.copy2(archive,release_temp)
            if digest(release_temp)!=archive_sha:raise ValueError('Round4 release copy differs from versioned archive')
        else:
            linked_stat=release_temp.stat()
            if (linked_stat.st_dev,linked_stat.st_ino)!=(archive_stat.st_dev,archive_stat.st_ino):raise ValueError('Round4 release hardlink does not identify the source archive')
        current_stat=archive.stat()
        if (current_stat.st_dev,current_stat.st_ino,current_stat.st_size,current_stat.st_mtime_ns)!=(archive_stat.st_dev,archive_stat.st_ino,archive_stat.st_size,archive_stat.st_mtime_ns):raise ValueError('Source archive changed before Round4 release')
        os.replace(release_temp,release)
        receipt=dict(schema='Factory80Round4PackedDeliveryV7',status='PACKED',building=bid,batch=house.name,phase=evidence['status'].get('phase'),layout_tier=evidence['status'].get('layout_tier'),READY_V4=True,
            created_utc=utc(),zip_path=str(release),versioned_archive_path=str(archive),zip_sha256=archive_sha,zip_bytes=archive_stat.st_size,cohort_plan=cohort_ref,delivery=delivery,previous_release=previous_release,
            manifest_files=len(manifest),source_directory=str(house),config=evidence['status']['config'],config_sha256=evidence['config_sha256'],source_tool_shas=evidence['source_tool_shas'],packaging_tool_sha256=packaging_sha,packaging_tool='deliver_batch_legacy.py',previous_archive=previous_archive,
            source_metadata={name:digest(house/name) for name in ('CHECKS.json','STATUS.json','E2E_RESULT.json')},
            elapsed_s=time.monotonic()-began,CRC='PASS',verification=review_reference,remote_only=True,remote_delivery_status='SUCCESS',
            local_ZIP_download_performed=False,remote_receipt_path=str(out/'REMOTE_DELIVERY_RECEIPT.json'))
        receipt_path=out/'DELIVERY_RECEIPT.json'
        if receipt_path.is_file():shutil.copy2(receipt_path,out/('DELIVERY_RECEIPT.before_'+token+'.json'))
        atomic_json(receipt_path,receipt)
        remote_receipt=dict(receipt,schema='Factory80Round4RemoteDeliveryV7',status='SUCCESS',remote_packed_receipt=str(receipt_path))
        atomic_json(out/'REMOTE_DELIVERY_RECEIPT.json',remote_receipt)
        atomic_json(release.with_name(bid+'_DELIVERY_RECEIPT.json'),remote_receipt)
        sha_path=release.with_suffix('.sha256');sha_temp=sha_path.with_name('.'+sha_path.name+'.'+token+'.tmp')
        sha_temp.write_text(archive_sha+'  '+release.name+'\n',encoding='utf-8');os.replace(sha_temp,sha_path)
        print(json.dumps(dict(receipt=str(receipt_path),**receipt)))
    except BaseException as exc:
        atomic_json(out/('PACK_FAILED_'+token+'.json'),dict(status='FAILED',building=bid,error=repr(exc),temporary_archive=str(temp),elapsed_s=time.monotonic()-began))
        raise

def preserve(path,expected_sha=None):
    path=Path(path)
    if not path.exists():return None
    before=path.stat();expected_sha=expected_sha or digest(path);history=path.parent/'history';history.mkdir(parents=True,exist_ok=True)
    saved=history/(path.stem+'_pre_round4_'+stamp()+path.suffix)
    try:os.link(path,saved)
    except OSError:
        shutil.copy2(path,saved)
        if digest(saved)!=expected_sha:raise ValueError('Preserved historical archive copy differs')
    else:
        after=saved.stat()
        if (after.st_dev,after.st_ino)!=(before.st_dev,before.st_ino):raise ValueError('Historical archive hardlink differs')
    after=path.stat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('Previous canonical archive changed during preservation')
    return str(saved)


def main():
    ap=argparse.ArgumentParser(description=__doc__);subs=ap.add_subparsers(dest='operation',required=True)
    p=subs.add_parser('pack');p.add_argument('--project-root',type=Path,default=Path('/cephfs/shared/yixing/outputs/factory80_20261011'))
    p.add_argument('--cohort-plan',type=Path,required=True);p.add_argument('--building',required=True);p.add_argument('--house-directory',type=Path,required=True);p.add_argument('--package-dir',type=Path);p.add_argument('--compresslevel',type=int,choices=range(10),default=1);p.add_argument('--cpu-core',type=int,default=63)
    return pack(ap.parse_args())

if __name__=='__main__':raise SystemExit(main())
