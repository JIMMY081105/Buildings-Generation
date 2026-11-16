"""Art Deco architecture and period curio cases, ADJUST12. No provenance inventions."""
import math,collections
import numpy as np
from shapely import wkt
from shapely.geometry import Point,Polygon,LineString,box
from shapely.ops import unary_union
from building_core import extrude
from staircase_core import Geometry

PALETTES={'M17':{'upper':(.73,.69,.57),'dark':(.018,.022,.021),'metal':(.60,.37,.10),'floor':(.64,.61,.52),'walnut':(.13,.061,.026),'name':'black and gold'},'M18':{'upper':(.61,.48,.24),'dark':(.045,.095,.14),'metal':(.57,.36,.10),'floor':(.36,.44,.50),'walnut':(.11,.076,.052),'name':'gold plaster with blue onyx and black marble'},'M19':{'upper':(.67,.71,.61),'dark':(.035,.16,.095),'metal':(.57,.36,.10),'floor':(.075,.23,.14),'walnut':(.14,.075,.031),'name':'green and gold'}}

def materials(B):
 bid=B.spec['building_id'];p=PALETTES[bid]
 B.material('DecoWalnut',p['walnut'],'walnut_veneer',1.2,.45)
 B.material('DecoDark',p['dark'],'stone',2.4,.37)
 B.material('DecoMetal',p['metal'],'stone',1.2,.31,.88)
 B.material('DecoCream',(.78,.74,.64),'ashlar',2.4,.72,source_hint='limestone')
 B.material('DecoMedallionLight',(.68,.65,.55),'stone',1.2,.42,source_hint='marble' if bid=='M19' and B.spec.get('deco31') else 'terrazzo')

