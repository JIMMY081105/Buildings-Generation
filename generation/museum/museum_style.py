"""Style-specific museum architecture, safe rail details and real factory parts."""
import math,json
import numpy as np
import bpy
from mathutils import Vector
from shapely import wkt
from shapely.geometry import Point,Polygon,LineString
from shapely.ops import unary_union

GROUND={
 'M01':((.76,.75,.68),'white_marble_inlay','marble',6.4), 'M02':((.60,.51,.39),'radial_deco','marble',3.2),
 'M03':((.57,.49,.35),'stone','travertine',1.),'M04':((.62,.39,.18),'star_tile','',3.2),
 'M05':((.34,.19,.075),'herringbone','',3.2),'M06':((.25,.27,.26),'stone','slate_floor',1.),
 'M07':((.31,.29,.22),'stone','stone_floor',1.),'M08':((.22,.24,.23),'stone','patterned_slate_tiles',1.),
 'M09':((.38,.39,.38),'stone','concrete',2.4),'M10':((.20,.11,.055),'plank','',3.2),
 'M11':((.36,.38,.40),'stone','Concrete002',2.4),'M12':((.72,.72,.68),'stone','terrazzo',1.),
 'M13':((.48,.48,.46),'stone','Concrete003',2.4),'M14':((.63,.59,.49),'stone','limestone',1.2),
 'M15':((.34,.34,.32),'stone','wood_textured_concrete',2.4),'M16':((.12,.13,.14),'stone','granite',.8),
 'M17':((.64,.62,.54),'radial_deco','terrazzo',3.2),'M18':((.57,.42,.20),'stone','onyx',.9),
 'M19':((.075,.23,.14),'stone','marble',.9),'M20':((.60,.43,.25),'herringbone','',3.2),
 'M21':((.62,.49,.30),'ash_plank','',3.2),'M22':((.43,.31,.14),'plank','',3.2),
 'M23':((.13,.085,.042),'plank','',3.2),'M24':((.57,.31,.15),'star_tile','',3.2),
 'M25':((.43,.16,.075),'star_tile','glazedterracotta',3.2),'M26':((.035,.14,.24),'mosaic','',3.2),
 'M27':((.18,.27,.32),'stone','slate_floor_03',1.),'M28':((.32,.115,.05),'brick','',2.3),'M29':((.30,.32,.29),'stone','slate_floor',1.)}

