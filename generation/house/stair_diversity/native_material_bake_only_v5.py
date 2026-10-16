"""Select the actual A40 with Infinigen's native device initializer before baking."""
import bpy
import ast
import copy
import inspect
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from infinigen.core import init as native_init
from infinigen.tools import export as native_export
from evidence_common import ref,utc_now,write_new
from native_material_bake_only_v4 import bake as calibrated_native_bake


def cpu_guarded_bake_pass_ast(source):
    """Change only the real native bake operator target, not its arguments/body."""
    module = ast.parse(source)
    functions = [node for node in module.body if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1 or functions[0].name != 'bake_pass':
        raise RuntimeError('Expected exactly the unchanged native bake_pass function')
    function = functions[0]
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == 'bake'
             and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == 'object'
             and isinstance(node.func.value.value, ast.Attribute) and node.func.value.value.attr == 'ops'
             and isinstance(node.func.value.value.value, ast.Name) and node.func.value.value.value.id == 'bpy']
    if len(calls) != 1:
        raise RuntimeError('Native bake_pass must have exactly one actual bpy.ops.object.bake call')
    calls[0].func = ast.copy_location(ast.Name(id='__goal1_late_cpu_bake_operator', ctx=ast.Load()), calls[0].func)
    function.name = '__goal1_late_cpu_bake_pass'
    return ast.fix_missing_locations(module)


@contextmanager
def late_cpu_bake_guard(output_dir):
    """CPU enforcement and evidence at every real native bake operator call.

    The exporter sets GPU after its initializer. Instrument only the native
    operator call, so that late override is corrected immediately before work.
    The native function still uses its actual module globals (including the
    calibrated temporary SPECIAL_BAKE table), and all functions restore on exit.
    """
    if os.environ.get('CUDA_VISIBLE_DEVICES') != '':
        raise RuntimeError('Late CPU bake guard requires CUDA_VISIBLE_DEVICES empty')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / 'NATIVE_CPU_BAKE_TRACE.jsonl'
    result_path = output_dir / 'NATIVE_CPU_BAKE_RESULT.json'
    if trace_path.exists() or result_path.exists():
        raise FileExistsError('Late CPU evidence is immutable; use a fresh output directory')
    original = native_export.bake_pass
    native_source = inspect.getsource(original)
    program = cpu_guarded_bake_pass_ast(native_source)
    namespace = vars(native_export)
    names = ('__goal1_late_cpu_bake_operator', '__goal1_late_cpu_bake_pass')
    if any(name in namespace for name in names):
        raise RuntimeError('Nested/conflicting native CPU guard is not supported')
    native_operator = bpy.ops.object.bake
    records = []
    failure = None
    with trace_path.open('x', encoding='utf-8') as stream:
        def emit(value):
            stream.write(json.dumps(value, sort_keys=True, allow_nan=False) + '\n')
            stream.flush()
        def guarded_operator(*args, **kwargs):
            before = bpy.context.scene.cycles.device
            native_init.configure_cycles_devices(use_gpu=False)
            actual = bpy.context.scene.cycles.device
            record = {'index': len(records), 'utc': utc_now(), 'device_before_late_guard': before,
                      'device_immediately_before_operator': actual, 'bake_type': kwargs.get('type'),
                      'status': 'STARTED'}
            records.append(record)
            emit(record)
            if actual != 'CPU' or os.environ.get('CUDA_VISIBLE_DEVICES') != '':
                record['status'] = 'CPU_GUARD_FAILED'
                emit(record)
                raise RuntimeError('Actual native bake operator is not CPU-only')
            started = time.monotonic()
            try:
                result = native_operator(*args, **kwargs)
                record['device_after_operator'] = bpy.context.scene.cycles.device
                if record['device_after_operator'] != 'CPU':
                    raise RuntimeError('CPU device changed during the native bake operator')
                record['status'] = 'COMPLETED'
                return result
            except BaseException as error:
                record.update(status='OPERATOR_FAILED', error_type=type(error).__name__, error=str(error))
                raise
            finally:
                record['elapsed_seconds'] = time.monotonic() - started
                emit(record)
        namespace[names[0]] = guarded_operator
        try:
            exec(compile(program, inspect.getfile(original), 'exec'), namespace)
            native_export.bake_pass = namespace[names[1]]
            yield records
            if not records or any(record['status'] != 'COMPLETED' for record in records):
                raise RuntimeError('No complete measured CPU bake operator calls')
        except BaseException as error:
            failure = {'type': type(error).__name__, 'message': str(error)}
            raise
        finally:
            native_export.bake_pass = original
            for name in names:
                namespace.pop(name, None)
            write_new(result_path, {
                'schema': 'NativeLateCpuBakeOperatorEvidenceV1',
                'status': 'PASS' if failure is None and records else 'FAIL',
                'operator_call_count': len(records),
                'cpu_completed_operator_count': sum(record['status'] == 'COMPLETED' and record.get('device_after_operator') == 'CPU' for record in records),
                'late_gpu_override_observed_count': sum(record['device_before_late_guard'] == 'GPU' for record in records),
                'records': records, 'failure': failure, 'trace': ref(trace_path),
                'native_baker': ref(inspect.getfile(original)), 'adapter': ref(__file__),
                'native_algorithm_or_operator_arguments_changed': False,
                'runtime_patch': 'Only replace the one native bake_pass operator target with a CPU guard; use original module globals, operator and arguments; restore functions in finally.',
            })


def bake(blend_obj, blueprint, source_stage, output_dir, use_gpu=True):
    bpy.context.scene.render.engine='CYCLES'
    selected=native_init.configure_cycles_devices(use_gpu=use_gpu)
    prefs=bpy.context.preferences.addons['cycles'].preferences
    if use_gpu:
        assert selected and len(selected)==1,'Native Cycles did not select exactly one GPU'
        assert selected[0].type in ('OPTIX','CUDA') and 'A40' in selected[0].name and selected[0].use
        assert bpy.context.scene.cycles.device=='GPU'
        assert all(not d.use for d in prefs.devices if d.type=='CPU')
    else:
        assert bpy.context.scene.cycles.device=='CPU','Native CPU selection did not take effect'
    write_new(Path(output_dir).parent/'NATIVE_GPU_DEVICE.json',{'schema':'Residential54NativeCyclesDeviceV1','status':'RECORDED',
        'generated_at_utc':utc_now(),'blender_version':bpy.app.version_string,'scene_device':bpy.context.scene.cycles.device,
        'compute_device_type':prefs.compute_device_type,
        'selected':[{'name':d.name,'type':d.type,'id':d.id,'use':bool(d.use)} for d in (selected or [])],
        'all_devices':[{'name':d.name,'type':d.type,'id':d.id,'use':bool(d.use)} for d in prefs.devices],
        'use_gpu':bool(use_gpu),
        'native_initializer':ref(native_init.__file__),'adapter':ref(__file__),
        'scope':'Use the unchanged official native device-selection function. The calibrated material bake, native graphs, geometry and export predicates remain unchanged. Runtime GPU telemetry is recorded separately.'})
    if use_gpu:
        return calibrated_native_bake(blend_obj, blueprint, source_stage, output_dir)
    with late_cpu_bake_guard(Path(output_dir).parent):
        return calibrated_native_bake(blend_obj, blueprint, source_stage, output_dir)
