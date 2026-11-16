"""Exports evaluated Blender geometry to portable UsdPreviewSurface and exact collision USD."""
from pathlib import Path
import os, sys, json, math, hashlib, shutil, collections, time, io
Path('/proc/self/oom_score_adj').write_text('1000')
import bpy, numpy as np
from pxr import Usd, UsdGeom, UsdShade, UsdPhysics, UsdLux, Sdf, Gf, Vt, Tf
from PIL import Image
ROOT=Path('/cephfs/shared/yixing/outputs/museum29_20261107')
blend=Path(sys.argv[1]);out=Path(sys.argv[2]);mode=sys.argv[3] if len(sys.argv)>3 else 'both'
out.mkdir(parents=True,exist_ok=True);texdir=out/'scene'/'textures';texdir.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(blend));scene=bpy.context.scene;dg=bpy.context.evaluated_depsgraph_get()
def make_stage(path):
    st=Usd.Stage.CreateNew(str(path));UsdGeom.SetStageUpAxis(st,UsdGeom.Tokens.z);UsdGeom.SetStageMetersPerUnit(st,1)
    root=UsdGeom.Xform.Define(st,'/World');st.SetDefaultPrim(root.GetPrim());return st
visual=make_stage(out/'scene'/'museum.usdc') if mode=='both' else None
if visual:UsdGeom.Xform.Define(visual,'/MuseumPrototypes').CreateVisibilityAttr().Set('invisible')
cp=out/'collision_package';cp.mkdir(exist_ok=True);collision=make_stage(cp/'collision_only.usdc')
graymat=UsdShade.Material.Define(collision,'/World/Looks/CollisionGray');gs=UsdShade.Shader.Define(collision,'/World/Looks/CollisionGray/Surface');gs.CreateIdAttr('UsdPreviewSurface');gs.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set((.52,.57,.60));gs.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(.65);graymat.CreateSurfaceOutput().ConnectToSource(gs.ConnectableAPI(),'surface')
materials={};imagefiles={};image_transforms=[];inventory=[];vrows=[];prototype_by_data={};proto_count=0
def imagefile(im):
    key=im.as_pointer()
    if key in imagefiles:return imagefiles[key]
    src=Path(bpy.path.abspath(im.filepath)) if im.filepath else None
    if src and src.is_file():
        payload=src.read_bytes();original_sha=hashlib.sha256(payload).hexdigest()
        if src.suffix.lower() in ['.png','.jpg','.jpeg']:
            with Image.open(src) as original:
                old_size=list(original.size)
                if max(original.size)>2048:
                    reduced=original.copy();reduced.thumbnail((2048,2048),Image.Resampling.LANCZOS)
                    if 'normal' in src.name.lower() and reduced.mode in ['RGB','RGBA']:
                        pixels=np.asarray(reduced).copy();normal=pixels[:,:,:3].astype(np.float32)/127.5-1.;normal/=np.maximum(np.linalg.norm(normal,axis=2,keepdims=True),1e-6);pixels[:,:,:3]=np.clip(np.round((normal+1)*127.5),0,255).astype(np.uint8);reduced=Image.fromarray(pixels)
                    stream=io.BytesIO();reduced.save(stream,format='PNG' if src.suffix.lower()=='.png' else 'JPEG',**({} if src.suffix.lower()=='.png' else {'quality':95,'subsampling':0}));payload=stream.getvalue();image_transforms.append({'source':str(src),'source_sha256':original_sha,'source_resolution':old_size,'output_sha256':hashlib.sha256(payload).hexdigest(),'output_resolution':list(reduced.size),'operation':'Lanczos fit within2048; normal vectors renormalized when named normal map; library source unchanged'})
        digest=hashlib.sha256(payload).hexdigest();dst=texdir/(digest[:16]+'_'+src.name)
        if not dst.exists():dst.write_bytes(payload)
    else:
        digest=hashlib.sha256(im.name.encode()).hexdigest();dst=texdir/(digest[:16]+'.png');im.filepath_raw=str(dst);im.file_format='PNG';im.save()
    imagefiles[key]='textures/'+dst.name;return imagefiles[key]
def upstream_image(socket,seen=None):
    seen=set() if seen is None else seen
    for link in socket.links:
        node=link.from_node
        if node.as_pointer() in seen:continue
        seen.add(node.as_pointer())
        if node.type=='TEX_IMAGE' and node.image:return node.image,link.from_socket.name
        for s in node.inputs:
            if s.is_linked:
                r=upstream_image(s,seen)
                if r:return r
    return None
