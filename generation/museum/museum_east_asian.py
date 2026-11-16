"""ADJUST13: grey-tile deep eaves, white/timber halls and real Chinese curatorial sources."""
import math,collections,numpy as np,bpy
from mathutils import Vector,Matrix
from shapely import wkt
from shapely.geometry import Point,Polygon,box
from shapely.ops import unary_union
from building_core import extrude
from staircase_core import Geometry
from museum_pitched_roof import pitched_roof
from museum_exhibits import sources,place,internal_gallery_door,reserve_art,painting,import_model
from museum_themes import display_positions

def materials(B):
 dark=(.13,.060,.025) if B.spec['building_id']=='M22' else (.075,.060,.045);floor=(.52,.37,.21) if B.spec['building_id']=='M22' else (.28,.21,.14)
 for name in ['Ground','UpperFloor']:B.material(name,floor,'hinoki_plank',[3.6,4.8],.55)
 for name in ['Wall','ExteriorStone']:B.material(name,(.78,.76,.69) if B.spec['building_id']=='M22' else (.70,.67,.58),'stone',2.4,.84,source_hint='plaster')
 B.material('Wood',dark,'walnut_veneer',1.2,.52);B.material('Plinth',dark,'walnut_veneer',1.2,.52);B.material('EastDisplayWood',dark,'walnut_veneer',1.2,.48);B.material('StairTread',dark,'long_board',1.2,.50);B.material('RoofTile',(.11,.12,.115) if B.spec['building_id']=='M22' else (.08,.10,.12),'roof_tiles',3.,.84,source_hint='grey_roof_tiles');B.material('EastStoneBase',(.26,.27,.25),'ashlar',2.4,.85,source_hint='granite')

