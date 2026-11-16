"""Gothic tracery, steep slate roofs, open cloister and source-backed medieval display."""
import math,collections,numpy as np,bpy
from shapely import wkt
from shapely.geometry import Point,Polygon,LineString,MultiPoint,box
from shapely.ops import triangulate
from building_core import extrude
from staircase_core import Geometry
from museum_pitched_roof import pitched_roof

def materials(B):
 if B.spec.get('gothic18'):
  from museum_gothic_towers import materials as materials18
  materials18(B)
 B.material('Ground',(.28,.29,.27),'stone',1.4,.93,source_hint='patterned_slate_tiles');B.material('UpperFloor',(.28,.29,.27),'stone',1.4,.92,source_hint='patterned_slate_tiles');B.material('RoofTile',(.095,.11,.12),'stone',1.8,.86,source_hint='slate');B.material('GothicOak',(.075,.04,.018),'walnut_veneer',1.2,.78);B.material('Plinth',(.31,.29,.25),'ashlar',1.2,.88,source_hint='limestone');B.material('GothicCourtCobble',(.32,.31,.27),'stone',1.5,.95,source_hint='cobblestone')

def arch_curve(half,height,spring):
 # Equilateral pointed arch constructed as two circular arcs, never a triangle.
 rise=height-spring;offset=(rise*rise-half*half)/(2*half);radius=half+offset;angle=math.atan2(rise,offset)
 left=[(offset+radius*math.cos(t),spring+radius*math.sin(t)) for t in np.linspace(math.pi,math.pi-angle,24)]
 right=[(-x,z) for x,z in reversed(left)];return left+right[1:]

def oriented(B,ob,c,d,n,z=0):ob.location=(*c,z);ob.rotation_euler.z=math.atan2(d[1],d[0])

def aperture(B,prefix,c,d,out,half,z0,height,material='WindowGlass',glazed=True,frame=.095,depth=.26):
 spring=height-half*1.4;arc=arch_curve(half,height,spring);poly=Polygon([(-half,0),(half,0),*reversed(arc)]);assert poly.is_valid,(prefix,poly.wkt);G=Geometry();infill=box(-half,0,half,height).difference(poly)
 if not infill.is_empty:
  extrude(G,prefix+'_StoneArchInfill',infill,-depth/2,depth/2,'facade','ExteriorStone')
  for q in G.parts:
   # extrude locally in XY then map its extrusion axis to wall-normal Y.
   pts=[(x,-zz,y) for x,y,zz in q['vertices']];ob=B.mesh(q['name'],pts,q['faces'],'ExteriorStone',True,'facade');oriented(B,ob,c,d,out,z0)
 points=[(-half,0,0),(-half,0,spring),*[(x,0,z) for x,z in arc],(half,0,0)];ob=B.tube(prefix+'_PointedStoneArch',points,frame,'ExteriorStone',True,'facade');oriented(B,ob,c,d,out,z0)
 if glazed:
  v=[(x,0,z) for x,z in poly.exterior.coords[:-1]];ob=B.mesh(prefix+'_PointedGlass',v,[tuple(range(len(v)))],material,False,'window_glass');oriented(B,ob,c,d,out,z0)
  for j,x in enumerate([-half*.36,half*.36]):
   ob=B.tube(prefix+f'_TraceryMullion{j}',[(x,-.025,.025),(x,-.025,spring-.12)],.060,'ExteriorStone',True,'window_frame');oriented(B,ob,c,d,out,z0)
  for j,sgn in enumerate([-1,1]):
   pts=[(sgn*half*.36+half*.32*math.cos(t),-.025,spring-.1+half*.32*math.sin(t)) for t in np.linspace(0,math.tau,40)];ob=B.tube(prefix+f'_TraceryTrefoil{j}',pts,.045,'ExteriorStone',True,'window_frame');oriented(B,ob,c,d,out,z0)