def materials(B):
 s=B.spec;bid=s['building_id'];family=s['style_family'];color,kind,hint,scale=GROUND[bid]
 if s.get('natural24'):
  m=B.material('Glass',(.75,.81,.82),'stone',2.4,.28);bs=m.node_tree.nodes['Principled BSDF'];bs.inputs['Alpha'].default_value=.018;bs.inputs['Transmission Weight'].default_value=0;m.diffuse_color=(.75,.81,.82,.018);B.material_records['Glass'].update(kind='thin_sheet_case_glass_alpha_approximation',opacity=.018,method='Explicit thin-sheet alpha without recursive zero-thickness refraction, all glass panes and exact collision proxies retained. Actual new native galleries must confirm specimen visibility.')
 if family=='east_asian' and s.get('east_asian13'):
  from museum_east_asian import materials as east_materials
  east_materials(B)
 if family=='gothic' and s.get('gothic14'):
  from museum_gothic import materials as gothic_materials
  gothic_materials(B)
 if family=='modern':
  B.material('ExteriorStone',(.83,.83,.81),'stone',1.2,.80,source_hint='plaster');B.material('Wall',(.80,.80,.77),'stone',2.4,.85,source_hint='plaster');B.material('Ceiling',(.81,.81,.79),'stone',2.4,.86,source_hint='plaster');B.material('Plinth',(.76,.76,.73),'stone',1.2,.73,source_hint='plaster')
  if bid=='M13':B.material('Iron',(.78,.79,.77),'stone',1.,.40,.12)
 if family=='brutalist':
  if s.get('brutalist26'):
   B.material('Wall',(.39,.40,.39),'board_concrete26',4.8,.86);B.material('ExteriorStone',(.39,.40,.39),'board_concrete26',4.8,.86);B.material('Brutal26Joint',(.075,.08,.075),'stone',1.2,.96)
  else:B.material('Wall',(.79,.80,.78),'stone',2.4,.88,source_hint='wood_textured_concrete');B.material('ExteriorStone',(.83,.83,.81),'stone',2.4,.88,source_hint='wood_textured_concrete')
 if bid=='M10':B.material('ExteriorStone',(.30,.085,.035) if s.get('industrial27') else (.92,.91,.90),'brick',[2.3,2.4],.84)
 if s.get('nordic28'):
  B.material('Wall',(.82,.81,.78),'stone',1.2,.93,source_hint='PaintedPlaster');B.material('ExteriorStone',(.27,.18,.10),'vertical_larch28',2.4,.82);B.material('Wood',(.57,.43,.25),'long_board',1.6,.58);B.material('Nordic28Platform',(.57,.43,.25),'ash_plank',3.2,.55)
 if family=='nordic':B.material('Plinth',(.57,.43,.25),'long_board',1.6,.55)
 if bid=='M05':B.material('Plinth',(.12,.065,.026),'wood',3.2,.58)
 if s.get('mediterranean32'):
  color=(.42,.16,.065);kind='cotto32';scale=2.4;hint='';B.material('Wall',(.80,.77,.70),'stone',1.2,.95,source_hint='plaster');B.material('ExteriorStone',(.82,.79,.72),'stone',1.2,.95,source_hint='plaster')
 if family=='mediterranean':B.material('Plinth',(.51,.39,.25),'stone',1.2,.76,source_hint='limestone')
 if family=='mediterranean':B.material('ExteriorStone',(.81,.78,.70),'stone',2.4,.90,source_hint='plaster')
 if family=='glasshouse':
  for name in ['Glass','WindowGlass']:
   glass_color=(.34,.62,.45) if s.get('glasshouse19') and name=='WindowGlass' else (.60,.75,.67) if s.get('glasshouse19') else (.75,.85,.88);opacity=(.21 if name=='WindowGlass' else .10) if s.get('glasshouse19') else .065;m=B.material(name,glass_color,'stone',2.4,.12 if s.get('glasshouse19') else .15,.22 if s.get('glasshouse19') and name=='WindowGlass' else 0);bs=m.node_tree.nodes['Principled BSDF'];bs.inputs['Alpha'].default_value=opacity;bs.inputs['Transmission Weight'].default_value=0;m.diffuse_color=(*glass_color,opacity);B.material_records[name].update(kind='thin_sheet_glass_opacity_approximation',opacity=opacity,method='Thin sheet alpha transparency with pale green visible pane reflection, no recursive zero-thickness refraction. Same declared opacity/color exported to USD; actual final roof visibility mandatory.')
 if family=='art_deco':
  from museum_deco import materials as deco_materials,PALETTES
  deco_materials(B);color=PALETTES[bid]['floor'];kind='stone';scale=1.8
  if s.get('deco24'):B.material('Plinth',PALETTES[bid]['walnut'],'walnut_veneer',1.2,.45)
 if bid=='M19' and s.get('deco31'):color=(.13,.068,.028);kind='herringbone';scale=3.2;hint=''
 if bid=='M02':kind='concentric_marble';scale=48.
 floor_roughness={'M01':.28,'M02':.30,'M03':.55,'M04':.35,'M05':.50,'M06':.85,'M07':.94,'M08':.86,'M09':.25,'M10':.78,'M11':.18,'M13':.23,'M15':.88,'M16':.35,'M22':.50,'M23':.58,'M24':.45,'M25':.85,'M27':.78,'M28':.85,'M29':.82}.get(bid,.62)
 if s.get('mediterranean32'):floor_roughness=.80
 B.material('Ground',color,kind,scale,floor_roughness,source_hint=hint)
 wood=family in ['neoclassical','victorian_natural','nordic','east_asian'] and bid not in ['M02','M04','M06']
 B.material('UpperFloor',(.35,.21,.10) if wood else color,'herringbone' if wood else kind,3.2 if wood else scale,.80 if s.get('mediterranean32') else .6,source_hint='' if wood else hint)
 wall={'neoclassical':(.035,.105,.075),'victorian_natural':(.46,.25,.10),'gothic':(.28,.26,.20),'industrial':(.33,.115,.06),'modern':(.72,.72,.68),'brutalist':(.32,.33,.31),'art_deco':(.46,.31,.11),'nordic':(.71,.69,.62),'east_asian':(.68,.66,.56),'mediterranean':(.72,.66,.52),'marine':(.008,.026,.054),'glasshouse':(.65,.77,.75)}[family]
 if bid=='M02':wall=(.66,.57,.42)
 if bid=='M03':wall=(.22,.025,.038)
 if bid=='M05':wall=(.12,.065,.026)
 if bid=='M12':wall=(.82,.82,.80)
 if family=='art_deco':wall=PALETTES[bid]['upper']
 brick=bid in ['M04','M08','M09','M10'];B.material('Wall',wall,'brick' if brick else 'stone',[2.3,2.4] if brick else 2.4,.85,source_hint='' if brick else 'plaster' if family not in ['brutalist','gothic'] else 'concrete' if family=='brutalist' else 'stone')
 if family=='art_deco':B.material('ExteriorStone',(.78,.74,.64),'ashlar',2.4,.72,source_hint='limestone');B.material('Brass',PALETTES[bid]['metal'],'stone',1.2,.31,.88,source_hint='metal')
 B.material('Ceiling',(.72,.68,.58) if family not in ['marine','industrial','brutalist'] else (.28,.30,.31),'stone',2.4,.85,source_hint='plaster' if family!='brutalist' else 'concrete')
 B.material('ExteriorStone',(.53,.37,.18) if bid=='M04' else (.83,.83,.81) if bid=='M12' else (.62,.55,.43) if family in ['neoclassical','art_deco','mediterranean'] else (.36,.12,.055) if family in ['industrial','victorian_natural'] else (.64,.65,.62) if family=='modern' else (.035,.11,.15) if family=='marine' else (.35,.24,.12) if family in ['nordic','east_asian'] else (.40,.38,.33),'brick' if family in ['industrial','victorian_natural'] else 'plank' if family in ['nordic','east_asian'] else 'ashlar' if family in ['neoclassical','gothic'] else 'stone',1.,.75,source_hint='brick' if family in ['industrial','victorian_natural'] else 'concrete' if family in ['brutalist','marine'] else 'limestone|sandstone|plaster')
 stairwood=bid in ['M05','M10','M11','M12','M20','M21','M22','M23'];treadcolor=(.56,.38,.18) if stairwood else (.60,.58,.49);treadkind='long_board' if stairwood else 'stone';treadscale=3.2 if stairwood else .8;treadmetal=0.;treadrough=.55;treadhint='' if stairwood else 'marble' if family in ['neoclassical','art_deco'] else 'concrete' if family=='brutalist' else 'limestone'
 if bid in ['M09','M28','M29']:treadcolor=(.16,.18,.19) if bid=='M09' else (.035,.045,.038);treadkind='steel_grating' if bid=='M09' else 'cast_iron_grating';treadscale=.96;treadmetal=.85;treadrough=.38 if bid=='M09' else .58;treadhint=''
 if bid=='M10':treadcolor=(.16,.18,.19);treadkind='stone';treadscale=.8;treadmetal=.85;treadrough=.38;treadhint='metal'
 if bid=='M02':treadhint='limestone';treadcolor=(.66,.61,.50)
 if bid in ['M07','M08']:treadcolor=(.36,.37,.35);treadhint='slate|stone_floor';treadrough=.9
 if family=='brutalist':treadcolor=(.42,.43,.42) if bid=='M15' else (.32,.33,.32);treadrough=.84
 if bid=='M18':treadcolor=(.028,.031,.033);treadrough=.28
 if bid=='M19':treadcolor=(.06,.18,.10);treadrough=.45
 if bid=='M24':treadcolor=(.48,.18,.075);treadhint='terracotta';treadrough=.8
 B.material('StairTread',treadcolor,treadkind,treadscale,treadrough,treadmetal,source_hint=treadhint)
 B.material('Iron',(.025,.033,.030),'stone',1.,.44,.65,source_hint='metal')
 B.material('Brass',(.56,.32,.075),'stone',.8,.32,.85,source_hint='metal')
 B.material('Steel',(.19,.23,.25),'stone',1.,.4,.8,source_hint='metal')
 B.material('Wood',(.16,.08,.035),'wood',3.2,.5)
 B.material('PaleWood',(.57,.38,.19),'wood',3.2,.52)
 B.material('Carpet',(.22,.012,.023),'stone',1.2,.97,source_hint='carpet')
 B.material('Glass',(.88,.94,.96),'stone',2.4,.04,glass=True)
 B.material('WindowGlass',(.55,.74,.82),'stone',2.4,.01,glass=True)
 B.material('WindowFrame',(.60,.64,.60),'stone',1.,.45,.12)
 B.material('Plaza',(.68,.70,.67) if bid=='M12' else (.28,.30,.29),'ashlar' if bid=='M12' else 'stone',1.2 if bid=='M12' else 1.5,.84 if bid=='M12' else .88,source_hint='limestone|stone_floor' if bid=='M12' else 'slate|stone_floor')
 B.material('Plinth',(.56,.53,.45) if family in ['neoclassical','art_deco'] else (.38,.40,.41),'stone',.8,.65,source_hint='marble' if family=='neoclassical' else 'concrete')
 if s.get('fossil37'):
  from museum_fossil_hall import materials as fossil_materials
  fossil_materials(B)

