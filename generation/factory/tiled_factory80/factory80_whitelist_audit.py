import ast
import hashlib
import json
from pathlib import Path

BASE = Path('/cephfs/shared/yixing/outputs/diversity54_20260908')
TASK = Path('/cephfs/shared/yixing/outputs/factory80_20261011')
allocator = BASE / 'scripts/portfolio_allocator.py'
manifests = [BASE / 'scripts/FROZEN_CONTRACT_SOURCES.json', BASE / 'evidence/FROZEN_CONTRACT_SOURCES_GATE5_V2_R1.json']
data = {'allocator': str(allocator), 'allocator_sha256': hashlib.sha256(allocator.read_bytes()).hexdigest(), 'manifests': []}
for manifest in manifests:
    doc = json.loads(manifest.read_text())
    rows = doc.get('rows', [])
    matched = [row for row in rows if 'portfolio_allocator.py' in json.dumps(row)]
    data['manifests'].append({'path': str(manifest), 'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(), 'rows': len(rows), 'allocator_entries': matched})
tree = ast.parse(allocator.read_text())
palettes = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PALETTES' for t in node.targets))
data['existing_palettes'] = {name: {'rgb': value[0], 'tread_material': value[1], 'step_material': value[2], 'handrail_material': value[3]} for name, value in palettes.items()}
data['all_metal_existing'] = [name for name, row in data['existing_palettes'].items() if all(row[key] in ('BrushedMetal', 'MetalBasic') for key in ('step_material','tread_material','handrail_material'))]
data['palette_tuple_order_observed'] = ['RGB', 'tread_material', 'step_material', 'handrail_material']
data['task_sha256'] = hashlib.sha256((TASK / 'FACTORY80_MASTER_PROMPT.md').read_bytes()).hexdigest()
print(json.dumps(data, indent=2))
