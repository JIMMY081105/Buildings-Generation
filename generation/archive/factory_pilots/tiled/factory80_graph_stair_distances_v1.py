"""Descriptive actual graph-node distances to nearest stair landing per floor."""
import argparse
import heapq
import math
from pathlib import Path
import numpy as np

def measure(arrays):
    storeys=np.asarray(arrays['node_storey_id']);pairs=np.asarray(arrays['edge_node_ids'])
    lengths=np.asarray(arrays['edge_path_length_m'],dtype=np.float64);stair=np.asarray(arrays['edge_is_stair'],dtype=np.bool_)
    node_ids=np.asarray(arrays['node_id']);centers=np.asarray(arrays['node_center_m'])
    if not np.array_equal(node_ids,np.arange(len(node_ids))):raise ValueError('Actual artifact node IDs must index their arrays')
    if len(pairs)!=len(lengths) or len(stair)!=len(pairs):raise ValueError('Actual graph edge arrays differ in length')
    if np.any(~np.isfinite(lengths)) or np.any(lengths<=0):raise ValueError('Actual graph edge lengths must remain finite and positive')
    adjacency=[[] for _ in node_ids];skipped_crossstorey=[]
    for eid,(pair,length,is_stair) in enumerate(zip(pairs,lengths,stair)):
        first,second=map(int,pair)
        if is_stair:continue
        if storeys[first]!=storeys[second]:skipped_crossstorey.append(eid);continue
        adjacency[first].append((second,float(length)));adjacency[second].append((first,float(length)))
    rows=[]
    for sid in sorted(set(map(int,storeys))):
        nodes=np.flatnonzero(storeys==sid);sources=sorted({int(node) for pair in pairs[stair] for node in pair if int(storeys[int(node)])==sid})
        distances=np.full(len(node_ids),np.inf,dtype=np.float64);nearest=np.full(len(node_ids),-1,dtype=np.int64);heap=[]
        for source in sources:distances[source]=0.;nearest[source]=source;heapq.heappush(heap,(0.,source,source))
        while heap:
            current_distance,source,current=heapq.heappop(heap)
            if current_distance!=distances[current] or source!=nearest[current]:continue
            for neighbor,length in adjacency[current]:
                candidate=current_distance+length
                if candidate<distances[neighbor] or (candidate==distances[neighbor] and source<nearest[neighbor]):
                    distances[neighbor]=candidate;nearest[neighbor]=source;heapq.heappush(heap,(candidate,source,neighbor))
        finite=distances[nodes][np.isfinite(distances[nodes])]
        statistics={name:None for name in ('min_m','max_m','median_m','p95_m')}
        if len(finite):statistics=dict(min_m=float(finite.min()),max_m=float(finite.max()),median_m=float(np.median(finite)),p95_m=float(np.quantile(finite,.95)))
        rows.append(dict(storey_id=sid,node_count=len(nodes),covered_node_count=len(finite),no_path_node_count=len(nodes)-len(finite),
            stair_landing_node_ids=sources,shortest_graph_distance_statistics=statistics,
            per_node=[dict(node_id=int(node),node_center_xyz_m=centers[node].tolist(),nearest_stair_landing_node_id=int(nearest[node]) if nearest[node]>=0 else None,
                shortest_graph_distance_to_nearest_landing_m=float(distances[node]) if math.isfinite(distances[node]) else None) for node in nodes]))
    return dict(schema='Factory80DescriptiveActualGraphStairDistancesV1',status='MEASURED',
        method='Delete all actual stair edges; per-storey multi-source Dijkstra on actual positive edge path lengths',
        threshold_or_acceptance_rule_added=False,all_stair_edges_removed_count=int(stair.sum()),
        nonstair_crossstorey_edge_ids_excluded_from_per_floor_statistics=skipped_crossstorey,storeys=rows,
        cannot_support='Distances describe only the supplied graph nodes and graph edge paths. They are not continuous floor coverage, evacuation distances, fire-code compliance, a new gate, or candidate acceptance.')

def main():
    import gate_extension_nstorey_v1 as gate
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--graph',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    arrays=gate.frozen.load_multistorey_artifact(a.graph);doc=measure(arrays);doc['graph']=gate.ref(a.graph);doc['helper']=gate.ref(__file__)
    gate.write_new(a.output,doc)
    print(dict(status=doc['status'],storeys=[dict(storey_id=r['storey_id'],node_count=r['node_count'],max_m=r['shortest_graph_distance_statistics']['max_m'],no_path_node_count=r['no_path_node_count']) for r in doc['storeys']]))
    return 0
if __name__=='__main__':raise SystemExit(main())
