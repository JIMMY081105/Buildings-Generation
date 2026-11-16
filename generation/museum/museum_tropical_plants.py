"""Light original palms/bananas/ferns; shared meshes, no Infinigen factory."""
from pathlib import Path
import math,json,hashlib,collections
import numpy as np,bpy,bmesh
from shapely import wkt
from shapely.geometry import Point,Polygon,box
from shapely.ops import unary_union
from museum_tropical import materials,ground_beds,bed_mesh

def tube_mesh(points,radii,sides=12):
 v=[];f=[]
 for (x,y,z),rad in zip(points,radii):
  for a in np.linspace(0,math.tau,sides,endpoint=False):v.append([x+rad*math.cos(a),y+rad*math.sin(a),z])
 for k in range(len(points)-1):
  for i in range(sides):a=k*sides+i;b=k*sides+(i+1)%sides;f.append([a,b,b+sides,a+sides])
 f.extend([list(reversed(range(sides))),list(range((len(points)-1)*sides,len(points)*sides))]);return v,f

def original_geometry(kind,variant):
 rng=np.random.default_rng(22000+variant+(0 if kind=='palm' else 1000 if kind=='banana' else 2000));leaves=[];faces=[];leaf_uv=[]
 def quad(a,b,c,d,uv=None):
  i=len(leaves);leaves.extend([a,b,c,d]);faces.append([i,i+1,i+2,i+3]);leaf_uv.append(uv or [(0,.49),(0,.51),(1,.51),(1,.49)])
 if kind in ['palm','banana']:
  top=.78 if kind=='palm' else .66;points=[[.035*math.sin(t*1.5+variant),.028*math.sin(t*2.1),top*t] for t in np.linspace(0,1,24)];radii=np.linspace(.020 if kind=='palm' else .035,.012,24);body=tube_mesh(points,radii);root=np.array(points[-1]);n=16+variant%3 if kind=='palm' else 10+variant%2
  for j in range(n):
   a=j*math.tau/n+float(rng.uniform(-.15,.15));d=np.array([math.cos(a),math.sin(a),0]);side=np.array([-d[1],d[0],0]);length=float(rng.uniform(.35,.49));height=float(rng.uniform(.18,.24));ts=np.linspace(0,1,22);spine=[root+d*length*t+np.array([0,0,height*math.sin(t*math.pi*.93)]) for t in ts]
   for k in range(len(ts)-1):
    factor=.050 if kind=='banana' else .004;w0=factor*max(.003,math.sin(math.pi*ts[k])**.7);w1=factor*max(.003,math.sin(math.pi*ts[k+1])**.7);quad(spine[k]-side*w0,spine[k]+side*w0,spine[k+1]+side*w1,spine[k+1]-side*w1,[(ts[k],0),(ts[k],1),(ts[k+1],1),(ts[k+1],0)] if kind=='banana' else None)
   if kind=='palm':
    for t in np.linspace(.10,.96,19):
     center=root+d*length*t+np.array([0,0,height*math.sin(t*math.pi*.93)]);size=.115*math.sin(math.pi*t)**.7
     for sign in [-1,1]:
      tip=center+side*sign*size+d*.055+np.array([0,0,-.018]);quad(center-d*.004,center+d*.004,tip+d*.003,tip-d*.003,[(0,0),(0,1),(1,1),(1,0)])
 elif kind=='broadleaf':
  body=tube_mesh([[0,0,0],[0,0,.16]],[.035,.02],10);root=np.array([0,0,.10])
  for j in range(10+variant%3):
   a=j*math.tau/(10+variant%3)+float(rng.uniform(-.12,.12));d=np.array([math.cos(a),math.sin(a),0]);side=np.array([-d[1],d[0],0]);length=float(rng.uniform(.37,.49));height=float(rng.uniform(.58,.88));ts=np.linspace(0,1,13)
   for aa,bb in zip(ts,ts[1:]):
    ca=root+d*length*aa+np.array([0,0,height*math.sin(math.pi*aa*.72)]);cb=root+d*length*bb+np.array([0,0,height*math.sin(math.pi*bb*.72)]);wa=.115*math.sin(math.pi*aa)**.65;wb=.115*math.sin(math.pi*bb)**.65;quad(ca-side*wa,ca,cb,cb-side*wb,[(aa,0),(aa,.5),(bb,.5),(bb,0)]);quad(ca,ca+side*wa,cb+side*wb,cb,[(aa,.5),(aa,1),(bb,1),(bb,.5)])
 else:
  body=tube_mesh([[0,0,0],[0,0,.16]],[.04,.025],10)
  for j in range(10+variant%5):
   a=j*math.tau/(10+variant%5)+float(rng.uniform(-.14,.14));d=np.array([math.cos(a),math.sin(a),0]);side=np.array([-d[1],d[0],0]);length=float(rng.uniform(.37,.49));height=float(rng.uniform(.58,.90));root=np.array([0,0,.08])
   spine=[root+d*length*t+np.array([0,0,height*math.sin(math.pi*t*.78)]) for t in np.linspace(0,1,20)]
   for aa,bb in zip(spine,spine[1:]):quad(aa-side*.006,aa+side*.006,bb+side*.006,bb-side*.006)
   for t in np.linspace(.12,.96,16):
    c=root+d*length*t+np.array([0,0,height*math.sin(math.pi*t*.78)]);size=.14*math.sin(math.pi*t)**.7
    for sign in [-1,1]:
     axis=side*sign*size+d*.055;perp=np.array([-axis[1],axis[0],0]);perp/=np.linalg.norm(perp);tip=c+axis;i=len(leaves);leaves.extend([c,c+axis*.30+perp*.025,c+axis*.72+perp*.030,tip,c+axis*.72-perp*.030,c+axis*.30-perp*.025]);faces.append([i+k for k in range(6)]);leaf_uv.append([(0,.5),(.3,1),(.72,1),(1,.5),(.72,0),(.3,0)])
 # Normalize one canonical shared object to height1 and horizontal span1.
 bodyv,bodyf=body;allv=np.asarray(bodyv+leaves);zspan=np.ptp(allv[:,2]);span=np.ptp(allv[:,:2],axis=0).max();offset=allv[:,:2].min(0)+np.ptp(allv[:,:2],axis=0)/2
 def norm(v):
  a=np.asarray(v,dtype=float);a[:,:2]=(a[:,:2]-offset)/span;a[:,2]/=zspan;return a.tolist()
 return [(norm(bodyv),bodyf,'BotanyTrunk25',True,'procedural_exhibit',None),(norm(leaves),faces,{'palm':'BotanyPalm25','banana':'BotanyBanana25','broadleaf':'BotanyBroadleaf25','fern':'BotanyFern25'}[kind],kind not in ['palm','banana'],'botanical_leaf',leaf_uv)]

