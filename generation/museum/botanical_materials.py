"""Original portable alpha leaf textures and ringed bark; no plant factory."""
from pathlib import Path
import hashlib,json
import numpy as np,bpy
from PIL import Image

def materials(B):
 if 'BotanyPalm25' in B.materials:return
 n=2048;v,u=np.mgrid[:n,:n].astype(np.float32)/n;rng=np.random.default_rng(2507001)
 noise=rng.normal(0,1,(n,n)).astype(np.float32)
 records=[]
 for kind,name in [('palm','BotanyPalm25'),('banana','BotanyBanana25'),('broadleaf','BotanyBroadleaf25'),('fern','BotanyFern25'),('bark','BotanyTrunk25')]:
  leaf=kind!='bark';m=bpy.data.materials.new(name);m.use_nodes=True;bs=m.node_tree.nodes.get('Principled BSDF');bs.inputs['Roughness'].default_value=.63 if leaf else .91
  if leaf:
   radius=abs(v-.5)*2;silhouette=np.clip(np.sin(np.pi*u),0,1)**.56
   if kind=='banana':
    alpha=(v>.012+.008*np.sin(u*73))&(v<.988-.008*np.sin(u*61));tears=np.zeros_like(alpha)
    for j in range(13):
     t=.13+j*.061;slant=u-t-.11*abs(v-.5);edge=(v<.16+(j%3)*.025)|(v>.84-(j%4)*.02);tears|=(abs(slant)<.0015+(j%3)*.0005)&edge
    alpha&=~tears;vein=np.exp(-((v-.5)/.008)**2);sidevein=np.cos((u-.19*abs(v-.5))*2*np.pi*31)**28
   else:
    serration=.012*np.sin(u*230) if kind=='fern' else .004*np.sin(u*140)
    alpha=(radius<silhouette*.97+serration)&(u>.007)&(u<.994);vein=np.exp(-((v-.5)/.014)**2);sidevein=np.maximum(0,np.cos((u-.30*abs(v-.5))*2*np.pi*23))**36
   palette=np.array([.075,.31,.062]) if kind=='banana' else np.array([.032,.225,.045]) if kind=='palm' else np.array([.047,.265,.055]) if kind=='broadleaf' else np.array([.045,.24,.035])
   variation=.80+.20*np.sin(u*4.1+.7)+.10*np.sin(v*3.4)+.035*noise+.05*np.sin(u*133+v*41);color=palette[None,None,:]*variation[...,None];color+=vein[...,None]*np.array([.095,.11,.025])+sidevein[...,None]*np.array([.008,.018,.004]);color*=1-.10*radius[...,None]
   encoded=np.where(color<=.0031308,color*12.92,1.055*np.power(np.maximum(color,0),1/2.4)-.055);a=np.rint(np.clip(np.concatenate([encoded,alpha[...,None]],2),0,1)*255).astype(np.uint8);mode='RGBA'
  else:
   ring=(v*19+.065*np.sin(u*37))%1;seam=np.exp(-((ring-.14)/.025)**2);fibers=np.sin(u*490+np.sin(v*31))*np.sin(u*171);color=np.array([.32,.235,.15])[None,None,:]*(.95+.15*fibers[...,None]+.025*noise[...,None]);color-=seam[...,None]*np.array([.13,.10,.062]);encoded=np.where(color<=.0031308,color*12.92,1.055*np.power(np.maximum(color,0),1/2.4)-.055);a=np.rint(np.clip(encoded,0,1)*255).astype(np.uint8);mode='RGB'
  path=B.tex/(name+'.png');Image.fromarray(a,mode=mode).save(path);im=bpy.data.images.load(str(path),check_existing=True);node=m.node_tree.nodes.new('ShaderNodeTexImage');node.image=im;node.extension='REPEAT';m.node_tree.links.new(node.outputs['Color'],bs.inputs['Base Color'])
  if leaf:m.node_tree.links.new(node.outputs['Alpha'],bs.inputs['Alpha']);m.surface_render_method='DITHERED'
  m['uv_repeat_xy_m']=[1.,1.];B.materials[name]=m;record={'part':name,'kind':'original_alpha_botanical_leaf_card25' if leaf else 'original_ringed_bark25','texture':str(path),'texture_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'resolution':[n,n],'source_asset':'procedural original ADJUST25','source_license':'procedural original','shader':'UsdPreviewSurface + portable sRGB image + alpha','roughness':.63 if leaf else .91,'base_color_linear':[.06,.24,.04] if leaf else [.32,.235,.15],'uv_repeat_xy_m':[1.,1.],'appearance':'Original botanical vein/edge-tear/leaflet silhouette and tonal variation; not a scan or species-accurate photograph.','visual_collision_exception':'Alpha cuts affect appearance; existing exact uncut low-leaf mesh colliders remain. Tall palm/banana leaves remain visual only above actual local floor+2m.' if leaf else None};B.material_records[name]=record;records.append(record);B.licenses['procedural:'+name]={'source':'procedural','id':name,'license':'procedural original','source_sha256':record['texture_sha256'],'title':record['kind']}
 (B.out/'LEAF25_AUTHORING.json').write_text(json.dumps({'status':'AUTHORED_REQUIRES_EXPORTED_CHECKS_AND_NATIVE_IMAGES','textures':records,'palm_leaflets_per_compound':38,'banana':'Continuous whole-leaf UV over segmented leaf mesh, midrib/31 side vein periods/13 edge tears in RGBA','geometry_rule':'All original22 vertex positions, polygon indices and low-leaf collider flags unchanged; new UV/material/alpha only.','factory_invocations':0},indent=2))

def apply_uv(ob,uvs):
 layer=ob.data.uv_layers.get('st');assert layer
 if uvs is not None:
  assert len(uvs)==len(ob.data.polygons)
  for face,coords in zip(ob.data.polygons,uvs):
   assert len(coords)==len(face.loop_indices)
   for loop,uv in zip(face.loop_indices,coords):layer.data[loop].uv=uv
 else:
  for face in ob.data.polygons:
   points=np.asarray([ob.data.vertices[ob.data.loops[i].vertex_index].co[:] for i in face.loop_indices]);angle=(np.arctan2(points[:,1],points[:,0])+np.pi)/(2*np.pi)
   if np.ptp(angle)>.5:angle[angle<.5]+=1
   for j,loop in enumerate(face.loop_indices):layer.data[loop].uv=(float(angle[j]),float(points[j,2]))
