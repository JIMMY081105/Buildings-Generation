"""Closed masonry rotunda dome with an actual oculus and interior coffers."""
import math,numpy as np

def rotunda_dome(B,model):
 Z=B.spec['floors']*B.spec['floor_height_m'];R=17.25;rise=6.;a0=math.asin(3./R);rings=16;segments=96;vertices=[];faces=[]
 angles=np.linspace(a0,math.pi/2,rings+1)
 for outer in [False,True]:
  for theta in angles:
   radius=(R+(.20 if outer else 0))*math.sin(theta)
   for phi in np.linspace(0,math.tau,segments,endpoint=False):vertices.append((radius*math.cos(phi),radius*math.sin(phi),Z+rise*math.cos(theta)+(.20 if outer else 0)))
 count=(rings+1)*segments
 for layer in range(2):
  for i in range(rings):
   for j in range(segments):
    k=(j+1)%segments;a=layer*count+i*segments;b=a+segments;face=(a+j,a+k,b+k,b+j);faces.append(tuple(reversed(face)) if layer==0 else face)
 for i in [0,rings]:
  for j in range(segments):
   k=(j+1)%segments;a=i*segments;faces.append((a+j,a+k,count+a+k,count+a+j))
 B.mesh('Roof_Rotunda_CofferedDome',vertices,faces,'Ceiling',True,'roof')
 # Exposed grid beams frame recessed masonry fields on the inner surface.
 for j in range(32):
  phi=j/32*math.tau;p=[(R*math.sin(t)*math.cos(phi),R*math.sin(t)*math.sin(phi),Z+rise*math.cos(t)-.07) for t in np.linspace(a0,math.pi/2,40)];B.tube(f'Coffer_Dome_Radial{j}',p,.09,'Ceiling',False,'decor')
 for j,t in enumerate(np.linspace(a0,math.pi/2,9)):
  p=[(R*math.sin(t)*math.cos(a),R*math.sin(t)*math.sin(a),Z+rise*math.cos(t)-.07) for a in np.linspace(0,math.tau,129)];B.tube(f'Coffer_Dome_Ring{j}',p,.09,'Ceiling',False,'decor')
 z=Z+rise*math.cos(a0)+.10;v=[(0,0,z)]+[(3.06*math.cos(a),3.06*math.sin(a),z) for a in np.linspace(0,math.tau,segments,endpoint=False)];B.mesh('Skylight_RotundaOculus',v,[(0,j+1,(j+1)%segments+1) for j in range(segments)],'WindowGlass',False,'roof_glass')
 for j in range(8):
  a=j/8*math.tau;B.tube(f'Skylight_RotundaOculusRib{j}',[(0,0,z+.025),(3.06*math.cos(a),3.06*math.sin(a),z+.025)],.025,'Brass',False,'roof_detail')
 B.layout.append({'kind':'architecture','name':'coffered_rotunda_dome','inner_radius_m':R,'rise_m':rise,'oculus_diameter_m':6.,'radial_coffer_beams':32,'coffer_rings':9,'collision':'closed masonry shell, exact triangle mesh; high-level ornamental beams visual only'})
