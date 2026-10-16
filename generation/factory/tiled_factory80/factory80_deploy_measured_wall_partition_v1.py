"""Deploy the owned author revision only after actual frozen fixture equality."""
import difflib,hashlib,json,os,subprocess
from pathlib import Path
from datetime import datetime,timezone
W=Path('/cephfs/shared/yixing/outputs/factory80_20261011');CPU='/cephfs/shared/yixing/envs/main/bin/python'
def ref(p):
 p=Path(p);b=p.read_bytes();return dict(path=str(p),bytes=len(b),sha256=hashlib.sha256(b).hexdigest())
def read(p):return json.loads(Path(p).read_text())
def main():
 base=W/'work/gate_audit/wall_partition_revision';before=base/'baseline_build_factory80_overlay.py';candidate=base/'candidate_build_factory80_overlay.py';official=W/'scripts/build_factory80_overlay.py'
 assert ref(before)['sha256']=='6f3d44e6dac236db0adc1ed2cdeda761df616a8297eeb9446276a796c9a9c1c7'
 assert ref(candidate)['sha256']=='f06f863bd063b13f85b8e53227f1534a6bc27c0227294824f296658234b5048d'
 assert (ref(official)['sha256'],ref(official)['bytes'])==(ref(before)['sha256'],ref(before)['bytes'])
 D=W/'work/gate_audit/wall_partition_equivalence_r1';terminal=read(D/'ACTUAL_FIXTURE_TERMINAL.json');result=read(D/'ACTUAL_EQUIVALENCE_RESULT.json')
 assert terminal['status']=='ACTUAL_FIXTURE_COMPLETE' and len(terminal['actual_execution_refs'])==8
 for r in terminal['actual_execution_refs']:
  assert ref(r['path'])==r;d=read(r['path']);assert d['actual_linux_parent_wait_status'] is True and d['actual_phase_returncode']==0
 assert result['status']=='ACTUAL_EXACT_FIXTURE_EQUIVALENCE' and len(result['actual_comparisons'])==3
 for d in result['actual_comparisons']:
  assert d['missing_voxel_centers']==0 and d['extra_voxel_centers']==0
  assert all(d[k] is True for k in ('exact_5cm_occupied_center_set_equal','expanded_world_triangles_exactly_equal','bounds_exactly_equal','signed_volume_exactly_equal','material_paths_equal','beauty_visual_vertices_faces_exactly_equal'))
 assert ref(result['input_manifest']['path'])==result['input_manifest']
 inputs=read(result['input_manifest']['path']);assert inputs['original_owned_author_source']==ref(before) and inputs['partition_owned_author_source']==ref(candidate)
 frozen=ref(result['frozen_voxelizer_after']['path']);assert frozen==result['frozen_voxelizer_after']==inputs['frozen_voxelizer_source']
 assert frozen['sha256']=='2c12e261e80ded1351610083562425087833f3d3159c39c4175d9d268f73e96e'
 diff=''.join(difflib.unified_diff(before.read_text().splitlines(True),candidate.read_text().splitlines(True),fromfile=str(before),tofile=str(candidate)))
 diff_path=base/'OWN_WALL_PARTITION_AUTHOR_EXACT_DIFF.patch'
 with diff_path.open('x') as f:f.write(diff)
 temporary=W/'scripts/.OWN_WALL_PARTITION_MEASURED_DEPLOY.py'
 with temporary.open('xb') as f:f.write(candidate.read_bytes())
 assert ref(temporary)['sha256']==ref(candidate)['sha256']
 os.replace(temporary,official)
 actual_compile=subprocess.run([CPU,'-m','py_compile',str(official)],capture_output=True,text=True)
 assert actual_compile.returncode==0,actual_compile.stderr
 assert ref(official)==dict(ref(candidate),path=str(official)) and ref(result['frozen_voxelizer_after']['path'])==frozen
 receipt=W/'evidence/gate_audit/OWN_WALL_PARTITION_MEASURED_AUTHOR_DEPLOYMENT.json'
 d=dict(schema='Factory80OwnedWallPartitionMeasuredAuthorDeploymentV1',utc=datetime.now(timezone.utc).isoformat(),status='ACTUAL_FIXTURE_VERIFIED_OWN_AUTHOR_DEPLOYED',
  original_owned_author=ref(before),staged_owned_author=ref(candidate),actual_deployed_owned_author=ref(official),exact_diff=ref(diff_path),actual_remote_compile_returncode=actual_compile.returncode,
  actual_fixture_terminal=ref(D/'ACTUAL_FIXTURE_TERMINAL.json'),actual_fixture_equivalence=ref(D/'ACTUAL_EQUIVALENCE_RESULT.json'),actual_compared_wall_results=result['actual_comparisons'],
  actual_frozen_voxelizer_before=inputs['frozen_voxelizer_source'],actual_frozen_voxelizer_after=frozen,frozen_voxelizer_unchanged=True,
  original_collision_root_mesh_geometry_retained=True,beauty_visual_paths_and_geometry_retained=True,existing_candidate_root_changed=False,source_or_native_asset_changed=False,
  actual_fixture_scope=result['measured_scope'],whole_building_memory_improvement='UNVERIFIED',candidate_gate_pass_claim=False,actual_negative_control_executed=False)
 with receipt.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
 print(json.dumps(dict(actual_deployment=ref(receipt),actual_author=ref(official),actual_wall_center_counts=[r['actual_occupied_original'] for r in result['actual_comparisons']]),indent=2))
if __name__=='__main__':main()
