"""Round4 current-cohort report;13 R-tier jobs and16 byte-frozen prior READY houses; remote-only delivery."""
from pathlib import Path
import resource_trial as ops
import argparse, copy, datetime, hashlib, json, math, os, re, statistics

DEFAULT_ROOT='/cephfs/shared/yixing/outputs/factory80_20261011'

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp.'+str(os.getpid()))
    temp.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    os.replace(temp,path)

def read_json(path,errors=None):
    path=Path(path)
    if not path.is_file():return None
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,UnicodeError,json.JSONDecodeError) as exc:
        if errors is not None:errors.append(dict(path=str(path),error=str(exc)))
        return None

def numeric(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def building_id(value):
    value=str(value)
    if not re.fullmatch(r'\d{3}',value):raise ValueError('Building ID must be exactly three digits')
    return value

def normalized_checks(doc):
    if not isinstance(doc,dict):return {}
    return doc.get('checks',doc)

def failed_rules(doc):
    if not isinstance(doc,dict):return []
    rules=doc.get('failed_rules')
    if isinstance(rules,list):return rules
    audit=doc.get('audit',{})
    return [r for r in audit.get('rules',[]) if isinstance(r,dict) and r.get('status') not in ('PASS',True)]

def machine_fraction(status,layout):
    explicit=status.get('ground_MACHINE_fraction') if 'ground_MACHINE_fraction' in status else (layout or {}).get('ground_MACHINE_fraction')
    if numeric(explicit):return dict(actual_ratio=explicit,threshold=status.get('ground_MACHINE_threshold',(layout or {}).get('ground_MACHINE_threshold')),raw=explicit)
    candidates=([explicit] if isinstance(explicit,dict) else [])+failed_rules(layout)+failed_rules(status)
    for row in candidates:
        if not isinstance(row,dict):continue
        name=row.get('rule',row.get('name','ground_MACHINE_fraction' if row is explicit else ''))
        if name!='ground_MACHINE_fraction':continue
        actual=next((row[k] for k in ('actual_fraction','actual_ratio','actual','value','ratio','fraction') if numeric(row.get(k))),None)
        threshold=next((row[k] for k in ('required_minimum','threshold','minimum','required','min_ratio') if numeric(row.get(k))),None)
        return dict(actual_ratio=actual,threshold=threshold,raw=row)
    return None

def machine_seed_summary(directory,errors):
    """Read ordinary-layout attempts; a type alternative reusing a seed is not another seed."""
    directory=Path(directory);records=[]
    paths=list(directory.glob('LAYOUT_ATTEMPT_*.json'))+list((directory/'layout').glob('LAYOUT_ATTEMPT_*.json'))
    paths+=list(directory.glob('layout_after_check_seed_*/LAYOUT_ATTEMPT_*.json'))
    for path in sorted(paths):
        if path.stem.endswith('_RECEIPT'):continue
        doc=read_json(path,errors)
        if not isinstance(doc,dict):continue
        match=re.match(r'LAYOUT_ATTEMPT_(\d+)',path.name)
        index=doc.get('layout_attempt',doc.get('layout_seed_index',int(match.group(1)) if match else None))
        if not isinstance(index,int) or index not in range(20):continue
        rule=next((r for r in doc.get('audit',{}).get('rules',[]) if isinstance(r,dict) and r.get('rule')=='ground_MACHINE_fraction'),None)
        fraction=machine_fraction({},dict(failed_rules=[rule])) if rule else machine_fraction({},doc)
        if fraction is not None:records.append(dict(seed_index=index,source=str(path),rule_status=(rule or {}).get('status'),**fraction))
    values=[r['actual_ratio'] for r in records if numeric(r['actual_ratio'])]
    failures=sorted({r['seed_index'] for r in records if r['rule_status']=='FAIL' or (numeric(r['actual_ratio']) and numeric(r['threshold']) and r['actual_ratio']<r['threshold'])})
    return dict(expected_seed_indices=list(range(20)),observed_seed_indices=sorted({r['seed_index'] for r in records}),
        ratio_min=min(values) if values else None,ratio_max=max(values) if values else None,
        failed_seed_indices=failures,records=records)


def bound_json(reference,errors):
    """Read a declared metadata reference; a mismatched binding is never current evidence."""
    if not reference:return None
    path=Path(reference['path'] if isinstance(reference,dict) else reference)
    if isinstance(reference,dict) and reference.get('sha256'):
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=reference['sha256']:
            errors.append(dict(path=str(path),error='Declared metadata SHA mismatch'));return None
    return read_json(path,errors)


def reference(path):
    path=Path(path)
    return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()) if path.is_file() else dict(path=str(path),pending=True)


PRIOR_REPORT_SHA='cae6ae65cb58d2be39d005bfc0a498ae2eb4a6c535d4003cacc632f14c806755'
ROUND4_BUILDINGS=('001','049','006','013','061','063','029','011','060','027','057','067','054')
RULE_TIERS=('R1','R2','R3')
BASELINE_LABEL='ORIGINAL_RULE_BASELINE'
REMOTE_DELIVERY_DIRECTORY=Path('/cephfs/shared/yixing/deliveries/Factory80_v4')


