"""Layered tropical garden, genuine factory palms and exact low plant collision."""
from pathlib import Path
import json,math,hashlib,collections
import numpy as np,bpy
from mathutils import Vector
from shapely import wkt
from shapely.geometry import Point,Polygon,LineString,box
from shapely.ops import unary_union
from staircase_core import Geometry
from building_core import extrude

def materials(B):
 B.material('BotanySoil19',(.045,.032,.014),'stone',.40,.99);B.material('BotanyEdge19',(.24,.28,.23),'ashlar',1.2,.9,source_hint='limestone');B.material('BotanyLeaf19',(.028,.19,.045),'original_tropical_leaf',.8,.46);B.material('BotanyLeafLight19',(.065,.27,.055),'original_tropical_leaf',.8,.50);B.material('BotanyTrunk19',(.19,.10,.045),'wood',.55,.94)

def compact_mesh(B,name,vertices,faces,material,collision,role):
 used=np.unique(np.concatenate(faces));mapping={old:i for i,old in enumerate(used)};return B.mesh(name,np.asarray(vertices)[used],[[mapping[i] for i in face] for face in faces],material,collision,role)

def factory_catalog(B):
 raise RuntimeError('ADJUST22_DISABLED_FACTORY19_ROUTE_USE_CACHED_CC0_OR_LIGHT_ORIGINAL_PLANTS22')
 p=B.root/'evidence/TROPICAL19_SELECTED.json';q=json.loads(p.read_text());d=Path(q['directory']);sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();assert q['status']=='PASS' and sha(d/'RESULT.json')==q['result_sha256'] and sha(d/'components.blend')==q['components_blend_sha256'];doc=json.loads((d/'RESULT.json').read_text());assert doc['status']=='PASS' and len(doc['components'])==6
 B.licenses['Infinigen:tropical_factories19']={'source':'Infinigen','id':'tropical_factories19','license':'BSD-3-Clause','url':'https://github.com/princeton-vl/infinigen','components_blend_sha256':q['components_blend_sha256'],'original_source_license_sha256':doc['source_license_sha256'],'scope':'Real upstream PalmTreeFactory/CoconutTreeFactory/BananaMonocotFactory evaluated meshes, normalization/size adaptation and portable material replacement; recorded compatibility adapters retain dependencies and realize instances, upstream disk unchanged.'}
 return d,{x['key']:x for x in doc['components']}

def factory_plant(B,name,key,x,y,z,height,width,index,floor,bed,catalog):
 directory,records=catalog;record=records[key];cache=('tropical19',key)
 if cache not in B.templates:
  with bpy.data.libraries.load(str(directory/'components.blend'),link=False) as (src,dst):assert key in src.objects;dst.objects=[key]
  source=dst.objects[0];B.scene.collection.objects.link(source);source.name='SOURCE_'+key;source.hide_render=True;source.hide_viewport=True;B.templates[cache]=source
 source=B.templates[cache];v=np.asarray([p.co[:] for p in source.data.vertices]);extent=np.ptp(v,axis=0);v[:,:2]*=width/max(extent[:2]);v[:,2]*=height/extent[2];a=(index*.731)%math.tau;R=np.array([[math.cos(a),-math.sin(a)],[math.sin(a),math.cos(a)]]);v[:,:2]=v[:,:2]@R.T+[x,y];v[:,2]+=z;leafslots=set(record['actual_leaf_slots']);structural=[];structural_leaf=[];high=[];pruned=0;threshold=(floor-1)*B.spec['floor_height_m']+2.
 for face in source.data.polygons:
  ids=list(face.vertices);leaf=face.material_index in leafslots;points=v[ids]
  if leaf and points[:,2].min()<threshold and not all(bed.covers(Point(q)) for q in points[:,:2]):pruned+=1;continue
  if leaf and points[:,2].min()>=threshold:high.append(ids)
  else:structural.append(ids);structural_leaf.append(leaf)
 assert structural and high,(key,len(structural),len(high));ob=compact_mesh(B,name+'_TrunkAndLowLeaves',v,structural,'BotanyTrunk19',True,'procedural_exhibit');ob.data.materials.append(B.materials['BotanyLeafLight19' if index%2 else 'BotanyLeaf19'])
 for face,leaf in zip(ob.data.polygons,structural_leaf):face.material_index=int(leaf)
 compact_mesh(B,name+'_HighLeaves',v,high,'BotanyLeafLight19' if index%2 else 'BotanyLeaf19',False,'botanical_leaf');v=v[np.unique(np.concatenate(structural+high))];B.layout.append({'kind':'exhibit','name':name,'floor':floor,'visible_from_gallery_floors':[1,2] if height>=8 else [floor],'source_key':'Infinigen:'+key,'source_model_sha256':record['canonical_geometry_sha256'],'source_asset_title':record['factory']+' actual seed '+str(record['factory_seed']),'theme_category':'tall_tropical_factory_plant','actual_min_m':v.min(0).tolist(),'actual_max_m':v.max(0).tolist(),'support_z_m':z,'support_embed_m':0.,'actual_height_m':float(np.ptp(v[:,2])),'actual_width_m':float(np.ptp(v[:,:2],axis=0).max()),'exact_structural_low_leaf_faces':len(structural),'visible_only_leaf_faces_above2m':len(high),'low_leaf_faces_pruned_outside_beds':pruned,'collision':'Exact actual source trunk all heights plus leaves touching/below absolute2m; high leaf faces visual only; no convex/AABB substitute','source_factory_component':record})
 return B.layout[-1]

