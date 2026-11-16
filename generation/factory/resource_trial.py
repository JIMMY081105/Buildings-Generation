"""Factory80 operational OOM evidence and ownership; no scientific predicates."""
from pathlib import Path
import ctypes, datetime, hashlib, json, os, re, signal, subprocess, time

VERSION = 'factory80_resource_trial@v1'
TOKEN_KEY = 'FACTORY80_OWNERSHIP_TOKEN'
CGROUP = Path('/sys/fs/cgroup/memory')

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp.' + str(os.getpid()))
    temp.write_text(json.dumps(value, indent=2) + '\n'); temp.replace(path)

def birth(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]

def priority(pid=None):
    pid = pid or os.getpid()
    path = Path(f'/proc/{pid}/oom_score_adj'); path.write_text('1000')
    actual = int(path.read_text())
    if actual != 1000: raise RuntimeError('Owned process oom_score_adj readback differs')
    return actual

def setup(cores=None):
    if cores is not None: os.sched_setaffinity(0, set(cores))
    priority()

def subreaper():
    libc=ctypes.CDLL(None,use_errno=True);value=ctypes.c_int()
    if libc.prctl(36,1,0,0,0)!=0 or libc.prctl(37,ctypes.byref(value),0,0,0)!=0 or value.value!=1:
        raise OSError(ctypes.get_errno(),'Cannot verify owned-child subreaper')
    return value.value

def fact(pid):
    p = Path('/proc') / str(pid); fields = (p/'stat').read_text().rsplit(') ', 1)[1].split()
    status = {r.split(':',1)[0]:r.split(':',1)[1].strip() for r in (p/'status').read_text().splitlines() if ':' in r}
    return dict(pid=pid, birth=fields[19], ppid=int(fields[1]), state=fields[0],
        oom_score_adj=int((p/'oom_score_adj').read_text()),
        namespace_pids=[int(x) for x in status.get('NSpid', str(pid)).split()],
        RSS_bytes=int(status.get('VmRSS','0 kB').split()[0])*1024,
        HWM_bytes=int(status.get('VmHWM','0 kB').split()[0])*1024)

def poll(process):
    """Capture kernel wait4 RSS even when an owned process was SIGKILLed."""
    if process.returncode is not None: return process.returncode
    try: pid, status, usage = os.wait4(process.pid, os.WNOHANG)
    except ChildProcessError:
        if process.returncode is None: raise RuntimeError('Owned child reaped without wait4 evidence')
        return process.returncode
    if pid:
        process.returncode = os.waitstatus_to_exitcode(status)
        process.factory80_wait4 = dict(raw_wait_status=status, actual_exit_code=process.returncode,
            peak_RSS_bytes=int(usage.ru_maxrss)*1024, user_s=usage.ru_utime, system_s=usage.ru_stime)
    return process.returncode

def memory():
    stat = {r.split()[0]:int(r.split()[1]) for r in (CGROUP/'memory.stat').read_text().splitlines()}
    oom = {r.split()[0]:int(r.split()[1]) for r in (CGROUP/'memory.oom_control').read_text().splitlines()}
    limit = int((CGROUP/'memory.limit_in_bytes').read_text())
    group = next(r.split(':',2)[2] for r in Path('/proc/self/cgroup').read_text().splitlines() if r.split(':',2)[1]=='memory')
    return dict(UTC=utc(), monotonic_ns=time.monotonic_ns(), cgroup=group, limit_bytes=limit,
        total_rss_bytes=stat['total_rss'], total_shmem_bytes=stat['total_shmem'],
        available_bytes=limit-stat['total_rss']-stat['total_shmem'], cache_subtracted=False,
        oom_kill=oom.get('oom_kill',0), under_oom=oom.get('under_oom'),
        failcnt=int((CGROUP/'memory.failcnt').read_text()))

def kernel():
    r = subprocess.run(['dmesg'], capture_output=True, text=True, timeout=20,
        preexec_fn=setup)
    times = [float(v) for v in re.findall(r'^\[\s*(\d+(?:\.\d+)?)\]', r.stdout, re.M)]
    return dict(UTC=utc(), exit_code=r.returncode, cursor=max(times,default=0.),
        text=r.stdout, stderr=r.stderr)

