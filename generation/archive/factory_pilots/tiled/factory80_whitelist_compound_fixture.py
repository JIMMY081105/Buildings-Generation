"""Independent small-fixture readback of own compound floor authoring.

Calls the original source-bound 5 cm surface voxelizer with fill=False.
No geometry checker or threshold is modified; this is not a building verdict.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics
from shapely.geometry import box

TASK = Path('/cephfs/shared/yixing/outputs/factory80_20261011')
REPO = Path('/cephfs/shared/yixing/code/Scenesmith-code-study/ProtoMotions-main')
sys.path[:0] = [str(TASK/'scripts'), str(REPO)]
import build_factory80_overlay as authored
from protomotions.simulator.isaaclab.utils import scene_geometry_baking as original


def ref(path):
    path = Path(path).resolve()
    return dict(path=str(path), bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def triangles(mesh):
    vertices=np.asarray(mesh.GetPointsAttr().Get(),dtype=np.float32)
    counts=mesh.GetFaceVertexCountsAttr().Get()
    indices=mesh.GetFaceVertexIndicesAttr().Get()
    faces=[];cursor=0
    for count in counts:
        face=list(indices[cursor:cursor+count]);cursor+=count
        faces.extend((face[0],face[j],face[j+1]) for j in range(1,count-1))
    return vertices,np.asarray(faces,dtype=np.int64)


def center_set(stage):
    records,excluded=original._iter_collision_meshes(stage,[])
    xforms=UsdGeom.XformCache(Usd.TimeCode.Default())
    units=float(UsdGeom.GetStageMetersPerUnit(stage))
    rows=[];centers=[]
    for record in records:
        if record.approximation!='none':raise ValueError('Fixture collision must remain exact none')
        prim=stage.GetPrimAtPath(record.prim_path)
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):raise ValueError('Fixture collider is not static')
        for piece in original.iter_collision_geometry_pieces(stage,record,units,xforms):
            points=original.voxelize_triangle_mesh(piece.vertices_m,piece.triangles,
                                                  voxel_size_m=.05,fill=False)
            centers.append(points)
            rows.append(dict(path=record.prim_path,approximation=record.approximation,
                             voxel_centers=len(points),actual_triangles=len(piece.triangles),
                             purpose=str(UsdGeom.Imageable(prim).GetPurposeAttr().Get())))
    points=np.concatenate(centers)
    # The original voxelizer produces a globally snapped 5 cm lattice. Compare
    # its actual integer lattice coordinates without changing rasterization.
    lattice=np.rint(points/.05).astype(np.int64)
    residual=float(np.max(np.abs(points-lattice*.05)))
    if residual>1e-9:raise ValueError('Original centers are not on the 5 cm lattice')
    unique=np.unique(lattice,axis=0)
    keys=set(map(tuple,unique.tolist()))
    return keys,dict(records=rows,excluded_count=excluded,unique_center_count=len(keys),
                     maximum_lattice_roundtrip_error_m=residual,
                     sorted_integer_centers_sha256=hashlib.sha256(unique.tobytes()).hexdigest())


def stage(path):
    result=Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(result,1.0);UsdGeom.SetStageUpAxis(result,'Z')
    root=UsdGeom.Xform.Define(result,'/World');result.SetDefaultPrim(root.GetPrim())
    mat=authored.material(result,'/World/Looks/Concrete',(.32,.34,.35))
    return result,mat


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=False)
    source_refs=[ref(authored.__file__),ref(original.__file__)]
    fixtures=[('four_buckets_with_hole',box(-1.15,-1.05,1.25,1.05).difference(box(-.35,-.3,.4,.35)),4.0),
              ('long_strip_refinement',box(9.25,-.55,21.25,.65),0.0)]
    results=[]
    for name,poly,z in fixtures:
        started=time.monotonic();directory=args.output_dir/name;directory.mkdir()
        record=dict(fixture=name,status='UNVERIFIED',cannot_support='小型合成夹层fixture；不证明整栋碰撞、站立、连通性、G25、Isaac或验收。')
        try:
            baseline,bmat=stage(directory/'unpartitioned.usda')
            authored.slab(baseline,'/World/Slab',poly,z,bmat)
            old_mesh=UsdGeom.Mesh(baseline.GetPrimAtPath('/World/Slab'))
            before_refinement=authored.refine_synthetic_collider(old_mesh)
            baseline.GetRootLayer().Save()
            compound,cmat=stage(directory/'compound.usda')
            receipt=authored.compound_slab(compound,'/World/Slab',poly,z,cmat)
            compound.GetRootLayer().Save()
            visual=UsdGeom.Mesh(compound.GetPrimAtPath(receipt['visual_path']))
            if not compound.GetPrimAtPath('/World/Slab').IsA(UsdGeom.Xform):raise ValueError('Compound root must be an Xform')
            if UsdPhysics.CollisionAPI(visual.GetPrim()).GetCollisionEnabledAttr().Get() is not False:raise ValueError('Visual collision unexpectedly enabled')
            vertices,faces=triangles(old_mesh);baseline_expanded=vertices[faces]
            actual_vertices,actual_faces=triangles(visual);visual_expanded=actual_vertices[actual_faces]
            reconstruction=np.empty_like(visual_expanded)
            collision_triangles=[]
            for part in receipt['triangle_surface_partition']['parts']:
                mesh=UsdGeom.Mesh(compound.GetPrimAtPath(part['path']))
                pv,pf=triangles(mesh);expanded=pv[pf]
                reconstruction[part['original_visual_triangle_indices']]=expanded
                collision_triangles.extend(row.tobytes() for row in expanded)
            exact_baseline=np.array_equal(baseline_expanded,visual_expanded)
            exact_reconstruction=np.array_equal(visual_expanded,reconstruction)
            exact_multiset=Counter(row.tobytes() for row in visual_expanded)==Counter(collision_triangles)
            left,left_record=center_set(Usd.Stage.Open(str(directory/'unpartitioned.usda')))
            right,right_record=center_set(Usd.Stage.Open(str(directory/'compound.usda')))
            missing=sorted(left-right);extra=sorted(right-left)
            record.update(status='PASS' if exact_baseline and exact_reconstruction and exact_multiset and not missing and not extra else 'FAIL',
                authoring_receipt=receipt,baseline_refinement=before_refinement,
                actual_baseline_visual_triangles_identical=exact_baseline,
                actual_partition_reconstruction_identical=exact_reconstruction,
                actual_no_added_or_missing_triangles=exact_multiset,
                independent_baseline_expanded_sha256=hashlib.sha256(baseline_expanded.tobytes()).hexdigest(),
                independent_partition_expanded_sha256=hashlib.sha256(reconstruction.tobytes()).hexdigest(),
                unpartitioned=left_record,compound=right_record,
                actual_center_sets_identical=left==right,missing_center_count=len(missing),extra_center_count=len(extra),
                missing_sample_integer_centers=missing[:30],extra_sample_integer_centers=extra[:30],
                stage_refs=[ref(directory/'unpartitioned.usda'),ref(directory/'compound.usda')])
        except Exception as error:
            record.update(status='ACTUAL_TOOL_FAILURE',error=type(error).__name__+': '+str(error))
        record['elapsed_seconds']=time.monotonic()-started;results.append(record)
    unchanged=[ref(item['path'])==item for item in source_refs]
    result=dict(schema='Factory80CompoundSlabSmallFixtureAuditV1',generated_at_utc=datetime.now(timezone.utc).isoformat(),
        status='PASS' if all(r['status']=='PASS' for r in results) and all(unchanged) else 'FAIL_OR_TOOL_FAILURE',
        actual_method='Original voxelize_triangle_mesh(voxel_size_m=0.05,fill=False), exact none approximation',
        immutable_source_refs=source_refs,source_bytes_unchanged=unchanged,fixtures=results,
        cannot_support='仅小型fixture表面/5cm中心集合对照；不能支持整栋检查或任何门禁放行。')
    with (args.output_dir/'RESULT.json').open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps(dict(status=result['status'],fixtures=[dict(fixture=r['fixture'],status=r['status'],actual_center_sets_identical=r.get('actual_center_sets_identical'),missing=r.get('missing_center_count'),extra=r.get('extra_center_count'),error=r.get('error')) for r in results])))


if __name__=='__main__':main()