def bed_mesh(B,name,polygon,z,floor):
 assert polygon.is_valid and polygon.area>2;inner=polygon.buffer(-.16,join_style=2);assert not inner.is_empty;g=Geometry();extrude(g,name+'_Soil',inner,z+.015,z+.26,'planting_soil','BotanySoil19');extrude(g,name+'_Curbs',polygon.difference(inner),z,z+.34,'planting_bed_edge','BotanyEdge19')
 for q in g.parts:B.mesh(q['name'],q['vertices'],q['faces'],q['material'],True,q['role'])
 B.layout.append({'kind':'planting_bed','name':name,'floor':floor,'area_m2':polygon.area,'actual_polygon_wkt':polygon.wkt,'soil_surface_z_m':z+.26,'collision':'Exact triangulated soil solids and curb ring, no AABB proxy'});return inner

def fern_plant(B,name,x,y,z,index,floor,bed,scale=1.7):
 from museum_glasshouse import fern
 before={ob.name for ob in B.scene.objects};fern(B,name,x,y,z,index);row=B.layout[-1];row.update(floor=floor,visible_from_gallery_floors=[floor],theme_category='original_layered_fern');threshold=(floor-1)*B.spec['floor_height_m']+2.
 for ob in list(B.scene.objects):
  if ob.name in before or ob.type!='MESH':continue
  vertices=np.asarray([p.co[:] for p in ob.data.vertices]);vertices[:,:2]=(vertices[:,:2]-[x,y])*scale+[x,y];vertices[:,2]=(vertices[:,2]-z)*scale+z;faces=[list(f.vertices) for f in ob.data.polygons];role=ob.get('museum_category');isleaf=role in ['botanical_leaf'];keep=[]
  for face in faces:
   points=vertices[face]
   if isleaf and points[:,2].min()<threshold and not all(bed.covers(Point(q)) for q in points[:,:2]):continue
   keep.append(face)
  # Explicit split for leaves above2m; every remaining low face exact collision.
  for high in [False,True]:
   ff=[face for face in keep if bool(isleaf and vertices[face,2].min()>=threshold)==high]
   if ff:compact_mesh(B,name+'_'+ob.name.split(name)[-1]+('_HighOnly' if high else '_ExactLow'),vertices,ff,'BotanyLeaf19' if isleaf else 'BotanyTrunk19',not high,role)
  bpy.data.objects.remove(ob,do_unlink=True)
 pieces=[o for o in B.scene.objects if o.type=='MESH' and o.name.startswith(name+'_')];bounds=[B.bounds(o) for o in pieces];row['actual_min_m']=np.min([q[0] for q in bounds],axis=0).tolist();row['actual_max_m']=np.max([q[1] for q in bounds],axis=0).tolist();row['source_key']=f'procedural:{B.spec["building_id"]}:layered_fern19:{index}';digest=hashlib.sha256()
 for ob in pieces:digest.update(np.asarray([p.co[:] for p in ob.data.vertices]).tobytes());digest.update(str([list(p.vertices) for p in ob.data.polygons]).encode())
 row['geometry_sha256']=digest.hexdigest();row['collision']='Exact trunk and low leaves; only leaf faces entirely above absolute2m visual only, low aisle overhang pruned.';return row