def plant(B,name,kind,variant,x,y,z,height,width,floor,bed,used):
 key=(kind,variant);used[key]+=1;assert used[key]<=3,('MODEL_OVER3',key)
 cache=('light22',*key)
 if cache not in B.templates:
  pieces=[];digest=hashlib.sha256();face_count=0
  from botanical_materials import apply_uv
  for j,(vertices,faces,mat,collision,role,uvs) in enumerate(original_geometry(*key)):
   ob=B.mesh(f'Template22_{kind}{variant}_{j}',vertices,faces,mat,collision,role)
   apply_uv(ob,uvs)
   if role=='botanical_leaf':
    bm=bmesh.new();bm.from_mesh(ob.data);bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=1e-6);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(ob.data);bm.free()
    for polygon in ob.data.polygons:polygon.use_smooth=True
   ob.hide_render=True;ob.hide_viewport=True;pieces.append(ob);digest.update(np.asarray([v.co[:] for v in ob.data.vertices]).tobytes());digest.update(str([list(p.vertices) for p in ob.data.polygons]).encode());face_count+=sum(len(p.vertices)-2 for p in ob.data.polygons)
  assert face_count<=50000;B.templates[cache]=(pieces,digest.hexdigest(),face_count)
 templates,digest,faces=B.templates[cache];objects=[];yaw=(variant*.713+used[key]*1.139)%math.tau
 for j,source in enumerate(templates):
  ob=source.copy();ob.data=source.data;B.scene.collection.objects.link(ob);ob.name=name+('_Trunk' if j==0 else '_Leaves');ob.hide_render=False;ob.hide_viewport=False;ob.location=(x,y,z);ob.rotation_euler=(0,0,yaw);ob.scale=(width,width,height);objects.append(ob)
 bpy.context.view_layer.update();bounds=[B.bounds(o) for o in objects];lo=np.min([q[0] for q in bounds],0);hi=np.max([q[1] for q in bounds],0);low_floor_z=(floor-1)*B.spec['floor_height_m']
 for ob in objects:
  if ob.get('museum_category')=='botanical_leaf' and not ob['museum_collision']:
   verts=np.asarray([ob.matrix_world@v.co for v in ob.data.vertices]);assert verts[:,2].min()>=low_floor_z+2,'VISUAL_ONLY_LEAF_BELOW_LOCAL2M'
  if kind not in ['palm','banana']:
   verts=np.asarray([ob.matrix_world@v.co for v in ob.data.vertices]);assert all(bed.covers(Point(p)) for p in verts[:,:2]),'LOW_LEAF_OUTSIDE_REAL_BED'
 leaflo,leafhi=B.bounds(objects[1]);istall=kind in ['palm','banana']
 B.layout.append({'kind':'exhibit','name':name,'floor':floor,'visible_from_gallery_floors':[1,2] if istall else [floor],'source_key':f'procedural:{B.spec["building_id"]}:light22:{kind}:{variant}','source_model_sha256':digest,'source_asset_title':f'Original lightweight {kind} growth study variant{variant}, not scanned botanical species','theme_category':'original_tall_tropical' if istall else 'original_layered_understory','actual_min_m':lo.tolist(),'actual_max_m':hi.tolist(),'gallery_focus_world_m':((leaflo+leafhi)/2).tolist(),'gallery_foliage_extent_m':(leafhi-leaflo).tolist(),'support_z_m':z,'support_embed_m':0.,'actual_height_m':float(hi[2]-lo[2]),'triangle_count_per_plant':faces,'shared_mesh_data':True,'source_model_use_count_in_building':used[key],'collision':'Exact segmented tapered trunk at all heights; fern and broadleaf understory leaves exact. Tall palm/banana leaves only visual above local ground+2m. Roots and all low leaves contained in actual beds.'})
 key=B.layout[-1]['source_key'];B.licenses.setdefault(key,{'source':'procedural','id':key,'license':'procedural original generated mesh','source_sha256':digest,'title':B.layout[-1]['source_asset_title'],'method':'Original lightweight segmented trunk and leaf mesh, shared instanceable data; no external plant factory.'})
 return B.layout[-1]

