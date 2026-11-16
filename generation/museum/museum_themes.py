"""Curated licensed object galleries, with original pottery and live plant beds."""
import math,hashlib,collections
import numpy as np
from shapely import wkt
from shapely.geometry import Point,box
from museum_exhibits import sources,place,internal_gallery_door

WORDS={
 'M02':['statue','_head','marble_bust'],
 'M07':['gothic','medieval','shield','mace','hammer','estoc','chest','candlestick','candleholders','goblets','katana','mirror','lantern'],
 'M08':['gothic','statue','chest','candlestick','candleholders','lantern','goblets','carved','shield','mace','hammer','estoc'],
 'M09':['ship','crane','vice','drill','tool','wrench','screwdriver','hammer','multimeter','microscope','projector','camera','instrument','cart'],
 'M11':['microscope','multimeter','projector','camera','spacecraft','instrument','vice','drill','tool','wrench','screwdriver','pocket_watch','clock'],
 'M15':['statue','vase','ceramic','jug','bowl','goblet','carved','_head'],
 'M16':['statue','vase','ceramic','jug','bowl','carved','_head'],
 'M17':['vase','clock','watch','goblet','chess','jug','candleholders','lantern','tea_set'],
 'M19':['ceramic','vase','jug','bowl','tea_set','plate'],
 'M20':['chair','table','sofa','ottoman','bench','stool'],
 'M21':['wooden_bowl','wooden_plate','wooden_elephant','spinning_wheel','lamp','lantern','chandelier','clock','vase','tea_set','goblet','jug','kettle'],
 'M22':['chinese','tea_set','ceramic','vase','katana','wooden_plate','wooden_bowl'],
 'M23':['tea_set','ceramic','vase','jug','bowl','kettle','plate'],
 'M24':['ceramic','vase','jug','bowl','goblet','wooden_plate','barrel','tea_set','candleholders','spinning_wheel'],
 'M25':['dutch_ship','ship_pinnace','sea_marker','buoy','life_jacket','compass','binocular','barrel','ceramic','vase','jug'],
 'M28':['plant','anthurium','calathea','palm','fern'], 'M29':['plant','anthurium','calathea','palm','fern']}

def select_sources(B):
 bid=B.spec['building_id'];pool=list(sources(B).values());rows=[]
 groups=B.spec['exhibition'].get('theme_source_groups')
 if groups:
  wanted={k for values in groups.values() for k in values};rows=[q for q in pool if q['source']+':'+str(q['id']) in wanted]
  assert len(rows)==len(wanted),'CATEGORY_BOUND_MODEL_MISSING';return rows
 for q in pool:
  identifier=str(q['id']).lower();title=str(q.get('title','')).lower();unit=q.get('unit');ph=q.get('source')=='Poly Haven';si=q.get('source')=='Smithsonian';ok=ph and any(x in identifier for x in WORDS.get(bid,[]))
  if bid in ['M04','M05','M06']:
   ok=si and unit in (['NMNHPALEO','NMNHMAMMALS','NMNHHERPS','NMNHEDUCATION'] if bid in ['M04','M06'] else ['NMNHENTO','NMNHINV','NMNHMAMMALS','NMNHPALEO','NMNHHERPS'])
   if bid=='M05' and si and title=='cher ami':ok=True
  if bid=='M02' and si and unit=='SAAM':ok=True
  if bid in ['M09','M11'] and si and (unit=='NASM' or unit=='NMAH' and any(x in title for x in ['battery','coil','patent model','instrument','desk']) or title=='chandra x-ray observatory'):ok=True
  if bid in ['M15','M16'] and si and unit in ['SAAM','NMAA','CHNDM']:ok=True
  if bid in ['M17','M19','M21','M23','M24'] and si and unit=='CHNDM' and any(x in title for x in ['ewer','kylix']):ok=True
  if bid in ['M20','M21'] and si and unit=='CHNDM' and 'chair' in title:ok=True
  if bid=='M22' and si and unit=='NMAA':ok=True
  if bid in ['M28','M29'] and si and unit in ['NMNHBOTANY','OFEO-SG']:ok=True
  if ok:rows.append(q)
 # Identical bytes under different catalogue IDs do not add reuse capacity.
 rows.sort(key=lambda q:(0 if any(x in str(q.get('title','')).lower() for x in ['deer','komodo','cranium','hoplophoneus','bell x-1','battery','coil']) else 1,str(q['id'])));seen=set();unique=[]
 for row in rows:
  digest=hashlib.sha256(__import__('pathlib').Path(row['path']).read_bytes()).hexdigest()
  if digest not in seen:seen.add(digest);unique.append(row)
 return unique

