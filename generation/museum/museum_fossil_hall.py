"""Distinct limestone fossil hall, with supported barrel roof and real arches."""
import math,json,numpy as np,bpy
from shapely.geometry import box,Point,Polygon,LineString
from shapely.ops import unary_union
from staircase_core import Geometry
from building_core import extrude

def materials(B):
 for name,color,rough in [('Wall',(.64,.59,.48),.85),('ExteriorStone',(.70,.65,.53),.80),('Plinth',(.39,.36,.29),.82)]:
  old=B.materials.pop(name,None)
  if old:
   assert old.users==0;bpy.data.materials.remove(old)
  B.material(name,color,'ashlar',1.2,rough,source_hint='limestone')
 B.material('Fossil37RoofMetal',(.16,.19,.18),'stone',1.2,.42,.72,source_hint='metal')

def glazing(B,win,i,model):
 from museum_victorian import arched_window
 # Existing physical wall rectangles remain; true masonry corner spandrels
 # close the rectangular aperture above the semicircular head.
 B.materials['VictorianBuff']=B.materials['ExteriorStone'];B.materials['VictorianTerracotta']=B.materials['ExteriorStone']
 arched_window(B,f'Fossil37_Window{i}',win['center'],win['direction'],win['sill_m'],win['top_m'],win['width_m'])

def roof(B,model):
 w,h=model['building_xy_m'];z=B.spec['floors']*B.spec['floor_height_m'];rx=w/2+.38;rise=4.2;y0=-h/2-.32;y1=h/2+.32;xs=np.linspace(-rx,rx,129)
 def height(x):return z+.06+rise*math.sqrt(max(0,1-(x/rx)**2))
 # Thick closed metal shells flank one real central glazed skylight strip.
 for side,(a,b) in enumerate([(-rx,-3.5),(3.5,rx)]):
  xx=np.linspace(a,b,65);profile=[(float(x),height(x)) for x in xx]+[(float(x),height(x)-.18) for x in reversed(xx)];G=Geometry();extrude(G,f'Fossil37_BarrelShell{side}',Polygon(profile),y0,y1,'roof_structure','Fossil37RoofMetal')
  for q in G.parts:B.mesh(q['name'],[(x,t,zz) for x,zz,t in q['vertices']],[f[::-1] for f in q['faces']],q['material'],True,q['role'])
 xx=np.linspace(-3.5,3.5,33);v=[(float(x),y,height(x)+.002) for y in [y0,y1] for x in xx];faces=[(i,i+1,len(xx)+i+1,len(xx)+i) for i in range(len(xx)-1)];B.mesh('Fossil37_CentralBarrelSkylight',v,faces,'WindowGlass',False,'roof_glass')
 # Actual 18cm masonry end walls close the full arc down to the existing wall.
 profile=Polygon([(-rx,z-.04),(rx,z-.04)]+[(float(x),height(x)) for x in reversed(xs)])
 for end,y in enumerate([y0+.09,y1-.09]):
  G=Geometry();extrude(G,f'Fossil37_ClosedBarrelGable{end}',profile,y-.09,y+.09,'roof_structure','ExteriorStone')
  for q in G.parts:B.mesh(q['name'],[(x,t,zz) for x,zz,t in q['vertices']],[f[::-1] for f in q['faces']],q['material'],True,q['role'])
 for j,y in enumerate(np.linspace(y0+.04,y1-.04,10)):
  B.tube(f'Fossil37_BarrelRib{j}',[(float(x),float(y),height(x)+.035) for x in xs],.055,'Iron',True,'roof_structure')
 for j,x in enumerate([-3.5,3.5]):B.tube(f'Fossil37_GlassEdgePurlin{j}',[(x,y0,height(x)+.02),(x,y1,height(x)+.02)],.045,'Iron',True,'roof_structure')
 B.layout.append({'name':'Fossil37_LimestoneBarrelHall','kind':'architecture','barrel_rise_m':rise,'closed_end_gables':2,'end_gable_thickness_m':.18,'roof_shell_thickness_m':.18,'central_glazed_strip_width_m':7.,'roof_ribs':10,'all_original_stairs_floors_walls_retained':True,'scope':'New original iron/metal barrel shell with two actual closed limestone end gables, real central skylight, no unsupported external arch spans.'})

