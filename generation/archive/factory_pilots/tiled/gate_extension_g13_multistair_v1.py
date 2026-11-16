"""G13 input adapter limited to M stair endpoints and per-stair tread selection.

All native S2 thresholds, formulae, geometry functions and sealed-opening
controls remain unchanged. Tread selection is bound to an actual measured
Mesh, never a planned name. N remains 2 for the pilot.
"""
from __future__ import annotations
import argparse
import ast
import difflib
from pathlib import Path
import sys
import traceback
import gate_extension_nstorey_v1 as gate

sys.path[:0]=[str(gate.LEGACY/'evidence/frozen_sources/validation'),
             '/cephfs/shared/yixing/outputs/factory10/runtime_plot_deps',
             '/cephfs/shared/yixing/outputs/factory10/runtime_tensor_deps']
import gate13_geometry_audit as original
from pxr import Usd,UsdGeom
TASK=Path('/cephfs/shared/yixing/outputs/factory80_20261011')


def actual_tread_paths(root,entries):
    # This own stage only enumerates the complete physical input inventory.
    # Return strings, so no Stage/Prim handles survive into the original audit.
    stage=Usd.Stage.Open(str(root),Usd.Stage.LoadAll)
    if stage is None:raise ValueError('Cannot open actual root for complete stair inventory')
    result=[]
    for entry in entries:
        stair=stage.GetPrimAtPath(entry['prim_path'])
        if not stair or not stair.IsValid():raise ValueError('Actual physical stair root is missing')
        meshes=[str(prim.GetPath()) for prim in Usd.PrimRange(stair)
                if prim.IsA(UsdGeom.Mesh) and prim.GetName()=='integrated_tread_collision']
        if len(meshes)!=1:raise ValueError('Every physical stair input must bind exactly one actual integrated tread Mesh')
        result.append((entry,meshes[0]))
    return result


def transformed_function():
    source=Path(original.__file__).read_text()
    function=next(node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name=='_audit_one')
    text=''.join(source.splitlines(keepends=True)[function.lineno-1:function.end_lineno])
    old="""    if stair_pairs.shape != (1, 2):
        raise ValueError(f"{building}: expected exactly one stair edge")
    stair_pair = stair_pairs[0]
"""
    new="""    if stair_pairs.ndim != 2 or stair_pairs.shape[1] != 2 or len(stair_pairs) < 1:
        raise ValueError(f"{building}: expected M >= 1 complete stair edges")
    stair_pair = stair_pairs.reshape(-1)
"""
    if text.count(old)!=1:raise ValueError('Frozen G13 stair-shape source drift')
    changed=text.replace(old,new)
    old='        if len(endpoint_ids) != 1:\n'
    new='        if len(endpoint_ids) != len(stair_pairs):\n'
    if changed.count(old)!=1:raise ValueError('Frozen G13 per-storey endpoint-count source drift')
    changed=changed.replace(old,new)
    old='        reachable = _reachable_support_component(support, seed_xy)\n'
    new="""        reachable = np.zeros(support.shape, dtype=np.bool_)
        for physical_stair_endpoint in endpoint_ids:
            physical_seed_xy = tuple(int(value) for value in node_xy_indices[physical_stair_endpoint])
            reachable |= _reachable_support_component(support, physical_seed_xy)
"""
    if changed.count(old)!=1:raise ValueError('Frozen G13 stair-seed source drift')
    changed=changed.replace(old,new)
    old='            "stair_edge_node_id": endpoint_id,\n'
    new=old+'            "all_stair_edge_node_ids": endpoint_ids,\n'
    if changed.count(old)!=1:raise ValueError('Frozen G13 evidence-field source drift')
    changed=changed.replace(old,new)
    scope=dict(original.__dict__)
    exec(compile(changed,str(Path(__file__).resolve()),'exec'),scope)
    return scope['_audit_one'],text,changed,function


