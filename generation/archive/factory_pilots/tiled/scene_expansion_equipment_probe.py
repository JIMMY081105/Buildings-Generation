import hashlib,json,pathlib
WORK=pathlib.Path('/cephfs/shared/yixing/outputs/factory80_20261011/work/scene_expansion')
stats=json.load(open(WORK/'industrial_diversity_stats.json'))
root=pathlib.Path('/cephfs/shared/yixing/outputs/factory10/work/objects/objaverse');rows=[]
for asset_id,record in stats['old184']['assets'].items():
 directory='obja_'+asset_id;file=root/directory/(directory+'.usda')
 result={'asset_id':asset_id,'object_type':record.get('object_type'),'matched_terms':record['matched_terms'],'expected_usda':str(file),'scope':'positive size and actual first 4096-byte read only; textures/composition/hash of full file remain unverified'}
 try:
  if not file.is_file() or file.stat().st_size<=0:raise RuntimeError('root USD absent/empty')
  with file.open('rb') as f:sample=f.read(4096)
  if not sample:raise RuntimeError('zero readable bytes')
  result.update(status='READABLE_SAMPLE',size=file.stat().st_size,bytes_actually_read=len(sample),sample_sha256=hashlib.sha256(sample).hexdigest(),resolved_usda=str(file.resolve()))
 except Exception as e:result.update(status='FAIL',error=f'{type(e).__name__}: {e}')
 rows.append(result)
out={'scope':'old184 industrial asset roots quick read-only probe, not dependency closure','count':len(rows),'readable_sample_count':sum(r['status']=='READABLE_SAMPLE' for r in rows),'failed_count':sum(r['status']=='FAIL' for r in rows),'rows':rows,'cannot_support':'Full-file integrity, texture availability, USD composition, equipment dimensions, visual identity or acceptance.'}
(WORK/'old184_equipment_probe.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:out[k] for k in ['count','readable_sample_count','failed_count']}))
