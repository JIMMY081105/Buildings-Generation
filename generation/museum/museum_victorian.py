"""Romanesque natural-history frontage and a real iron/glass barrel roof."""
import math,numpy as np
from shapely.geometry import Point,Polygon,box
from shapely.ops import unary_union
from shapely import wkt

def local_box(B,name,c,d,lo,hi,material,role='facade'):
 ob=B.box(name,lo,hi,material,True,role);ob.location=(*c,0);ob.rotation_euler.z=math.atan2(d[1],d[0]);return ob

def profile(B,name,c,d,points,y0,y1,material,collision=True,role='facade'):
 c=np.asarray(c);d=np.asarray(d);n=np.array([-d[1],d[0]]);v=[(*(c+d*x+n*y),z) for y in [y0,y1] for x,z in points];N=len(points);f=[tuple(reversed(range(N))),tuple(range(N,2*N))]+[(i,(i+1)%N,(i+1)%N+N,i+N) for i in range(N)];return B.mesh(name,v,f,material,collision,role)

def arch(B,name,c,d,spring,inner,outer,y0,y1,material):
 c=np.asarray(c);d=np.asarray(d);n=np.array([-d[1],d[0]]);v=[];f=[];angles=np.linspace(0,math.pi,49)
 for a in angles:
  for r,y in [(inner,y0),(outer,y0),(outer,y1),(inner,y1)]:v.append((*(c+d*(r*math.cos(a))+n*y),spring+r*math.sin(a)))
 for i in range(len(angles)-1):
  for j in range(4):f.append((4*i+j,4*i+(j+1)%4,4*(i+1)+(j+1)%4,4*(i+1)+j))
 f.extend([(3,2,1,0),tuple(4*(len(angles)-1)+j for j in range(4))]);B.mesh(name,v,f,material,True,'facade')

def arched_window(B,name,c,d,sill,top,width,front=-.30):
 r=width/2;spring=top-r;assert spring>sill
 for sign in [-1,1]:local_box(B,name+f'_Jamb{sign}',c,d,(sign*r-.10,front-.08,sill-.10),(sign*r+.10,front+.08,spring),'VictorianBuff')
 arch(B,name+'_RoundArch',c,d,spring,r,r+.16,front-.10,front+.10,'VictorianBuff')
 # Genuine masonry spandrels close the rectangular RAW aperture's corners.
 corners=box(-r,spring,r,top).difference(Point(0,spring).buffer(r,resolution=48));polys=list(corners.geoms) if corners.geom_type=='MultiPolygon' else [corners]
 for i,p in enumerate(polys):
  if p.area>1e-8:profile(B,name+f'_Spandrel{i}',c,d,list(p.exterior.coords)[:-1],-.22,.22,'Wall',True,'wall')
 pts=[(-r,sill),(r,sill)]+[(r*math.cos(a),spring+r*math.sin(a)) for a in np.linspace(0,math.pi,49)];c=np.asarray(c);d=np.asarray(d);n=np.array([-d[1],d[0]]);B.mesh(name+'_ArchedGlass',[(*(c+d*x+n*.015),z) for x,z in pts],[tuple(range(len(pts)))],'WindowGlass',False,'window_glass')
 local_box(B,name+'_Sill',c,d,(-r-.16,front-.16,sill-.13),(r+.16,front+.16,sill+.015),'VictorianTerracotta')
 local_box(B,name+'_IronMullion',c,d,(-.025,-.045,sill),(.025,.045,top-.025),'Iron')
 for j,z in enumerate([sill+(top-sill)*.33,sill+(top-sill)*.65]):local_box(B,name+f'_IronTransom{j}',c,d,(-r,-.04,z-.018),(r,.04,z+.018),'Iron')

