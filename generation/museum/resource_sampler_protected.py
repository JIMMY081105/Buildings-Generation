"""Read only cgroup totals, Museum29-owned job identities and the two explicitly protected PIDs under AD104."""
from pathlib import Path
import json,os,time,datetime,importlib.util
ROOT=Path('/cephfs/shared/yixing/outputs/museum29_20261107');OUT=ROOT/'evidence/AD98_RESOURCE_SAMPLES1927.jsonl'
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def proc(pid):
 p=Path('/proc')/str(pid)/'stat'
 if not p.exists():return {'pid':pid,'alive':False}
 a=p.read_text().rsplit(')',1)[1].split();status=Path('/proc')/str(pid)/'status';fields=dict((line.split(':',1)[0],line.split(':',1)[1].strip())for line in status.read_text().splitlines()if':'in line);anon=float(fields.get('RssAnon','0 kB').split()[0])*1024/2**30;return {'pid':pid,'alive':a[0]!='Z','state':a[0],'start_ticks':a[19],'rss_gib':int(a[21])*os.sysconf('SC_PAGE_SIZE')/2**30,'anonymous_rss_gib':anon}
def owned_tree(root):
 pending=[root];seen=set();members=[]
 while pending:
  pid=pending.pop()
  if pid in seen:continue
  seen.add(pid);p=Path('/proc')/str(pid)/'stat'
  if not p.exists():continue
  a=p.read_text().rsplit(')',1)[1].split()
  if int(a[2])!=root:continue
  members.append(proc(pid));p=Path('/proc')/str(pid)/'task'/str(pid)/'children'
  if p.exists():pending.extend(map(int,p.read_text().split()))
 return members
def sample():
 spec=importlib.util.spec_from_file_location('museum_guard98',ROOT/'scripts/resource_guard.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g);active=g.running();owned=[];own_rss=0.
 for item in active:
  members=owned_tree(item['pid']);rss=sum(x.get('anonymous_rss_gib',0.)for x in members);own_rss+=rss;owned.append({'tag':item['tag'],'pid':item['pid'],'start_ticks':item['start_ticks'],'group_anonymous_rss_gib':rss})
 me=proc(os.getpid());total=g.total_rss()/2**30;oom=dict(line.split()for line in Path('/sys/fs/cgroup/memory/memory.oom_control').read_text().splitlines());count=int(oom['oom_kill']);return {'utc':utc(),'container_total_rss_gib':total,'own_live_heavy_jobs':owned,'own_science_group_anonymous_rss_gib':own_rss,'sampler_rss_gib':me['rss_gib'],'others_rss_conservative_proxy_gib':max(0.,total-own_rss-me['anonymous_rss_gib']),'oom_kill':count,'protected':[proc(pid)for pid in [24730,2456]],'method':'Cgroup total_rss minus anonymous RSS of explicitly Museum29-owned root/descendant processes in each exact owned group and this lightweight sampler. No enumeration or command access of other users processes. During the initial required no-own-heavy window this conservatively includes other load plus small unclassified SSH overhead; not individual-process attribution.'}
def main():
 assert OUT.exists();deadline=time.monotonic()
 while True:
  q=sample()
  with OUT.open('a',encoding='utf-8')as f:f.write(json.dumps(q)+'\n');f.flush()
  deadline+=60.;time.sleep(max(.01,deadline-time.monotonic()))
if __name__=='__main__':main()