def facade(B,model,shape,opening):
 w,h=model['building_xy_m'];H=model['floor_height_m'];N=len(model['floors']);Z=N*H;G=Geometry();cuts=[]
 for door in model['doorways']:
  c=np.array(door['center']);d=np.array(door['direction']);n=np.array([-d[1],d[0]]);half=door['clear_width_m']/2+.25;cuts.append(Polygon([c-d*half-n,c+d*half-n,c+d*half+n,c-d*half+n]))
 cuts=unary_union(cuts);base=shape.buffer(.42,join_style=2).difference(shape.buffer(.20,join_style=2)).difference(cuts);extrude(G,'East13_StoneFoundation',base,.015,.37,'facade','EastStoneBase')
 for level in range(1,N+1):
  band=shape.buffer(.30,join_style=2).difference(shape.buffer(.18,join_style=2));extrude(G,f'East13_TimberStoreyBeam{level}',band,level*H-.22,level*H+.05,'facade','Wood')
 # Grid frames are authored at every actual window opening, including courtyard walls.
 for i,win in enumerate(model['windows']):
  c=np.array(win['center']);d=np.array(win['direction']);n=np.array([-d[1],d[0]]);out=-n if shape.contains(Point(c+n*.6)) else n;yaw=math.atan2(d[1],d[0]);ww=win['width_m']/2;hh=win['top_m']-win['sill_m'];parts=[]
  for sign in [-1,1]:parts.append(B.box(f'East13_WindowFrame{i}_{sign}',(sign*ww-.045,-.055,0),(sign*ww+.045,.055,hh),'Wood',True,'window_frame'))
  for j,z in enumerate([0,hh]):parts.append(B.box(f'East13_WindowEdge{i}_{j}',(-ww-.045,-.055,z-.045),(ww+.045,.055,z+.045),'Wood',True,'window_frame'))
  for j,x in enumerate(np.arange(-ww+.14,ww-.08,.18 if B.spec['building_id']=='M22' else .27)):parts.append(B.box(f'East13_LatticeVertical{i}_{j}',(x-.012,-.035,0),(x+.012,.035,hh),'Wood',True,'window_frame'))
  for j,z in enumerate(np.linspace(0,hh,5)[1:-1]):parts.append(B.box(f'East13_LatticeCrossbar{i}_{j}',(-ww,-.04,z-.016),(ww,.04,z+.016),'Wood',True,'window_frame'))
  for ob in parts:ob.location=(*(c+out*.25),win['sill_m']);ob.rotation_euler.z=yaw
 # Timber posts sit on real wall piers, not over the open entrance.
 for i,ring in enumerate([shape.exterior,*shape.interiors]):
  for j,dist in enumerate(np.arange(0,ring.length,4.2)):
   p=ring.interpolate(float(dist));p2=ring.interpolate(float(min(dist+.1,ring.length)));d=np.array([p2.x-p.x,p2.y-p.y]);d/=max(np.linalg.norm(d),1e-8);n=np.array([-d[1],d[0]]);c=np.array([p.x,p.y]);out=-n if shape.contains(Point(c+n*.5)) else n;pos=c+out*.27
   if any(q.intersects(Point(*pos).buffer(.16)) for q in ([cuts] if not cuts.is_empty else [])):continue
   B.box(f'East13_TimberPost{i}_{j}',(pos[0]-.08,pos[1]-.08,.37),(pos[0]+.08,pos[1]+.08,Z+.06),'Wood',True,'facade')
 pitched_roof(B,model,shape,2.2,Z+4.5)
 # Exposed eave rafters follow front/back walls and courtyard edges.
 rafters=0
 for ri,ring in enumerate([shape.exterior,*shape.interiors]):
  for j,dist in enumerate(np.arange(0,ring.length,.90)):
   p=ring.interpolate(float(dist));q=ring.interpolate(float(min(dist+.10,ring.length)));t=np.array([q.x-p.x,q.y-p.y]);t/=max(np.linalg.norm(t),1e-8);n=np.array([-t[1],t[0]]);c=np.array([p.x,p.y]);out=-n if shape.contains(Point(c+n*.5)) else n
   if abs(out[1])<.5:continue
   a=c-out*.45;b=c+out*2.2;zz=lambda y:Z+4.5*(1-abs(y)/(h/2+2.2))-.08;B.tube(f'East13_ExposedEaveRafter{ri}_{j}',[(*a,zz(a[1])),(*b,zz(b[1]))],.065,'Wood',False,'roof_detail');rafters+=1
 # Closed end prisms meet the actual roof at the exterior wall, including eaves rise.
 yb=h/2+.225;edge=Z+4.5*(1-yb/(h/2+2.2))-.025;profile=[(-yb,Z-.025),(yb,Z-.025),(yb,edge),(0,Z+4.5-.025),(-yb,edge)]
 for sign in [-1,1]:
  x=sign*(w/2+.225);v=[(xx,y,z) for xx in [x,x-sign*.18] for y,z in profile];outer=list(range(5)) if sign>0 else list(reversed(range(5)));faces=[outer,[j+5 for j in reversed(outer)]]+[[outer[j],outer[(j+1)%5],outer[(j+1)%5]+5,outer[j]+5] for j in range(5)];B.mesh('GableClosure32_East_'+('Left' if sign<0 else 'Right'),v,faces,'ExteriorStone',False,'roof_detail');B.tube(f'East13_GableTimberRake{sign}',[(x,-yb,edge-.10),(x,0,Z+4.5-.10),(x,yb,edge-.10)],.06,'Wood',False,'roof_detail')
 safe=opening.buffer(-.25);safe=safe if not safe.is_empty else opening;xmin,ymin,xmax,ymax=safe.bounds;seeds=[]
 from shapely.ops import nearest_points
 for i,(fx,fy) in enumerate([(.2,.2),(.8,.2),(.2,.8),(.8,.8)]):
  point=Point(xmin+(xmax-xmin)*fx,ymin+(ymax-ymin)*fy)
  if not safe.covers(point):point=nearest_points(safe,point)[0]
  seeds.append([point.x,point.y]);B.area(f'EastRoofUpwash33_{i}',(point.x,point.y,Z+.15),(point.x,point.y,Z+3.),600,2.4,(1.,.93,.82))
 B.layout.append({'name':'EastRoofLinerUpwash33','kind':'architecture','closed_gable_prisms':2,'gable_depth_m':.18,'actual_roof_liner_halves':2,'upwash_seeds_xy':seeds,'upwash_z_m':Z+.15,'upwash_count':4,'source_diagnosis':'Actual M23f1 B07 black roof underside and actual old gable triangle does not reach roof at eave ends. Visual-only roof detail above retained ceiling; new full source checks and native whole12 required.'})
 # Flush roofed portal: posts and plinths outside the real door approach.
 for i,door in enumerate(model['doorways']):
  if door.get('kind')=='courtyard_double':continue
  c=np.array(door['center']);d=np.array(door['direction']);n=np.array([-d[1],d[0]]);out=-n if shape.contains(Point(c+n*.6)) else n
  for sign in [-1,1]:
   for j,depth in enumerate([.5,3.2]):
    p=c+d*sign*3.+out*depth;B.box(f'East13_PorchStoneFoot{i}_{sign}_{j}',(p[0]-.23,p[1]-.23,0),(p[0]+.23,p[1]+.23,.18),'EastStoneBase',True,'entrance');B.box(f'East13_PorchWoodPost{i}_{sign}_{j}',(p[0]-.105,p[1]-.105,.18),(p[0]+.105,p[1]+.105,3.55),'Wood',True,'entrance')
  for sign in [-1,1]:
   p=c+d*sign*3.;B.tube(f'East13_PorchBeam{i}_{sign}',[(*(p+out*.2),3.55),(*(p+out*3.6),3.55)],.12,'Wood',True,'entrance')
  p=c+out*1.85
  for sign in [-1,1]:
   vertices=[(*tuple(p+d*-3.6+out*(sign*2.)),3.55),(*tuple(p+d*3.6+out*(sign*2.)),3.55),(*tuple(p+d*3.6),4.65),(*tuple(p+d*-3.6),4.65)];B.mesh(f'East13_PorchTileRoof{i}_{sign}',vertices,[(0,1,2,3)],'RoofTile',False,'roof_detail')
  for j,x in enumerate(np.arange(-3.4,3.5,.7)):
   for sign in [-1,1]:B.tube(f'East13_PorchRafter{i}_{j}_{sign}',[(*tuple(p+d*x),4.58),(*tuple(p+d*x+out*sign*2.0),3.48)],.055,'Wood',False,'roof_detail')
 for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],q['material'],True,q['role'])
 B.layout.append({'kind':'architecture','name':'East13_authored_architecture','main_eaves_m':2.2,'exposed_rafters':rafters,'wall_system':'white plaster and dark timber posts/storey beams, stone foundation','windows':'real vertical wood grille with horizontal crossbars, exterior and courtyard','porch':'stone feet, timber posts and beams, tiled roof, flush access','actual_courtyard_doors':sum(q.get('kind')=='courtyard_double' for q in model['doorways'])})

