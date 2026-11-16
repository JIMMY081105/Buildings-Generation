"""AD100: only proven small B tasks skip window proof; all current safety conditions stay mandatory."""
from pathlib import Path
import json,datetime,hashlib,math
from resource_admission import assess,oom_count,live_oom_kill
WINDOW_REASONS={'TEN_MINUTES_OF_MINUTELY_SAMPLES_NOT_COMPLETE','OTHER_RSS_FORWARD_GROWTH_OVER2GIB','MINUTELY_SAMPLE_GAP_OR_STALE_PROOF','PROTECTED_LONG_LIVED_IDENTITY_MISSING'}
def decide(base,kind,history):
 peak=history.get('history_peak_gib_for_exemption');small=kind=='blender_building'and peak is not None and math.isfinite(float(peak))and 0<float(peak)<3.
 result=dict(base);reasons=list(base.get('reasons',[]));skipped=[r for r in reasons if r in WINDOW_REASONS]if small else[];reasons=[r for r in reasons if r not in WINDOW_REASONS]if small else reasons
 result.update(reasons=reasons,admission_allowed=not reasons,AD100={'task_history':history,'known_small_B_strictly_below3':small,'stability_window_required':not small,'only_skipped_window_reasons':skipped,'class_peak_plus8_singleIsaac_GPU12000_live46_liveOOM05s_history_only_increases':'UNCHANGED_OUTER_GUARD_AND_LIVE_LOOP'})
 result['scope']='AD100 only proven small blender_building tasks may skip past-window requirements. CurrentOOM/protected identities explicitly checked for every task. Class peak+8 and all original outer/live controls unchanged; unknown or changed-source task is conservatively large.'
 return result

def command_identity(command):
 for i,arg in enumerate(command):
  if str(arg).endswith('.py'):return Path(arg).name,list(map(str,command[i+1:]))
 return None,None
def task_history(root,kind,command,source65):
 root=Path(root);entry,args=command_identity(command);out={'entry_script':entry,'arguments':args,'history_peak_gib_for_exemption':None,'family_maximum_observed_group_peak_gib':None,'same_current_source_and_arguments_history_found':False,'method':'Max actual whole-group observations of this entrypoint+arguments across all code versions including failed/interrupted and live records; exemption additionally needs an exact current payload source-set match. Unknown version/data is not presumed small.'}
 if kind!='blender_building'or entry is None:return out
 current=source65['all_python_files_sha256'];maximum=0.;matched=False;records=[]
 for suffix in['_RESULT.json','_RESOURCE.json']:
  for file in(root/'logs').glob('*'+suffix):
   try:
    row=json.loads(file.read_text());peak=row.get('peak_group_rss_gib',row.get('group_peak_gib'));oldentry,oldargs=command_identity(row.get('command',[]))
    if row.get('kind')!=kind or row.get('self_test22')or row.get('calibrate_factory')or peak is None or not math.isfinite(float(peak))or float(peak)<=0 or oldentry!=entry or oldargs!=args:continue
    peak=float(peak);maximum=max(maximum,peak);tag=row['tag'];binding=root/'runs'/tag/'SOURCE_BINDING.json';same=binding.is_file()and json.loads(binding.read_text())['script_sha256']==current;matched|=same;records.append({'tag':tag,'observed_group_peak_gib':peak,'current_payload_source_set_matches':same,'runtime_exit_code':row.get('runtime_exit_code')})
   except(OSError,KeyError,TypeError,ValueError):continue
 out.update(family_maximum_observed_group_peak_gib=maximum or None,same_current_source_and_arguments_history_found=matched,history_peak_gib_for_exemption=maximum if matched and maximum else None,actual_history_records=records)
 return out

def evaluate_for_guard(root,now,active,kind,command,source65):
 root=Path(root);p=root/'evidence/RESOURCE_AD98_POLICY.json'
 if not p.exists():return{'admission_allowed':False,'reasons':['AD100_REQUIRED_POLICY_MISSING'],'scope':'Fail closed; no small-task exemption without live policy and identity proof.'}
 policy=json.loads(p.read_text());path=root/'evidence/AD98_RESOURCE_SAMPLES1927.jsonl';samples=[json.loads(x)for x in path.read_text().splitlines()if x.strip()]if path.exists()else[]
 from resource_sampler import sample
 current=sample();samples.append(current);oom=oom_count();base=assess(policy,samples,current['utc'],active,oom);expected={int(k):str(v)for k,v in policy['protected_start_ticks'].items()};actual={int(x['pid']):str(x['start_ticks'])for x in current['protected']if x['alive']};base['AD100_actual_current_protected']=current['protected'];base['AD100_actual_current_oom_kill']=oom
 if actual!=expected:base['reasons'].append('AD100_CURRENT_PROTECTED_IDENTITY_MISSING_OR_CHANGED')
 if oom!=policy['oom_baseline']:base['reasons'].append('AD100_CURRENT_OOM_BASELINE_CHANGED_AWAIT_SUPERVISOR')
 return decide(base,kind,task_history(root,kind,command,source65))

SELFTESTS={'small056_unstable':('blender_building',.56,True,False),'large32_unstable':('blender_building',3.2,False,False),'isaac_unstable':('isaac_c5',.56,False,False),'unknownB_unstable':('blender_building',None,False,False),'smallOOM_unstable':('blender_building',.56,False,True),'smallProtected_unstable':('blender_building',.56,False,False)}
def selftest(root,kind,name,tag):
 expected_kind,peak,should_accept,oom_bad=SELFTESTS[name];prefix='AD100_POLICY_SELFTEST_'+name;assert kind==expected_kind and(tag==prefix or tag.startswith(prefix+'_V')and tag[len(prefix+'_V'):].isdigit())
 base={'admission_allowed':False,'reasons':['OTHER_RSS_FORWARD_GROWTH_OVER2GIB'],'maximum_actual_forward_other_RSS_growth_gib':3.5,'window_sample_count':12,'fixture_scope':'EXPLICIT_SIMULATED_UNSTABLE_RESOURCE_SNAPSHOT_SELFTEST_ONLY_NOT_REAL_RESOURCE_ADMISSION'}
 if oom_bad:base['reasons'].append('AD100_CURRENT_OOM_BASELINE_CHANGED_AWAIT_SUPERVISOR')
 if name=='smallProtected_unstable':base['reasons'].append('AD100_CURRENT_PROTECTED_IDENTITY_MISSING_OR_CHANGED')
 result=decide(base,kind,{'history_peak_gib_for_exemption':peak,'fixture_scope':'EXPLICIT_SELFTEST_HISTORY_ONLY_NEVER_WRITTEN_TO_RESOURCE_PEAKS'});assert result['admission_allowed']==should_accept;code=0 if result['admission_allowed']else 75
 record={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'tag':tag,'kind':kind,'status':'SELFTEST_POLICY_ACCEPTED'if code==0 else'SELFTEST_POLICY_REJECTED','exit_code':code,'runtime_exit_code':None,'scientific_process_started':False,'is_real_resource_admission':False,'actual_same_admission_predicate_result':result,'helper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'scope':'Actual guardian selftest branch and actual production predicate, simulated unstable snapshot. No scientific payload is ever started, no OOM/resource history counters altered.'};p=Path(root)/'logs'/(tag+'_RESULT.json');assert not p.exists();p.write_text(json.dumps(record,indent=2));print(json.dumps(record),flush=True);return code
