"""Licensed exhibition assets, unique wall art and physically supported displays."""
from pathlib import Path
import collections,fcntl,hashlib,io,json,math,random
import bpy,numpy as np
from mathutils import Vector,Matrix
from PIL import Image,ImageDraw
from shapely.geometry import Point,box as rect
from shapely import wkt

def sources(B):
 rows=[]
 for row in B.asset_rows:
  paths=row.get('paths',[]) or [row.get('path','')];p=next((p for p in paths if Path(p).suffix.lower() in ['.gltf','.glb']),None)
  if p:rows.append({**row,'path':p})
 old=B.root/'library/reused_museum_v1/polyhaven/DOWNLOADS.json'
 if old.exists():
  for row in json.loads(old.read_text())['assets']:
   p=Path(row['path'].replace('/cephfs/shared/yixing/outputs/museum_v1_20261104/assets',str(B.root/'library/reused_museum_v1')))
   if p.exists():rows.append({**row,'path':str(p)})
 lookup={row['source']+':'+str(row['id']):row for row in rows};bound=B.spec.get('exhibition',{}).get('source_keys')
 if bound is not None:
  assert all(key in lookup for key in bound),'BOUND_MODEL_MISSING_FROM_LIBRARY';lookup={key:lookup[key] for key in bound}
 return lookup

def import_model(B,row):
 key=row['source']+':'+str(row['id'])
 if key in B.templates:return B.templates[key]
 from scan_color_model import inspect_color
 color24=inspect_color(row['path']);assert color24['status']=='PASS','ADJUST24_SOURCE_MODEL_HAS_NO_BASECOLOR_OR_VERTEX_COLOR_REPLACE_OR_DOCUMENT_REAL_MONOCHROME:'+key
 decoder=B.root/'runtime/draco/libextern_draco.so'
 if decoder.exists():__import__('os').environ['BLENDER_EXTERN_DRACO_LIBRARY_PATH']=str(decoder)
 before=set(bpy.data.objects);bpy.ops.import_scene.gltf(filepath=row['path']);new=set(bpy.data.objects)-before;meshes=[o for o in new if o.type=='MESH'];helpers=[o for o in new if o.type!='MESH'];assert meshes,'NO_SOURCE_MESHES'
 bpy.ops.object.select_all(action='DESELECT')
 for ob in meshes:ob.select_set(True)
 bpy.context.view_layer.objects.active=meshes[0]
 if len(meshes)>1:bpy.ops.object.join()
 ob=bpy.context.view_layer.objects.active
 if ob.data.shape_keys or ob.modifiers:
  graph=bpy.context.evaluated_depsgraph_get();baked=bpy.data.meshes.new_from_object(ob.evaluated_get(graph),preserve_all_data_layers=True,depsgraph=graph);ob.data=baked;ob.modifiers.clear()
 xf=ob.matrix_world.copy();ob.parent=None;ob.matrix_world=xf;bpy.ops.object.select_all(action='DESELECT');ob.select_set(True);bpy.context.view_layer.objects.active=ob;bpy.ops.object.transform_apply(location=True,rotation=True,scale=True)
 pose=B.spec['exhibition'].get('source_orientation_deg',{}).get(key)
 if pose:
  before_pose=np.asarray([v.co[:] for v in ob.data.vertices]);ob.rotation_mode='XYZ';ob.rotation_euler=tuple(math.radians(a) for a in pose);bpy.context.view_layer.update();bpy.ops.object.transform_apply(location=False,rotation=True,scale=False);after_pose=np.asarray([v.co[:] for v in ob.data.vertices]);assert not np.allclose(before_pose,after_pose),'SOURCE_ORIENTATION_PARAMETER_DID_NOT_CHANGE_GEOMETRY'
 limit=80000 if '341c96cd-f967-4540-8ed1-d3fc56d31f12' in str(row['id']) else 60000 if any(x in str(row['id']).lower() for x in ['jacaranda_tree','tree_small']) else 18000
 if len(ob.data.polygons)>limit:
  mod=ob.modifiers.new('BoundedMuseumDisplayDetail','DECIMATE');mod.ratio=limit/len(ob.data.polygons);bpy.ops.object.modifier_apply(modifier=mod.name)
 from scan_materials import adapt_scan_materials
 adapt_scan_materials(B,row,ob)
 B.model_import_records=getattr(B,'model_import_records',{});B.model_import_records.setdefault(key,{}).update(source_color24=color24)
 v=np.asarray([q.co[:] for q in ob.data.vertices]);pivot=(v.min(0)+v.max(0))/2;pivot[2]=v[:,2].min()
 for q in ob.data.vertices:q.co-=Vector(pivot)
 ob.name='SOURCE_'+str(row['id']);ob.hide_render=True;ob.location=(0,0,-100);ob['museum_collision']=False
 for other in helpers:
  if other.name in bpy.data.objects:bpy.data.objects.remove(other,do_unlink=True)
 for im in bpy.data.images:
  if max(im.size)>2048:
   ratio=2048/max(im.size);im.scale(max(1,round(im.size[0]*ratio)),max(1,round(im.size[1]*ratio)));p=B.tex/('scaled_'+hashlib.sha256(im.name.encode()).hexdigest()[:16]+'.png');im.filepath_raw=str(p);im.file_format='PNG';im.save()
 B.templates[key]=ob;return ob

