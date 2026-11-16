"""Industrial halls: actual roof/daylight, supported services and zoned exhibits."""
import math,numpy as np
from shapely import wkt
from shapely.geometry import box,Point
from building_core import extrude
from staircase_core import Geometry
import collections,hashlib
from museum_exhibits import sources,place,internal_gallery_door

def exhibition(B,model,room):
 s=B.spec;H=s['floor_height_m'];w,h=model['building_xy_m'];pool=sources(B);groups=s['exhibition']['theme_source_groups'];used=collections.Counter();opening=wkt.loads(model['opening_wkt']).buffer(1.2);all_exhibits=[];zones=[]
 for name,color,kind,rough,metal in [('IndustrialDeck',(.07,.09,.10),'stone',.48,.72),('IndustrialTimber',(.22,.12,.055),'long_board',.70,0),('IndustrialConcrete',(.29,.30,.28),'stone',.86,0)]:B.material(name,color,kind,1.2,rough,metal,source_hint='metal' if metal else 'concrete' if kind=='stone' else '')
 B.material('IndustrialSafety',(.8,.52,.02),'hazard_stripe',1.2,.75)
 ships=[pool[k] for k in groups['ship']];machines=[q for q in pool.values() if any(x in str(q['id']).lower() for x in ['generator','drill_press','bench_vice','tool_cart'])];precision=[pool[k] for k in groups['science'] if not any(x in str(pool[k].get('title','')).lower() for x in ['bell x-1','abraham lincoln'])];patent=[q for q in pool.values() if 'abraham lincoln' in str(q.get('title','')).lower()];assert ships and machines and len(precision)>=5 and patent
 def select(rows):
  available=[q for q in rows if used[q['source']+':'+str(q['id'])]<3];assert available,'INDUSTRIAL_SOURCE_CAPACITY';available.sort(key=lambda q:(used[q['source']+':'+str(q['id'])],str(q['id'])));q=available[0];used[q['source']+':'+str(q['id'])]+=1;return q
 for level in range(s['floors']):
  z=level*H;internal_gallery_door(B,model,room,level);floor=wkt.loads(model['floors'][level]['polygon_wkt']);positions=[]
  for x in np.arange(-w/2+4,w/2-3,5.):
   for y in np.arange(-h/2+4,h/2-3,5.):
    footprint=box(x-1.6,y-1.3,x+1.6,y+1.3)
    if abs(x)<2.1 or abs(y-h*.4)<2.6 or not floor.contains(footprint) or footprint.intersects(opening):continue
    positions.append((float(x),float(y)))
  rng=np.random.default_rng(s['seed']+level*193);rng.shuffle(positions)
  byzone={}
  for x,y in positions:
   zone=('naval' if x<-11 else 'power' if x<11 else 'precision') if level==0 else ('naval' if x>11 else 'power' if x>-11 else 'precision') if level==1 else ('naval' if y>4 else 'precision' if y<-4 else 'power')
   byzone.setdefault(zone,[]).append((x,y))
  assert all(len(byzone.get(k,[]))>=n for k,n in [('naval',4),('power',6),('precision',5)]),('INDUSTRIAL_ZONE_CAPACITY',level,{k:len(v) for k,v in byzone.items()})
  occupied=[]
  for zone,n in [('naval',4),('power',6),('precision',5)]:
   selected=byzone[zone][:n];zone_footprints=[]
   for i,(x,y) in enumerate(selected):
    name=f'Industrial_F{level+1}_{zone}_{i:02}';material='IndustrialTimber' if zone=='naval' else 'IndustrialDeck' if zone=='power' else 'IndustrialTimber' if i%2 else 'IndustrialDeck';half=(1.48,.90) if zone=='naval' else (1.50,1.10) if zone=='power' else (1.00,.65);height=.38 if zone=='naval' else .16 if zone=='power' else [.72,.88,.78,.94,.68][i];foot=box(x-half[0],y-half[1],x+half[0],y+half[1]);assert floor.contains(foot) and all(foot.distance(p)>=1.2-1e-6 for p in occupied),'INDUSTRIAL_CLEAR_ROUTE_UNDER_1_2M';occupied.append(foot);zone_footprints.append(foot)
    if zone=='power' and i%3==0:material='IndustrialConcrete'
    if zone=='precision':
     for a in [-1,1]:
      for b in [-1,1]:B.box(name+f'_BenchLeg{a}_{b}',(x+a*(half[0]-.08)-.04,y+b*(half[1]-.08)-.04,z),(x+a*(half[0]-.08)+.04,y+b*(half[1]-.08)+.04,z+height),'IndustrialDeck',True,'exhibit_table')
     B.box(name+'_BenchTop',(x-half[0],y-half[1],z+height-.06),(x+half[0],y+half[1],z+height),material,True,'exhibit_table')
    elif zone=='naval':
     for k,dy in enumerate([-.65,0,.65]):B.box(name+f'_TimberSleeper{k}',(x-half[0],y+dy-.10,z),(x+half[0],y+dy+.10,z+height-.05),material,True,'plinth')
     B.box(name+'_TimberTop',(x-half[0],y-half[1],z+height-.05),(x+half[0],y+half[1],z+height),material,True,'plinth')
    else:B.box(name+'_LowMachinePlatform',(x-half[0],y-half[1],z),(x+half[0],y+half[1],z+height),'IndustrialConcrete' if i%3==0 else material,True,'plinth')
    if zone=='power' and i%2==0:
     from museum_originals import engine
     engine(B,name,x,y,z+height,level*15+i)
     import bpy
     from mathutils import Matrix,Vector
     bpy.context.view_layer.update();pivot=Vector((x,y,z+height));scale_matrix=Matrix.Translation(pivot)@Matrix.Scale(1.6,4)@Matrix.Translation(-pivot)
     for ob in B.scene.objects:
      if ob.type=='MESH' and ob.name.startswith(name+'_') and ob.get('museum_category')=='procedural_exhibit':
       ob.matrix_world=scale_matrix@ob.matrix_world
     B.layout[-1]['full_size_teaching_model_scale']=1.6
    else:
     row=select(ships if zone=='naval' and i<3 else patent if zone=='naval' else machines if zone=='power' else precision);maxbox=(2.6,1.45,2.2) if zone=='naval' else (1.4,1.0,1.25)
     if zone=='power':maxbox=(.95,.85,2.05) if 'drill_press' in row['id'] else (1.55,1.,1.65) if 'generator' in row['id'] else (.65,.50,.45) if 'vice' in row['id'] else (1.2,.75,1.2)
     place(B,row,name,x,y,z+height,maxbox,yaw=math.atan2(-y,-x))
    if zone=='power' and i%2==0:
     pieces=[ob for ob in B.scene.objects if ob.type=='MESH' and ob.name.startswith(name+'_') and ob.get('museum_category')=='procedural_exhibit'];bounds=[B.bounds(ob) for ob in pieces];lo=np.min([b[0] for b in bounds],axis=0);hi=np.max([b[1] for b in bounds],axis=0);assert lo[0]>=x-half[0] and hi[0]<=x+half[0] and lo[1]>=y-half[1] and hi[1]<=y+half[1] and lo[2]>=z+height-.02,('ENGINE_OUTSIDE_ACTUAL_PLATFORM',name,lo.tolist(),hi.tolist());B.layout[-1].update(actual_min_m=lo.tolist(),actual_max_m=hi.tolist(),support_embed_m=z+height-float(lo[2]),actual_platform_fit='PASS')
    B.layout[-1].update(floor=level+1,industrial_zone=zone,platform_material=material,platform_height_m=height,platform_footprint_m=[x-half[0],y-half[1],x+half[0],y+half[1]]);all_exhibits.append(name)
    if zone=='precision':
     top=z+height+1.40
     for j,(lo,hi) in enumerate([((x-1.01,y-.66,z+height),(x-.995,y+.66,top)),((x+.995,y-.66,z+height),(x+1.01,y+.66,top)),((x-.995,y-.66,z+height),(x+.995,y-.645,top)),((x-.995,y+.645,z+height),(x+.995,y+.66,top))]):B.box(name+f'_CaseGlass{j}',lo,hi,'Glass',True,'display_case')
     B.box(name+'_CaseRoof',(x-1.03,y-.68,top),(x+1.03,y+.68,top+.04),'IndustrialDeck',True,'display_case')
   from shapely.ops import unary_union
   boundary=unary_union(zone_footprints).convex_hull.buffer(.65).boundary.buffer(.065).intersection(floor).difference(opening);G=Geometry()
   if not boundary.is_empty:
    extrude(G,f'Industrial_F{level+1}_{zone}_SafetyZone',boundary,z+.001,z+.003,'floor_marking','IndustrialSafety')
    for p in G.parts:B.mesh(p['name'],p['vertices'],p['faces'],'IndustrialSafety',False,'floor_marking')
   zones.append({'floor':level+1,'zone':zone,'actual_exhibits':n,'platform_positions':selected,'visitor_spacing_min_m':1.2})
 B.layout.append({'name':'M09_actual_industrial_zones','kind':'exhibition_summary','zones':zones,'all_exhibits':all_exhibits,'licensed_model_counts':dict(used),'scope':'Actual placed meshes and spacing; image richness/major sources require actual image review'})
 return {'industrial_displays':len(all_exhibits),'per_floor':15,'zones':zones,'source_counts':dict(used)}

