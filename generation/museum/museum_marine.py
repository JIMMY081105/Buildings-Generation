"""Marine galleries with licensed specimens, mounted bronzes and lit aquaria."""
import math,hashlib,collections
import numpy as np
from shapely.geometry import Point
from museum_exhibits import sources,place

def fish(B,name,center,length,kind,index,material):
 x,y,z=center;v=[];f=[];rings=24;sides=20
 for i in range(rings+1):
  t=i/rings;radius=max(.008,math.sin(math.pi*t)**.68)*length*((.13+.018*(index%5)) if kind!='ray' else .33)
  for j in range(sides):
   a=j/sides*math.tau;v.append((x+(t-.5)*length,y+radius*math.cos(a)*(1 if kind!='ray' else 1.8),z+radius*math.sin(a)*(.8 if kind!='ray' else .25)))
 for i in range(rings):
  for j in range(sides):f.append((i*sides+j,i*sides+(j+1)%sides,(i+1)*sides+(j+1)%sides,(i+1)*sides+j))
 f.extend([tuple(reversed(range(sides))),tuple(rings*sides+j for j in range(sides))]);body=B.mesh(name+'_Body',v,f,material,False,'marine_specimen')
 for face in body.data.polygons:face.use_smooth=True
 tail=[(x+length*.48,y,z),(x+length*.78,y,z+length*.23),(x+length*.69,y,z),(x+length*.78,y,z-length*.23)];B.mesh(name+'_Tail',tail,[(0,1,2),(0,2,3)],material,False,'marine_specimen')
 for sign in [-1,1]:B.mesh(name+f'_Fin{sign}',[(x-length*.08,y+sign*length*.12,z),(x+length*.25,y+sign*length*.38,z-length*.08),(x+length*.12,y+sign*length*.08,z)],[(0,1,2)],material,False,'marine_specimen')
 B.mesh(name+'_Dorsal',[(x-length*.08,y,z+length*.12),(x+length*.10,y,z+length*.37),(x+length*.22,y,z+length*.10)],[(0,1,2)],material,False,'marine_specimen')
 return np.asarray(v)

def jelly(B,name,x,y,z,seed):
 rng=np.random.default_rng(seed);v=[];f=[];rings=10;side=32
 for i in range(rings+1):
  a=i/rings*math.pi/2;r=.34*math.sin(a)
  for j in range(side):
   t=j/side*math.tau;v.append((x+r*math.cos(t),y+r*math.sin(t),z+.32*math.cos(a)))
 for i in range(rings):
  for j in range(side):f.append((i*side+j,i*side+(j+1)%side,(i+1)*side+(j+1)%side,(i+1)*side+j))
 B.mesh(name+'_Bell',v,f,'Jelly',False,'marine_specimen')
 for j in range(12):
  a=j/12*math.tau;phase=rng.uniform(0,math.tau);pts=[(x+.20*math.cos(a)+.04*math.sin(t*9+phase),y+.20*math.sin(a)+.04*math.cos(t*7+phase),z-t*.65) for t in np.linspace(0,1,24)];B.tube(name+f'_Tentacle{j}',pts,.009,'Jelly',False,'marine_specimen')
 B.tube(name+'_Suspension',[(x,y,z+.32),(x,y,z+.7)],.004,'Glass',False,'specimen_mount')

def marine_sources(B):
 pool=list(sources(B).values());specimens=[q for q in pool if q.get('source')=='Smithsonian' and (q.get('unit')=='NMNHFISHES' or q.get('unit')=='NMNHINV' and any(x in str(q.get('title','')).lower() for x in ['corallium','coral','crab','lobster','shell','conus','nautilus','tridacna','cypraea']) or q.get('unit')=='OCIO_DPO3D' and q.get('title')=='Mantis shrimp')];specimens.sort(key=lambda q:(0 if B.spec['building_id']=='M27' and any(x in str(q.get('title','')).lower() for x in ['crab','lobster','shrimp','shell','conus','nautilus']) else 1,str(q['id'])));bronzes={q['id']:q for q in pool if q.get('source')=='Poly Haven' and q['id'] in ['bronze_whale_statue','bronze_shark_statue','bronze_ray_statue']};return specimens,bronzes