def load_cohort_plan(root,path):
    root=Path(root);plan_path=Path(path);plan=read_json(plan_path)
    if not plan or plan.get('schema')!='Factory80Round4PlanV1' or plan.get('status')!='READY' or plan.get('ready') is not True:raise ValueError('Round4 immutable READY plan required')
    prior_ref=plan.get('prior_cohort_report',{})
    if prior_ref.get('sha256')!=PRIOR_REPORT_SHA:raise ValueError('Round4 prior report differs from authorized frozen16 snapshot')
    errors=[];prior=bound_json(prior_ref,errors)
    if errors or not prior:raise ValueError('Frozen prior cohort report missing or changed')
    old=prior.get('houses',[]);by_id={h['building']:h for h in old}
    master=read_json(root/'pipeline/v4/V4_BUILDING_PLAN.json') or {};master_ids={p['building'] for p in master.get('buildings',[])}
    if len(old)!=29 or len(by_id)!=29 or set(by_id)!=master_ids or sum(h['category']=='READY' for h in old)!=16 or sum(h['category']=='EXCLUDED' for h in old)!=13:raise ValueError('Prior current29 must exactly preserve16 READY and13 EXCLUDED')
    jobs=plan.get('jobs',[]);ids=[j.get('building') for j in jobs]
    if len(ids)!=13 or len(set(ids))!=13 or set(ids)!=set(ROUND4_BUILDINGS) or set(ids)!={h['building'] for h in old if h['category']=='EXCLUDED'}:raise ValueError('Round4 must target exactly the13 authorized prior excluded houses')
    if len({j.get('job_id') for j in jobs})!=13 or any(not j.get('job_id') for j in jobs):raise ValueError('Round4 job ids must be unique and nonempty')
    for job in jobs:
        bid=building_id(job['building']);expected=root/'runs'/bid/'v4/round4_v1'
        if job.get('phase')!='round4' or Path(job['house_directory']).resolve()!=expected.resolve():raise ValueError('Round4 explicit phase/directory mismatch '+bid)
        selection=job.get('tier_selection') or {}
        if Path(selection.get('path','')).resolve()!=(expected/'layout_tiers_v1/TIER_SELECTION.json').resolve():raise ValueError('Round4 house requires its own R-tier selection '+bid)
    retained_remote={}
    for ref in plan.get('source_bindings',[]):
        path=Path(ref['path'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=ref.get('sha256'):raise ValueError('Round4 frozen source differs '+str(path))
        if path.suffix.lower()=='.json':
            doc=read_json(path)
            if isinstance(doc,dict) and doc.get('schema')=='Factory80RemoteOnlyCloseoutDeliveryV1' and doc.get('status')=='SUCCESS':
                bid=doc.get('building');old_house=by_id.get(bid,{})
                if old_house.get('category')!='READY' or Path(doc.get('source_pack_receipt','')).parent.parent.resolve()!=Path(old_house['directory']).resolve():raise ValueError('Historical remote receipt differs from frozen READY source')
                retained_remote[bid]=dict(reference=ref,receipt=doc,scope='HISTORICAL_BOUND_RECEIPT_NOT_NEW_ZIP_VERIFICATION')
    delivery=plan.get('delivery',{})
    if delivery.get('mode')!='REMOTE_ONLY' or Path(delivery.get('remote_directory','')).resolve()!=REMOTE_DELIVERY_DIRECTORY.resolve():raise ValueError('Round4 delivery must use authorized remote-only directory')
    plan['_prior_report']=prior;plan['_reference']=reference(plan_path);plan['_retained_remote_deliveries']=retained_remote
    return plan


def current_cohort(root,bid,cohort_plan):
    """Replace only13 excluded houses; frozen prior16 retain their actual source."""
    root=Path(root);bid=building_id(bid)
    prior=cohort_plan.get('_prior_report') or bound_json(cohort_plan['prior_cohort_report'],[])
    old=next(h for h in prior['houses'] if h['building']==bid)
    job=next((j for j in cohort_plan['jobs'] if j['building']==bid),None)
    if job:
        return dict(kind='LAYOUT_TIERS',directory=job['house_directory'],job=job,tier_selection=job['tier_selection'],
            historic_status=reference(Path(old['directory'])/'STATUS.json'),historic_status_value=old.get('status',{}),
            prior_house=old,round='ROUND4',phase_plan_reference=cohort_plan['_reference'],layout_plan_reference=cohort_plan.get('layout_execution_plan'))
    if old['category']!='READY':raise ValueError('Only frozen prior READY may be inherited '+bid)
    return dict(kind='PRIOR_FROZEN_READY',directory=old['directory'],job={},tier_selection=None,prior_house=old,round='PRIOR_FROZEN',retained_remote_delivery=cohort_plan.get('_retained_remote_deliveries',{}).get(bid))


def inherited_house(cohort,prior_ref):
    h=copy.deepcopy(cohort['prior_house']);h['prior_cohort']=h.get('cohort');h['cohort']='PRIOR_FROZEN_READY'
    h['current_round']='PRIOR_FROZEN';h['frozen_prior_report']=dict(prior_ref)
    h['source_reuse_scope']='Exact prior READY evidence; no new physics, pack or ZIP verification'
    if cohort.get('retained_remote_delivery'):
        h['retained_remote_delivery']=copy.deepcopy(cohort['retained_remote_delivery']);h['remote_delivered']=True
    return h


def per_house_sdk(status,e2e,checks,photos,errors):
    ref=status.get('SDK_peak_RSS');doc=bound_json(ref,errors) if ref else None
    valid=False;chosen=None
    if isinstance(doc,dict) and doc.get('schema')=='Factory80PerHouseSDKRSSV1' and doc.get('building')==status.get('building') and doc.get('scope')=='FRESH_SDK_PER_HOUSE_NO_CROSS_HOUSE_REUSE':
        all_bound=True
        for row in doc.get('instances',[]):
            raw=bound_json(row,errors)
            if not raw or raw.get('building')!=status.get('building') or raw.get('oom_score_adj_readback')!=1000 or not raw.get('no_cross_house_reuse') or not numeric(raw.get('actual_peak_RSS_bytes')) or not raw.get('wait4'):
                all_bound=False;continue
            if (checks.get('C5',{}).get('resident_session') or {}).get('pid')==raw.get('pid') and str((checks.get('C5',{}).get('resident_session') or {}).get('birth'))==str(raw.get('birth')):
                chosen=raw
        photo_session=(photos or {}).get('resident_session',{})
        valid=bool(all_bound and chosen and chosen.get('actual_exit_code')==0 and photo_session.get('pid')==chosen.get('pid') and str(photo_session.get('birth'))==str(chosen.get('birth')) and (e2e or {}).get('SDK_peak_RSS')==ref)
    return dict(valid=valid,summary=doc,reference=ref,chosen=chosen,actual_peak_RSS_bytes=(doc or {}).get('actual_peak_RSS_bytes'),instances=(doc or {}).get('instances',[]))

def house_evidence(root,bid,house_directory=None,cohort=None):
    """Read the explicit current house, not an old READY/exclusion alias."""
    bid=building_id(bid);directory=Path(house_directory or Path(root)/'runs'/bid/'v4/batch_v1');errors=[]
    allowed=Path(root)/'runs'/bid/'v4'
    if directory.resolve()==allowed.resolve() or not directory.resolve().is_relative_to(allowed.resolve()):raise ValueError('House directory outside named building')
    cohort=cohort or dict(kind='ORIGINAL_BATCH' if directory.name=='batch_v1' else 'EXPLICIT_HOUSE',job={})
    status=read_json(directory/'STATUS.json',errors) or {}
    checks_doc=read_json(directory/'CHECKS.json',errors);checks=normalized_checks(checks_doc)
    e2e=read_json(directory/'E2E_RESULT.json',errors)
    binding_errors=[]
    selection_ref=cohort.get('tier_selection')
    if status.get('tier_selection'):
        declared=status['tier_selection']
        if not selection_ref or Path(declared['path']).resolve()!=Path(selection_ref['path']).resolve():
            binding_errors.append(dict(error='STATUS tier selection path differs from current cohort'))
        else:selection_ref=declared
    selection=bound_json(selection_ref,binding_errors)
    if selection and selection.get('building')!=bid:
        binding_errors.append(dict(error='Tier selection building mismatch'));selection=None
    tier_evidence=[]
    for row in (selection or {}).get('tiers',[]):
        pre=bound_json(row.get('precompute_result'),binding_errors)
        cfg=bound_json(row.get('config'),binding_errors)
        valid=bool(pre and isinstance(cfg,dict) and pre.get('layout_tier')==row.get('tier') and pre.get('config')==row.get('config')
            and cfg.get('v4',{}).get('layout_tier')==row.get('tier') and cfg.get('v4',{}).get('layout_threshold_profile')==row.get('tier'))
        tier_evidence.append(dict(tier=row.get('tier'),valid_binding=valid,precompute_result=row.get('precompute_result'),config=row.get('config'),
            actual_status=(pre or {}).get('status'),layout_seed_indices_used=(pre or {}).get('layout_seed_indices_used',[]),failed_rules=failed_rules(pre),
            ground_machine_seed_summary=machine_seed_summary(row.get('layout_directory') or Path(row['precompute_result']['path']).parent,errors) if pre and row.get('precompute_result') else None))
    layout_ref=cohort.get('job',{}).get('precompute_result')
    tier=cohort['kind']
    if cohort['kind']=='LAYOUT_TIERS':
        tier='PENDING'
        if selection and selection.get('selected_tier') in RULE_TIERS:
            tier=selection['selected_tier'];layout_ref=selection.get('selected_precompute_result')
        elif selection and selection.get('status')=='EXCLUDED_LAYOUT':
            tiers=selection.get('tiers',[])
            if selection.get('selected_tier') is None and selection.get('final_tier')=='R3' and len(tiers)==3 and [r.get('tier') for r in tiers]==list(RULE_TIERS) and all(r.get('status') in ('LAYOUT_FAIL','EXCLUDED_LAYOUT') for r in tiers) and all(r['valid_binding'] and r['actual_status'] in ('LAYOUT_FAIL','EXCLUDED_LAYOUT') and r['layout_seed_indices_used']==list(range(20)) for r in tier_evidence):
                tier='ALL_FAILED';layout_ref=selection.get('final_precompute_result')
            else:binding_errors.append(dict(error='Incomplete three-tier failure evidence'))
    layout=bound_json(layout_ref,binding_errors) if layout_ref else read_json(directory/'layout/PRECOMPUTE_RESULT.json',errors)
    if cohort['kind']=='LAYOUT_TIERS' and tier in RULE_TIERS:
        expected=list(RULE_TIERS)[:int(tier[1])]
        strictest=([r['tier'] for r in tier_evidence]==expected and all(r['valid_binding'] for r in tier_evidence)
            and all(r['actual_status'] in ('LAYOUT_FAIL','EXCLUDED_LAYOUT') and r['layout_seed_indices_used']==list(range(20)) for r in tier_evidence[:-1])
            and bool(tier_evidence) and tier_evidence[-1]['actual_status']=='LAYOUT_PASS')
        indices=(layout or {}).get('layout_seed_indices_used',[])
        seed_budget_ok=bool(1<=len(indices)<=20 and indices==list(range(len(indices))) and (layout or {}).get('layout_seed_index')==indices[-1] and (layout or {}).get('attempt_count',len(indices))==len(indices))
        if not strictest or not seed_budget_ok or not layout or layout.get('status')!='LAYOUT_PASS' or layout.get('layout_tier')!=tier or Path(layout_ref['path']).parent.resolve()!=Path(selection['selected_layout_directory']).resolve():
            binding_errors.append(dict(error='Selected tier/result/directory mismatch'));tier='PENDING'
    if tier=='ALL_FAILED' and (not layout or layout.get('status') not in ('LAYOUT_FAIL','EXCLUDED_LAYOUT')):
        tier='PENDING';binding_errors.append(dict(error='Final failed tier result missing or not byte-bound'))
    config=status.get('config') or cohort.get('job',{}).get('config') or (layout or {}).get('config')
    if not config and cohort['kind']=='ORIGINAL_BATCH':config=reference(Path(root)/'pipeline/v4/config_batch_v1.yaml')
    config_doc=bound_json(config,binding_errors) if config else None
    config_sha=(config or {}).get('sha256')
    photos=read_json(directory/'photos/PHOTOS.json',errors)
    gates={f'C{i}':checks.get(f'C{i}',{}).get('status','NOT_EXECUTED') if isinstance(checks.get(f'C{i}',{}),dict) else checks.get(f'C{i}','NOT_EXECUTED') for i in range(1,6)}
    names=photos.get('image_files',[]) if isinstance(photos,dict) else []
    files=[]
    for name in names:
        p=Path(name);files.append(p if p.is_absolute() else directory/'photos'/p)
    seven=bool(photos and photos.get('status')=='PASS' and len(files)==7 and len(set(map(str,files)))==7 and all(p.is_file() and p.stat().st_size>0 for p in files) and 'collision_gray.png' in [p.name for p in files])
    elapsed=e2e.get('elapsed_s') if isinstance(e2e,dict) else None
    start=(e2e or {}).get('monotonic_start_ns');end=(e2e or {}).get('monotonic_end_ns')
    actual_stages={r.get('name') for r in (e2e or {}).get('stages',[]) if isinstance(r,dict)}
    finished=bool(e2e and e2e.get('status')=='PASS' and e2e.get('clean_run') is True and numeric(elapsed) and 0<=elapsed<=3600 and numeric(start) and numeric(end) and elapsed==(end-start)/1e9 and {'assembly','voxel','C1_C4','C5','photos'}<=actual_stages and e2e.get('photos_count')==7)
    declared=status.get('READY_V4',status.get('READY')) is True
    identity_ok=status.get('building')==bid and (not status.get('house_directory') or Path(status['house_directory']).resolve()==directory.resolve())
    if declared and not identity_ok:errors.append(dict(error='READY metadata building differs from directory',actual=status.get('building'),expected=bid))
    config_ok=bool(config and config_doc is not None and config_sha==status.get('config_sha256')==(e2e or {}).get('config_sha256'))
    if cohort['kind']=='LAYOUT_TIERS':
        config_ok&=bool(tier in RULE_TIERS and status.get('layout_tier')==tier and config==(layout or {}).get('config'))
    source_ok=bool(status.get('source_tool_shas') and status['source_tool_shas']==(e2e or {}).get('source_tool_shas'))
    phase_ok=bool(status.get('phase')=='round4' and status.get('phase_plan')==cohort.get('phase_plan_reference'))
    sdk=per_house_sdk(status,e2e,checks,photos,errors)
    ready=bool(sdk['valid'] and declared and identity_ok and finished and seven and config_ok and source_ok and phase_ok and all(x=='PASS' for x in gates.values()))
    disposition=str(status.get('disposition',status.get('status','')))
    if not disposition and layout:disposition=str(layout.get('disposition',''))
    if tier=='ALL_FAILED' and not disposition:disposition='EXCLUDED_LAYOUT'
    excluded=disposition.startswith('EXCLUDED')
    resource_state=bound_json(status.get('resource_exclusion'),errors) if disposition=='EXCLUDED_RESOURCE' else read_json(directory/'RESOURCE_RETRY_STATE.json',errors)
    if disposition=='EXCLUDED_RESOURCE' and (not resource_state or not ops.retry_state_valid(resource_state)):
        excluded=False;errors.append(dict(error='Resource exclusion lacks3 confirmed kernel OOM attempts,600s delays or exact retry inputs'))
    current_evidence_valid=True;current_evidence_reasons=[]
    if cohort['kind']=='LAYOUT_TIERS':
        current_evidence_valid=bool(tier in (*RULE_TIERS,'ALL_FAILED') and not binding_errors and config_doc is not None and config==(layout or {}).get('config'))
        if status.get('config_sha256') is not None:current_evidence_valid&=config_sha==status['config_sha256']
        if (e2e or {}).get('config_sha256') is not None:current_evidence_valid&=config_sha==e2e['config_sha256']
        expected_tier='R3' if tier=='ALL_FAILED' else tier
        if status.get('layout_tier') is not None:current_evidence_valid&=status['layout_tier']==expected_tier
        if status and status.get('phase_plan')!=cohort.get('phase_plan_reference'):
            historical_layout_only=status.get('status') in ('LAYOUT_PASS_AWAITING_PHYSICS','EXCLUDED_LAYOUT','RUNNING') and e2e is None and status.get('phase_plan')==cohort.get('layout_plan_reference')
            if not historical_layout_only:current_evidence_valid=False
        if status and status.get('phase')!='round4':current_evidence_valid=False
        if not current_evidence_valid:
            ready=False;excluded=False
            current_evidence_reasons=[r['error'] for r in binding_errors]
            if not current_evidence_reasons:current_evidence_reasons=['Current tier/selection/config evidence is pending or inconsistent']
    errors.extend(binding_errors)
    if ready and excluded:
        errors.append(dict(error='Conflicting READY and EXCLUDED metadata'));ready=False;excluded=False
    category='READY' if ready else ('EXCLUDED' if excluded else 'PENDING')
    used=status.get('layout_seed_indices_used',(layout or {}).get('layout_seed_indices_used'))
    seeds=len(set(used)) if isinstance(used,list) else status.get('layout_seeds_tried')
    if seeds is None and layout:
        seeds=layout.get('seeds_tried',layout.get('seed_count'))
        if seeds is None and isinstance(layout.get('attempts'),list):seeds=len(layout['attempts'])
        if seeds is None and isinstance(layout.get('layout_seed_index'),int):seeds=layout['layout_seed_index']+1
    if seeds is None:
        indices={int(m.group(1)) for p in (directory/'layout').glob('LAYOUT_ATTEMPT_*.json') if not p.stem.endswith('_RECEIPT') and (m:=re.match(r'LAYOUT_ATTEMPT_(\d+)',p.name))}
        if indices:seeds=len(indices)
    fractions=machine_fraction(status,layout)
    ground_failure=excluded and (fractions is not None or 'ground_MACHINE_fraction' in json.dumps(failed_rules(status)+failed_rules(layout)))
    seed_summary=machine_seed_summary(directory,errors) if ground_failure else None
    delivery_receipt=read_json(directory/'delivery/REMOTE_DELIVERY_RECEIPT.json',errors)
    delivered=False
    if category=='READY' and delivery_receipt:
        expected=delivery_receipt.get('source_metadata',{});proof=bound_json(delivery_receipt.get('verification'),errors)
        target=REMOTE_DELIVERY_DIRECTORY/(bid+'.zip')
        delivered=bool(delivery_receipt.get('schema')=='Factory80Round4RemoteDeliveryV7' and delivery_receipt.get('status')=='SUCCESS'
            and delivery_receipt.get('building')==bid and delivery_receipt.get('source_directory')==str(directory)
            and delivery_receipt.get('phase')=='round4' and delivery_receipt.get('cohort_plan')==cohort.get('phase_plan_reference')
            and delivery_receipt.get('config_sha256')==config_sha and delivery_receipt.get('source_tool_shas')==status.get('source_tool_shas')
            and Path(delivery_receipt.get('zip_path','')).resolve()==target.resolve() and target.is_file()
            and target.stat().st_size==delivery_receipt.get('zip_bytes') and proof and proof.get('status')=='PASS'
            and proof.get('zip_sha256')==delivery_receipt.get('zip_sha256') and proof.get('actual_house_directory')==str(directory)
            and all(name in expected and (directory/name).is_file() and hashlib.sha256((directory/name).read_bytes()).hexdigest()==expected[name] for name in ('CHECKS.json','STATUS.json','E2E_RESULT.json')))
    return dict(building=bid,directory=str(directory),category=category,READY_V4=ready,disposition=disposition,
        cohort=cohort['kind'],layout_tier=tier,layout_threshold_profile=status.get('layout_threshold_profile',(layout or {}).get('layout_threshold_profile',config_doc.get('v4',{}).get('layout_threshold_profile') if config_doc else None)),tier_selection=selection,tier_evidence=tier_evidence,tier_selection_reference=reference(cohort['tier_selection']['path']) if cohort.get('tier_selection') else None,
        current_evidence_valid=current_evidence_valid,current_evidence_reasons=current_evidence_reasons,
        historic_status=cohort.get('historic_status'),historic_status_value=cohort.get('historic_status_value'),config=config,config_verified=config_ok,
        status=status,checks=checks_doc,gates=gates,e2e=e2e,elapsed_s=elapsed if numeric(elapsed) else None,
        photos_count=sum(p.is_file() and p.stat().st_size>0 for p in files),seven_actual_images=seven,
        layout=layout,layout_seed_count=seeds,ground_machine_failure=ground_failure,ground_machine_fraction=fractions,
        ground_machine_seed_summary=seed_summary,
        source_tool_shas=status.get('source_tool_shas',(e2e or {}).get('source_tool_shas',{})),
        config_sha256=status.get('config_sha256',(e2e or {}).get('config_sha256',config_sha)),
        SDK_peak_RSS_bytes=sdk['actual_peak_RSS_bytes'],SDK_instances=sdk['instances'],SDK_lifecycle_valid=sdk['valid'],resource_retry_state=resource_state,resource_retries_used=(resource_state or {}).get('resource_retries_used',0),remote_delivered=delivered,windows_delivered=False,delivery_receipt=delivery_receipt,read_errors=errors)

def summarize_actual_attempts(records):
    records=sorted(records,key=lambda r:r['monotonic_start_ns']);rows=[]
    for doc in records:
        names=[r['name'] for r in doc.get('stages',[]) if isinstance(r,dict) and 'name' in r]
        rows.append(dict(started_utc=doc.get('started_utc'),ended_utc=doc.get('ended_utc'),elapsed_s=doc['elapsed_s'],
            actual_stage_names=names,photos_count=doc.get('photos_count'),execution_extent='PARTIAL' if not {'C5','photos'}<=set(names) or not numeric(doc.get('photos_count')) or doc['photos_count']<7 else 'ALL_MAIN_STAGES_RECORDED',
            limit_s=doc.get('limit_s'),historical_included_stages_is_not_execution_evidence=True))
    first,last=records[0],records[-1]
    try:utc_wall=(datetime.datetime.fromisoformat(last['ended_utc'])-datetime.datetime.fromisoformat(first['started_utc'])).total_seconds()
    except (KeyError,ValueError,TypeError):utc_wall=None
    return dict(attempt_count=len(rows),additional_clean_attempts=max(len(rows)-1,0),attempts=rows,
        sum_actual_attempt_elapsed_s=sum(r['elapsed_s'] for r in rows),
        first_to_last_monotonic_wall_s=(last['monotonic_end_ns']-first['monotonic_start_ns'])/1e9,
        first_to_last_UTC_wall_s=utc_wall,cumulative_retry_time_is_not_individual_60min_limit=True)


def show(value):
    if value is None:return '未记录'
    return str(value).replace('|','/').replace('\n',' ')


def actual_timing(house):
    records=[read_json(p,house['read_errors']) for p in Path(house['directory']).glob('clean_*/CLEAN_E2E_RESULT.json')]
    records=[r for r in records if isinstance(r,dict) and numeric(r.get('elapsed_s')) and numeric(r.get('monotonic_start_ns')) and numeric(r.get('monotonic_end_ns'))]
    return summarize_actual_attempts(records) if records else None


def ready_table(plans,houses,field):
    labels=[BASELINE_LABEL,*RULE_TIERS];result={p[field]:{label:0 for label in labels} for p in plans}
    for planned,house in zip(plans,houses):
        if house['category']!='READY':continue
        value=house['status'].get(field,planned[field]);label=BASELINE_LABEL if house.get('current_round')=='PRIOR_FROZEN' else house['layout_tier']
        if label not in labels:raise ValueError('READY has no auditable current round/tier '+house['building'])
        result.setdefault(value,{key:0 for key in labels})[label]+=1
    return [dict(value=key,READY=sum(counts.values()),by_rule_tier=counts) for key,counts in sorted(result.items())]


def primary_ready_table(plans,houses):
    return [dict(primary_function=r['value'],READY=r['READY'],by_rule_tier=r['by_rule_tier']) for r in ready_table(plans,houses,'primary_function')]


def shape_ready_table(plans,houses):
    return [dict(mezzanine_shape=r['value'],READY=r['READY'],by_rule_tier=r['by_rule_tier']) for r in ready_table(plans,houses,'mezzanine_shape')]


def rule_ready_table(houses):
    labels=[BASELINE_LABEL,*RULE_TIERS]
    return [dict(rule_tier=label,READY=sum(h['category']=='READY' and (BASELINE_LABEL if h.get('current_round')=='PRIOR_FROZEN' else h['layout_tier'])==label for h in houses)) for label in labels]


def render_report(payload,plans):
    houses=payload['houses'];counts=payload['counts']
    head=['Factory80 v4 第四轮：仅13栋R规则重跑，旧16 READY的来源/文件冻结。',
        '更新时间 UTC '+payload['UTC']+'。',
        f"当前READY {counts['READY']}/29，EXCLUDED {counts['EXCLUDED']}/29，PENDING {counts['PENDING']}/29；本轮远端实际交付 {payload['new_remote_delivered']} 栋。",
        '13栋均按R1→R2→R3，每档最多20 seed，采用首个实际PASS的最严一档。',
        '最后失败档仅记录失败来源，ALL_FAILED不算采用R3。',
        'R规则配置/实际源码逐项SHA绑定；旧16按其原真实来源继承，不重跑不重验大包。',
        '物理C1–C5保留原门槛；READY仍需全PASS、7张原图和完整单clean计时。',
        '只有布局PASS进入按原grid顺序的物理流水线；未执行/绑定失效记PENDING。',
        '完整E2E取一次monotonic计时；PARTIAL、重试累计、打包分别记载。',
        '新ZIP仅远端Factory80_v4目录；旧同名包先保留history并记录SHA。',
        '旧Windows交付仅保留历史口径；第四轮不下载ZIP到C/F、不造Windows回执。',
        '每栋使用新SDK并退出；自有进程OOM优先级1000；准入8GiB和显存12GB。',
        '内核OOM为技术中断，同输入等待600秒后最多重试2次；每个SDK峰值RSS单独记录。',
        '无新增人工检查点，不做自动外观评分。']
    assert len(head)<=15
    text='\n'.join(head)+'\n\n| 楼号 | 状态 | 本轮/规则 | 主/次功能 | 夹层形状 | 材质 | 楼梯 | C1 | C2 | C3 | C4 | C5 | 实际计时范围 | seed数 | 本轮远端交付 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n'
    for planned,h in zip(plans,houses):
        status=h['status'];function=status.get('primary_function',planned['primary_function']);secondary=status.get('secondary_function',planned.get('secondary_function'))
        if secondary:function+=' / '+secondary
        timing='未完成'
        if h['category']=='READY':timing=f"完整E2E {h['elapsed_s']:.3f}s"
        elif h.get('actual_attempt_timing'):
            rows=h['actual_attempt_timing'];last=rows['attempts'][-1];timing=f"末次{last['execution_extent']} {last['elapsed_s']:.3f}s；{rows['attempt_count']}次累计{rows['sum_actual_attempt_elapsed_s']:.3f}s"
        elif h.get('layout') and numeric(h['layout'].get('elapsed_s')):timing=f"仅布局 {h['layout']['elapsed_s']:.3f}s；整栋未执行"
        category=h['category']
        if not h.get('current_evidence_valid',True):category+=' / 绑定待修复：'+'; '.join(h['current_evidence_reasons'])
        tier=BASELINE_LABEL+' / '+h['layout_tier'] if h.get('current_round')=='PRIOR_FROZEN' else h['layout_tier']
        design='（设计值，未物理执行）' if not status.get('clean_directory') and h['category']!='READY' else ''
        delivered=('既有远端SUCCESS（继承）' if h.get('retained_remote_delivery') else '继承旧交付口径') if h.get('current_round')=='PRIOR_FROZEN' else ('已交付' if h.get('remote_delivered') else '未交付')
        values=[h['building'],category,tier,function,status.get('mezzanine_shape',planned['mezzanine_shape'])+design,status.get('material',planned.get('material')),','.join(status.get('stair_kinds',planned.get('stair_kinds',[])))]+list(h['gates'].values())+[timing,h['layout_seed_count'],delivered]
        text+='| '+' | '.join(show(v) for v in values)+' |\n'
    for field,label,key in [('primary_function','主功能','ready_by_primary_function'),('mezzanine_shape','夹层形状','ready_by_mezzanine_shape')]:
        text+='\n'+label+' × READY：\n\n| '+label+' | READY | 原规则16基线 | R1 | R2 | R3 |\n|---|---|---|---|---|---|\n'
        for row in payload[key]:text+='| '+' | '.join(show(v) for v in [row[field],row['READY'],*row['by_rule_tier'].values()])+' |\n'
    text+='\n规则档位 × READY：\n\n| 规则组 | READY |\n|---|---:|\n'
    for row in payload['ready_by_rule_tier']:text+='| '+row['rule_tier']+' | '+str(row['READY'])+' |\n'
    text+='\n“原规则16基线”为继承组，不改写其各自既有V档/重检来源。\n'
    failed=[h for h in houses if h['layout_tier']=='ALL_FAILED']
    if failed:
        text+='\n三档实际全部失败的布局：\n\n| 楼号 | 最终规则失败实测 | 各档seed数 | 最终来源 |\n|---|---|---|---|\n'
        for h in failed:
            tiers=(h['tier_selection'] or {}).get('tiers',[])
            text+='| '+' | '.join(show(v) for v in [h['building'],failed_rules(h['layout']),{r['tier']:len(set(r.get('layout_seed_indices_used',[]))) for r in tiers},(h['tier_selection'] or {}).get('final_precompute_result')])+' |\n'
    text+='\n每栋SDK实际峰值与资源重试：\n\n| 楼号 | SDK峰值RSS GiB | 资源重试次数 | SDK实例数 | 资源排除证据 |\n|---|---:|---:|---:|---|\n'
    for h in houses:
        if h.get('current_round')!='ROUND4':continue
        peak=h.get('SDK_peak_RSS_bytes');proof=(h.get('status') or {}).get('resource_exclusion')
        text+='| '+' | '.join(show(v) for v in [h['building'],round(peak/1024**3,4) if numeric(peak) else None,h.get('resource_retries_used'),len(h.get('SDK_instances',[])),proof])+' |\n'
        if h.get('disposition')=='EXCLUDED_RESOURCE':
            for attempt in (h.get('resource_retry_state') or {}).get('attempts',[]):
                evidence=attempt['OOM_evidence'];text+='\n'+h['building']+' resource attempt '+str(attempt['resource_attempt'])+'：'+show(dict(attribution=evidence['attribution'],oom_kill_delta=evidence['cgroup_oom_kill_delta'],kernel_lines=[line for line in evidence['new_kernel_lines'] if 'oom-kill:' in line or 'Killed process' in line]))+'\n'
    return text


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project-root',type=Path,default=Path(DEFAULT_ROOT));ap.add_argument('--plan',type=Path)
    ap.add_argument('--cohort-plan',type=Path,required=True);ap.add_argument('--batch-config',type=Path)
    ap.add_argument('--house-directory',type=Path);ap.add_argument('--no-update-progress',action='store_true')
    args=ap.parse_args();root=args.project_root;D=root/'pipeline/v4'
    master_path=args.plan or D/'V4_BUILDING_PLAN.json';master=read_json(master_path)
    if not master:raise ValueError('Actual unchanged 29-building master required')
    plans=master['buildings']
    if len(plans)!=29 or len({p['building'] for p in plans})!=29:raise ValueError('Master must contain29 unique buildings')
    cohort_plan=load_cohort_plan(root,args.cohort_plan);houses=[]
    for planned in plans:
        cohort=current_cohort(root,planned['building'],cohort_plan)
        if cohort['kind']=='PRIOR_FROZEN_READY':h=inherited_house(cohort,cohort_plan['prior_cohort_report'])
        else:
            h=house_evidence(root,planned['building'],cohort['directory'],cohort);h['current_round']='ROUND4';h['actual_attempt_timing']=actual_timing(h)
        houses.append(h)
    if args.house_directory and args.house_directory.resolve() not in {Path(h['directory']).resolve() for h in houses if h['current_round']=='ROUND4'}:raise ValueError('Driver current house is not a new Round4 job')
    identities={}
    for h in houses:
        if h.get('current_round')!='ROUND4' or h.get('category')!='READY':continue
        raw=(h.get('checks') or {}).get('checks',h.get('checks') or {}).get('C5',{}).get('resident_session',{})
        identities.setdefault((raw.get('pid'),str(raw.get('birth'))),[]).append(h)
    for ident,duplicates in identities.items():
        if len(duplicates)>1:
            for h in duplicates:h.update(category='PENDING',READY_V4=False);h['read_errors'].append(dict(error='Cross-house SDK PID/birth reuse violates trial policy',identity=ident))
    counts={category:sum(h['category']==category for h in houses) for category in ('READY','EXCLUDED','PENDING')};now=utc()
    history=D/'report_history'/('round4_'+stamp());history.mkdir(parents=True,exist_ok=False)
    for src in (root/'REPORT.md',D/'PROGRESS.json',D/'ROUND4_REPORT.json',D/'ROUND4_PROGRESS.json'):
        if src.is_file():(history/src.name).write_bytes(src.read_bytes())
    payload=dict(schema='Factory80Round4ActualReportV1',UTC=now,active_batch='round4',batch_released=True,further_human_checkpoints=False,
        counts=counts,new_remote_delivered=sum(h.get('remote_delivered',False) for h in houses if h['current_round']=='ROUND4'),legacy_windows_delivered=sum(h.get('windows_delivered',False) for h in houses if h['current_round']=='PRIOR_FROZEN'),plan=reference(master_path),cohort_plan=reference(args.cohort_plan),
        prior_cohort_report=cohort_plan['prior_cohort_report'],reporting_tool_version=7,driver_active_config=reference(args.batch_config) if args.batch_config else None,
        source_bindings={h['building']:dict(directory=h['directory'],current_round=h['current_round'],config=h['config'],source_tool_shas=h['source_tool_shas'],historic_status=h.get('historic_status'),frozen_prior_report=h.get('frozen_prior_report')) for h in houses},
        houses=houses,ready_by_primary_function=primary_ready_table(plans,houses),ready_by_mezzanine_shape=shape_ready_table(plans,houses),ready_by_rule_tier=rule_ready_table(houses),
        previous_report_snapshot=str(history),old_pilot_ready_counted=False,old_READY_frozen_count=16,
        physical_thresholds_unchanged=dict(C2=.95,C4=.9,head_height_m=1.6),old_results_preserved=True)
    atomic_json(D/'ROUND4_REPORT.json',payload);text=render_report(payload,plans);(root/'REPORT.md').write_text(text,encoding='utf-8')
    with (root/'REPORT_HISTORY.md').open('a',encoding='utf-8') as stream:stream.write('\n\n'+text)
    if not args.no_update_progress:
        progress=read_json(D/'PROGRESS.json') or {};progress.update(UTC=now,status='ROUND4_RUNNING' if counts['PENDING'] else 'ROUND4_TERMINAL',active_batch='round4',batch_released=True,
            ready=counts['READY'],excluded=counts['EXCLUDED'],remaining=counts['PENDING'],new_remote_delivered=payload['new_remote_delivered'],legacy_windows_delivered=payload['legacy_windows_delivered'],round4_report=str(D/'ROUND4_REPORT.json'),
            ready_by_primary_function=payload['ready_by_primary_function'],ready_by_mezzanine_shape=payload['ready_by_mezzanine_shape'],ready_by_rule_tier=payload['ready_by_rule_tier'],further_human_checkpoints=False,old_pilot_ready_in_current_batch=False)
        atomic_json(D/'ROUND4_PROGRESS.json',progress);atomic_json(D/'PROGRESS.json',progress)
    print(json.dumps(dict(report=str(root/'REPORT.md'),counts=counts,new_remote_delivered=payload['new_remote_delivered'],legacy_windows_delivered=payload['legacy_windows_delivered'],round4_report=str(D/'ROUND4_REPORT.json'))))


if __name__=='__main__':main()
