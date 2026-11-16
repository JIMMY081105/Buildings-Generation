"""Original anatomical teaching models, explicitly not collected specimens."""
import math,numpy as np

def crustacean(B,name,x,y,z,index,lobster=False):
 rng=np.random.default_rng(B.spec['seed']+index*947);width=float(rng.uniform(.31,.43));length=float(rng.uniform(.30,.40));bodyz=z+.19
 B.material('CrustaceanShell',(.29,.085,.035) if lobster else (.18,.24,.19),'stone',.6,.48,.18)
 B.material('CrustaceanEye',(.008,.009,.007),'stone',.3,.20)
 def ellipsoid(suffix,c,scale,material='CrustaceanShell'):
  v=[];f=[];n=24;m=12
  for k in range(1,m):
   a=k/m*math.pi
   for j in range(n):
    b=j/n*math.tau;v.append((c[0]+scale[0]*math.sin(a)*math.cos(b),c[1]+scale[1]*math.sin(a)*math.sin(b),c[2]+scale[2]*math.cos(a)))
  for k in range(m-2):
   for j in range(n):f.append((k*n+j,k*n+(j+1)%n,(k+1)*n+(j+1)%n,(k+1)*n+j))
  top=len(v);v.append((c[0],c[1],c[2]+scale[2]));bottom=len(v);v.append((c[0],c[1],c[2]-scale[2]))
  for j in range(n):f.append((top,(j+1)%n,j));f.append((bottom,(m-2)*n+j,(m-2)*n+(j+1)%n))
  ob=B.mesh(name+'_'+suffix,v,f,material,True,'procedural_exhibit')
  for p in ob.data.polygons:p.use_smooth=True
 ellipsoid('Carapace',(x,y,bodyz),(width,length,.16))
 for side in [-1,1]:
  for j in range(4):
   yy=y+length*(.65-j*.40);end=(x+side*(.70+.035*j),yy-.14,z-.002);knee=(x+side*(.49+.025*j),yy+.08,bodyz+.025)
   B.tube(name+f'_WalkingLeg{side}_{j}',[(x+side*width*.78,yy,bodyz),knee,end],.021,'CrustaceanShell',True,'procedural_exhibit')
  wrist=(x+side*.51,y+.49,bodyz+.10);B.tube(name+f'_ClawArm{side}',[(x+side*width*.72,y+length*.65,bodyz),(x+side*.47,y+.36,bodyz+.04),wrist],.043,'CrustaceanShell',True,'procedural_exhibit')
  ellipsoid(f'ClawPalm{side}',(wrist[0],wrist[1]+.08,wrist[2]),(.12,.15,.075))
  for finger in [-1,1]:B.tube(name+f'_Pincer{side}_{finger}',[(wrist[0]+finger*.075,wrist[1]+.15,wrist[2]),(wrist[0]+finger*.10,wrist[1]+.29,wrist[2]),(wrist[0]+finger*.02,wrist[1]+.32,wrist[2])],.025,'CrustaceanShell',True,'procedural_exhibit')
  eye=(x+side*.15,y+length*.94,bodyz+.13);B.tube(name+f'_EyeStalk{side}',[(x+side*.14,y+length*.75,bodyz+.08),eye],.012,'CrustaceanShell',True,'procedural_exhibit');ellipsoid(f'Eye{side}',eye,(.025,.025,.025),'CrustaceanEye')
 if lobster:
  for j in range(6):ellipsoid(f'TailSegment{j}',(x,y-length-.105*j,bodyz-.015*j),(width*(.78-.085*j),.11,.12-.01*j))
  for j in [-1,0,1]:ellipsoid(f'TailFan{j}',(x+j*.12,y-length-.67,bodyz-.09),(.13,.18,.035))
  for side in [-1,1]:B.tube(name+f'_Antenna{side}',[(x+side*.10,y+length,bodyz+.10),(x+side*.20,y+.61,bodyz+.15),(x+side*.37,y+.86,bodyz+.17)],.006,'CrustaceanShell',True,'procedural_exhibit')
 else:
  for side in [-1,1]:
   for j in range(5):ellipsoid(f'CarapaceSpine{side}_{j}',(x+side*width*.96,y+length*(.65-j*.30),bodyz),(.045,.035,.035))
 if lobster:
  # Fit the complete tail fan and antennae inside the actual glass case.
  import bpy
  for ob in B.scene.objects:
   if ob.type=='MESH' and ob.name.startswith(name+'_') and ob.get('museum_category')=='procedural_exhibit':
    for v in ob.data.vertices:v.co=(x+(v.co.x-x)*.72,y+(v.co.y-y)*.72,z+(v.co.z-z)*.72)
 B.layout.append({'name':name,'kind':'exhibit','source_key':f'procedural:{B.spec["building_id"]}:{"lobster" if lobster else "crab"}_teaching_model:{index}','theme':'Original anatomical crustacean teaching model; no claimed species identification or collected-object provenance','walking_legs':8,'chelipeds':2,'tail_segments':6 if lobster else 0,'support_z_m':z,'support_embed_m':.002,'procedural_parameters':{'carapace_half_width_m':width,'carapace_half_length_m':length,'seed':B.spec['seed']+index*947}})