def marine(B,model,room):
 s=B.spec;H=s['floor_height_m'];radius=min(room['size'])/2-6.5 if '圆形' in s['plan'] else min(room['size'])/2-4.5
 B.material('MarineBronze',(.12,.23,.19),'stone',1.,.38,.82,source_hint='metal');B.material('FishSilver',(.30,.46,.51),'stone',1.,.24,.65);B.material('Jelly',(.42,.60,.72),'stone',1.,.15,glass=True)
 specimens,bronzes=marine_sources(B);assert len(specimens)>=5,'NEED_FIVE_LICENSED_MARINE_SPECIMENS'
 if s['building_id']=='M26':assert len(bronzes)==3
 count=0;used=collections.Counter()
 # A rear-room wall with a real open portal exercises C2 on every floor.
 from museum_exhibits import internal_gallery_door
 for level in range(s['floors']):
  z=level*H;internal_gallery_door(B,model,room,level)
  if s['building_id']!='M26':
   from museum_themes import display_positions
   positions=display_positions(s,model,level,B);assert len(positions)>=16,'MARINE_DISPLAY_CAPACITY'
  for i in range(16):
   a=(i+.5)/16*math.tau;x,y=(radius*math.cos(a),radius*math.sin(a)) if s['building_id']=='M26' else positions[i];a=math.atan2(y,x);foot=Point(x,y).buffer(1.05,cap_style=3);floor=__import__('shapely').wkt.loads(model['floors'][level]['polygon_wkt']);assert floor.contains(foot),'MARINE_DISPLAY_OUTSIDE_ACTUAL_FLOOR'
   name=f'Marine_F{level+1}_{i:02}';B.box(name+'_Base',(x-1.,y-.80,z),(x+1.,y+.80,z+.86),'Plinth',True,'plinth');B.box(name+'_MetalPlaque',(x-.42,y-.83,z+.62),(x+.42,y-.80,z+.76),'MarineBronze',False,'label')
   if s['building_id']=='M27':
    if i==0 or i in range(5,10):
     from museum_crustaceans import crustacean
     crustacean(B,name,x,y,z+.86,level*16+i,lobster=i%2==0);B.layout[-1]['floor']=level+1;B.layout[-1]['theme_category']='original_crustacean_teaching_model'
    else:
     category='crustacean' if i==0 else 'coral' if i in [2,4,10,12,14] else 'shell';keys=s['exhibition']['theme_source_groups'][category];available=[q for q in specimens if q['source']+':'+str(q['id']) in keys and used[q['source']+':'+str(q['id'])]<3];assert available,('M27_THEME_CATEGORY_CAPACITY',category,level,i)
     available.sort(key=lambda q:(used[q['source']+':'+str(q['id'])],str(q['id'])));row=available[(level+i)%len(available)];key=row['source']+':'+str(row['id']);used[key]+=1;place(B,row,name,x,y,z+.86,(1.55,1.15,1.65),a+math.pi/2);B.layout[-1].update(floor=level+1,theme_category=category)
   elif i<5:
    row=specimens[i];place(B,row,name,x,y,z+.86,(1.55,1.15,1.65),a+math.pi/2);B.layout[-1]['floor']=level+1
   elif i<10:
    geom=fish(B,name,(x,y,z+1.70),1.35,'fish',i,'FishSilver');B.tube(name+'_SpecimenMount',[(x,y,z+.86),(x,y,float(geom[:,2].min())+.005)],.014,'Steel',True,'specimen_mount');B.layout.append({'name':name,'kind':'exhibit','floor':level+1,'source_key':f'procedural:{s["building_id"]}:fish:{i}','support':'visible steel museum-specimen mount touching actual body underside','body_width_ratio':.13+.018*(i%5),'theme':'marine fish'})
   elif i<13:
    jelly(B,name,x,y,z+2.,s['seed']+level*20+i);B.layout.append({'name':name,'kind':'exhibit','floor':level+1,'source_key':f'procedural:{s["building_id"]}:jelly:{level}:{i}','support':'clear suspension to case roof','theme':'jellyfish'})
   else:
    kind=['whale','shark','ray'][i-13];row=bronzes['bronze_'+kind+'_statue'];place(B,row,name,x,y,z+.86,(1.55,1.15,1.65),a+math.pi/2);B.layout[-1]['floor']=level+1;B.layout[-1]['theme']=kind+' bronze'
   for j,(lo,hi) in enumerate([((x-1.,y-.80,z+.86),(x-.985,y+.80,z+2.85)),((x+.985,y-.80,z+.86),(x+1.,y+.80,z+2.85)),((x-.985,y-.80,z+.86),(x+.985,y-.785,z+2.85)),((x-.985,y+.785,z+.86),(x+.985,y+.80,z+2.85)),((x-1.,y-.80,z+2.835),(x+1.,y+.80,z+2.85))]):B.box(name+f'_CaseGlass{j}',lo,hi,'Glass',True,'display_case')
   B.box(name+'_CaseRoof',(x-1.01,y-.81,z+2.85),(x+1.01,y+.81,z+2.91),'Iron',True,'display_case');B.area(name+'_DisplayLight',(x,y,z+2.79),(x,y,z+1.40),120,.9,(.65,.85,1.),4300);count+=1
 return {'marine_displays':count,'real_CC0_specimen_models':[q['id'] for q in specimens[:5]],'per_floor':16}