def material(m,scope='/World/Looks'):
    key=(m.as_pointer(),scope)
    if key in materials:return materials[key]
    path=scope+'/'+Tf.MakeValidIdentifier(m.name)
    mat=UsdShade.Material.Define(visual,path);sh=UsdShade.Shader.Define(visual,path+'/Surface');sh.CreateIdAttr('UsdPreviewSurface')
    nodes=[n for n in m.node_tree.nodes if n.type=='BSDF_PRINCIPLED'] if m.use_nodes else []
    bs=nodes[0] if nodes else None
    color=tuple(bs.inputs['Base Color'].default_value[:3]) if bs else tuple(m.diffuse_color[:3]);rough=float(bs.inputs['Roughness'].default_value) if bs else .5;metal=float(bs.inputs['Metallic'].default_value) if bs else 0
    sh.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color));sh.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(rough);sh.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metal)
    trans=float(bs.inputs['Transmission Weight'].default_value) if bs and 'Transmission Weight' in bs.inputs else 0
    if trans>.5:
        sh.CreateInput('opacity',Sdf.ValueTypeNames.Float).Set(float(m.get('museum_preview_opacity',.16)));sh.CreateInput('ior',Sdf.ValueTypeNames.Float).Set(1.5);sh.CreateInput('useSpecularWorkflow',Sdf.ValueTypeNames.Int).Set(0)
    uv=UsdShade.Shader.Define(visual,path+'/UV');uv.CreateIdAttr('UsdPrimvarReader_float2');uv.CreateInput('varname',Sdf.ValueTypeNames.Token).Set('st')
    if bs:
        for label,socket,channel in [('diffuseColor','Base Color','rgb'),('roughness','Roughness','r'),('metallic','Metallic','r'),('normal','Normal','rgb')]:
            source=upstream_image(bs.inputs[socket])
            if not source:continue
            im,source_channel=source;tx=UsdShade.Shader.Define(visual,path+'/'+label+'Texture');tx.CreateIdAttr('UsdUVTexture');tx.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(imagefile(im)));tx.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),'result');tx.CreateInput('wrapS',Sdf.ValueTypeNames.Token).Set('repeat');tx.CreateInput('wrapT',Sdf.ValueTypeNames.Token).Set('repeat');tx.CreateInput('sourceColorSpace',Sdf.ValueTypeNames.Token).Set('sRGB' if label=='diffuseColor' else 'raw')
            if label in ['roughness','metallic']:
                # glTF ORM channels: G roughness, B metallic. Separate-node chains retain the original image.
                channel='g' if label=='roughness' and any(l.from_node.type in ['SEPRGB','SEPARATE_COLOR'] for l in bs.inputs[socket].links) else ('b' if label=='metallic' and any(l.from_node.type in ['SEPRGB','SEPARATE_COLOR'] for l in bs.inputs[socket].links) else 'r')
            if label=='normal':
                # Preserve Blender NormalMap strength instead of exporting every floor at strength1.
                normal_nodes=[link.from_node for link in bs.inputs[socket].links if link.from_node.type=='NORMAL_MAP'];strength=float(normal_nodes[0].inputs['Strength'].default_value) if normal_nodes else 1.
                tx.CreateInput('scale',Sdf.ValueTypeNames.Float4).Set((2*strength,2*strength,2*strength,1));tx.CreateInput('bias',Sdf.ValueTypeNames.Float4).Set((-strength,-strength,1-2*strength,0));tx.GetPrim().CreateAttribute('museum:normalStrength',Sdf.ValueTypeNames.Float).Set(strength)
            typ=Sdf.ValueTypeNames.Normal3f if label=='normal' else (Sdf.ValueTypeNames.Color3f if channel=='rgb' else Sdf.ValueTypeNames.Float)
            sh.CreateInput(label,typ).ConnectToSource(tx.ConnectableAPI(),channel)
        alpha=bs.inputs.get('Alpha');alpha_source=upstream_image(alpha) if alpha else None
        if alpha_source:
            im,_=alpha_source;tx=UsdShade.Shader.Define(visual,path+'/OpacityTexture');tx.CreateIdAttr('UsdUVTexture');tx.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(imagefile(im)));tx.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),'result');tx.CreateInput('wrapS',Sdf.ValueTypeNames.Token).Set('repeat');tx.CreateInput('wrapT',Sdf.ValueTypeNames.Token).Set('repeat');tx.CreateInput('sourceColorSpace',Sdf.ValueTypeNames.Token).Set('raw');sh.CreateInput('opacity',Sdf.ValueTypeNames.Float).ConnectToSource(tx.ConnectableAPI(),'a');sh.CreateInput('opacityThreshold',Sdf.ValueTypeNames.Float).Set(.5)
        elif alpha and not alpha.is_linked and alpha.default_value<.999 and trans<=.5:sh.CreateInput('opacity',Sdf.ValueTypeNames.Float).Set(float(alpha.default_value))
        vertex_color=any(n.type in ['VERTEX_COLOR','ATTRIBUTE'] for n in m.node_tree.nodes)
        if vertex_color and not upstream_image(bs.inputs['Base Color']):
            reader=UsdShade.Shader.Define(visual,path+'/VertexColor');reader.CreateIdAttr('UsdPrimvarReader_float3');reader.CreateInput('varname',Sdf.ValueTypeNames.Token).Set('displayColor');sh.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).ConnectToSource(reader.ConnectableAPI(),'result')
        if vertex_color and alpha and alpha.is_linked and not alpha_source:
            reader=UsdShade.Shader.Define(visual,path+'/VertexOpacity');reader.CreateIdAttr('UsdPrimvarReader_float');reader.CreateInput('varname',Sdf.ValueTypeNames.Token).Set('displayOpacity');sh.CreateInput('opacity',Sdf.ValueTypeNames.Float).ConnectToSource(reader.ConnectableAPI(),'result')
    mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(),'surface');materials[key]=mat;return mat
