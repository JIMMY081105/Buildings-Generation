"""Exact style-specific railing infill, shared by flights and gallery edges."""
import math
import numpy as np

SPECIAL={'M02':'stone_baluster','M05':'turned_wood','M07':'stone_panel','M09':'horizontal_steel','M11':'wire_mesh','M15':'concrete_panel','M18':'deco_geometric','M19':'deco_geometric','M20':'wood_slat'}

def handrail_material(B):
 bid=B.spec['building_id'];family=B.spec['style_family']
 if bid=='M07' and B.spec.get('gothic18'):return 'Gothic18GuardStone'
 if bid in ['M02','M07']:return 'StairTread'
 if bid in ['M03','M05','M15','M20','M21','M22','M23']:return 'PaleWood' if bid=='M20' else 'Wood'
 return 'Brass' if family in ['neoclassical','art_deco','victorian_natural'] else 'Steel'

def styled_infill(B,name,centers,height=1.08):
 """Return exact meshes; no solid proxy is substituted for visible open infill."""
 bid=B.spec['building_id'];style=SPECIAL.get(bid)
 if not style:return False
 p=np.asarray(centers,dtype=float);assert len(p)>=2
 if style=='deco_geometric':
  # H/helix raw handrail samples can be sparse: add actual protective rods.
  dense=[p[0]]
  for a,b in zip(p[:-1],p[1:]):
   count=max(1,math.ceil(float(np.linalg.norm((b-a)[:2]))/.10))
   dense.extend(a+(b-a)*j/count for j in range(1,count+1))
  p=np.asarray(dense);assert np.linalg.norm(np.diff(p[:,:2],axis=0),axis=1).max()<=.100001
 from staircase_core import Geometry
 G=Geometry();verts=[];faces=[]
 def append(v,f):
  offset=len(verts);verts.extend(v);faces.extend(tuple(offset+i for i in face) for face in f)
 if style.endswith('_panel'):
  # 80 mm panels remain within the original 1.6 m stair's >=1.4 m clear width.
  for i,c in enumerate(p):
   tangent=p[min(i+1,len(p)-1)]-p[max(i-1,0)];normal=np.cross(tangent,[0,0,1]);normal/=max(np.linalg.norm(normal),1e-9)
   for q in [c+normal*.04-[0,0,height+.05],c-normal*.04-[0,0,height+.05],c-normal*.04-[0,0,.025],c+normal*.04-[0,0,.025]]:verts.append(q)
  for i in range(len(p)-1):
   for j in range(4):faces.append((4*i+j,4*i+(j+1)%4,4*(i+1)+(j+1)%4,4*(i+1)+j))
  faces.extend([(3,2,1,0),tuple(4*(len(p)-1)+j for j in range(4))]);mat='Gothic18GuardStone' if B.spec.get('gothic18') and bid=='M07' else 'StairTread'
 elif style=='deco_geometric':
  # Full protective rods remain <=105mm apart; actual brass diamonds sit in
  # the same guard plane and are also exact closed collision tubes.
  for j,c in enumerate(p):G.tube(name+f'_DecoRod{j}',[c-[0,0,height+.025],c-[0,0,.025]],.012)
  distance=np.r_[0,np.cumsum(np.linalg.norm(np.diff(p,axis=0),axis=1))];last=-1.
  for j,c in enumerate(p):
   if distance[j]-last<.85 or j==0 or j==len(p)-1:continue
   tangent=p[min(j+1,len(p)-1)]-p[max(j-1,0)];tangent/=max(np.linalg.norm(tangent[:2]),1e-9);center=c-[0,0,height*.53];a=center-tangent*.24;b=center+[0,0,.25];cc=center+tangent*.24;dd=center-[0,0,.25];G.tube(name+f'_DecoDiamond{j}',[a,b,cc,dd,a],.012);last=distance[j]
  for row in G.parts:append(row['vertices'],row['faces'])
  mat='Brass'
 elif style in ['wire_mesh','horizontal_steel']:
  # Actual 80-90 mm openings: vertical wires plus continuous horizontal wires.
  if style=='wire_mesh':
   for j,c in enumerate(p):G.tube(name+f'_VerticalWire{j}',[c-[0,0,height+.025],c-[0,0,.055]],.0045)
  for j,dz in enumerate(np.arange(.085,height,.085)):G.tube(name+f'_HorizontalWire{j}',p-[0,0,dz],.0045 if style=='wire_mesh' else .012)
  distance=np.r_[0,np.cumsum(np.linalg.norm(np.diff(p,axis=0),axis=1))];last=-2.
  for j,c in enumerate(p):
   if distance[j]-last>=1. or j==len(p)-1:G.tube(name+f'_FramePost{j}',[c-[0,0,height+.025],c],.022);last=distance[j]
  for row in G.parts:append(row['vertices'],row['faces'])
  mat='Steel'
 else:
  # Original path samples are <=105 mm apart; even the narrowest turned neck
  # leaves <110 mm gaps. All profiles are closed, metric collision meshes.
  for i,c in enumerate(p):
   base=c[2]-height-.015;top=c[2]-.025;length=top-base
   if style=='wood_slat':
    tangent=p[min(i+1,len(p)-1)]-p[max(i-1,0)];tangent[2]=0;tangent/=max(np.linalg.norm(tangent),1e-9);normal=np.cross(tangent,[0,0,1]);v=[]
    for z in [base,top]:
     for a,b in [(-1,-1),(1,-1),(1,1),(-1,1)]:v.append(np.array([c[0],c[1],z])+tangent*a*.022+normal*b*.022)
    append(v,[(3,2,1,0),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)])
   else:
    profile=[(0,.035),(.08,.035),(.12,.025),(.24,.018),(.45,.033),(.57,.035),(.72,.024),(.89,.017),(.94,.034),(1,.034)];v=[];f=[];n=12
    for t,r in profile:
     for a in np.linspace(0,math.tau,n,endpoint=False):v.append((c[0]+r*math.cos(a),c[1]+r*math.sin(a),base+t*length))
    for k in range(len(profile)-1):
     for j in range(n):f.append((k*n+j,k*n+(j+1)%n,(k+1)*n+(j+1)%n,(k+1)*n+j))
    f.extend([tuple(reversed(range(n))),tuple((len(profile)-1)*n+j for j in range(n))]);append(v,f)
  mat='StairTread' if style=='stone_baluster' else 'PaleWood' if style=='wood_slat' else 'Wood'
 B.mesh(name+'_'+style,verts,faces,mat,True,'guard')
 B.layout.append({'kind':'railing_assembly','name':name,'style':style,'material':mat,'collision':'same exact authored closed meshes as visual','sample_spacing_max_m':float(np.linalg.norm(np.diff(p[:,:2],axis=0),axis=1).max()),'panel_thickness_m':.08 if style.endswith('_panel') else None})
 return True
