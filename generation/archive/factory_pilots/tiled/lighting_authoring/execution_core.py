"""OWN finite process/ref utilities; no simulator imports or package writes."""
import hashlib,json,math,os,re,signal,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
def stamp():return datetime.now(timezone.utc).isoformat()
def ref(path):
    p=Path(path).resolve();h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return {'path':str(p),'bytes':p.stat().st_size,'sha256':h.hexdigest()}
def checked(r):
    if ref(r['path'])!=r:raise ValueError('bound actual bytes changed: '+r['path'])
    return Path(r['path'])
def save(path,value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
def basename(path):return str(path).replace('\\','/').rsplit('/',1)[-1]
def interpreter_entry(path):
    # Preserve the venv entry path even when it is a symlink to a shared main
    # executable. Resolving that symlink changes Python's venv discovery.
    return str(Path(path).absolute())
def actual_query(argv,timeout=30):
    p=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:out,err=p.communicate(timeout=timeout);timeout_hit=False
    except subprocess.TimeoutExpired:
        p.kill();out,err=p.communicate();timeout_hit=True
    return {'argv':list(argv),'actual_pid':p.pid,'true_wait_completed':True,'actual_signed_returncode':p.returncode,
        'stdout':out.decode('utf-8',errors='replace'),'stderr':err.decode('utf-8',errors='replace'),'scheduling_timeout':timeout_hit}
def foreign_cpu_startups(ps_stdout,own_pid):
    hits=[]
    for line in ps_stdout.splitlines()[1:]:
        parts=line.split(None,5)
        if len(parts)!=6:continue
        pid,ppid,stat,elapsed,cpu,cmd=parts
        if not pid.isdigit() or int(pid)==own_pid or 'Z' in stat:continue
        executable=cmd.split()[0].lower();name=executable.rsplit('/',1)[-1]
        if name in ['kit','kit.exe'] or (('python' in name or 'isaac' in name) and any(s in cmd.lower() for s in
            ['isaacsim','isaaclab','inference.py','inference_agent.py','train_agent.py','render_','stage3_physics.py','stage3_zeroshot_routes.py'])):hits.append(line)
    return hits
def validate_idle_preflight(q,own_pid):
    if any(r['actual_signed_returncode']!=0 or r.get('scheduling_timeout') for r in q.values()):raise RuntimeError('resource query failed')
    if q['compute']['stdout'].strip():raise RuntimeError('actual GPU compute owners present or namespace unresolved')
    if foreign_cpu_startups(q['processes']['stdout'],own_pid):raise RuntimeError('actual foreign GPU/Isaac CPU-startup present')
    lines=[x for x in q['autohold']['stdout'].splitlines() if re.match(r'^pause:\s+',x)]
    if len(lines)!=1 or not re.fullmatch(r'pause:\s+SET\s*',lines[0]):raise RuntimeError('autohold pause must already be SET; no pause/resume mutation authorized here')
    gpu=[x for x in q['gpu']['stdout'].splitlines() if x.strip()]
    if not gpu or not any(x.split(',',1)[0].strip()=='0' and 'A40' in x for x in gpu):raise RuntimeError('actual GPU0 A40 identity missing')
    return True
def run_owned(argv,stdout,stderr,raw_path,env,cwd,wall_timeout_s=14400.,termination_grace_s=60.):
    """Signal/timeout handling affects only this Popen handle; signed wait first."""
    if not all(math.isfinite(x) and x>0 for x in [wall_timeout_s,termination_grace_s]):raise ValueError('owned scheduling bounds must be positive and finite')
    interrupts=[];actions=[];previous={};proc=None;code=None;wait_completed=False
    start=stamp();started=time.monotonic();stop_requested=None;error=None
    def caught(signum,frame):interrupts.append({'kind':'CAUGHT_OWN_PARENT_SIGNAL','signal':int(signum),'utc':stamp()})
    try:
        for sig in (signal.SIGINT,signal.SIGTERM):previous[sig]=signal.getsignal(sig);signal.signal(sig,caught)
        proc=subprocess.Popen(argv,stdout=stdout,stderr=stderr,env=env,cwd=cwd)
        while code is None:
            timeout=time.monotonic()-started>=wall_timeout_s
            if (interrupts or timeout) and stop_requested is None:
                stop_requested=time.monotonic();reason='OWN_PARENT_INTERRUPT' if interrupts else 'OWN_SCHEDULING_TIMEOUT'
                actions.append({'method':'owned Popen.terminate','actual_owned_child_pid':proc.pid,'reason':reason,'utc':stamp()});proc.terminate()
            if stop_requested is not None and time.monotonic()-stop_requested>=termination_grace_s and not any(x['method']=='owned Popen.kill' for x in actions):
                actions.append({'method':'owned Popen.kill','actual_owned_child_pid':proc.pid,'utc':stamp()});proc.kill()
            if stop_requested is not None and time.monotonic()-stop_requested>termination_grace_s+30.:
                error='OWN_CHILD_WAIT_UNVERIFIED_AFTER_KILL_TIMEOUT';break
            try:code=proc.wait(timeout=.25);wait_completed=True
            except subprocess.TimeoutExpired:continue
    except BaseException as exc:
        error=type(exc).__name__+': '+str(exc)
        if proc is not None and proc.poll() is None:
            actions.append({'method':'owned Popen.terminate','actual_owned_child_pid':proc.pid,'reason':'OWN_PARENT_EXCEPTION','utc':stamp()});proc.terminate()
            try:code=proc.wait(timeout=termination_grace_s);wait_completed=True
            except subprocess.TimeoutExpired:
                actions.append({'method':'owned Popen.kill','actual_owned_child_pid':proc.pid,'utc':stamp()});proc.kill()
                try:code=proc.wait(timeout=30.);wait_completed=True
                except subprocess.TimeoutExpired:error='OWN_CHILD_WAIT_UNVERIFIED_AFTER_KILL_TIMEOUT'
        elif proc is not None:code=proc.wait();wait_completed=True
    finally:
        try:
            raw={'schema':'Factory80OwnedFinitePhysicsTrueWaitV1','actual_parent_pid':os.getpid(),'actual_child_pid':proc.pid if proc else None,
                'argv':list(argv),'started_at_utc':start,'finished_at_utc':stamp(),'actual_parent_wait_completed':wait_completed,
                'actual_signed_returncode':code,'observed_owned_parent_interruptions':interrupts,'actual_owned_child_actions':actions,
                'own_parent_exception':error,'scheduling_wall_timeout_s':wall_timeout_s,'scheduling_timeout_is_not_C4':True}
            save(raw_path,raw)
        finally:
            for sig,handler in previous.items():signal.signal(sig,handler)
    return code,raw
def grade_completion(signed_code,result,stop_receipt):
    if stop_receipt is not None and stop_receipt.get('entire_task_stop') is True:return 2,'ACTUAL_NEGATIVE_CONTROL_DID_NOT_FAIL_STOP'
    if signed_code==2 or (result is not None and result.get('entire_task_stop') is True):return 2,'ACTUAL_RUNNER_STOP2'
    if result is None:return 1,'FAILED_EXECUTION_NO_REQUIRED_PHYSICS_JSON'
    if signed_code!=0:return 1,'FAILED_EXECUTION_WITH_PHYSICS_PARTIAL_RESULT'
    if result.get('validation_status')!='V2_STAGE3_BASELINE_CLOSED':return 1,'ACTUAL_STAGE3_NOT_CLOSED'
    return 0,'ACTUAL_STAGE3_BASELINE_CLOSED'
