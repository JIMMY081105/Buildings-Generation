"""Verify adapter data assembly on an actual historical graph; no new building."""
from pathlib import Path
from types import SimpleNamespace
import argparse
import hashlib
import numpy as np
import gate_extension_nstorey_v1 as gate
import gate_extension_multistair_builder_v1 as builder

W=Path('/cephfs/shared/yixing/outputs/factory80_20261011')
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=W/'evidence/gate_audit/MULTISTAIR_BUILDER_ASSEMBLY_SMOKE.json');args=parser.parse_args()
R=W/'evidence/gate_audit/REGRESSION_REAL_PREVIOUS_PASS.json'
r=gate.read_json(R);arrays=gate.frozen.load_multistorey_artifact(r['graph']['path'])
extraction=SimpleNamespace(bands=(SimpleNamespace(stair_mask=arrays['floor1_stair_mask']),
                                SimpleNamespace(stair_mask=arrays['floor2_stair_mask'])))
one=builder.merge_native_artifacts([('single_old',arrays)],extraction)
one_equal=builder.native.deterministic_multistorey_npz_bytes(one)==builder.native.deterministic_multistorey_npz_bytes(arrays)
merged=builder.merge_native_artifacts([('fixture_duplicate_edge_01',arrays),('fixture_duplicate_edge_02',arrays)],extraction)
manifest=builder.native.manifest_from_artifact(merged)
g25=gate.read_json(r['g25']['path'])
arrivals=dict(rows=[dict(edge_id=row['edge_id'],lower_arrival_xyz_m=g25['storeys']['Floor1']['stair_arrival_xyz_m'],
                    upper_arrival_xyz_m=g25['storeys']['Floor2']['stair_arrival_xyz_m']) for row in manifest['stair_edges']])
result=gate.gate8(merged,g25,r['stage_d_row'],expected_storeys=2,expected_stairs=2,arrivals=arrivals)
payload=builder.native.deterministic_multistorey_npz_bytes(merged)
stable=payload==builder.native.deterministic_multistorey_npz_bytes(merged)
native_g1=gate.frozen._g1(merged);native_g2=gate.frozen._g2(merged);native_g6=gate.frozen._g6(merged)
native_g9=gate.frozen._g9(merged)
path_measures=gate.frozen.graph_path_measures(merged)
checks=dict(one_stair_serialized_bytes_identical=one_equal,merged_graph_native_schema_accepted=True,
    merged_stair_count_is_two=int(np.count_nonzero(merged['edge_is_stair']))==2,
    merged_graph_G8_PASS=result['status']=='PASS',deterministic_serialization=stable,
    native_G1_PASS=native_g1['status']=='PASS',native_G2_PASS=native_g2['status']=='PASS',
    native_G6_PASS=native_g6['status']=='PASS',native_G9_PASS=native_g9['status']=='PASS',
    next_hops_reach_goal=path_measures['all_next_hops_reach_goal_monotonically'])
doc=dict(schema='Factory80MultistairAdapterAssemblySmokeV1',status='PASS' if all(checks.values()) else 'FAIL',
    execution='ACTUALLY_EXECUTED_GRAPH_ASSEMBLY_ONLY',checks=checks,G8=result,
    native_G1=native_g1,native_G2=native_g2,native_G6=native_g6,native_G9=native_g9,path_measures=path_measures,
    nodes=len(merged['node_id']),edges=len(merged['edge_id']),byte_count=len(payload),
    graph_fixture_sha256=hashlib.sha256(payload).hexdigest(),original=gate.ref(R),
    native=gate.ref(builder.native.__file__),adapter=gate.ref(builder.__file__),
    cannot_support='Two copied graph links are an assembly fixture, not two physical stairs. This check cannot support a physical factory, actual stair inventory, pilot gate completion, visual quality, or acceptance.')
gate.write_new(args.output,doc)
print(doc['status'],checks)
raise SystemExit(0 if doc['status']=='PASS' else 1)