def run(a):
    a.output.mkdir(parents=True,exist_ok=True)
    before=gate.ref(original.__file__)
    if before['sha256']!='81ef3b9f18b68cde636e78277a3523f952baea7cff741a6b05489081a3d40e58':
        raise ValueError('Frozen G13 source drift')
    fn,old,new,node=transformed_function()
    # Observe the unchanged native negative control immediately. A later
    # rendering/formatting exception must never hide a control that ran and
    # failed to reject. This wrapper does not change its input or verdict.
    actual_control_context={}
    native_control=original._sealed_opening_control
    def observed_control(extraction,artifact,top_tread_cells):
        control=native_control(extraction,artifact,top_tread_cells)
        evidence=dict(execution='ACTUALLY_EXECUTED',physical_stair=actual_control_context.get('stair_id'),
                      sealed_scope='ALL actual stair-mask footprints and removal of ALL graph stair edges, as in the original native control',
                      evaluated_top_tread_stair_id=actual_control_context.get('stair_id'),actual_native_control=control)
        path=a.output/(str(actual_control_context.get('stair_id'))+'_ACTUAL_SEALED_CONTROL.json')
        if control.get('control_correctly_fails') is not True:
            stop=dict(status='ENTIRE_TASK_STOP_REQUIRED',building=a.building,
                reason='ACTUALLY EXECUTED sealed-opening negative control did not correctly fail',evidence=evidence)
            # An evidence-write error must not convert an observed gate
            # failure into an ordinary candidate failure that may continue.
            for target,value in ((path,evidence),(a.output/'CRITICAL_STOP_G13_NEGATIVE_CONTROL.json',stop),
                (TASK/'CRITICAL_STOP_G13_NEGATIVE_CONTROL.json',stop)):
                try:gate.write_new(target,value)
                except Exception as error:print('CRITICAL G13 control persistence error: '+str(error),file=sys.stderr,flush=True)
            print('ENTIRE_TASK_STOP_REQUIRED: actual sealed-opening control did not correctly fail',file=sys.stderr,flush=True)
            raise SystemExit(80)
        gate.write_new(path,evidence)
        return control
    fn.__globals__['_sealed_opening_control']=observed_control
    diffpath=a.output/'G13_M_STAIR_INPUT_DIFF.patch'
    with diffpath.open('x',encoding='utf-8') as stream:
        stream.write(''.join(difflib.unified_diff(old.splitlines(keepends=True),new.splitlines(keepends=True),fromfile=str(original.__file__),tofile=str(Path(__file__).resolve()))))
    root=a.root_stage.resolve();g25=gate.read_json(a.g25)
    graph_arrays=gate.frozen.load_multistorey_artifact(a.graph)
    graph_manifest=gate.frozen.manifest_from_artifact(graph_arrays)
    source_before=gate.ref(root)
    if graph_manifest['building']!=a.building or graph_manifest['source_usdz_sha256']!=source_before['sha256']:
        raise ValueError('G13 actual root identity/SHA differs from the actual graph source binding')
    decision=Path(graph_manifest['decision_json'])
    if gate.sha256(decision)!=graph_manifest['decision_json_sha256']:
        raise ValueError('G13 actual authoring receipt differs from the actual graph decision binding')
    if len(set(map(int,graph_arrays['node_storey_id'])))!=2:
        raise ValueError('This pilot G13 adapter accepts exactly two actual storeys')
    alias_bid=a.building+'_h'+str(a.height_cm)
    alias=a.output/(alias_bid+root.suffix)
    if alias.exists():raise FileExistsError('Source alias already exists')
    alias.symlink_to(root)
    if not alias.is_file() or gate.sha256(alias)!=gate.sha256(root):raise ValueError('G13 source alias is not byte-identical')
    rows={(alias_bid,name):row for name,row in g25['storeys'].items()}
    results=[]
    stair_manifest=gate.read_json(a.stairs_manifest)
    bound_stairs=actual_tread_paths(root,stair_manifest.get('stairs',stair_manifest.get('rows',[])))
    tread_inventory=a.output/'ACTUAL_ALL_STAIR_TREAD_INPUT_INVENTORY.json'
    gate.write_new(tread_inventory,dict(schema='Factory80ActualAllStairTreadInputInventoryV1',
        source_root=source_before,stairs_manifest=gate.ref(a.stairs_manifest),
        actual_stair_count=len(bound_stairs),
        rows=[dict(stair_id=entry['stair_id'],prim_path=entry['prim_path'],
            actual_tread_mesh=tread_path) for entry,tread_path in bound_stairs],
        own_enumeration_stage_and_prim_handles_released_before_native_audit=True,
        actual_native_geometry_audit='NOT EXECUTED AT INVENTORY_WRITE',
        memory_saving_measurement='UNVERIFIED'))
    for entry,tread_path in bound_stairs:
        actual_control_context['stair_id']=entry['stair_id']
        saved=original.TREAD_PATH;original.TREAD_PATH=tread_path
        figures=a.output/entry['stair_id'];figures.mkdir(exist_ok=False)
        try:
            result=fn(alias,a.graph,figures,rows)
            results.append(dict(stair_id=entry['stair_id'],actual_tread_mesh=tread_path,execution='ACTUALLY_EXECUTED',native_result=result))
            gate.write_new(figures/'G13.json',results[-1])
            control=result['negative_control']
            if control.get('control_correctly_fails') is not True:
                gate.write_new(a.output/'CRITICAL_STOP_G13_NEGATIVE_CONTROL.json',dict(status='ENTIRE_TASK_STOP_REQUIRED',
                    reason='ACTUALLY EXECUTED sealed-opening negative control did not correctly fail',stair=entry['stair_id'],control=control))
                return 80
        except Exception as error:
            control_path=a.output/(str(entry['stair_id'])+'_ACTUAL_SEALED_CONTROL.json')
            results.append(dict(stair_id=entry['stair_id'],actual_tread_mesh=tread_path,
                execution='FAILED_BEFORE_RETURNING_TERMINAL_REPORT',native_result=dict(status='FAIL',error=str(error),traceback=traceback.format_exc()),
                negative_control_evidence=gate.ref(control_path) if control_path.is_file() else None,
                negative_control='See immediate actual native control evidence' if control_path.is_file() else 'UNVERIFIED; an exception before control return does not establish a negative-control verdict'))
            gate.write_new(figures/'G13_EXCEPTION.json',results[-1])
        finally:original.TREAD_PATH=saved
    after=gate.ref(original.__file__)
    source_after=gate.ref(root)
    report=dict(schema='Factory80PilotG13MStairGeometryEvidenceV1',building=a.building,
        status='PASS' if results and all(x['native_result']['status']=='PASS' for x in results) and source_before==source_after else 'FAIL',
        alias_input_name=alias_bid,source_root=gate.ref(root),alias_root=gate.ref(alias),
        declared_height_cm=a.height_cm,declared_height_is_not_an_actual_measurement=True,
        graph=gate.ref(a.graph),g25=gate.ref(a.g25),frozen_before=before,frozen_after=after,
        actual_all_stair_tread_input_inventory=gate.ref(tread_inventory),
        graph_source_binding_verified=True,authoring=gate.ref(decision),
        root_source_before=source_before,root_source_after=source_after,root_source_unchanged=source_before==source_after,
        frozen_source_unchanged=before==after,adapter=gate.ref(__file__),diff=gate.ref(diffpath),
        base_function_lines=[node.lineno,node.end_lineno],
        unchanged_slab_coverage_formula='peak-layer occupied cells / binary_fill_holes(same peak-layer occupied cells)',
        unchanged_slab_coverage_threshold=0.70,stair_results=results,
        cannot_support='The declared height alias is only the original parser input; actual height is still measured by native S2b. G13 does not establish visual review, full-chain approval, or robot traversal.')
    gate.write_new(a.output/'G13_M_STAIR_REPORT.json',report)
    print(dict(status=report['status'],stairs=len(results),output=str(a.output/'G13_M_STAIR_REPORT.json')))
    return 0 if report['status']=='PASS' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--building',required=True);p.add_argument('--root-stage',type=Path,required=True)
    p.add_argument('--graph',type=Path,required=True);p.add_argument('--g25',type=Path,required=True)
    p.add_argument('--stairs-manifest',type=Path,required=True);p.add_argument('--height-cm',type=int,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    from factory80_cpu_resources_v1 import heavy_cpu_lock
    try:
        with heavy_cpu_lock('G13',a.output):return run(a)
    except Exception as error:
        a.output.mkdir(parents=True,exist_ok=True)
        exception=a.output/'G13_RUNNER_EXCEPTION.json'
        gate.write_new(exception,dict(status='FAIL',execution='ACTUAL_EXCEPTION',error=str(error),traceback=traceback.format_exc(),
            entire_task_stop_required=False,disposition='Record the candidate failure and continue under section 8'))
        terminal=a.output/'G13_M_STAIR_REPORT.json'
        if not terminal.exists():gate.write_new(terminal,dict(schema='Factory80PilotG13MStairGeometryEvidenceV1',building=a.building,
            status='FAIL',work_completion='PARTIAL',exception_evidence=gate.ref(exception),
            actual_control_evidence=[gate.ref(path) for path in sorted(a.output.glob('*_ACTUAL_SEALED_CONTROL.json'))],
            cannot_support='A runner exception does not establish a completed G13 or negative-control measurement. See any immediate actual-control receipts.'))
        print(str(error),file=sys.stderr,flush=True);return 1


if __name__=='__main__':raise SystemExit(main())
