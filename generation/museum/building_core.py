"""Parametric museum shells and source-bound repeated stairs, from one spec."""
import math,json
from pathlib import Path
import numpy as np
from shapely.geometry import Polygon,Point,box
from shapely.geometry.polygon import orient
from shapely.ops import triangulate,unary_union
from shapely import affinity
from staircase_core import Geometry,build_stair
from stair_assemblies import apply_assembly

def extrude(G,name,poly,z0,z1,role='floor',material='floor'):
 if poly.geom_type=='MultiPolygon':
  return [extrude(G,name+f'_Part{i}',p,z0,z1,role,material) for i,p in enumerate(poly.geoms)]
 poly=orient(poly,sign=1);v=[];f=[];lookup={}
 def vi(x,y,z):
  k=(round(float(x),9),round(float(y),9),round(float(z),9))
  if k not in lookup:lookup[k]=len(v);v.append(k)
  return lookup[k]
 for p in triangulate(poly):
  if not poly.covers(p):continue
  xy=list(p.exterior.coords)[:3]
  if np.cross(np.asarray(xy[1])-xy[0],np.asarray(xy[2])-xy[0])<0:xy.reverse()
  f.append(tuple(vi(x,y,z1) for x,y in xy));f.append(tuple(vi(x,y,z0) for x,y in reversed(xy)))
 for ring in [poly.exterior,*poly.interiors]:
  xy=list(ring.coords)
  for a,b in zip(xy,xy[1:]):f.append((vi(*a,z0),vi(*b,z0),vi(*b,z1),vi(*a,z1)))
 return G.mesh(name,v,f,role=role,material=material)
def transform(data,origin=(0,0),angle=0,z=0,prefix='',mirror=False):
 c,s=math.cos(angle),math.sin(angle);R=np.array([[c,-s],[s,c]]);sign=-1 if mirror else 1
 def xy(v):
  p=np.array(v,dtype=float);p[0]*=sign;return (p@R.T+origin).tolist()
 def direction(v):
  p=np.array(v,dtype=float);p[0]*=sign;return (p@R.T).tolist()
 for p in data['parts']:
  v=np.asarray(p['vertices']);v[:,:2]=[xy(q) for q in v[:,:2]];v[:,2]+=z;p['vertices']=v.tolist();p['name']=prefix+p['name']
  if mirror:p['faces']=[list(reversed(f)) for f in p['faces']]
 data['treads']=[prefix+n for n in data['treads']]
 for e in data['exits']:
  e['start_xy']=xy(e['start_xy']);e['direction_xy']=direction(e['direction_xy']);e['floor_z_m']+=z;e['last_tread']=prefix+e['last_tread'];e['name']=prefix+e['name'];e['floor_prefix']=None;e['negative_translation_xyz']=[-.6*q for q in e['direction_xy']]+[0.]
 for route in data['routes']:
  route['xy']=[xy(p) for p in route['xy']];route['name']=prefix+route['name']
  if 'fixed_z_m' in route:route['fixed_z_m']+=z
  else:route['start_z_m']+=z
 return data
def footprint(spec):
 w,h=spec['building_xy_m'];plan=spec['plan'];bid=spec['building_id'];court=None
 if bid=='M02':w,h=44,40
 if '圆形' in plan or 'rotunda' in plan:
  P=Point(0,0).buffer(min(w,h)/2,resolution=8)
  if bid in ['M02','M13']:P=affinity.rotate(P,5.625,origin=(0,0))
  if '两翼' in plan:P=unary_union([P,box(-w/2,-7,-12,7),box(12,-7,w/2,7)])
 else:P=box(-w/2,-h/2,w/2,h/2)
 if 'L 形' in plan:P=P.difference(box(w*.08,h*.06,w/2+1,h/2+1))
 if '内院' in plan:
  court=box(0,-5.5,11,5.5);P=P.difference(court)
 return P,court,[w,h]
