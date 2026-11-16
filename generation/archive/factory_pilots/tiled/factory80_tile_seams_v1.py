"""Describe real cached native V1 crossings at authored tile seams. No gate verdict."""
import argparse,hashlib,json,sys
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
REPO=Path('/cephfs/shared/yixing/code/Scenesmith-code-study/ProtoMotions-main')
sys.path.insert(0,str(REPO))
from protomotions.simulator.base_simulator.standability import StandabilityConfig,CARDINAL_NEIGHBORS
V1=REPO/'protomotions/simulator/base_simulator/standability.py'
V1_SHA='6c120102d4d11829db2882a73ce2db1cde590c95d271c493a5e261413e2f3e7f'
def read(path):return json.loads(Path(path).read_text())
def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(8*1024**2),b''):digest.update(chunk)
    return digest.hexdigest()
def ref(path):
    p=Path(path);return dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p))
def reachable_tiles(cells,edges):
    if not cells:return []
    adjacency={cell:set() for cell in cells}
    for first,second in edges:adjacency[first].add(second);adjacency[second].add(first)
    components=[];unseen=set(cells)
    while unseen:
        seed=min(unseen);unseen.remove(seed);todo=[seed];found=[]
        while todo:
            cell=todo.pop();found.append(list(cell))
            for neighbor in adjacency[cell]&unseen:unseen.remove(neighbor);todo.append(neighbor)
        components.append(sorted(found))
    return components