def group(o):
    name=o.name
    if name.startswith(('Stair_','Bridge_','Landing_')):return 'Stair'
    if name.startswith(('Exhibit_','Plinth_','Rope_','Case_','Bench_','Painting_','Frame_','Label_','Chandelier_')):return 'Exhibits'
    return 'Architecture'
def author_mesh(st,path,me,ob,bind=True,world=True,material_scope='/World/Looks'):
    mesh=UsdGeom.Mesh.Define(st,path);co=np.empty(len(me.vertices)*3,dtype=np.float32);me.vertices.foreach_get('co',co);co=co.reshape(-1,3)
    if world:
        transform=np.asarray(ob.matrix_world,dtype=float);co=(co@transform[:3,:3].T+transform[:3,3]).astype(np.float32)
    # Explicit evaluated Blender tessellation preserves concave door openings.
    # A polygon fan in downstream readers otherwise invents walls across U-frames.
    me.calc_loop_triangles();triangles=list(me.loop_triangles);counts=np.full(len(triangles),3,dtype=np.int32);indices=np.asarray([t.vertices[:] for t in triangles],dtype=np.int32).reshape(-1);corners=np.asarray([t.loops[:] for t in triangles],dtype=np.int32).reshape(-1)
    mesh.GetPrim().CreateAttribute('museum:role',Sdf.ValueTypeNames.String,custom=True).Set(str(ob.get('museum_category','architecture')));mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(co));mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(counts));mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(indices));mesh.CreateSubdivisionSchemeAttr('none');mesh.CreateDoubleSidedAttr(True);mesh.CreateExtentAttr([Gf.Vec3f(*map(float,co.min(0))),Gf.Vec3f(*map(float,co.max(0)))])
    if visual and bind:
        if len(me.corner_normals)==len(me.loops):
            normals=np.empty(len(me.loops)*3,dtype=np.float32);me.corner_normals.foreach_get('vector',normals);normals=normals.reshape(-1,3)[corners]
            if world:normals=normals@np.linalg.inv(np.asarray(ob.matrix_world)[:3,:3]);normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-12)
            mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(normals.astype(np.float32)));mesh.SetNormalsInterpolation('faceVarying')
        if me.uv_layers:
            arr=np.empty(len(me.loops)*2,dtype=np.float32);me.uv_layers.active.data.foreach_get('uv',arr);uv=UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,'faceVarying');uv.Set(Vt.Vec2fArray.FromNumpy(arr.reshape(-1,2)[corners].copy()))
        if me.color_attributes:
            attr=me.color_attributes.active_color or me.color_attributes[0];colors=np.empty(len(attr.data)*4,dtype=np.float32);attr.data.foreach_get('color',colors);colors=colors.reshape(-1,4)
            colors=colors[indices] if attr.domain=='POINT' else colors[corners]
            assert len(colors)==len(indices),'COLOR_DOMAIN_MISMATCH';api=UsdGeom.PrimvarsAPI(mesh);api.CreatePrimvar('displayColor',Sdf.ValueTypeNames.Color3fArray,'faceVarying').Set(Vt.Vec3fArray.FromNumpy(colors[:,:3].copy()));api.CreatePrimvar('displayOpacity',Sdf.ValueTypeNames.FloatArray,'faceVarying').Set(Vt.FloatArray.FromNumpy(colors[:,3].copy()))
        if len(ob.material_slots)==1 and ob.material_slots[0].material:UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material(ob.material_slots[0].material,material_scope))
        elif len(ob.material_slots)>1:
            for idx,slot in enumerate(ob.material_slots):
                faces=[i for i,t in enumerate(triangles) if me.polygons[t.polygon_index].material_index==idx]
                if not faces or not slot.material:continue
                subset=UsdGeom.Subset.CreateGeomSubset(mesh,Tf.MakeValidIdentifier(slot.material.name)+str(idx),UsdGeom.Tokens.face,Vt.IntArray(faces),'materialBind','partition');UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(material(slot.material,material_scope))
    return mesh,co,int(sum(counts-2))

