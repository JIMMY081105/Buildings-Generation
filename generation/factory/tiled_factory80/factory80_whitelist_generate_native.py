"""Source-bound unchanged native geometry/export with Factory80 result/paint adapter."""
import ast
import hashlib
import json
import sys
from pathlib import Path

BASE = Path('/cephfs/shared/yixing/outputs/diversity54_20260908/scripts')
SOURCE = BASE / 'generate_staircase_diversity_v4.py'
SOURCE_SHA256 = 'eac63625979db761d21f06bf25a0fdce5b953ea33af0a200feb2e29e2f0211ad'

def main():
    raw = SOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256: raise ValueError('Source generator changed since audit')
    text = raw.decode()
    old = 'from native_style_overrides_v2 import add_arguments, apply_overrides'
    new = 'from factory80_whitelist_native_adapter import add_arguments, apply_overrides, apply_native_rgb'
    assert text.count(old) == 1
    text = text.replace(old,new)
    old = '    obj.hide_render = False\n'
    new = '    color_trace = apply_native_rgb(obj, args)\n    write_new(args.output_dir / "NATIVE_RGB.json", color_trace)\n'+old
    assert text.count(old) == 1
    text = text.replace(old,new)
    old = '        "style_override_trace": style_override_trace,\n'
    new = old+'        "factory80_native_rgb": ref(args.output_dir / "NATIVE_RGB.json"),\n'
    assert text.count(old) == 1
    text = text.replace(old,new)
    ast.parse(text)
    if '--bake-device' not in sys.argv or sys.argv[sys.argv.index('--bake-device')+1] != 'cpu': raise ValueError('Factory80 native asset generation is CPU-only')
    if '--native-role-audit' not in sys.argv: raise ValueError('Actual face material role audit is required')
    if '--output-dir' in sys.argv:
        root = Path(sys.argv[sys.argv.index('--output-dir')+1]).parent
        record = root/'FACTORY80_NATIVE_WRAPPER.json'
        with record.open('x') as stream:
            json.dump(dict(source=str(SOURCE),source_sha256=SOURCE_SHA256,wrapper=str(Path(__file__).resolve()),transformed_source_sha256=hashlib.sha256(text.encode()).hexdigest(), scope='Only native style import, pre-export actual color hook and color evidence ref changed in memory; source file not written; geometry/CoACD/collision normalization unchanged'),stream,indent=2)
    sys.path.insert(1,str(BASE))
    # The copied native environment's editable .pth still names /ssd_xs.
    # Resolve the actually retained immutable package via this owned runner.
    sys.path.insert(1,str(BASE.parent/'runtime/infinigen_staircase_poc/infinigen/src'))
    namespace = {'__name__':'__main__','__file__':str(SOURCE),'__package__':None}
    exec(compile(text,str(SOURCE),'exec'),namespace)

if __name__ == '__main__': main()