def exhibition(B,model,room):
 from museum_glasshouse import select_sources
 from museum_exhibits import place
 materials(B)
 from botanical_materials import materials as alpha_materials25
 alpha_materials25(B);s=B.spec;bid=s['building_id'];H=s['floor_height_m'];w,h=model['building_xy_m'];beds,paths=ground_beds(B,model);garden=unary_union(beds);used=collections.Counter();counts=collections.Counter();roots=[];tall=[]
 if bid=='M28':candidates=[(x,y) for x in [-7.2,-4.8,-2.4,2.4,4.8,7.2] for y in [-7.2,-4.8,-2.4,2.4,4.8,7.2] if garden.covers(Point(x,y).buffer(.7))];target=24
 else:
  ox,oy=model['stair_placements'][0]['origin'];candidates=[(ox+2.*math.cos(a),oy+2.*math.sin(a)) for a in np.linspace(0,math.tau,12,endpoint=False)];target=12
 assert len(candidates)>=target;np.random.default_rng(s['seed']).shuffle(candidates)
 for i,(x,y) in enumerate(candidates[:target]):
  kind='palm' if i%2==0 else 'banana';variant=(i//2)%4;height=[11.8,10.6,9.6,8.4][i%4] if bid=='M28' else [11.4,11.1,10.9,10.7][i%4]
  assert garden.covers(Point(x,y).buffer(.3));tall.append(plant(B,f'Tropical22_F1_Tall{i:02}',kind,variant,x,y,.26,height,8.2 if kind=='palm' else 7.5,1,garden,used));counts[1]+=1;roots.append((x,y,.35))
 # Actual cached CC0 botany; reserve separate root footprints before understory.
 catalog=select_sources(B);pool=[q for q in catalog if q['id']!='jacaranda_tree'];options=[(float(x),float(y)) for x in np.arange(-w/2+3,w/2-2,3.1) for y in np.arange(-h/2+3,h/2-2,3.1) if garden.covers(box(x-1.15,y-1.15,x+1.15,y+1.15))];np.random.default_rng(s['seed']+22).shuffle(options);licensed=[]
 for row in (pool*2)[:16]:
  good=[p for p in options if all(math.hypot(p[0]-x,p[1]-y)>=rad+1.35 for x,y,rad in roots)]
  if not good:break
  x,y=good[0];ob=place(B,row,f'Tropical22_F1_CC0_{len(licensed):02}',x,y,.26,(2.,2.,2.6),len(licensed)*.731);B.layout[-1].update(floor=1,visible_from_gallery_floors=[1],theme_category='cached_CC0_living_botany');licensed.append(B.layout[-1]);roots.append((x,y,1.1));counts[1]+=1
 assert len(licensed)>=4,'CACHED_CC0_BOTANY_UNDER4'
 n=0
 for x in np.arange(-w/2+2.8,w/2-2,1.6):
  for y in np.arange(-h/2+2.8,h/2-2,1.6):
   if n>=180 or not garden.covers(Point(x,y).buffer(1.35)) or any(math.hypot(x-a,y-b)<rad+.50 for a,b,rad in roots):continue
   kind='broadleaf' if n%5<3 else 'fern';variant=min(range(40),key=lambda v:used[(kind,v)]);plant(B,f'Tropical22_F1_Understory{n:03}',kind,variant,float(x),float(y),.26,1.45+(n%4)*.12,2.3,1,garden,used);n+=1;counts[1]+=1
 assert n>=80,('DENSE_UNDERSTORY_UNDER80',n)
 upper=wkt.loads(model['floors'][1]['polygon_wkt']);upperbeds=[]
 if bid=='M28':
  for j in range(12):
   a0=j*math.tau/12+.025;a1=(j+1)*math.tau/12-.025;p=Polygon([(21*math.cos(a),21*math.sin(a)) for a in np.linspace(a0,a1,22)]+[(19.5*math.cos(a),19.5*math.sin(a)) for a in np.linspace(a1,a0,22)]).intersection(upper.buffer(-.12))
   if p.area>3:upperbeds.append(bed_mesh(B,f'Tropical22_UpperRingBed{j}',p,H,2))
 else:
  shape=wkt.loads(model['footprint_wkt']);p=shape.buffer(-1.2).difference(shape.buffer(-3.1)).intersection(upper);pieces=list(p.geoms) if hasattr(p,'geoms') else [p]
  for j,piece in enumerate(pieces):
   if piece.area>5:upperbeds.append(bed_mesh(B,f'Tropical22_UpperLongBed{j}',piece,H,2))
 uppergarden=unary_union(upperbeds)
 points=[(20.25*math.cos(a),20.25*math.sin(a)) for a in np.linspace(0,math.tau,32,endpoint=False)] if bid=='M28' else [(float(x),float(y)) for x in np.arange(-w/2+2.15,w/2-2,1.2) for y in np.arange(-h/2+2.15,h/2-2,1.2)]
 points=[p for p in points if uppergarden.covers(Point(*p).buffer(.50))]
 if bid=='M29':np.random.default_rng(s['seed']+4102).shuffle(points)
 assert len(points)>=15,('UPPER_SAFE_ROOTS_UNDER15',len(points))
 for j,(x,y) in enumerate(points[:25]):plant(B,f'Tropical22_F2_Fern{j:02}','fern',min(range(40),key=lambda v:used[('fern',v)]),x,y,H+.26,1.25+(j%3)*.1,.88,2,uppergarden,used);counts[2]+=1
 doc={'status':'AUTHORED_REQUIRES_EXPORTED_CHECKS_AND_ACTUAL_IMAGES','authorization':'ADJUST_19_1300.md+ADJUST_22_1355.md','actual_plants_per_floor':dict(counts),'actual_tall_plants':tall,'ground_bed_area_m2':garden.area,'upper_bed_area_m2':uppergarden.area,'original_source_use_counts':{str(k):v for k,v in used.items()},'cached_CC0_plants':len(licensed),'infinigen_factory_invocations':0,'maximum_triangles_per_original_plant':max(q['triangle_count_per_plant'] for q in B.layout if q.get('triangle_count_per_plant')),'collision_rule':'离相应楼面2m以上的棕榈/香蕉叶只有可视；分段锥柱树干全高精确碰撞，蕨类所有低叶、花池和花盆精确碰撞。所有低叶严格包含在实际花坛内。','scope':'Original lightweight growth studies and actual cached CC0 models, shared mesh instances, no Infinigen plant/tree factories. Source names honest, not scanned species. Actual C2/C3/C4/C5 and >half-plant gallery images still mandatory.'};(B.out/'TROPICAL22_AUTHORING.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2));return doc

def connection_view(model):
 from stair_junction_camera import fit
 q=fit(model_override=model);return q['eye'],q['target'],q['lens']
