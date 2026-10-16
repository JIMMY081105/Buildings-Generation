"""Read each selected candidate's own cached seams without changing gate reports."""
import argparse,hashlib,json,math
import numpy as np
from pathlib import Path
from datetime import datetime,timezone
from factory80_tile_seams_v2 import measure
W=Path('/cephfs/shared/yixing/outputs/factory80_20261011')
def read(p):return json.loads(Path(p).read_text())
def ref(p):
 p=Path(p);h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
 return dict(path=str(p),bytes=p.stat().st_size,sha256=h.hexdigest())
def write(p,v):
 with Path(p).open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def one(root,selected_sha):
 author=root/'AUTHORING.json';source=ref(root/'root.usda')
 assert source['sha256']==selected_sha and read(author)['stage']==source
 d=root/'cpu_geometry';objective=d/'OBJECTIVE_TILING_QUALITY.json';old=d/'DESCRIPTIVE_TILE_SEAMS.json'
 obj=read(objective)
 assert obj['stage']==source
 if obj.get('work_completion')!='MEASURED' or not old.is_file():
  return dict(building=read(author)['building'],source=source,status='NOT EXECUTED',objective=ref(objective),reason='No complete actual original V1 and cached descriptor available; no corrected measurement invented.')
 original_objective=ref(objective);original_descriptor=ref(old)
 result=d/'DESCRIPTIVE_TILE_SEAMS_CORRECTED_V2.json'
 created=False
 if not result.is_file():write(result,measure(author,d));created=True
 before=read(old);after=read(result)
 assert after['source']==source and after['authoring']==ref(author) and after['prior_descriptor']==original_descriptor
 assert after['descriptor_source']==ref(W/'scripts/factory80_tile_seams_v2.py')
 assert after['actual_floor_inventory']==ref(d/'ACTUAL_GEOMETRY_INVENTORY.json')
 inventory=read(d/'ACTUAL_GEOMETRY_INVENTORY.json')
 assert original_descriptor==ref(old) and original_objective==ref(objective)
 assert len(before['storeys'])==len(after['storeys'])==len(obj['voxel_connectivity']['storeys'])
 keys=('first_authored_cell','second_authored_cell','axis','seam_coordinate_m','actual_cardinal_step_valid_crossing_pair_count','actual_component_label_pair_counts','example_actual_pairs')
 rows=[]
 for a,b,v in zip(before['storeys'],after['storeys'],obj['voxel_connectivity']['storeys']):
  assert a['storey']==b['storey']==v['storey']
  assert a['actual_support_cache']==b['actual_support_cache']==ref(a['actual_support_cache']['path'])
  with np.load(a['actual_support_cache']['path'],allow_pickle=False) as cached:
   meta=json.loads(cached['metadata_json_utf8'].tobytes().decode())
  assert meta['source']==source and meta['storey']==v['storey']
  assert meta['native_standability']==after['native_standability']==ref(after['native_standability']['path'])
  surfaces=[s for s in inventory['actual_inventory']['floor_surfaces'] if s['storey']==v['storey']]
  expected=[]
  for surface in surfaces:
   z=float(surface['top_z_m']);voxel=float(meta['fine_voxel_size_m']);aligned=float(z/voxel).is_integer()
   expected.append(dict(actual_mesh_path=surface['path'],actual_top_z_m=z,
    source_plane_exactly_aligned_to_original_voxel_pitch=aligned,
    original_source_cube_upper_target_index=math.floor((z+.5*voxel-meta['grid_origin_m'][2]-voxel*1e-9)/voxel) if aligned else None,
    original_nominal_point_index=math.floor((z-meta['grid_origin_m'][2])/voxel),
    execution='ACTUAL_EXISTING_CACHE_COORDINATE_DESCRIPTION' if aligned else 'UNVERIFIED_NON_GRID_ALIGNED_SOURCE_PLANE'))
  assert b['actual_floor_plane_coordinate_bindings']==expected
  assert b['exact_raster_floor_plane_support_indices']==sorted({x['original_source_cube_upper_target_index'] for x in expected if x['source_plane_exactly_aligned_to_original_voxel_pitch']})
  assert a['actual_native_V1_component_count']==b['actual_native_V1_component_count']==v['component_count']
  assert len(a['seams'])==len(b['seams'])
  assert all({k:x[k] for k in keys}=={k:y[k] for k in keys} for x,y in zip(a['seams'],b['seams']))
  rows.append(dict(storey=v['storey'],original_full_layer_components=v['component_count'],original_full_layer_status=v['status'],
   old_plane_indices=a['exact_raster_floor_plane_support_indices'],actual_candidate_corrected_plane_bindings=b['actual_floor_plane_coordinate_bindings'],
   corrected_plane_indices=b['exact_raster_floor_plane_support_indices'],main_floor_label=b['main_floor_plane_component_label'],seam_count=len(b['seams']),
   actual_crossing_pairs=sum(x['actual_cardinal_step_valid_crossing_pair_count'] for x in b['seams']),
   actual_main_plane_crossings=sum(x['actual_main_floor_plane_crossing_pair_count'] for x in b['seams']),
   main_floor_component_in_each_tile=b['main_floor_component_observed_in_each_authored_tile'],main_floor_seam_tile_adjacency_connected=b['all_authored_tiles_connected_by_measured_main_floor_seams']))
 return dict(building=read(author)['building'],source=source,authoring=ref(author),status='ACTUAL_DESCRIPTOR_CORRECTION_VERIFIED',
  original_objective=original_objective,original_descriptor=original_descriptor,corrected_descriptor=ref(result),actual_new_corrected_file_written=created,
  original_crossing_counts_label_pairs_examples_exactly_unchanged=True,full_layer_gate_verdict_unchanged=True,storeys=rows)
