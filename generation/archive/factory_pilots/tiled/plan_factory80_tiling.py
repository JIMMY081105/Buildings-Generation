"""Plan six pilots; reserve later specifications without generating later buildings."""
import hashlib, json, random
from pathlib import Path
from datetime import datetime, timezone

W=Path('/cephfs/shared/yixing/outputs/factory80_20261011')
F=Path('/cephfs/shared/yixing/outputs/factory10')
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as s:
        for b in iter(lambda:s.read(1048576),b''):h.update(b)
    return h.hexdigest()

def main():
    selection=F/'source_catalog/combined_round1_76_round2_108_selection.json'
    doc=json.loads(selection.read_text());names=[r['scene'].removesuffix('_ceiling') for r in doc['selected']]
    assert len(names)==len(set(names))==184
    rng=random.Random(20261011);rows=[];source_refs={}
    for i in range(1,81):
        grid=[3,4,5,3,4,5][i-1] if i<=6 else rng.randint(3,7)
        storeys=2 if i<=6 else rng.randint(2,4)
        upper_count={3:4,4:7,5:10,6:15,7:21}[grid]
        cells=[(x,y) for y in range(grid) for x in range(grid)]
        upper=cells[:upper_count]
        row={'building':f'factory80_{i:03d}','grid_n':grid,'storeys':storeys,
             'tile_pitch_m':[20.0,21.0],'floor_to_floor_m':4.0,'roof_height_m':9.0,
             'target_mezzanine_coverage':upper_count/(grid*grid),
             'planned_stair_count':2 if grid<=4 else 3,
             'work_state':'PILOT_PLANNED' if i<=6 else 'DEFERRED_UNTIL_USER_CONTINUES',
             'verification_state':'NOT_EXECUTED','cannot_support':'规划不证明实测面积、连通性、设备无重复、门禁、颜色或视觉合格。'}
        if i<=6:
            schemes=[]
            for attempt in range(1,4):
                order=names.copy();random.Random(20261011+i*100+attempt).shuffle(order)
                chosen=order[:grid*grid+upper_count];assert len(chosen)==len(set(chosen))
                tiles=[]
                for s,(level,cell) in zip(chosen,[(0,c) for c in cells]+[(1,c) for c in upper]):
                    p=F/'work/official_scenes/extracted'/s/'scene.usda'
                    assert p.is_file() and p.stat().st_size>0
                    if s not in source_refs:source_refs[s]={'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
                    tiles.append({'storey':level,'cell':list(cell),'scene':s,'source':source_refs[s],
                                  'placement_rule':'设备几何保持原尺寸；移除源建筑墙、门窗、地板及天花的根层覆盖；设备边界实测后置于格内，超界拒绝。'})
                schemes.append({'attempt':attempt,'tiles':tiles,'unique_scene_count':len(chosen),
                                'same_building_scene_reuse_count':0,'status':'PLANNED_NOT_GENERATED'})
            row['schemes']=schemes
        rows.append(row)
    out={'schema':'Factory80TilingPlanV1','utc':datetime.now(timezone.utc).isoformat(),
         'source_selection':{'path':str(selection),'sha256':sha(selection)},
         'pilot_limit':6,'rows':rows,'source_layers_modified':False,
         'floor_material_max_kinds':1,'connectivity_required_components_per_storey':1,
         'design_rules':['全层连续地面，共用材料；不靠源场景之间隔墙制造格子。',
                         '柱网、桁架与屋顶实际写入USD；屋顶仅视觉，无可站立碰撞面。',
                         '楼梯沿夹层开放边缘分散布置，所有梯接入同一夹层；位置由真实路径端点确定。'],
         'cannot_support':'未生成、未测量；仅首6有备选场景计划；第7栋及以后禁止执行直至明确继续。'}
    with (W/'TILING_PLAN.json').open('x') as f:json.dump(out,f,indent=2,ensure_ascii=False)
    print('PILOT_PLANS_WRITTEN',len(source_refs),'source layer hashes',flush=True)

if __name__=='__main__':main()