def facade(B,model,shape,opening):
 s=B.spec;bid=s['building_id'];p=PALETTES[bid];H=s['floor_height_m'];Z=H*s['floors'];w,h=model['building_xy_m'];G=Geometry();doorcuts=[]
 for door in model['doorways']:
  c=np.array(door['center']);d=np.array(door['direction']);n=np.array([-d[1],d[0]]);half=door['clear_width_m']/2+.5;doorcuts.append(Polygon([c-d*half-n*1.4,c+d*half-n*1.4,c+d*half+n*1.4,c-d*half+n*1.4]))
 cuts=unary_union(doorcuts)
 # Broad stepped parapet plus a central raised ziggurat above the solid entrance bay.
 for i in range(3):
  outer=.48-i*.12;inner=.26-i*.12;poly=shape.buffer(outer,join_style=2).difference(shape.buffer(inner,join_style=2));extrude(G,f'Deco12_ParapetStep{i}',poly,Z+i*.28,Z+(i+1)*.28,'facade','DecoCream')
 for i,(sx,sy,zz) in enumerate([(9.2,6.3,1.6),(7.1,4.9,1.5),(5.2,3.5,1.4)]):
  z=Z+.12+sum([1.6,1.5,1.4][:i]);cy=-h/2+3.5;B.box(f'Deco12_CentralZiggurat{i}',(-sx/2,cy-sy/2,z),(sx/2,cy+sy/2,z+zz),'DecoCream',True,'facade');B.box(f'Deco12_CrownDarkFrieze{i}',(-sx/2-.025,cy-sy/2-.035,z+zz-.23),(sx/2+.025,cy-sy/2-.012,z+zz-.07),'DecoDark',False,'facade')
  for x in np.linspace(-sx/2+.25,sx/2-.25,9):B.box(f'Deco12_CrownMetalRib{i}_{x}',(x-.035,cy-sy/2-.075,z+.06),(x+.035,cy-sy/2-.04,z+zz+.06),'DecoMetal',False,'facade')
 # Connect real window bays with narrow vertical ribs and dark inter-floor spandrels.
 groups={}
 for win in model['windows']:groups.setdefault(tuple(np.round(win['center'],5)),[]).append(win)
 for j,wins in enumerate(groups.values()):
  wins.sort(key=lambda q:q['sill_m']);win=wins[0];c=np.array(win['center']);d=np.array(win['direction']);n=np.array([-d[1],d[0]]);out=-n if shape.contains(Point(c+n*.6)) else n;yaw=math.atan2(d[1],d[0]);ww=win['width_m']/2
  def piece(name,a,b,z0,z1,material,depth=.16,offset=.30):
   ob=B.box(name,(a,-depth/2,z0),(b,depth/2,z1),material,True,'facade');ob.location=(*(c+out*offset),0);ob.rotation_euler.z=yaw
  for sign in [-1,1]:
   xx=sign*(ww+.20);piece(f'Deco12_FlutedPilaster{j}_{sign}',xx-.14,xx+.14,.08,Z+.35,'DecoCream',.24,.32)
   for k in [-1,0,1]:piece(f'Deco12_PilasterGroove{j}_{sign}_{k}',xx+k*.065-.012,xx+k*.065+.012,.2,Z+.26,'DecoMetal',.025,.46)
   piece(f'Deco12_WindowBandJamb{j}_{sign}',sign*ww-.025,sign*ww+.025,.95,Z-.45,'DecoDark',.04,.26)
  for k,(a,b) in enumerate(zip(wins,wins[1:])):
   if b['sill_m']>a['top_m']:piece(f'Deco12_ContinuousSpandrel{j}_{k}',-ww,ww,a['top_m'],b['sill_m'],'DecoDark',.04,.255)
 # Recessed entrance jambs never occupy the actual doorway/robot approach.
 for j,door in enumerate(model['doorways']):
  c=np.array(door['center']);d=np.array(door['direction']);n=np.array([-d[1],d[0]]);out=-n if shape.contains(Point(c+n*.6)) else n;yaw=math.atan2(d[1],d[0]);half=door['clear_width_m']/2;top=door['height_m'];portal_top=H+.48
  for i in range(4):
   ww=half+.18+i*.19;dep=.22+i*.16;mat='DecoMetal' if i%2 else 'DecoDark'
   for sign in [-1,1]:
    ob=B.box(f'Deco12_RecessedPortal{j}_{i}_{sign}',(sign*ww-.065,-.08,0),(sign*ww+.065,.08,portal_top-i*.17),mat,True,'entrance');ob.location=(*(c+out*dep),0);ob.rotation_euler.z=yaw
   ob=B.box(f'Deco12_PortalLintel{j}_{i}',(-ww-.065,-.08,portal_top-i*.17),(ww+.065,.08,portal_top+.12-i*.17),mat,True,'entrance');ob.location=(*(c+out*dep),0);ob.rotation_euler.z=yaw
  if bid=='M19' and s.get('deco31'):
   # Full-height entrance axis, two structural ribs reaching the ziggurat.
   # The actual3.95m clear double doorway remains unobstructed below.
   for sign in [-1,1]:
    for k in range(3):
     xx=sign*(half+.82+k*.12);ob=B.box(f'Deco31_EntryAxisRib{j}_{sign}_{k}',(xx-.035,-.035,.05),(xx+.035,.035,Z+1.5-k*.30),'DecoMetal',True,'facade');ob.location=(*(c+out*(.55+k*.08)),0);ob.rotation_euler.z=yaw
   for k in range(3):
    ww=half+.94-k*.14;zz=Z+.25+k*.24;ob=B.box(f'Deco31_EntryAxisCrown{j}_{k}',(-ww,-.06,zz),(ww,.06,zz+.12),'DecoMetal',True,'facade');ob.location=(*(c+out*.62),0);ob.rotation_euler.z=yaw
   B.layout.append({'kind':'architecture','name':f'Deco31_EntryAxis{j}','actual_clear_door_height_m':top,'full_height_axis_m':Z+1.5,'scope':'Real narrow relief ribs flank clear doorway and reach crown, full-height facade composition; no claim that3.95m door itself is two storeys.'})
  center=c+out*.32;za=top+.20
  if s.get('deco24'):
   verts=[(*(center-out*.035),za)]+[(*(center-out*.035+d*(1.58*math.cos(a))),za+1.58*math.sin(a)) for a in np.linspace(0,math.pi,35)];B.mesh(f'Deco24_SunburstDarkBack{j}',verts,[tuple(range(len(verts)))],'DecoDark',False,'facade_relief')
  # Clearly visible raised metal fan, entirely over the real door head.
  for k,a in enumerate(np.linspace(.12,math.pi-.12,17)):
   rr=.78;u=np.array([-.16*math.cos(a),.16*math.sin(a)]) if s.get('deco24') else np.array([-rr*math.cos(a),rr*math.sin(a)]);v=np.array([-1.55*math.cos(a),1.55*math.sin(a)]) if s.get('deco24') else np.array([-1.18*rr*math.cos(a),1.18*rr*math.sin(a)]);aa=center+d*u[0];bb=center+d*v[0];B.tube(f'Deco12_EntranceSunburst{j}_{k}',[(*aa,za+u[1]),(*bb,za+v[1])],.028,'DecoMetal',False,'facade_relief')
 for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],q['material'],True,q['role'])
 B.layout.append({'name':'ADJUST12_actual_deco_facade','kind':'architecture','palette':p['name'],'central_crown_rise_m':4.62,'features':['ziggurat central tower','fluted pilasters','vertical connected window bands','recessed tall entrance composition','raised metal sunburst'],'door_safety':'Jambs placed outside actual clear opening; no full-height fin over entrance center; C2 still required'})

