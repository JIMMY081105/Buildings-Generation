"""Factory80 v4 C5: load the collision package and query full-size capsules."""
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
        if len(columns)>3 and columns[1]=='0' and columns[2]=='NVIDIA A40' and re.fullmatch(r'Yes\s*:\s*0',columns[3]):
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stage', type=Path, required=True)
    ap.add_argument('--spawns', type=Path, required=True)
    ap.add_argument('--expected-colliders', type=int, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--negative-machine-point', type=Path)
    args, kitargs = ap.parse_known_args()
    os.environ.setdefault('ACCEPT_EULA','Y')
    os.environ.setdefault('OMNI_KIT_ACCEPT_EULA','YES')
    args.out.mkdir(parents=True, exist_ok=False)
    Path('/proc/self/oom_score_adj').write_text('1000')
    started = time.monotonic()
    result = dict(tool='isaac_c5@v2', status='ERROR', body_radius_m=.25,
                  total_body_height_m=1.60, training='NOT_EXECUTED',
                  formal_acceptance='NOT_EXECUTED')
    app = None
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
        log = args.out/'ISAAC.log'
        sys.argv = [sys.argv[0], *kitargs, '--/renderer/activeGpu=0',
                    '--/physics/cudaDevice=0', '--/renderer/multiGpu/enabled=False',
                    '--/app/useFabricSceneDelegate=false', '--/log/file='+str(log),
                    '--/log/level=info']
        threading.Thread(target=sample, daemon=True).start()
        from isaacsim import SimulationApp
        sdk = time.monotonic()
        app = SimulationApp(dict(headless=True, fast_shutdown=True, active_gpu=0,
                                 physics_gpu=0, multi_gpu=False), experience='')
        result['SDK_startup_elapsed_s'] = time.monotonic()-sdk
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
            return 2
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
        if app:
            app.close(skip_cleanup=True)
    print(json.dumps({k: v for k, v in result.items() if k not in ('spawn_queries','GPU_samples','traceback')}, indent=2))
    return {'PASS': 0, 'FAIL': 2, 'NEGATIVE_ESCAPE_STOP_ALL': 80}.get(result['status'], 1)


if __name__ == '__main__':
    raise SystemExit(main())
