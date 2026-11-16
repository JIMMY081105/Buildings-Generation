"""Spec-specific open treads and real load-bearing side or centre beams."""
import numpy as np
from staircase_core import Geometry

def apply_assembly(data,spec):
 bid=spec['building_id'];opened=bid in ['M04','M09','M11','M13','M21','M26','M27','M28','M29']
 if not opened:return data
 for row in data['parts']:
  if row['role']!='tread':continue
  v=np.array(row['vertices']);top=v[:,2].max()
  if row.get('soffit')=='continuous_helical_inclined':
   for k in range(len(v)//4):v[4*k:4*k+2,2]=top-.10
  else:v[:len(v)//2,2]=top-.10
  row['vertices']=v.tolist();row['construction']='open riser,100mm tread, supported by authored beams'
 G=Geometry()
 for route in data['routes']:
  if 'count' not in route:continue
  points=np.asarray(route['xy'])[::10]
  if np.linalg.norm(points[-1]-route['xy'][-1])>.001:points=np.vstack([points,route['xy'][-1]])
  treads=[p for p in data['parts'] if p['name'].startswith(route['name']+'_Tread')];width=1.8 if data['form']=='A' else 1.6 if data['form'] in ['H','W'] else 2.6 if 'Central' in route['name'] else 1.6
  side=bid in ['M04','M09','M21','M28','M29'];offsets=[-width/2+.13,width/2-.13] if side else [0.];s=np.r_[0,np.cumsum(np.linalg.norm(np.diff(points,axis=0),axis=1))]
  for bi,offset in enumerate(offsets):
   verts=[];faces=[]
   for i,p in enumerate(points):
    d=points[min(i+1,len(points)-1)]-points[max(i-1,0)];d/=np.linalg.norm(d);lat=np.array([-d[1],d[0]]);c=p+lat*offset;top=route['start_z_m']+s[i]/route['going_m']*route['rise_m']-.08
    for q,z in [(c-lat*.10,top-.22),(c+lat*.10,top-.22),(c+lat*.10,top),(c-lat*.10,top)]:verts.append((*q,z))
   for i in range(len(points)-1):
    for j in range(4):faces.append((4*i+j,4*i+(j+1)%4,4*(i+1)+(j+1)%4,4*(i+1)+j))
   faces.extend([(3,2,1,0),tuple(4*(len(points)-1)+j for j in range(4))]);G.mesh(route['name']+f'_StructuralBeam{bi}',verts,faces,role='stair_beam',material='wood' if bid=='M21' else 'metal')
 data['parts']+=G.parts;return data