def interiors(B,model,shape,opening):
 s=B.spec;bid=s['building_id'];H=s['floor_height_m'];G=Geometry();cuts=[]
 for door in model['doorways']:
  c=np.array(door['center']);d=np.array(door['direction']);n=np.array([-d[1],d[0]]);half=door['clear_width_m']/2+.22;cuts.append(Polygon([c-d*half-n,c+d*half-n,c+d*half+n,c-d*half+n]))
 cuts=unary_union(cuts);panel=shape.buffer(-.205).difference(shape.buffer(-.27))
 for level in range(s['floors']):
  z=level*H;wallband=panel.difference(cuts) if level==0 else panel
  extrude(G,f'Deco12_WalnutWainscot_F{level+1}',wallband,z+.025,z+1.12,'wainscot','DecoWalnut')
  cap=shape.buffer(-.185).difference(shape.buffer(-.275));cap=cap.difference(cuts) if level==0 else cap;extrude(G,f'Deco12_WainscotMetalCap_F{level+1}',cap,z+1.12,z+1.155,'wainscot','DecoMetal')
  # Stepped upper cornices stay within the actual ceiling support polygon.
  ceiling=wkt.loads(model['floors'][level+1]['polygon_wkt']) if level<s['floors']-1 else shape.difference(opening)
  for j,width in enumerate([.18,.32,.46]):
   band=shape.buffer(-.22).difference(shape.buffer(-.22-width)).intersection(ceiling);extrude(G,f'Deco12_SteppedCeiling_F{level+1}_{j}',band,z+H-.30-j*.07,z+H-.23-j*.07,'ceiling_detail','DecoMetal' if j==1 else 'Ceiling')
  # Thin brass divisions on calm floors; no repeated black-white fan texture.
  floor=wkt.loads(model['floors'][level]['polygon_wkt'])
  for axis in [0,1]:
   for a in np.arange(-18,19,6):
    poly=(box(a-.009,-40,a+.009,40) if axis==0 else box(-40,a-.009,40,a+.009)).intersection(floor)
    if not poly.is_empty:extrude(G,f'Deco12_FloorBrassGrid_F{level+1}_{axis}_{a}',poly,z+.0006,z+.0014,'floor_inlay','DecoMetal')
 # Single 8m entry-hall medallion on ground, all upper floors remain calm.
 center=(0.,-10.5) if bid=='M17' else (0.,-9.) if bid=='M18' else (0.,0.);floor=wkt.loads(model['floors'][0]['polygon_wkt']);disk=Point(*center).buffer(4.,resolution=64);assert floor.contains(disk),'DECO_MEDALLION_OUTSIDE_FLOOR'
 for i in range(32):
  aa=i*math.tau/32;bb=(i+1)*math.tau/32;angles=np.linspace(aa,bb,6);poly=Polygon([center]+[(center[0]+3.87*math.cos(a),center[1]+3.87*math.sin(a)) for a in angles]);extrude(G,f'Deco12_SingleHallMedallionSector{i}',poly,.0015,.0025,'floor_inlay','DecoDark' if i%2 else 'DecoMedallionLight')
 ring=disk.difference(Point(*center).buffer(3.87,resolution=64));extrude(G,'Deco12_SingleMedallionBrassRim',ring,.0015,.003,'floor_inlay','DecoMetal')
 for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],q['material'],False,q['role'])
 B.layout.append({'kind':'architecture','name':'ADJUST12_deco_interior','walnut_panel_height_m':1.12,'grain':'vertical walnut veneer','single_medallion_diameter_m':8.,'single_medallion_center_xy_m':center,'upper_floor_medallions':0,'other_floors':'calm material and6m fine metal grid'})

