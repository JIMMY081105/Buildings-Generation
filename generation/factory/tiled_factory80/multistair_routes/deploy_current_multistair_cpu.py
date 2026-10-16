"""Register and start one reviewed OWN CPU continuation; no scientific run here."""
import hashlib, json, pathlib, subprocess, sys
W = pathlib.Path('/cephfs/shared/yixing/outputs/factory80_20261011')
I = W/'work/P1_IMPLEMENTATION_20261015'
sys.path.insert(0, str(I))
from stat_hash_cache import StatHashCache
from owned_memory_guard import self_oom_priority, dump
self_oom_priority()
S = W/'scripts/current_multistair_cpu_20261015_R2'
D = W/'work/CURRENT_FULL_M_CPU_SERIAL_20261015_R2'
C = StatHashCache([W/'work/P1_HASH_CACHE_20261015.json'])
def require(v, s):
    if not v:
        raise ValueError(s)
def read(p):
    return json.loads(pathlib.Path(p).read_bytes())
def pinned(p, sha):
    r = C.ref(p)
    require(r['sha256'] == sha, 'Reviewed source differs: '+str(p))
    return r
require(not D.exists() and not (S/'SOURCE_INDEX.json').exists(), 'Exclusive new source/output required')
frame = pinned(W/'scripts/current_frames_all_20261015_R1/SOURCE_INDEX.json',
    '3480fcf3f397237fc257b8b25089e06f80b0ecb30773123e77f1ec5eda5a138a')
r3 = pinned(W/'scripts/cpu_all_serial_20261015_R3/SOURCE_INDEX.json',
    '462d707d09c27f9e9dd55cb3a58334d64c934aaec0f017f0c8a3c5d1b829ef60')
variant = pinned(W/'scripts/current_variants_cpu_20261015_R1/SOURCE_INDEX.json',
    'e559c197c407065747a38120b4ae0b53fe8cebeec8943953abe9c3584da1d60a')
pilot = {
    '001': pinned(W/'work/CURRENT001_A1_NAV_INPUTS_20261015/ACTUAL_CURRENT_SUPPORT_CONSUMER_INPUT.json',
        '3fc55c6383e3a4489a6b84d4ea1869edc7c50aefa32d547a04088e7a33eccb16'),
    '002': pinned(W/'work/P5_R4_A1_NAV_INPUTS_20261015/ACTUAL_R4_SUPPORT_CONSUMER_INPUT.json',
        '934a6efd4bfec99f107245fb02cd4c01aa7764a1863e21541c7f4a7716422aa5')}
plan = pinned(W/'TILING_PLAN.json', '100c4fe2be62c7c8abe0043aed053cced40f69feeec4186047a32096a9510e78')
runtime = [C.ref(S/n) for n in ['current_multistair_cpu.py', 'current_multistair_queue.py',
    'multistair_cpu_contract.py', 'native_path_cpu_projection.py', 'current_full_m_source_binding.py']]
deps = [frame, r3, variant, plan, *pilot.values(),
        *read(r3['path'])['runtime_refs'], *read(variant['path'])['runtime_refs'],
        C.ref(I/'owned_memory_guard.py'), C.ref(I/'run_guarded_job.py'), C.ref(I/'stat_hash_cache.py')]
for name in ['factory80_stage2_cross_storey.py', 'factory80_stage2_navigation.py',
             'gate_protomotions_ingest_conformance.py']:
    deps.append(C.ref('/cephfs/shared/yixing/code/Scenesmith-code-study/final_training_validation/'+name))
authority = pinned(W/'work/USER_STEERING_20261015_CPU_ALL.json',
    '6d7cd349ea2290095e92d0730232825646f114ecb5f83b9e161846d4e750c8d7')
deps.append(authority)
preflight = C.ref(S/'ACTUAL_PILOT_SOURCE_AND_COORDINATE_PREFLIGHT.json')
pf = read(preflight['path'])
require([r['building'] for r in pf['rows']] == ['factory80_001', 'factory80_002']
        and all(r['actual_unchanged_producer_binder_called'] is True
                and r['structural_count_from_actual_raw'] == len(r['projections']) == 2
                for r in pf['rows']) and pf['B2'] == 'NOT_EXECUTED',
        'Both genuine pilot source/coordinate preflights must complete')
deps.append(preflight)
deps = list({r['path']: r for r in deps}.values())
sys.path.insert(0, str(W/'scripts/cpu_all_serial_20261015_R3'))
from cpu_all_serial import SCOPE, stop
require(not stop(), 'Existing critical STOP')
idx = dict(schema='Factory80CurrentFullMCPUQueueSourceV1', runtime_refs=runtime,
    dependency_refs=deps, authorized_29=['factory80_'+s for s in SCOPE],
    pilot_consumer_refs=pilot, original_plan_ref=plan,
    existing_completed_frames=read(frame['path'])['existing_completed_frames'],
    user_authority_ref=authority, GPU_used=False,
    original_scientific_functions_modified=False,
    original_multistair_builder_main_called=False,
    scope='Exact current29 plusR5/singleP8; original per-stair split/build/merge/serialize/load, original B2 and full-M ingest; G8/G13/nativeG25/StageD not inferred.')
dump(S/'SOURCE_INDEX.json', idx)
ix = C.ref(S/'SOURCE_INDEX.json')
main = '/cephfs/shared/yixing/envs/main/bin/python'
t = subprocess.run([main, str(S/'test_multistair_cpu_contract.py')], capture_output=True, text=True)
require(t.returncode == 0, 'Actual input/receipt fixture failed: '+t.stderr)
tests = json.loads(t.stdout)
require(tests['status'] == 'PASS' and len(tests['cases']) == 16, 'Incomplete actual fixture coverage')
dump(S/'ACTUAL_BOUNDARY_TEST_RESULTS.json', tests)
negative = [main, str(S/'current_multistair_cpu.py'), '--input', str(S/'NOT_A_REAL_INPUT.json'),
    '--expected-input-sha256', '0'*64, '--expected-index-sha256', '0'*64,
    '--output', str(W/'work/FULL_M_R2_WRONG_INDEX_MUST_NOT_CREATE_20261015')]
result = subprocess.run(negative, capture_output=True, text=True)
require(result.returncode == 1 and 'Full-M source index differs' in result.stderr
        and not (W/'work/FULL_M_R2_WRONG_INDEX_MUST_NOT_CREATE_20261015').exists(),
        'Wrong-index actual CLI did not fail at the intended boundary')
dump(S/'ACTUAL_WRONG_INDEX_REJECTION.json', dict(argv=negative,
    actual_signed_returncode=result.returncode, stderr=result.stderr,
    output_created=False, scientific_child_started=False))
log = W/'work/CURRENT_FULL_M_CPU_SERIAL_20261015_R2.CONTROLLER.log'
require(not log.exists(), 'Exclusive controller log required')
import shlex
argv = [main, '-u', str(S/'current_multistair_queue.py'),
        '--expected-index-sha256', ix['sha256'], '--output', str(D)]
shell = 'exec '+shlex.join(argv)+' >'+shlex.quote(str(log))+' 2>&1'
launch = subprocess.run(['tmux', 'new-session', '-d', '-s',
    'factory80_full_m_cpu_20261015_R2', shell], capture_output=True, text=True)
require(launch.returncode == 0, 'tmux queue launch failed: '+launch.stderr)
print(json.dumps(dict(source_index_ref=ix, controller_dispatched=True,
    output=str(D), actual_boundary_tests=16,
    scientific_results='PENDING_ACTUAL_GUARDED_CHILD', tmux_stdout=launch.stdout)))
