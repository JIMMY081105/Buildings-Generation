"""AD98 admission proof and live OOM check; no external process enumeration."""
from pathlib import Path
import json,datetime
def dt(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
def assess(policy,samples,now,active,oom):
 now=dt(now)if isinstance(now,str)else now;reasons=[];cut=now-datetime.timedelta(seconds=600);rows=sorted(samples,key=lambda x:x['utc']);before=[x for x in rows if dt(x['utc'])<=cut]
 if now<dt(policy['not_before_utc']):reasons.append('BEFORE_1720_MINIMUM_RECOVERY_TIME')
 if oom!=policy['oom_baseline']:reasons.append('OOM_BASELINE_CHANGED_AWAIT_SUPERVISOR')
 if now<dt(policy['serial_until_utc'])and active:reasons.append('ONE_OWN_HEAVY_UNTIL_TWO_HOURS_STABLE')
 window=[]
 if before:
  first=before[-1];window=[x for x in rows if dt(x['utc'])>=dt(first['utc'])and dt(x['utc'])<=now]
 if len(window)<11 or not window or(dt(window[-1]['utc'])-dt(window[0]['utc'])).total_seconds()<600.:reasons.append('TEN_MINUTES_OF_MINUTELY_SAMPLES_NOT_COMPLETE')
 if window:
  times=[dt(x['utc'])for x in window]
  if(now-times[-1]).total_seconds()>65 or(cut-times[0]).total_seconds()>65 or any((b-a).total_seconds()>65 for a,b in zip(times,times[1:])):reasons.append('MINUTELY_SAMPLE_GAP_OR_STALE_PROOF')
  if any(x['oom_kill']!=policy['oom_baseline']for x in window):reasons.append('OOM_CHANGED_IN_REQUIRED_WINDOW')
  expected={int(k):str(v)for k,v in policy['protected_start_ticks'].items()}
  if any({x['pid']:x.get('start_ticks')for x in sample['protected']if x['alive']}!=expected for sample in window):reasons.append('PROTECTED_LONG_LIVED_IDENTITY_MISSING')
  lowest=float(window[0]['others_rss_conservative_proxy_gib']);growth=0.
  for sample in window:
   value=float(sample['others_rss_conservative_proxy_gib']);growth=max(growth,value-lowest);lowest=min(lowest,value)
  if growth>2.:reasons.append('OTHER_RSS_FORWARD_GROWTH_OVER2GIB')
 else:growth=None
 return {'admission_allowed':not reasons,'reasons':reasons,'oom_baseline':policy['oom_baseline'],'current_oom_kill':oom,'not_before_utc':policy['not_before_utc'],'serial_until_utc':policy['serial_until_utc'],'actual_own_heavy_count':len(active),'window_sample_count':len(window),'window_first_utc':window[0]['utc']if window else None,'window_last_utc':window[-1]['utc']if window else None,'maximum_actual_forward_other_RSS_growth_gib':growth,'window_other_RSS_samples':[{'utc':x['utc'],'others_rss_conservative_proxy_gib':x['others_rss_conservative_proxy_gib'],'container_total_rss_gib':x['container_total_rss_gib']}for x in window],'scope':'AD98 all conditions required for every new heavy admission. Stable-window proof uses cgroup total minus explicitly owned anonymous groups; retains method/actual samples. No threshold relaxation.'}
def oom_count():
 return int(dict(x.split()for x in Path('/sys/fs/cgroup/memory/memory.oom_control').read_text().splitlines())['oom_kill'])
def evaluate_for_guard(root,now,active):
 root=Path(root);p=root/'evidence/RESOURCE_AD98_POLICY.json'
 if not p.exists():return {'admission_allowed':True,'scope':'AD98_POLICY_NOT_APPLICABLE'}
 policy=json.loads(p.read_text());samples=[json.loads(x)for x in(root/'evidence/AD98_RESOURCE_SAMPLES1927.jsonl').read_text().splitlines()if x.strip()]
 from resource_sampler import sample
 current=sample();samples.append(current);return assess(policy,samples,current['utc'],active,oom_count())
def live_oom_kill(root):
 p=Path(root)/'evidence/RESOURCE_AD98_POLICY.json'
 if not p.exists():return None
 baseline=json.loads(p.read_text())['oom_baseline'];now=oom_count();return now if now>baseline else None
