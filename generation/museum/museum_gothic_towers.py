"""Ground-up western bell towers and individually honed stone stair slabs."""
import math,numpy as np,bpy
from shapely.geometry import Polygon,box
from staircase_core import Geometry
from building_core import extrude

def materials(B):
 B.material('Gothic18GuardStone',(.36,.37,.35),'stone',.8,.9,source_hint='slate|stone_floor')
 B.material('StairTread',(.65,.64,.59),'monolithic_honed_stone',2.4,.74)
 B.material('Gothic18Riser',(.34,.35,.32),'monolithic_honed_stone',2.4,.84)
 B.material_records['StairTread'].update(single_slab_per_tread=True,no_masonry_joints=True,original_procedural_material='Subtle2percent stone grain, no block pattern; individual actual meshes provide slab boundaries.',front_edge_bevel_m=.005 if B.spec['building_id']=='M07' else 0.,integral_front_nosing_m=.008 if B.spec['building_id']=='M07' else 0.,flat_support='Original full nominal horizontal support and rear floor joint retained; bevel only at extended leading edge.')

def tread(B,ob,row=None,model=None):
 ob.data.materials.append(B.materials['Gothic18Riser'])
 for face in ob.data.polygons:face.material_index=0 if face.normal.z>.7 else 1
 if B.spec['building_id']=='M07':
  assert row is not None and model is not None
  route=next(q for q in model['routes'] if q['name']==row['name'].rsplit('_Tread',1)[0]);direction=np.asarray(route['xy'][-1])-route['xy'][0];direction/=np.linalg.norm(direction);points=np.asarray([v.co[:] for v in ob.data.vertices]);top=points[:,2].max();along=points[:,:2]@direction;leading=along.min();upper_front=np.flatnonzero((abs(points[:,2]-top)<1e-6)&(abs(along-leading)<1e-6));assert len(upper_front)==2
  # 8mm integral nosing, beveled only at the extended front edge. The original
  # complete horizontal support rectangle and back joint are retained.
  for i in upper_front:ob.data.vertices[int(i)].co.x-=direction[0]*.008;ob.data.vertices[int(i)].co.y-=direction[1]*.008
  ob.data.update();weights=ob.data.attributes.get('bevel_weight_edge') or ob.data.attributes.new('bevel_weight_edge','FLOAT','EDGE');count=0
  for edge in ob.data.edges:
   selected=set(edge.vertices)==set(map(int,upper_front));weights.data[edge.index].value=float(selected);count+=selected
  assert count==1;bpy.ops.object.select_all(action='DESELECT');bpy.context.view_layer.objects.active=ob;ob.select_set(True);mod=ob.modifiers.new('Integral8mmNosingFront5mmBevel18','BEVEL');mod.width=.005;mod.segments=2;mod.affect='EDGES';mod.limit_method='WEIGHT';bpy.ops.object.modifier_apply(modifier=mod.name);ob.select_set(False)

def bell_towers(B,model,roof_high):
 from museum_gothic import arch_curve,aperture
 w,h=model['building_xy_m'];Z=len(model['floors'])*model['floor_height_m'];top=roof_high+7.;spirebase=top-5.1;belfry=spirebase-3.5
 # West facade: two complete3.2m towers outside the facade, leaving entrance clear.
 for sign in [-1,1]:
  x=sign*5.8;y=-h/2-1.15;half=1.6;thick=.34;G=Geometry();G.box('lower',(x-half,y-half,0),(x+half,y+half,belfry));v=[];f=[]
  for side in range(4):
   angle=side*math.pi/2;d=np.array([math.cos(angle),math.sin(angle)]);out=np.array([-d[1],d[0]]);c=np.array([x,y])+out*(half-thick/2);a=arch_curve(.62,2.7,1.82);hole=Polygon([(-.62,0),(.62,0),*reversed(a)]);panel=box(-half,0,half,3.5).difference(Polygon([(u,z+.35) for u,z in hole.exterior.coords]));part=Geometry();extrude(part,'belfrywall',panel,-thick/2,thick/2,'facade','ExteriorStone')
   for q in part.parts:
    off=len(v);v.extend([(*tuple(c+d*u+out*depth),belfry+z) for u,z,depth in q['vertices']]);f.extend([tuple(off+i for i in face) for face in q['faces']])
   aperture(B,f'Gothic18_Belfry{sign}_{side}',c+out*(thick/2+.035),d,out,.62,belfry+.35,2.7,glazed=False,frame=.08,depth=.04)
   # Real horizontal wood slats, open spaces between; not a painted window.
   for k in range(5):
    ob=B.box(f'Gothic18_BellLouver{sign}_{side}_{k}',(-.54,-.055,.09+k*.27),(.54,.055,.16+k*.27),'GothicOak',True,'facade');ob.location=(*tuple(c),belfry+.56);ob.rotation_euler.z=angle
  for q in G.parts:
   off=len(v);v.extend(q['vertices']);f.extend([tuple(off+i for i in face) for face in q['faces']])
  B.mesh(f'Gothic14_BellTurret{sign}',v,f,'ExteriorStone',True,'facade')
  for k,z in enumerate([.45,Z/3,Z*2/3,belfry,spirebase-.2]):B.box(f'Gothic18_TowerBelt{sign}_{k}',(x-half-.10,y-half-.10,z),(x+half+.10,y+half+.10,z+.16),'ExteriorStone',True,'facade')
  B.box(f'Gothic14_TurretCornice{sign}',(x-1.83,y-1.83,spirebase-.15),(x+1.83,y+1.83,spirebase+.10),'ExteriorStone',True,'facade');verts=[(x+dx,y+dy,spirebase+.1) for dx,dy in [(-1.8,-1.8),(1.8,-1.8),(1.8,1.8),(-1.8,1.8)]]+[(x,y,top)];B.mesh(f'Gothic14_TurretSpire{sign}',verts,[(0,1,4),(1,2,4),(2,3,4),(3,0,4)],'RoofTile',False,'roof_detail')
  # Narrow recessed vertical bays establish the tower's height from its base.
  for k,z in enumerate([2.0,model['floor_height_m']+1.,2*model['floor_height_m']+1.]):
   if z+2.1>belfry:continue
   B.box(f'Gothic18_TowerBay{sign}_{k}',(x-.33,y-half-.018,z),(x+.33,y-half-.008,z+2.1),'GothicOak',False,'facade')
 B.layout.append({'kind':'architecture','name':'Gothic18_ground_up_western_bell_towers','shaft_base_z_m':0,'tower_centers':[[-5.8,-h/2-1.15],[5.8,-h/2-1.15]],'actual_spire_tip_z_m':top,'belfry_openings':8,'slabs':'individual light honed slabs and darker riser faces, M07 actual5mm bevel'})

def connection_view(model):
 e=model['exits'][0];start=np.array(e['start_xy']);direction=np.array(e['direction_xy']);side=np.array([-direction[1],direction[0]]);z=e['floor_z_m']
 # On the upper floor beyond the landing, looking obliquely back at the last5.
 eye=[*tuple(start+direction*(e['landing_length_m']+.6)+side*.50),z+1.70];target=[*tuple(start-direction*1.10),z-.64]
 return eye,target,28
