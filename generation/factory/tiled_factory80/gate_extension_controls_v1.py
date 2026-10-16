"""Execute mandatory graph controls and regress a real historical PASS graph."""
from __future__ import annotations
import ast
import difflib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
import numpy as np
import gate_extension_nstorey_v1 as adapter

W=Path('/cephfs/shared/yixing/outputs/factory80_20261011')
OUT=W/'evidence/gate_audit'
OLD=Path('/cephfs/shared/yixing/outputs/factory10/work/exact10_final_after_navigation_repair_r2')
BASE=OLD/'post_stage_d_cpu_results'


def arrays_of(centers, storeys, pairs, stairs):
    centers=np.asarray(centers,dtype=np.float64)
    pairs=np.asarray(pairs,dtype=np.int32)
    records=[];arrival=[]
    for edge_id in stairs:
        first,second=map(int,pairs[edge_id]);length=float(np.linalg.norm(centers[second]-centers[first]))
        records.append(dict(edge_id=edge_id,lower_storey_id=int(storeys[first]),
            upper_storey_id=int(storeys[second]),lower_node_id=first,upper_node_id=second,length_m=length))
        arrival.append(dict(edge_id=edge_id,lower_arrival_xyz_m=centers[first].tolist(),
                            upper_arrival_xyz_m=centers[second].tolist()))
    manifest=dict(schema='Factory80GraphControlFixtureV1',stair_edges=records)
    result=dict(edge_node_ids=pairs,node_storey_id=np.asarray(storeys,dtype=np.int8),
        edge_is_stair=np.isin(np.arange(len(pairs)),stairs),node_center_m=centers,
        manifest_json_utf8=np.frombuffer(json.dumps(manifest).encode(),dtype=np.uint8).copy())
    return result,dict(schema='Factory80MeasuredStairArrivalsV1',rows=arrival)


