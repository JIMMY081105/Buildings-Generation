"""Actual stone-edged soil beds and source-bound botanical collections."""
import math,collections,hashlib
import numpy as np
import bpy
from mathutils import Vector
from shapely import wkt
from shapely.geometry import Point,box
from museum_exhibits import place,internal_gallery_door
from museum_themes import select_sources,plant

def feather_palm(B,name,x,y,z,index):
 rng=np.random.default_rng(B.spec['seed']*100+index);height=float(rng.uniform(2.7,3.6));B.cylinder(name+'_Trunk',(x,y,z-.01),.075,height,'BotanyTrunk14',True,.045);vertices=[];faces=[]
 for j in range(15):
  angle=math.tau*j/15+float(rng.uniform(-.12,.12));d=np.array([math.cos(angle),math.sin(angle),0]);side=np.array([-d[1],d[0],0]);length=float(rng.uniform(1.15,1.65));origin=np.array([x,y,z+height]);spine=[]
  for t in np.linspace(0,1,22):spine.append(origin+d*length*t+np.array([0,0,.34*math.sin(math.pi*t)-.62*t*t]))
  B.tube(name+f'_LeafRachis{j}',spine,.012,'BotanyLeaf14',False,'botanical_leaf')
  for t in np.linspace(.12,.96,19):
   c=origin+d*length*t+np.array([0,0,.34*math.sin(math.pi*t)-.62*t*t]);leaf=.35*math.sin(math.pi*t)**.55
   for sign in [-1,1]:
    tip=c+sign*side*leaf+d*.13+np.array([0,0,-.11]);k=len(vertices);vertices.extend([c-d*.015,c+d*.015,tip+d*.010,tip-d*.010]);faces.append((k,k+1,k+2,k+3))
 B.mesh(name+'_FeatherLeaflets',vertices,faces,'BotanyLeaf14',False,'botanical_leaf');v=np.asarray(vertices);lo=np.minimum(v.min(0),[x-.075,y-.075,z-.01]);hi=v.max(0);B.layout.append({'name':name,'kind':'exhibit','source_key':f'procedural:{B.spec["building_id"]}:feather_palm_growth_study:{index}','theme':'Original feather-palm growth study with pinnate leaflets, explicitly authored not a scan','actual_min_m':lo.tolist(),'actual_max_m':hi.tolist(),'support_z_m':z,'support_embed_m':.01,'geometry_sha256':hashlib.sha256(v.tobytes()).hexdigest()})

def positions(B,model,level):
 H=B.spec['floor_height_m'];floor=wkt.loads(model['floors'][level]['polygon_wkt']);w,h=model['building_xy_m'];z=level*H;blocked=[]
 bpy.context.view_layer.update()
 for ob in B.scene.objects:
  if ob.type!='MESH' or not ob.get('museum_collision'):continue
  role=ob.get('museum_category');stair=ob.name.startswith(('Stair_','GalleryOpeningGuard_'))
  if not (stair or role in ['wall','stair_wall','gallery_wall','exhibition_wall','column'] or any(k in ob.name for k in ['Pillar_','_Column'])):continue
  a=np.array([ob.matrix_world@Vector(p) for p in ob.bound_box]);lo,hi=a.min(0),a.max(0)
  if hi[2]>=z-.01 and lo[2]<=z+H-.5:
   margin=1.3 if stair else .12;blocked.append(box(lo[0]-margin,lo[1]-margin,hi[0]+margin,hi[1]+margin))
 options=[]
 for pitch in [4.7,4.5]:
  for phase in [0.,pitch/2]:
   out=[]
   for x in np.arange(-w/2+2.4+phase,w/2-2.,pitch):
    for y in np.arange(-h/2+2.4+phase,h/2-2.,pitch):
     if abs(x)<2.0 or abs(y-h*.40)<1.6:continue
     foot=box(x-1.65,y-1.55,x+1.65,y+1.55)
     if floor.contains(foot) and not any(foot.intersects(q) for q in blocked):out.append((float(x),float(y)))
   options.append(out)
 out=max(options,key=len);assert len(out)>=15,('GLASSHOUSE_15_SAFE_BEDS',len(out));np.random.default_rng(B.spec['seed']+level).shuffle(out);return out[:15]

