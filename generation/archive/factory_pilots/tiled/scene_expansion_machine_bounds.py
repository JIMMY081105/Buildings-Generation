"""Small CPU-only actual USD measurements of four original machine assets."""
import argparse
import hashlib
import json
import os
import pathlib
import resource
import time
from datetime import datetime, timezone

os.environ['PXR_WORK_THREAD_LIMIT']='1'
os.environ['OMP_NUM_THREADS']='1'
resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
from pxr import Usd, UsdGeom, UsdUtils, Sdf, Work
Work.SetConcurrencyLimit(1)
ROOT=pathlib.Path('/cephfs/shared/yixing/outputs/factory80_20261011')
WORK=ROOT/'work'/'scene_expansion'

def utc(): return datetime.now(timezone.utc).isoformat()
def critical():
    assert ROOT.is_dir() and (ROOT/'work').is_dir()
    return [str(p) for pattern in ('CRITICAL_STOP*.json','work/CRITICAL_STOP*.json','work/*/CRITICAL_STOP*.json') for p in ROOT.glob(pattern)]
def fingerprint(path):
    real=path.resolve(strict=True)
    before=real.stat()
    value=hashlib.sha256()
    with real.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''): value.update(block)
    after=real.stat()
    signature=lambda s:[s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
    if signature(before)!=signature(after): raise RuntimeError('measured USD dependency changed')
    return dict(authored_path=str(path),actual_resolved_path=str(real),actual_size=after.st_size,actual_sha256=value.hexdigest(),stat_signature=signature(after))
def bbox(cache,prim,units):
    bound=cache.ComputeWorldBound(prim).ComputeAlignedRange()
    if bound.IsEmpty(): raise RuntimeError('actual geometry bound is empty')
    lo,hi=bound.GetMin(),bound.GetMax()
    return dict(prim_path=str(prim.GetPath()),min_stage_units=list(lo),max_stage_units=list(hi),
                size_stage_units=list(hi-lo),size_m=[float(v)*units for v in hi-lo])

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',required=True)
    parser.add_argument('--input-size',required=True,type=int)
    parser.add_argument('--input-sha256',required=True)
    args=parser.parse_args()
    input_path=pathlib.Path(args.input)
    assert input_path.resolve().is_relative_to(WORK.resolve())
    content=input_path.read_bytes()
    assert (len(content),hashlib.sha256(content).hexdigest())==(args.input_size,args.input_sha256)
    metadata=json.loads(content)
    assert metadata['unique_machine_metadata_ids']==len(metadata['machines'])==4
    result=dict(status='RUNNING',started_utc=utc(),input_actual_size=len(content),input_actual_sha256=hashlib.sha256(content).hexdigest(),
                input_manifest_sha256=metadata['manifest_actual_sha256'],cpu_only=True,work_thread_limit=1,rlimit_as_bytes=4*1024**3,records=[])
    for machine in metadata['machines']:
        if critical(): result.update(status='STOPPED_ACTUAL_CRITICAL',critical_files=critical()); break
        entry=dict(asset_id=machine['asset_id'],original_annotations=machine['original_annotations'],actual_ready_scene_refs=machine['actual_ready_scene_refs'])
        started=time.monotonic()
        try:
            identity='obja_'+machine['asset_id']
            path=WORK/'objects'/'objaverse'/identity/(identity+'.usda')
            if not path.is_file(): raise RuntimeError('exact task asset USDA unreadable')
            layers,assets,unresolved=UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(path)))
            if unresolved: raise RuntimeError('actual asset has unresolved dependencies:'+str(unresolved))
            receipts=[fingerprint(pathlib.Path(p)) for p in sorted(set([str(l.realPath or l.identifier) for l in layers]+list(assets)))]
            stage=Usd.Stage.Open(str(path),Usd.Stage.LoadAll)
            if not stage: raise RuntimeError('actual asset USD stage open failed')
            units=UsdGeom.GetStageMetersPerUnit(stage)
            cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),[UsdGeom.Tokens.default_,UsdGeom.Tokens.render,UsdGeom.Tokens.proxy],useExtentsHint=False)
            entry.update(status='PASS_ASSET_BOUNDS',actual_asset_path=str(path),actual_asset_resolved_path=str(path.resolve()),
                         asset_dependency_sha_receipts=receipts,asset_meters_per_unit=units,asset_up_axis=str(UsdGeom.GetStageUpAxis(stage)),
                         asset_geometry_bound=bbox(cache,stage.GetPseudoRoot(),units),asset_mesh_count=sum(p.IsA(UsdGeom.Mesh) for p in stage.Traverse()))
            del cache,stage,layers
            # Exactly one already-ready source context per asset; keep this probe small.
            source=machine['actual_ready_scene_refs'][0]
            source_path=pathlib.Path(source['source_path'])/'scene.usda'
            source_stage=Usd.Stage.Open(str(source_path),Usd.Stage.LoadAll)
            source_units=UsdGeom.GetStageMetersPerUnit(source_stage)
            source_cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),[UsdGeom.Tokens.default_,UsdGeom.Tokens.render,UsdGeom.Tokens.proxy],useExtentsHint=False)
            matched=[]
            for prim in source_stage.Traverse():
                references=prim.GetMetadata('references')
                authored=[ref.assetPath for ref in references.GetAddedOrExplicitItems()] if references else []
                if not any(identity in ref for ref in authored): continue
                measure=bbox(source_cache,prim,source_units)
                measure.update(authored_references=authored,world_transform=[list(row) for row in UsdGeom.XformCache().GetLocalToWorldTransform(prim)])
                matched.append(measure)
            entry.update(source_probe_scene=source['scene'],source_probe_path=str(source_path),source_root_actual_sha_receipt=fingerprint(source_path),
                         source_meters_per_unit=source_units,source_up_axis=str(UsdGeom.GetStageUpAxis(source_stage)),actual_matching_source_instance_count=len(matched),source_instances=matched)
            if not matched: entry['source_instance_status']='NO_DIRECT_CANONICAL_REFERENCE_MATCH_NO_INSTANCE_SIZE_CLAIM'
            else: entry['source_instance_status']='PASS_ACTUAL_DIRECT_REFERENCE_BOUNDS'
            del source_cache,source_stage
        except Exception as error:
            entry.update(status='FAIL' if 'asset_geometry_bound' not in entry else 'PASS_ASSET_BOUNDS_SOURCE_PROBE_FAILED',error=f'{type(error).__name__}: {error}')
        entry.update(elapsed_seconds=time.monotonic()-started,completed_utc=utc())
        result['records'].append(entry)
        print(json.dumps(dict(asset_id=entry['asset_id'],status=entry['status'],asset_size_m=entry.get('asset_geometry_bound',{}).get('size_m'),source_instance_count=entry.get('actual_matching_source_instance_count'),elapsed_seconds=entry['elapsed_seconds'])),flush=True)
    if result['status']=='RUNNING': result['status']='PASS' if len(result['records'])==4 and all(r['status']=='PASS_ASSET_BOUNDS' for r in result['records']) else 'PARTIAL'
    result.update(completed_utc=utc(),limitation='Actual USD AABB and annotations only. No render/industrial appearance/geometry gate acceptance; no arbitrary scaling or threshold changes. One source context per asset measured.')
    target=WORK/'industrial_machine_actual_bounds.json'
    temporary=target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(temporary,target)
    print(json.dumps(dict(status=result['status'],actual_output=str(target),actual_size=target.stat().st_size,actual_sha256=fingerprint(target)['actual_sha256'])),flush=True)

if __name__=='__main__': main()