def measure(authoring,geometry_dir):
    receipt=read(authoring);source=ref(receipt['stage']['path'])
    if source['sha256']!=receipt['stage']['sha256']:raise ValueError('Actual source SHA changed')
    if sha(V1)!=V1_SHA:raise ValueError('Frozen Standability V1 source differs')
    inventory_path=geometry_dir/'ACTUAL_GEOMETRY_INVENTORY.json';inventory=read(inventory_path)
    if inventory['stage']['sha256']!=source['sha256']:raise ValueError('Actual floor inventory source differs')
    pitch=tuple(map(float,receipt['tile_pitch_m']));config=StandabilityConfig();config.validate(.05)
    if not ((1,0) in CARDINAL_NEIGHBORS and (0,1) in CARDINAL_NEIGHBORS):raise ValueError('Native cardinal schema differs')
    rows=[]
    for sid,name in enumerate(('Floor1','Floor2')):
        cache_path=geometry_dir/('ACTUAL_VOXEL_SUPPORT_'+name+'.npz')
        actual_support_receipt=read(geometry_dir/('ACTUAL_VOXEL_STANDABILITY_'+name+'.json'))
        if ref(cache_path)!=actual_support_receipt['actual_native_support_diagnostic_cache']:
            raise ValueError('Actual cached support bytes differ from the recorded native execution')
        with np.load(cache_path,allow_pickle=False) as cached:
            support=cached['support_z_index'];labels=cached['component_labels']
            meta=json.loads(cached['metadata_json_utf8'].tobytes().decode())
        if meta['source']['sha256']!=source['sha256'] or meta['storey']!=name:raise ValueError('Actual support source/storey binding differs')
        if meta['native_standability']['sha256']!=V1_SHA or sha(meta['native_components']['path'])!=meta['native_components']['sha256']:
            raise ValueError('Native V1/component source binding differs')
        if support.shape!=labels.shape or support.ndim!=2:raise ValueError('Actual native support/label shape differs')
        voxel=float(meta['fine_voxel_size_m']);origin=np.asarray(meta['grid_origin_m'],dtype=float)
        max_step_cells=int(round(config.max_support_step_m/voxel))
        x_centers=origin[0]+(np.arange(support.shape[0])+.5)*voxel
        y_centers=origin[1]+(np.arange(support.shape[1])+.5)*voxel
        floor_surfaces=[item for item in inventory['actual_inventory']['floor_surfaces'] if item['storey']==name]
        plane_indices=sorted({int(np.floor((item['top_z_m']-origin[2])/voxel)) for item in floor_surfaces})
        plane_cells=np.isin(support,plane_indices)&(support>=0)
        floor_labels,floor_counts=np.unique(labels[plane_cells],return_counts=True)
        floor_label=int(floor_labels[np.argmax(floor_counts)]) if len(floor_labels) else None
        roster=[item for item in receipt['source_inventory'] if int(item.get('floor_id',item.get('storey')))==sid]
        cells={tuple(map(int,item['cell'])) for item in roster}
        tile_rows=[]
        for cell in sorted(cells):
            ix=np.flatnonzero((x_centers>=cell[0]*pitch[0])&(x_centers<(cell[0]+1)*pitch[0]))
            iy=np.flatnonzero((y_centers>=cell[1]*pitch[1])&(y_centers<(cell[1]+1)*pitch[1]))
            region=np.ix_(ix,iy);label_values=labels[region];support_values=support[region]
            count=int(np.count_nonzero((label_values==floor_label)&np.isin(support_values,plane_indices))) if floor_label else 0
            tile_rows.append(dict(authored_cell=list(cell),actual_native_floor_plane_cells_in_main_floor_component=count,
                actual_component_labels_present=sorted(map(int,np.unique(label_values[label_values>0])))))
        seam_rows=[];main_edges=[]
        for cell in sorted(cells):
            for axis,neighbor in ((0,(cell[0]+1,cell[1])),(1,(cell[0],cell[1]+1))):
                if neighbor not in cells:continue
                coordinate=(cell[axis]+1)*pitch[axis]
                centers=(x_centers,y_centers)[axis];left=int(np.searchsorted(centers,coordinate,side='left'))-1;right=left+1
                other=1-axis;other_centers=(x_centers,y_centers)[other]
                candidates=np.flatnonzero((other_centers>=cell[other]*pitch[other])&(other_centers<(cell[other]+1)*pitch[other]))
                pairs=[];examples=[];main_count=0;label_counts=[]
                if 0<=left<right<support.shape[axis] and len(candidates):
                    first=(np.full(len(candidates),left,dtype=int),candidates) if axis==0 else (candidates,np.full(len(candidates),left,dtype=int))
                    second=(np.full(len(candidates),right,dtype=int),candidates) if axis==0 else (candidates,np.full(len(candidates),right,dtype=int))
                    a=support[first];b=support[second];la=labels[first];lb=labels[second]
                    valid=(a>=0)&(b>=0)&(np.abs(a.astype(np.int64)-b.astype(np.int64))<=max_step_cells)
                    indices=np.flatnonzero(valid)
                    if len(indices):
                        keys,counts=np.unique(np.stack((la[valid],lb[valid]),axis=1),axis=0,return_counts=True)
                        label_counts=[dict(first_component_label=int(key[0]),second_component_label=int(key[1]),cardinal_step_valid_pair_count=int(count)) for key,count in zip(keys,counts)]
                        main_count=int(np.count_nonzero(valid&(la==floor_label)&(lb==floor_label)&np.isin(a,plane_indices)&np.isin(b,plane_indices))) if floor_label else 0
                        for index in indices[np.unique(np.linspace(0,len(indices)-1,min(3,len(indices)),dtype=int))]:
                            ij1=(int(first[0][index]),int(first[1][index]));ij2=(int(second[0][index]),int(second[1][index]))
                            xyz1=(origin+np.array([ij1[0]+.5,ij1[1]+.5,int(a[index])+1])*voxel).tolist()
                            xyz2=(origin+np.array([ij2[0]+.5,ij2[1]+.5,int(b[index])+1])*voxel).tolist()
                            examples.append(dict(first_grid_xy=list(ij1),second_grid_xy=list(ij2),first_world_support_top_xyz_m=xyz1,
                                second_world_support_top_xyz_m=xyz2,actual_component_labels=[int(la[index]),int(lb[index])]))
                    pairs=indices.tolist()
                if main_count:main_edges.append((cell,neighbor))
                seam_rows.append(dict(first_authored_cell=list(cell),second_authored_cell=list(neighbor),axis='XY'[axis],seam_coordinate_m=coordinate,
                    actual_cardinal_step_valid_crossing_pair_count=len(pairs),actual_main_floor_plane_crossing_pair_count=main_count,
                    actual_component_label_pair_counts=label_counts,example_actual_pairs=examples))
        tile_components=reachable_tiles(cells,main_edges)
        rows.append(dict(storey=name,actual_native_V1_component_count=meta['component_count'],actual_support_cache=ref(cache_path),
            measured_floor_surfaces=floor_surfaces,exact_raster_floor_plane_support_indices=plane_indices,
            main_floor_plane_component_label=floor_label,main_floor_label_selection='Label containing most native support cells in the actual floor-plane raster indices; diagnostic only',
            authored_source_tile_cells=sorted(map(list,cells)),tiles=tile_rows,seams=seam_rows,
            actual_main_floor_tile_adjacency_components=tile_components,
            main_floor_component_observed_in_each_authored_tile=bool(tile_rows) and all(item['actual_native_floor_plane_cells_in_main_floor_component']>0 for item in tile_rows),
            all_authored_tiles_connected_by_measured_main_floor_seams=bool(cells) and len(tile_components)==1))
    after=ref(receipt['stage']['path'])
    if after!=source:raise ValueError('Actual source changed during read-only descriptor')
    return dict(schema='Factory80DescriptiveActualNativeTileSeamsV1',status='MEASURED',building=receipt['building'],
        generated_at_utc=datetime.now(timezone.utc).isoformat(),source=source,authoring=ref(authoring),actual_floor_inventory=ref(inventory_path),
        native_standability=ref(V1),native_cardinal_neighbors=list(map(list,CARDINAL_NEIGHBORS)),native_max_support_step_m=config.max_support_step_m,
        storeys=rows,original_full_layer_component_acceptance_not_changed=True,no_new_PASS_threshold=True,
        cannot_support='Seam counts describe actual native V1 support-cell adjacency at authored cell boundaries. Main floor labels only explain the descriptor; equipment/rail components remain in original full-layer counts. This does not establish single-component acceptance, continuous full-floor reachability, fire compliance, Isaac traversal or formal acceptance.')
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--authoring',type=Path,required=True);parser.add_argument('--geometry-dir',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);a=parser.parse_args()
    doc=measure(a.authoring,a.geometry_dir)
    with a.output.open('x') as handle:json.dump(doc,handle,indent=2)
    print(json.dumps(dict(status='MEASURED',building=doc['building'],output=str(a.output))))
    return 0
if __name__=='__main__':raise SystemExit(main())
