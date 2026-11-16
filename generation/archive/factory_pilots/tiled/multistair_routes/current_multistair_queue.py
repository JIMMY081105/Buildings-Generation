"""Serial full-M CPU continuation; ordinary failures are logged then skipped."""
import argparse, json, os, subprocess, sys, time
from pathlib import Path
W = Path('/cephfs/shared/yixing/outputs/factory80_20261011')
I = W/'work/P1_IMPLEMENTATION_20261015'
R3 = W/'scripts/cpu_all_serial_20261015_R3'
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(R3), str(I), str(HERE)]
from cpu_all_serial import read, require, actual_terminal, stop, issue, SCOPE
from owned_memory_guard import dump, self_oom_priority
from stat_hash_cache import StatHashCache
from multistair_cpu_contract import clean_terminal, stable_result, frame_author_matches
MAIN = '/cephfs/shared/yixing/envs/main/bin/python'
CPU = W/'work/CPU_ALL_29_SERIAL_20261015_R3'
FRAMES = W/'work/ALL_CURRENT_FRAMER2_SERIAL_20261015'
VARIANTS = W/'work/CURRENT_R5_P8_CPU_SERIAL_20261015'


def main():
    self_oom_priority()
    p = argparse.ArgumentParser(allow_abbrev=False)
    p.add_argument('--expected-index-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    C = StatHashCache([W/'work/P1_HASH_CACHE_20261015.json'])
    ix = C.ref(HERE/'SOURCE_INDEX.json')
    require(ix['sha256'] == a.expected_index_sha256, 'Full-M queue source index differs')
    idx = read(ix['path'])
    refs = [ix, *idx['runtime_refs'], *idx['dependency_refs']]
    for r in refs:
        require(C.ref(r['path']) == r, 'Actual full-M queue source changed')
    D = a.output.resolve()
    require(D.is_relative_to(W/'work') and not D.exists(), 'Fresh full-M queue output required')
    D.mkdir()
    dump(D/'BEGIN.json', dict(own_pid=os.getpid(), source_index_ref=ix,
        scope='29 selected current N2 worlds plus R5 and single P8',
        no_original_whole_world_reraster=True, GPU_started=False))
    records = []
    def pending_until(fn):
        while True:
            require(not stop(), 'Existing STOP while waiting')
            value = fn()
            if value is not None:
                return value
            time.sleep(30)
    def dispatch(label, inp):
        require(not stop(), 'STOP before next full-M job')
        B = D/label
        B.mkdir()
        dump(B/'INPUT.json', inp)
        ir = C.ref(B/'INPUT.json')
        argv = [MAIN, '-u', str(HERE/'current_multistair_cpu.py'),
            '--input', ir['path'], '--expected-input-sha256', ir['sha256'],
            '--expected-index-sha256', ix['sha256'], '--output', str(B/'SUBJECT')]
        plan = dict(schema='Factory80OwnedSingleMemoryGuardedJobV1', argv=argv,
            expected_peak_bytes=20*1024**3,
            expected_peak_basis='Original per-stair masks/build/merge, current scratch reused via local memmaps;20GiB conservative own-tree prediction,AS28GiB;8GiB reserve and serial heavy lock.',
            take_cpu_lease=True, GPU_job=False, protected_refs=[*refs, ir, inp['world_input_ref'], inp['frame_manifest_ref']],
            environment=dict(CUDA_VISIBLE_DEVICES='', PXR_WORK_THREAD_LIMIT='2',
                MALLOC_ARENA_MAX='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='2', MKL_NUM_THREADS='1'))
        dump(B/'JOB_PLAN.json', plan)
        pr = C.ref(B/'JOB_PLAN.json')
        args = [MAIN, '-u', str(I/'run_guarded_job.py'), '--plan', pr['path'],
            '--expected-plan-sha256', pr['sha256'], '--output', str(B/'GUARDED')]
        with (B/'stdout.log').open('xb') as out, (B/'stderr.log').open('xb') as err:
            child = subprocess.Popen(args, stdout=out, stderr=err)
            dump(B/'DISPATCH_BEGIN.json', dict(argv=args, child_pid=child.pid))
            code = child.wait()
        dump(B/'DISPATCH_TRUE_WAIT.json', dict(argv=args, actual_child_pid=child.pid,
            actual_signed_returncode=code, actual_wait_completed=True))
        terminal = actual_terminal(B/'GUARDED')
        require(terminal is not None, 'Unknown actual full-M terminal; next job must not start')
        if (B/'SUBJECT/CRITICAL_STOP_NEGATIVE_ESCAPE.json').exists() or stop():
            raise SystemExit(2)
        rp = B/'SUBJECT/FINITE_RESULT.json'
        result = read(rp) if rp.exists() else {}
        record = dict(label=label, terminal=terminal,
                      result_ref=C.ref(rp) if result else None)
        dump(B/'ROW_COMPLETE.json', record)
        records.append(record)
        if terminal['code'] or result.get('ordinary_failures'):
            state = B/'LEDGER'
            state.mkdir()
            issue(state, label+' full-M CPU ordinary failure',
                json.dumps(dict(terminal=terminal, ordinary_failures=result.get('ordinary_failures'),
                                evidence=str(B)), ensure_ascii=False), 'FULL_M_'+label)
    # These two scratch producers and FrameR2 results are already genuine and
    # complete. No bounded producer is run before the second equivalence test.
    for suffix in ('001', '002'):
        source = idx['pilot_consumer_refs'][suffix]
        require(C.ref(source['path']) == source, 'Reviewed pilot consumer changed')
        row = read(source['path'])['rows'][0].copy()
        row.update(world_binding_kind='EXISTING_CURRENT_SUPERVISED_SUPPORT',
                   existing_consumer_ref=source, original_n2_plan_ref=idx['original_plan_ref'])
        wr = D/(suffix+'.WORLD_INPUT.json')
        dump(wr, row)
        matching = []
        for r in idx['existing_completed_frames']:
            manifest = read(r['manifest_ref']['path'])
            fa = manifest['actual_source_authoring']
            require(C.ref(fa['path']) == fa, 'Actual frame author source changed')
            if frame_author_matches(row, manifest, read(fa['path'])):
                matching.append(r)
        require(len(matching) == 1, 'One genuine exact-current pilot frame manifest required')
        dispatch('factory80_'+suffix, dict(world_input_ref=C.ref(wr),
            scratch_mode='EXISTING_GENUINE_ORIGINAL', frame_manifest_ref=matching[0]['manifest_ref']))
    # Every remaining building uses the already-running serial producer.
    for label in ['factory80_'+s for s in SCOPE if s not in ('001', '002')] + ['VARIANT_R5', 'VARIANT_P8']:
        isvariant = label.startswith('VARIANT_')
        up = VARIANTS/label.removeprefix('VARIANT_') if isvariant else CPU/label
        wp = up/'SUBJECT/ACTUAL_VARIANT_WORLD_INPUT.json' if isvariant else CPU/'PREPARE/INPUTS'/(label+'.json')
        terminal = pending_until(lambda: actual_terminal(up/'GUARDED') if (up/'GUARDED').exists() else None)
        rp = up/'SUBJECT/FINITE_RESULT.json'
        fp = FRAMES/label
        ft = pending_until(lambda: actual_terminal(fp/'GUARDED') if (fp/'GUARDED').exists()
            else ({'code': 1, 'child_started': False} if (fp/'ROW_COMPLETE.json').exists() else None))
        frp = fp/'SUBJECT/FINITE_RESULT.json'
        try:
            clean_terminal(terminal)
            clean_terminal(ft)
            require(wp.exists() and rp.exists() and frp.exists(), 'Actual current CPU/frame result missing')
            row = read(wp)
            stable_result(read(rp), row['root_ref'])
            frame_result = read(frp)
            stable_result(frame_result, row['root_ref'])
            require(frame_result.get('frame_manifest_ref'), 'Actual completed FrameR2 manifest missing')
        except Exception as error:
            B = D/label
            B.mkdir()
            state = B/'LEDGER'
            state.mkdir()
            reason = dict(status='BLOCKED_NO_QUALIFIED_CURRENT_CPU_OR_FRAMES', reason=str(error),
                CPU_terminal=terminal, frame_terminal=ft, required_result=str(rp), required_frames=str(frp))
            dump(B/'ROW_COMPLETE.json', reason)
            records.append(dict(label=label, **reason))
            issue(state, label+' missing qualified full-M CPU inputs', json.dumps(reason), 'FULL_M_INPUT_'+label)
            continue
        dispatch(label, dict(world_input_ref=C.ref(wp), scratch_mode='COMPLETED_CURRENT_SERIAL_CPU',
            upstream_guard=str(up/'GUARDED'), upstream_result_ref=C.ref(rp),
            frame_guard=str(fp/'GUARDED'), frame_result_ref=C.ref(frp),
            frame_manifest_ref=frame_result['frame_manifest_ref']))
    dump(D/'FINITE_RESULT.json', dict(rows=records, all31_full_M_jobs_accounted=True,
        GPU_used=False, READY='NOT_INFERRED', entire_task_stop=False))


if __name__ == '__main__':
    raise SystemExit(main())