def pottery(B,name,x,y,z,index):
 rng=np.random.default_rng(B.spec['seed']*100+index);height=float(rng.uniform(.55,1.05));belly=float(rng.uniform(.25,.41));neck=float(rng.uniform(.10,.19));profile=[(.17,0),(.19,.04),(belly*.85,height*.16),(belly,height*.44),(belly*.72,height*.72),(neck,height*.88),(neck*1.10,height)];profile+= [(r-.025,zz) for r,zz in reversed(profile[1:])]+[(.14,.055)];segments=64;v=[];f=[]
 for radius,zz in profile:
  for j in range(segments):
   a=j/segments*math.tau;v.append((x+radius*math.cos(a),y+radius*math.sin(a),z+zz-.002))
 for k in range(len(profile)-1):
  for j in range(segments):jj=(j+1)%segments;f.append((k*segments+j,k*segments+jj,(k+1)*segments+jj,(k+1)*segments+j))
 f.append(tuple(reversed(range(segments))));f.append(tuple((len(profile)-1)*segments+j for j in range(segments)));glaze='OriginalVesselGlaze_'+str(index%3);B.material(glaze,[(.16,.27,.23),(.56,.43,.27),(.67,.65,.56)][index%3],'stone',1.2,.24);ob=B.mesh(name,v,f,glaze,True,'procedural_exhibit')
 for face in ob.data.polygons:face.use_smooth=True
 B.layout.append({'name':name,'kind':'exhibit','source_key':f'procedural:{B.spec["building_id"]}:original_pottery:{index}','theme':'original wheel-thrown vessel study','height_m':height,'belly_radius_m':belly,'neck_radius_m':neck,'support_z_m':z,'support_embed_m':.002,'geometry_sha256':hashlib.sha256(np.asarray(v).tobytes()).hexdigest()})

def plant(B,name,x,y,z,index):
 rng=np.random.default_rng(B.spec['seed']*100+index);height=float(rng.uniform(1.8,3.4));B.material('PlantLeaves',(.045,.16,.055),'stone',1.2,.65);B.material('PlantTrunk',(.16,.095,.04),'wood',1.,.9);B.cylinder(name+'_Trunk',(x,y,z-.01),.075,height,'PlantTrunk',True,.045)
 for j in range(12):
  a=j/12*math.tau+rng.uniform(-.12,.12);start=np.array([x,y,z+height]);d=np.array([math.cos(a),math.sin(a),0]);lat=np.array([-d[1],d[0],0]);length=float(rng.uniform(1.,1.8));points=[];faces=[]
  for k,t in enumerate(np.linspace(0,1,16)):
   center=start+d*length*t+[0,0,.30*math.sin(math.pi*t)-.45*t*t];width=.18*math.sin(math.pi*t)
   points.extend([center-lat*width,center+lat*width])
   if k:faces.append((2*k-2,2*k-1,2*k+1,2*k))
  B.mesh(name+f'_Frond{j}',points,faces,'PlantLeaves',False,'botanical_leaf')
 B.layout.append({'name':name,'kind':'exhibit','source_key':f'procedural:{B.spec["building_id"]}:palm_study:{index}','theme':'original palm growth study','trunk_height_m':height,'support_z_m':z,'support_embed_m':.01})

def display_positions(s,model,level,B=None):
 w,h=model['building_xy_m'];floor=wkt.loads(model['floors'][level]['polygon_wkt']);positions=[]
 blocked=[]
 if B is not None:
  import bpy
  from mathutils import Vector
  bpy.context.view_layer.update();z=level*s['floor_height_m']
  for ob in B.scene.objects:
   if ob.type!='MESH' or not ob.get('museum_collision'):continue
   stair=ob.name.startswith(('Stair_','GalleryOpeningGuard_'))
   if not (stair or ob.get('museum_category') in ['wall','stair_wall','gallery_wall','exhibition_wall'] or any(k in ob.name for k in ['Pillar_','_Column','TowerWall'])):continue
   pts=np.array([ob.matrix_world@Vector(p) for p in ob.bound_box]);lo,hi=pts.min(0),pts.max(0)
   if hi[2]>=z-1e-5 and lo[2]<=z+3.10:
    margin=1.2 if stair else .03;blocked.append(box(lo[0]-margin,lo[1]-margin,hi[0]+margin,hi[1]+margin))
 half=1.30 if s.get('east_asian13') else 1.05
 grids=[(4.,0.),(4.,2.),(4.,1.)] if s.get('east_asian13') else [(4.,0.),(3.6,0.),(3.6,1.8)]
 for pitch,phase in grids:
  candidate=[]
  for x in np.arange(-w/2+3.5+phase,w/2-2.5,pitch):
   for y in np.arange(-h/2+3.5+phase,h/2-2.5,pitch):
    if abs(x)<w*.24 and abs(y)<h*.24:continue
    if abs(y-h*.40)<1.7 or abs(x)<1.7:continue
    footprint=box(x-half,y-half,x+half,y+half)
    if floor.contains(footprint) and not any(footprint.intersects(q) for q in blocked):candidate.append((float(x),float(y)))
  if len(candidate)>len(positions):positions=candidate
  if len(positions)>=max(15,s.get('exhibition',{}).get('min_exhibits_per_floor',15)):break
 rng=np.random.default_rng(s['seed']+level);rng.shuffle(positions);return positions

