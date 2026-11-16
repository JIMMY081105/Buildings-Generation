"""ADJUST11 factory contemporary/aviation displays. Actual density audit required."""
import math,collections,numpy as np,bpy
from mathutils import Vector,Matrix
from shapely import wkt
from shapely.geometry import box,MultiPoint
from museum_industrial_groups import actual_points,adaptive_platform,precision_case
from museum_exhibits import sources,place,internal_gallery_door,abstract_sculpture
from museum_themes import display_positions
from museum_originals import engine
from museum_machines import boiler,gear,propeller

def materials(B):
 for name,color,kind,rough,metal in [('IndustrialDeck',(.07,.09,.10),'stone',.48,.72),('IndustrialTimber',(.22,.12,.055),'long_board',.70,0),('IndustrialConcrete',(.29,.30,.28),'stone',.86,0)]:B.material(name,color,kind,1.2,rough,metal,source_hint='metal' if metal else 'concrete' if kind=='stone' else '')

def scale_about(B,name,pivot,scale):
 bpy.context.view_layer.update();pivot=Vector(pivot);matrix=Matrix.Translation(pivot)@Matrix.Scale(scale,4)@Matrix.Translation(-pivot)
 for ob in B.scene.objects:
  if ob.type=='MESH' and (ob.name==name or ob.name.startswith(name+'_')) and ob.get('museum_category')=='procedural_exhibit':ob.matrix_world=matrix@ob.matrix_world

def relocate_aviation_display(B,name,x,y,floor,opening,occupied,large,preferred,structural):
 """Pack the actual assembled hull before authoring its unchanged safe support."""
 points=actual_points(B,name);hull=MultiPoint(points[:,:2]).convex_hull
 if large:
  foot=next((hull.buffer(m,join_style=2) for m in [.08,.05,.025,.01] if hull.area/hull.buffer(m,join_style=2).area>=.60),None);assert foot is not None
 else:
  extent=np.ptp(points,axis=0);hx=max(.35,extent[0]/2+.12);hy=max(.30,extent[1]/2+.12);foot=box(x-hx-.03,y-hy-.03,x+hx+.03,y+hy+.03)
 from shapely.affinity import translate
 lo=floor.bounds;grid=[(float(a),float(b)) for a in np.arange(lo[0]+2,lo[2]-1.9,1.2) for b in np.arange(lo[1]+2,lo[3]-1.9,1.2)];candidates=list(dict.fromkeys([(x,y),*preferred,*grid]));candidates.sort(key=lambda p:(p[0]-x)**2+(p[1]-y)**2)
 chosen=None
 for xx,yy in candidates:
  moved=translate(foot,xx-x,yy-y)
  if not floor.contains(moved) or moved.intersects(opening) or any(moved.distance(p)<1.2-1e-6 for p in occupied) or any(moved.intersects(p) for p in structural):continue
  chosen=(xx,yy,moved);break
 assert chosen is not None,('M11_NO_ACTUAL_HULL_SAFE_POSITION',name,foot.bounds,len(occupied))
 xx,yy,moved=chosen;dx,dy=xx-x,yy-y
 if abs(dx)+abs(dy)>1e-9:
  delta=Matrix.Translation(Vector((dx,dy,0)))
  for ob in B.scene.objects:
   if ob.type=='MESH' and (ob.name==name or ob.name.startswith(name+'_')) and not ob.hide_render:ob.matrix_world=delta@ob.matrix_world
  bpy.context.view_layer.update()
 row=B.layout[-1];actual=actual_points(B,name);row.update(actual_min_m=actual.min(0).tolist(),actual_max_m=actual.max(0).tolist(),actual_hull_relocation={'old_xy':[x,y],'new_xy':[xx,yy],'minimum_existing_display_clearance_m':min((moved.distance(p) for p in occupied),default=None),'method':'Full actual assembled mesh convex hull/support and unchanged1.2m visitor clearance, structural wall/column avoidance; no threshold relaxation.'})
 return xx,yy