def place(B,row,name,x,y,z,maxbox=(1.2,1.2,1.5),yaw=0):
 key=row['source']+':'+str(row['id']);B.model_counts=getattr(B,'model_counts',collections.Counter());assert B.model_counts[key]<3,'MODEL_REUSE_OVER_THREE '+key
 B.model_digest_cache=getattr(B,'model_digest_cache',{})
 if row['path'] not in B.model_digest_cache:B.model_digest_cache[row['path']]=hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()
 digest=B.model_digest_cache[row['path']];B.model_digest_counts=getattr(B,'model_digest_counts',collections.Counter());assert B.model_digest_counts[digest]<3,'IDENTICAL_SOURCE_BYTES_REUSED_OVER_THREE '+key
 original=import_model(B,row);ob=original.copy();ob.data=original.data;B.scene.collection.objects.link(ob);ob.name=name;ob.hide_render=False;ob.location=(0,0,0);ob.rotation_mode='XYZ';ob.rotation_euler=(0,0,yaw);ob.scale=(1,1,1);lo,hi=B.bounds(ob);scale=min(np.asarray(maxbox)/np.maximum(hi-lo,1e-7));ob.scale=(scale,scale,scale);lo,hi=B.bounds(ob);ob.location+=Vector((x-(lo[0]+hi[0])/2,y-(lo[1]+hi[1])/2,z-lo[2]-.002));ob['museum_collision']=True;ob['museum_category']='licensed_exhibit';lo,hi=B.bounds(ob);actual_yaw=math.atan2(ob.matrix_world[1][0],ob.matrix_world[0][0]);assert abs(math.atan2(math.sin(actual_yaw-yaw),math.cos(actual_yaw-yaw)))<1e-5,'SOURCE_EXHIBIT_YAW_NOT_APPLIED';B.model_counts[key]+=1
 B.model_digest_counts[digest]+=1;B.licenses[key]={k:row.get(k) for k in ['source','id','license','url']};B.licenses[key]['path']=row['path'];B.licenses[key]['source_sha256']=digest;B.layout.append({'name':name,'kind':'exhibit','source_key':key,'source_model_sha256':digest,'source_asset_title':row.get('title') or str(row['id']),'theme':B.spec['exhibition']['theme'],'actual_min_m':lo.tolist(),'actual_max_m':hi.tolist(),'support_z_m':z,'support_embed_m':z-float(lo[2]),'yaw_rad':yaw,'scale':scale,'collision':'exact_same_evaluated_visual_mesh','source_model_use_count_in_building':B.model_counts[key],'source_byte_use_count_in_building':B.model_digest_counts[digest]});return ob

