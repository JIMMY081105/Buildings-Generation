"""Native bake with measured USD channel-encoding and schema corrections."""
from pathlib import Path
from evidence_common import ref, read_json, write_new
from native_material_bake_only_v2 import bake as original_bake
from native_preview_surface_semantics_v2 import preview_schema_export


def bake(blend_obj, blueprint, source_stage, output_dir):
    with preview_schema_export():
        usd, native_metadata = original_bake(blend_obj, blueprint, source_stage, output_dir)
    metadata = Path(output_dir)/'native_preview_schema_metadata.json'
    write_new(metadata, {'schema': 'Residential54NativePreviewSchemaBakeV1', 'status': 'RECORDED',
        'unchanged_native_bake_block': ref(native_metadata), 'adapter': ref(__file__),
        'schema_adapter': ref(Path(__file__).with_name('native_preview_surface_semantics_v2.py')),
        'native_bake_calibration': ref('/cephfs/shared/yixing/outputs/diversity54_20260908/evidence/native_bake_channel_calibration_r1/RESULT.json'),
        'official_specification': 'https://openusd.org/24.08/spec_usdpreviewsurface.html',
        'changes': ['Typed diffuse/normal/RGB ports and token primvar name',
                    'Raw tangent normal decoding using scale 2 and bias -1',
                    'Explicit sRGB decoding of native sRGB-encoded scalar PNGs',
                    'Native transmission encoded as standard opacity=1-transmission',
                    'Bake the unchanged native Base Color graph with the existing native emission mechanism; restore all original shader links afterwards'],
        'successful_base_color_calibration': ref('/cephfs/shared/yixing/outputs/diversity54_20260908/evidence/native_bake_channel_calibration_r3/RESULT.json'), 'new_material_system': False, 'palette_or_color_override': False, 'geometry_modified': False,
        'usd_exporter_export_called': False, 'visual_only_argument_supplied': False})
    return usd, metadata
