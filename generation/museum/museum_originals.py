"""Original illustrative exhibit studies, with explicit procedural provenance."""
import math,hashlib,numpy as np

def quartz(B,name,x,y,z,index):
 rng=np.random.default_rng(B.spec['seed']+index*103);B.material('Quartz',(.58,.63,.59),'stone',1.,.20,.03);verts=[];faces=[]
 for j in range(9):
  angle=j/9*math.tau;cx=x+math.cos(angle)*(.22 if j else 0);cy=y+math.sin(angle)*(.22 if j else 0);height=float(rng.uniform(.38,.9));radius=float(rng.uniform(.055,.10));base=len(verts)
  for zz in [z-.004,z+height*.78]:
   for a in np.linspace(0,math.tau,6,endpoint=False):verts.append((cx+radius*math.cos(a),cy+radius*math.sin(a),zz))
  verts.append((cx,cy,z+height));faces.append(tuple(base+j for j in reversed(range(6))))
  for k in range(6):faces.append((base+k,base+(k+1)%6,base+6+(k+1)%6,base+6+k));faces.append((base+6+k,base+6+(k+1)%6,base+12))
 B.mesh(name,verts,faces,'Quartz',True,'procedural_exhibit');B.layout.append({'name':name,'kind':'exhibit','source_key':f'procedural:{B.spec["building_id"]}:quartz_study:{index}','theme':'Original illustrative quartz crystal cluster; no claimed collected-object provenance','support_z_m':z,'geometry_sha256':hashlib.sha256(np.asarray(verts).tobytes()).hexdigest()})

def engine(B,name,x,y,z,index):
 """An exposed piston-engine teaching model, not a claimed historical artifact."""
 rng=np.random.default_rng(B.spec['seed']+index*271);n=4 if index%2 else 6;length=1.05+.16*(n-4)+float(rng.uniform(-.06,.06));finish='EngineEnamel_'+str(index%3);B.material(finish,[(.045,.12,.10),(.13,.14,.15),(.23,.045,.025)][index%3],'stone',.8,.32,.65,source_hint='metal');head_height=float(rng.uniform(.46,.60))
 B.box(name+'_Crankcase',(x-length/2,y-.30,z),(x+length/2,y+.30,z+.34),finish,True,'procedural_exhibit')
 def cylinder(suffix,center,radius,height,material,axis='Z'):
  ob=B.cylinder(name+'_'+suffix,(0,0,0),radius,height,material,True,n=20);ob.location=center;ob['museum_category']='procedural_exhibit'
  if axis=='X':ob.rotation_euler.y=math.pi/2
  return ob
 for i,xx in enumerate(np.linspace(x-length*.38,x+length*.38,n)):
  h=head_height;cylinder(f'Cylinder{i}',(xx,y,z+.30),.105,h,'Steel');cylinder(f'Head{i}',(xx,y,z+.30+h),.12,.08,finish)
  for k in range(6):cylinder(f'CoolingFin{i}_{k}',(xx,y,z+.34+k*.07),.12,.018,'Steel')
  for sign in [-1,1]:B.tube(name+f'_Exhaust{i}_{sign}',[(xx,y+sign*.08,z+.65),(xx,y+sign*.36,z+.65),(xx,y+sign*.45,z+.42)],.028,'Iron',True,'procedural_exhibit')
 cylinder('Flywheel',(x-length/2-.12,y,z+.34),.31,.10,'Iron','X');cylinder('FlywheelHub',(x-length/2-.17,y,z+.34),.09,.20,'Brass','X')
 B.box(name+'_Sump',(x-length*.34,y-.22,z-.005),(x+length*.34,y+.22,z+.04),'Iron',True,'procedural_exhibit');B.layout.append({'name':name,'kind':'exhibit','source_key':f'procedural:{B.spec["building_id"]}:exposed_engine:{index}','theme':'Original exposed piston-engine teaching model','cylinders':n,'support_z_m':z,'procedural_parameters':{'length_m':length,'head_height_m':head_height,'finish_variant':index%3}})