def reserve_art(B,count):
 rows=[row for row in B.asset_rows if row.get('source')=='Cleveland Museum of Art' and row.get('share_license_status')=='CC0'];family=B.spec['style_family'];words=['japan','china','korea'] if family=='east_asian' else ['france','germany','italy','netherlands','britain','united states','flanders']
 bound=B.spec.get('exhibition',{}).get('painting_ids')
 if bound is not None:
  lookup={str(row['id']):row for row in rows};assert len(bound)>=count and len(set(bound))==len(bound) and all(key in lookup for key in bound),'BOUND_UNIQUE_PAINTING_MISSING';return [lookup[key] for key in bound[:count]]
 rows.sort(key=lambda q:(-sum(w in str(q.get('culture',[])).lower() for w in words),str(q['id'])));lock=(B.root/'evidence/ART_RESERVATIONS.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX);p=B.root/'evidence/ART_RESERVATIONS.json';state=json.loads(p.read_text()) if p.exists() else {};bid=B.spec['building_id'];current=state.get(bid,[]);used={x for b,ids in state.items() if b!=bid for x in ids};lookup={str(q['id']):q for q in rows}
 if len(current)<count:
  for row in rows:
   key=str(row['id'])
   if key not in used and key not in current:current.append(key)
   if len(current)>=count:break
 assert len(current)>=count,'INSUFFICIENT_UNIQUE_LICENSED_ARTWORKS';state[bid]=current;p.write_text(json.dumps(state,indent=2));fcntl.flock(lock,fcntl.LOCK_UN);return [lookup[x] for x in current[:count]]

def procedural_art(B,index):
 if B.spec['building_id'] in ['M10','M14']:
  from museum_modern_art import artwork
  return artwork(B,index)
 seed=B.spec['seed']*1000+index;rng=np.random.default_rng(seed);im=Image.new('RGB',(1024,768),(216,207,184));d=ImageDraw.Draw(im);palette=[(29,45,60),(160,46,36),(204,156,45),(60,102,98),(222,212,184)]
 for k in range(10):
  x,y=rng.integers(-80,900,2);w,h=rng.integers(90,460,2);color=palette[int(rng.integers(len(palette)))];d.rectangle((int(x),int(y),int(x+w),int(y+h)),fill=color) if k%3 else d.ellipse((int(x),int(y),int(x+w),int(y+h)),fill=color)
 for _ in range(5):
  p=rng.integers(50,970,(2,2));d.line([tuple(map(int,q)) for q in p],fill=(22,25,28),width=int(rng.integers(4,13)))
 p=B.tex/f'{B.spec["building_id"]}_OriginalAbstract_{index:03}.png';im.save(p);return {'source':'Museum29 procedural original art','id':B.spec['building_id']+f'_abstract_{index:03}','title':'Original geometric abstraction '+str(index),'license':'procedural','path':str(p),'dimensions':list(im.size)}

def painting(B,row,name,anchor,yaw,floor):
 im=bpy.data.images.load(row['path'],check_existing=True);aspect=im.size[0]/im.size[1];h=min(1.22,1.8/max(aspect,1e-8));w=h*aspect;material='Artwork_'+str(row['id']);m=bpy.data.materials.new(material);m.use_nodes=True;bs=m.node_tree.nodes['Principled BSDF'];bs.inputs['Roughness'].default_value=.78;tx=m.node_tree.nodes.new('ShaderNodeTexImage');tx.image=im;m.node_tree.links.new(tx.outputs['Color'],bs.inputs['Base Color']);B.materials[material]=m
 ob=B.mesh(name+'_Canvas',[(-w/2,-.050,-h/2),(w/2,-.050,-h/2),(w/2,-.050,h/2),(-w/2,-.050,h/2)],[(0,1,2,3)],material,False,'painting');uv=ob.data.uv_layers.active
 for loop,value in zip(uv.data,[(0,0),(1,0),(1,1),(0,1)]):loop.uv=value
 parts=[ob,B.box(name+'_Backing',(-w/2,-.049,-h/2),(w/2,.045,h/2),'Wood',True,'painting_support')];frame='Brass' if B.spec['style_family'] in ['neoclassical','art_deco','victorian_natural'] else 'Wood' if B.spec['style_family']=='east_asian' else 'Iron'
 for k,(margin,thick,depth) in enumerate([(.015,.065,.095),(.055,.025,.112)]):
  # One mitered, watertight rectangular ring per profile. Separate overlapping
  # horizontal and vertical front faces caused the four black corner patches.
  outer=[(-w/2-margin-thick/2,-h/2-margin-thick/2),(w/2+margin+thick/2,-h/2-margin-thick/2),(w/2+margin+thick/2,h/2+margin+thick/2),(-w/2-margin-thick/2,h/2+margin+thick/2)];inner=[(-w/2-margin+thick/2,-h/2-margin+thick/2),(w/2+margin-thick/2,-h/2-margin+thick/2),(w/2+margin-thick/2,h/2+margin-thick/2),(-w/2-margin+thick/2,h/2+margin-thick/2)];v=[(x,y,z) for y in [-depth,-.040] for ring in [outer,inner] for x,z in ring];faces=[]
  for j in range(4):
   q=(j+1)%4;faces.extend([(j,q,4+q,4+j),(8+j,12+j,12+q,8+q),(j,8+j,8+q,q),(4+j,4+q,12+q,12+j)])
  parts.append(B.mesh(name+f'_MiteredFrame{k}',v,faces,frame,True,'frame'))
 for item in parts:item.location=anchor;item.rotation_euler.z=yaw
 front=Vector((math.sin(yaw),-math.cos(yaw),0));loc=Vector(anchor)+front*.42+Vector((0,0,h/2+.22));B.area(name+'_PictureLight',loc,anchor,35,.55,(1,.94,.84));B.tube(name+'_LightArm',[tuple(Vector(anchor)+Vector((0,0,h/2+.12))),tuple(loc)],.012,frame,True,'fixture')
 key=row['source']+':'+str(row['id']);B.licenses[key]={k:row.get(k) for k in ['source','id','title','license','url','share_license_status']};B.licenses[key]['image_sha256']=hashlib.sha256(Path(row['path']).read_bytes()).hexdigest();B.layout.append({'name':name,'kind':'painting','source_key':key,'floor':floor,'image_dimensions':list(im.size),'canvas_dimensions_m':[w,h],'actual_aspect_error':abs(w/h-aspect),'anchor_world_m':list(anchor),'wall_supported':True,'frame_concentric':True,'lower_frame_z_m':anchor[2]-h/2-.10,'source_public_license':row.get('share_license_status') or row.get('license'),'facing_normal_world':list(front)})

def abstract_sculpture(B,name,x,y,z,index):
 from staircase_core import Geometry
 rng=np.random.default_rng(B.spec['seed']+index);phase=float(rng.uniform(0,math.tau));amp=float(rng.uniform(.16,.34));depth=float(rng.uniform(.16,.37));thickness=float(rng.uniform(.085,.135));t=np.linspace(0,math.tau,128);points=[((1+amp*math.cos(3*q+phase))*math.cos(2*q),depth*math.sin(3*q+phase),(1+amp*math.cos(3*q+phase))*math.sin(2*q)) for q in t];g=Geometry();g.tube(name,points,thickness);p=g.parts[0]
 # Close the shared end ring; coincident cap faces are not retained.
 endring=len(p['vertices'])-8;p['vertices']=p['vertices'][:-8];p['faces']=[[a-endring if a>=endring else a for a in f] for f in p['faces'][:-2]];v=np.asarray(p['vertices']);lo=v.min(0);hi=v.max(0);scale=min(np.array([1.35,1.35,1.7])/(hi-lo));v=(v-(lo+hi)/2)*scale;canonical=hashlib.sha256(v.tobytes()).hexdigest();v[:,0]+=x;v[:,1]+=y;v[:,2]+=z-v[:,2].min()-.002;B.mesh(name,v,p['faces'],'Steel' if index%2 else 'StairTread',True,'procedural_exhibit');B.layout.append({'name':name,'kind':'exhibit','source_key':'procedural:'+B.spec['building_id']+':sculpture:'+str(index),'support_z_m':z,'actual_min_m':v.min(0).tolist(),'actual_max_m':v.max(0).tolist(),'support_embed_m':.002,'geometry_sha256':hashlib.sha256(v.tobytes()).hexdigest(),'canonical_shape_sha256':canonical,'shape_parameters':{'radial_amplitude':amp,'minor_depth':depth,'tube_radius':thickness}})

def internal_gallery_door(B,model,room,level):
 from building_core import extrude
 from staircase_core import Geometry
 s=B.spec;w,h=room['size'];z=level*s['floor_height_m'];partition_shape=wkt.loads(model['storey_footprints_wkt'][level]) if s.get('brutalist26') else room['shape'];wall_y=partition_shape.bounds[3]-4.0 if s.get('brutalist26') else h*.40;door_w=2.4;g=Geometry();wall=partition_shape.intersection(rect(-w/2,wall_y-.10,w/2,wall_y+.10)).difference(rect(-door_w/2,wall_y-.20,door_w/2,wall_y+.20))
 extrude(g,f'GalleryPartition_F{level+1}',wall,z,z+3.5,'gallery_wall','Wall')
 for row in g.parts:B.mesh(row['name'],row['vertices'],row['faces'],'Wall',True,'gallery_wall')
 B.box(f'GalleryPartitionLintel_F{level+1}',(-door_w/2,wall_y-.1,z+3.05),(door_w/2,wall_y+.1,z+3.5),'Wall',True,'gallery_wall')
 frame='Steel' if s['style_family']=='marine' else 'Ceiling' if s['style_family']=='modern' else 'Wood';B.component('DoorCasing',f'GalleryDoorFrame_F{level+1}',frame,(0,wall_y,z),(door_w+.16,.28,3.16),0,True)
 for sign in [-1,1]:B.component('GlassDoor_Leaf' if s['style_family'] in ['marine','modern'] else 'PanelDoor_Leaf',f'GalleryDoorLeaf_F{level+1}_{sign}',frame,(sign*(door_w/2-.035),wall_y+door_w/4,z),(door_w/2-.065,.10,3.0),sign*math.pi/2,True)
 B.layout.append({'name':f'GalleryDoor_F{level+1}','kind':'internal_door','floor':level+1,'fixed_open_deg':90,'requested_opening_width_m':door_w,'center_xy_m':[0,wall_y],'direction_xy':[1,0],'body_path_across_wall_normal_xy':[0,1],'z_m':z})

def gallery(B,model,room):
 s=B.spec;family=s['style_family'];w,h=room['size'];floorh=s['floor_height_m'];modern=family=='modern' or s['building_id']=='M10';pool=sources(B);patterns=['chair','cabinet','console','vase','clock','sofa']
 candidates=[row for row in pool.values() if row.get('source')=='Poly Haven' and any(x in str(row['id']).lower() for x in patterns)];candidates.sort(key=lambda x:str(x['id']));art=None if modern else reserve_art(B,16*s['floors']);artindex=0;used=collections.Counter()
 if s['building_id']=='M18':candidates=[row for row in pool.values() if row.get('source')=='Poly Haven' and any(x in str(row['id']).lower() for x in ['statue','marble_bust']) or row.get('source')=='Smithsonian' and row.get('unit')=='SAAM']
 if s['building_id']=='M01':
  candidates=[row for row in pool.values() if row.get('source')=='Poly Haven' and row['id']=='marble_bust_01'];assert candidates,'VERIFIED_CLASSICAL_BUST_REQUIRED'
 for level in range(s['floors']):
  z=level*floorh
  # The rear gallery is reached through a real open double doorway on every
  # floor. Closing that opening disconnects more than5% of the sampled floor.
  internal_gallery_door(B,model,room,level)
  from gallery_layout import display_walls
  for wi,(x,y,length,angle) in enumerate(display_walls(B,model,level)):
    wall=B.box(f'DisplayWall_F{level+1}_{wi}',(-length/2,-.09,0),(length/2,.09,3.4),'Wall',True,'exhibition_wall');wall.location=(x,y,z);wall.rotation_euler.z=angle
    for side in [-1,1]:
     for off in [-length*.28,length*.28]:
      row=procedural_art(B,artindex) if modern else art[artindex];a,b=off,side*.125;anchor=(x+a*math.cos(angle)-b*math.sin(angle),y+a*math.sin(angle)+b*math.cos(angle),z+1.55);yaw=angle+(math.pi if side==1 else 0.)
      painting(B,row,f'Painting_F{level+1}_{artindex:03}',anchor,yaw,level+1);artindex+=1
  positions=[(-w*.37,-h*.18),(w*.37,-h*.18),(-w*.37,h*.18),(w*.37,h*.18)]
  if s['building_id']=='M10':positions=[]
  if s['building_id']=='M01':positions=[positions[[1,0,0][level%3]]]
  for i,(x,y) in enumerate(positions):
   footprint=rect(x-.65,y-.65,x+.65,y+.65);floor=wkt.loads(model['floors'][level]['polygon_wkt'])
   if not floor.contains(footprint):continue
   machine=s['building_id']=='M10' and i%2==0;base=.22 if machine else .88;name=f'Exhibit_F{level+1}_{i}';B.box(name+'_Plinth',(x-.55,y-.55,z),(x+.55,y+.55,z+base-.04),'Steel' if machine else 'Plinth',True,'plinth');B.box(name+'_PlinthCap',(x-.59,y-.59,z+base-.07),(x+.59,y+.59,z+base),'Steel' if machine else 'Plinth',True,'plinth')
   if machine:
    available=[q for q in pool.values() if used[q['source']+':'+str(q['id'])]<3];assert available;row=available[(level*2+i//2)%len(available)];used[row['source']+':'+str(row['id'])]+=1;place(B,row,name,x,y,z+base,(1.04,1.04,1.6),yaw=math.atan2(-y,-x));B.layout[-1].update({'floor':level+1,'theme_category':'industrial_object_low_steel_base'})
   elif modern:abstract_sculpture(B,name,x,y,z+.88,level*4+i);B.layout[-1]['floor']=level+1
   else:
    available=[row for row in candidates if used[row['source']+':'+str(row['id'])]<3];assert available,'INSUFFICIENT_THEME_MODELS';row=available[(level*4+i)%len(available)];key=row['source']+':'+str(row['id']);used[key]+=1;place(B,row,name,x,y,z+.88,(1.04,1.04,1.5),yaw=math.atan2(-y,-x));B.layout[-1]['floor']=level+1
 # Chandelier fixtures are real licensed models, never unsupported floating meshes.
 if family in ['neoclassical','art_deco','victorian_natural']:
  lights=[row for row in pool.values() if 'chandelier' in str(row['id']).lower()]
  for level in range(s['floors']):
   if not lights:break
   row=lights[level%len(lights)];key=row['source']+':'+str(row['id']);original=import_model(B,row);ob=original.copy();ob.data=original.data;B.scene.collection.objects.link(ob);ob.name=f'Chandelier_F{level+1}';ob.hide_render=False;ob.location=(0,0,0);lo,hi=B.bounds(ob);scale=min(np.array([1.9,1.9,1.0])/np.maximum(hi-lo,1e-6));ob.scale=(scale,scale,scale);lo,hi=B.bounds(ob);x,y=-w*.32,0;ceiling=(level+1)*floorh-.32;ob.location+=Vector((x-(lo[0]+hi[0])/2,y-(lo[1]+hi[1])/2,ceiling-float(hi[2])));ob['museum_collision']=True;ob['museum_category']='hanging_fixture';B.licenses[key]={k:row.get(k) for k in ['source','id','license','url']};B.area(f'ChandelierLight_F{level+1}',(x,y,ceiling-.85),(x,y,level*floorh),240,1.2,(1,.92,.80),s['lighting']['kelvin'])
 if s['building_id']=='M10':
  from museum_contemporary_displays import contemporary
  dense=contemporary(B,model,room)
  return {'paintings':artindex,'model_counts':dict(getattr(B,'model_counts',{})),'density11':dense}
 return {'paintings':artindex,'model_counts':dict(getattr(B,'model_counts',{}))}