data_counts=collections.Counter(o.data.as_pointer() for o in scene.objects if o.type=='MESH')
for i,ob in enumerate(scene.objects):
    if ob.type!='MESH' or ob.hide_render:continue
    path='/World/'+group(ob)+'/'+Tf.MakeValidIdentifier(ob.name)
    evaluated=ob.evaluated_get(dg);me=evaluated.to_mesh()
    if not len(me.vertices):evaluated.to_mesh_clear();continue
    iscollision=bool(ob.get('museum_collision',False))
    if visual:
        # Imported linked geometry is wrapped in an anonymous local prototype with material binding inside.
        key=(ob.data.as_pointer(),tuple(s.material.as_pointer() if s.material else 0 for s in ob.material_slots))
        instances=data_counts[ob.data.as_pointer()]
        if instances>1 and not ob.modifiers:
            if key not in prototype_by_data:
                proto_count+=1;pp='/MuseumPrototypes/P'+str(proto_count);author_mesh(visual,pp+'/Mesh',me,ob,world=False,material_scope=pp+'/Looks');prototype_by_data[key]=pp
            xf=UsdGeom.Xform.Define(visual,path);xf.GetPrim().GetReferences().AddInternalReference(prototype_by_data[key]);xf.AddTransformOp().Set(Gf.Matrix4d(np.asarray(ob.matrix_world).T.tolist()));xf.GetPrim().SetInstanceable(True)
        else:author_mesh(visual,path,me,ob)
        vrows.append({'name':ob.name,'path':path,'collision':iscollision,'category':ob.get('museum_category','unknown')})
    if iscollision:
        cm,co,tris=author_mesh(collision,path,me,ob,False);UsdPhysics.CollisionAPI.Apply(cm.GetPrim()).CreateCollisionEnabledAttr(True);UsdPhysics.MeshCollisionAPI.Apply(cm.GetPrim()).CreateApproximationAttr('none');UsdShade.MaterialBindingAPI.Apply(cm.GetPrim()).Bind(graymat)
        inventory.append({'name':ob.name,'path':path,'approximation':'none','vertices':len(me.vertices),'triangles':tris,'bounds_min_m':co.min(0).tolist(),'bounds_max_m':co.max(0).tolist(),'sha256_geometry':hashlib.sha256(co.tobytes()+np.array(cm.GetFaceVertexIndicesAttr().Get(),dtype=np.int32).tobytes()).hexdigest()})
    evaluated.to_mesh_clear()
    if i%500==0:print('EXPORTED',i,ob.name,flush=True)

