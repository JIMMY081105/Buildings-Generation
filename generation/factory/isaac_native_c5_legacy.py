"""Factory80 native6: unchanged C5/lifecycle5 with render5 stage transition."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import resource
import re
import subprocess
import sys
import threading
import time
import traceback

HASH_CACHE = {}


def selected_gpu(log):
    selected=[]
    for line in Path(log).read_text(errors='replace').splitlines():
        if 'Active GPU:' in line and line.rsplit('Active GPU:',1)[1].strip()=='NVIDIA A40':
            selected.append(line)
        columns=[v.strip() for v in line.split('|')]
        if line.lstrip().startswith('|') and len(columns)>3 and columns[1]=='0' and columns[2]=='NVIDIA A40' and re.fullmatch(r'Yes\s*:\s*0',columns[3]):
            selected.append(line)
    assert selected, 'ACTUAL_RENDERER_DEVICE_NOT_A40_INDEX0'
    return selected

def digest(path):
    path = Path(path)
    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    if key in HASH_CACHE:
        return HASH_CACHE[key]
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
    HASH_CACHE[key] = h.hexdigest()
    return HASH_CACHE[key]


def write(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp.' + str(os.getpid()))
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(tmp, path)


def gpu():
    return subprocess.check_output([
        'nvidia-smi', '--query-gpu=index,name,uuid,memory.used,utilization.gpu',
        '--format=csv,noheader,nounits'], text=True).strip()


class NativeRegistrationFailure(RuntimeError):
    pass


def lifecycle_cleanup(app, phase, timeout_s=60.):
    """Owned operation boundary only; no CUDA cache or global settings reset."""
    import asyncio
    import ctypes
    import gc
    import omni.usd
    import omni.timeline
    started=time.monotonic()
    def current_rss():
        return next(int(r.split()[1])*1024 for r in Path('/proc/self/status').read_text().splitlines() if r.startswith('VmRSS:'))
    receipt=dict(phase=phase,RSS_before_bytes=current_rss(),replicator_loaded=False,
                 PhysX_objects_released=False,stage_closed=False)
    timeline=omni.timeline.get_timeline_interface();timeline.stop()
    # Do not import/start Replicator for C5-only jobs. Render4 already imports
    # this public module; its nonblocking stop is completed here after capture.
    rep=sys.modules.get('omni.replicator.core')
    if rep is not None:
        receipt['replicator_loaded']=True
        stopped=bool(rep.orchestrator.get_is_stopped())
        receipt['rep_stop_requested']=not stopped
        waiting=rep.orchestrator.wait_until_complete_async() if stopped else rep.orchestrator.stop_async()
        future=asyncio.ensure_future(waiting);deadline=time.monotonic()+timeout_s
        while not future.done():
            if time.monotonic()>deadline:
                future.cancel()
                raise TimeoutError('Replicator stop incomplete at '+phase+' after '+str(timeout_s)+'s')
            app.update()
        future.result()
        if not rep.orchestrator.get_is_stopped():raise RuntimeError('Replicator not STOPPED after awaited cleanup')
        receipt['replicator_stopped']=True
    timeline.stop()
    context=omni.usd.get_context()
    # Keep no Python Usd.Stage reference alive across close/GC/trim.
    had_stage=context.get_stage() is not None;receipt['had_stage']=had_stage
    if had_stage:
        from omni.physx import get_physx_interface
        # Installed5.1 PhysX.release_physics_objects releases scene objects;
        # it does not reset the interface/settings or arbitrary CUDA buffers.
        try:
            get_physx_interface().release_physics_objects()
            receipt['PhysX_objects_released']=True
        finally:
            closed=bool(context.close_stage());receipt['close_stage_return']=closed
            if not closed:raise RuntimeError('Synchronous USD close_stage returned false')
    if context.get_stage() is not None:raise RuntimeError('USD context still has a stage after synchronous close')
    for _ in range(3):app.update()
    if context.get_stage() is not None:raise RuntimeError('USD stage reappeared during cleanup updates')
    receipt['stage_closed']=True
    receipt['gc_collected']=gc.collect()
    for _ in range(3):app.update()
    libc=ctypes.CDLL('libc.so.6')
    trim=libc.malloc_trim;trim.argtypes=[ctypes.c_size_t];trim.restype=ctypes.c_int
    receipt['malloc_trim_return']=int(trim(0))
    # A zero return means no additional pages returned, not a cleanup failure.
    receipt.update(RSS_after_bytes=current_rss(),elapsed_s=time.monotonic()-started,status='PASS')
    print('FACTORY80_LIFECYCLE '+json.dumps(receipt),flush=True)
    return receipt


def query_session(app, args, log, session):
    """Actual native C5 predicate; never initializes or closes the SDK."""
    args.out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = dict(tool='isaac_c5@v4.0', status='ERROR', body_radius_m=.25,
                  total_body_height_m=1.60, training='NOT_EXECUTED',
                  formal_acceptance='NOT_EXECUTED')
    stop = threading.Event()
    samples = []
    def sample():
        while not stop.is_set():
            try:
                samples.append(dict(elapsed_s=time.monotonic()-started,
                                    device=gpu()))
            except (OSError, subprocess.SubprocessError):
                pass
            stop.wait(1.)
    result['resident_session'] = dict(session)
    result['SDK_startup_included_in_this_job'] = False
    threading.Thread(target=sample, daemon=True).start()
    try:
        assert args.stage.is_file() and args.expected_colliders > 0
        points = json.loads(args.spawns.read_text())
        floors = points['floors']
        assert len(floors) == 2 and all(len(r['points_xyz_m']) == 20 for r in floors)
        assert len({r['floor_index'] for r in floors}) == 2
        platforms = points['stair_platforms']
        assert len(platforms) in (2, 3)
        queries = [dict(kind='floor', floor_index=r['floor_index'], index=i,
                        point_xyz_m=p) for r in floors
                   for i, p in enumerate(r['points_xyz_m'])]
        queries += [dict(kind='stair_platform', stair_id=r['stair_id'],
                         point_xyz_m=r['point_xyz_m']) for r in platforms]
        negative = None
        if args.negative_machine_point:
            negative = json.loads(args.negative_machine_point.read_text())
            assert negative['machine_prim'] and len(negative['point_xyz_m']) == 3
            queries[0] = dict(kind='inside_real_machine_negative', **negative)
        result['input_sha'] = dict(stage=digest(args.stage), spawns=digest(args.spawns))
        if args.negative_machine_point:
            result['input_sha']['negative_machine_point']=digest(args.negative_machine_point)
        result['GPU_before'] = gpu()
        import omni.usd
        import omni.timeline
        from omni.physx import (get_physx_scene_query_interface,
                               get_physx_cooking_private_interface,
                               get_physxunittests_interface)
        from pxr import Gf, Usd, UsdGeom, UsdPhysics, PhysicsSchemaTools
        selected = selected_gpu(log)
        result['actual_GPU'] = selected[-1]
        context = omni.usd.get_context()
        loading = time.monotonic()
        assert context.open_stage(str(args.stage.resolve()))
        while context.get_stage_loading_status()[2]:
            app.update()
        stage = context.get_stage()
        assert stage
        stage.SetEditTarget(stage.GetSessionLayer())
        colliders = {
            str(p.GetPath()) for p in stage.Traverse(Usd.TraverseInstanceProxies())
            if p.HasAPI(UsdPhysics.CollisionAPI)
            and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False}
        result['actual_enabled_colliders'] = len(colliders)
        assert len(colliders) == args.expected_colliders, 'COLLIDER_COUNT_DIFFERS_FROM_C1'
        assert not stage.GetPrimAtPath('/Factory80C5Probe'), 'PROBE_NAMESPACE_ALREADY_USED'
        if not any(p.IsA(UsdPhysics.Scene) for p in stage.Traverse()):
            UsdPhysics.Scene.Define(stage, '/Factory80C5Physics')
        probe = UsdGeom.Capsule.Define(stage, '/Factory80C5Probe')
        probe.CreateRadiusAttr(.25)
        probe.CreateHeightAttr(1.60 - 2*.25)
        probe.CreateAxisAttr('Z')
        UsdPhysics.CollisionAPI.Apply(probe.GetPrim()).CreateCollisionEnabledAttr(True)
        transform = probe.AddTranslateOp()
        timeline = omni.timeline.get_timeline_interface()
        timeline.play()
        for _ in range(5):
            app.update()
        cooking=get_physx_cooking_private_interface()
        while True:
            cooked=cooking.get_cooking_statistics()
            if cooked.total_scheduled_tasks <= cooked.total_finished_tasks:
                break
            app.update()
        for _ in range(3):
            app.update()
        stats=get_physxunittests_interface().get_physics_stats()
        shape_keys=['numSphereShapes','numBoxShapes','numCapsuleShapes',
                    'numCylinderShapes','numConvexShapes','numTriMeshShapes',
                    'numPlaneShapes','numConeShapes']
        native_shape_count=sum(int(stats.get(k,0)) for k in shape_keys)
        result['actual_native_PhysX_statistics']=stats
        result['actual_native_shapes_including_probe']=native_shape_count
        result['native_cooking_tasks']=dict(scheduled=cooked.total_scheduled_tasks,
                                           finished=cooked.total_finished_tasks)
        result['load_elapsed_s'] = time.monotonic()-loading
        if native_shape_count < args.expected_colliders+1:
            result.update(status='FAIL',reason='NATIVE_COLLIDER_REGISTRATION_INCOMPLETE',
                          expected_native_shapes_including_probe=args.expected_colliders+1)
            raise NativeRegistrationFailure(result['reason'])
        timeline.pause()
        interface = get_physx_scene_query_interface()
        encoded = PhysicsSchemaTools.encodeSdfPath(probe.GetPath())
        rows = []
        for row in queries:
            foot = list(map(float, row['point_xyz_m']))
            assert len(foot) == 3 and all(__import__('math').isfinite(v) for v in foot)
            transform.Set(Gf.Vec3d(foot[0], foot[1], foot[2]+.80))
            timeline.play()
            for _ in range(3):
                app.update()
            timeline.pause()
            hits = []
            def hit(record):
                path = str(record.collision)
                if path != str(probe.GetPath()):
                    hits.append(path)
                return True
            returned = interface.overlap_shape(*encoded, hit, False)
            rows.append(dict(**row, actual_returned_hit_count=int(returned),
                             other_colliders=sorted(set(hits)),
                             status='FAIL' if hits else 'PASS'))
        result['spawn_queries'] = rows
        result['status'] = 'FAIL' if any(r['other_colliders'] for r in rows) else 'PASS'
        if negative is not None:
            detected = any(h == negative['machine_prim'] or h.startswith(negative['machine_prim'].rstrip('/')+'/')
                           for h in rows[0]['other_colliders'])
            result['negative_control'] = dict(expected='FAIL', actual=result['status'],
                                              real_machine_detected=detected,
                                              machine_prim=negative['machine_prim'])
            if not detected:
                result['status'] = 'NEGATIVE_ESCAPE_STOP_ALL'
        timeline.stop()
        result['source_stage_unchanged'] = digest(args.stage) == result['input_sha']['stage']
        assert result['source_stage_unchanged']
    except NativeRegistrationFailure:
        pass
    except BaseException as error:
        result.update(status='ERROR', error=str(error), traceback=traceback.format_exc())
    finally:
        stop.set()
        result['elapsed_s'] = time.monotonic()-started
        result['peak_memory_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        result['GPU_samples'] = samples
        result['VRAM_measurement'] = 'Raw whole-device MiB sampled every second; shared-process attribution is not inferred.'
        if samples:
            result['peak_device_VRAM_MiB'] = max(int(r['device'].split(',')[3].strip()) for r in samples)
        write(args.out/'C5.json', result)
    return result


def main():
    ap = argparse.ArgumentParser(description='Native C5 and seven-photo SDK session')
    ap.add_argument('--serve', action='store_true')
    ap.add_argument('--session-out', type=Path, required=True)
    ap.add_argument('--stage', type=Path)
    ap.add_argument('--spawns', type=Path)
    ap.add_argument('--expected-colliders', type=int)
    ap.add_argument('--out', type=Path)
    ap.add_argument('--negative-machine-point', type=Path)
    args, kitargs = ap.parse_known_args()
    args.session_out.mkdir(parents=True, exist_ok=False)
    Path('/proc/self/oom_score_adj').write_text('1000')
    os.environ.setdefault('ACCEPT_EULA', 'Y')
    os.environ.setdefault('OMNI_KIT_ACCEPT_EULA', 'YES')
    launch = time.monotonic()
    log = args.session_out / 'ISAAC.log'
    birth = Path('/proc/self/stat').read_text().split(') ', 1)[1].split()[19]
    session = dict(tool='isaac_c5@v6.0', pid=os.getpid(), birth=birth,
                   source_sha256=digest(Path(__file__)), jobs=[])
    write(args.session_out/'OWNER.json', dict(pid=os.getpid(), birth=birth, argv=sys.argv))
    sys.argv = [sys.argv[0], *kitargs, '--/renderer/activeGpu=0',
                '--/physics/cudaDevice=0', '--/renderer/multiGpu/enabled=False',
                '--/app/useFabricSceneDelegate=false', '--/log/file='+str(log),
                '--/log/level=info']
    session['GPU_before'] = gpu()
    print('FACTORY80_GPU_BEFORE '+session['GPU_before'], flush=True)
    from isaacsim import SimulationApp
    sdk = time.monotonic()
    app = SimulationApp(dict(headless=True, fast_shutdown=True,
        renderer='PathTracing', width=1280, height=720, active_gpu=0,
        physics_gpu=0, multi_gpu=False, samples_per_pixel_per_frame=32,
        denoiser=True, max_bounces=6), experience='')
    session['SDK_startup_elapsed_s'] = time.monotonic()-sdk
    session['actual_GPU'] = selected_gpu(log)[-1]
    write(args.session_out/'SESSION.json', session)
    print('FACTORY80_SESSION_READY '+json.dumps({k:v for k,v in session.items() if k!='jobs'}), flush=True)
    import omni.usd
    import omni.timeline
    def operation(command):
        mode = command.pop('mode')
        job_id = command.pop('job_id', str(len(session['jobs'])+1))
        started = time.monotonic()
        print('FACTORY80_JOB_STARTED '+json.dumps(dict(job_id=job_id, mode=mode)), flush=True)
        fields = dict(command)
        for key in ('stage', 'spawns', 'out', 'negative_machine_point', 'cameras'):
            if fields.get(key) is not None: fields[key] = Path(fields[key])
        summary = dict(pid=os.getpid(), birth=birth,
            SDK_startup_elapsed_s=session['SDK_startup_elapsed_s'],
            SDK_startup_already_recorded_in_session=True)
        boundaries=[];result=None;operation_error=None;raw_out=None;canonical_created=False
        def publish(value):
            value=dict(value)
            value['resident_session']=summary
            value['native_session_tool']=session['tool']
            value['lifecycle_v5']=dict(tool=session['tool'],source_sha256=session['source_sha256'],boundaries=boundaries)
            name='C5.json' if mode=='c5' else 'PHOTOS.json'
            if raw_out is not None and (raw_out/name).is_file():
                value['raw_query_or_capture_result']=dict(path=str(raw_out/name),sha256=digest(raw_out/name))
            if mode=='c5':
                value['query_tool']=value.get('tool')
                value['tool']=session['tool']
            if mode=='photos' and value.get('status')=='PASS':
                # Byte moves only, after cleanup. Render4's geometry, cameras,
                # exposure, pixels and source hash are untouched.
                for name_png in value['image_files']:
                    if Path(name_png).name!=name_png:raise ValueError('Unexpected render output filename')
                    os.replace(raw_out/name_png,fields['out']/name_png)
            if canonical_created:write(fields['out']/name,value)
            row=dict(job_id=job_id,mode=mode,status=value.get('status','ERROR'),
                     elapsed_s=time.monotonic()-started,
                     out=str(fields['out']) if fields.get('out') else None,
                     lifecycle_boundaries=boundaries)
            session['jobs'].append(row);write(args.session_out/'SESSION.json',session)
            print('FACTORY80_JOB_RESULT '+json.dumps(row),flush=True)
            return value
        try:
            boundaries.append(lifecycle_cleanup(app,'before_'+mode))
            if mode in ('c5','photos'):
                fields['out'].mkdir(parents=True,exist_ok=False);canonical_created=True
                raw_out=fields['out']/('query_raw' if mode=='c5' else 'capture_raw')
                payload_fields=dict(fields,out=raw_out)
            if mode=='release':
                result=dict(status='PASS',stage_released=True,actual_GPU=selected_gpu(log)[-1])
            elif mode=='c5':
                payload_fields.setdefault('negative_machine_point',None)
                result=query_session(app,argparse.Namespace(**payload_fields),log,summary)
            elif mode=='photos':
                from rtx_render_legacy import capture_session
                payload_fields.setdefault('scale_xy',1.)
                result=capture_session(app,argparse.Namespace(**payload_fields),log)
            else:raise ValueError('Unknown native operation '+str(mode))
            # Preserve the original immediate stop-all semantics. No cleanup
            # exception may mask an actual escaped negative control.
            if result.get('status')=='NEGATIVE_ESCAPE_STOP_ALL':
                try:publish(result)
                finally:
                    try:sys.stdout.flush();sys.stderr.flush()
                    finally:os._exit(80)
        except BaseException as error:
            operation_error=dict(error=str(error),traceback=traceback.format_exc())
        # The caught exception variable is cleared before collecting garbage;
        # no traceback object is kept that could retain a stage/capture frame.
        try:
            boundaries.append(lifecycle_cleanup(app,'after_'+mode))
        except BaseException as error:
            cleanup_error=dict(error=str(error),traceback=traceback.format_exc())
            operation_error=dict(previous_error=operation_error,cleanup_error=cleanup_error)
        if operation_error is not None:
            result=dict(result or {},status='ERROR',operation_error=operation_error)
        elif result is None:
            result=dict(status='ERROR',operation_error='Operation returned no result')
        if mode=='release' and result.get('status')=='PASS':result['GPU_after']=gpu()
        result=publish(result)
        if operation_error is not None:raise RuntimeError('Native6 operation/cleanup failed: '+json.dumps(operation_error))
        return result
    code = 0
    try:
        if args.serve:
            for line in sys.stdin:
                if not line.strip(): continue
                command = json.loads(line)
                if command.get('mode')=='shutdown': break
                operation(command)
        else:
            required = (args.stage, args.spawns, args.expected_colliders, args.out)
            assert all(v is not None for v in required)
            result = operation(dict(mode='c5', stage=args.stage, spawns=args.spawns,
                expected_colliders=args.expected_colliders, out=args.out,
                negative_machine_point=args.negative_machine_point))
            code = {'PASS':0, 'FAIL':2, 'NEGATIVE_ESCAPE_STOP_ALL':80}.get(result['status'], 1)
    except BaseException as error:
        session.update(status='ERROR', error=str(error), traceback=traceback.format_exc())
        code = 1
    finally:
        omni.timeline.get_timeline_interface().stop()
        session['process_elapsed_s'] = time.monotonic()-launch
        session['peak_memory_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        session['intended_exit_code'] = code
        write(args.session_out/'SESSION.json', session)
        # The actual semantic code must survive SimulationApp.close's exit(0).
        sys.stdout.flush(); sys.stderr.flush()
        if code: os._exit(code)
        try: app.close(skip_cleanup=True)
        except SystemExit: pass
    return code


if __name__ == '__main__':
    raise SystemExit(main())
