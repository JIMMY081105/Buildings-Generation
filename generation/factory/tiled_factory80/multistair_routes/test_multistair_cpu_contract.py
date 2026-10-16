"""Executable failure cases for the new full-M ownership/input boundary."""
import copy, json
from multistair_cpu_contract import (clean_terminal, stable_result,
    stair_inventory, negative_escapes, qualified_b2, check_structural_scope, frame_author_matches)


def run():
    passed = []
    def rejects(name, fn):
        try:
            fn()
        except ValueError as error:
            passed.append(dict(test=name, observed='REJECTED', reason=str(error)))
        else:
            raise AssertionError('Invalid fixture escaped: '+name)
    root = dict(path='/actual/root.usda', bytes=12, sha256='a'*64)
    row = dict(building='factory80_001', root_ref=root)
    local = [dict(stair_id=s, prim_path='/World/Stair/world') for s in ['S01', 'S02']]
    frame = dict(building=row['building'], original_candidate=root, stairs=local)
    world = dict(building=row['building'], stairs=[
        dict(stair_id='S01', prim_path='/World/Stair/world'),
        dict(stair_id='S02', prim_path='/World/Stair/S02/world')])
    pairs = stair_inventory(frame, world, row, 2)
    assert pairs[1][0]['prim_path'] == '/World/Stair/S02/world'
    passed.append(dict(test='repeated_LOCAL_frame_paths_use_distinct_WORLD_paths', observed='PASS'))
    bad = copy.deepcopy(world)
    bad['stairs'][1]['prim_path'] = bad['stairs'][0]['prim_path']
    rejects('duplicate_physical_stair_path', lambda: stair_inventory(frame, bad, row, 2))
    wrong = copy.deepcopy(frame)
    wrong['original_candidate']['sha256'] = 'b'*64
    rejects('different_current_root', lambda: stair_inventory(wrong, world, row, 2))
    rejects('missing_physical_stair', lambda: stair_inventory(frame, world, row, 3))
    t = dict(child_started=True, actual_signed_returncode=1, source_unchanged=True,
             own_RSS_budget_terminated=False, own_watchdog_failure=False)
    clean_terminal(t)
    passed.append(dict(test='actual_clean_business1_remains_eligible_for_independent_phases', observed='PASS'))
    rejects('missing_terminal', lambda: clean_terminal(None))
    rejects('RSS_terminated_is_not_science', lambda: clean_terminal(dict(t, own_RSS_budget_terminated=True)))
    rejects('signal_negative_returncode_preserved', lambda: clean_terminal(dict(t, actual_signed_returncode=-9)))
    stable = dict(current_root_ref=root, source_unchanged=True,
                  source_refs_before=[root], source_refs_after=[root])
    rejects('changed_source_vector', lambda: stable_result(dict(stable, source_refs_after=[]), root))
    escaped = negative_escapes(dict(reports=[dict(negative_controls=[dict(status='DID_NOT_FAIL_GATE_IS_INVALID')])]))
    assert len(escaped) == 1
    assert negative_escapes(dict(status='FAIL', negative_controls=[dict(status='FAIL_AS_REQUIRED')])) == []
    passed.append(dict(test='nested_ingest_escape_observed', observed='PASS'))
    assert not qualified_b2(dict(status='PASS', pairs=199), 0)
    rejects('B2_exit_report_disagreement', lambda: qualified_b2(dict(status='PASS', pairs=2000), 1))
    passed.append(dict(test='B2_under200_positive_cannot_grade_N2_2', observed='PASS'))
    density = dict(building=row['building'], storeys=2, schema='Factory80OwnedN2DensityDerivedAuthoringV1')
    raw = dict(building=row['building'], storeys=2, schema='Factory80RootOverlayAuthoringV1', stair_count=2, stairs=[{}, {}])
    plan = dict(building=row['building'], storeys=2, planned_stair_count=2)
    check_structural_scope(density, raw, plan)
    passed.append(dict(test='density_missing_count_uses_bound_original_structure', observed='PASS'))
    rejects('arbitrary_missing_structural_metadata', lambda: check_structural_scope(dict(density, schema='UNREGISTERED'), raw, plan))
    rejects('wrong_current_structural_count', lambda: check_structural_scope(dict(density, stair_count=3), raw, plan))
    rr = dict(row, authoring_ref=dict(path='/actual/density.json'), raw_authoring_ref=dict(path='/actual/raw.json'))
    ff = dict(frame, actual_source_authoring=dict(path='/actual/current_stage_d.json'))
    aa = dict(building=row['building'], storeys=2, stage=root,
        schema='Factory80OwnedCurrentGeometryStageDInputV1', current_geometry_authoring_ref=rr['authoring_ref'],
        original_structural_authoring_ref=rr['raw_authoring_ref'], historical_scientific_results_inherited=False)
    assert frame_author_matches(rr, ff, aa)
    assert not frame_author_matches(rr, ff, dict(aa, current_geometry_authoring_ref=dict(path='/wrong.json')))
    passed.append(dict(test='current_frame_density_lineage_accepts_exact_and_rejects_wrong_source', observed='PASS'))
    return dict(status='PASS', cases=passed, real_building_scientific_gates='NOT_EXECUTED')


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