def contemporary(B,model,room):
 # Called after16 actual wall artworks/floor. Those are retained and counted.
 materials(B);s=B.spec;H=s['floor_height_m'];pool=sources(B);press=next(q for q in pool.values() if q['id']=='old_drill_press')
 for level in range(s['floors']):
  z=level*H;floor=wkt.loads(model['floors'][level]['polygon_wkt']);opening=wkt.loads(model['opening_wkt']).buffer(1.2);positions=display_positions(s,model,level,B);positions.sort(key=lambda p:(p[1],p[0]));assert len(positions)>=12,('M10_DENSE_POSITION_CAPACITY',level,len(positions));occupied=[]
  for i,(x,y) in enumerate(positions[:12]):
   name=f'ContemporaryDense_F{level+1}_{i:02}';height=.16;index=level*100+i
   if i<8:abstract_sculpture(B,name,x,y,z+height,index);scale_about(B,name,(x,y,z+height),1.6)
   elif i==8:place(B,press,name,x,y,z+height,(1.65,1.65,2.05),math.atan2(-y,-x))
   elif i==9:boiler(B,name,x,y,z+height,index)
   elif i==10:gear(B,name,x,y,z+height,index)
   else:propeller(B,name,x,y,z+height,index)
   row=B.layout[-1];row.update(floor=level+1,theme_category='contemporary industrial sculpture' if i<8 else 'industrial object or original teaching assembly');foot,record=adaptive_platform(B,name,x,y,z,height,'IndustrialConcrete' if i%3==0 else 'IndustrialDeck',floor,opening,occupied);row.update(record)
 return {'per_floor':28,'wall_art':16,'large_floor_displays':12,'style':'Original abstract sculptures and industrial teaching objects, actual hull-fitted low platforms'}

def instrument(B,name,x,y,z,index):
 """Original laboratory teaching apparatus with varied real geometry."""
 rng=np.random.default_rng(B.spec['seed']+index*331);style=index%3;w=float(rng.uniform(.48,.68));height=float(rng.uniform(.43,.68));B.material('ScienceCopper',(.45,.17,.055),'stone',1.2,.35,.78)
 B.box(name+'_Foot',(x-w/2,y-.19,z),(x+w/2,y+.19,z+.055),'Wood',True,'procedural_exhibit')
 if style==0:
  # Three-axis gyroscope in a real stand.
  for side in [-1,1]:B.tube(name+f'_Stand{side}',[(x+side*w*.40,y,z+.05),(x+side*w*.40,y,z+height*.65)],.025,'Steel',True,'procedural_exhibit')
  center=np.array([x,y,z+height*.65]);radius=height*.39
  for axis in range(3):
   pts=[]
   for a in np.linspace(0,math.tau,65):
    v=np.zeros(3);v[(axis+1)%3]=radius*math.cos(a);v[(axis+2)%3]=radius*math.sin(a);pts.append(center+v)
   B.tube(name+f'_Gimbal{axis}',pts,.014,'Brass' if axis%2 else 'Steel',True,'procedural_exhibit')
  ob=B.cylinder(name+'_Rotor',(0,0,0),radius*.72,.035,'ScienceCopper',True,n=32);ob.location=center;ob.rotation_euler.x=math.pi/2;ob['museum_category']='procedural_exhibit'
 elif style==1:
  # A wound electrical induction coil on paired insulated supports.
  for xx in [x-w*.30,x+w*.30]:B.box(name+'_Insulator'+str(xx),(xx-.03,y-.12,z+.05),(xx+.03,y+.12,z+height*.45),'Plinth',True,'procedural_exhibit')
  radius=height*.23;pts=[(x-w*.35+w*.7*t,y+radius*math.cos(t*math.tau*18),z+height*.49+radius*math.sin(t*math.tau*18)) for t in np.linspace(0,1,361)];B.tube(name+'_Winding',pts,.009,'ScienceCopper',True,'procedural_exhibit')
  B.tube(name+'_IronCore',[(x-w*.42,y,z+height*.49),(x+w*.42,y,z+height*.49)],.045,'Iron',True,'procedural_exhibit')
 else:
  # Laboratory balance; pans hang from visible ties rather than floating.
  B.tube(name+'_CentrePost',[(x,y,z+.05),(x,y,z+height)],.021,'Brass',True,'procedural_exhibit');B.tube(name+'_Beam',[(x-w*.4,y,z+height),(x+w*.4,y,z+height)],.016,'Brass',True,'procedural_exhibit')
  for side in [-1,1]:
   xx=x+side*w*.4;B.tube(name+f'_PanTie{side}',[(xx,y,z+height),(xx,y,z+height*.44)],.006,'Steel',True,'procedural_exhibit');ob=B.cylinder(name+f'_Pan{side}',(xx,y,z+height*.40),w*.15,.025,'ScienceCopper',True);ob['museum_category']='procedural_exhibit'
 B.layout.append({'name':name,'kind':'exhibit','source_key':f'procedural:{B.spec["building_id"]}:laboratory_apparatus:{index}','theme':'Original gyroscope/induction-coil/balance teaching study, no claimed collected-object provenance','support_z_m':z,'actual_parameters':{'variant':style,'width_m':w,'height_m':height}})