def fixture_record(arrays,arrivals):
    return dict(arrays={key:array.tolist() for key,array in arrays.items() if key!='manifest_json_utf8'},
                manifest=adapter.frozen.manifest_from_artifact(arrays),arrivals=arrivals)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    started=datetime.now(timezone.utc).isoformat()
    before=adapter.ref(adapter.frozen.__file__)
    manifest=adapter.read_json(adapter.LEGACY/'evidence/FROZEN_CONTRACT_SOURCES_GATE5_V2_R1.json')
    protected=[]
    for row in manifest['rows']:
        if row['group']=='simulator':
            name=row['path'].replace('\\','/').rsplit('/',1)[-1]
            actual=adapter.ref(adapter.REPO/'protomotions/simulator/base_simulator'/name)
            protected.append(dict(**actual,expected_sha256=row['sha256'],unchanged=actual['sha256']==row['sha256']))
    if not all(row['unchanged'] for row in protected):
        raise RuntimeError('Runtime pinned simulator source differs; no controls run')
    g25=dict(status='PASS',storeys={
        'Floor1':dict(stair_arrival_xyz_m=[0,0,0]),
        'Floor2':dict(stair_arrival_xyz_m=[0,0,3])})
    stage_d=dict(status='PASS')
    controls=[]
    positive,arrivals=arrays_of([[0,0,0],[1,0,0],[0,0,3],[1,0,3]],
        [0,0,1,1],[[0,1],[2,3],[0,2],[1,3]],[2,3])
    result=adapter.gate8(positive,g25,stage_d,expected_storeys=2,expected_stairs=2,arrivals=arrivals)
    controls.append(dict(name='two_storey_two_stair_positive',execution='ACTUALLY_EXECUTED',
        input=fixture_record(positive,arrivals),expected_gate_status='PASS',actual=result,
        correct=result['status']=='PASS'))
    adapter.write_new(OUT/'CONTROL_01_POSITIVE.json',controls[-1])
    if result['status']!='PASS':
        raise RuntimeError('Positive fixture failed; runner requires repair, no negative control executed')
    # A disjoint same-floor island is connected only by an extra staircase.
    # The whole graph stays connected; removing ALL stairs leaves 3, not N=2.
    bad,arrivals_bad=arrays_of([[0,0,0],[1,0,0],[0,0,3],[1,0,3],[5,0,0]],
        [0,0,1,1,0],[[0,1],[2,3],[0,2],[1,3],[4,3]],[2,3,4])
    result=adapter.gate8(bad,g25,stage_d,expected_storeys=2,expected_stairs=3,arrivals=arrivals_bad)
    controls.append(dict(name='all_stair_removal_wrong_component_count',execution='ACTUALLY_EXECUTED',
        input=fixture_record(bad,arrivals_bad),expected_gate_status='FAIL',actual=result,
        correct=result['status']=='FAIL' and result['actual'].get('components_after_stair_edge_removal')==3))
    adapter.write_new(OUT/'CONTROL_02_WRONG_ALL_REMOVAL.json',controls[-1])
    if result['status']!='FAIL':
        adapter.write_new(W/'GATE_EXTENSION_NSTOREY.json',dict(status='ENTIRE_TASK_STOP_REQUIRED',
            reason='ACTUALLY EXECUTED negative control did not report FAIL',controls=controls))
        return 80
    # Remove only one real staircase from the positive graph. Its floor skeleton
    # and the other staircase remain; connectivity must still be exactly one.
    keep=np.ones(len(positive['edge_node_ids']),dtype=np.bool_);keep[2]=False
    component=adapter.frozen._connected_component_count(len(positive['node_storey_id']),positive['edge_node_ids'][keep])
    controls.append(dict(name='remove_only_one_stair_redundancy',execution='ACTUALLY_EXECUTED',
        removed_edge_id=2,remaining_edge_node_ids=positive['edge_node_ids'][keep].tolist(),
        expected_component_count=1,actual_component_count=component,status='PASS' if component==1 else 'FAIL',
        correct=component==1,negative_gate_rejection_expected=False))
    adapter.write_new(OUT/'CONTROL_03_SINGLE_REMOVAL_REDUNDANCY.json',controls[-1])
    # Non-stair route has legal legacy loop delta=2, so rejecting this input
    # specifically exercises the removal control rather than the delta limit.
    bad,arrivals_bad=arrays_of([[0,0,0],[1,0,0],[0,0,3],[1,0,3]],
        [0,0,1,1],[[0,1],[2,3],[0,2],[1,3],[0,3]],[2,3])
    result=adapter.gate8(bad,g25,stage_d,expected_storeys=2,expected_stairs=2,arrivals=arrivals_bad)
    controls.append(dict(name='non_stair_cross_storey_route',execution='ACTUALLY_EXECUTED',
        input=fixture_record(bad,arrivals_bad),expected_gate_status='FAIL',actual=result,
        correct=result['status']=='FAIL' and result['actual'].get('components_after_stair_edge_removal')==1))
    adapter.write_new(OUT/'CONTROL_04_NON_STAIR_ROUTE.json',controls[-1])
    if result['status']!='FAIL':
        adapter.write_new(W/'GATE_EXTENSION_NSTOREY.json',dict(status='ENTIRE_TASK_STOP_REQUIRED',
            reason='ACTUALLY EXECUTED negative control did not report FAIL',controls=controls))
        return 80
    regression=[]
    accepted=adapter.read_json(BASE/'GRAPH_ACCEPTANCE.json')
    sd=adapter.read_json(OLD/'stage_d10_a40_evidence/STAGE_D10_A40_SUMMARY.json')
    sdrows={row['BID']:row for row in sd['rows']}
    choices=[bid for bid,row in accepted['table'].items() if row.get('FINAL')=='PASS' and row.get('G8')=='PASS']
    if not choices:
        regression.append(dict(execution='NOT EXECUTED',reason='No historical FINAL/G8 PASS target found'))
    else:
        bid=sorted(choices)[0]
        graph=BASE/'graphs'/(bid+'.multistorey_topology_v1.npz')
        gp=BASE/'g25_evidence'/(bid+'.json')
        arrays=adapter.frozen.load_multistorey_artifact(graph)
        old_g25=adapter.read_json(gp)
        original=adapter.frozen.gate8(arrays,old_g25,sdrows[bid])
        extended=adapter.gate8(arrays,old_g25,sdrows[bid],expected_storeys=2,expected_stairs=1)
        same=original['status']==extended['status']=='PASS' and original['actual']==extended['actual'] and original['expected']==extended['expected']
        row=dict(building=bid,execution='ACTUALLY_EXECUTED',historical_final=accepted['table'][bid]['FINAL'],
            historical_G8=accepted['table'][bid]['G8'],native_original=original,adapter_result=extended,
            verdict_and_all_measured_values_unchanged=same,graph=adapter.ref(graph),g25=adapter.ref(gp),
            stage_d_row=sdrows[bid],stage_d_summary=adapter.ref(OLD/'stage_d10_a40_evidence/STAGE_D10_A40_SUMMARY.json'),
            recorded_acceptance=adapter.ref(BASE/'GRAPH_ACCEPTANCE.json'))
        adapter.write_new(OUT/'REGRESSION_REAL_PREVIOUS_PASS.json',row);regression.append(row)
    after=adapter.ref(adapter.frozen.__file__)
    source=Path(adapter.frozen.__file__).read_text();parsed=ast.parse(source)
    fn=next(node for node in parsed.body if isinstance(node,ast.FunctionDef) and node.name=='gate8')
    lines=source.splitlines(keepends=True);base_fn=''.join(lines[fn.lineno-1:fn.end_lineno])
    new_source=Path(adapter.__file__).read_text();newparsed=ast.parse(new_source)
    newfn=next(node for node in newparsed.body if isinstance(node,ast.FunctionDef) and node.name=='gate8')
    newlines=new_source.splitlines(keepends=True);new_fn=''.join(newlines[newfn.lineno-1:newfn.end_lineno])
    diff=''.join(difflib.unified_diff(base_fn.splitlines(keepends=True),new_fn.splitlines(keepends=True),
        fromfile=str(adapter.frozen.__file__)+' gate8',tofile=str(adapter.__file__)+' gate8'))
    diffpath=OUT/'G8_AUTHORIZED_MULTIPLICITY_DIFF.patch'
    with diffpath.open('x',encoding='utf-8') as stream:stream.write(diff)
    result=dict(schema='Factory80GateExtensionNStoreyV1',status='PASS' if all(x['correct'] for x in controls) and all(x.get('verdict_and_all_measured_values_unchanged',False) for x in regression) and before==after else 'PARTIAL',
        generated_at_utc=datetime.now(timezone.utc).isoformat(),started_at_utc=started,
        work_completion='G8 adapter and graph-level mandatory controls implemented and actually executed',
        validation='Graph-control fixtures and one actual historical PASS building; pilot building-specific controls not yet executed',
        frozen_base_before=before,frozen_base_after=after,frozen_original_unmodified=before==after,
        adapter=adapter.ref(adapter.__file__),runtime_simulator_sources=protected,
        original_function_lines=[fn.lineno,fn.end_lineno],adapter_function_lines=[newfn.lineno,newfn.end_lineno],
        exact_function_diff=adapter.ref(diffpath),
        authorized_changes=['Exactly two storeys -> explicit consecutive N storeys',
            'Exactly one stair -> explicit M>=1 stairs','ALL stair edges removed must leave N components',
            'The same <=0.50 m endpoint and [rise,4*rise] length predicates applied to EACH stair'],
        unchanged_predicates=['components == 1','edge_node_relation_delta <= 2',
            'stage_d_status == PASS','g25_status == PASS','All Standability V1 parameters and implementation',
            'All G25 and stage_d and capacity implementations'],controls=controls,regressions=regression,
        non_stair_cross_storey_control_is_graph_fixture=True,
        cannot_support='These graph fixtures do not demonstrate a physical multi-stair building, rendered quality, actual non-stair physical routes, candidate gate completion, formal acceptance, or humanoid stair locomotion.')
    adapter.write_new(W/'GATE_EXTENSION_NSTOREY.json',result)
    print(json.dumps(dict(status=result['status'],controls=[(x['name'],x['correct']) for x in controls],
        real_pass_regressions=[(x.get('building'),x.get('verdict_and_all_measured_values_unchanged')) for x in regression],
        output=str(W/'GATE_EXTENSION_NSTOREY.json'))))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
