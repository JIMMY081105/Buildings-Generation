"""Factory80 G8 adapter: only generalize the storey and stair multiplicities.

The frozen G8 source and all Standability/G25/stage_d/capacity predicates stay
unchanged. The original single-stair input schema remains supported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

LEGACY = Path('/cephfs/shared/yixing/outputs/diversity54_20260908')
REPO = Path('/cephfs/shared/yixing/code/Scenesmith-code-study/ProtoMotions-main')
VALID = LEGACY / 'evidence/frozen_sources/validation'
sys.path[:0] = [str(REPO), str(VALID), str(LEGACY/'evidence/supplemental_sources_r1')]
import gate7_global_graph48 as frozen


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8*1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def ref(path: str | Path) -> dict[str, Any]:
    path = Path(path).resolve()
    return dict(path=str(path), bytes=path.stat().st_size, sha256=sha256(path))


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_new(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def gate8(arrays: dict[str, np.ndarray], g25: dict[str, Any],
          stage_d_row: dict[str, Any], *, expected_storeys: int,
          expected_stairs: int, arrivals: dict[str, Any] | None = None
          ) -> dict[str, Any]:
    """Keep all legacy predicates, with N contiguous storeys and M stair edges.

    Multi-stair manifests contain ``stair_edges``. Each row binds an edge_id,
    lower/upper storey IDs, lower_node_id/upper_node_id (or the legacy floor1 /
    floor2 keys), and length_m. Arrivals must be measured, not planned; every
    row contains edge_id, lower_arrival_xyz_m and upper_arrival_xyz_m.
    """
    try:
        if type(expected_storeys) is not int or expected_storeys < 2:
            raise ValueError('N must be an explicit integer >= 2')
        if type(expected_stairs) is not int or expected_stairs < 1:
            raise ValueError('M must be an explicit integer >= 1')
        pairs = np.asarray(arrays['edge_node_ids'])
        storeys = np.asarray(arrays['node_storey_id'])
        stair_mask = np.asarray(arrays['edge_is_stair'], dtype=np.bool_)
        manifest = frozen.manifest_from_artifact(arrays)
        records = manifest.get('stair_edges')
        if records is None:
            records = [manifest['stair_edge']]
        centers = np.asarray(arrays['node_center_m'], dtype=np.float64)
        components = frozen._connected_component_count(len(storeys), pairs)
        # This is still the original negative-control operation: remove ALL.
        components_without_stair = frozen._connected_component_count(
            len(storeys), pairs[~stair_mask])
        actual_stair_ids = set(map(int, np.flatnonzero(stair_mask)))
        record_ids = [int(row['edge_id']) for row in records]
        if len(record_ids) != len(set(record_ids)) or set(record_ids) != actual_stair_ids:
            raise ValueError('Every actual stair edge must have exactly one bound record')
        measured_rows = {int(row['edge_id']): row for row in (arrivals or {}).get('rows', [])}
        if arrivals is not None and set(measured_rows) != actual_stair_ids:
            raise ValueError('Every actual stair edge must have exactly one measured arrival pair')
        endpoint_errors = {}
        rises = {}
        lengths = {}
        length_passes = []
        for stair in records:
            edge_id = int(stair['edge_id'])
            lower = int(stair.get('lower_storey_id', 0))
            upper = int(stair.get('upper_storey_id', 1))
            lower_node = int(stair.get('lower_node_id', stair.get('floor1_node_id')))
            upper_node = int(stair.get('upper_node_id', stair.get('floor2_node_id')))
            if set(map(int, pairs[edge_id])) != {lower_node, upper_node}:
                raise ValueError('Stair metadata must bind actual graph endpoints')
            if int(storeys[lower_node]) != lower or int(storeys[upper_node]) != upper:
                raise ValueError('Stair metadata must bind actual endpoint storeys')
            names = ('Floor'+str(lower+1), 'Floor'+str(upper+1))
            if arrivals is None:
                if len(records) != 1:
                    raise ValueError('Multi-stair input requires measured per-stair arrivals')
                expected_xyz = [g25['storeys'][name]['stair_arrival_xyz_m'] for name in names]
            else:
                row = measured_rows[edge_id]
                expected_xyz = [row['lower_arrival_xyz_m'], row['upper_arrival_xyz_m']]
            errors = {}
            for name, node, xyz in zip(names, (lower_node, upper_node), expected_xyz):
                expected_xy = np.asarray(xyz[:2], dtype=np.float64)
                errors[name] = float(np.linalg.norm(centers[node, :2]-expected_xy))
            rise = float(expected_xyz[1][2])-float(expected_xyz[0][2])
            length = float(stair['length_m'])
            endpoint_errors[str(edge_id)] = errors
            rises[str(edge_id)] = rise
            lengths[str(edge_id)] = length
            length_passes.append(rise <= length <= 4.0*rise)
        single = len(records) == 1
        first_id = str(record_ids[0]) if records else ''
        actual = {
            'merged_component_count': components,
            'components_after_stair_edge_removal': components_without_stair,
            'storeys_present': sorted(int(value) for value in np.unique(storeys)),
            'stair_edge_count': int(np.count_nonzero(stair_mask)),
            'stair_endpoint_error_m': endpoint_errors[first_id] if single else endpoint_errors,
            'floor_to_floor_rise_m': rises[first_id] if single else rises,
            'stair_edge_length_m': lengths[first_id] if single else lengths,
            'stair_length_in_rise_to_4x_rise': bool(all(length_passes)),
            'edge_node_relation_delta': abs(len(pairs)-(len(storeys)-1)),
            'stage_d_status': stage_d_row.get('status'),
            'g25_status': g25.get('status'),
        }
        passed = (
            components == 1
            and components_without_stair == expected_storeys
            and actual['storeys_present'] == list(range(expected_storeys))
            and actual['stair_edge_count'] == expected_stairs
            and all(error <= 0.50+1.0e-12 for errors in endpoint_errors.values()
                    for error in errors.values())
            and actual['stair_length_in_rise_to_4x_rise']
            and actual['edge_node_relation_delta'] <= 2
            and actual['stage_d_status'] == 'PASS'
            and actual['g25_status'] == 'PASS'
        )
        return frozen._gate_result(
            actual=actual,
            expected={
                'merged_component_count': 1,
                'components_after_stair_edge_removal': expected_storeys,
                'storeys_present': list(range(expected_storeys)),
                'stair_edge_count': expected_stairs,
                'maximum_stair_endpoint_error_m': 0.50,
                'stair_edge_length_range': '[rise, 4*rise]',
                'maximum_edge_node_relation_delta': 2,
                'stage_d_status': 'PASS', 'g25_status': 'PASS',
            }, passed=passed,
            detail='Cross-storey connectivity; removing ALL stair edges must split into exactly N components. Only layer/stair multiplicity is generalized.')
    except Exception as error:
        return frozen._gate_result(
            actual={'exception': type(error).__name__+': '+str(error)},
            expected='complete graph and measured per-stair endpoint bindings',
            passed=False, detail='Input exception is an explicit fail-closed result')


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--graph', type=Path, required=True)
    p.add_argument('--g25', type=Path, required=True)
    p.add_argument('--stage-d', type=Path, required=True)
    p.add_argument('--storeys', type=int, required=True)
    p.add_argument('--stairs', type=int, required=True)
    p.add_argument('--arrivals', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    arrays = frozen.load_multistorey_artifact(a.graph)
    result = gate8(arrays, read_json(a.g25), read_json(a.stage_d),
                   expected_storeys=a.storeys, expected_stairs=a.stairs,
                   arrivals=read_json(a.arrivals) if a.arrivals else None)
    output = dict(schema='Factory80G8StoreyStairExtensionV1', status=result['status'],
                  G8=result, graph=ref(a.graph), g25=ref(a.g25),
                  stage_d=ref(a.stage_d), arrivals=ref(a.arrivals) if a.arrivals else None,
                  base=ref(frozen.__file__), adapter=ref(__file__),
                  cannot_support='G8 alone does not establish the other gates, scene quality, formal acceptance, or humanoid locomotion.')
    write_new(a.output, output)
    print(json.dumps(dict(status=result['status'], output=str(a.output)), ensure_ascii=False))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
