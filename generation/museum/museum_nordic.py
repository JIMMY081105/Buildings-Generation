"""ADJUST28 original room-like furniture groups, white plaster and design studies."""
import math,json,hashlib
import numpy as np,bpy
from mathutils import Matrix,Vector
from shapely import wkt
from shapely.geometry import box
from PIL import Image,ImageDraw,ImageFont

def actual_evaluated_bounds(objects):
 graph=bpy.context.evaluated_depsgraph_get();points=[]
 for ob in objects:
  evaluated=ob.evaluated_get(graph);mesh=evaluated.to_mesh();v=np.asarray([q.co[:] for q in mesh.vertices],dtype=float);matrix=np.asarray(evaluated.matrix_world);points.append(v@matrix[:3,:3].T+matrix[:3,3]);evaluated.to_mesh_clear()
 return np.min([q.min(0) for q in points],0),np.max([q.max(0) for q in points],0)

def poster(B,name,x,y,z,inward,index):
 name=name.replace('-','N')
 key=f'procedural:M20:design_poster28:{index}';mat='Nordic28Poster_'+str(index);B.material(mat,(.83,.80,.70),'stone',[1.6,2.0],.96);rec=B.material_records[mat];im=Image.new('RGB',(2048,2048),(234,229,211));draw=ImageDraw.Draw(im);ink=(53,61,58);accent=(151,105,54)
 fontfile='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
 try:font=ImageFont.truetype(fontfile,62);small=ImageFont.truetype(fontfile,32)
 except OSError:font=small=ImageFont.load_default()
 draw.text((160,130),'FORM / MATERIAL / CRAFT',font=font,fill=ink);draw.text((160,230),'ORIGINAL DESIGN STUDY  '+str(index+1).zfill(2),font=small,fill=accent)
 # Clearly original dimensioned elevations, never attributed to a designer.
 for shift,variant in [(0,index%3),(850,(index+1)%3)]:
  yy=550+shift;draw.line([(300,yy+350),(300,yy+50),(1020,yy+50),(1020,yy+350)],fill=ink,width=9);draw.line([(300,yy+240),(1020,yy+240)],fill=ink,width=12);draw.line([(390,yy+240),(350,yy+530)],fill=ink,width=10);draw.line([(920,yy+240),(960,yy+530)],fill=ink,width=10)
  if variant!=1:draw.line([(1020,yy+50),(1150,yy-60),(1250,yy-60),(1250,yy+250),(1020,yy+350)],fill=ink,width=10)
  for xx in [230,1330]:draw.line([(xx,yy-70),(xx,yy+540)],fill=accent,width=3)
  draw.line([(230,yy+570),(1330,yy+570)],fill=accent,width=3);draw.text((1400,yy+180),'ASH\nLINEN\nJOINERY',font=small,fill=ink)
 im.save(rec['texture']);rec.update(texture_sha256=hashlib.sha256(open(rec['texture'],'rb').read()).hexdigest(),kind='original_design_poster28',source_asset=key,source_license='procedural original',method='Original metric furniture elevation study drawn here; no historical designer or collected-object attribution.')
 for node in B.materials[mat].node_tree.nodes:
  if node.type=='TEX_IMAGE' and node.image:node.image.reload()
 verts=[(x-.8,y,z+.95),(x+.8,y,z+.95),(x+.8,y,z+2.95),(x-.8,y,z+2.95)];faces=[(0,1,2,3)] if inward<0 else [(3,2,1,0)];ob=B.mesh(name,verts,faces,mat,False,'wall_art');uv=ob.data.uv_layers.active
 if uv:
  for poly in ob.data.polygons:
   for loop in poly.loop_indices:
    v=ob.data.vertices[ob.data.loops[loop].vertex_index].co;u=(v.x-(x-.8))/1.6;uv.data[loop].uv=(1-u if inward>0 else u,(v.z-(z+.95))/2.)
 for side in [-1,1]:B.box(name+f'_Frame{side}',(x+side*.82-.025,y-.035,z+.91),(x+side*.82+.025,y+.035,z+2.99),'Wood',False,'wall_art_frame')
 for j,zz in enumerate([z+.93,z+2.97]):B.box(name+f'_FrameHorizontal{j}',(x-.84,y-.035,zz-.025),(x+.84,y+.035,zz+.025),'Wood',False,'wall_art_frame')
 floor=round(z/B.spec['floor_height_m'])+1;B.layout.append({'name':name,'kind':'painting','floor':floor,'source_key':key,'source_asset_title':'Original Nordic furniture elevation study '+str(index+1),'anchor_world_m':[x,y,z+1.95],'canvas_dimensions_m':[1.6,2.0],'facing_normal_world':[0,inward,0],'source_image_sha256':rec['texture_sha256']});B.licenses[key]={'source':'procedural','id':key,'license':'procedural original','source_sha256':rec['texture_sha256'],'method':rec['method']}