def materials(B):
 B.material('VictorianBuff',(.63,.48,.25),'ashlar',1.2,.80,source_hint='limestone')
 B.material('VictorianTerracotta',(.42,.15,.065),'stone',1.0,.82,source_hint='terracotta')
 B.material('VictorianRoofTile',(.15,.075,.04),'stone',1.2,.8,source_hint='roof_tile')

def roof(B,model):
 materials(B);H=B.spec['floor_height_m'];Z=H*B.spec['floors'];opening=unary_union([wkt.loads(q['opening_wkt']) for q in model['floors'] if q.get('opening_wkt')]);x0,y0,x1,y1=opening.bounds;cx=(x0+x1)/2;rx=(x1-x0)/2+.10;rise=4.8;segments=64;v=[];f=[]
 for y in [y0-.10,y1+.10]:
  for a in np.linspace(0,math.pi,segments+1):v.append((cx+rx*math.cos(a),y,Z+.06+rise*math.sin(a)))
 for j in range(segments):f.append((j,j+1,j+1+segments+1,j+segments+1))
 B.mesh('Skylight_Victorian_BarrelGlass',v,f,'WindowGlass',False,'roof_glass')
 from staircase_core import Geometry
 from building_core import extrude
 curb=Geometry();extrude(curb,'Roof_Victorian_SkylightCurb',opening.buffer(.25).difference(opening.buffer(.15)),Z-.02,Z+.12,'roof_structure','Iron')
 for q in curb.parts:B.mesh(q['name'],q['vertices'],q['faces'],'Iron',True,'roof_structure')
 for j,y in enumerate(np.linspace(y0-.10,y1+.10,9)):
  p=[(cx+rx*math.cos(a),y,Z+.08+rise*math.sin(a)) for a in np.linspace(0,math.pi,65)];B.tube(f'Roof_Victorian_IronArch{j}',p,.075,'Iron',True,'roof_structure');B.tube(f'Roof_Victorian_Tie{j}',[(x0,y,Z+.04),(x1,y,Z+.04)],.045,'Iron',True,'roof_structure')
 for j,a in enumerate(np.linspace(0,math.pi,13)):B.tube(f'Roof_Victorian_Purlin{j}',[(cx+rx*math.cos(a),y0-.12,Z+.08+rise*math.sin(a)),(cx+rx*math.cos(a),y1+.12,Z+.08+rise*math.sin(a))],.035,'Iron',True,'roof_structure')
 # Clear glazed end walls meet the roof, so the roof is an enclosed structure.
 for j,y in enumerate([y0-.10,y1+.10]):B.mesh(f'Skylight_Victorian_EndGlass{j}',[(x0,y,Z),(x1,y,Z)]+[(cx+rx*math.cos(a),y,Z+.06+rise*math.sin(a)) for a in np.linspace(0,math.pi,65)],[tuple(range(67))],'WindowGlass',False,'roof_glass')
 B.layout.append({'kind':'architecture','name':'Victorian_iron_glass_barrel_roof','actual_roof_opening_bounds':list(opening.bounds),'rise_m':rise,'arch_ribs':9,'lower_gallery_trusses':0,'all_floor_slabs_preserved':True})