def build(spec):
 form=spec['stair']['form'];height=spec['floor_height_m'];floors=spec['floors'];P,court,size=footprint(spec);G=Geometry();parts=[];treads=[];exits=[];routes=[];unitrows=[]
 storey_shapes=[P]*floors
 if spec.get('brutalist26'):
  from museum_brutalist import storeys
  storey_shapes=storeys(spec,P)
 origin=(-9.5,0) if court is not None else (-6,0) if form=='A' else (0,0)
 if spec['building_id']=='M28' and spec.get('glasshouse19'):origin=tuple(spec['stair']['origin_xy_m'])
 if form=='H':opening=Point(*origin).buffer(5.2,resolution=96)
 elif form=='A':opening=Point(*origin).buffer(7.8,resolution=96)
 elif form=='I':opening=box(-8.8,-8.8,8.8,8.8)
 elif form=='W':opening=box(-5.6,-5.7,2.4,3.)
 else:opening=box(-5.6,-5.7,4.5,4.58)
 if form in 'IWC' and origin!=(0,0):opening=affinity.translate(opening,*origin)
 placements=[(origin,False)]
 if spec['building_id']=='M28' and spec.get('glasshouse19'):opening=unary_union([Point(0,0).buffer(spec['glasshouse19']['central_void_radius_m'],resolution=96),opening])
 if spec['building_id']=='M02':
  offset=spec['stair'].get('pair_center_offset_m',8.);placements=[((-offset,0),False),((offset,0),True)];opening=unary_union([Point(-offset,0).buffer(7.8,resolution=96),Point(offset,0).buffer(7.8,resolution=96)])
 for level in range(floors-1):
  for pi,(center,mirror) in enumerate(placements):
   actual_center=(center[0]+(spec['stair'].get('upper_group_offset_xy_m',[0,0])[0] if form=='I' and level%2 else 0),center[1]+(spec['stair'].get('upper_group_offset_xy_m',[0,0])[1] if form=='I' and level%2 else 0))
   unit=apply_assembly(build_stair(form,{'gallery_outward_exits':form=='I'},test_floor=False),spec)
   angle=level*(32*.32/2.9+math.radians(35)) if form=='H' else level*math.pi/2 if form=='I' else 0
   prefix=f'Stair_F{level+1}_P{pi}_';unit=transform(unit,actual_center,angle,level*height,prefix,mirror)
   for p in unit['parts']:
    p['level']=level+1;p['role']='stair_wall' if p['role']=='wall' else p['role'];parts.append(p)
   treads.extend(unit['treads']);routes.extend(unit['routes'])
   for e in unit['exits']:e['floor_index']=level+2;exits.append(e)
   unitrows.append({'level':level+1,'placement':pi,'origin':actual_center,'angle_rad':angle,'mirror':mirror,'form':form})
 # Upper-level access to a repeated non-spiral flight. H continues directly
 # from the preceding 35-degree landing into the next flight on the same axis.
 for level in range(1,floors-1):
  for pi,(center,mirror) in enumerate(placements):
   E=Geometry();z=level*height
   actual_center=(center[0]+(spec['stair'].get('upper_group_offset_xy_m',[0,0])[0] if form=='I' and level%2 else 0),center[1]+(spec['stair'].get('upper_group_offset_xy_m',[0,0])[1] if form=='I' and level%2 else 0))
   if form=='I':
    E.rect('EntryPlatform',(0,-8.8),(0,1),3.8,2.6,z-.23,z);E.landing_rails('Entry',(0,-8.8),(0,1),3.8,2.6,z)
    E.finish_joints([((-1.3,-5),z),((1.3,-5),z)])
   elif form=='W':E.rect('EntryPlatform',(-5.6,-4),(1,0),1.6,1.6,z-.23,z);E.landing_rails('Entry',(-5.6,-4),(1,0),1.6,1.6,z)
   elif form=='C':E.rect('EntryPlatform',(-5.6,-4),(1,0),1.6,2.2,z-.23,z);E.landing_rails('Entry',(-5.6,-4),(1,0),1.6,2.2,z,open_sides=(1,))
   elif form=='A':
    r0=4.5;rw=5.4;land=1.6/rw;E.sector('EntrySector',r0,6.3,-land,0,z-.23,z);d=np.array([math.cos(-land/2),math.sin(-land/2)]);E.rect('EntryBridge',d*rw,d,2.4,1.8,z-.23,z);E.merge_landing_bridge('EntrySector','EntryBridge','EntryPlatform');E.landing_rails('Entry',d*rw,d,2.4,1.8,z)
   else:continue
   dd={'parts':E.parts,'treads':[],'exits':[],'routes':[]};dd=transform(dd,actual_center,level*math.pi/2 if form=='I' else 0,0,f'Stair_F{level+1}_P{pi}_Entry_',mirror);parts.extend(dd['parts'])
 # Shared cantilever bearing walls are continuous solids, with no duplicated
 # coplanar surfaces between storeys.
 if form=='C':
  for wall in ['C_LoadWall1','C_LoadWall2']:
   selected=[p for p in parts if p['name'].endswith(wall)]
   if not selected:continue
   allv=np.concatenate([np.asarray(p['vertices']) for p in selected]);lo=allv.min(0);hi=allv.max(0);C=Geometry();C.box('Stair_'+wall+'_Continuous',lo,hi,role='stair_wall',material='stone');parts=[p for p in parts if not p['name'].endswith(wall)]+C.parts
 floorrows=[]
 for level in range(floors):
  shift=spec['stair'].get('upper_group_offset_xy_m',[0,0]) if form=='I' and level>0 and (level-1)%2 else [0,0];floor_opening=affinity.translate(opening,*shift)
  if spec['building_id']=='M04' and level>=2:floor_opening=unary_union([opening,floor_opening])
  outline=storey_shapes[level];z=level*height;floor=outline if level==0 else outline.difference(floor_opening)
  # Remove coplanar ownership overlap: platforms supply their own exact top.
  if level:
   profiles=[]
   for row in parts:
    v=np.asarray(row['vertices'])
    if abs(v[:,2].max()-z)>1e-7 or not any(x in row['name'] for x in ['Landing','Platform','Bridge','Tread32','Tread33']):continue
    for face in row['faces']:
     vv=v[face]
     if (abs(vv[:,2]-z)<1e-7).all() and len(face)>=3:
      q=Polygon(vv[:,:2])
      if q.is_valid and q.area>1e-10:profiles.append(q)
   if profiles:
    platforms=unary_union(profiles)
    # Fill unusable narrow slots between a bridge and the gallery with real
    # slab geometry. This removes paired guards by removing the actual gap.
    void=floor_opening.intersection(outline).difference(platforms)
    widevoid=void.buffer(-.30,join_style=2).buffer(.30,join_style=2)
    floor=outline.difference(widevoid).difference(platforms)
  assert floor.is_valid and floor.area>=600,f'FLOOR_AREA_INVALID {spec["building_id"]} {level+1} {floor.area}'
  extrude(G,f'Floor{level+1}_Slab',floor,z-.32,z,'floor','ground' if level==0 else 'upper_floor');floorrows.append({'floor_index':level+1,'z_m':z,'actual_floor_slab_area_m2':floor.area,'polygon_wkt':floor.wkt,'opening_wkt':floor_opening.wkt if level else None})
 # Actual openings in facade walls, rather than opaque overlays.
 doorways=[];windows=[];bounds=P.bounds;front=(bounds[1]);rings=[P.exterior,*P.interiors]
 for ri,ring in enumerate(rings):
  coords=list(ring.coords)
  modern_curved=spec['style_family']=='modern' and ri==0 and len(coords)>12
  for edge,(a,b) in enumerate(zip(coords,coords[1:])):
   a=np.array(a);b=np.array(b);length=np.linalg.norm(b-a);d=(b-a)/length;n=np.array([-d[1],d[0]]);family=spec['style_family'];bay_pitch=9. if family in ['modern','brutalist'] else 6. if family in ['industrial','marine','glasshouse','nordic'] else 5.2;bays=max(1,int(length/bay_pitch));bays += 1 if bays%2==0 else 0
   if family=='modern':bays=3 if length>35 else 1
   if spec.get('nordic28') and ri==0 and abs(a[1]-front)<.3 and abs(b[1]-front)<.3:bays=5 if length>35 else 3
   bay=length/bays
   for j in range(bays):
    c=a+d*(j+.5)*bay;courtyard_gate=bool(spec.get('east_asian13')) and ri>0 and abs(a[1]-ring.bounds[1])<1e-7 and abs(b[1]-ring.bounds[1])<1e-7 and j==bays//2;cloister_arch=bool(spec.get('gothic14')) and ri>0;entrance=(ri==0 and abs(c[1]-front)<.3 and abs(c[0])<bay*.55) or courtyard_gate
    opening_width=min(8.0 if entrance and family=='modern' else 3.2 if entrance else min(6.,bay*.48) if family=='modern' else bay*.72 if family in ['industrial','marine','glasshouse','nordic'] else 2.15,bay-.35)
    nordic_bay=bool(spec.get('nordic28') and entrance and ri==0)
    if nordic_bay:opening_width=min(7.6,bay-.35)
    if family=='gothic' and not entrance and not cloister_arch:opening_width=min(2.35,bay-.70)
    if cloister_arch:opening_width=bay-.70
    if modern_curved and not entrance:opening_width=bay-.12
    for level in range(floors):
     if modern_curved and not entrance and abs(c[1])<min(size)*.45:
      G.rect(f'Wall_{ri}_{edge}_{j}_F{level}_SolidWhite',a+d*j*bay,d,bay,.45,level*height,(level+1)*height,role='wall',material='wall');continue
     z=level*height;sill=0 if entrance and level==0 else 2.6 if family=='modern' else .4 if family in ['industrial','glasshouse','marine','nordic'] else 1.1;top=height-.4 if entrance and level==0 and family=='modern' else 3.2 if entrance and level==0 else sill+height*(.28 if family=='modern' else .70 if family in ['industrial','glasshouse','marine','nordic'] else .55);top=min(top,height-.35 if entrance and family=='modern' else height-.55);start=a+d*j*bay;left=(bay-opening_width)/2
     if family=='art_deco' and entrance and level==0:top=min(height-.55,spec.get('deco12',{}).get('main_door_height_m',3.95))
     if nordic_bay:sill=0.;top=height-.32
     if family=='gothic':
      if entrance and level==0:top=min(height-.55,4.15)
      else:sill=.70;top=height-.42
     if cloister_arch and level==0:sill=0;top=height-.55
     for name,offset,L,z0,z1 in [('PierL',0,left,0,height),('PierR',left+opening_width,left,0,height),('Below',left,opening_width,0,sill),('Above',left,opening_width,top,height)]:
      if L>1e-5 and z1-z0>1e-5:G.rect(f'Wall_{ri}_{edge}_{j}_F{level}_{name}',start+d*offset,d,L,.45,z+z0,z+z1,role='wall',material='wall')
     if nordic_bay and level==0:
      doorways.append({'center':c.tolist(),'direction':d.tolist(),'clear_width_m':3.2,'height_m':3.2,'z_m':z,'kind':'main_double'});sidewidth=(opening_width-3.2)/2
      for sign in [-1,1]:windows.append({'center':(c+d*sign*(1.6+sidewidth/2)).tolist(),'direction':d.tolist(),'width_m':sidewidth,'sill_m':z,'top_m':z+3.2,'nordic28_full_height_bay':True})
      windows.append({'center':c.tolist(),'direction':d.tolist(),'width_m':opening_width,'sill_m':z+3.2,'top_m':z+top,'nordic28_full_height_bay':True})
     elif (entrance or cloister_arch) and level==0:doorways.append({'center':c.tolist(),'direction':d.tolist(),'clear_width_m':opening_width,'height_m':top,'z_m':z,'kind':'cloister_open_arch' if cloister_arch else 'courtyard_double' if courtyard_gate else 'main_double'})
     else:windows.append({'center':c.tolist(),'direction':d.tolist(),'width_m':opening_width,'sill_m':z+sill,'top_m':z+top})
 if spec.get('brutalist26'):
  from museum_brutalist import shell
  G.parts=[p for p in G.parts if p['role']!='wall'];doorways,windows=shell(G,spec,storey_shapes)
 # A roof around an atrium/skylight; full stair/slab connections remain present.
 roof_void=Point(0,0).buffer(17.25,resolution=48) if spec['building_id']=='M02' else unary_union([opening,affinity.translate(opening,*spec['stair'].get('upper_group_offset_xy_m',[0,0]))]).buffer(.20) if spec['building_id']=='M04' else opening.buffer(.20);roof=P.difference(roof_void);extrude(G,'Roof_Slab',roof,floors*height-.28,floors*height,'roof','ceiling')
 parts=G.parts+parts
 for e in exits:e['floor_prefix']=f"Floor{e['floor_index']}_Slab"
 return {'schema':'Museum29BuildingMeshV1','building_id':spec['building_id'],'parts':parts,'treads':treads,'exits':exits,'routes':routes,'floor_height_m':height,'rise_m':spec['stair']['rise_m'],'floors':floorrows,'stair_placements':unitrows,'doorways':doorways,'windows':windows,'building_xy_m':size,'footprint_wkt':P.wkt,'storey_footprints_wkt':[p.wkt for p in storey_shapes],'opening_wkt':opening.wkt,'spec':spec}
if __name__=='__main__':
 import sys
 spec=json.loads(Path(sys.argv[1]).read_text());data=build(spec);Path(sys.argv[2]).write_text(json.dumps(data,ensure_ascii=False));print(spec['building_id'],len(data['parts']),len(data['treads']),[round(x['actual_floor_slab_area_m2'],2) for x in data['floors']])