def exhibition(B,model,room):
 from museum_themes import objects
 result=objects(B,model,room);H=B.spec['floor_height_m'];w,h=model['building_xy_m'];opening=wkt.loads(model['opening_wkt']).buffer(1.2);groups=[]
 for level in range(B.spec['floors']):
  z=level*H;floor=wkt.loads(model['floors'][level]['polygon_wkt']);structural=[];bpy.context.view_layer.update()
  for ob in B.scene.objects:
   if ob.type!='MESH' or not ob.get('museum_collision') or not (ob.get('museum_category') in ['wall','stair_wall','gallery_wall','exhibition_wall'] or any(k in ob.name for k in ['Pillar_','_Column'])):continue
   lo,hi=B.bounds(ob)
   if hi[2]>=z and lo[2]<=z+2.5:structural.append(box(lo[0]-.03,lo[1]-.03,hi[0]+.03,hi[1]+.03))
  preferred=[(-w*.29,-h*.25),(w*.29,-h*.25),(w*.29,h*.20),(-w*.29,h*.20)];preferred=preferred[level:]+preferred[:level];grid=[(float(x),float(y)) for x in np.arange(-w/2+3.4,w/2-3.3,2.) for y in np.arange(-h/2+2.6,h/2-2.5,2.)];anchors=[];footprints=[]
  for x,y in preferred+grid:
   p=box(x-3.1,y-2.3,x+3.1,y+2.3)
   if not floor.contains(p) or p.intersects(opening) or any(p.intersects(q) for q in structural) or any(p.distance(q)<1.2-1e-6 for q in footprints):continue
   anchors.append((x,y));footprints.append(p)
   if len(anchors)==3:break
  assert len(anchors)==3,('NORDIC28_THREE_SAFE_SCENE_GROUPS_NOT_FIT',level,anchors)
  for group,(cx,cy) in enumerate(anchors):
   pname=f'Nordic28_Group_F{level+1}_{group}';B.box(pname+'_Platform',(cx-3.1,cy-2.3,z),(cx+3.1,cy+2.3,z+.18),'Nordic28Platform',True,'plinth');members=[]
   for slot,(rx,ry) in enumerate([(-1.6,-1.),(0,0),(-1.6,1.),(1.6,-1.),(1.6,1.)]):
    i=group*5+slot;name=f'Theme_F{level+1}_{i:02}';row=next(q for q in B.layout if q.get('kind')=='exhibit' and q['name']==name);obs=[ob for ob in B.scene.objects if ob.type=='MESH' and not ob.hide_render and (ob.name==name or ob.name.startswith(name+'_'))]
    for ob in list(obs):
     if ob.name in [name+'_Plinth',name+'_Cap']:bpy.data.objects.remove(ob,do_unlink=True);obs.remove(ob)
    assert obs;bounds=[B.bounds(ob) for ob in obs];lo=np.min([q[0] for q in bounds],0);hi=np.max([q[1] for q in bounds],0);oldcenter=(lo+hi)/2;oldcenter[2]=lo[2];oldyaw=math.atan2(obs[0].matrix_world[1][0],obs[0].matrix_world[0][0]);yaw=math.atan2(-ry,-rx) if slot!=1 else group*math.pi/2;target=np.array([cx+rx,cy+ry,z+.178]);matrix=Matrix.Translation(Vector(target))@Matrix.Rotation(yaw-oldyaw,4,'Z')@Matrix.Translation(Vector(-oldcenter))
    for ob in obs:ob.matrix_world=matrix@ob.matrix_world
    bpy.context.view_layer.update();lo,hi=actual_evaluated_bounds(obs);assert footprints[group].covers(box(lo[0],lo[1],hi[0],hi[1])),('FURNITURE_OUTSIDE_SCENE_PLATFORM',name,lo,hi);row.update(actual_min_m=lo.tolist(),actual_max_m=hi.tolist(),support_z_m=z+.18,support_embed_m=.002,yaw_rad=yaw,nordic28_group=pname,display_support_prefix=pname+'_Platform',actual_bounds_method='All actual evaluated mesh vertices transformed to world; never rotated local bounding-box corners.');members.append(name)
   groups.append({'name':pname,'kind':'furniture_scene_group','floor':level+1,'members':members,'platform_name':pname+'_Platform','footprint_wkt':footprints[group].wkt,'platform_height_m':.18,'group_theme':['conversation and low tables','reading and cabinet study','lounge furniture series'][group]})
  for side in [-1,1]:
   intervals=[]
   for ob in B.scene.objects:
    if ob.type!='MESH' or ob.get('museum_category')!='wall':continue
    lo,hi=B.bounds(ob)
    if abs((lo[1]+hi[1])/2-side*h/2)<.1 and hi[1]-lo[1]<.6 and lo[2]<=z+.90 and hi[2]>=z+3.00:intervals.append((float(lo[0]),float(hi[0])))
   merged=[]
   for a,b in sorted(intervals):
    if merged and a<=merged[-1][1]+1e-5:merged[-1]=(merged[-1][0],max(merged[-1][1],b))
    else:merged.append((a,b))
   choices=[((a+b)/2,a,b) for a,b in merged if b-a>=1.72 and abs((a+b)/2)<w/2-2.];left=sorted([q for q in choices if q[0]<-2],key=lambda q:abs(q[0]));right=sorted([q for q in choices if q[0]>2],key=lambda q:abs(q[0]));assert left and right,('NORDIC28_POSTER_NEEDS_ACTUAL_SOLID_WALL_PIER',level,side,merged)
   for col,(xx,a,b) in enumerate([left[0],right[0]]):
    poster(B,f'Nordic28_DesignPoster_F{level+1}_{side}_{col}',xx,side*(h/2-.255),z,-side,level*4+(side+1)+col);B.layout[-1]['actual_solid_wall_support_interval_x_m']=[a,b];half=min(.83,(b-a)/2-.03);center_y=side*(h/2-.225)-side*.006;panel=B.box(f'Nordic28_WallPanel_F{level+1}_{side}_{col}',(xx-half,center_y-.01,z+.04),(xx+half,center_y+.01,z+.86),'Wood',True,'wall_panel');B.layout.append({'name':panel.name,'kind':'architecture','floor':level+1,'material':'Wood','purpose':'Local pale timber wall veneer below original design poster, embedded4mm into the verified actual full-height solid wall pier; exact same20mm panel collider exported.','actual_wall_support_interval_x_m':[a,b]})
 B.layout.extend(groups);B.layout.append({'name':'Nordic28_scene_group_summary','kind':'architecture','actual_group_count':len(groups),'per_floor_groups':3,'method':'Original licensed furniture selection and source caps preserved; relocate actual meshes onto three shared18cm pale timber platforms/floor with>=1.2m aisles and original design posters. No remaining individual pedestals.'});return {**result,'nordic28_scene_groups':groups,'original_design_posters':4*B.spec['floors']}
