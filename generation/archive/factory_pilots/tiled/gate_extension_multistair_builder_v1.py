"""Factory80 two-storey multi-stair graph assembly through frozen primitives.

Each physical stair gets its own native one-stair extraction and connector.
The native per-floor graph edges are combined without altering their paths,
support, clearances, pruning, tolerances, or deterministic serialization rules.
Only the number of authored stair links is generalized.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
from pathlib import Path
import sys
from typing import Any
import numpy as np
import gate_extension_nstorey_v1 as gate

sys.path.insert(0,str(gate.LEGACY/'evidence/supplemental_sources_r1'))
from build_multistorey_graphs import _load_scratch
from protomotions.simulator.base_simulator import multistorey_topology as native
from protomotions.simulator.base_simulator import scene_storey_bands as bands
from protomotions.simulator.base_simulator.scene_geometry_cache import FINE_VOXEL_SIZE_M
from protomotions.simulator.isaaclab.utils.scene_geometry_baking import (
    _authored_mesh_geometry_m,rasterize_triangle_mesh)
from pxr import Usd,UsdGeom,UsdPhysics
import gate25_storey_navigation as g25_native


def split_stair_mask(stage: Any, prim_path: str, extraction: Any) -> np.ndarray:
    if not (prim_path.startswith('/World/Stair/') or prim_path=='/World/Stair'):
        raise ValueError('Stair paths must be descendants of frozen /World/Stair root')
    root=stage.GetPrimAtPath(prim_path)
    if not root or not root.IsValid():raise ValueError('Missing physical stair root: '+prim_path)
    result=np.zeros(extraction.full_grid_spec.fine_shape,dtype=np.bool_)
    count=0;units=float(UsdGeom.GetStageMetersPerUnit(stage));xforms=UsdGeom.XformCache(Usd.TimeCode.Default())
    for prim in Usd.PrimRange(root):
        if not (prim.IsA(UsdGeom.Mesh) and prim.HasAPI(UsdPhysics.CollisionAPI)):
            continue
        if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:continue
        vertices,faces=_authored_mesh_geometry_m(stage,prim,units,xforms)
        rasterize_triangle_mesh(result,extraction.full_grid_spec,vertices,faces,fill=False)
        count+=1
    if not count or not np.any(result):raise ValueError('Stair has no enabled rasterized mesh collider: '+prim_path)
    return result


def isolated_extraction(extraction: Any, stair_mask: np.ndarray) -> Any:
    first_stop=min(extraction.cut_z_index+1,extraction.full_occupancy.shape[2])
    first,_=bands._crop_grid(extraction.full_grid_spec,stair_mask,0,first_stop)
    second,_=bands._crop_grid(extraction.full_grid_spec,stair_mask,
                             extraction.cut_z_index,stair_mask.shape[2])
    result=replace(extraction,full_stair_mask=stair_mask,bands=(
        replace(extraction.bands[0],stair_mask=first),replace(extraction.bands[1],stair_mask=second)))
    result.validate()
    return result


def refine_native_path_union(nodes,storey_ids,pairs,paths,lengths,stairs,records):
    """Common-subdivide unchanged native paths at native materialized nodes.

    Each one-stair native graph splits its floor spine at a different stair
    connector. Unioning an unsplit edge with the other stair's split version
    would manufacture a duplicate route. This operation preserves the whole
    native path geometry and only reconciles those existing subdivisions.
    It does not prune a distinct path or create a new floor shortcut.
    """
    lookup={}
    for node,(position,sid) in enumerate(zip(nodes,storey_ids)):
        lookup.setdefault((sid,tuple(position)),[]).append(node)
    result_pairs=[];result_paths=[];result_lengths=[];result_stairs=[];keys={};old_to_new={}
    for old_edge,(pair,path,length,is_stair) in enumerate(zip(pairs,paths,lengths,stairs)):
        first,second=map(int,pair);path=np.asarray(path,dtype=np.float64)
        stops=[(0,first)]
        if not is_stair:
            if storey_ids[first]!=storey_ids[second]:raise ValueError('Non-stair path changes storey')
            for index,position in enumerate(path[1:-1],start=1):
                matches=lookup.get((storey_ids[first],tuple(position)),[])
                # Never collapse distinct coincident native node identities.
                if len(matches)==1 and matches[0] not in (first,second):stops.append((index,matches[0]))
        stops.append((len(path)-1,second))
        emitted=[]
        for (begin,start),(end,finish) in zip(stops,stops[1:]):
            segment=np.ascontiguousarray(path[begin:end+1])
            segment_length=float(length) if len(stops)==2 else float(np.linalg.norm(np.diff(segment,axis=0),axis=1).sum())
            if segment_length<=0 or start==finish:raise ValueError('Native path refinement yielded a zero-length/self edge')
            canonical=segment if start<finish else segment[::-1]
            key=(min(start,finish),max(start,finish),canonical.tobytes(),old_edge if is_stair else None)
            if key not in keys:
                keys[key]=len(result_pairs);result_pairs.append([start,finish]);result_paths.append(segment.copy())
                result_lengths.append(segment_length);result_stairs.append(bool(is_stair))
            elif not np.isclose(result_lengths[keys[key]],segment_length,rtol=0,atol=1e-12):
                raise ValueError('Identical native path fragments disagree on walking length')
            emitted.append(keys[key])
        old_to_new[old_edge]=emitted
    for record in records:
        replacement=old_to_new[int(record['edge_id'])]
        if len(replacement)!=1:raise ValueError('Authored stair link was altered by floor path refinement')
        record['edge_id']=replacement[0]
    return result_pairs,result_paths,result_lengths,result_stairs


def merge_native_artifacts(items: list[tuple[str,dict[str,np.ndarray]]], extraction: Any
                           ) -> dict[str,np.ndarray]:
    """Union unchanged native intra-floor paths and M distinct stair links."""
    if not items:raise ValueError('No stair artifacts')
    if len(items)==1:
        # A one-stair graph keeps its original complete serialized bytes.
        return items[0][1]
    result={key:np.array(value,copy=True) for key,value in items[0][1].items()}
    nodes=[];xy=[];storey_ids=[];clearance=[];nodekeys={}
    paths=[];edgepairs=[];lengths=[];stairs=[];edgekeys={};records=[]
    goal=None
    for ordinal,(stair_id,artifact) in enumerate(items):
        native.validate_multistorey_artifact_arrays(artifact)
        for key in ('floor1_topology_npz_bytes','floor2_topology_npz_bytes',
                    'floor1_occupancy','floor2_occupancy'):
            if not np.array_equal(result[key],artifact[key]):
                raise ValueError('Per-stair extraction changed frozen floor topology: '+key)
        mapping={};occurrences={}
        for old_id in range(len(artifact['node_id'])):
            position=tuple(float(x) for x in artifact['node_center_m'][old_id])
            index=tuple(int(x) for x in artifact['node_xy_index'][old_id])
            sid=int(artifact['node_storey_id'][old_id])
            basic=(sid,index,position);occurrence=occurrences.get(basic,0)
            occurrences[basic]=occurrence+1;key=basic+(occurrence,)
            if key not in nodekeys:
                nodekeys[key]=len(nodes);nodes.append(position);xy.append(index)
                storey_ids.append(sid);clearance.append(float(artifact['node_clearance_m'][old_id]))
            else:
                if clearance[nodekeys[key]]!=float(artifact['node_clearance_m'][old_id]):
                    raise ValueError('Shared graph node clearance changed across physical stair extractions')
            mapping[old_id]=nodekeys[key]
        if ordinal==0:goal=mapping[int(np.asarray(artifact['goal_node_id']).item())]
        manifest=native.manifest_from_artifact(artifact);one=dict(manifest['stair_edge'])
        if np.count_nonzero(artifact['edge_is_stair'])!=1:
            raise ValueError('Each native one-stair artifact must contain exactly one actual stair edge')
        for old_edge,pair in enumerate(artifact['edge_node_ids']):
            first,second=[mapping[int(x)] for x in pair]
            start,stop=map(int,artifact['edge_path_offsets'][old_edge:old_edge+2])
            path=np.asarray(artifact['edge_path_xyz_m'][start:stop],dtype=np.float64)
            length=float(artifact['edge_path_length_m'][old_edge]);is_stair=bool(artifact['edge_is_stair'][old_edge])
            if first<second:canonical_path=path
            else:canonical_path=path[::-1]
            key=(min(first,second),max(first,second),length,canonical_path.tobytes(),
                 stair_id if is_stair else None)
            if key not in edgekeys:
                edgekeys[key]=len(edgepairs);edgepairs.append([first,second]);paths.append(path.copy())
                lengths.append(length);stairs.append(is_stair)
            eid=edgekeys[key]
            if is_stair:
                one.update(stair_id=stair_id,edge_id=eid,
                    floor1_node_id=mapping[int(one['floor1_node_id'])],
                    floor2_node_id=mapping[int(one['floor2_node_id'])],
                    lower_node_id=mapping[int(manifest['stair_edge']['floor1_node_id'])],
                    upper_node_id=mapping[int(manifest['stair_edge']['floor2_node_id'])],
                    lower_storey_id=0,upper_storey_id=1)
                records.append(one)
    edgepairs,paths,lengths,stairs=refine_native_path_union(nodes,storey_ids,edgepairs,paths,lengths,stairs,records)
    centers=np.asarray(nodes,dtype='<f8');pairs=np.asarray(edgepairs,dtype='<i4')
    lengths_array=np.asarray(lengths,dtype='<f8')
    geodesic,next_hop=native._geodesics(len(nodes),pairs,lengths_array,centers,goal)
    if not np.all(np.isfinite(geodesic)):raise ValueError('Merged multi-stair graph is not connected')
    offsets=np.zeros(len(paths)+1,dtype='<i8')
    for index,path in enumerate(paths):offsets[index+1]=offsets[index]+len(path)
    result.update(node_id=np.arange(len(nodes),dtype='<i4'),node_storey_id=np.asarray(storey_ids,dtype=np.int8),
        node_xy_index=np.asarray(xy,dtype='<i4'),node_center_m=centers,node_clearance_m=np.asarray(clearance,dtype='<f4'),
        edge_id=np.arange(len(edgepairs),dtype='<i4'),edge_node_ids=pairs,edge_path_offsets=offsets,
        edge_path_xyz_m=np.concatenate(paths,axis=0).astype('<f8'),edge_path_length_m=lengths_array,
        edge_is_stair=np.asarray(stairs,dtype=np.bool_),node_geodesic_to_goal_m=np.asarray(geodesic,dtype='<f8'),
        node_next_hop_xy=np.asarray(next_hop,dtype='<f8'),goal_node_id=np.asarray(goal,dtype='<i4'),
        floor1_stair_mask=np.asarray(extraction.bands[0].stair_mask,dtype=np.bool_),
        floor2_stair_mask=np.asarray(extraction.bands[1].stair_mask,dtype=np.bool_))
    manifest=native.manifest_from_artifact(result)
    manifest['stair_edge']=records[0];manifest['stair_edges']=records
    manifest['factory80_extension']=dict(storeys=2,stairs=len(records),
        scope='M authored stair links; all floor topology and connector primitives remain frozen',
        native_source=gate.ref(native.__file__),adapter=gate.ref(__file__))
    for sid,name in ((0,'Floor1'),(1,'Floor2')):
        manifest['storeys'][name]['node_count']=sum(value==sid for value in storey_ids)
    manifest['goal']['goal_node_id']=goal;manifest['goal']['goal_xy_m']=centers[goal,:2].tolist()
    result['manifest_json_utf8']=np.frombuffer(native.canonical_json_bytes(manifest),dtype=np.uint8).copy()
    result={key:np.ascontiguousarray(result[key]) for key in native.MULTISTOREY_ARTIFACT_KEYS}
    native.validate_multistorey_artifact_arrays(result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--building',required=True);p.add_argument('--root-stage',type=Path,required=True)
    p.add_argument('--scratch',type=Path,required=True);p.add_argument('--stairs-manifest',type=Path,required=True)
    p.add_argument('--decision',type=Path,required=True);p.add_argument('--g25',type=Path,required=True)
    p.add_argument('--stage-d',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    stage_path=a.root_stage.resolve();source=gate.ref(stage_path)
    binding=gate.read_json(a.scratch.with_suffix(a.scratch.suffix+'.binding.json'))
    if binding['source_usdz_sha256']!=source['sha256'] or binding['scratch_sha256']!=gate.sha256(a.scratch):
        raise ValueError('Scratch binding differs from actual root layer')
    extraction=_load_scratch(a.scratch)
    stage=Usd.Stage.Open(str(stage_path),Usd.Stage.LoadAll)
    if stage is None:raise ValueError('Cannot open full composed root layer')
    manifest=gate.read_json(a.stairs_manifest);rows=manifest.get('stairs',manifest.get('rows',[]))
    if len({row['prim_path'] for row in rows})!=len(rows) or len({row['stair_id'] for row in rows})!=len(rows):
        raise ValueError('Physical stair roots and IDs must be unique')
    masks=[];artifacts=[];arrivals=[];g25_each=[]
    all_masks=np.zeros(extraction.full_grid_spec.fine_shape,dtype=np.bool_)
    for row in rows:
        mask=split_stair_mask(stage,row['prim_path'],extraction);all_masks|=mask;masks.append(mask)
        isolated=isolated_extraction(extraction,mask)
        arrays=native.build_multistorey_artifact_arrays(isolated,building=a.building,
            source_usdz_path=stage_path,source_usdz_sha256=source['sha256'],decision_path=a.decision,
            decision_sha256=gate.sha256(a.decision))
        artifacts.append((row['stair_id'],arrays))
        second=isolated.bands[1]
        upper,_=g25_native._floor2_arrival(isolated.full_occupancy,mask,isolated.full_grid_spec,
            isolated.peak_z_index,second.occupancy,second.grid_spec)
        # Each untouched native Isaac report is installed under its stair view.
        report=Path(row['isaac_report']).resolve();report_ref=gate.ref(report)
        if report.name!='isaac_validation.json' or report.parent.name!='reports':
            raise ValueError('Each Isaac input must use its native reports/isaac_validation.json layout')
        lower_xy,_=g25_native._floor1_approach_xy(report.parent.parent)
        lower=(lower_xy[0],lower_xy[1],float(isolated.floor1_support_top_z_m))
        floors=[g25_native.analyze_storey(isolated.bands[0].occupancy,isolated.bands[0].grid_spec,lower,
                    storey='Floor1',enforce_unreachable_component_limit=True),
                g25_native.analyze_storey(second.occupancy,second.grid_spec,upper,
                    storey='Floor2',enforce_unreachable_component_limit=True)]
        g25_each.append(dict(stair_id=row['stair_id'],isaac_report=report_ref,storeys=floors,
                             status='PASS' if all(x['status']=='PASS' for x in floors) else 'FAIL'))
        arrivals.append(dict(stair_id=row['stair_id'],lower_arrival_xyz_m=list(lower),
                             upper_arrival_xyz_m=list(upper),isaac_report=report_ref))
    if not np.array_equal(all_masks,extraction.full_stair_mask):
        raise ValueError('Listed physical stairs do not exactly cover the actual rasterized /World/Stair colliders')
    arrays=merge_native_artifacts(artifacts,extraction)
    final_manifest=native.manifest_from_artifact(arrays)
    records=final_manifest.get('stair_edges',[final_manifest['stair_edge']])
    for row,record in zip(arrivals,records):row['edge_id']=int(record['edge_id'])
    arrival_doc=dict(schema='Factory80MeasuredStairArrivalsV1',rows=arrivals)
    g25=gate.read_json(a.g25);stage_d=gate.read_json(a.stage_d)
    result=gate.gate8(arrays,g25,stage_d,expected_storeys=2,expected_stairs=len(rows),arrivals=arrival_doc)
    payload=native.deterministic_multistorey_npz_bytes(arrays)
    if payload!=native.deterministic_multistorey_npz_bytes(arrays):raise ValueError('Serialization is not deterministic')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('xb') as stream:stream.write(payload)
    loaded=native.load_multistorey_artifact(a.output)
    report=dict(schema='Factory80TwoStoreyMultiStairGraphBuildV1',building=a.building,
        status='PASS' if result['status']=='PASS' and all(x['status']=='PASS' for x in g25_each) else 'FAIL',
        graph=gate.ref(a.output),nodes=len(loaded['node_id']),edges=len(loaded['edge_id']),G8=result,
        arrivals=arrival_doc,g25_per_stair=g25_each,source=source,scratch=gate.ref(a.scratch),
        stairs_manifest=gate.ref(a.stairs_manifest),native=gate.ref(native.__file__),adapter=gate.ref(__file__),
        cannot_support='This builder does not replace G1-G7/G9, stage_d completion, capacity checks, physical negative controls, visual review, or final acceptance.')
    gate.write_new(a.report,report)
    print(dict(status=report['status'],nodes=report['nodes'],stairs=len(rows),output=str(a.output)))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
