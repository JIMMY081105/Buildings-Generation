import json,pathlib
work=pathlib.Path('/cephfs/shared/yixing/outputs/factory80_20261011/work/scene_expansion')
old=pathlib.Path('/cephfs/shared/yixing/outputs/factory10/work/official_scenes/extracted')
out=[]
for r in json.load(open(work/'old_missing_equipment_scenes.json')):
 p=old/r['scene']/'Payload/Geometry.usda';text=p.read_text(encoding='utf-8')
 out.append(dict(r,geometry_path=str(p),actual_geometry_contains_asset_id=r['asset_id'] in text,matching_reference_lines=[s.strip() for s in text.splitlines() if r['asset_id'] in s and 'references' in s]))
(work/'old_missing_equipment_geometry_evidence.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out))
