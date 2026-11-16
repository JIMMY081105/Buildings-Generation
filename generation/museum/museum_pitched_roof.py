"""Pitch follows actual building footprint, including its L recess/courtyard holes."""
import numpy as np
from shapely.geometry import box
from shapely.ops import triangulate

def pitched_roof(B,model,shape,overhang,ridge,material='RoofTile'):
 w,h=model['building_xy_m'];Z=model['floor_height_m']*len(model['floors']);outline=shape.buffer(overhang,join_style=2);rows=[]
 nordic=B.spec['style_family']=='nordic'
 mediterranean=bool(B.spec.get('mediterranean_roof24'));east=bool(B.spec.get('east_asian13'));liner='NordicRoofLiner24' if nordic else 'EastRoofLiner33' if east else 'MediterraneanRoofLiner24'
 if nordic:B.material('NordicRoofLiner24',(.60,.48,.32),'wood',2.4,.72)
 if east:B.material(liner,(.46,.34,.21),'hinoki_plank',[3.6,4.8],.78)
 if mediterranean:B.material(liner,(.65,.61,.50),'stone',2.4,.82,source_hint='plaster')
 for sign in [-1,1]:
  piece=outline.intersection(box(-w*3,-h*3,w*3,0) if sign<0 else box(-w*3,0,w*3,h*3));vertices=[];faces=[];lookup={}
  def vi(x,y):
   key=(round(float(x),9),round(float(y),9))
   if key not in lookup:lookup[key]=len(vertices);vertices.append((key[0],key[1],Z+(ridge-Z)*(1-abs(key[1])/(h/2+overhang))))
   return lookup[key]
  for tri in triangulate(piece):
   if not piece.covers(tri):continue
   coords=list(tri.exterior.coords)[:3]
   if np.cross(np.array(coords[1])-coords[0],np.array(coords[2])-coords[0])<0:coords.reverse()
   faces.append(tuple(vi(x,y) for x,y in coords))
  assert faces,'PITCHED_ROOF_EMPTY_CLIPPED_HALF';name=f'PitchedRoof_Clipped{sign}';B.mesh(name,vertices,faces,material,False,'roof_detail');rows.append({'name':name,'projected_area_m2':piece.area,'triangle_count':len(faces)})
  if nordic or mediterranean or east:
   B.mesh(f'{liner}_{sign}',[(x,y,z-.035) for x,y,z in vertices],[tuple(reversed(face)) for face in faces],liner,False,'ceiling_detail')
 B.layout.append({'name':'pitched_roof_actual_footprint12','kind':'architecture','overhang_m':overhang,'ridge_z_m':ridge,'actual_footprint_holes':len(shape.interiors) if shape.geom_type=='Polygon' else None,'roof_halves':rows,'scope':'Actual footprint buffer, split at ridge and triangulated with hole exclusion. Courtyard inner opening and L-shaped recess retained; visual roof, physical slabs unchanged.'})
 return rows
