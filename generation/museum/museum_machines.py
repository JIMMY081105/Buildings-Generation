"""Original full-size industrial teaching assemblies, with explicit provenance."""
import math,numpy as np
def materials(B):
 for name,color,rough,metal in [('MachineEnamel',(.075,.16,.13),.45,.35),('MachineCastIron',(.035,.045,.04),.55,.55),('MachineBronze',(.28,.19,.08),.38,.7),('MachineGauge',(.72,.70,.62),.65,0)]:B.material(name,color,'stone',1.,rough,metal,source_hint='metal' if metal else '')
def cylinder(B,name,center,radius,length,mat,axis='Z'):
 ob=B.cylinder(name,(0,0,0),radius,length,mat,True,n=32);ob.location=center;ob['museum_category']='procedural_exhibit'
 if axis=='X':ob.rotation_euler.y=math.pi/2
 elif axis=='Y':ob.rotation_euler.x=math.pi/2
 return ob
def box(B,name,lo,hi,mat='MachineCastIron'):return B.box(name,lo,hi,mat,True,'procedural_exhibit')
def plate_xz(B,name,outline,y,thickness,mat):
 n=len(outline);v=[(x,y+dy,z) for dy in [-thickness/2,thickness/2] for x,z in outline];f=[tuple(reversed(range(n))),tuple(range(n,2*n))]+[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)];return B.mesh(name,v,f,mat,True,'procedural_exhibit')
def record(B,name,kind,index,z,params):B.layout.append({'name':name,'kind':'exhibit','source_key':f'procedural:{B.spec["building_id"]}:{kind}:{index}','theme':'Original full-size '+kind.replace('_',' ')+' teaching study; not a claimed historical artifact','support_z_m':z,'procedural_parameters':params})
def boiler(B,name,x,y,z,index):
 materials(B);rng=np.random.default_rng(B.spec['seed']+index*317);length=float(rng.uniform(1.8,2.15));radius=float(rng.uniform(.57,.67));center=z+radius+.23
 cylinder(B,name+'_Vessel',(x-length/2,y,center),radius,length,'MachineEnamel','X')
 for sign in [-1,1]:
  cylinder(B,name+f'_Flange{sign}',(x+sign*(length/2+.035),y,center),radius*1.06,.07,'MachineCastIron','X')
  for k in range(12):
   a=k*math.tau/12;cylinder(B,name+f'_Bolt{sign}_{k}',(x+sign*(length/2+.075),y+radius*.9*math.cos(a),center+radius*.9*math.sin(a)),.025,.035,'Steel','X')
 for sign in [-1,1]:
  xx=x+sign*length*.30;box(B,name+f'_Foot{sign}',(xx-.28,y-.52,z),(xx+.28,y+.52,z+.10));box(B,name+f'_Saddle{sign}',(xx-.15,y-.43,z+.09),(xx+.15,y+.43,center-.30))
 cylinder(B,name+'_Chimney',(x-.28,y,center+radius-.04),.12,.58,'MachineCastIron');cylinder(B,name+'_PressureGauge',(x+length/2+.12,y+.20,center+.20),.11,.06,'MachineGauge','X');B.tube(name+'_GaugeNeedle',[(x+length/2+.185,y+.20,center+.20),(x+length/2+.185,y+.25,center+.25)],.008,'Iron',True,'procedural_exhibit');B.tube(name+'_SteamPipe',[(x+.30,y+.20,center+radius),(x+.30,y+.62,center+radius),(x+.30,y+.62,center+.10)],.045,'MachineBronze',True,'procedural_exhibit');record(B,name,'steam_boiler',index,z,{'vessel_length_m':length,'vessel_radius_m':radius})
def gear(B,name,x,y,z,index):
 materials(B);rng=np.random.default_rng(B.spec['seed']+index*419);radius=float(rng.uniform(.91,1.05));teeth=int(rng.integers(24,36));center=z+radius+.22;outline=[]
 for k in range(teeth*4):
  a=k/(teeth*4)*math.tau;r=radius*(1 if k%4 in [1,2] else .89);outline.append((x+r*math.cos(a),center+r*math.sin(a)))
 plate_xz(B,name+'_SpurGear',outline,y,.16,'MachineBronze');cylinder(B,name+'_Shaft',(x,y+.34,center),.13,.70,'Steel','Y');box(B,name+'_Bearing',(x-.25,y+.18,center-.25),(x+.25,y+.48,center+.25),'MachineEnamel');box(B,name+'_Stand',(x-.12,y+.20,z+.06),(x+.12,y+.43,center-.20));box(B,name+'_Feet',(x-.43,y-.34,z),(x+.43,y+.55,z+.09));record(B,name,'gear_drive',index,z,{'radius_m':radius,'teeth':teeth,'shaft_length_m':.70})
def propeller(B,name,x,y,z,index):
 materials(B);rng=np.random.default_rng(B.spec['seed']+index*523);radius=float(rng.uniform(1.03,1.17));center=z+radius+.20;twist=float(rng.uniform(.18,.28))
 for blade in range(3):
  left=[];right=[]
  for t in np.linspace(0,1,18):
   a=blade*math.tau/3+twist*t;r=.17+(radius-.17)*t;width=.055+.20*math.sin(math.pi*t)**.7;cx=x+r*math.cos(a);cz=center+r*math.sin(a);tx=-math.sin(a);tz=math.cos(a);left.append((cx+tx*width,cz+tz*width));right.append((cx-tx*width,cz-tz*width))
  plate_xz(B,name+f'_Blade{blade}',left+list(reversed(right)),y,.075,'MachineBronze')
 cylinder(B,name+'_Hub',(x,y+.15,center),.19,.32,'MachineBronze','Y');box(B,name+'_Stand',(x-.07,y+.12,z+.08),(x+.07,y+.30,center));box(B,name+'_StandFoot',(x-.40,y-.30,z),(x+.40,y+.40,z+.09));record(B,name,'marine_propeller',index,z,{'blades':3,'radius_m':radius,'radial_twist_rad':twist})
