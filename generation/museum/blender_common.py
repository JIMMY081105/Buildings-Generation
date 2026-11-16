"""Portable textured materials and exact, shared Blender mesh authoring."""
from pathlib import Path
import hashlib,json,math,os,random
import bpy,bmesh,numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter
from mathutils import Vector,Matrix

def linear(x):return np.where(x<=.04045,x/12.92,((x+.055)/1.055)**2.4)
def srgb(x):return np.where(x<=.0031308,12.92*x,1.055*np.power(np.maximum(x,0),1/2.4)-.055)

class Builder:
 def __init__(self,spec,out):
  self.spec=spec;self.out=Path(out);self.root=Path('/cephfs/shared/yixing/outputs/museum29_20261107');self.tex=self.out/'textures';self.tex.mkdir(exist_ok=True,parents=True);self.materials={};self.material_records={};self.licenses={};self.layout=[];self.templates={}
  bpy.ops.wm.read_factory_settings(use_empty=True);self.scene=bpy.context.scene;self.scene.unit_settings.system='METRIC';self.scene.unit_settings.scale_length=1.;self.scene.render.threads_mode='FIXED';self.scene.render.threads=8
  self.scene.use_nodes=False;self.scene.render.use_compositing=False;self.scene.render.use_sequencer=False
  self.library=json.loads((self.root/'LIBRARY_MANIFEST.json').read_text());self.asset_rows=list(self.library['assets'].values())
  self.scene.world=bpy.data.worlds.new('MuseumSky');self.scene.world.use_nodes=True;self.scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=(.68,.78,1,1);self.scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.3
 def texture_asset(self,hint):
  hints=hint.lower().split('|');rows=[r for r in self.asset_rows if r.get('source') in ['ambientCG','Poly Haven'] and r.get('paths') and any(h in r.get('id','').lower() for h in hints)]
  for row in sorted(rows,key=lambda r:r['id']):
   paths=[Path(p) for p in row['paths'] if Path(p).suffix.lower() in ['.jpg','.png']];colors=[p for p in paths if any(k in p.name.lower() for k in ['_color','_diff_','basecolor','albedo'])]
   if colors:return row,colors[0]
  return None,None
 def material(self,name,color,kind='stone',scale=2.4,rough=.6,metal=0,source_hint='',glass=False):
  if name in self.materials:return self.materials[name]
  m=bpy.data.materials.new(name);m.use_nodes=True;bs=m.node_tree.nodes.get('Principled BSDF');bs.inputs['Base Color'].default_value=(*color,1);bs.inputs['Roughness'].default_value=rough;bs.inputs['Metallic'].default_value=metal;m.diffuse_color=(*color,1)
  if glass:
   bs.inputs['Transmission Weight'].default_value=1;bs.inputs['IOR'].default_value=1.5;bs.inputs['Roughness'].default_value=.005;m['museum_preview_opacity']=.12 if name=='WindowGlass' else .025
  n=2048;rng=np.random.default_rng(self.spec['seed']+sum(map(ord,name)));yy,xx=np.mgrid[:n,:n];noise=gaussian_filter(rng.normal(size=(n,n)),3);noise/=max(noise.std(),1e-9);col=np.asarray(color)[None,None,:]*(1+.02*noise[...,None]);row,source=self.texture_asset(source_hint) if source_hint else (None,None)
  if source:
   im=Image.open(source).convert('RGB').resize((n,n),Image.Resampling.LANCZOS);a=linear(np.asarray(im,dtype=float)/255);mean=np.median(a.reshape(-1,3),axis=0);col=a/np.maximum(mean,1e-3)*np.asarray(color)[None,None,:]
   key=row['source']+':'+row['id'];entry=self.licenses.setdefault(key,{k:row.get(k) for k in ['source','id','license','url']});entry.setdefault('used_for_materials',[]).append(name)
  uv=[scale,scale] if isinstance(scale,(float,int)) else list(scale)
  alpha=None
  if kind in ['steel_grating','cast_iron_grating']:
   uv=[.96,.96];cells=16 if kind=='steel_grating' else 8;u=xx/n*cells;v=yy/n*cells;a=u%1-.5;b=v%1-.5
   bars=((u%1)<.20)|((v%1)<.13)
   if kind=='cast_iron_grating':bars|=(abs(abs(a)-abs(b))<.055)|(abs(np.hypot(a,b)-.29)<.05)
   alpha=bars.astype(np.uint8)*255
  elif kind=='vertical_larch28':
   uv=[2.4,2.4];u=xx/n*2.4;v=yy/n*2.4;board=np.floor(u/.12);grain=gaussian_filter(rng.normal(size=(n,n)),[60,.9]);grain/=max(grain.std(),1e-6);shade=.88+.20*(np.sin(board*5.17)+1)/2;col=np.asarray(color)[None,None,:]*shade[...,None]*(1+.045*grain[...,None]+.008*noise[...,None]);col[(u%.12)<.004]*=.35
  elif kind=='board_concrete26':
   uv=[4.8,4.8];u=xx/n*4.8;v=yy/n*4.8;board=np.floor(v/.20);grain=gaussian_filter(rng.normal(size=(n,n)),[.8,55]);grain/=max(grain.std(),1e-6);shade=.98+.035*np.sin(board*4.17);col=np.asarray(color)[None,None,:]*shade[...,None]*(1+.018*grain[...,None]+.012*noise[...,None]);joint=(v%.20<.006)|(u%1.2<.006)|(v%.6<.008);col[joint]*=.64;radius=np.hypot((u%1.2)-.6,(v%.6)-.3);col[radius<.023]*=.36;col[(radius>=.023)&(radius<.031)]*=1.09
  elif kind=='hazard_stripe':
   uv=[1.2,.30];stripe=(((xx+yy)/n*6)%1)<.5;col=np.where(stripe[...,None],np.asarray(color)[None,None,:],np.array([.012,.014,.012])[None,None,:])
  elif kind=='concentric_marble':
   uv=[48.,48.];radius=np.hypot(xx/n-.5,yy/n-.5)*48.;ring=np.floor(radius/.90).astype(int);palette=np.array([[.68,.61,.48],[.22,.34,.28],[.45,.20,.13],[.76,.72,.59],[.075,.09,.085]]);col=palette[ring%len(palette)]*(1+.035*noise[...,None]);col[(radius%.90)<.025]*=.58;m['uv_offset_xy']=[.5,.5]
  elif kind=='herringbone':
   # Exact 0.08 x 0.48 m boards; 36 diagonal cells make this texture seamless.
   uv=[36*.08*math.sqrt(2)]*2;u=(xx+yy)/n*36;v=(yy-xx)/n*36;ix=np.floor(u).astype(int);iy=np.floor(v).astype(int);phase=(ix-iy)%12;horizontal=phase<6
   board=np.where(horizontal,np.floor((ix-iy)/12)*73+iy*31,np.floor((ix-iy)/12)*97+ix*47);shade=.82+.30*(np.sin(board*73.33)+1)/2
   along_h=(u-iy)%12;along_v=(v-ix-1)%12;edge=np.where(horizontal,(v%1<.018)|(along_h<.018)|(along_h>5.982),(u%1<.018)|(along_v<.018)|(along_v>5.982))
   grain_h=gaussian_filter(rng.normal(size=(n,n)),[.7,5]);grain_v=gaussian_filter(rng.normal(size=(n,n)),[5,.7]);grain=np.where(horizontal,grain_h,grain_v);grain/=max(grain.std(),1e-9);col=np.asarray(color)[None,None,:]*shade[...,None]*(1+.025*grain[...,None]);col[edge]*=.55
  elif kind=='long_board':
   # Three continuous lengthwise boards; no cross joints or parquet blocks.
   uv=[1.,1.];grain=gaussian_filter(rng.normal(size=(n,n)),[1.2,65]);grain/=max(grain.std(),1e-6);wave=np.sin(yy/n*160+1.2*np.sin(xx/n*math.tau)+grain*.45);board=np.floor(yy/n*3);col=np.asarray(color)[None,None,:]*(.90+.08*board[...,None])*(1+.045*grain[...,None]+.025*wave[...,None]);col[((yy/n*3)%1)<.008]*=.65
  elif kind=='hinoki_plank':
   uv=[3.6,4.8];u=xx/n*30;v=yy/n*2;ix=np.floor(u);phase=(ix%4)/4;board=ix*37+np.floor(v+phase)*71;shade=.91+.14*(np.sin(board*73.33)+1)/2;grain=gaussian_filter(rng.normal(size=(n,n)),[70,1.1]);grain/=max(grain.std(),1e-6);col=np.asarray(color)[None,None,:]*shade[...,None]*(1+.025*grain[...,None]);col[(u%1<.014)|((v+phase)%1<.003)]*=.55
  elif kind=='ash_plank':
   # Distinct pale straight-grain ash study:80mm boards,1.6m staggered lengths.
   uv=[3.2,3.2];u=xx/n*40;v=yy/n*2;ix=np.floor(u);phase=(ix%3)/3;board=ix*31+np.floor(v+phase)*67;shade=.88+.20*(np.sin(board*73.33)+1)/2;grain=gaussian_filter(rng.normal(size=(n,n)),[65,1.1]);grain/=max(grain.std(),1e-6);col=np.asarray(color)[None,None,:]*shade[...,None]*(1+.025*grain[...,None]);col[(u%1<.015)|((v+phase)%1<.002)]*=.55
  elif kind=='walnut_veneer':
   uv=[.9,1.2];grain=gaussian_filter(rng.normal(size=(n,n)),[65,1.4]);grain/=max(grain.std(),1e-6);ribbon=np.sin(xx/n*130+grain*.8+2*np.sin(yy/n*math.tau));col=np.asarray(color)[None,None,:]*(1+.12*grain[...,None]+.055*ribbon[...,None]);col[((xx/n*3)%1)<.012]*=.45
  elif kind in ['wood','plank']:
   uv=[3.2,3.2];u=(xx+yy)/math.sqrt(2)/(n/32) if kind=='herringbone' else xx/(n/32);v=(yy-xx)/math.sqrt(2)/(n/32) if kind=='herringbone' else yy/(n/32)
   ix=np.floor(u).astype(int);iy=np.floor(v).astype(int);horizontal=((ix-iy)%8)<4;plank=np.where(horizontal,np.floor((ix-iy)/8)*17+iy*11,np.floor((ix-iy-4)/8)*13+ix*19)
   if kind!='herringbone':plank=ix*31+np.floor((v+(ix%2)*3)/6)*67
   shade=.84+.28*(np.sin(plank*73.33)+1)/2;grain=gaussian_filter(rng.normal(size=(n,n)),[1,13]);grain/=max(grain.std(),1e-6)
   edge=np.where(horizontal,((v%1)<.028)|((u-iy)%8<.028),((u%1)<.028)|((ix-v-4)%8<.028)) if kind=='herringbone' else ((u%1)<.025)|(((v+(ix%2)*3)%6)<.03)
   col=np.asarray(color)[None,None,:]*shade[...,None]*(1+.045*grain[...,None]);col[edge]*=.55
  elif kind=='cotto32':
   uv=[2.4,2.4];u=xx/n*8;v=yy/n*8;ix=np.floor(u);iy=np.floor(v);shade=.88+.24*(np.sin(ix*73.31+iy*29.17)+1)/2;soft=gaussian_filter(rng.normal(size=(n,n)),55);soft/=max(soft.std(),1e-9);col=np.asarray(color)[None,None,:]*shade[...,None]*(1+.012*noise[...,None]+.028*soft[...,None]);joint=(np.minimum(u%1,1-u%1)<.002/.30)|(np.minimum(v%1,1-v%1)<.002/.30);col[joint]=[.17,.135,.10]
  elif kind=='cotto_tile41':
   uv=[1.2,1.2];tile=512
   for rr in range(4):
    for cc in range(4):
     shade=float(rng.uniform(.88,1.12));patch=rng.normal(size=(tile,tile));patch=gaussian_filter(patch,.7);patch/=max(patch.std(),1e-6);col[rr*tile:(rr+1)*tile,cc*tile:(cc+1)*tile]=np.asarray(color)[None,None,:]*shade*(1+.022*patch[...,None])
   col[(xx%tile<2)|(xx%tile>=510)|(yy%tile<2)|(yy%tile>=510)]=[.055,.025,.012]
  elif kind=='brick':
   uv=[2.3,2.4];u=xx/n*10;v=yy/n*32;rowidx=np.floor(v).astype(int);u+=(rowidx%2)*.5;brick=np.floor(u)*13+rowidx*27;shade=.8+.3*(np.sin(brick*81.37)+1)/2
   col=np.asarray(color)[None,None,:]*shade[...,None]*(1+.045*noise[...,None]);joint=(u%1<.01/.23)|(v%1<.01/.075);col[joint]=np.array([.30,.27,.22])
  elif kind=='ashlar':
   uv=[2.4,1.2];u=xx/n*2;v=yy/n*3;u+=(np.floor(v).astype(int)%2)*.5;col[(u%1<.006)|(v%1<.012)]*=.48
  elif kind=='white_marble_inlay':
   uv=[6.4,6.4];tile=n//8;stone=col.copy();result=np.zeros_like(col)
   for i in range(8):
    for j in range(8):
     patch=np.roll(np.rot90(stone,int(rng.integers(4))),rng.integers(n),axis=0);patch=Image.fromarray((np.clip(srgb(patch),0,1)*255).astype('uint8')).resize((tile,tile),Image.Resampling.LANCZOS);result[i*tile:(i+1)*tile,j*tile:(j+1)*tile]=linear(np.asarray(patch)/255)
   col=result;seam=((xx%tile)<1)|((yy%tile)<1);col[seam]*=.85
   # A metric 6.4 m repeating dark perimeter and compass diamond mosaic.
   u=xx/n;v=yy/n;r=np.sqrt((u-.5)**2+(v-.5)**2);diamond=abs(u-.5)+abs(v-.5)
   inlay=(u<.025)|(v<.025)|(u>.975)|(v>.975)|((diamond>.31)&(diamond<.335))|((r>.235)&(r<.247));col[inlay]=[.018,.026,.025]
  elif kind in ['star_tile','mosaic','radial_deco']:
   uv=[3.2,3.2];tiles=16 if kind!='mosaic' else 64;u=xx/n*tiles;v=yy/n*tiles;ix=np.floor(u).astype(int);iy=np.floor(v).astype(int);a=u%1-.5;b=v%1-.5
   if kind=='star_tile':motif=(abs(a)+abs(b)<.45)|((np.minimum(abs(a),abs(b))<.12)&(np.maximum(abs(a),abs(b))<.43));other=np.array([.025,.12,.15])
   elif kind=='radial_deco':motif=(np.floor(np.arctan2(yy-n/2,xx-n/2)/math.pi*16).astype(int)%2)==0;other=np.array([.012,.015,.018])
   else:motif=((ix+iy)%3)==0;other=np.asarray(color)*.65
   col=np.where(motif[...,None],col,other[None,None,:]);col[((u%1)<.022)|((v%1)<.022)]*=.45
  pixels=(np.clip(srgb(np.clip(col,0,1)),0,1)*255).astype('uint8')
  if alpha is not None:pixels=np.dstack([pixels,alpha])
  p=self.tex/(name+'.png');Image.fromarray(pixels).save(p)
  tx=m.node_tree.nodes.new('ShaderNodeTexImage');tx.image=bpy.data.images.load(str(p),check_existing=True);tx.extension='REPEAT';m.node_tree.links.new(tx.outputs['Color'],bs.inputs['Base Color']);m['uv_repeat_xy_m']=uv;m['uv_repeat_m']=uv[0]
  if alpha is not None:m.node_tree.links.new(tx.outputs['Alpha'],bs.inputs['Alpha'])
  # Bake the chosen roughness median; a raw source map would override the
  # declared polish/epoxy/concrete finish when linked directly to the shader.
  roughness_record=None
  if source and not glass and kind not in ['wood','plank','herringbone','brick','white_marble_inlay','star_tile','mosaic','radial_deco']:
   paths=[Path(q) for q in row['paths']]
   for label,words in [('Roughness',['rough']),('Normal',['normalgl','nor_gl'])]:
    q=next((q for q in paths if q.suffix.lower() in ['.jpg','.png'] and any(w in q.name.lower() for w in words)),None)
    if q:
     if label=='Roughness':
      original=q;values=np.asarray(Image.open(q).convert('L').resize((n,n),Image.Resampling.LANCZOS),dtype=np.float32)/255.;values=np.clip(values*(rough/max(float(np.median(values)),1e-4)),0,1);quantized=np.round(values*255).astype(np.uint8);q=self.tex/(name+'_Roughness.png');Image.fromarray(quantized).save(q);roughness_record={'roughness_texture':str(q),'roughness_texture_sha256':hashlib.sha256(q.read_bytes()).hexdigest(),'roughness_source':str(original),'roughness_source_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'roughness_target_median':rough,'roughness_actual_median':float(np.median(quantized)/255.)}
     im=bpy.data.images.load(str(q),check_existing=True);im.colorspace_settings.name='Non-Color';node=m.node_tree.nodes.new('ShaderNodeTexImage');node.image=im
     if label=='Normal':normal=m.node_tree.nodes.new('ShaderNodeNormalMap');normal.inputs['Strength'].default_value=.35;m.node_tree.links.new(node.outputs['Color'],normal.inputs['Color']);m.node_tree.links.new(normal.outputs['Normal'],bs.inputs['Normal'])
     else:m.node_tree.links.new(node.outputs['Color'],bs.inputs[label])
  self.materials[name]=m;self.material_records[name]={'part':name,'base_color_linear':color,'roughness':rough,'metallic':metal,'texture':str(p),'texture_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'resolution':[n,n],'uv_repeat_xy_m':uv,'kind':kind,'source_asset':row.get('id') if row else 'procedural','source_license':row.get('license') if row else 'procedural','source_url':row.get('url') if row else None,'shader':'UsdPreviewSurface via image textures'}
  if roughness_record:self.material_records[name].update(roughness_record)
  if alpha is not None:self.material_records[name]['visual_collision_exception']='Procedural opacity grating appearance; original exact solid tread/platform collider retained for safe foot support, as authorized by GOAL3.3.'
  if kind=='concentric_marble':self.material_records[name]['uv_offset_xy']=[.5,.5]
  if kind=='long_board':self.material_records[name].update({'grain_direction':'one continuous lengthwise U axis, each tread/landing uses principal horizontal mesh axis','lengthwise_boards':3,'cross_joints':False})
  return m
 def mesh(self,name,v,f,material,collision=True,role='architecture'):
  name=name.replace('-','N')
  me=bpy.data.meshes.new(name);me.from_pydata(v,[],f);me.update();bm=bmesh.new();bm.from_mesh(me);bmesh.ops.recalc_face_normals(bm,faces=bm.faces);bm.to_mesh(me);bm.free();ob=bpy.data.objects.new(name,me);self.scene.collection.objects.link(ob);me.materials.append(self.materials[material] if isinstance(material,str) else material);ob['museum_collision']=collision;ob['museum_category']=role
  uv=me.uv_layers.new(name='st');scale=me.materials[0].get('uv_repeat_xy_m',[2.4,2.4]);offset=me.materials[0].get('uv_offset_xy',[0.,0.])
  for p in me.polygons:
   axis=max(range(3),key=lambda i:abs(p.normal[i]));axes=((1,2),(0,2),(0,1))[axis]
   for li in p.loop_indices:
    co=me.vertices[me.loops[li].vertex_index].co;uv.data[li].uv=(co[axes[0]]/scale[0]+offset[0],co[axes[1]]/scale[1]+offset[1])
  matname=me.materials[0].name
  if self.material_records.get(matname,{}).get('kind')=='long_board':
   points=np.asarray([v.co[:] for v in me.vertices]);xy=points[:,:2];_,axes=np.linalg.eigh(np.cov(xy.T));along=axes[:,-1];across=np.array([-along[1],along[0]]);u=xy@along;v=xy@across;uz=np.maximum(np.ptp(u),1e-6);vz=np.maximum(np.ptp(v),1e-6)
   for face in me.polygons:
    for li in face.loop_indices:
     idx=me.loops[li].vertex_index;uv.data[li].uv=((u[idx]-u.min())/uz,(v[idx]-v.min())/vz if abs(face.normal.z)>.5 else (points[idx,2]-points[:,2].min())/max(np.ptp(points[:,2]),1e-6))
  return ob
 def box(self,name,lo,hi,material,collision=True,role='architecture'):
  x,y,z=lo;X,Y,Z=hi;v=[(x,y,z),(X,y,z),(X,Y,z),(x,Y,z),(x,y,Z),(X,y,Z),(X,Y,Z),(x,Y,Z)];f=[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)];return self.mesh(name,v,f,material,collision,role)
 def tube(self,name,points,radius,material,collision=True,role='architecture'):
  from staircase_core import Geometry
  g=Geometry();g.tube(name,points,radius);p=g.parts[0];return self.mesh(name,p['vertices'],p['faces'],material,collision,role)
 def cylinder(self,name,center,radius,height,material,collision=True,rtop=None,n=32):
  x,y,z=center;rtop=radius if rtop is None else rtop;v=[(x+r*math.cos(a),y+r*math.sin(a),zz) for r,zz in [(radius,z),(rtop,z+height)] for a in np.linspace(0,math.tau,n,endpoint=False)];f=[tuple(reversed(range(n))),tuple(range(n,2*n))]+[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)];ob=self.mesh(name,v,f,material,collision)
  for face in ob.data.polygons:
   if abs(face.normal.z)<.5:face.use_smooth=True
  return ob
 def area(self,name,loc,target,power,size=3,color=(1,1,1),kelvin=None):
  d=bpy.data.lights.new(name,'AREA');d.energy=power;d.shape='DISK';d.size=size;d.color=color;ob=bpy.data.objects.new(name,d);self.scene.collection.objects.link(ob);ob.location=loc;ob.rotation_euler=(Vector(target)-ob.location).to_track_quat('-Z','Y').to_euler()
  # ADJUST10: light cards illuminate, while actual fixture meshes carry appearance.
  ob.visible_camera=False;ob.visible_glossy=False;ob.visible_transmission=False;d['museum_ghost_factor']=1.
  if kelvin:d['museum_source_kelvin']=kelvin
  return ob
 def camera(self,name,eye,target,lens=24):
  d=bpy.data.cameras.new(name);d.lens=lens;d.clip_end=500;d.dof.use_dof=False;ob=bpy.data.objects.new(name,d);self.scene.collection.objects.link(ob);ob.location=eye;ob.rotation_euler=(Vector(target)-ob.location).to_track_quat('-Z','Y').to_euler();return ob
 def bounds(self,ob):
  bpy.context.view_layer.update();v=np.array([ob.matrix_world@Vector(p) for p in ob.bound_box]);return v.min(0),v.max(0)
 def component(self,key,name,material,center,target_size,yaw=0,collision=True):
  cachekey=(key,material)
  if cachekey not in self.templates:
   path=self.root/('library/procedural_components/pillar40_closed/components.blend' if key=='Pillar' else 'library/procedural_components/attempt003/components.blend')
   if key=='Pillar':
    proof=json.loads(path.with_name('PILLAR40_CLOSED.json').read_text());assert proof['status']=='PASS' and proof['closed_boundary_edges']==proof['nonmanifold_edges']==0 and proof['signed_volume_m3']>0 and hashlib.sha256(path.read_bytes()).hexdigest()==proof['components_sha256'],'ADJUST40_VERIFIED_CLOSED_PILLAR_SOURCE_REQUIRED'
   with bpy.data.libraries.load(str(path),link=False) as (src,dst):
    assert 'Component_'+key in src.objects,key;dst.objects=['Component_'+key]
   ob=dst.objects[0];self.scene.collection.objects.link(ob);ob.name='SOURCE_'+key
   # Freeze any remaining evaluated modifier. Sources are shared within a building.
   if key=='Pillar' and len(ob.data.polygons)>8000 and not ob.get('museum_pillar40_bounded_closed'):
    bpy.ops.object.select_all(action='DESELECT');ob.select_set(True);bpy.context.view_layer.objects.active=ob;mod=ob.modifiers.new('BoundedVisualAndExactCollision','DECIMATE');mod.ratio=8000/len(ob.data.polygons);bpy.ops.object.modifier_apply(modifier=mod.name)
   xf=ob.matrix_world.copy()
   for point in ob.data.vertices:point.co=xf@point.co
   ob.matrix_world=Matrix.Identity(4);v=np.array([p.co[:] for p in ob.data.vertices]);lo=v.min(0);hi=v.max(0);pivot=(lo+hi)/2;pivot[2]=lo[2]
   for point in ob.data.vertices:point.co-=Vector(pivot)
   oldmats=list(ob.data.materials);glassslots=set()
   for i,oldmat in enumerate(oldmats):
    if not oldmat:continue
    isglass='glass' in oldmat.name.lower()
    if oldmat.use_nodes:
     for node in oldmat.node_tree.nodes:
      if node.type=='BSDF_GLASS':isglass=True
      if node.type=='BSDF_PRINCIPLED':
       socket=node.inputs.get('Transmission Weight') or node.inputs.get('Transmission')
       if socket and float(socket.default_value)>.5:isglass=True
    if isglass:glassslots.add(i)
   faceglass=[f.material_index in glassslots for f in ob.data.polygons];ob.data['normalized_component']=True;ob.data.materials.clear();ob.data.materials.append(self.materials[material]);ob.data.materials.append(self.materials['Glass'])
   for f,isglass in zip(ob.data.polygons,faceglass):f.material_index=1 if isglass else 0
   ob.hide_render=True;ob.hide_viewport=True;ob.location=(0,0,-100)
   # Factory procedural shaders are replaced with image-based materials and a
   # real metric UV map; source meshes are shared by all matching instances.
   if not ob.data.uv_layers:ob.data.uv_layers.new(name='st')
   uv=ob.data.uv_layers.active;scale=self.materials[material].get('uv_repeat_xy_m',[2.4,2.4])
   for face in ob.data.polygons:
    axis=max(range(3),key=lambda i:abs(face.normal[i]));axes=((1,2),(0,2),(0,1))[axis]
    for li in face.loop_indices:
     point=ob.data.vertices[ob.data.loops[li].vertex_index].co;uv.data[li].uv=(point[axes[0]]/scale[0],point[axes[1]]/scale[1])
   self.templates[cachekey]=ob
  original=self.templates[cachekey];ob=original.copy();ob.data=original.data;self.scene.collection.objects.link(ob);ob.name=name;ob.hide_render=False;ob.hide_viewport=False;ob.parent=None;ob.location=(0,0,0);ob.rotation_euler=(0,0,0);ob.scale=(1,1,1)
  # Normalize from actual frozen vertices before setting physical dimensions.
  if not ob.data.get('normalized_component'):
   v=np.array([p.co[:] for p in ob.data.vertices]);lo=v.min(0);hi=v.max(0);pivot=(lo+hi)/2;pivot[2]=lo[2]
   for p in ob.data.vertices:p.co-=Vector(pivot)
   ob.data['normalized_component']=True
  v=np.array([p.co[:] for p in ob.data.vertices]);dim=v.max(0)-v.min(0);ob.scale=tuple(np.asarray(target_size)/np.maximum(dim,1e-6));ob.rotation_euler.z=yaw;ob.location=center;ob['museum_collision']=collision;ob['museum_category']='infinigen_architecture';ob['source_factory_component']=key
  return ob
 def save_metadata(self):
  if hasattr(self,'model_import_records'):(self.out/'SOURCE_MODEL_IMPORTS.json').write_text(json.dumps(self.model_import_records,ensure_ascii=False,indent=2))
  bpy.context.view_layer.update();occluders=[]
  for ob in self.scene.objects:
   if ob.type!='MESH' or ob.hide_render or not ob.get('museum_collision') or not (ob.get('museum_category') in ['wall','stair_wall','gallery_wall','exhibition_wall'] or any(k in ob.name for k in ['Pillar_','_Column','TowerWall'])):continue
   points=np.array([ob.matrix_world@Vector(p) for p in ob.bound_box]);occluders.append({'name':ob.name,'bounds':[points.min(0).tolist(),points.max(0).tolist()]})
  (self.out/'GALLERY_OCCLUDERS.json').write_text(json.dumps({'scope':'Actual opaque wall/column world AABBs for conservative gallery camera planning; actual image review still required','objects':occluders},indent=2))
  # Bind real display bounds for gallery planning, including multipart originals.
  for row in self.layout:
   if row.get('kind') not in ['exhibit','painting'] or 'actual_min_m' in row:continue
   pieces=[ob for ob in self.scene.objects if ob.type=='MESH' and not ob.hide_render and (ob.name==row['name'] or ob.name.startswith(row['name']+'_')) and ob.get('museum_category') not in ['plinth','display_case','label','fixture']]
   if pieces:
    bounds=[self.bounds(ob) for ob in pieces];row['actual_min_m']=np.min([b[0] for b in bounds],axis=0).tolist();row['actual_max_m']=np.max([b[1] for b in bounds],axis=0).tolist()
  for row in self.layout:
   if row.get('kind')!='exhibit':continue
   cases=[ob for ob in self.scene.objects if ob.type=='MESH' and not ob.hide_render and (ob.name.startswith(row['name']+'_Case') or ob.name in [row['name']+'_Plinth',row['name']+'_Cap',row['name']+'_Base'])]
   if any(ob.name.startswith(row['name']+'_Case') for ob in cases):
    bounds=[self.bounds(ob) for ob in cases];row['display_envelope_m']=[np.min([b[0] for b in bounds],axis=0).tolist(),np.max([b[1] for b in bounds],axis=0).tolist()]
  used=set();factories=set()
  for ob in self.scene.objects:
   if ob.type!='MESH' or ob.hide_render:continue
   for i in {p.material_index for p in ob.data.polygons}:
    if i<len(ob.material_slots) and ob.material_slots[i].material:used.add(ob.material_slots[i].material.name)
   if ob.get('source_factory_component'):factories.add(ob['source_factory_component'])
  licenses=[]
  for row in self.licenses.values():
   row=dict(row)
   if 'used_for_materials' in row:
    row['used_for_materials']=[x for x in row['used_for_materials'] if x in used]
    if not row['used_for_materials']:continue
   licenses.append(row)
  (self.out/'MATERIALS.json').write_text(json.dumps({k:v for k,v in self.material_records.items() if self.materials[k].name in used},ensure_ascii=False,indent=2));(self.out/'ASSET_LICENSES.json').write_text(json.dumps({'assets':licenses,'procedural':{'source':'Museum29 authored geometry and baked textures','license':'procedural'},'infinigen':{'source':'Princeton infinigen architectural factory geometry','factories_used':sorted(factories),'license':'BSD-3-Clause source; procedural generated geometry','generation_manifest':str(self.root/'library/procedural_components/attempt003/GENERATIONS.json')}},ensure_ascii=False,indent=2));(self.out/'LAYOUT.json').write_text(json.dumps(self.layout,ensure_ascii=False,indent=2))
