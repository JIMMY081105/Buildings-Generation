"""Retain actual scan maps; never invent a scanned RGB albedo where none exists."""
from pathlib import Path
import json,struct,hashlib,numpy as np

def texture_info(path):
 p=Path(path)
 if p.suffix.lower()=='.glb':
  with p.open('rb') as f:
   header=f.read(20);assert header[:4]==b'glTF';n,kind=struct.unpack_from('<I4s',header,12);assert kind==b'JSON' and n<20_000_000;doc=json.loads(f.read(n))
 else:doc=json.loads(p.read_text())
 channels=[]
 for material in doc.get('materials',[]):
  if material.get('normalTexture'):channels.append('normal')
  if material.get('occlusionTexture'):channels.append('occlusion')
  pbr=material.get('pbrMetallicRoughness',{})
  if pbr.get('baseColorTexture'):channels.append('baseColor')
  if pbr.get('metallicRoughnessTexture'):channels.append('metallicRoughness')
 return {'source_images':len(doc.get('images',[])),'referenced_texture_channels':sorted(set(channels)),'has_scanned_RGB_albedo':'baseColor' in channels,'source_file_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

def adapt_scan_materials(B,row,ob):
 if row.get('source')!='Smithsonian':return
 info=texture_info(row['path']);key=row['source']+':'+str(row['id'])
 if B.spec['building_id']=='M04':assert info['source_images']>0 and info['referenced_texture_channels'],'ADJUST08_NO_UNTEXTURED_SCAN:'+key
 adjustments=[]
 def upstream_image(socket,seen):
  for link in socket.links:
   node=link.from_node
   if node.as_pointer() in seen:continue
   seen.add(node.as_pointer())
   if node.type=='TEX_IMAGE' and node.image:return node.image
   for input in node.inputs:
    if input.is_linked:
     image=upstream_image(input,seen)
     if image:return image
  return None
 for mat in ob.data.materials:
  if not mat or not mat.use_nodes:continue
  nodes=mat.node_tree.nodes;bs=next((n for n in nodes if n.type=='BSDF_PRINCIPLED'),None)
  if not bs or bs.inputs['Base Color'].is_linked:continue
  ao=None
  for node in nodes:
   socket=node.inputs.get('Occlusion')
   if socket and socket.is_linked:ao=upstream_image(socket,set());break
  if not ao:
   assert 'occlusion' not in info['referenced_texture_channels'],'SOURCE_AO_NOT_FOUND_IN_IMPORTED_SHADER';continue
  w,h=ao.size
  if max(w,h)>2048:
   scale=2048/max(w,h);ao.scale(round(w*scale),round(h*scale));w,h=ao.size
  pixels=np.empty(w*h*4,dtype=np.float32);ao.pixels.foreach_get(pixels);a=pixels.reshape(h,w,4)[:,:,0];assert np.isfinite(a).all();base=np.array([.65,.57,.43]);linear=np.clip(a[:,:,None]*base,0,1)
  from blender_common import srgb
  from PIL import Image
  code=hashlib.sha256((key+mat.name).encode()).hexdigest()[:18];p=B.tex/(code+'_source_AO_bone.png');Image.fromarray(np.flipud(np.round(np.clip(srgb(linear),0,1)*255).astype(np.uint8))).save(p)
  import bpy
  image=bpy.data.images.load(str(p),check_existing=True);tx=nodes.new('ShaderNodeTexImage');tx.image=image;mat.node_tree.links.new(tx.outputs['Color'],bs.inputs['Base Color']);bs.inputs['Metallic'].default_value=0
  source_ao_sha=hashlib.sha256(ao.packed_file.data).hexdigest() if ao.packed_file else None
  B.materials[mat.name]=mat;B.material_records[mat.name]={'part':mat.name,'kind':'original_scan_AO_with_declared_neutral_bone_shading','texture':str(p),'texture_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'resolution':[w,h],'source_asset':key,'source_license':row['license'],'source_url':row.get('url'),'source_ao_image':ao.name,'source_ao_packed_sha256':source_ao_sha,'source_ao_range':[float(a.min()),float(a.max())],'source_ao_std':float(a.std()),'neutral_base_color_linear':base.tolist(),'source_has_RGB_albedo':False,'method':'Original scan AO multiplied by declared neutral bone color; original normal map retained. No claim of original scanned RGB color.'};adjustments.append(B.material_records[mat.name])
 B.model_import_records=getattr(B,'model_import_records',{});B.model_import_records[key]={'source_texture_info':info,'material_adjustments':adjustments,'source_orientation_deg':B.spec['exhibition'].get('source_orientation_deg',{}).get(key),'transform_baking':'Blender native transform_apply, XYZ selected explicitly for pose/yaw; actual geometry/yaw assertions'}
