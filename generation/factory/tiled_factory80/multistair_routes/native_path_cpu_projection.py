"""CPU-only projection of the frozen native validator's deterministic path values.

Execute only selected unchanged pure AST statements. Never import or execute
the native module, SimulationApp, ray/sweep queries, or its main/gates.
"""
import ast,hashlib,math
from pathlib import Path
from types import SimpleNamespace
NATIVE=Path('/cephfs/shared/yixing/outputs/diversity54_20260908/evidence/frozen_sources/pipeline/validate_building_isaac.py')
SHA='63d74178d75be4cb5f4f57850579b3bc6a7fdfe1f090595b01afbfe9ce146ed8'
def require(v,s):
 if not v:raise ValueError(s)
def assigned(node,name):
 return isinstance(node,ast.Assign)and any(isinstance(t,ast.Name)and t.id==name for t in node.targets)
def coordinate_program(expected_sha=SHA):
 raw=NATIVE.read_bytes();require(hashlib.sha256(raw).hexdigest()==expected_sha==SHA,'Frozen native source SHA differs; no coordinate projection');tree=ast.parse(raw.decode());rotate=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='rotate_xy');main=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='main');body=main.body
 off=next(n for n in body if assigned(n,'off'));local=next(n for n in body if assigned(n,'local'));rotation=next(n for n in body if assigned(n,'rotation_deg'));sp=next(n for n in body if assigned(n,'sp'))
 rotation_record=next(n for n in body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Subscript)and isinstance(t.value,ast.Name)and t.value.id=='R'and isinstance(t.slice,ast.Constant)and t.slice.value=='stair_rotation_deg'for t in n.targets))
 loop=next(n for n in body if isinstance(n,ast.For)and isinstance(n.target,ast.Name)and n.target.id=='p'and isinstance(n.iter,ast.Name)and n.iter.id=='local');uxy=next(n for n in body if isinstance(n,ast.FunctionDef)and n.name=='uxy');directions=next(n for n in body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Tuple)and[t.id for t in t.elts]==['en','ex']for t in n.targets));tail=next(n for n in body if assigned(n,'tail'));approach=next(n for n in body if assigned(n,'approach'))
 selected=[rotate,off,rotation_record,local,rotation,sp,loop,uxy,directions,tail,approach]
 module=ast.Module(body=selected,type_ignores=[]);ast.fix_missing_locations(module)
 # A pinned, explicit pure subset; reject expansion into native SDK/device work.
 names={n.id for n in ast.walk(module)if isinstance(n,ast.Name)};require(not names.intersection({'APP','SimulationApp','isaacsim','subprocess','q','ray','sweep','stage'}),'Unexpected non-pure native coordinate statement')
 spans=[dict(start=n.lineno,end=n.end_lineno,node=type(n).__name__)for n in selected];return compile(module,str(NATIVE),'exec'),spans
def project_floor1(build,path,*,expected_sha=SHA):
 require(type(build)is dict and type(path)is dict and type(path.get('points_xyz_m'))is list and len(path['points_xyz_m'])>=2,'Real native traversal and build input required')
 require(type(build.get('stair_offset'))is list and len(build['stair_offset'])==3,'Native full XYZ offset required');values=[*build['stair_offset'],*[v for p in path['points_xyz_m']for v in p]];require(all(type(v)in(int,float)and math.isfinite(v)for v in values),'Finite real native coordinates required')
 code,spans=coordinate_program(expected_sha);scope=dict(math=math,b=build,path_contract=path,R={},args=SimpleNamespace(tail_m=1.2));exec(code,scope,scope)
 require(scope['approach']and all(len(p)==3 for p in scope['sp']),'Native path has no complete Floor1 approach');local=[round(float(v),4)for v in scope['approach'][-1]]
 translation=build.get('frame_translation_world_to_local_xyz_m',[0,0,0]);require(translation==[0,0,0],'This N2 CPU provider only accepts an identity world/local frame')
 return dict(source='SELECTED_UNCHANGED_NATIVE_PATH_VALUE_AST_ONLY',floor1_xyz_m=local,first_native_stair_xyz_m=[float(v)for v in scope['sp'][0]],last_native_stair_xyz_m=[float(v)for v in scope['sp'][-1]],native_source_sha256=SHA,executed_original_AST_spans=spans,native_sample_rounding_decimal_places=4,native_tail_m=1.2,original_native_main_called=False,SDK_or_physics_executed=False)