def ground_beds(B,model):
 shape=wkt.loads(model['footprint_wkt']);w,h=model['building_xy_m'];origin=model['stair_placements'][0]['origin']
 if B.spec['building_id']=='M28':
  ring=LineString([(9.5*math.cos(t),9.5*math.sin(t)) for t in np.linspace(0,math.tau,160)]).buffer(1.0);paths=unary_union([ring,LineString([(0,-h),(0,h)]).buffer(1.0),LineString([(-14,0),(0,0)]).buffer(1.),LineString([(-10,-5),(-7,-8),(0,-10)]).buffer(1.1),Point(-7,-8).buffer(2.2)]);excluded=Point(*origin).buffer(6.15)
 else:
  paths=unary_union([LineString([(-w,0),(w,0)]).buffer(1.0),LineString([(0,-h),(0,h)]).buffer(1.0)]).difference(Point(*origin).buffer(3.65));excluded=Point(*origin).buffer(7.3).difference(Point(*origin).buffer(3.4))
 garden=shape.buffer(-1.8).difference(paths.union(excluded));polys=list(garden.geoms) if garden.geom_type=='MultiPolygon' else [garden];polys=[q for q in polys if q.area>5];beds=[bed_mesh(B,f'Tropical19_GroundBed{i}',q,0.,1) for i,q in enumerate(polys)];assert len(beds)>=2 and sum(q.area for q in beds)>150
 return beds,paths