def objects(B,model,room):
 s=B.spec;bid=s['building_id'];H=s['floor_height_m'];w,h=room['size'];pool=select_sources(B);used=collections.Counter();plants=s['style_family']=='glasshouse';allow_pottery=bid in ['M15','M16','M17','M19','M21','M23','M24'];count=0
 original_quartz_slots=3*s['floors'] if bid in ['M05','M20'] else 0
 assert len(pool)*3+original_quartz_slots>=15*s['floors'] or plants or allow_pottery,('INSUFFICIENT_CURATED_THEME_MODELS',bid,len(pool),original_quartz_slots)
 for level in range(s['floors']):
  z=level*H;internal_gallery_door(B,model,room,level);positions=display_positions(s,model,level,B);assert len(positions)>=15,('INSUFFICIENT_SAFE_DISPLAY_FOOTPRINTS',bid,level,len(positions));groups=s['exhibition'].get('theme_source_groups',{});lookup={q['source']+':'+str(q['id']):q for q in pool}
  if bid in ['M04','M06'] and groups and level==0:
   floor=wkt.loads(model['floors'][level]['polygon_wkt']);large=(0.,5.8) if bid=='M04' else next((p for p in positions if floor.contains(box(p[0]-2.4,p[1]-1.45,p[0]+2.4,p[1]+1.45))),None);assert large and floor.contains(box(large[0]-2.4,large[1]-1.45,large[0]+2.4,large[1]+1.45)),'LARGE_SKELETON_SUPPORTED_FOOTPRINT'
   if large in positions:positions.remove(large)
   positions.insert(0,large)
  for i,(x,y) in enumerate(positions[:15]):
   name=f'Theme_F{level+1}_{i:02}';large_animal=bid in ['M04','M06'] and bool(groups) and i==0 and level==0;plinth=.18 if bid=='M20' else .22 if large_animal else .35 if plants else .82;half_x,half_y=(2.35,1.40) if large_animal else (.85,.8);B.box(name+'_Plinth',(x-half_x,y-half_y,z),(x+half_x,y+half_y,z+plinth),'Plinth',True,'plinth');B.box(name+'_Cap',(x-half_x-.02,y-half_y-.02,z+plinth-.025),(x+half_x+.02,y+half_y+.02,z+plinth+.035),'Plinth',True,'plinth');available=[q for q in pool if used[q['source']+':'+str(q['id'])]<3]
   category=None
   if groups:
    if bid=='M04':category=['large_animal' if level==0 else 'bone','bone','skull','geology','bone','skull','geology','skull','bone','skull','geology','bone','skull','skull','skull'][i]
    elif bid=='M06':category='large_animal' if large_animal else 'bone' if i%4 else 'geology'
    elif bid=='M05':category=['bird','insect','shell','shell','geology'][i%5] if i<5 else ['insect','shell','geology'][i%3]
    elif bid=='M07':category=['religious','weapon','shield','medieval','medieval'][i%5] if i<5 else ['weapon','medieval','shield','medieval'][i%4]
    elif bid=='M08':category='chest' if i==1 else 'sculpture' if i%5 in [0,3] else 'candle' if i%5==2 else 'artifact'
    elif bid in ['M15','M16']:category='sculpture' if i%4==0 else 'ceramic'
    elif bid=='M17':category='clock' if i==0 else 'vessel'
    elif bid=='M19':category='lamp' if i in [0,10] else 'ceramic'
    elif bid=='M20':category=['seating','table','seating','stool','table','seating','casework','table','seating','lighting','table','seating','stool','table','seating'][i]
    elif bid=='M21':category='lamp' if i in [0,10] else 'craft'
    elif bid=='M23':category='tea' if i==0 else 'ceramic'
    elif bid in ['M09','M11']:category='ship' if bid=='M09' and i%5==1 else 'science' if i%3 else 'machine'
    elif bid=='M22':category=['screen','furniture','ceramic','blade','art'][i%5] if i<5 else ['furniture','ceramic','ceramic','blade','art'][i%5]
    elif bid=='M24':category='bronze' if i%3==0 else 'pottery'
    elif bid=='M25':category='compass' if i==1 else 'ship' if i%5==0 else 'navigation' if i%5==2 else 'pottery'
    elif plants:category='tree' if i==0 else 'plant'
    keys=groups.get(category,[])
    if bid=='M23' and category=='tea':keys=[k for k in keys if 'tea_set_01' in k]
    selected=[lookup[k] for k in keys if k in lookup and used[k]<3];available=selected if bid in ['M15','M16','M17','M19','M21','M23'] and category in ['vessel','ceramic','craft'] else selected or available
   # Each floor cycles through distinct actual source models before reuse.
   if bid in ['M09','M11'] and i in [0,5,10]:
    from museum_originals import engine
    engine(B,name,x,y,z+plinth+.035,level*15+i);B.layout[-1]['floor']=level+1
   elif bid=='M20' and i in [2,11,14]:
    from museum_design_studies import furniture
    furniture(B,name,{2:'slatted_bench',11:'bentwood_armchair',14:'linen_settee'}[i],x,y,z+plinth+.035,math.atan2(-y,-x))
   elif bid=='M05' and i in [4,9,14]:
    from museum_originals import quartz
    quartz(B,name,x,y,z+plinth+.035,level*15+i);B.layout[-1]['floor']=level+1
   elif plants and i in [5,10]:plant(B,name,x,y,z+plinth+.035,level*15+i);B.layout[-1]['floor']=level+1
   elif available:
    display_size=(4.5,2.4,3.6) if large_animal else (1.6,1.45,3.6 if plants else 2.05)
    if bid=='M20':
     row=min(available,key=lambda q:(used[q['source']+':'+str(q['id'])],str(q['id'])));available=[row];identifier=str(row['id']).lower();display_size=(1.8,.95,1.0) if 'sofa' in identifier else (.85,.75,.45) if 'ottoman' in identifier else (1.2,.72,.47) if 'coffee' in identifier else (1.35,.55,1.5) if 'cabinet' in identifier else (.48,.48,.67) if 'lamp' in identifier else (.52,.52,.70) if 'stool' in identifier else (.95,.95,.76) if 'table' in identifier else (.9,.95,1.15)
    row=available[(level*15+i)%len(available)];key=row['source']+':'+str(row['id']);used[key]+=1;ob=place(B,row,name,x,y,z+plinth+.035,display_size,math.atan2(-y,-x));B.layout[-1]['floor']=level+1;B.layout[-1]['theme_category']=category
   elif plants:plant(B,name,x,y,z+plinth+.035,level*15+i);B.layout[-1]['floor']=level+1
   else:assert allow_pottery;pottery(B,name,x,y,z+plinth+.035,level*15+i);B.layout[-1]['floor']=level+1
   if s['style_family']=='victorian_natural' and not large_animal:
    for j,(lo,hi) in enumerate([((x-.86,y-.81,z+plinth),(x-.845,y+.81,z+2.98)),((x+.845,y-.81,z+plinth),(x+.86,y+.81,z+2.98)),((x-.845,y-.81,z+plinth),(x+.845,y-.795,z+2.98)),((x-.845,y+.795,z+plinth),(x+.845,y+.81,z+2.98))]):B.box(name+f'_CaseGlass{j}',lo,hi,'Glass',True,'display_case')
    B.box(name+'_CaseRoof',(x-.88,y-.83,z+2.98),(x+.88,y+.83,z+3.02),'Wood',True,'display_case');B.area(name+'_CaseLight',(x,y,z+2.94),(x,y,z+1.5),70,.7,(1,.94,.85))
    B.layout[-1]['display_envelope_m']=[[x-.88,y-.83,z+plinth],[x+.88,y+.83,z+3.02]]
   count+=1
 return {'theme':s['exhibition']['theme'],'curated_source_models':len(pool),'object_exhibits':count,'source_counts':dict(used),'minimum_per_floor':15,'placement':'actual per-floor polygon, 2.1m support footprint, 4m display pitch; open portal approaches excluded'}
