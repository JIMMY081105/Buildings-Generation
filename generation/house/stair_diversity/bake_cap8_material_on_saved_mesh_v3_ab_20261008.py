"""Bake native materials on an existing audited saved mesh; create no staircase or collider."""
import argparse,sys,os,hashlib,inspect,json
from pathlib import Path
import bpy,numpy as np
from pxr import Usd,UsdGeom
W=Path('/cephfs/shared/yixing/outputs/diversity54_20260908');sys.path.insert(0,str(W/'scripts'))
from evidence_common import ref,read_json,write_new,utc_now
from native_style_overrides import surface_registry
from infinigen.core import surface
from infinigen.core.util.math import FixedSeed
from native_material_bake_only_v5 import bake
import generate_staircase_diversity_v2 as gen
p=argparse.ArgumentParser();p.add_argument('--candidate',type=Path,required=True);p.add_argument('--material',choices=('BrushedMetal','MetalBasic','Marble','Wood','WhitePlywood','BlackPlywood','BlondePlywood'),required=True);p.add_argument('--seed',type=int,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();assert os.environ['CUDA_VISIBLE_DEVICES']==''
if (W/'evidence/signature_cap8_r2_20261005/STOP.json').exists():raise SystemExit(2)
c=read_json(a.candidate);metrics=read_json(c['metrics']['path']);a.output.mkdir(parents=True,exist_ok=False)
for r in (c['metrics'],c['geometry_source_blend'],c['asset_usd'],c['corrected_usd_audit']):assert ref(r['path'])==r
assert read_json(c['corrected_usd_audit']['path'])['passed'] is True
write_new(a.output/'INPUT.json',{'candidate':ref(a.candidate),'source_blend':c['geometry_source_blend'],'source_native_asset':c['asset_usd'],'material_requested':a.material,'material_seed':a.seed,'geometry_generated':False,'new_stair_factory_create_asset_called':False,'new_collision_generation':False,'geometry_gates_executed':False,'script':ref(__file__)})
bpy.ops.wm.open_mainfile(filepath=c['geometry_source_blend']['path']);objects=[o for o in bpy.data.objects if o.type=='MESH'];assert len(objects)==1;obj=objects[0];assert not obj.modifiers
def array(value,dtype):
 ar=np.asarray(value,dtype=dtype);return {'shape':list(ar.shape),'dtype':ar.dtype.str,'sha256':hashlib.sha256(ar.tobytes()).hexdigest()}
def mesh_snapshot():
 return {'vertices':array([v.co[:] for v in obj.data.vertices],'<f4'),'edges':array([e.vertices[:] for e in obj.data.edges],'<i4'),'loops':array([l.vertex_index for l in obj.data.loops],'<i4'),'polygons':array([(p.loop_start,p.loop_total) for p in obj.data.polygons],'<i4'),'matrix_world':array(obj.matrix_world,'<f8'),'uv_layers':{u.name:array([v.uv[:] for v in u.data],'<f4') for u in obj.data.uv_layers},'role_masks':{n:array(surface.read_attr_data(obj,n,domain='FACE',result_dtype=bool),'?') for n in ('treads','sides','handrails','posts','glasses') if n in obj.data.attributes}}
def native_role_inventory():
 result={}
 for n in ('treads','sides','rails','handrails','posts','glasses'):
  if n not in obj.data.attributes:continue
  attr=obj.data.attributes[n];assert attr.domain=='FACE'
  values=np.asarray(surface.read_attr_data(obj,n,domain='FACE',result_dtype=np.float32),dtype='<f4')
  result[n]={'domain':'FACE','shape':list(values.shape),'dtype':values.dtype.str,'sha256':hashlib.sha256(values.tobytes()).hexdigest(),'positive_face_count':int(np.count_nonzero(values))}
 return result
before_roles=native_role_inventory();actual_roles=('treads','sides') if 'sides' in obj.data.attributes else ('treads',)
if actual_roles==('treads',):
 assert metrics['style']['has_sides'] is False and metrics['style']['has_rail'] is True and metrics['style']['support_type'] in ('single-rail','double-rail')
 assert before_roles['rails']['positive_face_count']>0 and before_roles['treads']['positive_face_count']>0
 tm=surface.read_attr_data(obj,'treads',domain='FACE',result_dtype=bool);rm=surface.read_attr_data(obj,'rails',domain='FACE',result_dtype=bool);assert not np.any(tm & rm)

before=mesh_snapshot();calls=[];cls=surface_registry()[a.material];native=cls();assert type(native).__name__==a.material
with FixedSeed(a.seed):
 for role in actual_roles:
  mask=surface.read_attr_data(obj,role,domain='FACE',result_dtype=bool);assert mask.sum()>0;old=np.asarray([p.material_index for p in obj.data.polygons]);old_count=len(obj.material_slots)
  native.apply(obj,selection=role);new=np.asarray([p.material_index for p in obj.data.polygons]);assert len(obj.material_slots)==old_count+1
  record={'role':role,'factory_class':type(native).__module__+'.'+type(native).__name__,'factory_source':ref(inspect.getfile(cls)),'selected_faces':int(mask.sum()),'new_slot':old_count,'actual_shader':obj.material_slots[old_count].material.name,'selected_faces_use_new_native_shader':bool(np.all(new[mask]==old_count)),'unselected_faces_material_indices_identical':bool(np.array_equal(old[~mask],new[~mask]))}
  calls.append(record);assert record['selected_faces_use_new_native_shader'] and record['unselected_faces_material_indices_identical'],record
assert mesh_snapshot()==before,'Native material assignment changed saved mesh/UV/placement'
role_proof={'status':'PASS','calls':calls,'before_geometry_and_original_uv':before,'after_geometry_and_original_uv':mesh_snapshot(),'geometry_byte_arrays_identical':True,'factory_create_asset_called':False,'geometry_generated':False,'geometry_gates_executed':False,'utc':utc_now()};assert native_role_inventory()==before_roles
role_proof.update(actual_material_assignment_roles=list(actual_roles),side_role_absent_in_original_mesh='sides' not in before_roles,native_role_inventory_before=before_roles,native_role_inventory_after=native_role_inventory(),tread_and_rail_face_masks_disjoint=True if actual_roles==('treads',) else None,source_candidate=ref(a.candidate),source_blend=c['geometry_source_blend'],source_generation_metrics=c['metrics'])
write_new(a.output/'ACTUAL_NATIVE_FACE_MATERIAL_ASSIGNMENT.json',role_proof)
gen.add_static_export_modifier(obj);blueprint=gen.kinematic_compiler.compile(obj);gen.butil.apply_modifiers(obj);blueprint['name']='staircase';assert mesh_snapshot()==before,'Identity simulation descriptor changed original mesh'
bpy.ops.wm.save_as_mainfile(filepath=str(a.output/'material_source.blend'));source=Usd.Stage.Open(c['asset_usd']['path'],Usd.Stage.LoadAll)
baked,metadata=bake(obj,blueprint,source,a.output/'native_bake',use_gpu=False);actual=Usd.Stage.Open(str(baked),Usd.Stage.LoadAll)
def visual(st):
 prims=[p for p in st.Traverse() if p.IsA(UsdGeom.Mesh) and '/visual/' in str(p.GetPath())];assert len(prims)==1;m=UsdGeom.Mesh(prims[0]);points=np.asarray(m.GetPointsAttr().Get());counts=np.asarray(m.GetFaceVertexCountsAttr().Get());indices=np.asarray(m.GetFaceVertexIndicesAttr().Get());assert (counts==3).all();return points,{tuple(sorted(map(int,t))) for t in indices.reshape(-1,3)}
sp,si=visual(source);dp,di=visual(actual);assert np.array_equal(sp,dp) and si==di,'Material-only bake changed existing source vertices or triangle sets'
assert ref(c['geometry_source_blend']['path'])==c['geometry_source_blend'] and ref(c['asset_usd']['path'])==c['asset_usd']
measured=type(native).__name__;style=dict(metrics['style']);style.update(tread_material_factory=measured,step_material_factory=measured)
mr=a.output/'MATERIAL_METRICS.json';write_new(mr,{'schema':'SignatureCap8MaterialOnlyMeasuredNativeFactoryV1','style':style,'actual_face_material_assignment':ref(a.output/'ACTUAL_NATIVE_FACE_MATERIAL_ASSIGNMENT.json'),'source_generation_metrics':c['metrics'],'material_generation_only':True,'geometry_generated':False,'geometry_gates_executed':False,'new_stair_factory_create_asset_called':False,'formal_acceptance_added':False})
write_new(a.output/'DONOR.json',{'schema':'SignatureCap8MaterialOnlyDerivedNativeDonorV1','status':'MATERIAL_ONLY_NATIVE_BAKE_MEASURED','building':c['building'],'asset':ref(baked),'metrics':ref(mr),'material_prim':'/Asset/Looks/geom_0_mat','original_audited_candidate':ref(a.candidate),'original_corrected_usd_audit':c['corrected_usd_audit'],'actual_face_material_assignment':ref(a.output/'ACTUAL_NATIVE_FACE_MATERIAL_ASSIGNMENT.json'),'native_material_bake_metadata':ref(metadata),'actual_cpu_bake':ref(a.output/'NATIVE_CPU_BAKE_RESULT.json'),'vertex_arrays_identical':True,'triangle_sets_identical':True,'geometry_generated':False,'geometry_gates_executed':False,'utc':utc_now(),'工作完成状态':'在已有保存网格上只赋原生材质并烘焙，未创建楼梯或碰撞体','验证状态':'实际原生工厂与面材质分配、原网格/UV/变换字节和烘焙顶点三角对应已比较','不可支持':'没有新几何门禁、五视角或实际建筑签名，不支持材质成功、READY 或正式验收。'})
print(c['building'],'ACTUAL_MATERIAL_ONLY_NATIVE_DONOR',str(a.output/'DONOR.json'),flush=True)
