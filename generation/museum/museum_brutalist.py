"""True storey footprints, sparse thick-wall apertures and stepped concrete massing."""
import math,numpy as np
from shapely import wkt
from shapely.geometry import Point,Polygon

def storeys(spec,outer):
 offsets=[2.4,1.2,0.] if spec['building_id']=='M15' else [1.4,0.]
 assert len(offsets)==spec['floors'];return [outer.buffer(-d,join_style=2) for d in offsets]

def shell(G,s,outlines):
 H=s['floor_height_m'];doors=[];windows=[]
 for level,P in enumerate(outlines):
  coords=list(P.exterior.coords);z=level*H;wall_top=H-.32 if level<len(outlines)-1 else H
  for edge,(aa,bb) in enumerate(zip(coords,coords[1:])):
   a=np.asarray(aa);b=np.asarray(bb);length=float(np.linalg.norm(b-a));d=(b-a)/length;front=abs(a[1]-P.bounds[1])<1e-6 and abs(b[1]-P.bounds[1])<1e-6;holes=[]
   if front and level==0:holes.append((length/2,3.2,0.,3.25,'door'))
   fraction=.20 if front and level==0 else (.30 if (level+edge)%2 else .68);width=min(7.8 if s['building_id']=='M16' and length>35 else 4.4,length*.25);sill=1.45 if level else 1.2;top=min(H-.65,sill+1.50);holes.append((fraction*length,width,sill,top,'window'))
   holes.sort();cursor=0.
   for j,(center,width,sill,top,kind) in enumerate(holes):
    left=center-width/2;right=center+width/2;assert left>=cursor and right<=length
    if left>cursor:G.rect(f'Brutal26_Wall_F{level+1}_E{edge}_Pier{j}',a+d*cursor,d,left-cursor,.95,z,z+wall_top,role='wall',material='wall')
    for name,lo,hi in [('Below',0.,sill),('Above',top,wall_top)]:
     if hi>lo:G.rect(f'Brutal26_Wall_F{level+1}_E{edge}_{name}{j}',a+d*left,d,width,.95,z+lo,z+hi,role='wall',material='wall')
    c=a+d*center;row={'center':c.tolist(),'direction':d.tolist(),'clear_width_m':width,'height_m':top,'z_m':z,'kind':'main_double','brutalist26':True,'level_index':level+1,'edge_index':edge,'wall_thickness_m':.95,'glazing_inset_from_center_m':.35,'required_outer_reveal_depth_m':.825}
    if kind=='door':doors.append(row)
    else:row.update(width_m=width,sill_m=z+sill,top_m=z+top);windows.append(row)
    cursor=right
   if cursor<length:G.rect(f'Brutal26_Wall_F{level+1}_E{edge}_EndPier',a+d*cursor,d,length-cursor,.95,z,z+wall_top,role='wall',material='wall')
 return doors,windows

def glazing(B,win,index,model):
 c=np.asarray(win['center']);d=np.asarray(win['direction']);n=np.array([-d[1],d[0]]);poly=wkt.loads(model['storey_footprints_wkt'][win['level_index']-1]);inward=n if poly.contains(Point(*(c+n*.7))) else -n;assert poly.contains(Point(*(c+inward*.7)));center=c+inward*.35;width=win['width_m'];sill=win['sill_m'];top=win['top_m'];B.mesh(f'Brutal26_RecessedGlass{index}',[(*(center+d*x),z) for x,z in [(-width/2,sill),(width/2,sill),(width/2,top),(-width/2,top)]],[(0,1,2,3)],'WindowGlass',False,'window_glass')
 for side in [-1,1]:
  p=center+d*side*(width/2-.025);B.tube(f'Brutal26_WindowJamb{index}_{side}',[(*p,sill),(*p,top)],.025,'Steel',True,'window_frame')
 for j,z in enumerate([sill+.02,top-.02]):B.tube(f'Brutal26_WindowEdge{index}_{j}',[(*(center-d*width/2),z),(*(center+d*width/2),z)],.025,'Steel',True,'window_frame')

def facade(B,model,shape,opening):
 s=B.spec;H=s['floor_height_m'];outlines=[wkt.loads(p) for p in model['storey_footprints_wkt']];door=model['doorways'][0];c=np.asarray(door['center']);d=np.asarray(door['direction']);n=np.array([-d[1],d[0]]);out=-n if outlines[0].contains(Point(*(c+n*.7))) else n;yaw=math.atan2(d[1],d[0])
 # Entrance lies below the true projecting second floor, with a deep concrete
 # canopy and an accessible 1:22 ramp from the real lowered external grade.
 canopy=B.box('Brutal26_EntranceCanopy',(-3.1,-.20,3.5),(3.1,2.7,3.85),'Wall',True,'entrance');canopy.location=(*c,0);canopy.rotation_euler.z=math.atan2(out[1],out[0])-math.pi/2
 def point(x,t,z):return (*(c+d*x+out*t),z)
 faces=[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)];faces=[tuple(reversed(f)) for f in faces] if np.cross(d,out)<0 else faces
 v=[point(x,t,z) for z in [-.54,0.] for x,t in [(-1.8,-.25),(1.8,-.25),(1.8,2.0),(-1.8,2.0)]];B.mesh('Brutal26_EntranceConcretePad',v,faces,'Wall',True,'entrance')
 v=[point(-1.8,2.0,-.54),point(1.8,2.0,-.54),point(1.8,9.7,-.54),point(-1.8,9.7,-.54),point(-1.8,2.0,0.),point(1.8,2.0,0.),point(1.8,9.7,-.35),point(-1.8,9.7,-.35)];B.mesh('Brutal26_EntranceRamp',v,faces,'Wall',True,'entrance')
 import bpy
 sun=bpy.data.lights.new('Brutal26_DirectionalSun','SUN');sun.energy=2.2;sun.angle=math.radians(1.);ob=bpy.data.objects.new('Brutal26_DirectionalSun',sun);B.scene.collection.objects.link(ob);ob.rotation_euler=(math.radians(38),0,math.radians(-35))
 for level,P in enumerate(outlines):
  # A dark horizontal construction joint below each real projecting slab.
  if level==0:continue
  from staircase_core import Geometry
  from building_core import extrude
  G=Geometry();ring=P.buffer(.01,join_style=2).difference(P.buffer(-.03,join_style=2));extrude(G,f'Brutal26_SlabShadowJoint{level}',ring,level*H-.31,level*H-.27,'facade_relief','Brutal26Joint')
  for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],q['material'],False,q['role'])
 B.layout.append({'name':'Brutal26_ActualMassingAndEntrance','kind':'architecture','storey_areas_m2':[p.area for p in outlines],'storey_bounds_m':[list(p.bounds) for p in outlines],'actual_window_count':len(model['windows']),'window_outer_face_to_glass_m':.825,'canopy_projection_m':2.7,'entrance_flat_pad_outward_m':2.0,'ramp_width_m':3.6,'ramp_rise_m':.35,'ramp_length_m':7.7,'ground_plaza_top_m':-.35,'massing':'M15 three true inverse setbacks1.2m per storey; M16 distinct long two-storey gallery1.4m projection','material':'Original2K metric board-form grain, template joints and shaded tie-hole texture, explicit no displacement. Actual full01 must show mass/reveals/texture/shadows; no source-only appearance acceptance.'})