def wooden_case(B,name,x,y,z,hx,hy,base,height):
 B.box(name+'_WoodPlinth',(x-hx,y-hy,z),(x+hx,y+hy,z+base),'EastDisplayWood',True,'plinth');top=z+base+height
 for j,(lo,hi) in enumerate([((x-hx,y-hy,z+base),(x-hx+.012,y+hy,top)),((x+hx-.012,y-hy,z+base),(x+hx,y+hy,top)),((x-hx+.012,y-hy,z+base),(x+hx-.012,y-hy+.012,top)),((x-hx+.012,y+hy-.012,z+base),(x+hx-.012,y+hy,top))]):B.box(name+f'_CaseGlass{j}',lo,hi,'Glass',True,'display_case')
 for a in [-1,1]:
  for b in [-1,1]:B.box(name+f'_CaseWoodCorner{a}_{b}',(x+a*hx-.018,y+b*hy-.018,z+base),(x+a*hx+.018,y+b*hy+.018,top+.025),'EastDisplayWood',True,'display_case')
 B.box(name+'_CaseWoodRoof',(x-hx-.025,y-hy-.025,top),(x+hx+.025,y+hy+.025,top+.035),'EastDisplayWood',True,'display_case')

def horizontal_blade(B,row):
 ob=import_model(B,row)
 if ob.data.get('east13_horizontal_pose'):return
 points=np.asarray([v.co[:] for v in ob.data.vertices]);_,axes=np.linalg.eigh(np.cov(points.T));long=axes[:,-1];mid=axes[:,-2];thin=np.cross(long,mid);R=np.stack([long,mid,thin]);transformed=points@R.T
 for v,p in zip(ob.data.vertices,transformed):v.co=Vector(p)
 lo=transformed.min(0);hi=transformed.max(0);pivot=(lo+hi)/2;pivot[2]=lo[2]
 for v in ob.data.vertices:v.co-=Vector(pivot)
 ob.data['east13_horizontal_pose']=True

def fit_horizontal_length(B,ob,metadata,target):
 bpy.context.view_layer.update();p=np.asarray([ob.matrix_world@v.co for v in ob.data.vertices]);_,axes=np.linalg.eigh(np.cov(p[:,:2].T));length=float(np.ptp(p[:,:2]@axes[:,-1]));ratio=target/length;ob.scale*=ratio;lo,hi=B.bounds(ob);metadata.update(actual_min_m=lo.tolist(),actual_max_m=hi.tolist(),scale=float(metadata['scale']*ratio),display_horizontal_length_m=target);return lo,hi

