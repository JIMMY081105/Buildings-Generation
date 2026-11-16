"""ROOT-only bounded STD source binder. No PXR/SDK, no guessed input refs."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from datetime import datetime, timezone


def ref(p):
    p = Path(p).absolute(); h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''): h.update(b)
    return {'path': str(p), 'bytes': p.stat().st_size, 'sha256': h.hexdigest()}


def main():
    p = argparse.ArgumentParser(allow_abbrev=False); p.add_argument('--execute', action='store_true')
    p.add_argument('--source-index', type=Path); p.add_argument('--expected-source-index-sha256')
    p.add_argument('--building'); p.add_argument('--root', type=Path); p.add_argument('--expected-root-sha256')
    p.add_argument('--authoring', type=Path); p.add_argument('--expected-authoring-sha256'); p.add_argument('--output', type=Path)
    a = p.parse_args()
    if not a.execute:
        print(json.dumps({'status': 'SOURCE_ONLY_NOT_EXECUTED', 'PXR_SDK_render_science': False})); return 0
    if not all([a.source_index, a.expected_source_index_sha256, a.building, a.root, a.expected_root_sha256, a.authoring, a.expected_authoring_sha256, a.output]):
        raise ValueError('Explicit reviewed actual input/index hashes and fresh output required')
    for s in [a.expected_source_index_sha256, a.expected_root_sha256, a.expected_authoring_sha256]:
        if not re.fullmatch('[0-9a-f]{64}', s): raise ValueError('Full SHA required')
    index_ref = ref(a.source_index)
    if index_ref['sha256'] != a.expected_source_index_sha256: raise ValueError('Source index changed')
    index = json.loads(a.source_index.read_bytes())
    if a.building not in index['allowed_two_storey_buildings']: raise ValueError('Outside reviewed 29 N2 scope')
    running = ref(__file__); original = index['runtime_files'][Path(__file__).name]
    if (running['bytes'], running['sha256']) != (original['bytes'], original['sha256']): raise ValueError('Same-byte actual reader source changed')
    root, author_ref = ref(a.root), ref(a.authoring)
    if root['sha256'] != a.expected_root_sha256 or author_ref['sha256'] != a.expected_authoring_sha256: raise ValueError('Reviewed actual input changed')
    author = json.loads(a.authoring.read_bytes())
    if author['building'] != a.building or author['storeys'] != 2 or type(author['stair_count']) is not int or author['stair_count'] not in (2, 3) or len(author['stairs']) != author['stair_count'] or author['stage'] != root:
        raise ValueError('Actual AUTHORING building/N2/declared-M2-or-M3/root fullref differs')
    before = [index_ref, running, root, author_ref]; after = [ref(x['path']) for x in before]
    if before != after: raise ValueError('Source changed during STD input read')
    output = {'schema': 'Factory80RootActualN2RenderOnlyInputBindingsV1', 'utc': datetime.now(timezone.utc).isoformat(),
        'actual_reader_pid': os.getpid(), 'rows': [{'building': a.building, 'root_ref': root, 'authoring_ref': author_ref}],
        'source_refs_before': before, 'source_refs_after': after, 'source_count': len(before), 'source_unchanged': True,
        'PXR_SDK_render_science': 'NOT_EXECUTED', 'cannot_support': 'STD source identity only; no geometry/material/light/render/visual gate/device result.'}
    with a.output.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, allow_nan=False); f.write('\n'); f.flush(); os.fsync(f.fileno())
    print(json.dumps({'actual_fresh_input_vector_ref': ref(a.output)})); return 0


if __name__ == '__main__': raise SystemExit(main())