def architecture(B,model,shape,opening):
 s=B.spec;w,h=model['building_xy_m'];H=model['floor_height_m'];N=len(model['floors']);Z=N*H
 for ob in list(B.scene.objects):
  if ob.name.startswith(('WindowGlass_','Skylight_Glass','Skylight_Rib')):bpy.data.objects.remove(ob,do_unlink=True)
 for i,win in enumerate(model['windows']):
  c=np.array(win['center']);d=np.array(win['direction']);n=np.array([-d[1],d[0]]);out=-n if shape.contains(Point(c+n*.6)) else n;aperture(B,f'Gothic14_Window{i}',c+out*.025,d,out,win['width_m']/2,win['sill_m'],win['top_m']-win['sill_m'])
 for ri,ring in enumerate([shape.exterior,*shape.interiors]):
  if ri:continue
  distances=[];offset=0.
  coords=list(ring.coords)
  for a,b in zip(coords,coords[1:]):
   length=float(np.linalg.norm(np.array(b)-a));bays=max(1,int(length/5.2));bays+=1 if bays%2==0 else 0
   distances.extend(offset+length*j/bays+.04 for j in range(bays));offset+=length
  for j,dist in enumerate(distances):
   p=ring.interpolate(float(dist));q=ring.interpolate(float(min(dist+.1,ring.length)));d=np.array([q.x-p.x,q.y-p.y]);d/=max(np.linalg.norm(d),1e-9);n=np.array([-d[1],d[0]]);c=np.array([p.x,p.y]);out=-n if shape.contains(Point(c+n*.6)) else n
   if any(np.linalg.norm(c-np.array(door['center']))<3.0 for door in model['doorways']):continue
   if any(np.linalg.norm(c-np.array(win['center']))<win['width_m']/2+.36 for win in model['windows']):continue
   for k,zz in enumerate([0,Z*.38,Z*.70]):
    high=[Z*.38,Z*.70,Z*.94][k];projection=[.95,.75,.60][k];half=[.36,.31,.26][k];ob=B.box(f'Gothic14_Buttress{ri}_{j}_{k}',(-half,0,zz),(half,projection,high),'ExteriorStone',True,'facade');ob.location=(*c,0);ob.rotation_euler.z=math.atan2(-out[0],out[1])
 if shape.interiors:
  # A steep continuous hipped roof over the cloister wings, not one giant plane
  # spanning the courtyard. Height is distance from the actual eave boundary.
  outline=shape.buffer(.85,join_style=2);points=[tuple(q) for ring in [outline.exterior,*outline.interiors] for q in ring.coords[:-1]]
  for x in np.arange(outline.bounds[0],outline.bounds[2]+.25,1.0):
   for y in np.arange(outline.bounds[1],outline.bounds[3]+.25,1.0):
    if outline.contains(Point(x,y)):points.append((float(x),float(y)))
  vertices=[];faces=[];lookup={}
  for tri in triangulate(MultiPoint(points)):
   if not outline.covers(tri):continue
   face=[]
   for x,y in list(tri.exterior.coords)[:3]:
    key=(x,y)
    if key not in lookup:lookup[key]=len(vertices);vertices.append((x,y,Z+Point(x,y).distance(outline.boundary)))
    face.append(lookup[key])
   faces.append(face)
  B.mesh('Gothic14_SteepCloisterSlateRoof',vertices,faces,'RoofTile',False,'roof_detail')
 else:
  rise=h/2+.85;pitched_roof(B,model,shape,.85,Z+rise)
  for sign in [-1,1]:
   x=sign*w/2;B.mesh(f'Gothic14_Gable{sign}',[(x,-h/2,Z),(x,h/2,Z),(x,0,Z+rise)],[(0,1,2)],'ExteriorStone',False,'roof_detail');B.tube(f'Gothic14_GableRake{sign}',[(x,-h/2,Z+.08),(x,0,Z+rise+.08),(x,h/2,Z+.08)],.12,'ExteriorStone',False,'roof_detail')
 bpy.context.view_layer.update();roof_high=max(float(B.bounds(ob)[1][2]) for ob in B.scene.objects if ob.type=='MESH' and ob.name.startswith(('Gothic14_SteepCloisterSlateRoof','PitchedRoof_Clipped')));spire_top=roof_high+7.0;spire_base=spire_top-5.1
 # ADJUST18 replaces roof-mounted turrets with complete ground-up west towers.
 if s.get('gothic18'):
  from museum_gothic_towers import bell_towers
  bell_towers(B,model,roof_high)
 for sign in ([] if s.get('gothic18') else [-1,1]):
  x=sign*4.8;y=-h/2+.65;B.box(f'Gothic14_BellTurret{sign}',(x-.8,y-.8,Z-2.),(x+.8,y+.8,spire_base-.2),'ExteriorStone',True,'facade');B.box(f'Gothic14_TurretCornice{sign}',(x-.98,y-.98,spire_base-.3),(x+.98,y+.98,spire_base),'ExteriorStone',True,'facade');vertices=[(x+dx,y+dy,spire_base) for dx,dy in [(-1.0,-1.0),(1.0,-1.0),(1.0,1.0),(-1.0,1.0)]]+[(x,y,spire_top)];B.mesh(f'Gothic14_TurretSpire{sign}',vertices,[(0,1,4),(1,2,4),(2,3,4),(3,0,4)],'RoofTile',False,'roof_detail')
 for i,door in enumerate(model['doorways']):
  c=np.array(door['center']);d=np.array(door['direction']);n=np.array([-d[1],d[0]]);out=-n if shape.contains(Point(c+n*.6)) else n;cloister=door.get('kind')=='cloister_open_arch';half=door['clear_width_m']/2
  aperture(B,('Gothic14_Cloister' if cloister else 'Gothic14_Portal')+str(i),c+out*.04,d,out,half,0,door['height_m'],glazed=False,frame=.12 if cloister else .20,depth=.42)
  if not cloister:
   for k in range(2):aperture(B,f'Gothic14_RecessedPortal{i}_{k}',c+out*(.24+k*.18),d,out,half+.25+k*.2,0,door['height_m']+.45+k*.25,glazed=False,frame=.10,depth=.03)
   base=door['height_m']+.65;peak=base+7.0;rose_z=H+1.95;radius=1.10;triangle=Polygon([(-4.3,base),(4.3,base),(0,peak)]);circle=Point(0,rose_z).buffer(radius,resolution=64);assert triangle.contains(circle)
   G=Geometry();extrude(G,f'Gothic14_EntranceGable{i}',triangle.difference(circle),-.06,.06,'facade','ExteriorStone')
   for q in G.parts:
    ob=B.mesh(q['name'],[(x,-zz,z) for x,z,zz in q['vertices']],q['faces'],'ExteriorStone',True,'facade');oriented(B,ob,c+out*.70,d,out,0)
   for name,points,thick in [('GableRake',[(-4.3,0,base),(0,0,peak),(4.3,0,base)],.13),('RoseStoneRing',[(radius*math.cos(t),0,rose_z+radius*math.sin(t)) for t in np.linspace(0,math.tau,97)],.10)]:
    ob=B.tube(f'Gothic16_Entrance{i}_'+name,points,thick,'ExteriorStone',True,'facade');oriented(B,ob,c+out*.77,d,out,0)
   B.material('Gothic16RoseGlass',(.14,.25,.39),'stone',1.,.04,glass=True)
   v=[(radius*math.cos(t),0,rose_z+radius*math.sin(t)) for t in np.linspace(0,math.tau,96,endpoint=False)];ob=B.mesh(f'Gothic16_Entrance{i}_RoseGlass',v,[tuple(range(len(v)))],'Gothic16RoseGlass',False,'window_glass');oriented(B,ob,c+out*.78,d,out,0)
   for j,t in enumerate(np.linspace(0,math.tau,12,endpoint=False)):
    ob=B.tube(f'Gothic16_Entrance{i}_RoseSpoke{j}',[(0,0,rose_z),(radius*math.cos(t),0,rose_z+radius*math.sin(t))],.035,'ExteriorStone',True,'window_frame');oriented(B,ob,c+out*.80,d,out,0)
 # Honest trusses below each actual ceiling region; no members across stair holes.
 for level in range(N):
  z=(level+1)*H-(.32 if level<N-1 else .28);support=wkt.loads(model['floors'][level+1]['polygon_wkt']) if level<N-1 else shape.difference(opening.buffer(.65));support=support.buffer(-.24)
  for i,x in enumerate(np.arange(-w/2+2.5,w/2-1,5.0)):
   cut=support.intersection(LineString([(x,-h),(x,h)]));lines=list(cut.geoms) if hasattr(cut,'geoms') else [cut]
   for j,line in enumerate(lines):
    if line.geom_type!='LineString' or line.length<4:continue
    a,b=np.asarray(line.coords)[[0,-1]];mid=(a+b)/2;name=f'Gothic14_TimberTruss_F{level+1}_{i}_{j}';B.tube(name+'_Tie',[(*a,z-.70),(*b,z-.70)],.105,'GothicOak',False,'ceiling_truss');B.tube(name+'_RaisedChord',[(*a,z-.68),(*mid,z-.04),(*b,z-.68)],.12,'GothicOak',False,'ceiling_truss');B.tube(name+'_KingPost',[(*mid,z-.7),(*mid,z-.04)],.09,'GothicOak',False,'ceiling_truss')
 if shape.interiors:
  court=Polygon(shape.interiors[0]);G=Geometry();extrude(G,'Gothic14_CobbleCourtyard',court,-.008,.006,'site','GothicCourtCobble')
  for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],q['material'],False,q['role'])
 B.layout.append({'kind':'architecture','name':'Gothic14_actual_style','roof':'clipped steep slate roof preserving original courtyard hole','windows':'actual pointed glass opening plus stone infill and tracery, no Western window component','cloister_open_arches':sum(q.get('kind')=='cloister_open_arch' for q in model['doorways']),'ceiling':'timber ties, diagonal raised chords and king posts under actual support regions'})