def aviation(B,model,room):
 materials(B);s=B.spec;H=s['floor_height_m'];pool=sources(B);used=collections.Counter();science=[q for q in pool.values() if not any(w in str(q['id']).lower() for w in ['drill','generator','cart','ship']) and not any(w in str(q.get('title','')).lower() for w in ['bell x-1','abraham lincoln','desk'])]
 for level in range(s['floors']):
  z=level*H;floor=wkt.loads(model['floors'][level]['polygon_wkt']);opening=wkt.loads(model['opening_wkt']).buffer(1.2);internal_gallery_door(B,model,room,level);positions=display_positions(s,model,level,B);positions.sort(key=lambda p:(p[1],p[0]));assert len(positions)>=28,('M11_DENSE_POSITION_CAPACITY',level,len(positions));occupied=[];structural=[];bpy.context.view_layer.update()
  for ob in B.scene.objects:
   if ob.type!='MESH' or not ob.get('museum_collision') or not (ob.get('museum_category') in ['wall','stair_wall','gallery_wall','exhibition_wall'] or any(k in ob.name for k in ['Pillar_','_Column'])):continue
   vv=np.array([ob.matrix_world@Vector(p) for p in ob.bound_box]);a,b=vv.min(0),vv.max(0)
   if b[2]>=z-.001 and a[2]<=z+3.10:structural.append(box(a[0]-.03,a[1]-.03,b[0]+.03,b[1]+.03))
  for i,(x,y) in enumerate(positions[:28]):
   name=f'AviationDense_F{level+1}_{i:02}';index=level*100+i;height=.16 if i<12 else .82
   if i<6:engine(B,name,x,y,z+height,index);scale_about(B,name,(x,y,z+height),1.6)
   elif i<8:boiler(B,name,x,y,z+height,index)
   elif i<10:gear(B,name,x,y,z+height,index)
   elif i<12:propeller(B,name,x,y,z+height,index)
   else:
    available=[q for q in science if used[q['source']+':'+str(q['id'])]<3]
    if i<22 and available:
     available.sort(key=lambda q:(used[q['source']+':'+str(q['id'])],str(q['id'])));q=available[0];used[q['source']+':'+str(q['id'])]+=1;place(B,q,name,x,y,z+height,(.75,.60,.85),math.atan2(-y,-x))
    else:instrument(B,name,x,y,z+height,index)
   row=B.layout[-1];row.update(floor=level+1,theme_category='large power/propulsion teaching exhibit' if i<12 else 'scientific instrument');x,y=relocate_aviation_display(B,name,x,y,floor,opening,occupied,i<12,positions,structural)
   if i<12:
    foot,record=adaptive_platform(B,name,x,y,z,height,'IndustrialConcrete' if i%3==0 else 'IndustrialDeck',floor,opening,occupied);row.update(record)
   else:
    pts=actual_points(B,name);extent=np.ptp(pts,axis=0);hx=max(.35,extent[0]/2+.12);hy=max(.30,extent[1]/2+.12);foot=box(x-hx-.03,y-hy-.03,x+hx+.03,y+hy+.03);assert floor.contains(foot) and all(foot.distance(p)>=1.2 for p in occupied);occupied.append(foot);precision_case(B,name,x,y,z,height,hx,hy,max(.5,extent[2]+.16));row.update(display_support_kind='glass_case',display_support_prefix=name+'_Bench',platform_height_m=height)
 return {'per_floor':28,'large_displays_per_floor':12,'scientific_cases_per_floor':16,'licensed_source_counts':dict(used)}
