"""Bind already inspected native renders to completed READY evidence."""
import argparse,sys
from pathlib import Path
W=Path('/cephfs/shared/yixing/outputs/diversity54_20260908');sys.path.insert(0,str(W/'scripts'))
from evidence_common import ref,read_json,write_new,utc_now
p=argparse.ArgumentParser();p.add_argument('--fullchain-root',type=Path,required=True);a=p.parse_args();root=a.fullchain_root
r=read_json(root/'READY_FOR_ACCEPTANCE.json');assert r['status']=='READY_FOR_ACCEPTANCE' and r['formal_acceptance_added'] is False
b=read_json(r['source_binding']['path']);assert ref(r['source_binding']['path'])==r['source_binding']
package=read_json(b['package']['path']);assert ref(b['package']['path'])==b['package']
inspection=read_json(root/'PALETTE_VIEW_INSPECTION_PRE_READY.json');assert inspection['status']=='OBSERVED_RENDER_PALETTE_PENDING_COMPLETE_CHAIN'
assert inspection['observer']=='Codex actual image inspection' and inspection['palette_observed'] is True and inspection['building']==b['building']
assert inspection['images_actually_viewed'] is True and inspection['style_grade'] in ('MATCH','ACCEPTABLE','CLASH')
material_ref=ref(root/'MATERIAL_VISUAL_INSPECTION.json');material=read_json(material_ref['path'])
assert material['images_actually_viewed'] is True and material['style_grade']==inspection['style_grade'] and material['native_verdict']==inspection['native_verdict'] and material['view_observations']==inspection['view_observations']
assert ref(inspection['native_verdict']['path'])==inspection['native_verdict']
native=read_json(b['native_result']['path']);assert native['status']=='PASS' and native['verdict']==inspection['native_verdict']
assert package['stage']['path']==inspection['native_stage_binding']['stage'] and package['stage']['sha256']==inspection['native_stage_binding']['stage_sha256']
assert ref(r['five_view_collage']['path'])==r['five_view_collage']
for v in inspection['view_observations']:assert ref(v['render']['path'])==v['render']
o=dict(schema='SignatureCap8ObservedPaletteV1',building=b['building'],observed_at_utc=utc_now(),observer=inspection['observer'],images_actually_viewed=True,style_grade=inspection['style_grade'],style_judgment=inspection['style_judgment'],scene_harmony_subjective=True,material_visual_inspection=material_ref,authorized_style_revision=1,palette_observed=True,
 dominant_palette=inspection['dominant_palette'],source_stage=package['stage'],five_view_collage=r['five_view_collage'],
 view_observations=inspection['view_observations'],raw_view_inspection=ref(root/'PALETTE_VIEW_INSPECTION_PRE_READY.json'),
 observed_colours=inspection['observed_colours'],bucket_basis=inspection['bucket_basis'],formal_acceptance_added=False,
 cannot_support='不支持正式视觉验收、场景协调性验收、人形可爬或发布。')
write_new(root/'OBSERVED_PALETTE_FOR_READY.json',o);print(root/'OBSERVED_PALETTE_FOR_READY.json')