def exhibition(B,model,room):
 from museum_exhibits import sources,place
 materials(B);catalog=factory_catalog(B);beds,paths=ground_beds(B,model);garden=unary_union(beds);s=B.spec;bid=s['building_id'];H=s['floor_height_m'];w,h=model['building_xy_m'];used=collections.Counter();floor_counts=collections.Counter();tall=[];candidates=[]
 if bid=='M28':
  for x in [-6,-3,3,6]:
   for y in [-6,-3,3,6]:
    if math.hypot(x,y)<14 and garden.covers(Point(x,y).buffer(.7)):candidates.append((x,y))
 else:
  origin=model['stair_placements'][0]['origin'];candidates=[(origin[0]+dx,origin[1]+dy) for dx,dy in [(-1.5,0),(1.5,0),(0,1.6),(0,-1.6)]]
 np.random.default_rng(s['seed']).shuffle(candidates);keys=sorted(catalog[1]);target=12 if bid=='M28' else 4;assert len(candidates)>=target,('TALL_SAFE_PLANT_ROOTS',len(candidates),target)
 for i,(x,y) in enumerate(candidates[:target]):
  key=keys[i%len(keys)];assert used[key]<3;used[key]+=1;height=[11.8,10.6,9.6,8.4][i%4];row=factory_plant(B,f'Tropical19_F1_Tall{i:02}',key,x,y,.26,height,6.2 if 'banana' not in key else 5.5,i,1,garden,catalog);tall.append(row);floor_counts[1]+=1
 # Broad understory patches leave all actual named aisles clear.
 n=0
 for x in np.arange(-w/2+3,w/2-2,3.05):
  for y in np.arange(-h/2+3,h/2-2,3.05):
   if not garden.covers(Point(x,y).buffer(.65)):continue
   fern_plant(B,f'Tropical19_F1_Understory{n:03}',float(x),float(y),.26,100+n,1,garden,1.65 if n%3 else 2.0);floor_counts[1]+=1;n+=1
 assert n>=20,('DENSE_UNDERSTORY_UNDER20',n)
 # Licensed botanical models are additional exact geometry, max3 uses/source.
 pool=sources(B);plantkeys=[k for k in s['exhibition']['source_keys'] if k in pool];i=0
 for key in plantkeys:
  for rep in range(2):
   options=[(float(x),float(y)) for x in np.arange(-w/2+4,w/2-3,4.2) for y in np.arange(-h/2+4,h/2-3,4.2) if garden.covers(box(x-1.1,y-1.1,x+1.1,y+1.1))]
   assert options;np.random.default_rng(s['seed']+i).shuffle(options);x,y=options[i%len(options)];name=f'Tropical19_F1_Licensed{i:02}';maxbox=(2.2,2.2,2.5);ob=place(B,pool[key],name,x,y,.26,maxbox,i*.53);B.layout[-1].update(floor=1,theme_category='licensed_layered_living_plant',visible_from_gallery_floors=[1]);floor_counts[1]+=1;i+=1
 # Continuous peripheral upper-ring beds, with a broad inner walking belt.
 upper=wkt.loads(model['floors'][1]['polygon_wkt']);upperbeds=[]
 if bid=='M28':
  for j in range(12):
   a0=j*math.tau/12+.025;a1=(j+1)*math.tau/12-.025;outer=[(21.0*math.cos(a),21.0*math.sin(a)) for a in np.linspace(a0,a1,22)];inner=[(19.5*math.cos(a),19.5*math.sin(a)) for a in np.linspace(a1,a0,22)];p=Polygon(outer+inner).intersection(upper.buffer(-.12))
   if p.area>3:upperbeds.append(bed_mesh(B,f'Tropical19_UpperRingBed{j}',p,H,2))
 else:
  shape=wkt.loads(model['footprint_wkt']);p=shape.buffer(-1.2).difference(shape.buffer(-3.1)).intersection(upper);pieces=list(p.geoms) if hasattr(p,'geoms') else [p]
  for j,piece in enumerate(pieces):
   if piece.area>5:upperbeds.append(bed_mesh(B,f'Tropical19_UpperLongBed{j}',piece,H,2))
 uppergarden=unary_union(upperbeds);points=[]
 for x in np.arange(-w/2+1.5,w/2-1,2.1):
  for y in np.arange(-h/2+1.5,h/2-1,2.1):
   if uppergarden.covers(Point(x,y).buffer(.20)):points.append((float(x),float(y)))
 if bid=='M28':points=[(20.25*math.cos(a),20.25*math.sin(a)) for a in np.linspace(0,math.tau,25,endpoint=False) if uppergarden.covers(Point(20.25*math.cos(a),20.25*math.sin(a)).buffer(.20))]
 assert len(points)>=15,('UPPER_BOTANICAL_ROOTS_UNDER15',len(points))
 for j,(x,y) in enumerate(points[:25]):fern_plant(B,f'Tropical19_F2_Fern{j:02}',x,y,H+.26,500+j,2,uppergarden,1.45);floor_counts[2]+=1
 (B.out/'TROPICAL19_AUTHORING.json').write_text(json.dumps({'status':'AUTHORED_REQUIRES_EXPORTED_CHECKS_AND_ACTUAL_IMAGES','actual_plants_per_floor':dict(floor_counts),'actual_tall_plants':[{'name':q['name'],'source_key':q['source_key'],'actual_height_m':q['actual_height_m']} for q in tall],'ground_bed_area_m2':garden.area,'upper_bed_area_m2':uppergarden.area,'nominal_aisle_width_m':2.0,'source_use_counts':dict(used),'collision_rule':'离地2.0m以上的树叶只有可视、没有碰撞；树干/花池/花盆及低处叶面精确碰撞，低处伸出床进入通道的原生叶片已剪去。实际导出和C2仍必须验证。','scope':'Large connected ground beds and peripheral upper beds; original understory plus actual Infinigen tropical factories and licensed CC0 plants. No internal office partitions. All photos require actual plant coverage>half review.'},ensure_ascii=False,indent=2))
 return {'actual_plants_per_floor':dict(floor_counts),'theme':'Layered dense tropical planting and actual tall factory palms/bananas, original understory and CC0 botany','collision':'Actual trunks/low plants and soil/curbs exact; high leaves only visual, disclosed.'}
