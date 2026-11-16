"""ADJUST11: dense groups, actual platform occupancy, small instruments in cases."""
import math,collections,numpy as np,bpy
from mathutils import Matrix,Vector
from shapely import wkt
from shapely.geometry import box,MultiPoint
from shapely.ops import unary_union
from building_core import extrude
from staircase_core import Geometry
from museum_exhibits import sources,place,internal_gallery_door
from museum_machines import boiler,gear,propeller

def actual_points(B,name):
 bpy.context.view_layer.update();parts=[ob for ob in B.scene.objects if ob.type=='MESH' and not ob.hide_render and (ob.name==name or ob.name.startswith(name+'_')) and ob.get('museum_category') in ['procedural_exhibit','licensed_exhibit']];assert parts,name
 return np.concatenate([np.asarray([ob.matrix_world@v.co for v in ob.data.vertices],dtype=float) for ob in parts])
def adaptive_platform(B,name,x,y,z,height,material,floor,opening,occupied,wood=False):
 points=actual_points(B,name);hull=MultiPoint(points[:,:2]).convex_hull;assert hull.area>.01;foot=None
 for margin in [.08,.05,.025,.01]:
  candidate=hull.buffer(margin,join_style=2)
  if hull.area/candidate.area>=.60:foot=candidate;break
 assert foot is not None and floor.contains(foot) and not foot.intersects(opening),'PLATFORM_NOT_SAFE_OR_SPECIMEN_OCCUPANCY_LT_HALF'
 assert all(foot.distance(p)>=1.2-1e-6 for p in occupied),'INDUSTRIAL_ACTUAL_ROUTE_LT1_2M';occupied.append(foot);G=Geometry()
 if wood:
  lo=foot.bounds
  for k,y0 in enumerate(np.linspace(lo[1]+.08,lo[3]-.08,3)):
   strip=foot.intersection(box(lo[0]-.1,y0-.07,lo[2]+.1,y0+.07))
   if not strip.is_empty:extrude(G,name+f'_LowDeck_Sleeper{k}',strip,z,z+height-.07,'plinth',material)
  extrude(G,name+'_LowDeck_Top',foot,z+height-.07,z+height,'plinth',material)
 else:extrude(G,name+'_LowDeck',foot,z,z+height,'plinth',material)
 for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],material,True,'plinth')
 return foot,{'display_support_kind':'low_platform','display_support_prefix':name+'_LowDeck','authored_actual_exhibit_hull_area_m2':hull.area,'authored_actual_platform_area_m2':foot.area,'authored_actual_platform_occupancy_fraction':hull.area/foot.area,'platform_footprint_wkt':foot.wkt,'platform_height_m':height,'platform_material':material,'actual_min_m':points.min(0).tolist(),'actual_max_m':points.max(0).tolist()}
def precision_case(B,name,x,y,z,height,hx,hy,case_height):
 for a in [-1,1]:
  for b in [-1,1]:B.box(name+f'_BenchLeg{a}_{b}',(x+a*(hx-.08)-.035,y+b*(hy-.07)-.035,z),(x+a*(hx-.08)+.035,y+b*(hy-.07)+.035,z+height),'IndustrialDeck',True,'exhibit_table')
 B.box(name+'_BenchTop',(x-hx,y-hy,z+height-.06),(x+hx,y+hy,z+height),'IndustrialTimber',True,'exhibit_table');top=z+height+case_height
 for j,(lo,hi) in enumerate([((x-hx-.01,y-hy-.01,z+height),(x-hx+.005,y+hy+.01,top)),((x+hx-.005,y-hy-.01,z+height),(x+hx+.01,y+hy+.01,top)),((x-hx+.005,y-hy-.01,z+height),(x+hx-.005,y-hy+.005,top)),((x-hx+.005,y+hy-.005,z+height),(x+hx-.005,y+hy+.01,top))]):B.box(name+f'_CaseGlass{j}',lo,hi,'Glass',True,'display_case')
 B.box(name+'_CaseRoof',(x-hx-.03,y-hy-.03,top),(x+hx+.03,y+hy+.03,top+.04),'IndustrialDeck',True,'display_case')

