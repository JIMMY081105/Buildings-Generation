"""M24 physical arcade repair, closed gables, cotto floor and coastal facade."""
import math,collections
import numpy as np
import bpy
from shapely import wkt
from shapely.geometry import Point,Polygon,box
from staircase_core import Geometry
from building_core import extrude

def facade(B,model,shape,opening):
 from museum_facades import facade_kit
 facade_kit(B,model,shape,opening,allow_med32=False)
 for ob in list(B.scene.objects):
  if ob.name.startswith(('Arcade_Column','Arcade_Arch')):bpy.data.objects.remove(ob,do_unlink=True)
 w,h=model['building_xy_m'];H=model['floor_height_m'];N=len(model['floors']);Z=N*H;overhang=1.;ridge=Z+3.;G=Geometry();yb=h/2+.225;edge=Z+3*(1-yb/(h/2+overhang))-.025;profile=[(-yb,Z-.025),(yb,Z-.025),(yb,edge),(0,ridge-.025),(-yb,edge)]
 for sign in [-1,1]:
  x=sign*(w/2+.225);v=[(xx,y,z) for xx in [x,x-sign*.18] for y,z in profile];outer=list(range(5)) if sign>0 else list(reversed(range(5)));faces=[outer,[j+5 for j in reversed(outer)]]+[[outer[j],outer[(j+1)%5],outer[(j+1)%5]+5,outer[j]+5] for j in range(5)];B.mesh('GableClosure32_M24_'+('Left' if sign<0 else 'Right'),v,faces,'ExteriorStone',True,'roof_structure')
 # Central span is deliberately wider than the actual3.2m clear entrance.
 left=np.linspace(-w/2+.8,-2.2,max(2,math.ceil((w/2-3.)/4.2)+1));xs=np.r_[left,-left[::-1]];y=-h/2-1.
 for i,x in enumerate(xs):B.cylinder(f'Med32_ArcadeColumn{i}',(float(x),y,0),.22,2.8,'ExteriorStone',True)
 for i,(a,b) in enumerate(zip(xs[:-1],xs[1:])):
  mid=(a+b)/2;r=(b-a)/2;pts=[(mid+r*math.cos(t),y,2.8+1.3*math.sin(t)) for t in np.linspace(0,math.pi,36)];B.tube(f'Med32_ArcadeArch{i}',pts,.14,'Ceiling',True,'facade')
 B.material('Med32ShutterBlue',(.025,.105,.16),'long_board',1.2,.82);B.material('Med32Iron',(.028,.035,.032),'stone',1.2,.56,.8)
 shutters=0;balconies=0
 for i,win in enumerate(model['windows']):
  c=np.asarray(win['center']);d=np.asarray(win['direction']);n=np.array([-d[1],d[0]])
  if shape.exterior.distance(Point(c))>.35:continue
  out=-n if shape.contains(Point(c+n*.6)) else n;ww=win['width_m']/2;lo=win['sill_m'];hi=win['top_m'];yaw=math.atan2(d[1],d[0])
  for sign in [-1,1]:
   # Open180deg against the real wall face, actual slats and frame closed meshes.
   parts=Geometry()
   for k,z in enumerate(np.arange(lo+.06,hi-.04,.14)):
    ob=B.box(f'Med32_Shutter_{i}_{sign}_Louver{k}',(sign*(ww+.40)-.34,-.045,z),(sign*(ww+.40)+.34,.045,min(z+.08,hi)), 'Med32ShutterBlue',True,'window_shutter');ob.location=(*(c+out*.27),0);ob.rotation_euler.z=yaw
   for k,xx in enumerate([sign*(ww+.40)-.35,sign*(ww+.40)+.35]):
    ob=B.box(f'Med32_Shutter_{i}_{sign}_Frame{k}',(xx-.025,-.05,lo),(xx+.025,.05,hi),'Med32ShutterBlue',True,'window_shutter');ob.location=(*(c+out*.275),0);ob.rotation_euler.z=yaw
   for j,zz in enumerate([lo+.025,hi-.025]):
    centerx=sign*(ww+.40);ob=B.box(f'Med32_Shutter_{i}_{sign}_CrossFrame{j}',(centerx-.375,-.05,zz-.025),(centerx+.375,.05,zz+.025),'Med32ShutterBlue',True,'window_shutter');ob.location=(*(c+out*.275),0);ob.rotation_euler.z=yaw
   shutters+=1
  if H+.5<lo<2*H and c[1]<-h/2+.3 and abs(c[0])>3 and i%2==0:
   center=c+out*.38;z=lo-.06
   ob=B.box(f'Med32_BalconySlab{i}',(-ww-.23,-.38,z-.12),(ww+.23,.38,z),'ExteriorStone',True,'balcony');ob.location=(*center,0);ob.rotation_euler.z=yaw
   front=c+out*.76;points=[(*(front+d*t),z+1.1) for t in [-ww-.23,ww+.23]];B.tube(f'Med32_BalconyTop{i}',points,.022,'Med32Iron',True,'guard')
   for j,t in enumerate(np.linspace(-ww-.23,ww+.23,max(2,math.ceil((2*ww+.46)/.10)+1))):B.tube(f'Med32_BalconyPicket{i}_{j}',[(*(front+d*t),z),(*(front+d*t),z+1.1)],.009,'Med32Iron',True,'guard')
   for j,t in enumerate([-ww+.15,ww-.15]):B.tube(f'Med32_BalconyBracket{i}_{j}',[(*(c+d*t+out*.22),z-.65),(*(front+d*t),z-.12)],.045,'Med32Iron',True,'balcony_bracket')
   for side in [-1,1]:
    a=c+d*side*(ww+.23)+out*.225;b=front+d*side*(ww+.23);B.tube(f'Med32_BalconyReturnTop{i}_{side}',[(*a,z+1.1),(*b,z+1.1)],.022,'Med32Iron',True,'guard')
    for j,t in enumerate(np.linspace(0,1,7)):
     p=a+(b-a)*t;B.tube(f'Med32_BalconyReturnPicket{i}_{side}_{j}',[(*p,z),(*p,z+1.1)],.009,'Med32Iron',True,'guard')
   balconies+=1
 # Actual closed curved canal tiles, five subtle fired-clay shade batches.
 roof=shape.buffer(overhang,join_style=2);xmin,ymin,xmax,ymax=roof.bounds;batches=[([],[]) for _ in range(5)];count=0;phis=np.linspace(0,math.pi,5)
 for sy in [-1,1]:
  for row,t0 in enumerate(np.arange(0,max(abs(ymin),abs(ymax))-.42,.36)):
   ya,yb2=sorted([sy*t0,sy*(t0+.42)])
   for col,xa in enumerate(np.arange(xmin,xmax-.22,.22)):
    if not roof.covers(box(xa,ya,xa+.22,yb2)):continue
    shade=(col*17+row*13+(1 if sy>0 else 0))%5;v,f=batches[shade];offset=len(v);center=xa+.11;cross=[(center+.11*math.cos(p),.06+(.055 if col%2 else -.035)*math.sin(p)) for p in phis];cross=cross+[(x,z-.012) for x,z in reversed(cross)];nn=len(cross)
    for yy in [ya,yb2]:
     base=Z+3*(1-abs(yy)/(h/2+overhang));v.extend((x,yy,base+zz) for x,zz in cross)
    f.extend([tuple(offset+j for j in reversed(range(nn))),tuple(offset+nn+j for j in range(nn))]);f.extend((offset+j,offset+(j+1)%nn,offset+nn+(j+1)%nn,offset+nn+j) for j in range(nn));count+=1
 for k,(v,f) in enumerate(batches):
  mat=f'Med32CanalClay{k}';B.material(mat,tuple(np.array([.34,.105,.045])*(.86+k*.065)),'stone',1.2,.82);B.mesh(f'Med32_RealCanalTiles{k}',v,f,mat,False,'roof_detail')
 B.material('Med32FlowerBorder',(.57,.31,.15),'star_tile',3.2,.48)
 for level in range(N):
  floor=wkt.loads(model['floors'][level]['polygon_wkt']);band=opening.buffer(.60).difference(opening.buffer(.015)).intersection(floor);extrude(G,f'Med32_BlueFlowerAtriumBorder_F{level+1}',band,level*H+.0006,level*H+.0016,'floor_inlay','Med32FlowerBorder')
  if shape.interiors:
   court=Polygon(shape.interiors[0]);cx,cy=court.centroid.coords[0];y0=court.bounds[1];mat=box(cx-2,y0-1.3,cx+2,y0-.1).intersection(floor);extrude(G,f'Med32_BlueFlowerCourtEntry_F{level+1}',mat,level*H+.0006,level*H+.0016,'floor_inlay','Med32FlowerBorder')
 for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],q['material'],False,q['role'])
 # Original illustrative olive trees; no scanned species or historical claim.
 B.material('Med32TerracottaPot',(.33,.10,.045),'stone',.6,.84);B.material('Med32OliveLeaf',(.10,.19,.065),'stone',.3,.83);B.material('Med32Bark',(.12,.085,.045),'wood',.8,.87)
 for k,x in enumerate([-3.2,3.2]):
  yy=-h/2-2.0;B.cylinder(f'Med32_EntryPot{k}',(x,yy,0),.40,.58,'Med32TerracottaPot',True);trunk=B.cylinder(f'Med32_OliveTrunk{k}',(x,yy,.58),.045,1.5,'Med32Bark',True);trunk['museum_category']='plant_trunk';v=[];f=[]
  B.cylinder(f'Med32_PotSoil{k}',(x,yy,.58),.33,.008,'Med32Bark',False)
  for j in range(72):
   a=j*2.399963;radius=.30+.24*((j*17)%13)/12;z=1.45+.9*((j*11)%19)/18;cx=x+radius*math.cos(a);cy=yy+radius*math.sin(a);dx=.075*math.cos(a+.8);dy=.075*math.sin(a+.8);B.tube(f'Med32_OliveTwig{k}_{j}',[(x,yy,max(1.2,min(2.05,z-.12))),(cx,cy,z)],.005,'Med32Bark',True,'plant_branch');offset=len(v);v.extend([(cx-dx,cy-dy,z),(cx-dy*.30,cy+dx*.30,z+.025),(cx+dx,cy+dy,z),(cx+dy*.30,cy-dx*.30,z-.01)]);f.append(tuple(offset+i for i in range(4)))
  B.mesh(f'Med32_OriginalOliveFoliage{k}',v,f,'Med32OliveLeaf',False,'plant_foliage')
 B.layout.append({'kind':'architecture','name':'Mediterranean32_repaired_facade_and_cotto','arcade_columns':len(xs),'arcade_spans':len(xs)-1,'all_arcade_ends_supported':True,'gable_closed_prisms':2,'open_wood_shutters':shutters,'bracketed_iron_balconies':balconies,'actual_closed_canal_tiles':count,'source_floor':'30cm warm fired-clay square tiles, original baked variation, actual2.4m2K period; floral inlay confined to atrium ring/court entry','plants':'2 original illustrative olive trees in architectural terra cotta pots, no scanned-species provenance','physical_scope':'New exact closed columns/arches/shutters/balconies/gables/pots/trunks: full fresh C1-C5 mandatory; prior accepted stair/floor geometry must be independently compared.'})
