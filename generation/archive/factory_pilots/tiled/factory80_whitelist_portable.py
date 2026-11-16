"""Location-only native asset clone, with exact inverse layer and dependency checks."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path
from pxr import Ar, Sdf, UsdUtils

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ref(path): return dict(path=str(Path(path).resolve()),bytes=Path(path).stat().st_size,sha256=sha(path))

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--asset-dir',type=Path,required=True); args=parser.parse_args()
    raw=args.asset_dir; source=raw/'staircase.usd'; before=ref(source)
    portable=raw.parent/'portable'; portable.mkdir(exist_ok=False); (portable/'assets').mkdir()
    candidates=list((raw/'assets').glob('*.png'))
    if not candidates: raise ValueError('No actual exported native texture files')
    allowed={sha(file) for file in candidates}
    layer=Sdf.Layer.FindOrOpen(str(source)); clone=Sdf.Layer.CreateAnonymous(); clone.TransferContent(layer)
    changes=[]; inverse={}
    def localize(value):
        if not value: return value
        anchored=Sdf.ComputeAssetPathRelativeToLayer(layer,value)
        resolved=str(Ar.GetResolver().Resolve(anchored))
        original=Path(resolved) if resolved else Path(anchored)
        method='resolved_authored_path'
        if not original.is_file():
            matches=[file for file in candidates if file.name==Path(value).name]
            if len(matches)!=1: raise ValueError('Missing or ambiguous exported resource: '+value)
            original=matches[0]; method='unique_actual_export_basename'
        if sha(original) not in allowed: raise ValueError('Resource outside actual exported texture hash set: '+value)
        target=portable/'assets'/original.name
        if not target.exists(): shutil.copyfile(original,target)
        if sha(target)!=sha(original): raise ValueError('Portable texture checksum differs')
        relative='assets/'+target.name
        if relative in inverse and inverse[relative]!=value: raise ValueError('Ambiguous inverse path; no geometric mutation permitted')
        inverse[relative]=value; changes.append(dict(authored=value,original=ref(original),localized=ref(target),resolution_method=method))
        return relative
    UsdUtils.ModifyAssetPaths(clone,localize)
    restored=Sdf.Layer.CreateAnonymous(); restored.TransferContent(clone); UsdUtils.ModifyAssetPaths(restored,lambda value:inverse.get(value,value))
    if restored.ExportToString()!=layer.ExportToString(): raise ValueError('Location-only clone did not replay exact original layer')
    target=portable/'staircase.usda'
    if not clone.Export(str(target)): raise ValueError('Portable native stage export failed')
    _,_,missing=UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(target)))
    if missing: raise ValueError('Portable stage still has missing dependencies: '+str(missing))
    if ref(source)!=before: raise ValueError('Raw native asset changed during portability check')
    result=dict(schema='Factory80NativeLocationOnlyCloneV1',status='PASS',source=before,target=ref(target),changes=changes,exact_inverse_layer_equality=True,missing_dependencies=list(missing),source_unchanged=True,unsupported='Resource portability and exact location-only layer roundtrip; not geometry, physics, G25/graph, RGB appearance or acceptance.')
    with (portable/'RESOURCE_RELOCATION.json').open('x') as stream: json.dump(result,stream,indent=2); stream.write('\n')
    print(json.dumps(result))

if __name__=='__main__': main()