def facade(B,model,shape):
 materials(B);s=B.spec;w,h=model['building_xy_m'];H=s['floor_height_m'];Z=s['floors']*H
 for i,win in enumerate(model['windows']):arched_window(B,f'VictorianFacade_Window{i}',win['center'],win['direction'],win['sill_m'],win['top_m'],win['width_m'])
 from building_core import extrude
 from staircase_core import Geometry
 G=Geometry();cuts=[]
 for door in model['doorways']:
  c=np.asarray(door['center']);d=np.asarray(door['direction']);n=np.array([-d[1],d[0]]);half=door['clear_width_m']/2+.35;cuts.append(Polygon([c-d*half-n*3,c+d*half-n*3,c+d*half+n*3,c-d*half+n*3]))
 cut=unary_union(cuts);ring=shape.buffer(.36).difference(shape.buffer(.22))
 for j,(lo,hi) in enumerate([(.62,.84),(3.95,4.14),(H-.16,H+.12),(2*H-.16,2*H+.12),(Z-.22,Z+.14)]):extrude(G,f'VictorianFacade_StripedCourse{j}',ring.difference(cut) if lo<4.8 else ring,lo,hi,'facade','VictorianTerracotta' if j%2==0 else 'VictorianBuff')
 for row in G.parts:B.mesh(row['name'],row['vertices'],row['faces'],row['material'],True,row['role'])
 # A deep round-arched entrance vault, open at both ends, leads to real doors.
 c=(0.,-h/2-2.2);d=(1.,0.)
 for sign in [-1,1]:local_box(B,f'VictorianFacade_EntrancePier{sign}',c,d,(sign*2.65-.30,-.30,0),(sign*2.65+.30,2.25,2.15),'VictorianBuff','wall')
 arch(B,'VictorianFacade_EntranceBarrelVault',c,d,2.15,2.35,2.90,-.30,2.25,'VictorianBuff');arch(B,'VictorianFacade_EntranceArchivolt',c,d,2.15,2.90,3.05,-.42,-.25,'VictorianTerracotta')
 # Two hollow, windowed towers frame the entrance; their roofs rise above it.
 towerH=Z+2.8;tw=3.4;depth=3.4
 for sign in [-1,1]:
  x=sign*7.5;y=-h/2-1.85;prefix=f'VictorianFacade_Tower{sign}'
  B.box(prefix+'_TowerWallBack',(x-tw/2,y+depth/2-.28,0),(x+tw/2,y+depth/2,towerH),'VictorianBuff',True,'wall')
  for side in [-1,1]:B.box(prefix+f'_TowerWallSide{side}',(x+side*(tw/2-.14)-.14,y-depth/2,0),(x+side*(tw/2-.14)+.14,y+depth/2,towerH),'VictorianBuff',True,'wall')
  tiers=5;step=towerH/tiers;front=(x,y-depth/2);r=.78
  for level in range(tiers):
   z=level*step;sill=z+.85;top=z+step-.35
   for side in [-1,1]:local_box(B,prefix+f'_TowerWallPier{level}_{side}',front,(1,0),(side*(r+(tw/2-r)/2)-(tw/2-r)/2,-.02,sill),(side*(r+(tw/2-r)/2)+(tw/2-r)/2,.28,top),'VictorianBuff','wall')
   local_box(B,prefix+f'_TowerWallSill{level}',front,(1,0),(-tw/2,-.02,z),(tw/2,.28,sill),'VictorianBuff','wall');local_box(B,prefix+f'_TowerWallHead{level}',front,(1,0),(-tw/2,-.02,top),(tw/2,.28,z+step),'VictorianBuff','wall');arched_window(B,prefix+f'_ArchedWindow{level}',front,(1,0),sill,top,2*r,-.09)
   B.box(prefix+f'_TerracottaBand{level}',(x-tw/2-.07,y-depth/2-.07,z+.18),(x+tw/2+.07,y+depth/2+.07,z+.34),'VictorianTerracotta',True,'facade')
  v=[(x+a*1.84,y+b*1.84,towerH+.05) for a,b in [(-1,-1),(1,-1),(1,1),(-1,1)]]+[(x,y,towerH+3.)];B.mesh('Roof_'+prefix+'_PyramidalCap',v,[(3,2,1,0),(0,1,4),(1,2,4),(2,3,4),(3,0,4)],'VictorianRoofTile',True,'roof')
 B.layout.append({'kind':'architecture','name':'Victorian_Romanesque_frontage','arched_windows':len(model['windows']),'first_storey_window_groups':'three-light arcades either side of central entrance, united by striped courses','entry_vault_clear_width_m':4.7,'entry_vault_crown_m':4.5,'towers':2,'tower_body_height_m':towerH,'tower_roof_height_m':towerH+3.})