def fern(B,name,x,y,z,index):
 rng=np.random.default_rng(B.spec['seed']*100+index);vertices=[];faces=[];B.cylinder(name+'_Rhizome',(x,y,z-.005),.12,.24,'BotanyTrunk14',True,.16)
 for j in range(11):
  a=math.tau*j/11+float(rng.uniform(-.1,.1));direction=np.array([math.cos(a),math.sin(a),0]);lateral=np.array([-direction[1],direction[0],0]);length=float(rng.uniform(.9,1.25));height=float(rng.uniform(.75,1.15));spine=[]
  for t in np.linspace(0,1,20):spine.append(np.array([x,y,z+.10])+direction*length*t+np.array([0,0,height*math.sin(math.pi*t*.78)]))
  B.tube(name+f'_FrondRachis{j}',spine,.009,'BotanyLeaf14',False,'botanical_leaf')
  for k,t in enumerate(np.linspace(.12,.94,13)):
   center=np.array([x,y,z+.10])+direction*length*t+np.array([0,0,height*math.sin(math.pi*t*.78)]);size=.25*math.sin(math.pi*t)**.65
   for sign in [-1,1]:
    tip=center+sign*lateral*size+direction*.14;v=len(vertices);vertices.extend([center-direction*.035,center+direction*.035,tip+direction*.032,tip-direction*.032]);faces.append((v,v+1,v+2,v+3))
 ob=B.mesh(name+'_Pinnae',vertices,faces,'BotanyLeaf14',False,'botanical_leaf');v=np.asarray(vertices);lo=np.minimum(v.min(0),[x-.16,y-.16,z-.005]);hi=np.maximum(v.max(0),[x+.16,y+.16,z+.24])
 B.layout.append({'name':name,'kind':'exhibit','source_key':f'procedural:{B.spec["building_id"]}:fern_growth_study:{index}','theme':'Original pinnate fern growth study, authored geometry, not a scanned specimen','actual_min_m':lo.tolist(),'actual_max_m':hi.tolist(),'support_z_m':z,'support_embed_m':.005,'geometry_sha256':hashlib.sha256(v.tobytes()).hexdigest()})

def exhibition(B,model,room):
 s=B.spec;H=s['floor_height_m'];pool=select_sources(B);lookup={q['source']+':'+str(q['id']):q for q in pool};groups=s['exhibition']['theme_source_groups'];tree=groups['tree'];others=[k for k in lookup if k not in tree];used=collections.Counter();count=0
 assert len(others)*3>=24 and tree,'BOTANICAL_SOURCE_CAPACITY'
 B.material('BotanyStoneEdge14',(.28,.30,.23),'ashlar',1.2,.91,source_hint='limestone|stone_floor');B.material('BotanySoil14',(.075,.044,.021),'stone',.32,.99);B.material('BotanyLeaf14',(.035,.15,.045),'stone',.6,.75);B.material('BotanyTrunk14',(.15,.085,.035),'wood',.5,.92)
 for level in range(s['floors']):
  z=level*H;internal_gallery_door(B,model,room,level)
  for i,(x,y) in enumerate(positions(B,model,level)):
   name=f'Botany14_F{level+1}_{i:02}';edge=.32;soil=z+.235
   if i==5:
    feather_palm(B,name,x,y,soil,level*15+i);row=B.layout[-1];row['floor']=level+1;row['theme_category']='original_palm'
   elif i==10:fern(B,name,x,y,soil,level*15+i);B.layout[-1].update(floor=level+1,theme_category='original_fern')
   else:
    keys=tree if i==0 else others;available=[k for k in keys if used[k]<3];assert available,'BOTANICAL_SOURCE_EXHAUSTED';key=min(available,key=lambda k:(used[k],k));used[key]+=1
    identifier=key.split(':')[-1];maxbox=(2.6,2.4,H-.85) if i==0 else (2.4,2.2,1.4) if identifier=='fern_02' else (2.5,2.3,2.7) if identifier=='shrub_04' else (1.0,.9,.7) if 'iceplant' in identifier else (1.8,1.6,.8) if 'succulent' in identifier else (.95,.90,1.65) if 'potted' in identifier else (1.7,1.6,1.6);ob=place(B,lookup[key],name,x,y,soil,maxbox,math.atan2(-y,-x));B.layout[-1].update(floor=level+1,theme_category='tree' if i==0 else 'licensed_living_plant',botanical_source_scope='Official CC0 living plant model; original source title retained.')
   row=B.layout[-1];extent=np.array(row['actual_max_m'])-row['actual_min_m'];hx,hy=np.minimum([1.55,1.45],np.maximum([.60,.55],extent[:2]/2+.20));hx,hy=(.90,.85) if i in [0,5] else (float(hx),float(hy));innerx=hx-.16;innery=hy-.16
   B.box(name+'_Soil',(x-innerx,y-innery,z+.01),(x+innerx,y+innery,soil),'BotanySoil14',True,'planting_soil')
   for j,(lo,hi) in enumerate([((x-hx,y-hy,z),(x-innerx,y+hy,z+edge)),((x+innerx,y-hy,z),(x+hx,y+hy,z+edge)),((x-innerx,y-hy,z),(x+innerx,y-innery,z+edge)),((x-innerx,y+innery,z),(x+innerx,y+hy,z+edge))]):B.box(name+f'_StoneEdge{j}',lo,hi,'BotanyStoneEdge14',True,'planting_bed_edge')
   B.layout.append({'name':name+'_Bed','kind':'planting_bed','floor':level+1,'outer_bounds_m':[[x-hx,y-hy,z],[x+hx,y+hy,z+edge]],'soil_surface_z_m':soil,'support':'Actual opaque soil solid inside four separate stone curb meshes; bed footprint fitted to plant and root display, no white exhibition plinth.'});count+=1
 return {'theme':s['exhibition']['theme'],'object_exhibits':count,'minimum_per_floor':15,'source_counts':dict(used),'display':'Actual low stone-edged soil planting beds, palms/ferns explicitly original and licensed botanical specimens source-bound; actual preview review and fresh physics still mandatory.'}
