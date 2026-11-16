"""Metric continuous runners and one boundary railing per gallery opening."""
import math,re
import numpy as np
from shapely.geometry import Polygon,Point,LineString,box
from shapely.ops import unary_union

def glass_panels(B,name,centers):
 centers=np.asarray(centers);distance=np.r_[0,np.cumsum(np.linalg.norm(np.diff(centers,axis=0),axis=1))];start=0;count=0
 for end in range(1,len(centers)):
  if distance[end]-distance[start]<1.05 and end<len(centers)-1:continue
  p=centers[start];q=centers[end];d=q-p;lat=np.cross(d,[0,0,1]);lat/=max(np.linalg.norm(lat),1e-9);v=[]
  for c in [p,q]:
   v.extend([c+lat*.009-[0,0,.99],c-lat*.009-[0,0,.99],c-lat*.009-[0,0,.055],c+lat*.009-[0,0,.055]])
  B.mesh(name+f'_GlassPanel{count}',v,[(3,2,1,0),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)],'Glass',True,'glass_guard')
  B.tube(name+f'_GlassPost{count}',[(p[0],p[1],p[2]-.99),p],.018,'Steel',True,'guard');start=end;count+=1

def gallery_guards(B,model,opening):
 from museum_rails import styled_infill,handrail_material
 H=B.spec['floor_height_m'];family=B.spec['style_family'];parts=model['parts']
 # Replace the horizontal landing-edge guards at gallery elevation. Flight
 # rails and all intermediate-platform guards keep their own actual geometry.
 removed=set()
 for row in parts:
  if not any(x in row['name'] for x in ['_Exit','_Entry_','_TopInnerGuard']):continue
  if any(x in row['name'] for x in ['Handrail','_Post']):removed.add(row['name'])
 for level in range(1,B.spec['floors']):
  z=level*H;profiles=[];mouths=[]
  for row in parts:
   v=np.asarray(row['vertices'])
   if row['role']=='tread' and abs(v[:,2].max()-z)<1e-7:
    top=[v[f,:2] for f in row['faces'] if (abs(v[f,2]-z)<1e-7).all()]
    mouths.extend(Polygon(p).buffer(.13) for p in top)
   if not any(k in row['name'] for k in ['Landing','Bridge','Platform']):continue
   for face in row['faces']:
    p=v[face]
    if len(face)>=3 and (abs(p[:,2]-z)<1e-6).all():
     q=Polygon(p[:,:2])
     if q.is_valid and q.area>1e-8:profiles.append(q)
  # The actual support union determines the opening, so adjacent platform and
  # gallery edges cannot produce competing rows of guard rails.
  from shapely import wkt
  gallery=wkt.loads(model['floors'][level]['polygon_wkt'])
  floor_opening=wkt.loads(model['floors'][level].get('opening_wkt') or model['opening_wkt'])
  void=floor_opening.difference(gallery.union(unary_union(profiles))).buffer(.045,join_style=2)
  polys=[void] if void.geom_type=='Polygon' else list(void.geoms)
  gaps=unary_union(mouths) if mouths else Polygon()
  for route in model['routes']:
   if 'start_z_m' in route and abs(route['start_z_m']-z)<1e-7:gaps=gaps.union(Point(route['xy'][0]).buffer(1.45))
  for pi,poly in enumerate(polys):
   if poly.area<.02:continue
   curves=LineString(poly.exterior.coords).difference(gaps)
   lines=[curves] if curves.geom_type=='LineString' else list(curves.geoms)
   for li,line in enumerate(lines):
    if line.length<.08:continue
    coords=list(line.coords);name=f'GalleryOpeningGuard_F{level+1}_{pi}_{li}'
    B.tube(name+'_Handrail',[(x,y,z+1.08) for x,y in coords],.035,handrail_material(B),True,'guard')
    if family in ['modern','marine']:
     coords2=[line.interpolate(t).coords[0] for t in np.linspace(0,line.length,math.ceil(line.length/.15)+1)];glass_panels(B,name,[(x,y,z+1.08) for x,y in coords2])
    elif not styled_infill(B,name,[(q.x,q.y,z+1.08) for q in [line.interpolate(t) for t in np.linspace(0,line.length,math.ceil(line.length/.09)+1)]]):
     for j,t in enumerate(np.linspace(0,line.length,math.ceil(line.length/.105)+1)):
      p=line.interpolate(t);B.tube(name+f'_Post{j}',[(p.x,p.y,z-.025),(p.x,p.y,z+1.08)],.013,'Wood' if family in ['nordic','east_asian'] else 'Iron',True,'guard')
    for j,p in enumerate([coords[0],coords[-1]]):B.box(name+f'_Newel{j}',(p[0]-.07,p[1]-.07,z-.03),(p[0]+.07,p[1]+.07,z+1.15),handrail_material(B),True,'newel')
 return removed