def main():
 p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group(required=True)
 g.add_argument('--selection',type=Path);g.add_argument('--authoring',type=Path)
 p.add_argument('--output',type=Path,required=True);a=p.parse_args();targets=[]
 if a.selection:
  selection=read(a.selection)
  assert a.selection.resolve()==(W/'work/gate_audit/CPU_PILOT_FINAL_INPUTS.json').resolve()
  assert selection['schema']=='Factory80PilotSelectedCpuInputsV1' and len(selection['rows'])==6
  assert {r['building'] for r in selection['rows']}=={f'factory80_{i:03d}' for i in range(1,7)}
  for r in sorted(selection['rows'],key=lambda r:r['building']):
   assert type(r['attempt']) is int and 1<=r['attempt']<=3
   targets.append((W/'buildings'/r['building']/f"attempt_{r['attempt']:02d}",r['source_stage_sha256']))
 else:
  assert a.authoring.name=='AUTHORING.json' and a.authoring.parent.resolve().is_relative_to((W/'buildings').resolve())
  targets=[(a.authoring.parent,read(a.authoring)['stage']['sha256'])]
 rows=[]
 for root,sha in targets:
  try:rows.append(one(root,sha))
  except (OSError,ValueError,KeyError,AssertionError) as e:
   rows.append(dict(attempt_path=str(root),expected_source_sha256=sha,status='UNVERIFIED',actual_error=repr(e),cannot_support='No fabricated descriptor, acceptance or full-layer single-component result.'))
 doc=dict(schema='Factory80ActualCorrectedTileSeamBatchVerificationV1',utc=datetime.now(timezone.utc).isoformat(),selection=ref(a.selection) if a.selection else None,
  scope='DECLARED_SIX_FINAL_CANDIDATE_INPUTS' if a.selection else 'ONE_ACTUAL_CANDIDATE_CHECK_NOT_A_SIX_BUILDING_SELECTION',actual_candidate_count=len(targets),rows=rows,
  no_original_gate_reports_modified=True,no_new_PASS_threshold=True,
  cannot_support='Only original cached support/labels and exact source cube upper index describe authored tile adjacency. This does not replace whole-layer V1, geometry, native traversal, fire compliance or final acceptance.')
 write(a.output,doc);print(json.dumps(dict(output=ref(a.output),actual_candidate_count=len(rows),rows=rows)))
 return 0 if all(r['status']=='ACTUAL_DESCRIPTOR_CORRECTION_VERIFIED' for r in rows) else 1
if __name__=='__main__':raise SystemExit(main())