def ceilings(B,model):
 s=B.spec;H=s['floor_height_m'];w,h=model['building_xy_m'];N=s['floors'];G=Geometry();beam_count=0
 for level in range(N):
  Z=(level+1)*H;slab=wkt.loads(model['floors'][level+1]['polygon_wkt']) if level<N-1 else None
  if slab is not None:
   for i,x in enumerate(np.arange(-w/2+3,w/2,6)):
    p=box(x-.11,-h/2+.12,x+.11,h/2-.12).intersection(slab)
    if p.is_empty:continue
    extrude(G,f'Industrial_F{level+1}_CeilingBeam{i}',p,Z-.69,Z-.32,'beam','Steel');beam_count+=1
  for side in [-1,1]:
   y=side*h*.30;z=Z-.98;prefix=f'Industrial_F{level+1}_Service{side}'
   B.tube(prefix+'_Pipe',[(-w/2+.8,y,z),(w/2-.8,y,z)],.065,'Steel',True,'service_pipe')
   B.box(prefix+'_CableTray',(-w/2+.8,y+.22,z-.04),(w/2-.8,y+.52,z+.04),'Iron',True,'cable_tray')
   for i,x in enumerate(np.arange(-w/2+2,w/2,5.)):
    if slab is not None and not slab.contains(Point(x,y).buffer(.1)):continue
    if slab is not None:top=Z-.32
    else:
     bay=int((y+h/2)//6);start=-h/2+bay*6;end=min(start+6,h/2);top=Z+1.9*(y-start)/(end-start)
    B.tube(prefix+f'_Hanger{i}',[(x,y,z-.06),(x,y,top+.01)],.012,'Steel',True,'service_hanger')
    tray_top=top if slab is not None else Z+1.9*(y+.37-start)/(end-start)
    B.tube(prefix+f'_TrayHanger{i}',[(x,y+.37,z-.04),(x,y+.37,tray_top+.01)],.012,'Steel',True,'service_hanger')
    B.cylinder(prefix+f'_SpotHousing{i}',(x,y,z-.20),.075,.16,'Iron',True)
    B.area(prefix+f'_WarmSpot{i}',(x,y,z-.21),(x,y-side*3,level*H+1.0),90,.18,(1,.91,.78),3500)
 Z=N*H
 for j,x in enumerate(np.arange(-w/2+3,w/2,6.)):
  B.tube(f'Roof_Industrial_Truss{j}_LowerChord',[(x,-h/2-.08,Z-.35),(x,h/2+.08,Z-.35)],.06,'Steel',True,'roof_structure')
  for k,y0 in enumerate(np.arange(-h/2,h/2,6.)):
   y1=min(y0+6,h/2);mid=(y0+y1)/2;prefix=f'Roof_Industrial_Truss{j}_Bay{k}'
   B.tube(prefix+'_UpperChord',[(x,y0,Z),(x,y1,Z+1.9)],.055,'Steel',True,'roof_structure')
   for n,(y,top) in enumerate([(y0,Z),(y1,Z+1.9)]):B.tube(prefix+f'_Vertical{n}',[(x,y,Z-.35),(x,y,top)],.035,'Steel',True,'roof_structure')
   B.tube(prefix+'_DiagonalWeb',[(x,y0,Z-.35),(x,mid,Z+.95),(x,y1,Z-.35)],.028,'Steel',True,'roof_structure')
 for k,y0 in enumerate(np.arange(-h/2,h/2,6.)):
  y1=min(y0+6,h/2)
  for n,(y,z) in enumerate([(y0,Z),(y1,Z+1.9)]):B.tube(f'Roof_Industrial_Purlin{k}_{n}',[(-w/2-.05,y,z),(w/2+.05,y,z)],.045,'Steel',True,'roof_structure')
 for p in G.parts:B.mesh(p['name'],p['vertices'],p['faces'],'Steel',True,'beam')
 B.layout.append({'name':'M09_industrial_ceiling_and_sawtooth_daylight','kind':'architecture','lower_floor_beam_strips':beam_count,'lower_floor_beams_clipped_to_actual_slab_polygons':True,'pipes_and_trays':'Metric actual meshes, suspended by actual hanger rods to slab underside or sawtooth roof surface','top_roof':'Opaque RAW roof slab replaced by actual sloped steel sawtooth panels and vertical glass clerestories; all floor slabs retained','track_light_kelvin':3500,'status':'AUTHORED_REQUIRES_EXPORT_C4_AND_ACTUAL_VISUAL_CHECK'})
