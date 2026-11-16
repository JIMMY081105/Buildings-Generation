"""User-authorized operational clock contract; contains no scientific predicates."""
import hashlib,json,math
from pathlib import Path
POLICY_PATH=Path('/cephfs/shared/yixing/outputs/factory80_20261011/pipeline/v4/ROUND4_TIME_POLICY_V1.json')
POLICY_SHA='82926b75de1791c93ca48feff1541e1b355ba86720817ae5d9eb5c74d3442881'
SOFT_LIMIT_S=3600
HARD_LIMIT_S=10800
def reference():
    raw=POLICY_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=POLICY_SHA:raise ValueError('Time policy SHA differs')
    policy=json.loads(raw)
    if policy.get('schema')!='Factory80Round4TimePolicyV1' or policy.get('record_only_threshold_s')!=SOFT_LIMIT_S or policy.get('hard_stop_s')!=HARD_LIMIT_S:raise ValueError('Time policy values differ')
    return dict(path=str(POLICY_PATH),sha256=POLICY_SHA)
def numeric(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
def record(elapsed):
    if not numeric(elapsed) or elapsed<0:raise ValueError('Invalid elapsed time')
    return dict(time_policy=reference(),limit_s=HARD_LIMIT_S,record_only_threshold_s=SOFT_LIMIT_S,
                over_60min_record_only=elapsed>SOFT_LIMIT_S,over_180min_stuck=elapsed>HARD_LIMIT_S)
def clock_valid(doc):
    try:
        elapsed=doc['elapsed_s'];start=doc['monotonic_start_ns'];end=doc['monotonic_end_ns']
        if not all(numeric(v) for v in (elapsed,start,end)) or not 0<=elapsed==(end-start)/1e9:return False
        if 'time_policy' not in doc:return elapsed<=SOFT_LIMIT_S and 'batch_run_v14.py' not in doc.get('source_tool_shas',{})
        expected=record(elapsed)
        return elapsed<=HARD_LIMIT_S and all(doc.get(k)==v for k,v in expected.items())
    except (KeyError,TypeError,ValueError,OSError):return False
