"""Visible stepped atrium coves, distinct from the former tiny wall cornice."""
import numpy as np
from shapely import wkt
from building_core import extrude
from staircase_core import Geometry

def interiors(B,model,shape,opening):
 H=B.spec['floor_height_m'];N=B.spec['floors'];G=Geometry();records=[]
 for level in range(N):
  ceiling=wkt.loads(model['floors'][level+1]['polygon_wkt']) if level<N-1 else shape.difference(opening);hole=shape.difference(ceiling)
  for j in range(4):
   band=hole.buffer(.28*(j+1),join_style=2).difference(hole.buffer(.28*j,join_style=2)).intersection(ceiling);z=(level+1)*H-.13-j*.12
   extrude(G,f'Deco24_AtriumCove_F{level+1}_{j}',band,z-.055,z,'ceiling_detail','Brass' if j==1 else 'Ceiling');records.append({'floor':level+1,'step':j,'plan_area_m2':band.area,'actual_low_z_m':z-.055})
  ring=hole.buffer(.38,join_style=2).boundary
  for j,t in enumerate(np.linspace(0,ring.length,8,endpoint=False)):
   p=ring.interpolate(float(t));B.area(f'Deco24_CoveWash_F{level+1}_{j}',(p.x,p.y,(level+1)*H-.58),(p.x,p.y,(level+1)*H-.10),90,.8,(1.,.89,.69))
 for row in G.parts:B.mesh(row['name'],row['vertices'],row['faces'],row['material'],False,row['role'])
 B.layout.append({'name':'Deco24_VisibleAtriumCoves','kind':'architecture','steps':records,'lighting':'8 real hidden upward warm disk lights per floor','physical_scope':'Ceiling relief lies above floor+4.575m for5.12m storeys and is visual-only ceiling detail; stair/guard exact physical source independently rebuilt, no floor deletion.'})
