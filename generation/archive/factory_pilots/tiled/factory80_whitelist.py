"""Factory80 result-based preflight and generated-metrics measurement. No palette-name policy."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

METALS = ('BrushedMetal', 'MetalBasic')
FAMILIES = ('straight', 'l_shaped', 'u_shaped')
RAILS = ('vertical-post', 'horizontal-post')
SUPPORTS = ('double-rail', 'solid', 'hole')
ROLES = ('step_material', 'tread_material', 'handrail_material')

def ref(path):
    path = Path(path)
    return {'path': str(path.resolve()), 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

def read_palette_table(path):
    tree = ast.parse(Path(path).read_text())
    return next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PALETTES' for t in node.targets))

def resolve_palette(name, table):
    # OBSERVED allocator order: RGB, tread, step, handrail. Never infer from the name.
    rgb, tread, step, handrail = table[name]
    return dict(dominant_palette=name, rgb_target_srgb=rgb, step_material=step, tread_material=tread, handrail_material=handrail)

def validate(candidate):
    allowed = dict(staircase_family=FAMILIES, handrail_type=RAILS, support_type=SUPPORTS, **{key: METALS for key in ROLES})
    checks = {key: candidate.get(key) in choices for key, choices in allowed.items()}
    reasons = [f'{key}={candidate.get(key)!r} not in {list(allowed[key])}' for key, ok in checks.items() if not ok]
    return {'schema': 'Factory80WhitelistResultV1', 'status': 'PASS' if all(checks.values()) else 'FAIL', 'checks': checks, 'actual_values_examined': {key: candidate.get(key) for key in allowed}, 'failure_reasons': reasons, 'visual_review_eligible': all(checks.values()), 'unsupported': 'This preflight does not prove generated materials, RGB, visual quality, physical gates or acceptance.'}

def require(candidate):
    result = validate(candidate)
    if result['status'] != 'PASS':
        raise ValueError(json.dumps(result))
    return result

def measure_metrics(path):
    metrics = json.loads(Path(path).read_text())
    style = metrics['style']
    actual = {'staircase_family': metrics['kind'], 'support_type': style['support_type'], 'handrail_type': style['handrail_type'], **{role: style[role.replace('_material', '_material_factory')] for role in ROLES}}
    result = validate(actual)
    result.update(source_generation_metrics=ref(path), actual_materials_source='Generated native factory values; never planned values', ACTUAL_SIGNATURE={key: actual[key] for key in ('staircase_family', 'support_type', 'tread_material', 'handrail_type')}, palette_actual_status='PENDING_SHADER_MEASUREMENT_AND_ACTUAL_RTX_VIEW')
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--generation-metrics', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if bool(args.candidate) == bool(args.generation_metrics):
        parser.error('Exactly one candidate or generation-metrics is required')
    result = validate(json.loads(args.candidate.read_text())) if args.candidate else measure_metrics(args.generation_metrics)
    if args.output:
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)

if __name__ == '__main__':
    main()
