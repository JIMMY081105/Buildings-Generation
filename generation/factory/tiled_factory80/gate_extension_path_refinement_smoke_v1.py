"""Check own floor-path subdivision union without running a physical gate."""
import ast,json
from pathlib import Path
import numpy as np
SOURCE=Path('/cephfs/shared/yixing/outputs/factory80_20261011/scripts/gate_extension_multistair_builder_v1.py')
OUT=SOURCE.parent.parent/'evidence/gate_audit/MULTISTAIR_PATH_REFINEMENT_SMOKE.json'
tree=ast.parse(SOURCE.read_text())
function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='refine_native_path_union')
ns={'np':np};exec(compile(ast.Module(body=[function],type_ignores=[]),str(SOURCE),'exec'),ns)
nodes=[(0.,0.,0.),(4.,0.,0.),(0.,0.,4.),(4.,0.,4.),(2.,0.,0.),(2.,0.,4.)]
sids=[0,0,1,1,0,1]
pairs=[[0,1],[2,3],[0,2],[0,4],[4,1],[2,5],[5,3],[4,5]]
paths=[np.array([[float(x),0.,0.] for x in range(5)]),np.array([[float(x),0.,4.] for x in range(5)]),np.array([nodes[0],nodes[2]]),np.array([[float(x),0.,0.] for x in range(3)]),np.array([[float(x),0.,0.] for x in range(2,5)]),np.array([[float(x),0.,4.] for x in range(3)]),np.array([[float(x),0.,4.] for x in range(2,5)]),np.array([nodes[4],nodes[5]])]
lengths=[4.,4.,4.,2.,2.,2.,2.,4.];stairs=[False,False,True,False,False,False,False,True];records=[{'edge_id':2},{'edge_id':7}]
edges,outpaths,outlengths,outstairs=ns['refine_native_path_union'](nodes,sids,pairs,paths,lengths,stairs,records)
def segments(paths):
    return {tuple(sorted((tuple(a),tuple(b)))) for path in paths for a,b in zip(path,path[1:])}
checks={'exact_native_path_geometry_union_preserved':segments(paths)==segments(outpaths),
    'floor_subdivision_union_is_four_edges':sum(not x for x in outstairs)==4,
    'two_authored_stair_links_preserved':sum(outstairs)==2,
    'edge_node_delta_one_after_common_subdivision':abs(len(edges)-(len(nodes)-1))==1,
    'each_stair_record_binds_retained_stair_edge':all(outstairs[r['edge_id']] for r in records),
    'physical_floor_path_length_union_equals_eight':sum(v for v,s in zip(outlengths,outstairs) if not s)==8.}
doc={'status':'PASS' if all(checks.values()) else 'FAIL','execution':'ACTUALLY_EXECUTED_OWN_PATH_SUBDIVISION_HELPER_SMOKE_ONLY',
     'checks':checks,'raw_edge_count':len(pairs),'refined_edge_count':len(edges),'nodes':len(nodes),
     'raw_delta':abs(len(pairs)-(len(nodes)-1)),'refined_delta':abs(len(edges)-(len(nodes)-1)),
     'cannot_support':'Own helper smoke only. No physical multi-stair building, frozen graph gate or building negative control was executed.'}
with OUT.open('x') as f:json.dump(doc,f,indent=2)
print(json.dumps(doc))
raise SystemExit(0 if doc['status']=='PASS' else 1)