def exhibition(B,model,room):
 from museum_exhibits import sources,place,internal_gallery_door
 from museum_themes import display_positions
 from museum_east_asian import horizontal_blade,fit_horizontal_length
 s=B.spec;pool=sources(B);keys=s['exhibition']['gothic14_fixed_sequence'];H=s['floor_height_m'];counts=collections.Counter()
 for level in range(s['floors']):
  z=level*H;internal_gallery_door(B,model,room,level);positions=display_positions(s,model,level,B);assert len(positions)>=15
  for i,((x,y),key) in enumerate(zip(positions[:15],keys)):
   row=pool[key];identifier=key.split(':')[-1];name=f'Gothic14_F{level+1}_{i:02}';weapon=identifier in ['ornate_medieval_mace','ornate_war_hammer','antique_estoc'];small=weapon or identifier in ['brass_goblets','brass_candleholders','wooden_candlestick','Lantern_01'];base=.76 if small else .16 if 'Bed' in identifier or 'Cabinet' in identifier else .42;yaw=math.atan2(-y,-x)
   if weapon:horizontal_blade(B,row)
   ob=place(B,row,name,x,y,z+base,(1.5,1.4,.20) if weapon else (1.5,1.4,1.8),yaw);entry=B.layout[-1]
   if weapon:lo,hi=fit_horizontal_length(B,ob,entry,1.35 if identifier=='antique_estoc' else .85)
   else:lo,hi=B.bounds(ob)
   hx=max(.25,(hi[0]-lo[0])/2+.09);hy=max(.22,(hi[1]-lo[1])/2+.09);support='GothicOak' if small or any(q in identifier for q in ['Bed','Cabinet','Commode','table','chest']) else 'Plinth';B.box(name+'_StoneOrOakPlinth',(x-hx,y-hy,z),(x+hx,y+hy,z+base),support,True,'plinth')
   if small:
    top=hi[2]+.13
    for j,(a,b) in enumerate([((x-hx,y-hy,z+base),(x-hx+.012,y+hy,top)),((x+hx-.012,y-hy,z+base),(x+hx,y+hy,top)),((x-hx,y-hy,z+base),(x+hx,y-hy+.012,top)),((x-hx,y+hy-.012,z+base),(x+hx,y+hy,top))]):B.box(name+f'_CaseGlass{j}',a,b,'Glass',True,'display_case')
    for a in [-1,1]:
     for b in [-1,1]:B.box(name+f'_OakCaseCorner{a}_{b}',(x+a*hx-.015,y+b*hy-.015,z+base),(x+a*hx+.015,y+b*hy+.015,top+.015),'GothicOak',True,'display_case')
    B.box(name+'_OakCaseTop',(x-hx-.02,y-hy-.02,top),(x+hx+.02,y+hy+.02,top+.025),'GothicOak',True,'display_case')
   entry.update(floor=level+1,theme_category='weapon_in_oak_glass_case' if weapon else 'medieval_religious_artifact',display_support_kind='dark_oak_glass_case' if small else 'honed_stone_or_dark_oak');counts[key]+=1
 return {'per_floor':15,'actual_source_counts':dict(counts),'theme':'Real CC0 Gothic religious sculpture, shield, mace, hammer, estoc, chest/furniture, candles and goblets'}
