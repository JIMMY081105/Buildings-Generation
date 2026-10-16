"""Full-M CPU assembly and original B2, using genuine current scratch/frames.

This entry calls unchanged scientific functions. It does not run the original
builder main, full native G25, Stage D, G8, or G13. Those GPU-dependent verdicts
are explicitly left unclaimed. Temporary large arrays use local memmaps.
"""
import argparse, gc, json, os, resource, shutil, sys, tempfile, traceback, zipfile
from pathlib import Path
W = Path('/cephfs/shared/yixing/outputs/factory80_20261011')
I = W/'work/P1_IMPLEMENTATION_20261015'
R3 = W/'scripts/cpu_all_serial_20261015_R3'
V = W/'scripts/current_variants_cpu_20261015_R1'
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(R3), str(V), str(I)]
from cpu_all_serial import read, actual_terminal, qualification, stop, SHARED_STOP
from owned_memory_guard import dump, self_oom_priority
from stat_hash_cache import StatHashCache
from multistair_cpu_contract import (require, clean_terminal, stable_result,
    stair_inventory, negative_escapes, qualified_b2, check_structural_scope, frame_author_matches)
from current_full_m_source_binding import bind_existing_support
from native_path_cpu_projection import project_floor1


def main():
    self_oom_priority()
    p = argparse.ArgumentParser(allow_abbrev=False)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--expected-input-sha256', required=True)
    p.add_argument('--expected-index-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    C = StatHashCache([W/'work/P1_HASH_CACHE_20261015.json',
        W/'work/P2_COLLISION_REPAIR_20261015/HASH_CACHE.json',
        W/'work/CURRENT001_A1_NAV_INPUTS_20261015/ACTUAL_STAT_HASH_CACHE.json',
        W/'work/P5_R4_NEW_GEOMETRY_CHAIN_20261015_R3/original_subject/ACTUAL_STAT_HASH_CACHE.json',
        W/'work/P3_HASH_CACHE_20261015.json'])
    ix = C.ref(HERE/'SOURCE_INDEX.json')
    require(ix['sha256'] == a.expected_index_sha256, 'Full-M source index differs')
    idx = read(ix['path'])
    protected = [ix, *idx['runtime_refs'], *idx['dependency_refs']]
    def checked(r):
        require(C.ref(r['path']) == r, 'Actual source changed: '+r['path'])
        return r
    for r in protected:
        checked(r)
    ir = C.ref(a.input)
    require(ir['sha256'] == a.expected_input_sha256, 'Actual full-M input changed')
    inp = read(a.input)
    row = read(checked(inp['world_input_ref'])['path'])
    require(row['building'] in idx['authorized_29'] and row['attempt'] in (1, 2, 3),
            'Only authorized29 original N2 recipes')
    require(not stop(), 'Existing STOP')
    D = a.output.resolve()
    require(D.is_relative_to(W/'work') and not D.exists(), 'Fresh W/work output required')
    D.mkdir()
    resource.setrlimit(resource.RLIMIT_AS, (28*1024**3, 28*1024**3))
    import cpu_all_world as m
    m.C = C
    plan = read(checked(row['original_n2_plan_ref'])['path'])
    mainrow = next(r for r in plan['rows'] if r['building'] == row['building'])
    kind = row['world_binding_kind']
    if kind == 'EXISTING_CURRENT_SUPERVISED_SUPPORT':
        author, raw, proof, producer = bind_existing_support(m, row, mainrow)
    elif kind == 'GUARDED_REPEAT8_DENSITY':
        from current_variant_cpu import guarded_density_binding
        author, raw, proof, producer = guarded_density_binding(row, mainrow, C, protected)
    elif kind == 'PROCEDURAL_HIGH_PIECES':
        proof = m.checked_index(m.WORLD_BINDER/'SOURCE_INDEX.json',
            'c5c5222499e335e0514d2904698e5599e732cf2e17cd20a05be6e279625528ca')
        sys.path.insert(0, str(m.WORLD_BINDER))
        from current_factory80_world_binding import bind_current_world
        info, refs = bind_current_world(row, mainrow, C)
        author, raw = read(row['authoring_ref']['path']), info['raw_source_authoring']
        proof += refs + row['source_dependencies']
        producer = None
    else:
        author, raw, proof, producer = m.bind(row, mainrow)
    check_structural_scope(author, raw, mainrow)
    protected += [ir, inp['world_input_ref'], row['original_n2_plan_ref'],
                  row['root_ref'], row['authoring_ref'], row['raw_authoring_ref'], *proof]
    if inp['scratch_mode'] == 'EXISTING_GENUINE_ORIGINAL':
        require(row['building'] in ('factory80_001', 'factory80_002') and producer,
                'Only reviewed pilot genuine original reuse before002 qualification')
        scratch = checked(C.ref(producer['actual_original_scratch']['scratch']))
        require(scratch['sha256'] == producer['actual_original_scratch']['scratch_sha256'],
                'Genuine original scratch bytes differ')
        floors = producer['support_rows']
        protected += producer['actual_original_scratch_refs']
    else:
        protected += qualification(C)
        up = Path(inp['upstream_guard'])
        clean_terminal(actual_terminal(up))
        fr = checked(inp['upstream_result_ref'])
        result = read(fr['path'])
        stable_result(result, row['root_ref'])
        protected += [fr, *result['source_refs_before'],
                      *[C.ref(up/n) for n in ('BEGIN.json', 'TRUE_WAIT.json', 'FINITE_RESULT.json')]]
        sr = result['phases']['scratch_V1']
        if sr.get('scratch_ref'):
            scratch = checked(sr['scratch_ref'])
            binding = checked(C.ref(scratch['path']+'.binding.json'))
            b = read(binding['path'])
            require(b['schema'] == 'Factory80QualifiedBoundedScratchBindingV2'
                    and b['current_root_ref'] == row['root_ref'] and b['scratch_ref'] == scratch,
                    'Qualified current bounded scratch binding differs')
            protected.append(binding)
        else:
            require(producer is not None, 'Reused upstream scratch needs genuine producer')
            scratch = checked(C.ref(producer['actual_original_scratch']['scratch']))
            require(scratch['sha256'] == producer['actual_original_scratch']['scratch_sha256'],
                    'Genuine reused original scratch differs')
            protected += producer['actual_original_scratch_refs']
        floors = sr['support_rows']
    require([f['storey'] for f in floors] == ['Floor1', 'Floor2'], 'Both exact support rows required')
    protected += [scratch, *[f['support_ref'] for f in floors]]
    mf = checked(inp['frame_manifest_ref'])
    frame = read(mf['path'])
    if inp.get('frame_result_ref'):
        clean_terminal(actual_terminal(inp['frame_guard']))
        rr = checked(inp['frame_result_ref'])
        result = read(rr['path'])
        stable_result(result, row['root_ref'])
        require(result['frame_manifest_ref'] == mf and result['current_authoring_ref'] == row['authoring_ref'],
                'Completed current FrameR2 identity differs')
        protected += [rr, *result['source_refs_before']]
        protected += [C.ref(Path(inp['frame_guard'])/n)
                      for n in ('BEGIN.json', 'TRUE_WAIT.json', 'FINITE_RESULT.json')]
    else:
        registered = next(r for r in idx['existing_completed_frames'] if r['manifest_ref'] == mf)
        fa = checked(frame['actual_source_authoring'])
        require(frame_author_matches(row, frame, read(fa['path'])), 'Registered current frame author lineage differs')
        protected.append(fa)
        protected += registered['completion_refs']
    worldref = checked(frame['actual_world_manifest'])
    pairs = stair_inventory(frame, read(worldref['path']), row, raw['stair_count'])
    protected += [mf, worldref]
    for world, local in pairs:
        require(world['traversal_path'] == local['traversal_path'], 'World/local traversal differs')
        wb, lb = read(world['build_report']), read(local['build_report'])
        require(all(wb[k] == lb[k] for k in ('stair_offset', 'stair_rotation_deg', 'world_path')),
                'N2 world/local measured path differs')
        require(wb['building'] == row['building'] and wb['sources_unmodified'] is True,
                'Measured world build does not identify source building')
        protected += [C.ref(world['build_report']), C.ref(local['build_report']),
                      C.ref(world['traversal_path']), local['actual_owned_frame_evidence']]
    closure = checked(C.ref(m.CLOSURE))
    require(closure['sha256'] == 'd862f26bc420a7ed5e3b77b765a872857fb1100ac2cdfa0ed09ca29012cf9286',
            'Original22 closure differs')
    science = read(closure['path'])['actual_remote_original_source_refs']
    require(len(science) == 22, 'Complete22 original scientific closure required')
    protected += [closure, *science]
    before = m.unique(protected)
    for r in before:
        checked(r)
    byname = {Path(r['path']).name: r for r in science}
    sys.path[:0] = [str(Path(byname['standability.py']['path']).parents[3]),
        str(m.VALIDATION), str(W/'scripts'),
        str(Path(byname['gate25_storey_navigation.py']['path']).parent)]
    import numpy as np
    from pxr import Usd
    import gate_extension_multistair_builder_v1 as multi
    graph = m.load_module('unchanged_fullM_scratch_reader', byname['build_multistorey_graphs.py']['path'])
    require(shutil.disk_usage('/tmp').free >= 64*1024**3
            and os.stat('/tmp').st_dev != os.stat(W).st_dev, '64GiB local ephemeral space required')
    tmp = Path(tempfile.mkdtemp(prefix='factory80_fullM_', dir='/tmp'))
    extraction = m.memmap_scratch(scratch['path'], tmp, graph, np)
    mask = Usd.StagePopulationMask()
    mask.Add('/World/Stair')
    stage = Usd.Stage.OpenMasked(row['root_ref']['path'], mask)
    require(stage is not None, 'Actual current stair stage cannot open')
    dump(D/'PREREGISTERED_CURRENT_SOURCES.json', dict(source_refs_before=before,
        current_root_ref=row['root_ref'], original_multistair_builder_main_called=False,
        original_scientific_functions_modified=False, native_GPU_G25_StageD_G8_G13='NOT_EXECUTED'))
    decision = C.ref(D/'PREREGISTERED_CURRENT_SOURCES.json')
    failures, phases, artifacts, arrivals = [], {}, [], []
    union = np.lib.format.open_memmap(tmp/'stair_union.npy', mode='w+',
        dtype=np.bool_, shape=extraction.full_stair_mask.shape)
    union[:] = False
    def phase(name, fn):
        require(not stop(), 'Existing STOP before phase')
        try:
            value = fn()
            phases[name] = value
            dump(D/(name+'.RESULT.json'), value)
            return value
        except Exception:
            failure = dict(phase=name, status='FAILED_ORDINARY', traceback=traceback.format_exc(), entire_task_stop=False)
            failures.append(failure)
            dump(D/(name+'.FAILURE.json'), failure)
            return None
    def observe(value, name):
        escaped = negative_escapes(value)
        if escaped:
            evidence = dict(entire_task_stop=True, phase=name, actual_escaped_controls=escaped,
                            actual_report=value, current_root_ref=row['root_ref'])
            dump(D/'CRITICAL_STOP_NEGATIVE_ESCAPE.json', evidence)
            dump(SHARED_STOP, evidence)
            raise SystemExit(2)
    def mapped_artifact(path, folder):
        folder.mkdir()
        result = {}
        with zipfile.ZipFile(path) as z:
            require(set(z.namelist()) == {k+'.npy' for k in multi.native.MULTISTOREY_ARTIFACT_KEYS},
                    'Complete original artifact keys required')
            for k in multi.native.MULTISTOREY_ARTIFACT_KEYS:
                local = folder/(k+'.npy')
                with z.open(k+'.npy') as src, local.open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024**2)
                result[k] = np.load(local, mmap_mode='r', allow_pickle=False)
        multi.native.validate_multistorey_artifact_arrays(result)
        return result
    for world, local in pairs:
        sid = world['stair_id']
        require(not stop(), 'Existing STOP before physical split')
        try:
            stairmask = multi.split_stair_mask(stage, world['prim_path'], extraction)
        except Exception:
            failure = dict(phase=sid+'_SPLIT', status='FAILED_ORDINARY', traceback=traceback.format_exc())
            failures.append(failure)
            dump(D/(sid+'_SPLIT.FAILURE.json'), failure)
            continue
        split_result = dict(physical_prim_path=world['prim_path'],
                            actual_rasterized_stair_cells=int(np.count_nonzero(stairmask)))
        phases[sid+'_SPLIT'] = split_result
        dump(D/(sid+'_SPLIT.RESULT.json'), split_result)
        for begin in range(0, union.shape[0], 16):
            np.logical_or(union[begin:begin+16], stairmask[begin:begin+16], out=union[begin:begin+16])
        isolated = multi.isolated_extraction(extraction, stairmask)
        def coordinates_and_b2():
            second = isolated.bands[1]
            upper, selection = multi.g25_native._floor2_arrival(isolated.full_occupancy,
                stairmask, isolated.full_grid_spec, isolated.peak_z_index, second.occupancy, second.grid_spec)
            projected = project_floor1(read(world['build_report']), read(world['traversal_path']))
            lower = [*projected['floor1_xyz_m'][:2], float(isolated.floor1_support_top_z_m)]
            coords = dict(schema='Factory80CPUOriginalArrivalCoordinatesOnlyV2',
                current_root_ref=row['root_ref'], physical_stair_id=sid,
                storeys={name: dict(storey=name, stair_arrival_xyz_m=xyz)
                         for name, xyz in [('Floor1', lower), ('Floor2', list(upper))]},
                original_floor1_projection=projected, original_floor2_selection=selection,
                original_native_main_called=False, full_G25_status='NOT_CLAIMED')
            cp = D/(sid+'.COORDINATES_ONLY.json')
            dump(cp, coords)
            arrivals.append(dict(stair_id=sid, lower_arrival_xyz_m=lower,
                                 upper_arrival_xyz_m=list(upper), coordinate_ref=C.ref(cp)))
            base = [m.MAIN, '-u', str(m.VALIDATION/'factory80_stage2_cross_storey.py'),
                '--lower', floors[0]['support_ref']['path'], '--upper', floors[1]['support_ref']['path'],
                '--g25', str(cp)]
            name = sid+'_B2_POSITIVE'
            code = m.child(D, name, base+['--no-control', '--output', str(D/(name+'.json'))])
            result = read(D/(name+'.json'))
            carrier = qualified_b2(result, code)
            report = dict(positive_report_ref=C.ref(D/(name+'.json')), actual_positive_returncode=code,
                          qualified_positive=carrier, G25_GPU_dependency=False)
            if carrier:
                name = sid+'_B2_WITH_CONTROL'
                code = m.child(D, name, base+['--output', str(D/(name+'.json'))])
                controlled = read(D/(name+'.json'))
                observe(controlled, name)
                require(qualified_b2(controlled, code), 'Original controlled baseline changed from positive')
                report.update(controlled_report_ref=C.ref(D/(name+'.json')),
                              actual_controlled_returncode=code, control=controlled.get('negative_control'))
                if controlled.get('negative_control', {}).get('status') != 'FAIL_AS_REQUIRED':
                    failures.append(dict(phase=name, measured=controlled))
            else:
                failures.append(dict(phase=name, measured=result,
                    N2_2='NOT_EXECUTED_NO_QUALIFIED_POSITIVE_CARRIER'))
            return report
        phase(sid+'_B2', coordinates_and_b2)
        def one_graph():
            arrays = multi.native.build_multistorey_artifact_arrays(isolated,
                building=row['building'], source_usdz_path=row['root_ref']['path'],
                source_usdz_sha256=row['root_ref']['sha256'], decision_path=decision['path'],
                decision_sha256=decision['sha256'])
            multi.native.validate_multistorey_artifact_arrays(arrays)
            path = D/(sid+'.native_one_stair.npz')
            payload = multi.native.deterministic_multistorey_npz_bytes(arrays)
            with path.open('xb') as f:
                f.write(payload)
            del arrays, payload
            gc.collect()
            mapped = mapped_artifact(path, tmp/(sid+'_artifact'))
            artifacts.append((sid, mapped))
            return dict(original_one_stair_functions_called=True, artifact_ref=C.ref(path))
        phase(sid+'_GRAPH', one_graph)
        del isolated, stairmask
        gc.collect()
    exact = all(np.array_equal(union[b:b+16], extraction.full_stair_mask[b:b+16])
                for b in range(0, union.shape[0], 16))
    dump(D/'EXACT_STAIR_MASK_COVERAGE.json', dict(exact_union_equals_current_full_stair_mask=exact,
        expected_stair_count=len(pairs), successful_one_stair_graphs=len(artifacts)))
    def merge():
        require(exact and len(artifacts) == len(pairs), 'Every physical stair must have an original artifact')
        arrays = multi.merge_native_artifacts(artifacts, extraction)
        manifest = multi.native.manifest_from_artifact(arrays)
        records = manifest.get('stair_edges', [manifest['stair_edge']])
        require(len(records) == len(pairs)
                and int(np.count_nonzero(arrays['edge_is_stair'])) == len(pairs),
                'Merged actual stair-edge count differs')
        payload = multi.native.deterministic_multistorey_npz_bytes(arrays)
        require(payload == multi.native.deterministic_multistorey_npz_bytes(arrays),
                'Original full-M serialization is not deterministic')
        path = D/'CURRENT_FULL_M.multistorey_topology_v1.npz'
        with path.open('xb') as f:
            f.write(payload)
        del payload, arrays
        gc.collect()
        loaded = multi.native.load_multistorey_artifact(path)
        require(len(loaded['edge_id']) > 0, 'Original full-M load yielded no edges')
        for arrival in arrivals:
            record = next(r for r in records if r['stair_id'] == arrival['stair_id'])
            arrival['edge_id'] = int(record['edge_id'])
        dump(D/'CURRENT_FULL_M_ARRIVALS.json', dict(rows=arrivals,
            schema='Factory80CPUOriginalStairArrivalsV2',
            all_physical_arrivals_complete=len(arrivals) == len(pairs),
            G25_GPU_verdict='NOT_CLAIMED'))
        return dict(graph_ref=C.ref(path), manifest=manifest,
                    arrivals_ref=C.ref(D/'CURRENT_FULL_M_ARRIVALS.json'),
                    original_full_M_scientific_functions_called=True,
                    original_multistair_builder_main_called=False, G8_G13_StageD_G25='NOT_EXECUTED')
    built = phase('FULL_M_GRAPH', merge)
    del extraction, stage, artifacts, union
    gc.collect()
    if built:
        def ingest():
            name = 'FULL_M_INGEST'
            argv = [m.MAIN, '-u', str(m.VALIDATION/'gate_protomotions_ingest_conformance.py'),
                    built['graph_ref']['path'], '--output', str(D/(name+'.json'))]
            code = m.child(D, name, argv)
            value = read(D/(name+'.json'))
            observe(value, name)
            require(code in (0, 1) and (code == 0) == (value['status'] == 'PASS'),
                    'Actual original ingest return/report disagree')
            if value['status'] != 'PASS':
                failures.append(dict(phase=name, measured=value))
            return dict(status=value['status'], actual_signed_returncode=code,
                        original_result_ref=C.ref(D/(name+'.json')))
        phase('FULL_M_INGEST', ingest)
    else:
        failures.append(dict(phase='FULL_M_INGEST', status='NOT_EXECUTED_GRAPH_NOT_CREATED'))
    after = [C.ref(r['path']) for r in before]
    require(before == after, 'Full-M protected source changed')
    dump(D/'FINITE_RESULT.json', dict(schema='Factory80CurrentFullMCPUChainV1',
        building=row['building'], current_root_ref=row['root_ref'], phases=phases,
        ordinary_failures=failures, source_refs_before=before, source_refs_after=after,
        source_unchanged=True, original_scientific_functions_modified=False, GPU_used=False,
        READY='NOT_INFERRED', local_ephemeral_temp_path=str(tmp),
        VmHWM_kB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
    C.save(D/'STAT_HASH_CACHE.json')
    C.save(W/'work/P1_HASH_CACHE_20261015.json')
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
