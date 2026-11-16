"""Natural-history archive frontage: real arched apertures and one gabled roof."""
import math,numpy as np
from shapely.geometry import Point,Polygon
from shapely.ops import unary_union
from museum_victorian import materials,arched_window,arch,local_box
from staircase_core import Geometry
from building_core import extrude

def facade(B,model,shape):
 materials(B);s=B.spec;H=s['floor_height_m'];Z=H*s['floors'];G=Geometry()
 for i,win in enumerate(model['windows']):arched_window(B,f'Natural24_Window{i}',win['center'],win['direction'],win['sill_m'],win['top_m'],win['width_m'])
 cuts=[]
 for door in model['doorways']:
  c=np.asarray(door['center']);d=np.asarray(door['direction']);n=np.array([-d[1],d[0]]);half=door['clear_width_m']/2+.3;cuts.append(Polygon([c-d*half-n*2,c+d*half-n*2,c+d*half+n*2,c-d*half+n*2]))
 ring=shape.buffer(.33,join_style=2).difference(shape.buffer(.22,join_style=2));cut=unary_union(cuts)
 for level in range(1,s['floors']+1):extrude(G,f'Natural24_TerracottaCourse{level}',ring.difference(cut) if level==1 else ring,level*H-.23,level*H+.05,'facade','VictorianTerracotta')
 for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],q['material'],True,q['role'])
 door=model['doorways'][0];c=np.asarray(door['center']);d=np.asarray(door['direction']);n=np.array([-d[1],d[0]]);out=-n if shape.contains(Point(*(c+n*.6))) else n;entry=c+out*.75;r=door['clear_width_m']/2+.3;spring=door['height_m']+.15
 arch(B,'Natural24_EntranceArchivolt',entry,d,spring,r,r+.28,-.22,.22,'VictorianBuff')
 for sign in [-1,1]:local_box(B,f'Natural24_EntranceJamb{sign}',entry,d,(sign*r-.14,-.22,0),(sign*r+.14,.22,spring),'VictorianBuff','wall')
 # A single front gable, distinct from M04's twin towers. Its oculus is a
 # genuine hole through masonry, backed by a visible glass disk and iron cross.
 triangle=Polygon([(-5.,Z-.02),(5.,Z-.02),(0.,Z+4.1)]);hole=Point(0,Z+1.45).buffer(.68,resolution=48);gable=triangle.difference(hole);X=Geometry();extrude(X,'Natural24_Gable',gable,-.16,.16,'facade','VictorianBuff');face=c+out*.38
 for q in X.parts:
  v=[(*(face+d*u+n*t),z) for u,z,t in q['vertices']];B.mesh(q['name'],v,q['faces'],'VictorianBuff',True,'facade')
 points=[(*(face+d*x+out*.22),z) for x,z in [(-5.,Z),(0.,Z+4.1),(5.,Z)]];B.tube('Natural24_GableTerracottaRakingCoping',points,.15,'VictorianTerracotta',True,'facade')
 circle=[(*(face+d*(.68*math.cos(a))+out*.20),Z+1.45+.68*math.sin(a)) for a in np.linspace(0,math.tau,65)];B.tube('Natural24_OculusStoneRim',circle,.10,'VictorianTerracotta',True,'facade');B.mesh('Natural24_OculusGlass',[(*(face+d*(.65*math.cos(a))),Z+1.45+.65*math.sin(a)) for a in np.linspace(0,math.tau,64,endpoint=False)],[tuple(range(64))],'WindowGlass',False,'window_glass')
 for axis in [0,1]:
  aa=face+out*.03+d*(-.65 if axis==0 else 0);bb=face+out*.03+d*(.65 if axis==0 else 0);B.tube(f'Natural24_OculusIronCross{axis}',[(*aa,Z+1.45-(.65 if axis else 0)),(*bb,Z+1.45+(.65 if axis else 0))],.025,'Iron',True,'window_frame')
 B.layout.append({'name':'Natural24_ArchiveFrontage','kind':'architecture','style':'Victorian natural-history archive with one central gable/oculus, real round-arched windows and terracotta bands; L floor plan and full wood W stair retained','arched_window_count':len(model['windows']),'gable_rise_m':4.1,'oculus_actual_hole_diameter_m':1.36,'physics':'Fresh all C1-C5 required; arched aperture corners/entry piers and roof ribs are exact new collision meshes.'})