def exhibition(B,model,room):
 s=B.spec;bid=s['building_id'];H=s['floor_height_m'];w,h=model['building_xy_m'];pool=sources(B);art=reserve_art(B,4*s['floors']);artindex=0;furniture=['chinese_cabinet','chinese_armchair','chinese_console_table','chinese_tea_table','chinese_stool'];ceramics=['antique_ceramic_vase_01','ceramic_vase_01','ceramic_vase_02','ceramic_vase_03','ceramic_vase_04'];counts=collections.Counter()
 for level in range(s['floors']):
  z=level*H;internal_gallery_door(B,model,room,level);positions=display_positions(s,model,level,B);assert len(positions)>=15,('EAST13_SAFE_DISPLAY_CAPACITY',bid,level,len(positions));sequence=furniture+['chinese_screen_panels']+ceramics+['tea_set_01','antique_katana_01','carved_wooden_elephant','brass_diya_lantern'] if bid=='M22' else (['chinese_tea_table','chinese_stool','chinese_cabinet'] if level==0 else ['chinese_armchair','chinese_console_table','chinese_tea_table'])+['chinese_screen_panels','tea_set_01']+ceramics+['wooden_bowl_02','carved_wooden_plate','carved_wooden_elephant','brass_diya_lantern','antique_katana_01'];assert len(sequence)==15
  for i,((x,y),identifier) in enumerate(zip(positions[:15],sequence)):
   name=f'East13_F{level+1}_{i:02}';row=pool['Poly Haven:'+identifier];large=identifier in furniture+['chinese_screen_panels'];base=.06 if large else .75;yaw=math.atan2(-y,-x);size=(1.7,1.1,2.05) if 'cabinet' in identifier or 'screen' in identifier else (1.65,1.0,.88) if 'table' in identifier else (1.05,.85,1.15) if large else (.65,.55,.85)
   if identifier=='chinese_screen_panels':size=(2.1,2.1,2.05)
   elif identifier=='brass_diya_lantern':size=(.40,.38,.45)
   elif identifier=='carved_wooden_elephant':size=(.45,.30,.45)
   elif identifier=='tea_set_01':size=(1.05,.60,.55)
   if identifier=='antique_katana_01':
    standrow=pool['Poly Haven:katana_stand_01'];stand=place(B,standrow,name+'_KatanaStand',x,y,z+base,(1.25,1.25,.5),yaw);stand_meta=B.layout.pop();stand_meta['kind']='display_support';B.layout.append(stand_meta);lo,hi=fit_horizontal_length(B,stand,stand_meta,.80);horizontal_blade(B,row);ob=place(B,row,name,x,y,float(hi[2])-.025,(1.45,1.45,.18),yaw);entry=B.layout[-1];entry.update(support_source_key='Poly Haven:katana_stand_01',support_mesh_name=stand.name);lo,hi=fit_horizontal_length(B,ob,entry,1.15);lo=np.minimum(lo,B.bounds(stand)[0]);hi=np.maximum(hi,B.bounds(stand)[1])
   else:ob=place(B,row,name,x,y,z+base,size,yaw);entry=B.layout[-1];lo,hi=B.bounds(ob)
   entry.update(floor=level+1,theme_category=identifier,display_support_kind='dark_wood_low_stand' if large else 'wood_framed_glass_case');hx=max(.4,(hi[0]-lo[0])/2+.12);hy=max(.35,(hi[1]-lo[1])/2+.12)
   if large:B.box(name+'_WoodPlinth',(x-hx,y-hy,z),(x+hx,y+hy,z+base),'EastDisplayWood',True,'plinth')
   else:wooden_case(B,name,x,y,z,hx,hy,base,max(.55,hi[2]-(z+base)+.14))
   counts[identifier]+=1
  for x in [-w*.35,-w*.18,w*.18,w*.35]:painting(B,art[artindex],f'East13_WallArt_F{level+1}_{artindex:02}',(x,h*.40-.13,z+1.65),0.,level+1);artindex+=1
 return {'per_floor':19,'actual_floor_objects_per_floor':15,'distinct_wall_art_per_floor':4,'model_counts':dict(counts),'katana_support':'Actual licensed katana_stand_01 paired with horizontal PCA-oriented licensed antique_katana_01','style':'Darkwood stands/cabinets and exact named Chinese furniture/tea/ceramic/curio sources'}