def architecture(B,model):
 s=B.spec;bid=s['building_id'];family=s['style_family'];height=s['floor_height_m'];floors=s['floors'];shape=wkt.loads(model['footprint_wkt']);opening=wkt.loads(model['opening_wkt']);w,h=model['building_xy_m']
 from museum_details import gallery_guards,runners_and_stringers,glass_panels
 from museum_rails import SPECIAL,styled_infill,handrail_material
 removed_guards=gallery_guards(B,model,opening)
 replacement_names={ob.name for ob in B.scene.objects if ob.type=='MESH' and ob.get('museum_collision') and 'GalleryOpeningGuard_' in ob.name}
 removed_originals=set(removed_guards)
 for row in model['parts']:
  if row['name'] in removed_guards:continue
  if family in ['modern','marine'] and '_Post' in row['name']:removed_originals.add(row['name']);continue
  if bid in SPECIAL and '_Post' in row['name']:removed_originals.add(row['name']);continue
  name=row['name'];role=row['role'];mat='Wall' if role in ['wall','stair_wall'] else 'Ground' if row['material']=='ground' else 'UpperFloor' if row['material']=='upper_floor' else 'Ceiling' if role=='roof' else 'StairTread' if role=='tread' or 'Landing' in name or 'Bridge' in name or 'Platform' in name else 'Iron' if row['material']=='metal' or role=='newel' else 'StairTread'
  if 'Handrail' in name:mat=handrail_material(B)
  if '_Post' in name and family in ['nordic','east_asian']:mat='Wood'
  if 'WallHandrail' in name:mat='PaleWood' if family in ['modern','nordic','east_asian'] else 'Steel'
  if s.get('gothic18') and mat=='StairTread' and role!='tread' and not any(k in name for k in ['Landing','Bridge','Platform']):mat='Gothic18GuardStone'
  if family=='glasshouse' and role=='roof':continue
  if bid=='M09' and role=='roof':removed_originals.add(name);continue
  vertices=np.array(row['vertices'],dtype=float)
  if family=='glasshouse' and role=='wall':
   d=vertices[3,:2]-vertices[0,:2];d/=np.linalg.norm(d);center=vertices[:,:2].mean(0);offset=(vertices[:,:2]-center)@d;vertices[:,:2]-=offset[:,None]*d*(1-.025/.45);mat='Glass'
  ob=B.mesh(name,vertices,row['faces'],mat,True,role)
  if s.get('gothic18') and role=='tread':
   from museum_gothic_towers import tread
   tread(B,ob,row,model)
  if bid in SPECIAL and 'Handrail' in name and any(p['name'].startswith(name.removesuffix('_Handrail')+'_Post') for p in model['parts']):
   assert len(vertices)%8==0
   before={p.name for p in B.scene.objects};styled_infill(B,name.removesuffix('_Handrail'),vertices.reshape(-1,8,3).mean(1),.95 if '_Rail' in name else 1.08);replacement_names.update(p.name for p in B.scene.objects if p.name not in before and p.type=='MESH' and p.get('museum_collision'))
  if family=='marine' and any(k in name for k in ['Landing','Bridge','Platform']):
   ob.data.materials.clear();ob.data.materials.append(B.materials['Ground']);ob.data.materials.append(B.materials['Steel'])
   for face in ob.data.polygons:face.material_index=0 if face.normal.z>.5 else 1
  if family in ['modern','marine'] and 'Handrail' in name and 'WallHandrail' not in name and len(vertices)%8==0:
   before={ob.name for ob in B.scene.objects};glass_panels(B,name,vertices.reshape(-1,8,3).mean(1));replacement_names.update(ob.name for ob in B.scene.objects if ob.name not in before and ob.type=='MESH' and ob.get('museum_collision'))
  if role=='floor':
   ob.data.materials.append(B.materials['Ceiling'])
   for face in ob.data.polygons:
    if face.normal.z<-.5:face.material_index=1
  if role=='wall' and family!='glasshouse':
   ob.data.materials.append(B.materials['ExteriorStone'])
   for face in ob.data.polygons:
    c=face.center+face.normal*.60
    if not shape.contains(Point(c.x,c.y)):face.material_index=1
  if False and bid=='M01' and role=='tread':
   # A real thin runner, with no coplanar overlap or approximate tread collider.
   z=vertices[:,2].max();up=[f for f in row['faces'] if all(abs(vertices[i,2]-z)<1e-7 for i in f)]
   if up:
    xy=vertices[list(up[0]),:2];center=xy.mean(0);dims=xy.max(0)-xy.min(0);axis=int(np.argmax(dims));xy[:,axis]=center[axis]+(xy[:,axis]-center[axis])*.36;verts=[(*q,z+.0015) for q in xy];B.mesh(name+'_WineRunner',verts,[list(range(len(verts)))],'Carpet',False,'decor')
 # Continuous protection around every upper opening, with true bridge gaps.
 for level in []:
  z=level*height;ring=opening.exterior if opening.geom_type=='Polygon' else None
  loops=[ring] if ring else [p.exterior for p in opening.geoms]
  gaps=[]
  for e in model['exits']:
   if e['floor_index']==level+1:
    end=np.array(e['start_xy'])+np.array(e['direction_xy'])*e['landing_length_m'];gaps.append((end,e['width_m']/2+.10))
  supports=[]
  for row in model['parts']:
   if not any(k in row['name'] for k in ['Landing','Bridge','Platform']):continue
   v=np.asarray(row['vertices'])
   for face in row['faces']:
    p=v[face]
    if len(face)>=3 and (abs(p[:,2]-z)<1e-6).all():
     poly=Polygon(p[:,:2])
     if poly.is_valid and poly.area>1e-8:supports.append(poly.buffer(.12))
  for ri,loop in enumerate(loops):
   coords=list(loop.coords)
   for ei,(a,b) in enumerate(zip(coords,coords[1:])):
    a=np.array(a);b=np.array(b);length=np.linalg.norm(b-a);points=[a+(b-a)*t for t in np.linspace(0,1,max(2,math.ceil(length/.085)+1))]
    for j,(p,q) in enumerate(zip(points,points[1:])):
     mid=(p+q)/2
     if any(np.linalg.norm(mid-c)<radius for c,radius in gaps):continue
     if any(poly.intersects(LineString([p,q])) for poly in supports):continue
     # The guard sits on the solid gallery edge outside the clear opening.
     v=mid-np.array(opening.centroid.coords[0]);v/=max(np.linalg.norm(v),1e-8);p=p+v*.055;q=q+v*.055
     B.tube(f'AtriumGuard_F{level+1}_{ri}_{ei}_{j}_Top',[(*p,z+1.08),(*q,z+1.08)],.025,'Brass' if family in ['neoclassical','art_deco'] else 'Steel',True,'guard')
     B.tube(f'AtriumGuard_F{level+1}_{ri}_{ei}_{j}_Post',[(*p,z-.025),(*p,z+1.08)],.012,'Iron' if family not in ['nordic','east_asian'] else 'Wood',True,'guard')
 # Windows made from the actual infinigen WindowFactory geometry.
 for i,win in enumerate(model['windows']):
  if s.get('fossil37'):
   from museum_fossil_hall import glazing
   glazing(B,win,i,model);continue
  if s.get('brutalist26'):
   from museum_brutalist import glazing
   glazing(B,win,i,model);continue
  c=win['center'];d=win['direction'];yaw=math.atan2(d[1],d[0]);H=win['top_m']-win['sill_m']
  if family not in ['modern','marine','neoclassical','east_asian','gothic'] and bid!='M04' and not s.get('natural24'):
   B.component('Window',f'Window_{i:03}','Wood' if family in ['nordic','east_asian'] else 'Iron',(c[0],c[1],win['sill_m']),(win['width_m'],.14,H),yaw,True)
  # Independent glass pane remains visible and portable after shader replacement.
  n=np.array([-d[1],d[0]]);center=np.array(c)+n*.02
  if bid!='M04' and not s.get('natural24'):g=B.box(f'WindowGlass_{i:03}',(-win['width_m']/2,-.012,0),(win['width_m']/2,.012,H),'WindowGlass',False,'window_glass');g.location=(center[0],center[1],win['sill_m']);g.rotation_euler.z=yaw
  if family in ['modern','marine']:
   ww=win['width_m']/2;pieces=[]
   for sign in [-1,1]:
    pieces.append(B.box(f'ThinWindowJamb_{i}_{sign}',(sign*ww-.025,-.045,0),(sign*ww+.025,.045,H),'Steel',True,'window_frame'))
    zz=0 if sign==-1 else H-.05;pieces.append(B.box(f'ThinWindowEdge_{i}_{sign}',(-ww,-.045,zz),(ww,.045,zz+.05),'Steel',True,'window_frame'))
   for ob in pieces:ob.location=(*c,win['sill_m']);ob.rotation_euler.z=yaw
  if family=='neoclassical':
   ww=win['width_m']/2;pieces=[]
   for sign in [-1,1]:
    pieces.append(B.box(f'SashJamb_{i}_{sign}',(sign*ww-.0375,-.10,0),(sign*ww+.0375,.10,H),'WindowFrame',True,'window_sash'))
    zz=0 if sign==-1 else H-.075;pieces.append(B.box(f'SashHorizontalEdge_{i}_{sign}',(-ww,-.10,zz),(ww,.10,zz+.075),'WindowFrame',True,'window_sash'))
   pieces.append(B.box(f'SashCentreMullion_{i}',(-.015,-.09,0),(.015,.09,H),'WindowFrame',True,'window_sash'))
   for j,zz in enumerate([H/3,H*2/3]):pieces.append(B.box(f'SashCrossbar_{i}_{j}',(-ww,-.09,zz-.015),(ww,.09,zz+.015),'WindowFrame',True,'window_sash'))
   for ob in pieces:ob.location=(*c,win['sill_m']);ob.rotation_euler.z=yaw
  if family in ['neoclassical','art_deco','victorian_natural'] and bid!='M04' and not s.get('natural24'):
   center=np.array(c)-n*.29;parts=[];ww=win['width_m']/2
   for sign in [-1,1]:parts.append(B.box(f'WindowStoneJamb_{i}_{sign}',(sign*ww-.06,-.08,0),(sign*ww+.06,.08,H),'ExteriorStone',True,'window_trim'))
   parts.append(B.box(f'WindowStoneHeader_{i}',(-ww-.14,-.09,H),(ww+.14,.09,H+.16),'ExteriorStone',True,'window_trim'))
   parts.append(B.box(f'WindowStoneSill_{i}',(-ww-.15,-.12,-.10),(ww+.15,.12,.015),'ExteriorStone',True,'window_trim'))
   for ob in parts:ob.location=(center[0],center[1],win['sill_m']);ob.rotation_euler.z=yaw
 # Main double doors: separate source leaves held open at exactly 90 degrees.
 for i,door in enumerate(model['doorways']):
  if door.get('kind')=='cloister_open_arch':continue
  c=np.array(door['center']);d=np.array(door['direction']);n=np.array([-d[1],d[0]]);yaw=math.atan2(d[1],d[0]);width=door['clear_width_m'];z=door['z_m'];H=door['height_m']
  if family=='modern' or s.get('industrial27'):
   outer=width;width=2.8;doorH=3.1
   def portal_box(name,x0,x1,z0,z1,material,depth=.07):
    ob=B.box(name,(x0,-depth/2,z0),(x1,depth/2,z1),material,True,'entrance');ob.location=(*c,z);ob.rotation_euler.z=yaw;return ob
   for sign in [-1,1]:
    x0,x1=sorted([sign*width/2,sign*outer/2]);portal_box(f'EntranceSideGlass{i}_{sign}',x0,x1,0,H,'WindowGlass',.024);portal_box(f'EntranceOuterJamb{i}_{sign}',sign*outer/2-.035,sign*outer/2+.035,0,H,'Steel')
   portal_box(f'EntranceTransomGlass{i}',-width/2,width/2,doorH,H,'WindowGlass',.024);portal_box(f'EntranceTopFrame{i}',-outer/2,outer/2,H-.06,H,'Steel')
   B.component('DoorCasing',f'Doorframe_Main{i}','Steel',(*c,z),(width+.16,.25,doorH+.10),yaw,True)
   for sign in [-1,1]:
    hinge=c+sign*d*(width/2-.035);leaf=hinge+n*(width/4);B.component('GlassDoor_Leaf',f'Door_Main{i}_Open{sign}','Steel',(*leaf,z),(width/2-.065,.08,doorH-.06),yaw+sign*math.pi/2,True)
   for sign in [-1,1]:
    p=c+sign*d*(outer/2+.35)-n*1.5;B.box(f'EntranceCanopyPost{i}_{sign}',(p[0]-.035,p[1]-.035,-.02),(p[0]+.035,p[1]+.035,3.55),'Steel',True,'entrance')
   ob=B.box(f'EntranceCanopy{i}',(-outer/2-.6,-2.0,3.48),(outer/2+.6,.25,3.58),'Steel' if s.get('industrial27') else 'WindowGlass',False,'entrance');ob.location=(*c,z);ob.rotation_euler.z=yaw
   for yy in [-2.0,.25]:
    ob=B.box(f'EntranceCanopyEdge{i}_{yy}',(-outer/2-.6,yy-.04,3.47),(outer/2+.6,yy+.04,3.60),'Steel',True,'entrance');ob.location=(*c,z);ob.rotation_euler.z=yaw
   B.layout.append({'name':f'MainDoor{i}','door_fixed_open_deg':90,'clear_opening_width_m':width-.12,'facade_glazed_portal_width_m':outer,'center_xy_m':c.tolist(),'source':'infinigen GlassDoor and DoorCasingFactory with steel glazed vestibule'});continue
  if family=='glasshouse' and s.get('glasshouse19'):
   B.component('DoorCasing',f'Doorframe_Main{i}','Iron',(*c,z),(width+.16,.20,H+.12),yaw,True);B.layout.append({'name':f'MainDoor{i}','clear_opening_width_m':width-.12,'center_xy_m':c.tolist(),'source':'Actual Infinigen DoorCasingFactory iron frame, open doorway, no door leaves or wood panel.'});continue
  B.component('DoorCasing',f'Doorframe_Main{i}','ExteriorStone' if family=='neoclassical' else 'Steel' if family=='marine' else 'Wood',(*c,z),(width+.16,.48,H+.12),yaw,True)
  for sign in [-1,1]:
   hinge=c+sign*d*(width/2-.035);center=hinge+n*(width/4);B.component('GlassDoor_Leaf' if family in ['modern','glasshouse','marine'] else 'PanelDoor_Leaf',f'Door_Main{i}_Open{sign}','Steel' if family=='marine' else 'Wood' if family not in ['modern','glasshouse'] else 'PaleWood',(*center,z),(width/2-.065,.12,H-.06),yaw+sign*math.pi/2,True)
  B.layout.append({'name':f'MainDoor{i}','door_fixed_open_deg':90,'clear_opening_width_m':width-.12,'center_xy_m':c.tolist(),'source':'infinigen panel/glass leaf and DoorCasingFactory'})
 # Classical columns and complete entablatures, rather than an unsupported canopy.
 if family in ['neoclassical','victorian_natural','art_deco']:
  def hall_column(name,x,y,z,clear_height):
   B.component('Pillar',name,'StairTread',(x,y,z),(.72,.72,clear_height),0,True)
   if family=='neoclassical':
    B.box(name+'_Plinth',(x-.48,y-.48,z),(x+.48,y+.48,z+.16),'StairTread',True,'column_base')
    for k,(rr,zz,th,rt) in enumerate([(.43,.16,.12,.37),(.37,.28,.10,.33),(.37,clear_height-.34,.13,.45),(.45,clear_height-.21,.11,.49)]):B.cylinder(name+f'_Torus{k}',(x,y,z+zz),rr,th,'StairTread',True,rt)
    B.box(name+'_Abacus',(x-.49,y-.49,z+clear_height-.10),(x+.49,y+.49,z+clear_height),'StairTread',True,'column_capital')
  for level in range(floors):
   z=level*height
   floorpoly=wkt.loads(model['floors'][level]['polygon_wkt'])
   for x,y in [(-w*.33,-h*.28),(w*.33,-h*.28),(-w*.33,h*.28),(w*.33,h*.28)]:
    if floorpoly.contains(Point(x,y).buffer(.72)):hall_column(f'Pillar_F{level+1}_{x}_{y}',x,y,z,height-.32)
  if family=='neoclassical' and s['stair']['form']=='I' and floors>=3:
   B.layout.append({'kind':'structural_explanation','object':'UpperStairEntry','description':'Upper I group moved along east gallery, with actual floor-specific opening; entry bears directly on floor2 gallery. No added columns at central stair foot; no floors hidden.','upper_offset_xy_m':s['stair'].get('upper_group_offset_xy_m',[0,0])})
  if family=='neoclassical' and bid!='M02':
   for x in [-5.4,-1.8,1.8,5.4]:B.component('Pillar',f'Portico_Column{x}','ExteriorStone',(x,-h/2-3.0,0),(.86,.86,5.1),0,True)
   for x in [-5.4,-1.8,1.8,5.4]:
    y=-h/2-3.;B.box(f'Portico_Plinth{x}',(x-.58,y-.58,0),(x+.58,y+.58,.18),'ExteriorStone',True)
    for k,(radius,z,thick,rtop) in enumerate([(.53,.18,.12,.47),(.46,.30,.10,.42),(.46,4.62,.14,.53),(.53,4.76,.16,.59)]):B.cylinder(f'Portico_Mould{x}_{k}',(x,y,z),radius,thick,'ExteriorStone',True,rtop)
    B.box(f'Portico_Abacus{x}',(x-.62,y-.62,4.92),(x+.62,y+.62,5.10),'ExteriorStone',True)
   B.box('Portico_Entablature',(-6.7,-h/2-3.8,5.1),(6.7,-h/2+0.1,5.72),'ExteriorStone',True)
   # Ridge runs perpendicular to the front facade; front triangular pediment.
   ridge=5.72+6.7*math.tan(math.radians(17));verts=[(-6.7,-h/2-3.9,5.72),(6.7,-h/2-3.9,5.72),(0,-h/2-3.9,ridge),(-6.7,-h/2-.15,5.72),(6.7,-h/2-.15,5.72),(0,-h/2-.15,ridge)];faces=[(0,2,1),(3,4,5),(0,1,4,3),(1,2,5,4),(2,0,3,5)];B.mesh('Portico_Pediment',verts,faces,'ExteriorStone',True)
 # Masonry courses, cornices and actual wood wainscot stop at open doorways.
 if family in ['neoclassical','victorian_natural','art_deco','mediterranean'] and bid!='M04':
  from building_core import extrude
  from staircase_core import Geometry
  G=Geometry();cuts=[]
  for door in model['doorways']:
   c=np.array(door['center']);d=np.array(door['direction']);n=np.array([-d[1],d[0]]);half=door['clear_width_m']/2+.24;cuts.append(Polygon([c-d*half-n*1.5,c+d*half-n*1.5,c+d*half+n*1.5,c-d*half+n*1.5]))
  openingcuts=unary_union(cuts)
  base=shape.buffer(.42).difference(shape.buffer(.22)).difference(openingcuts);extrude(G,'Exterior_BasePlinth',base,.02,.42,'architecture','ExteriorStone')
  for level in range(1,floors):extrude(G,f'Exterior_StringCourse{level}',shape.buffer(.38).difference(shape.buffer(.22)),level*height-.13,level*height+.10,'architecture','ExteriorStone')
  extrude(G,'Exterior_RoofCornice',shape.buffer(.52).difference(shape.buffer(.22)),floors*height-.22,floors*height+.14,'architecture','ExteriorStone')
  for level in ([] if family=='art_deco' else range(floors)):
   panel=shape.buffer(-.215).difference(shape.buffer(-.255))
   if level==0:panel=panel.difference(openingcuts)
   extrude(G,f'Wainscot_F{level+1}',panel,level*height+.035,level*height+.78,'architecture','Wood')
  for row in G.parts:B.mesh(row['name'],row['vertices'],row['faces'],row['material'],True,row['role'])
 # Maritime museum: real wood beams below each retained ceiling/slab. The
 # support polygon clips every beam away from the real stair/open atrium.
 if bid=='M25':
  from building_core import extrude
  from staircase_core import Geometry
  from shapely.geometry import box as beam_box
  beams=Geometry()
  for level in range(floors):
   underside=(level+1)*height-(.32 if level<floors-1 else .28)
   support=wkt.loads(model['floors'][level+1]['polygon_wkt']) if level<floors-1 else shape.difference(opening.buffer(.2))
   for i,y in enumerate(np.arange(-h/2+2,h/2-1,3.0)):
    footprint=support.intersection(beam_box(-w/2+.40,y-.12,w/2-.40,y+.12))
    if not footprint.is_empty:extrude(beams,f'Maritime29_WoodBeam_F{level+1}_{i}',footprint,underside-.26,underside,'ceiling_beam','Wood')
  for row in beams.parts:B.mesh(row['name'],row['vertices'],row['faces'],'Wood',True,'ceiling_beam')
  B.layout.append({'name':'Maritime29_real_wood_ceiling_beams','kind':'architecture','per_floor_real_beam_support_clipped':True,'beam_width_m':.24,'beam_drop_m':.26,'pitch_m':3.0,'collision':'Exact same authored wood mesh; fresh C1-C5 required.'})
 # Detailed ceiling treatment; only add where an actual gallery slab exists.
 if family in ['neoclassical','art_deco','brutalist','nordic','east_asian']:
  for level in range(floors):
   z=(level+1)*height-(.32 if level<floors-1 else .28)+.008
   ceiling_support=wkt.loads(model['floors'][level+1]['polygon_wkt']) if level<floors-1 else shape.difference(opening.buffer(.2))
   for x in np.arange(-w/2+1.5,w/2,3):
    for y in np.arange(-h/2+1.5,h/2,3):
     tile=Point(x,y).buffer(1.3,cap_style=3)
     if not ceiling_support.contains(tile):continue
     for axis in [0,1]:
      lo=[x-1.45,y-1.45,z-.15];hi=[x+1.45,y+1.45,z];lo[axis]=[x,y][axis]-1.42;hi[axis]=lo[axis]+.09;B.box(f'Coffer_F{level+1}_{x}_{y}_{axis}',lo,hi,'Wood' if family in ['nordic','east_asian'] else 'Ceiling',False,'decor')
 # A closed glass skylight and visible iron ribs over the central opening.
 if s.get('fossil37'):
  from museum_fossil_hall import roof
  roof(B,model)
 elif bid=='M04' or s.get('natural24'):
  from museum_victorian import roof
  roof(B,model)
 elif bid=='M02':
  from museum_dome import rotunda_dome
  rotunda_dome(B,model)
 elif family=='marine':
  z=floors*height;radius=5.4;rise=2.4;rings=12;segments=96;vertices=[(0,0,z+rise)];faces=[]
  for ri in range(1,rings+1):
   theta=ri/rings*math.pi/2
   for j in range(segments):
    a=j/segments*math.tau;vertices.append((radius*math.sin(theta)*math.cos(a),radius*math.sin(theta)*math.sin(a),z+rise*math.cos(theta)+.06))
  for j in range(segments):faces.append((0,1+j,1+(j+1)%segments))
  for ri in range(rings-1):
   a=1+ri*segments;b=a+segments
   for j in range(segments):k=(j+1)%segments;faces.append((a+j,b+j,b+k,a+k))
  B.mesh('Marine_CircularDomeSkylight',vertices,faces,'WindowGlass',False,'roof_glass')
  for j in range(12):
   a=j/12*math.tau;pts=[(radius*math.sin(t)*math.cos(a),radius*math.sin(t)*math.sin(a),z+rise*math.cos(t)+.075) for t in np.linspace(0,math.pi/2,40)];B.tube(f'Marine_DomeRib{j}',pts,.035,'Steel',False,'roof_detail')
 elif family not in ['glasshouse','east_asian','nordic','mediterranean'] and bid!='M09' and not s.get('industrial27'):
  roof_opening=opening.buffer(.20);polys=[roof_opening] if roof_opening.geom_type=='Polygon' else list(roof_opening.geoms);z=floors*height
  for pi,poly in enumerate(polys):
   cen=poly.centroid;coords=list(poly.exterior.coords)[:-1];apex=.08 if family in ['modern','art_deco'] else 1.8;v=[(cen.x,cen.y,z+apex)]+[(x,y,z+.06) for x,y in coords];faces=[(0,1+i,1+(i+1)%len(coords)) for i in range(len(coords))];B.mesh(f'Skylight_Glass{pi}',v,faces,'WindowGlass' if family=='art_deco' else 'Glass',False,'roof_glass')
   for j in np.linspace(0,len(coords)-1,min(12,len(coords)),dtype=int):
    x,y=coords[j];B.tube(f'Skylight_Rib{pi}_{j}',[(x,y,z+.06),(cen.x,cen.y,z+apex)],.035,'Iron',False,'decor')
 # Finite site, floor-level access and restrained practical museum lighting.
 classic=family=='neoclassical' and bid!='M02'
 if not classic:
  # A continuous flush exterior surface meets the actual polygonal floor edge.
  # Removing the building footprint avoids coincident plaza/floor top faces.
  from shapely.geometry import box as site_box
  from building_core import extrude
  from staircase_core import Geometry
  site_shape=wkt.loads(model['storey_footprints_wkt'][0]) if s.get('brutalist26') else shape;site=Geometry();extrude(site,'Site_Plaza',site_box(-w*2.,-h*2.,w*2.,h*2.).difference(site_shape),-.54 if s.get('brutalist26') else -.24,-.35 if s.get('brutalist26') else 0.,role='site',material='Plaza')
  for part in site.parts:B.mesh(part['name'],part['vertices'],part['faces'],'Plaza',True,'site')
 else:B.box('Site_Plaza',(-w*2.,-h*2.,-.72 if classic else -.24),(w*2.,h*2.,-.48 if classic else -.02),'Plaza',True,'site')
 if classic:
  B.box('Portico_Landing',(-7.,-h/2-3.9,-.48),(7.,-h/2+.22,0.),'ExteriorStone',True,'entrance')
  for k in range(3):B.box(f'Portico_ApproachStep{k}',(-7.6+k*.2,-h/2-5.7+k*.6,-.48),(7.6-k*.2,-h/2-3.9,-.32+k*.16),'ExteriorStone',True,'entrance')
 from museum_facades import facade_kit
 before_facade={ob.name for ob in B.scene.objects}
 facade_kit(B,model,shape,opening)
 if family=='art_deco':
  from museum_deco import interiors
  interiors(B,model,shape,opening)
  if s.get('deco24'):
   from museum_deco_atrium import interiors as visible_coves24
   visible_coves24(B,model,shape,opening)
 if bid=='M09':
  from museum_industrial import ceilings
  ceilings(B,model);replacement_names.update(ob.name for ob in B.scene.objects if ob.type=='MESH' and ob.get('museum_collision') and ob.name not in before_facade)
 runners_and_stringers(B,model)
 for level in range(floors):
  z=level*height
  for i,(x,y) in enumerate([(-w*.3,-h*.3),(w*.3,-h*.3),(-w*.3,h*.3),(w*.3,h*.3)]):
   if shape.contains(Point(x,y)) and not opening.contains(Point(x,y)):B.area(f'GalleryLight_F{level+1}_{i}',(x,y,z+height-.55),(x,y,z),500 if family!='marine' else 180,3.2,(1,.91,.78) if family in ['neoclassical','art_deco'] else (.65,.82,1) if family=='marine' else (1,1,1),s['lighting']['kelvin'])
   support=wkt.loads(model['floors'][level+1]['polygon_wkt']) if level<floors-1 else shape.difference(opening)
   if bid!='M09' and family!='glasshouse' and support.contains(Point(x,y).buffer(.55)):
    from museum_lighting import ceiling_fixture
    ceiling_fixture(B,f'PracticalLamp_F{level+1}_{i}',x,y,z+height-(.32 if level<floors-1 else .28),family,s['lighting']['kelvin'])
  B.area(f'AtriumFill_F{level+1}',(0,-h*.30,z+height-.6),(0,0,z+2),900 if family!='marine' else 700,5,(1,.94,.84) if family=='neoclassical' else (.65,.83,1) if family=='marine' else (1,1,1),s['lighting']['kelvin'])
 hdri=[x for k,x in B.library['assets'].items() if k.startswith('ph_hdris:')]
 if hdri:
  preferred=s['lighting']['hdri_id'] if s.get('new_daylight41') else 'kloofendal_43d_clear_puresky' if bid in ['M10','M12','M13','M14','M15','M16','M04'] else 'kloofendal_48d_partly_cloudy_puresky' if family=='marine' else 'rosendal_park_sunset_puresky' if family in ['neoclassical','victorian_natural','art_deco'] else 'kloofendal_43d_clear_puresky';row=next((x for x in hdri if x['id']==preferred),hdri[0]);path=row['paths'][0];B.scene['museum_hdri_source']=path;B.scene['museum_hdri_id']=row['id'];B.licenses['HDRI:'+row['id']]={k:row.get(k) for k in ['source','id','license','url']};nodes=B.scene.world.node_tree.nodes;tx=nodes.new('ShaderNodeTexEnvironment');tx.image=bpy.data.images.load(path);B.scene.world.node_tree.links.new(tx.outputs['Color'],nodes['Background'].inputs['Color'])
 (B.out/'ARCHITECTURE_REPLACEMENTS.json').write_text(json.dumps({'raw_source_names_replaced':sorted(n.replace('-','N') for n in removed_originals),'actual_enabled_replacement_meshes':sorted(replacement_names),'reason':'Single gallery-boundary guard replaces redundant landing guards; glass panel assemblies replace dense balusters where specified','source_model_canonical_json_sha256':__import__('hashlib').sha256(json.dumps(model,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()},indent=2))
 return {'shape':shape,'opening':opening,'size':[w,h]}