def curio_case(B,name,x,y,z,hx,hy,base,height):
 top=z+base+height
 B.box(name+'_CaseWalnutBase',(x-hx,y-hy,z),(x+hx,y+hy,z+base),'DecoWalnut',True,'plinth')
 B.box(name+'_CaseMetalBase',(x-hx-.025,y-hy-.025,z+base-.045),(x+hx+.025,y+hy+.025,z+base),'DecoMetal',True,'display_case')
 for j,(lo,hi) in enumerate([((x-hx,y-hy,z+base),(x-hx+.012,y+hy,top)),((x+hx-.012,y-hy,z+base),(x+hx,y+hy,top)),((x-hx+.012,y-hy,z+base),(x+hx-.012,y-hy+.012,top)),((x-hx+.012,y+hy-.012,z+base),(x+hx-.012,y+hy,top))]):B.box(name+f'_CaseGlass{j}',lo,hi,'Glass',True,'display_case')
 for a in [-1,1]:
  for b in [-1,1]:B.box(name+f'_CaseBrassCorner{a}_{b}',(x+a*hx-.014,y+b*hy-.014,z+base),(x+a*hx+.014,y+b*hy+.014,top+.025),'DecoMetal',True,'display_case')
 B.box(name+'_CaseGlassRoof',(x-hx,y-hy,top),(x+hx,y+hy,top+.012),'Glass',True,'display_case')
 for lo,hi in [((x-hx-.02,y-hy-.02,top),(x+hx+.02,y-hy+.02,top+.035)),((x-hx-.02,y+hy-.02,top),(x+hx+.02,y+hy+.02,top+.035)),((x-hx-.02,y-hy,top),(x-hx+.02,y+hy,top+.035)),((x+hx-.02,y-hy,top),(x+hx+.02,y+hy,top+.035))]:B.box(name+'_CaseTopMetal'+str(len(B.scene.objects)),lo,hi,'DecoMetal',True,'display_case')

def curios(B,model,room):
 from museum_exhibits import sources,place,internal_gallery_door
 from museum_themes import display_positions,pottery
 pool=sources(B);groups=B.spec['exhibition']['theme_source_groups'];used=collections.Counter();H=B.spec['floor_height_m']
 def pick(category):
  keys=[k for k in groups[category] if used[k]<3];assert keys,('DECO_CURIO_CAPACITY',category);keys.sort(key=lambda k:(used[k],k));k=keys[0];used[k]+=1;return pool[k]
 for level in range(B.spec['floors']):
  z=level*H;internal_gallery_door(B,model,room,level);positions=display_positions(B.spec,model,level,B);assert len(positions)>=15
  for i,(x,y) in enumerate(positions[:15]):
   name=f'DecoCurio_F{level+1}_{i:02}';category='clock' if i==0 else 'antique_instrument' if i in [1,2] else 'vessel';base=.10 if category=='clock' else .78
   available=[k for k in groups[category] if used[k]<3]
   if available:
    row=pick(category);grand='grandfather' in str(row['id']);size=(.75,.60,2.4) if grand else (.70,.45,.70) if category=='clock' else (.72,.60,.85) if category=='antique_instrument' else (.72,.65,1.05);ob=place(B,row,name,x,y,z+base,size,math.atan2(-y,-x));lo,hi=B.bounds(ob)
   else:
    assert category=='vessel';pottery(B,name,x,y,z+base,level*15+i);ob=next(o for o in B.scene.objects if o.name==name);lo,hi=B.bounds(ob);grand=False
   entry=B.layout[-1];entry.update(floor=level+1,theme_category=category,period_selection='Cached period source model, or declared original wheel-thrown teaching study');hx=max(.40,(hi[0]-lo[0])/2+.10);hy=max(.35,(hi[1]-lo[1])/2+.10)
   if category=='clock' and grand:
    B.box(name+'_Plinth',(x-hx,y-hy,z),(x+hx,y+hy,z+base),'DecoDark',True,'plinth');entry['display_support_kind']='large_antique_direct_plinth'
   else:
    curio_case(B,name,x,y,z,hx,hy,base,max(.60,hi[2]-(z+base)+.15));entry['display_support_kind']='brass_edged_glass_case'
 return {'object_exhibits':15*B.spec['floors'],'source_counts':dict(used),'period_exclusions':['wall_clock','alarm_clock_01'],'small_items':'actual4 glass sides, glass top, brass edges, walnut base'}
