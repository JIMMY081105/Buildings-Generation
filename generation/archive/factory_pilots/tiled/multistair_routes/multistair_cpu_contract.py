"""Receipt and inventory checks; this module runs no scientific functions."""
def require(value, message):
    if not value:
        raise ValueError(message)


def clean_terminal(terminal):
    require(terminal is not None and terminal.get('child_started') is True,
            'Actual scientific child must have started and terminated')
    require(terminal.get('actual_signed_returncode') in (0, 1)
            and terminal.get('source_unchanged') is True
            and terminal.get('own_RSS_budget_terminated') is False
            and terminal.get('own_watchdog_failure') is False,
            'Clean actual terminal0or1 required; resource failure is not science')


def stable_result(result, root):
    require(result.get('current_root_ref') == root
            and result.get('source_unchanged') is True
            and result.get('source_refs_before')
            and result['source_refs_before'] == result.get('source_refs_after'),
            'Actual upstream result is not source-stable current geometry')


def check_structural_scope(author, raw, main):
    require(raw['schema'] == 'Factory80RootOverlayAuthoringV1'
            and author['building'] == raw['building'] == main['building']
            and author['storeys'] == raw['storeys'] == main['storeys'] == 2,
            'Exact original current N2 structural identity required')
    require(type(raw['stair_count']) is int and raw['stair_count'] > 0
            and raw['stair_count'] == main['planned_stair_count'] == len(raw['stairs']),
            'Actual original structure/plan stair count differs')
    if 'stair_count' in author:
        require(author['stair_count'] == raw['stair_count'], 'Current author stair count differs')
    else:
        require(author['schema'] == 'Factory80OwnedN2DensityDerivedAuthoringV1',
                'Only explicitly bound density metadata may omit structural stair count')


def frame_author_matches(row, frame, author):
    if (frame.get('original_candidate') != row['root_ref']
            or author.get('stage') != row['root_ref']
            or author.get('building') != row['building'] or author.get('storeys') != 2):
        return False
    if frame['actual_source_authoring'] == row['authoring_ref']:
        return True
    return (author.get('schema') == 'Factory80OwnedCurrentGeometryStageDInputV1'
            and author.get('current_geometry_authoring_ref') == row['authoring_ref']
            and author.get('original_structural_authoring_ref') == row['raw_authoring_ref']
            and author.get('historical_scientific_results_inherited') is False)


def stair_inventory(frame, world, row, count):
    require(frame['building'] == world['building'] == row['building']
            and frame['original_candidate'] == row['root_ref'],
            'Measured world/frame belongs to another root')
    local, physical = frame['stairs'], world['stairs']
    require(len(local) == len(physical) == count and count > 0,
            'All actual stairs must be accounted for')
    require(len({r['stair_id'] for r in physical}) == count
            and len({r['prim_path'] for r in physical}) == count,
            'Physical world stair IDs and paths must be unique')
    require({r['stair_id'] for r in local} == {r['stair_id'] for r in physical},
            'Local and world stair IDs differ')
    require(all(r['prim_path'].startswith('/World/Stair/') for r in physical),
            'Physical input must be under /World/Stair')
    # Local FrameR2 prim_paths intentionally repeat. Never use them to split
    # the physical building; the measured world manifest carries those paths.
    return [(r, next(x for x in local if x['stair_id'] == r['stair_id']))
            for r in physical]


def negative_escapes(value):
    if isinstance(value, dict):
        own = [value] if str(value.get('status', '')).startswith('DID_NOT_FAIL') else []
        return own + [r for child in value.values() for r in negative_escapes(child)]
    if isinstance(value, list):
        return [r for child in value for r in negative_escapes(child)]
    return []


def qualified_b2(value, code):
    require(type(code) is int and code in (0, 1), 'Original B2 child crashed')
    require((code == 0) == (value['status'] == 'PASS'), 'B2 return/report disagree')
    return value['status'] == 'PASS' and value.get('pairs', 0) >= 200