def facade(B,model,shape,opening):
 H=B.spec['floor_height_m'];w,h=model['building_xy_m'];G=Geometry()
 # Restrained warm limestone courses, distinct from both brick natural halls.
 ring=shape.buffer(.28,join_style=2).difference(shape.buffer(.23,join_style=2))
 for level in range(1,B.spec['floors']+1):extrude(G,f'Fossil37_LimestoneCourse{level}',ring,level*H-.14,level*H+.015,'facade','ExteriorStone')
 for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],q['material'],True,q['role'])
 # Continue the existing finite flush plaza visually; no giant new collider.
 G=Geometry();extrude(G,'Fossil37_DistantSite',box(-500,-500,500,500).difference(box(-w*2,-h*2,w*2,h*2)),-.24,0.,'site','Plaza')
 for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],'Plaza',False,'site')
 sun=bpy.data.lights.new('Fossil37_DirectionalSun','SUN');sun.energy=2.;sun.angle=math.radians(1.);ob=bpy.data.objects.new(sun.name,sun);B.scene.collection.objects.link(ob);ob.rotation_euler=(math.radians(35),0,math.radians(-35))
 # True 20mm warm dark stone strips, supported by each original stone tread.
 B.material('Fossil37StoneEdge',(.19,.16,.11),'stone',.8,.65)
 treads=[q for q in model['parts'] if q['role']=='tread'];routes={};rows=[]
 for q in treads:routes.setdefault(q['name'].rsplit('_Tread',1)[0],[]).append(q)
 def top(q):
  v=np.asarray(q['vertices']);z=float(v[:,2].max());return unary_union([Polygon(v[f,:2]) for f in q['faces'] if (abs(v[f,2]-z)<1e-7).all()]),z
 for route in routes.values():
  route=sorted(route,key=lambda q:int(q['name'].rsplit('Tread',1)[1]));centers=np.asarray([top(q)[0].centroid.coords[0] for q in route])
  for k,q in enumerate(route):
   poly,z=top(q);heading=centers[min(k+1,len(route)-1)]-centers[max(k-1,0)];heading/=np.linalg.norm(heading);ring=np.asarray(poly.exterior.coords);edges=[]
   for a,b in zip(ring[:-1],ring[1:]):
    delta=b-a;normal=np.array([delta[1],-delta[0]])*(1 if poly.exterior.is_ccw else -1);normal/=np.linalg.norm(normal);edges.append((float(normal@(-heading)),a,b))
   _,a,b=max(edges,key=lambda x:x[0]);strip=poly.intersection(LineString([a,b]).buffer(.02,cap_style=2));assert strip.geom_type=='Polygon' and 0<strip.area/poly.area<.10;v=[(float(x),float(y),z+.0004) for x,y in list(strip.exterior.coords)[:-1]];name='Fossil37_Edge_'+q['name'];B.mesh(name,v,[list(range(len(v)))],'Fossil37StoneEdge',False,'decorative_flush_stone_inlay');rows.append({'name':name,'source_tread':q['name'],'width_m':.02,'surface_offset_m':.0004,'retained_stone_fraction':1-strip.area/poly.area})
 (B.out/'FOSSIL37_AUTHORING.json').write_text(json.dumps({'status':'AUTHORED_NOT_VERIFIED','actual_round_arch_windows':len(model['windows']),'actual_stone_edges':rows,'scope':'First distinct limestone/barrel fossil hall; all original mainA stair and floor geometry retained. Real source color audit, current exported geometry/static/physics/native photos still required.'},indent=2))
