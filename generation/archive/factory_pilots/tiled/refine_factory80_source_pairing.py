"""Choose diverse eligible scenes; preserve original screen and old proposal."""
import hashlib,json,random,sys
from pathlib import Path
from datetime import datetime,timezone
from pxr import Usd,UsdGeom
W=Path('/cephfs/shared/yixing/outputs/factory80_20261011');F=Path('/cephfs/shared/yixing/outputs/factory10')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    pp=W/'TILING_PLAN.json';old=pp.read_bytes();plan=json.loads(old)
    screenpath=F/'source_catalog/combined184_screen_v2.json';screen=json.loads(screenpath.read_text())
    catalog=[]
    for row in screen['ranked_scenes']:
        name=row['scene'].removesuffix('_ceiling')
        if any(c['scene']==name for c in catalog):continue
        p=F/'work/official_scenes/extracted'/name/'scene.usda'
        if not p.is_file():continue
        metadata=json.loads(p.with_name('scene_metadata.json').read_text())
        ids={e['asset_id'] for g in ('industrial','operational','warehouse') for e in row['evidence'].get(g,[])}
        st=Usd.Stage.Open(str(p));root=st.GetDefaultPrim();geom=st.GetPrimAtPath(str(root.GetPath())+'/Geometry')
        bc=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render','guide'],useExtentsHint=False)
        objects=[]
        for q in geom.GetChildren():
            rec=metadata['objects'].get(q.GetName(),{});aid=rec.get('asset_id')
            if aid not in ids:continue
            bb=bc.ComputeWorldBound(q).ComputeAlignedRange()
            if bb.IsEmpty():continue
            objects.append({'name':q.GetName(),'asset_id':aid,'bbox_min':list(bb.GetMin()),'bbox_max':list(bb.GetMax())})
        if objects:
            catalog.append({'scene':name,'source':{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)},'objects':objects})
        print(name,len(objects),'actual industrial objects',flush=True)
        del st
    evidence={'utc':datetime.now(timezone.utc).isoformat(),'screen_sha256':sha(screenpath),'scenes':catalog,
              'status':'ACTUAL_USD_SOURCE_BBOX_MEASURED_NOT_BUILDING_PASS'}
    (W/'evidence/industrial_source_geometry_v1.json').write_text(json.dumps(evidence,indent=2))
    for i,row in enumerate(plan['rows'][:6],1):
        for scheme in row['schemes']:
            used=set();remaining=catalog.copy();rng=random.Random(20261011+i*100+scheme['attempt']);rng.shuffle(remaining)
            selected=[]
            for tile in scheme['tiles']:
                choices=[]
                for c in remaining:
                    novel=[o for o in c['objects'] if o['asset_id'] not in used]
                    if not novel:continue
                    lo=[min(o['bbox_min'][j] for o in novel) for j in range(3)]
                    hi=[max(o['bbox_max'][j] for o in novel) for j in range(3)]
                    if hi[0]-lo[0]>18 or hi[1]-lo[1]>19:continue
                    choices.append((len({o['asset_id'] for o in novel}),c,novel))
                if not choices:raise ValueError('No further distinct eligible industrial scene for '+row['building'])
                # Deterministic shuffle supplies ties; actual new canonical
                # equipment supplies score. No text eligibility is rewritten.
                count,c,novel=max(choices,key=lambda v:v[0]);remaining.remove(c)
                used.update(o['asset_id'] for o in novel)
                selected.append({**tile,'scene':c['scene'],'source':c['source'],'planned_novel_canonical_assets':count})
            scheme['tiles']=selected;scheme['planned_distinct_canonical_equipment']=len(used)
    previous=W/'evidence/TILING_PLAN_before_diverse_pairing.json'
    previous.write_bytes(old)
    plan['design_rules'][2]='楼梯井放在连续夹层带内，四侧保留实际楼板，出口朝向后立面并留4米地面；按实际路径端点排布。'
    plan['pairing_revision']={'utc':datetime.now(timezone.utc).isoformat(),'previous_sha256':hashlib.sha256(old).hexdigest(),
                             'method':'unchanged industrial-screen eligibility; measured source object bounds; greedy new canonical equipment with deterministic seeded ties',
                             'cannot_support':'来源测量和配对不证明生成后楼层连通、资产完整性、冻结门禁或视觉合格。'}
    pp.write_text(json.dumps(plan,indent=2,ensure_ascii=False))
    print('PAIRING_REVISED',sha(pp),flush=True)
if __name__=='__main__':main()
