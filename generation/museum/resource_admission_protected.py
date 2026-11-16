"""AD104 retires only33227; remaining exact protected identities and every admission/live gate stay mandatory."""
from pathlib import Path
import json,datetime,hashlib
from resource_admission import assess,oom_count,live_oom_kill
from resource_admission_small_tasks import decide,task_history
REQUIRED104={24730:'2750967813',2456:'2553014350'}
def current_safety_reasons(policy,current,oom):
 expected={int(k):str(v)for k,v in policy['protected_start_ticks'].items()};actual={int(x['pid']):str(x['start_ticks'])for x in current['protected']if x['alive']};reasons=[]
 if expected!=REQUIRED104:reasons.append('AD104_PROTECTED_POLICY_NOT_EXACT_AUTHORIZED_TWO_IDENTITIES')
 if actual!=expected:reasons.append('AD104_CURRENT_PROTECTED_IDENTITY_MISSING_OR_CHANGED')
 if oom!=policy['oom_baseline']:reasons.append('AD104_CURRENT_OOM_BASELINE_CHANGED_AWAIT_SUPERVISOR')
 return reasons
def evaluate_for_guard(root,now,active,kind,command,source65):
 root=Path(root);p=root/'evidence/RESOURCE_AD98_POLICY.json'
 if not p.exists():return{'admission_allowed':False,'reasons':['AD104_REQUIRED_POLICY_MISSING']}
 policy=json.loads(p.read_text());file=root/'evidence/AD98_RESOURCE_SAMPLES1927.jsonl';samples=[json.loads(x)for x in file.read_text().splitlines()if x.strip()]if file.exists()else[]
 from resource_sampler_protected import sample
 current=sample();samples.append(current);oom=oom_count();base=assess(policy,samples,current['utc'],active,oom);base['reasons']+=current_safety_reasons(policy,current,oom);base.update(AD104_actual_current_protected=current['protected'],AD104_actual_current_oom_kill=oom,AD104_retired_identity={'pid':33227,'birth':'2787585610','reason':'Owner normal shutdown independently verified by supervisor AD104; no longer required alive, never restarted or signalled here.'});return decide(base,kind,task_history(root,kind,command,source65))
def selftest(root,kind,name,tag):
 assert kind=='blender_building'and name in['protected_ok','missing24730','missing2456']and tag.startswith('AD104_POLICY_SELFTEST_'+name+'_V');root=Path(root);policy=json.loads((root/'evidence/RESOURCE_AD98_POLICY.json').read_text());assert{int(k):str(v)for k,v in policy['protected_start_ticks'].items()}==REQUIRED104;now=datetime.datetime.now(datetime.timezone.utc);missing=None if name=='protected_ok'else int(name.removeprefix('missing'));protected=[{'pid':pid,'start_ticks':birth,'alive':pid!=missing}for pid,birth in REQUIRED104.items()];samples=[{'utc':(now-datetime.timedelta(seconds=600-60*i)).isoformat(),'oom_kill':policy['oom_baseline'],'protected':protected,'others_rss_conservative_proxy_gib':10.,'container_total_rss_gib':10.}for i in range(11)];base=assess(policy,samples,now,[],policy['oom_baseline']);base['reasons']+=current_safety_reasons(policy,samples[-1],policy['oom_baseline']);out=decide(base,kind,{'history_peak_gib_for_exemption':.56,'scope':'EXPLICIT_SIMULATED_SMALL_TASK_HISTORY_FOR_SELFTEST_ONLY'});expected=name=='protected_ok';assert out['admission_allowed']==expected;code=0 if expected else 75;record={'utc':now.isoformat(),'status':'SELFTEST_POLICY_ACCEPTED'if expected else'SELFTEST_POLICY_REJECTED','tag':tag,'kind':kind,'exit_code':code,'scientific_process_started':False,'is_real_resource_admission':False,'simulated_current_protected':protected,'actual_production_predicate_result':out,'scope':'Actual guardianCLI and same production current-identity and admission functions; clearly simulated stable resource snapshot and missing protected identity. Missing24730/2456 must reject even known smallB .56 which skips window-only reasons. No scientific payload/production counters touched.'};p=root/'logs'/(tag+'_RESULT.json');assert not p.exists();p.write_text(json.dumps(record,indent=2));print(json.dumps(record),flush=True);return code
