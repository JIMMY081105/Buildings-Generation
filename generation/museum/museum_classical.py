"""Source-bound stone portico aligned to the actual rotunda entrance."""
import math,numpy as np
from shapely.geometry import Point

def connection_view(model):
 e=model['exits'][0];start=np.array(e['start_xy']);direction=np.array(e['direction_xy']);side=np.array([-direction[1],direction[0]]);z=e['floor_z_m'];return [*tuple(start+direction*(e['landing_length_m']+1.1)+side*.30),z+1.6],[*tuple(start-direction*.75),z-.40],30

def rotunda_portico(B,model,shape):
 door=min((q for q in model['doorways'] if q.get('kind')=='main_double'),key=lambda q:abs(q['center'][0]));c=np.array(door['center']);direction=np.array(door['direction']);normal=np.array([-direction[1],direction[0]])
 if shape.contains(Point(c+normal*.6)):normal=-normal
 def point(x,y,z):return [*(c+direction*x+normal*y),z]
 def block(name,lo,hi):
  x,y,z=lo;X,Y,Z=hi;v=[point(a,b,h) for a,b,h in [(x,y,z),(X,y,z),(X,Y,z),(x,Y,z),(x,y,Z),(X,y,Z),(X,Y,Z),(x,Y,Z)]];return B.mesh(name,v,[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)],'ExteriorStone',True,'facade')
 for j,x in enumerate([-5.1,-2.25,2.25,5.1]):
  p=point(x,3.0,0);B.component('Pillar',f'Portico16_Column{j}','ExteriorStone',p,(.86,.86,5.10),math.atan2(direction[1],direction[0]),True);block(f'Portico16_Plinth{j}',(x-.53,2.47,0),(x+.53,3.53,.18))
  for k,(radius,z,thick,top) in enumerate([(.50,.18,.12,.45),(.44,.30,.10,.40),(.44,4.62,.14,.51),(.51,4.76,.16,.57)]):B.cylinder(f'Portico16_Mould{j}_{k}',point(x,3,z),radius,thick,'ExteriorStone',True,top)
  block(f'Portico16_CapitalAbacus{j}',(x-.58,2.42,4.92),(x+.58,3.58,5.10))
 block('Portico16_Entablature',(-6.3,.10,5.10),(6.3,3.85,5.72));peak=5.72+6.3*math.tan(math.radians(19));v=[point(x,y,z) for y in [3.90,.15] for x,z in [(-6.3,5.72),(6.3,5.72),(0,peak)]];B.mesh('Portico16_StonePediment',v,[(0,2,1),(3,4,5),(0,1,4,3),(1,2,5,4),(2,0,3,5)],'ExteriorStone',True,'facade')
 B.layout.append({'kind':'architecture','name':'M02_actual_entrance_stone_portico','actual_source_doorway_center':c.tolist(),'column_count':4,'inner_capital_clear_gap_m':3.34,'source':'Actual Pillar factory meshes, authored stone plinth/torus/capital/entablature/pediment, aligned to real open entrance; no raised approach barrier.','status':'AUTHORED_REQUIRES_ACTUAL_EXPORT_CHECKS_AND_PREVIEW'})