def classify_oom(before, after, killed, kernel_after):
    """A SIGKILL alone, old kernel lines, or another cgroup is not an OOM proof."""
    new=[]
    for line in kernel_after.get('text','').splitlines():
        m=re.match(r'^\[\s*(\d+(?:\.\d+)?)\]',line)
        if m and float(m.group(1)) > before['kernel_cursor']: new.append(line)
    text='\n'.join(new); group=before['memory']['cgroup']
    group_lines=[line for line in new if ('oom-kill:' in line and ('task_memcg='+group in line or 'oom_memcg='+group in line))]
    victims=[int(v) for v in re.findall(r'Killed process\s+(\d+)',text)]
    known={int(p) for row in killed for p in [row.get('pid'),*row.get('namespace_pids',[]),*row.get('GPU_host_pids',[])] if p is not None}
    own_sigkill=any(row.get('exit_code') == -9 for row in killed)
    matched=sorted(set(victims)&known)
    delta=after['oom_kill']-before['memory']['oom_kill']
    # Correlation is explicitly labelled when PID namespaces hide the host victim PID.
    confirmed=bool(own_sigkill and delta>0 and group_lines and victims and kernel_after.get('exit_code')==0)
    return dict(schema='Factory80KernelOOMEvidenceV1', status='KERNEL_OOM_CONFIRMED' if confirmed else 'NOT_CONFIRMED_OOM',
        confirmed=confirmed, attribution='OWNED_KERNEL_PID_MATCH' if matched else 'OWNED_SIGKILL_WITH_SAME_CGROUP_KERNEL_OOM' if confirmed else 'UNVERIFIED',
        killed_owned_processes=killed, kernel_victim_pids=victims, matched_owned_kernel_pids=matched,
        cgroup_oom_kill_delta=delta, cgroup=group, before=before, after=after,
        new_kernel_lines=new, kernel_read_exit_code=kernel_after.get('exit_code'),
        signal_without_kernel_evidence_is_not_OOM=True, scientific_FAIL=False, scientific_budget_charged=False)

def checkpoint():
    k=kernel()
    return dict(UTC=utc(), memory=memory(), kernel_cursor=k['cursor'], kernel_read_exit_code=k['exit_code'])

class Ownership:
    def __init__(self, token, output):
        if not token or len(token)<16: raise ValueError('Unique owned-process marker required')
        self.token=token; self.output=Path(output); self.records={}; self.last_ns=0
    def scan(self, force=False):
        if not force and time.monotonic_ns()-self.last_ns < 500_000_000: return
        needle=(TOKEN_KEY+'='+self.token).encode(); changed=False
        for p in Path('/proc').glob('[0-9]*'):
            matched_token=False
            try:
                if needle not in (p/'environ').read_bytes().split(b'\0'): continue
                matched_token=True
                row=fact(int(p.name))
                if row['state']=='Z': continue
                if row['oom_score_adj']!=1000:
                    priority(row['pid']); row=fact(row['pid'])
                key=str(row['pid'])+':'+row['birth']; old=self.records.get(key)
                if old is None:
                    row['first_verified_UTC']=utc(); self.records[key]=row; changed=True
                elif row['HWM_bytes']>old['HWM_bytes']:
                    old['HWM_bytes']=row['HWM_bytes']; changed=True
            except (FileNotFoundError,ProcessLookupError): continue
            except PermissionError:
                if matched_token: raise
                continue
        self.last_ns=time.monotonic_ns()
        if changed or force: atomic(self.output,dict(UTC=utc(),token=self.token,readback_required=1000,processes=list(self.records.values())))
    def terminate(self, exclude=()):
        """Only processes with this exact inherited marker and matching birth are ours."""
        self.scan(True); actions=[]
        for sig,timeout in [(signal.SIGTERM,20),(signal.SIGKILL,5)]:
            deadline=time.monotonic()+timeout
            while True:
                live=[]
                for row in self.records.values():
                    pid=row['pid']
                    if pid in exclude or pid==os.getpid(): continue
                    try:
                        now=fact(pid)
                        if now['birth']!=row['birth'] or now['state']=='Z': continue
                        needle=(TOKEN_KEY+'='+self.token).encode()
                        if needle not in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'): continue
                        live.append(pid)
                    except (FileNotFoundError,ProcessLookupError,PermissionError): continue
                if not live: return actions
                for pid in live:
                    try: os.kill(pid,sig); actions.append(dict(pid=pid,signal=int(sig),reason='OWNED_TECHNICAL_INTERRUPTION_CLEANUP'))
                    except ProcessLookupError: pass
                if time.monotonic()>=deadline: break
                time.sleep(.2); self.scan(True)
        raise RuntimeError('Owned children remain after bounded cleanup')

def retry_state_valid(state):
    attempts=state.get('attempts',[])
    if len(attempts)!=3: return False
    if [r.get('resource_attempt') for r in attempts]!=[0,1,2]: return False
    used_events=set();last_oom_count=None
    for i,r in enumerate(attempts):
        proof=r.get('OOM_evidence',{})
        try:
            replay=classify_oom(proof['before'],proof['after'],proof['killed_owned_processes'],
                dict(text='\n'.join(proof['new_kernel_lines']),exit_code=proof['kernel_read_exit_code']))
        except (KeyError,TypeError,ValueError): return False
        if not proof.get('confirmed') or not replay['confirmed']: return False
        event=tuple(line for line in proof['new_kernel_lines'] if 'Killed process' in line)
        if not event or event in used_events:return False
        used_events.add(event)
        if last_oom_count is not None and proof['before']['memory']['oom_kill']<last_oom_count:return False
        last_oom_count=proof['after']['oom_kill']
        if i:
            old=attempts[i-1]
            if r.get('started_monotonic_ns',0)-old.get('interrupted_monotonic_ns',10**30)<600_000_000_000: return False
            if r.get('retry_input_signature')!=old.get('frozen_input_signature'): return False
    return True