def runners_and_stringers(B,model):
 family=B.spec['style_family'];wine=B.spec['building_id']=='M01'
 for route in model['routes']:
  if 'count' not in route:continue
  name=route['name'];rows=[p for p in model['parts'] if p['name'].startswith(name+'_Tread')]
  if not rows:continue
  rows.sort(key=lambda p:p['name']);path=np.asarray(route['xy']);d=path[-1]-path[0];straight=np.max(np.linalg.norm(np.cross(np.c_[path-path[0],np.zeros(len(path))],np.r_[d/np.linalg.norm(d),0]),axis=1))<.01
  if straight and family in ['neoclassical','art_deco','mediterranean','gothic']:
   d=d/np.linalg.norm(d);lat=np.array([-d[1],d[0]]);v=np.asarray(rows[0]['vertices']);width=np.ptp(v[:,:2]@lat);z=route['start_z_m'];rise=route['rise_m'];going=route['going_m'];L=route['count']*going
   for sign in [-1,1]:
    a=path[0]+lat*sign*(width/2+.018);b=a+d*L
    for k,(offset,radius) in enumerate([(-.12,.035),(-.22,.022)]):B.tube(name+f'_StringerMould{sign}_{k}',[(*a,z+offset),(*b,z+route['count']*rise+offset)],radius,'StairTread',True,'stair_stringer')
  if not wine:continue
  verts=[];faces=[]
  for i,row in enumerate(rows):
   v=np.asarray(row['vertices']);z=v[:,2].max();top=next(v[f] for f in row['faces'] if (abs(v[f,2]-z)<1e-7).all());c=top[:,:2].mean(0)
   if not straight:continue
   xy=top[:,:2];across=(xy-c)@lat;xy=xy-lat[None,:]*across[:,None]*.50
   a=len(verts);verts.extend((*p,z+.006) for p in xy);faces.append(tuple(range(a,a+len(xy))))
   leading=[p-d*.006 for p in sorted(xy,key=lambda p:np.dot(p,d))[:2]]
   # Move the runner outward from the actual vertical stone riser as well as
   # upward from the tread: a Z-only offset leaves both risers coplanar.
   for j,q in enumerate(xy):
    if np.dot(q-c,d)<0:verts[a+j]=(*tuple(q-d*.006),z+.006)
   a=len(verts);prev=z-route['rise_m'];verts.extend([(*leading[0],prev+.006),(*leading[1],prev+.006),(*leading[1],z+.006),(*leading[0],z+.006)]);faces.append(tuple(range(a,a+4)))
   B.tube(name+f'_CarpetRod{i:02}',[(*p,prev+.020) for p in leading],.012,'Brass',False,'decor')
  if verts:B.mesh(name+'_ContinuousWineRunner',verts,faces,'Carpet',False,'carpet')
 # Landing strips join all three flight runners into a split continuous path.
 if wine:
  from shapely import affinity
  from building_core import extrude
  from staircase_core import Geometry
  for place in model['stair_placements']:
   z=(place['level']-1)*B.spec['floor_height_m']+16*.16
   p=unary_union([box(-.65,.12,.65,1.35),box(-3.05,.82,3.05,1.42)])
   p=affinity.rotate(p,math.degrees(place['angle_rad']),origin=(0,0));p=affinity.translate(p,*place['origin']);g=Geometry();extrude(g,f"WineMidLanding_F{place['level']}",p,z+.002,z+.006,'carpet','Carpet')
   for row in g.parts:B.mesh(row['name'],row['vertices'],row['faces'],'Carpet',False,'carpet')