def exhibition(B,model,room):
 s=B.spec;H=s['floor_height_m'];w,h=model['building_xy_m'];pool=sources(B);groups=s['exhibition']['theme_source_groups'];used=collections.Counter();opening=wkt.loads(model['opening_wkt']).buffer(1.2);ships=[pool[k] for k in groups['ship']];patent=[q for q in pool.values() if 'abraham lincoln' in str(q.get('title','')).lower()];large=[q for q in pool.values() if any(x in str(q['id']).lower() for x in ['generator','drill_press'])];excluded={q['source']+':'+str(q['id']) for q in ships+patent+large};precision=[q for k,q in pool.items() if k not in excluded and 'bell x-1' not in str(q.get('title','')).lower()];assert len(ships)>=4 and patent and len(large)>=2 and len(precision)*3>=30
 for name,color,kind,rough,metal in [('IndustrialDeck',(.07,.09,.10),'stone',.48,.72),('IndustrialTimber',(.22,.12,.055),'long_board',.70,0),('IndustrialConcrete',(.29,.30,.28),'stone',.86,0)]:B.material(name,color,kind,1.2,rough,metal,source_hint='metal' if metal else 'concrete' if kind=='stone' else '')
 B.material('IndustrialSafety',(.8,.52,.02),'hazard_stripe',1.2,.75);zones=[]
 def select(rows):
  available=[q for q in rows if used[q['source']+':'+str(q['id'])]<3];assert available,'INDUSTRIAL11_LICENSED_MODEL_CAPACITY';available.sort(key=lambda q:(used[q['source']+':'+str(q['id'])],str(q['id'])));q=available[0];used[q['source']+':'+str(q['id'])]+=1;return q
 for level in range(s['floors']):
  z=level*H;floor=wkt.loads(model['floors'][level]['polygon_wkt']);internal_gallery_door(B,model,room,level);positions=[]
  for x in np.arange(-w/2+4,w/2-3,5.):
   for y in np.arange(-h/2+4,h/2-3,5.):
    footprint=box(x-1.6,y-1.3,x+1.6,y+1.3)
    if abs(x)<2.1 or abs(y-h*.4)<2.6 or not floor.contains(footprint) or footprint.intersects(opening):continue
    positions.append((float(x),float(y)))
  byzone={k:[] for k in ['naval','power','precision']}
  for x,y in positions:
   zone=('naval' if x<-11 else 'power' if x<11 else 'precision') if level==0 else ('naval' if x>11 else 'power' if x>-11 else 'precision') if level==1 else ('naval' if y>4 else 'precision' if y<-4 else 'power');byzone[zone].append((x,y))
  for zone in byzone:byzone[zone].sort(key=lambda p:((-1 if level%2 else 1)*p[1],p[0]))
  assert all(len(byzone[k])>=n for k,n in [('naval',6),('power',12),('precision',10)]),('INDUSTRIAL11_ZONE_CAPACITY',level,{k:len(v) for k,v in byzone.items()});occupied=[]
  for zone,count in [('naval',6),('power',12),('precision',10)]:
   footprints=[]
   for i,(x,y) in enumerate(byzone[zone][:count]):
    name=f'Dense_F{level+1}_{zone}_{i:02}';index=level*100+i;height=.38 if zone=='naval' and i else .20 if zone=='naval' else .16 if zone=='power' else [.72,.88,.78,.94,.68][i%5];material='IndustrialTimber' if zone=='naval' else 'IndustrialConcrete' if i%3==0 else 'IndustrialDeck'
    if zone=='power' and i<6:
     from museum_originals import engine
     engine(B,name,x,y,z+height,index);bpy.context.view_layer.update();pivot=Vector((x,y,z+height));matrix=Matrix.Translation(pivot)@Matrix.Scale(1.6,4)@Matrix.Translation(-pivot)
     for ob in B.scene.objects:
      if ob.type=='MESH' and ob.name.startswith(name+'_') and ob.get('museum_category')=='procedural_exhibit':ob.matrix_world=matrix@ob.matrix_world
     B.layout[-1]['teaching_engine_scale']=1.6
    elif zone=='power' and i<8:boiler(B,name,x,y,z+height,index)
    elif zone=='power' and i<10:gear(B,name,x,y,z+height,index)
    elif zone=='power' and i==11:boiler(B,name,x,y,z+height,index)
    elif zone=='naval' and i==0:propeller(B,name,x,y,z+height,index);material='IndustrialDeck'
    else:
     row=select([q for q in large if 'drill_press' in str(q['id'])] if zone=='power' else ships if zone=='naval' and i<5 else patent if zone=='naval' else precision);maxbox=(1.65,1.65,2.05) if zone=='power' else (3.1,1.9,2.5) if zone=='naval' else (.95,.75,.95)
     if zone=='precision':
      identifier=str(row['id']).lower();maxbox=(.65,.5,.45) if 'vice' in identifier else (1.2,.75,1.2) if 'cart' in identifier else (.55,.40,.22) if 'screwdriver' in identifier else (.55,.22,.10) if 'wrench' in identifier else (.45,.3,.35) if 'multimeter' in identifier else (.55,.45,.50) if 'camera' in identifier else (.70,.50,.60) if 'projector' in identifier else (.5,.45,.8) if 'microscope' in identifier else (.8,.65,.85)
     place(B,row,name,x,y,z+height,maxbox,yaw=math.atan2(-y,-x))
    row=B.layout[-1];row.update(floor=level+1,industrial_zone=zone)
    if zone=='precision':
     points=actual_points(B,name);extent=np.ptp(points,axis=0);hx=max(.35,float(extent[0])/2+.12);hy=max(.30,float(extent[1])/2+.12);case_height=max(.50,float(extent[2])+.16);foot=box(x-hx-.03,y-hy-.03,x+hx+.03,y+hy+.03);assert floor.contains(foot) and all(foot.distance(p)>=1.2-1e-6 for p in occupied);occupied.append(foot);precision_case(B,name,x,y,z,height,hx,hy,case_height);row.update(display_support_kind='glass_case',display_support_prefix=name+'_Bench',platform_height_m=height)
    else:
     foot,record=adaptive_platform(B,name,x,y,z,height,material,floor,opening,occupied,wood=zone=='naval' and i>0);row.update(record)
    footprints.append(foot)
   boundary=unary_union(footprints).convex_hull.buffer(.45).boundary.buffer(.055).intersection(floor).difference(opening);G=Geometry()
   if not boundary.is_empty:
    extrude(G,f'Dense_F{level+1}_{zone}_SafetyZone',boundary,z+.001,z+.003,'floor_marking','IndustrialSafety')
    for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],'IndustrialSafety',False,'floor_marking')
   zones.append({'floor':level+1,'zone':zone,'actual_exhibit_count':count,'grouped_grid_positions':byzone[zone][:count]})
 B.layout.append({'name':'M09_ADJUST11_dense_groups','kind':'exhibition_summary','zones':zones,'licensed_model_counts':dict(used),'per_floor':28,'source':'Original teaching assemblies and licensed cached scans/models. Small tools in glass cases; actual hull-fitted low decks for large displays. Export validator still required.'});return {'object_exhibits':28*s['floors'],'per_floor':28,'zones':zones,'source_counts':dict(used)}
