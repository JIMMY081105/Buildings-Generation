"""Freeze six real Infinigen tropical factories; no invented species or shader export."""
from pathlib import Path
import sys,os,json,hashlib,datetime,signal,time,traceback,inspect,textwrap
Path('/proc/self/oom_score_adj').write_text('1000');os.environ['OMP_NUM_THREADS']='8'
import bpy,numpy as np
from mathutils import Matrix
src=Path('/cephfs/shared/yixing/outputs/diversity54_20260908/runtime/infinigen_staircase_poc/infinigen/src');sys.path.insert(0,str(src))
from infinigen.assets.objects.tropic_plants.palm_tree import PalmTreeFactory
from infinigen.assets.objects.tropic_plants.coconut_tree import CoconutTreeFactory
from infinigen.assets.objects.monocot.banana import BananaMonocotFactory
from infinigen.core.util.math import FixedSeed
adapters={}
for cls,removed in [(PalmTreeFactory,'butil.delete([leaf])'),(CoconutTreeFactory,'butil.delete([leaf, coconut])')]:
 original=textwrap.dedent(inspect.getsource(cls.create_asset));adapted=original.replace(removed,'pass  # Keep GN ObjectInfo dependencies until evaluated mesh is frozen.');assert adapted!=original;scope=dict(sys.modules[cls.__module__].__dict__);exec(compile(adapted,'Museum29_TropicalGN_DependencyAdapter19','exec'),scope);cls.create_asset=scope['create_asset'];adapters[cls.__name__]={'original_method_sha256':hashlib.sha256(original.encode()).hexdigest(),'adapted_method_sha256':hashlib.sha256(adapted.encode()).hexdigest(),'change':'Retain leaf/fruit ObjectInfo source objects until actual dependency graph freeze; append RealizeInstances on final geometry output. Upstream disk files unchanged.'}
