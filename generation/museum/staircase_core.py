"""Five permitted staircase forms. Metres/Z-up, explicit watertight meshes.
No Blender or simulator defaults determine rise/going. Consumers export these
vertices; validation subsequently reads the exported collision USD independently.
"""
import math,json
import numpy as np

class Geometry:
 def __init__(self):self.parts=[];self.treads=[];self.exits=[];self.routes=[]
 def mesh(self,name,v,f,role='stair',material='stone',**meta):
  self.parts.append({'name':name,'vertices':np.asarray(v).tolist(),'faces':[list(x) for x in f],'role':role,'material':material,**meta});return name
 def prism(self,name,poly,z0,z1,**kw):
  n=len(poly);v=[(x,y,z) for z in [z0,z1] for x,y in poly]
  # Convex footprints only. A sector is authored as strips below.
  f=[tuple(reversed(range(n))),tuple(range(n,n*2))]+[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
  return self.mesh(name,v,f,**kw)
 def box(self,name,lo,hi,**kw):
  x,y,z=lo;X,Y,Z=hi;return self.prism(name,[(x,y),(X,y),(X,Y),(x,Y)],z,Z,**kw)
 def rect(self,name,start,direction,length,width,z0,z1,**kw):
  s=np.array(start,dtype=float);d=np.array(direction,dtype=float);d/=np.linalg.norm(d);lat=np.array([-d[1],d[0]])
  return self.prism(name,[s-lat*width/2,s+d*length-lat*width/2,s+d*length+lat*width/2,s+lat*width/2],z0,z1,**kw)
 def sector(self,name,r0,r1,a0,a1,z0,z1,**kw):
  n=max(2,math.ceil(abs(a1-a0)/.035));v=[];f=[]
  for i in range(n+1):
   a=a0+(a1-a0)*i/n;v.extend([(r*math.cos(a),r*math.sin(a),z) for r,z in [(r0,z0),(r1,z0),(r1,z1),(r0,z1)]])
  for i in range(n):
   for k in range(4):f.append((4*i+k,4*i+(k+1)%4,4*(i+1)+(k+1)%4,4*(i+1)+k))
  f.extend([(3,2,1,0),tuple(4*n+i for i in range(4))]);return self.mesh(name,v,f,**kw)
 def tube(self,name,points,radius=.025,**kw):
  p=np.asarray(points,dtype=float);v=[];faces=[];n=8
  for i,q in enumerate(p):
   tangent=p[min(i+1,len(p)-1)]-p[max(0,i-1)];tangent/=np.linalg.norm(tangent)
   axis=np.cross(tangent,[0,0,1])
   if np.linalg.norm(axis)<1e-8:axis=np.array([1.,0,0])
   axis/=np.linalg.norm(axis);other=np.cross(tangent,axis)
   v.extend(q+radius*(axis*math.cos(a)+other*math.sin(a)) for a in np.linspace(0,math.tau,n,endpoint=False))
  for i in range(len(p)-1):
   for j in range(n):faces.append((i*n+j,i*n+(j+1)%n,(i+1)*n+(j+1)%n,(i+1)*n+j))
  faces.extend([tuple(reversed(range(n))),tuple((len(p)-1)*n+i for i in range(n))]);return self.mesh(name,v,faces,material='metal',**kw)
 def rail(self,name,points,bottoms,glass=False):
  # Railing feet intersect the tread/edge. Gap between 25mm rods is <.11m.
  p=np.asarray(points);self.tube(name+'_Handrail',p,.035)
  for i,(q,z) in enumerate(zip(p,bottoms)):
   self.tube(name+f'_Post{i:03}',[(q[0],q[1],z-.015),q],.013)
 def fixed_route(self,name,corners,z):
  xy=[]
  for a,b in zip(corners,corners[1:]):
   a=np.asarray(a);b=np.asarray(b);xy.extend((a+(b-a)*t).tolist() for t in np.linspace(0,1,math.ceil(np.linalg.norm(b-a)/.01)+1))
  self.routes.append({'name':name,'xy':xy,'fixed_z_m':z,'sample_spacing_max_m':.01})
 def flight(self,name,start,d,n,z,h,g,width,material='stone',cantilever=False,open_sides=(-1,1)):
  start=np.asarray(start,dtype=float);d=np.asarray(d,dtype=float);lat=np.array([-d[1],d[0]]);thick=.09 if cantilever else .23
  names=[]
  for i in range(n):
   nm=name+f'_Tread{i+1:02}';self.rect(nm,start+d*i*g,d,g,width,z+(i+1)*h-thick,z+(i+1)*h,material=material,role='tread');self.treads.append(nm);names.append(nm)
   if not cantilever:
    # Adjacent solids share a continuous inclined soffit, while their actual
    # upper surfaces retain the specified discrete rise and going.
    row=self.parts[-1];v=np.asarray(row['vertices']);along=(v[:4,:2]-start)@d
    v[:4,2]=z+along/g*h-.25;row['vertices']=v.tolist();row['soffit']='continuous_inclined'
  for sign in open_sides:
   pts=[];bottom=[]
   for s in np.linspace(0,n*g,math.ceil(n*g/.09)+1):
    i=min(n-1,int(max(0,s-1e-7)/g));top=z+(i+1)*h;xy=start+d*s+lat*sign*(width/2-.012)
    # Elevation follows nosings; .95 m at each tread's leading edge.
    railz=z+max(h,s/g*h)+.95
    pts.append((*xy,railz));bottom.append(top)
   self.rail(name+f'_Rail{sign}',pts,bottom)
  self.routes.append({'name':name,'xy':[(start+d*s).tolist() for s in np.arange(0,n*g+.0001,.01)],'start_z_m':z,'going_m':g,'rise_m':h,'count':n})
  return start+d*n*g,names[-1]
 def curved(self,name,r0,width,a0,n,z,h,g,material='stone'):
  rw=r0+width/2;da=g/rw
  for i in range(n):
   nm=name+f'_Tread{i+1:02}';self.sector(nm,r0,r0+width,a0+i*da,a0+(i+1)*da,z+(i+1)*h-.23,z+(i+1)*h,role='tread',material=material);self.treads.append(nm)
   row=self.parts[-1];v=np.asarray(row['vertices']);rings=len(v)//4
   for k in range(rings):v[4*k:4*k+2,2]=z+(i+k/(rings-1))*h-.25
   row['vertices']=v.tolist();row['soffit']='continuous_helical_inclined'
  for side,r in [('Inner',r0+.012),('Outer',r0+width-.012)]:
   pts=[];bottom=[]
   for a in np.linspace(a0,a0+n*da,math.ceil(n*g/.09)+1):
    i=min(n-1,int(max(0,a-a0-1e-7)/da));top=z+(i+1)*h
    pts.append((r*math.cos(a),r*math.sin(a),z+max(h,(a-a0)/da*h)+.95));bottom.append(top)
   self.rail(name+'_Rail'+side,pts,bottom)
  self.routes.append({'name':name,'xy':[[rw*math.cos(a),rw*math.sin(a)] for a in np.arange(a0,a0+n*da+.00001,.01/rw)],'start_z_m':z,'going_m':g,'rise_m':h,'count':n})
  return a0+n*da,nm
 def landing_rails(self,name,start,d,length,width,z,open_sides=(-1,1)):
  s=np.array(start);d=np.array(d);lat=np.array([-d[1],d[0]])
  for sign in open_sides:
   p=[(*(s+d*t+lat*sign*(width/2-.012)),z+1.08) for t in np.linspace(0,length,math.ceil(length/.09)+1)]
   self.rail(name+f'_Guard{sign}',p,[z]*len(p))
 def exit(self,name,start,d,width,z,last_tread,landing=1.6,test_floor=True):
  s=np.asarray(start,dtype=float);d=np.asarray(d,dtype=float)
  self.rect(name+'_Landing',s,d,landing,width,z-.23,z)
  self.landing_rails(name,s,d,landing,width,z)
  # Floor begins at the landing boundary; +5mm overlap tolerates export float32.
  if test_floor:self.rect(name+'_FloorSlab',s+d*landing,d,3.0,width+1.2,z-.32,z,role='floor',material='floor')
  self.exits.append({'name':name,'start_xy':s.tolist(),'direction_xy':d.tolist(),'landing_length_m':landing,'width_m':width,'floor_z_m':z,'last_tread':last_tread,'floor_prefix':name+'_FloorSlab','negative_translation_xyz':[-.6*d[0],-.6*d[1],0.],'sampling_start_m':-.05,'sampling_end_m':landing+1.0})
 def merge_landing_bridge(self,landing_name,bridge_name,name):
  from shapely.geometry import Polygon
  from shapely.ops import triangulate
  from shapely.geometry.polygon import orient
  L=next(p for p in self.parts if p['name']==landing_name);B=next(p for p in self.parts if p['name']==bridge_name)
  lv=np.asarray(L['vertices']);bv=np.asarray(B['vertices']);outer=lv[2::4,:2];inner=lv[3::4,:2][::-1];P=Polygon(np.concatenate([outer,inner]));Q=Polygon(bv[len(bv)//2:,:2]);poly=orient(P.union(Q),sign=1);assert poly.geom_type=='Polygon' and poly.is_valid
  z0=min(lv[:,2].min(),bv[:,2].min());z1=max(lv[:,2].max(),bv[:,2].max());verts=[];faces=[];indices={}
  def vi(x,y,z):
   k=(round(float(x),10),round(float(y),10),round(float(z),10))
   if k not in indices:indices[k]=len(verts);verts.append(k)
   return indices[k]
  for tr in triangulate(poly):
   if not poly.covers(tr):continue
   xy=list(tr.exterior.coords)[:3]
   if np.cross(np.array(xy[1])-xy[0],np.array(xy[2])-xy[0])<0:xy.reverse()
   faces.append(tuple(vi(x,y,z1) for x,y in xy));faces.append(tuple(vi(x,y,z0) for x,y in reversed(xy)))
  for ring in [poly.exterior,*poly.interiors]:
   xy=list(ring.coords)
   for a,b in zip(xy,xy[1:]):faces.append((vi(*a,z0),vi(*b,z0),vi(*b,z1),vi(*a,z1)))
  self.parts=[p for p in self.parts if p['name'] not in [landing_name,bridge_name]];self.mesh(name,verts,faces,role='stair',material='stone',removed_coplanar_overlap_m2=float(P.intersection(Q).area))
 def merge_flat_prisms(self,names,name):
  from shapely.geometry import Polygon
  from shapely.ops import unary_union,triangulate
  from shapely.geometry.polygon import orient
  selected=[p for p in self.parts if p['name'] in names];profiles=[];z0=min(np.array(p['vertices'])[:,2].min() for p in selected);z1=max(np.array(p['vertices'])[:,2].max() for p in selected)
  for p in selected:
   v=np.array(p['vertices'])
   for f in p['faces']:
    if (abs(v[f,2]-z1)<1e-7).all():profiles.append(Polygon(v[f,:2]))
  poly=orient(unary_union(profiles),sign=1);assert poly.geom_type=='Polygon' and poly.is_valid;verts=[];faces=[];lookup={}
  def vi(x,y,z):
   key=tuple(round(float(q),10) for q in [x,y,z])
   if key not in lookup:lookup[key]=len(verts);verts.append(key)
   return lookup[key]
  for tr in triangulate(poly):
   if not poly.covers(tr):continue
   xy=list(tr.exterior.coords)[:3]
   if np.cross(np.array(xy[1])-xy[0],np.array(xy[2])-xy[0])<0:xy.reverse()
   faces.append(tuple(vi(x,y,z1) for x,y in xy));faces.append(tuple(vi(x,y,z0) for x,y in reversed(xy)))
  for ring in [poly.exterior,*poly.interiors]:
   for a,b in zip(ring.coords,list(ring.coords)[1:]):faces.append((vi(*a,z0),vi(*b,z0),vi(*b,z1),vi(*a,z1)))
  self.parts=[p for p in self.parts if p['name'] not in names];self.mesh(name,verts,faces,role='stair',material='stone',removed_coplanar_overlap_m2=sum(p.area for p in profiles)-poly.area)
 def newel(self,name,xy,z):
  x,y=xy;self.box(name,(x-.10,y-.10,z-.03),(x+.10,y+.10,z+1.15),role='newel',material='metal')
 def finish_joints(self,joints):
  # Trim redundant balusters at shared newels; existing rail ends enter the
  # solid post, so all intended section intersections remain inside that post.
  remove=set()
  for row in self.parts:
   if '_Post' not in row['name']:continue
   v=np.asarray(row['vertices']);xy=(v.min(0)+v.max(0))[:2]/2
   if any(np.linalg.norm(xy-np.asarray(p))<.13 and v[:,2].min()<z+1.2 and v[:,2].max()>z-.05 for p,z in joints):remove.add(row['name'])
  self.parts=[p for p in self.parts if p['name'] not in remove]
  for i,(xy,z) in enumerate(joints):self.newel(f'Newel_I_{i:02}',xy,z)

def build_stair(form,spec=None,test_floor=True):
 G=Geometry();S=spec or {};h=S.get('rise_m',.16);height=S.get('floor_height_m',(33 if form=='W' else 32)*h);G.box('Ground_Floor',(-12,-12,-.32),(12,12,0),role='floor',material='floor') if test_floor else None
 if form=='I':
  end,_=G.flight('I_Central',(0,-5),(0,1),16,0,h,.32,2.6)
  G.box('I_MidLanding',(-3.7,end[1],16*h-.23),(3.7,end[1]+1.8,16*h))
  if S.get('gallery_outward_exits'):
   for sign in [-1,1]:
    start=np.array([sign*2.6,end[1]+1.0]);direction=np.array([sign/math.sqrt(2),1/math.sqrt(2)])
    G.fixed_route('I_MidToBranch'+str(sign),[(0,end[1]),(0,end[1]+1.0),start],16*h)
    p,last=G.flight('I_Branch'+str(sign),start,direction,16,16*h,h,.32,1.6)
    G.rect('I_TurnLanding'+str(sign),p,direction,1.6,1.6,height-.23,height)
    elbow=p+direction*.8
    G.exit('I_Exit'+str(sign),elbow,(sign,0),1.6,height,last,landing=8.8-abs(elbow[0]),test_floor=test_floor)
    G.merge_flat_prisms(['I_TurnLanding'+str(sign),'I_Exit'+str(sign)+'_Landing'],'I_TurnAndExitLanding'+str(sign))
    G.fixed_route('I_ExitTurn'+str(sign),[p-direction*.03,p,elbow,elbow+np.array([sign*.5,0])],height)
    x0,x1=sorted([sign*1.3,sign*3.7]);G.landing_rails('I_MidFront'+str(sign),(x0,end[1]),(1,0),x1-x0,.025,16*h,open_sides=(-1,))
    outer=start+np.array([sign*.788/math.sqrt(2),-.788/math.sqrt(2)]);inner=start+np.array([-sign*.788/math.sqrt(2),.788/math.sqrt(2)])
    G.rail('I_MidOuterJoin'+str(sign),[(*(np.array([sign*3.7,end[1]])),16*h+1.08),(*outer,16*h+1.11)],[16*h,16*h])
    G.rail('I_MidInnerJoin'+str(sign),[(*(np.array([sign*1.7,end[1]+1.8])),16*h+1.08),(*inner,16*h+1.11)],[16*h,16*h])
   G.landing_rails('I_MidBack',(-1.7,end[1]+1.8),(1,0),3.4,.025,16*h,open_sides=(1,))
   joints=[((sign*1.3,end[1]),16*h) for sign in [-1,1]]
   joints += [((sign*3.7,end[1]),16*h) for sign in [-1,1]]
   joints += [((sign*1.7,end[1]+1.8),16*h) for sign in [-1,1]]
   joints += [((sign*2.6+sign*side*.788/math.sqrt(2),end[1]+1.-side*.788/math.sqrt(2)),16*h+.16) for sign in [-1,1] for side in [-1,1]]
   G.finish_joints(joints)
   return {'schema':'Museum29StairMeshV1','form':form,'rise_m':h,'floor_height_m':height,'parts':G.parts,'treads':G.treads,'exits':G.exits,'routes':G.routes,'dimensions':{'width_min_m':1.6,'max_rises_without_landing':16,'going_m':.32,'bifurcation':'outward splayed 45 degrees per branch'}}
  for sign in [-1,1]:G.fixed_route('I_MidToBranch'+str(sign),[(0,end[1]),(0,end[1]+.7),(sign*2.8,end[1]+.7),(sign*2.8,end[1])],16*h)
  G.landing_rails('I_MidBack',(-3.7,end[1]+1.8),(1,0),7.4,.05,16*h,open_sides=(1,))
  for s in [-1,1]:
   x0,x1=sorted([s*1.3,s*2.0]);G.landing_rails('I_FrontGap'+str(s),(x0,end[1]),(1,0),x1-x0,.025,16*h,open_sides=(-1,))
   G.landing_rails('I_MidSide'+str(s),(s*3.7,end[1]),(0,1),1.8,.025,16*h,open_sides=(-s,))
  for sign in [-1,1]:
   p,last=G.flight('I_Branch'+str(sign),(sign*2.8,end[1]),(0,-1),16,16*h,h,.32,1.6)
   if S.get('gallery_outward_exits'):
    # A full-width turn landing meets the upper gallery at the side of the
    # grand stair hall, leaving an unobstructed frontal view of the split.
    G.box('I_TurnLanding'+str(sign),(p[0]-.8,p[1]-1.6,height-.23),(p[0]+.8,p[1],height))
    start=p+np.array([0.,-.8]);G.fixed_route('I_ExitTurn'+str(sign),[p,start,start+np.array([sign*.8,0])],height)
    G.exit('I_Exit'+str(sign),start,(sign,0),1.6,height,last,landing=4.,test_floor=test_floor)
   else:G.exit('I_Exit'+str(sign),p,(0,-1),1.6,height,last,test_floor=test_floor)
  joints=[((sign*x,end[1]),16*h) for sign in [-1,1] for x in [1.3,2.0,3.65]]
  joints += [((sign*3.7,end[1]+1.8),16*h) for sign in [-1,1]]
  joints += [((sign*x,-5.0),32*h) for sign in [-1,1] for x in [2.0,3.6]]
  G.finish_joints(joints)
 elif form=='W':
  p,_=G.flight('W_Run1',(-4,-4),(1,0),11,0,h,.32,1.6)
  G.box('W_Landing1',(p[0],-4.8,11*h-.23),(p[0]+1.6,-3.2,11*h))
  G.fixed_route('W_Turn1',[(p[0],-4),(p[0]+.8,-4),(p[0]+.8,-3.2)],11*h)
  G.landing_rails('W_Turn1OuterA',(p[0],-4.8),(1,0),1.6,.025,11*h,open_sides=(-1,))
  G.landing_rails('W_Turn1OuterB',(p[0]+1.6,-4.8),(0,1),1.6,.025,11*h,open_sides=(-1,))
  p,_=G.flight('W_Run2',(p[0]+.8,-3.2),(0,1),11,11*h,h,.32,1.6)
  G.box('W_Landing2',(p[0]-.8,p[1],22*h-.23),(p[0]+.8,p[1]+1.6,22*h))
  G.fixed_route('W_Turn2',[(p[0],p[1]),(p[0],p[1]+.8),(p[0]-.8,p[1]+.8)],22*h)
  G.landing_rails('W_Turn2OuterA',(p[0]+.8,p[1]),(0,1),1.6,.025,22*h,open_sides=(-1,))
  G.landing_rails('W_Turn2OuterB',(p[0]-.8,p[1]+1.6),(1,0),1.6,.025,22*h,open_sides=(1,))
  p,last=G.flight('W_Run3',(p[0]-.8,p[1]+.8),(-1,0),11,22*h,h,.32,1.6)
  G.exit('W_Exit',p,(-1,0),1.6,height,last,test_floor=test_floor)
 elif form=='C':
  width=2.2;wall_y=-4-width/2+.17;second_x=-4+16*.33+width/2;wall_x=second_x+width/2-.17;second_y=-4+width/2
  p,_=G.flight('C_Run1',(-4,-4),(1,0),16,0,h,.33,width,material='wood',cantilever=True,open_sides=(1,))
  G.box('C_LoadWall1',(-4.3,wall_y-.35,-.32),(wall_x,wall_y,height+2.4),role='wall')
  G.box('C_MidLanding',(p[0],-4-width/2,16*h-.09),(p[0]+width,second_y,16*h),material='wood')
  G.fixed_route('C_Turn',[(p[0],-4),(second_x,-4),(second_x,second_y)],16*h)
  p,last=G.flight('C_Run2',(second_x,second_y),(0,1),16,16*h,h,.33,width,material='wood',cantilever=True,open_sides=(1,))
  G.box('C_LoadWall2',(wall_x,wall_y-.35,-.32),(wall_x+.35,p[1]+width,height+2.4),role='wall')
  G.exit('C_Exit',p,(0,1),width,height,last,landing=width,test_floor=test_floor)
  # Continuous wall-mounted rails at the same nosing height as the open edge.
  G.tube('C_WallHandrail1',[(-4,wall_y+.08,h+.95),(1.28,wall_y+.08,16*h+.95)],.035)
  G.tube('C_WallHandrail2',[(wall_x-.08,second_y,17*h+.95),(wall_x-.08,p[1],32*h+.95)],.035)
  for i,x in enumerate(np.linspace(-3.9,1.1,7)):
   z=h+((x+4)/5.28)*15*h+.95;G.tube(f'C_WallBracket1_{i}',[(x,wall_y-.04,z-.06),(x,wall_y+.08,z)],.02)
  for i,y in enumerate(np.linspace(second_y+.12,p[1]-.1,7)):
   z=17*h+((y-second_y)/5.28)*15*h+.95;G.tube(f'C_WallBracket2_{i}',[(wall_x+.04,y,z-.06),(wall_x-.08,y,z)],.02)
 elif form in ['H','A']:
  r0=2.1 if form=='H' else 4.5;width=1.6 if form=='H' else 1.8;rw=r0+width/2;g=.32 if form=='H' else .33
  if form=='H':a,last=G.curved('H_Run',r0,width,0,32,0,h,g);land=math.radians(35)
  else:
   a,_=G.curved('A_Run1',r0,width,0,16,0,h,g);land=1.6/rw
   G.sector('A_MidLanding',r0,r0+width,a,a+land,16*h-.23,16*h)
   G.fixed_route('A_MidWalk',[(rw*math.cos(t),rw*math.sin(t)) for t in np.linspace(a,a+land,math.ceil(rw*land/.01)+1)],16*h)
   for side,r in [('In',r0+.012),('Out',r0+width-.012)]:
    pts=[(r*math.cos(t),r*math.sin(t),16*h+1.08) for t in np.linspace(a,a+land,22)];G.rail('A_MidGuard'+side,pts,[16*h]*len(pts))
   a,last=G.curved('A_Run2',r0,width,a+land,16,16*h,h,g)
  G.sector(form+'_TopLanding',r0,r0+width,a,a+land,height-.23,height)
  G.fixed_route(form+'_TopLandingWalk',[(rw*math.cos(t),rw*math.sin(t)) for t in np.linspace(a,a+land,math.ceil(rw*land/.01)+1)],height)
  ang=a+land/2;d=np.array([math.cos(ang),math.sin(ang)]);start=d*rw
  # Narrow bridge leaves the landing radially; stairs retain exact sectors.
  G.exit(form+'_Exit',start,d,1.8,height,last,landing=(r0+width+1.5-rw),test_floor=test_floor)
  G.merge_landing_bridge(form+'_TopLanding',form+'_Exit_Landing',form+'_TopLandingBridge')
  # Inner landing edge is guarded; outer edge opens onto the bridge.
  pts=[((r0+.012)*math.cos(t),(r0+.012)*math.sin(t),height+1.08) for t in np.linspace(a,a+land,max(12,math.ceil(r0*land/.09)))];G.rail(form+'_TopInnerGuard',pts,[height]*len(pts))
 else:raise ValueError(form)
 return {'schema':'Museum29StairMeshV1','form':form,'rise_m':h,'floor_height_m':height,'parts':G.parts,'treads':G.treads,'exits':G.exits,'routes':G.routes,'dimensions':{'width_min_m':1.6,'inner_radius_m':2.1 if form=='H' else 4.5 if form=='A' else None,'max_rises_without_landing':32 if form=='H' else 16 if form!='W' else 11,'going_m':.32 if form in 'HIW' else .33}}

if __name__=='__main__':
 import sys
 from pathlib import Path
 D=Path(sys.argv[1]);D.mkdir(exist_ok=True,parents=True)
 for f in 'HAIWC':
  data=build_stair(f);p=D/(f+'_mesh.json');p.write_text(json.dumps(data));print(f,len(data['parts']),len(data['treads']),str(p),flush=True)
