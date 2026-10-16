"""Wait for actual pilot native results, then verify location-only clones and portable aggregate inputs."""
import json
import subprocess
import time
from pathlib import Path
from factory80_whitelist import ref

TASK=Path('/cephfs/shared/yixing/outputs/factory80_20261011')

def write(path,value):
    with path.open('x') as stream: json.dump(value,stream,indent=2); stream.write('\n')

def main():
    assigned=json.loads((TASK/'evidence/NATIVE_PILOT6_SEED_EXTENSION.json').read_text())['rows']
    ledger=[]
    for row in assigned:
        actual=[]
        for index,seed in enumerate(row['native_seeds'],1):
            root=TASK/'assets'/row['building']/f'stair{index:02d}'/'attempt2'
            while not (root/'RESULT.json').exists(): time.sleep(20)
            result=json.loads((root/'RESULT.json').read_text())
            record=dict(building=row['building'],stair_index=index,seed=seed,result=ref(root/'RESULT.json'),work_state='NOT_AVAILABLE')
            if result.get('asset') and result['validation_state']=='NATIVE_MATERIAL_RGB_MEASURED':
                try:
                    if not (root/'portable/RESOURCE_RELOCATION.json').exists():
                        command=['/cephfs/shared/yixing/envs/main/bin/python',str(TASK/'scripts/factory80_whitelist_portable.py'),'--asset-dir',str(root/'asset')]
                        proc=subprocess.run(command,capture_output=True,text=True)
                        if proc.returncode!=0: raise ValueError(proc.stderr[-3000:])
                    portable=json.loads((root/'portable/RESOURCE_RELOCATION.json').read_text())
                    if portable['status']!='PASS' or portable['missing_dependencies']: raise ValueError('Incomplete portable resource check')
                    record.update(work_state='PORTABLE_NATIVE_ASSET_AVAILABLE',resource_relocation=ref(root/'portable/RESOURCE_RELOCATION.json'),stage=portable['target']['path'])
                    actual.append(dict(stage=portable['target']['path'],metrics=result['metrics']['path'],traversal_path=result['traversal']['path'],actual_signature=result['actual_signature'],stair_index=index,seed=seed))
                except Exception as exc: record.update(work_state='PORTABILITY_FAILED',error=type(exc).__name__+': '+str(exc))
            record['cannot_support']='资源校验不证明楼梯几何/门禁/RTX/验收。'
            write(root/'PORTABLE_RESULT.json',record);ledger.append(record);print(json.dumps(record),flush=True)
        write(TASK/'assets'/row['building']/'STAIR_ASSETS_PORTABLE_R2.json',dict(schema='Factory80ActualStairAssetsInputV1',building=row['building'],planned_stair_count=row['planned_stair_count'],actual_asset_count=len(actual),work_state='ASSETS_AVAILABLE' if len(actual)==row['planned_stair_count'] else 'INCOMPLETE_NATIVE_ASSETS',validation_state='NATIVE_MATERIAL_RGB_RESOURCES_ONLY',rows=actual,cannot_support='原生材质/颜色/资源实测不证明完整建筑/门禁/RTX/验收。'))
    write(TASK/'evidence/NATIVE_PILOT6_PORTABLE_RESULT_R2.json',dict(work_state='PORTABILITY_ATTEMPTS_COMPLETED',rows=ledger))

if __name__=='__main__': main()