r=Path('/cephfs/shared/yixing/outputs/museum29_20261107');run=os.environ.get('M29_TROPICAL_RUN','tropical19');assert Path(run).name==run;out=r/'library/procedural_components'/run;out.mkdir(exist_ok=False);bpy.ops.wm.read_factory_settings(use_empty=True);rows=[]
def alarm(*_):raise TimeoutError('FACTORY_LIMIT300_SECONDS')
signal.signal(signal.SIGALRM,alarm)
for kind,cls in [('palm',PalmTreeFactory),('coconut',CoconutTreeFactory),('banana',BananaMonocotFactory)]:
 for index in range(2):
  seed=202613007+100*index+len(kind);assert 0<=seed<2**32;name=f'Tropical19_{kind}_{index}';before=set(bpy.data.objects);begin=time.monotonic();signal.alarm(300)
  try:
   with FixedSeed(seed):obj=cls(seed,coarse=True).create_asset()
   for modifier in obj.modifiers:
    if modifier.type!='NODES' or not modifier.node_group:continue
    tree=modifier.node_group
    for output in [n for n in tree.nodes if n.type=='GROUP_OUTPUT']:
     for socket in [s for s in output.inputs if s.type=='GEOMETRY' and s.is_linked]:
      link=socket.links[0];source=link.from_socket;tree.links.remove(link);realize=tree.nodes.new('GeometryNodeRealizeInstances');tree.links.new(source,realize.inputs['Geometry']);tree.links.new(realize.outputs['Geometry'],socket)
   bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get();ev=obj.evaluated_get(dg);me=bpy.data.meshes.new_from_object(ev,preserve_all_data_layers=True,depsgraph=dg);assert len(me.vertices)>100;me.calc_loop_triangles();original_triangles=len(me.loop_triangles);frozen=bpy.data.objects.new(name,me);bpy.context.scene.collection.objects.link(frozen);frozen.matrix_world=obj.matrix_world.copy();bpy.ops.object.select_all(action='DESELECT');frozen.select_set(True);bpy.context.view_layer.objects.active=frozen;bpy.ops.object.transform_apply(location=True,rotation=True,scale=True)
   if len(me.polygons)>50000:
    mod=frozen.modifiers.new('BoundedMuseumTropicalDetail19','DECIMATE');mod.ratio=50000/len(me.polygons);bpy.ops.object.modifier_apply(modifier=mod.name)
   v=np.asarray([q.co[:] for q in frozen.data.vertices]);lo=v.min(0);hi=v.max(0);pivot=(lo+hi)/2;pivot[2]=lo[2]
   for p in frozen.data.vertices:p.co-=__import__('mathutils').Vector(pivot)
   names=[q.name if q else 'UNBOUND' for q in frozen.data.materials];leafslots=[i for i,n in enumerate(names) if any(k in n.lower() for k in ['leaf','leaves','foliage','palm_plant']) and not any(k in n.lower() for k in ['stem','trunk'])];assert leafslots,('ACTUAL_FACTORY_LEAF_MATERIAL_SLOTS_UNIDENTIFIED',kind,names)
   frozen['museum_factory']=cls.__name__;frozen['museum_seed']=seed;frozen['museum_leaf_slots']=json.dumps(leafslots);frozen.hide_render=True;frozen.hide_viewport=True
   for ob in set(bpy.data.objects)-before:
    if ob!=frozen:bpy.data.objects.remove(ob,do_unlink=True)
   finalv=np.asarray([q.co[:] for q in frozen.data.vertices]);frozen.data.calc_loop_triangles();row={'key':name,'status':'PASS','factory':cls.__name__,'factory_seed':seed,'factory_source':str(Path(sys.modules[cls.__module__].__file__)),'factory_source_sha256':hashlib.sha256(Path(sys.modules[cls.__module__].__file__).read_bytes()).hexdigest(),'license':'BSD-3-Clause','original_evaluated_triangles':original_triangles,'final_triangles':len(frozen.data.loop_triangles),'normalized_actual_extent_m':np.ptp(finalv,axis=0).tolist(),'actual_material_names':names,'actual_leaf_slots':leafslots,'canonical_geometry_sha256':hashlib.sha256(finalv.tobytes()).hexdigest(),'elapsed_seconds':time.monotonic()-begin,'compatibility_adapter':adapters.get(cls.__name__),'scope':'Actual evaluated upstream geometry, normalized and bounded; final museum reassigns portable image materials, exact trunk/low-leaf collision, new final images required.'};rows.append(row);print('TROPICAL_FACTORY_FROZEN',name,row['final_triangles'],names,flush=True)
  except Exception as ex:
   rows.append({'key':name,'status':'FAILED','exception':repr(ex),'traceback':traceback.format_exc(),'elapsed_seconds':time.monotonic()-begin});(out/'GENERATIONS.json').write_text(json.dumps(rows,indent=2));raise
  finally:signal.alarm(0)
  (out/'GENERATIONS.json').write_text(json.dumps(rows,indent=2))
bpy.ops.wm.save_as_mainfile(filepath=str(out/'components.blend'));sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();licensepath=src.parent/'LICENSE';assert licensepath.is_file();(out/'INFINIGEN_LICENSE.txt').write_bytes(licensepath.read_bytes());doc={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'PASS','components':rows,'components_blend_sha256':sha(out/'components.blend'),'bytes':(out/'components.blend').stat().st_size,'source_license_sha256':sha(licensepath),'scope':'Six actual upstream tropical factory components ready; no museum19 building or native image accepted.'};(out/'RESULT.json').write_text(json.dumps(doc,indent=2));selected=r/'evidence/TROPICAL19_SELECTED.json';assert not selected.exists();selected.write_text(json.dumps({'status':'PASS','directory':str(out),'result_sha256':sha(out/'RESULT.json'),'components_blend_sha256':sha(out/'components.blend')},indent=2));print('TROPICAL_FACTORY19_COMPLETE',flush=True);sys.stdout.flush();os._exit(0)
