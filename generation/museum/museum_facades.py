"""Style facade kits. All details are authored meshes, with metric dimensions."""
import math
import numpy as np
from shapely.geometry import Point,Polygon
from building_core import extrude
from staircase_core import Geometry

def facade_kit(B,model,shape,opening,allow_med32=True):
 s=B.spec;family=s['style_family'];w,h=model['building_xy_m'];H=s['floor_height_m'];Z=H*s['floors'];G=Geometry()
 if s.get('fossil37'):
  from museum_fossil_hall import facade
  facade(B,model,shape,opening);return
 if allow_med32 and s.get('mediterranean32'):
  from museum_mediterranean import facade
  facade(B,model,shape,opening);return
 if s['building_id']=='M04':
  from museum_victorian import facade
  facade(B,model,shape);return
 if s.get('natural24'):
  from museum_natural_history import facade
  facade(B,model,shape);return
 if s.get('brutalist26'):
  from museum_brutalist import facade
  facade(B,model,shape,opening);return
 if s.get('industrial27'):
  from museum_industrial_brick import facade
  facade(B,model,shape,opening);return
 if family=='east_asian' and s.get('east_asian13'):
  from museum_east_asian import facade
  facade(B,model,shape,opening);return
 if family=='gothic' and s.get('gothic14'):
  from museum_gothic import architecture
  architecture(B,model,shape,opening);return
 def ring(name,outer,inner,z0,z1,mat):
  p=shape.buffer(outer,join_style=2).difference(shape.buffer(inner,join_style=2));extrude(G,name,p,z0,z1,'facade',mat)
 if family=='neoclassical':
  if s['building_id']=='M02':
   from museum_classical import rotunda_portico
   rotunda_portico(B,model,shape)
  ring('Parapet_StoneWall',.25,.02,Z+.13,Z+.70,'ExteriorStone');ring('Parapet_Coping',.37,-.03,Z+.70,Z+.83,'ExteriorStone')
  for win in model['windows']:
   if win['sill_m']>H:continue
   c=np.asarray(win['center']);d=np.asarray(win['direction']);n=np.array([-d[1],d[0]]);c-=n*.32;ww=win['width_m']/2+.20;z=win['top_m']+.16
   a=c-d*ww;b=c+d*ww;peak=c
   front=[(*a,z),(*b,z),(*peak,z+.40)];back=[(x+n[0]*.15,y+n[1]*.15,zz) for x,y,zz in front];B.mesh('Window_Pediment_'+str(len(B.scene.objects)),front+back,[(0,2,1),(3,4,5),(0,1,4,3),(1,2,5,4),(2,0,3,5)],'ExteriorStone',True,'facade')
 elif family=='gothic':
  for x in np.arange(-w/2+2,w/2,5.5):
   for sign in [-1,1]:
    y=sign*h/2;B.box(f'Gothic_Buttress_{x}_{sign}',(x-.38,y-.7,0),(x+.38,y+.7,Z*.82),'ExteriorStone',True,'facade');B.box(f'Gothic_ButtressCap_{x}_{sign}',(x-.52,y-.8,Z*.82),(x+.52,y+.8,Z*.85),'ExteriorStone',True,'facade')
  for i,win in enumerate(model['windows']):
   c=np.array(win['center']);d=np.array(win['direction']);n=np.array([-d[1],d[0]]);ww=win['width_m']/2;z=win['top_m'];pts=[(*(c-d*ww-n*.30),z-.6),(*(c-d*ww*.55-n*.30),z+.05),(*(c-n*.30),z+.65),(*(c+d*ww*.55-n*.30),z+.05),(*(c+d*ww-n*.30),z-.6)];B.tube(f'Gothic_PointedArch{i}',pts,.10,'ExteriorStone',True,'facade')
 elif family in ['industrial','victorian_natural']:
  B.material('Terracotta',(.40,.13,.065),'stone',1.2,.8,source_hint='terracotta|brick')
  if s['building_id']=='M09':B.material('IndustrialRoofLiner',(.19,.23,.25),'coated_steel_roof',1.,.85,.15,source_hint='metal')
  for level in range(1,s['floors']+1):ring(f'Terracotta_Course{level}',.34,.22,level*H-.20,level*H+.02,'Terracotta')
  for x in np.arange(-w/2+2,w/2,5.):
   if s['building_id']=='M09':continue
   for level in [s['floors']-1]:
    z=(level+1)*H-1.3
    B.tube(f'Industrial_Truss_{x}_{level}',[(x,-h/2+.5,z),(x,0,z+.8),(x,h/2-.5,z)],.07,'Iron',True,'truss')
    B.tube(f'Industrial_TrussTie_{x}_{level}',[(x,-h/2+.5,z),(x,h/2-.5,z)],.045,'Iron',True,'truss')
  for i,y in enumerate(np.arange(-h/2,h/2,6.)):
   yy=min(y+6,h/2);v=[(-w/2,y,Z),(w/2,y,Z),(-w/2,yy,Z+1.9),(w/2,yy,Z+1.9)];B.mesh(('Roof_' if s['building_id']=='M09' else '')+f'Sawtooth_Roof{i}',v,[(0,1,3,2)],'IndustrialRoofLiner' if s['building_id']=='M09' else 'Steel' if family=='industrial' else 'Glass',s['building_id']=='M09','roof_detail');B.mesh(('Skylight_' if s['building_id']=='M09' else '')+f'Sawtooth_Clerestory{i}',[(-w/2,yy,Z),(w/2,yy,Z),v[3],v[2]],[(0,1,2,3)],'WindowGlass',s['building_id']=='M09','roof_glass')
   if s['building_id']=='M09':
    for side in [-1,1]:B.mesh(f'Roof_Sawtooth_EndCap{i}_{side}',[(side*w/2,y,Z),(side*w/2,yy,Z),(side*w/2,yy,Z+1.9)],[(0,1,2)],'ExteriorStone',True,'roof_structure')
 elif family=='modern':
  ring('Modern_FloatingFascia',.55,.22,Z-.15,Z+.23,'Ceiling')
 elif family=='brutalist':
  ring('Brutalist_CantileverRoof',1.0,.20,Z-.38,Z+.22,'Wall')
  for i,win in enumerate(model['windows']):
   c=np.asarray(win['center']);d=np.asarray(win['direction']);n=np.array([-d[1],d[0]]);yaw=math.atan2(d[1],d[0]);ww=win['width_m']/2;z=win['sill_m'];top=win['top_m']
   for sign in [-1,1]:
    o=B.box(f'Brutalist_DeepReveal{i}_{sign}',(sign*ww-.13,-.75,z),(sign*ww+.13,.24,top),'Wall',True,'facade');o.location=(*c,0);o.rotation_euler.z=yaw
 elif family=='art_deco':
  from museum_deco import facade
  facade(B,model,shape,opening)
 elif family in ['nordic','east_asian','mediterranean']:
  B.material('RoofTile',(.30,.07,.035) if family=='mediterranean' else (.06,.065,.055),'brick' if family=='mediterranean' else 'wood',2.4,.8)
  overhang=2.2 if family=='east_asian' else 1.8 if s.get('nordic28') else 1.;ridge=Z+(7.0 if s.get('nordic28') else 4.5 if family=='east_asian' else 3.)
  from museum_pitched_roof import pitched_roof
  pitched_roof(B,model,shape,overhang,ridge)
  if s.get('nordic28'):
   for sign in [-1,1]:B.box(f'Nordic28_OverhangingEave{sign}',(-w/2-overhang,sign*(h/2+overhang)-.08,Z-.11),(w/2+overhang,sign*(h/2+overhang)+.08,Z+.08),'Wood',True,'roof_structure')
   front=-h/2-.245
   for sign in [-1,1]:B.box(f'Nordic28_FullHeightGlazingJamb{sign}',(sign*3.82-.08,front-.07,.02),(sign*3.82+.08,front+.07,Z-.10),'Wood',True,'window_frame')
  if family=='nordic' or s.get('mediterranean_roof24'):
   # Real upward wash lights within the actual ceiling opening illuminate the
   # pale timber lining; the original roof and every floor/guard remain intact.
   safe=opening.buffer(-.25);safe=safe if not safe.is_empty else opening
   xmin,ymin,xmax,ymax=safe.bounds;seeds=[]
   for fx,fy in [(.2,.2),(.8,.2),(.2,.8),(.8,.8)]:
    point=Point(xmin+(xmax-xmin)*fx,ymin+(ymax-ymin)*fy)
    if not safe.covers(point):
     from shapely.ops import nearest_points
     point=nearest_points(safe,point)[0]
    seeds.append((point.x,point.y))
   prefix='Nordic' if family=='nordic' else 'Mediterranean';power=700 if family=='nordic' else 500
   for i,(x,y) in enumerate(seeds):B.area(f'{prefix}RoofUpwash24_{i}',(x,y,Z+.15),(x,y,Z+2.2),power,2.4,(1.,.93,.82))
   B.layout.append({'name':prefix+'RoofLighting24','kind':'architecture','roof_liner_halves':2,'liner_vertical_offset_m':-.035,'upwash_count':4,'upwash_power_w_each':power,'upwash_xy_m':seeds,'upwash_z_m':Z+.15,'source_diagnosis':'M20_BLACK_OPENING24_DIAGNOSIS.json' if family=='nordic' else 'Actual M24f3 intermediate08 black unlit roof underside','scope':'Pale real roof underside and upward wash inside roof volume, .15m above flat ceiling so the retained slab cannot block the wash. Original roof/floors/guards retained; actual native06-08 still required.'})
  if family=='east_asian':
   for i,win in enumerate(model['windows']):
    c=np.asarray(win['center']);d=np.asarray(win['direction']);n=np.array([-d[1],d[0]])
    for j,t in enumerate(np.arange(-win['width_m']/2,win['width_m']/2,.18)):
     p=c+d*t-n*.12;B.tube(f'Lattice_{i}_{j}',[(*p,win['sill_m']),(*p,win['top_m'])],.018,'Wood',True,'facade')
  if family=='mediterranean':
   for i,x in enumerate(np.arange(-w/2+2,w/2,4.)):
    y=-h/2-1.;B.cylinder(f'Arcade_Column{i}',(x,y,0),.22,2.8,'ExteriorStone',True)
    pts=[(x+2+2*math.cos(t),y,2.8+1.3*math.sin(t)) for t in np.linspace(0,math.pi,30)];B.tube(f'Arcade_Arch{i}',pts,.14,'Ceiling',True,'facade')
 elif family=='marine':
  B.material('MarineBlue',(.014,.16,.25),'stone',2.4,.18,.55)
  for level in range(1,s['floors']+1):
   inner=shape.buffer(.24).exterior;outer=shape.buffer(.92).exterior;segments=512;v=[];f=[]
   for j in range(segments):
    t=j/segments;a=inner.interpolate(t,normalized=True);b=outer.interpolate(t,normalized=True);z=level*H+.32+math.sin(t*math.tau*8)*.28;v.extend([(a.x,a.y,z-.06),(b.x,b.y,z-.06),(b.x,b.y,z+.06),(a.x,a.y,z+.06)])
   for j in range(segments):
    a=4*j;b=4*((j+1)%segments)
    for k in range(4):f.append((a+k,b+k,b+(k+1)%4,a+(k+1)%4))
   B.mesh(f'Marine_WaveShadingFin{level}',v,f,'MarineBlue',True,'facade')
 elif family=='glasshouse':
  if s['building_id']=='M28':
   # Follow the actual polygonal rotunda perimeter at every roof latitude.
   # No rectangular roof corners extend beyond the circular glass enclosure.
   perimeter=np.asarray(list(shape.exterior.coords)[:-1]);N=len(perimeter);rise=5.2;rings=18;v=[];f=[]
   for t in np.linspace(0,math.pi/2,rings,endpoint=False):
    for x,y in perimeter:v.append((x*math.cos(t),y*math.cos(t),Z+rise*math.sin(t)))
   for k in range(rings-1):
    for j in range(N):q=(j+1)%N;f.append((k*N+j,k*N+q,(k+1)*N+q,(k+1)*N+j))
   top=len(v);v.append((0,0,Z+rise))
   for j in range(N):f.append(((rings-1)*N+j,(rings-1)*N+(j+1)%N,top))
   B.mesh('Roof_Glasshouse_RotundaGlassDome',v,f,'WindowGlass',False,'roof_glass')
   for j,(x,y) in enumerate(perimeter):
    B.tube(f'Roof_Glasshouse_RadialIronRib{j}',[(x*math.cos(t),y*math.cos(t),Z+.04+rise*math.sin(t)) for t in np.linspace(0,math.pi/2,40)],.07,'Iron',True,'roof_structure')
    B.tube(f'Glasshouse_CastIronPerimeterColumn{j}',[(x,y,0),(x,y,Z+.06)],.085,'Iron',True,'column')
   for k,t in enumerate(np.linspace(0,math.pi/2,8,endpoint=False)):
    pts=[(x*math.cos(t),y*math.cos(t),Z+.04+rise*math.sin(t)) for x,y in perimeter];B.tube(f'Roof_Glasshouse_CircumferentialRing{k}',pts+[pts[0]],.045,'Iron',True,'roof_structure')
   for level in range(1,s['floors']):
    pts=[(x,y,level*H+.03) for x,y in perimeter];B.tube(f'Glasshouse_FloorEdgeIronRing{level}',pts+[pts[0]],.09,'Iron',True,'beam')
   B.layout.append({'kind':'architecture','name':'M28_rotunda_iron_glass_dome','actual_perimeter_vertices':N,'dome_rise_m':rise,'radial_iron_ribs':N,'roof_geometry':'scaled actual rotunda perimeter rings, no rectangular corners','status':'AUTHORED_REQUIRES_ACTUAL_EXPORT_AND_VISUAL_CHECK'})
  else:
   for x in np.arange(-w/2,w/2+.1,3.):
    pts=[(x,-h/2,Z),(x,0,Z+4.),(x,h/2,Z)];B.tube(f'Glasshouse_RoofFrame{x}',pts,.085,'Iron',True,'roof_structure')
   for sign in [-1,1]:B.mesh(f'Roof_Glasshouse_GlassRoof{sign}',[(-w/2,sign*h/2,Z),(w/2,sign*h/2,Z),(w/2,0,Z+4),(-w/2,0,Z+4)],[(0,1,2,3)],'Glass',False,'roof_glass')
 for row in G.parts:B.mesh(row['name'],row['vertices'],row['faces'],row['material'],True,row['role'])
 B.layout.append({'kind':'facade_kit','family':family,'source':'Museum29 parametric authored geometry','residential_wood_windows':family in ['nordic','east_asian']})