if visual:
    physics=UsdGeom.Xform.Define(visual,'/World/Physics');physics.GetPrim().GetReferences().AddReference('../collision_package/collision_only.usdc');physics.CreatePurposeAttr().Set('guide')
    # Prototypes are non-imageable as roots; instances reference their children, avoiding duplicate display.
    if visual.GetPrimAtPath('/MuseumPrototypes'):
        UsdGeom.Xform(visual.GetPrimAtPath('/MuseumPrototypes')).CreateVisibilityAttr().Set('invisible')
    for ob in scene.objects:
        if ob.type=='CAMERA':
            cam=UsdGeom.Camera.Define(visual,'/World/Cameras/'+Tf.MakeValidIdentifier(ob.name));cam.CreateFocalLengthAttr(ob.data.lens);cam.CreateHorizontalApertureAttr(ob.data.sensor_width);cam.CreateVerticalApertureAttr(ob.data.sensor_width*9/16);cam.CreateClippingRangeAttr(Gf.Vec2f(.05,500));cam.AddTransformOp().Set(Gf.Matrix4d(np.asarray(ob.matrix_world).T.tolist()))
        elif ob.type=='LIGHT':
            d=ob.data;path='/World/Lights/'+Tf.MakeValidIdentifier(ob.name)
            if d.type=='AREA':
                light=UsdLux.DiskLight.Define(visual,path);light.CreateRadiusAttr(d.size/2);light.CreateIntensityAttr(d.energy*1.5);light.CreateNormalizeAttr(False)
            elif d.type=='SUN':light=UsdLux.DistantLight.Define(visual,path);light.CreateIntensityAttr(float(d.get('museum_intensity_override',1800)));light.CreateAngleAttr(math.degrees(d.angle))
            else:light=UsdLux.SphereLight.Define(visual,path);light.CreateRadiusAttr(max(d.shadow_soft_size,.1));light.CreateIntensityAttr(d.energy)
            light.CreateColorAttr(Gf.Vec3f(*d.color));UsdGeom.Xformable(light).AddTransformOp().Set(Gf.Matrix4d(np.asarray(ob.matrix_world).T.tolist()))
            if d.type=='AREA' and 'museum_ghost_factor' in d:
                light.GetPrim().CreateAttribute('visibleInPrimaryRay',Sdf.ValueTypeNames.Bool,custom=True).Set(False);light.GetPrim().CreateAttribute('ghostFactor',Sdf.ValueTypeNames.Float,custom=True).Set(float(d['museum_ghost_factor']))
            if 'museum_source_kelvin' in d:
                kelvin=float(d['museum_source_kelvin']);light.CreateEnableColorTemperatureAttr(True);light.CreateColorTemperatureAttr(kelvin)
                blackbody=UsdLux.BlackbodyTemperatureAsRgb(kelvin);balanced=Gf.Vec3f(*[float(d.color[k])/max(float(blackbody[k]),1e-6) for k in range(3)]);light.CreateColorAttr(balanced)
                light.GetPrim().CreateAttribute('museum:temperatureSaturationFraction',Sdf.ValueTypeNames.Float,custom=True).Set(float(d.get('museum_temperature_saturation_fraction',1.)))
    dome=UsdLux.DomeLight.Define(visual,'/World/Lights/Environment');dome.CreateIntensityAttr(450);dome.CreateColorAttr(Gf.Vec3f(.72,.82,1.0))
    if scene.get('museum_hdri_source'):
        hp=Path(scene['museum_hdri_source']);hdigest=hashlib.sha256(hp.read_bytes()).hexdigest();dest=texdir/(hdigest[:16]+hp.suffix);shutil.copyfile(hp,dest);dome.CreateTextureFileAttr(Sdf.AssetPath('textures/'+dest.name));dome.CreateTextureFormatAttr('latlong')
    visual.GetRootLayer().Save()
collision.GetRootLayer().Save()
if visual:
    dependency_files=sorted([*(out/'scene').rglob('*'),cp/'collision_only.usdc']);dependency_hashes={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in dependency_files if p.is_file()}
    (out/'SCENE_ASSET_BINDING.json').write_text(json.dumps({'status':'COMPLETE','files_sha256':dependency_hashes,'scope':'Every visual scene file plus referenced collision USD; source library untouched'},indent=2))
(cp/'collision_only_PACKAGE.json').write_text(json.dumps({'schema':'MuseumCollisionInventoryV1','source_blend':str(blend),'coordinate_frame':'World metres, Z-up','enabled_colliders':len(inventory),'colliders':inventory},indent=2))
(cp/'PACKAGE.json').write_text(json.dumps({'building':blend.parent.parent.parent.name,'collision_file':'collision_only.usdc','scene_file':'scenes.pt','enabled_colliders':len(inventory),'coordinate_frame':'Original world metres, Z-up, scale1','checks':'PENDING','training':'NOT_EXECUTED','formal_acceptance':'PENDING'},indent=2))
(out/'EXPORT_RECEIPT.json').write_text(json.dumps({'source_blend':str(blend),'source_sha256':hashlib.sha256(blend.read_bytes()).hexdigest(),'mode':mode,'visual_objects':len(vrows),'colliders':len(inventory),'prototype_count':proto_count,'textures':len(imagefiles),'image_transforms':image_transforms,'visual_rows':vrows},indent=2))
print('EXPORT_DONE',len(vrows),'visual',len(inventory),'colliders',flush=True)
sys.stdout.flush();sys.stderr.flush();os._exit(0)
