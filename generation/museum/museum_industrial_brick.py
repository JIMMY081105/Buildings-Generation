"""Exposed red brick, prominent clipped sawtooth roof and external factory stack."""
import numpy as np
from shapely.geometry import box,LineString
from shapely.ops import triangulate
from building_core import extrude
from staircase_core import Geometry

def facade(B,model,shape,opening):
 s=B.spec;w,h=model['building_xy_m'];Z=s['floors']*s['floor_height_m'];outline=shape.buffer(.75,join_style=2);period=5.5;rise=3.4;roof_rows=[]
 B.material('Industrial27_Roof',(.075,.085,.085),'coated_steel_roof',1.,.72,.55,source_hint='metal')
 ceiling_beams=[]
 for beam_index,yb in enumerate(np.arange(-h/2+2.,h/2-1.,5.5)):
  clipped=shape.buffer(-.16,join_style=2).intersection(LineString([(-w*2,yb),(w*2,yb)]));lines=[clipped] if clipped.geom_type=='LineString' else list(clipped.geoms) if hasattr(clipped,'geoms') else []
  for section,line in enumerate(lines):
   if line.length<1.:continue
   aa,bb=list(line.coords)[0],list(line.coords)[-1];x0,x1=sorted([aa[0],bb[0]])
   for suffix,yhalf,z0,z1 in [('LowerFlange',.20,Z-.65,Z-.57),('Web',.055,Z-.57,Z-.38),('UpperFlange',.20,Z-.38,Z-.30)]:B.box(f'Industrial27_CeilingBeam{beam_index}_{section}_{suffix}',(x0,yb-yhalf,z0),(x1,yb+yhalf,z1),'Steel',True,'ceiling_beam')
   ceiling_beams.append({'x_span_m':[x0,x1],'y_m':float(yb),'bottom_z_m':Z-.65,'top_z_m':Z-.30})
 for i,y in enumerate(np.arange(-h/2-.75,h/2+.75,period)):
  end=min(y+period,h/2+.75);piece=outline.intersection(box(-w*3,y,w*3,end));vertices=[];faces=[];lookup={}
  def point(x,yy):return (float(x),float(yy),float(Z+.05+rise*(yy-y)/period))
  for tri in triangulate(piece):
   if not piece.covers(tri):continue
   coords=list(tri.exterior.coords)[:3]
   if np.cross(np.asarray(coords[1])-coords[0],np.asarray(coords[2])-coords[0])<0:coords.reverse()
   indices=[]
   for x,yy in coords:
    key=(round(x,8),round(yy,8))
    if key not in lookup:lookup[key]=len(vertices);vertices.append(point(x,yy))
    indices.append(lookup[key])
   faces.append(tuple(indices))
  assert faces;B.mesh(f'Roof_Industrial27_Sawtooth{i}',vertices,faces,'Industrial27_Roof',True,'roof_structure')
  sections=shape.buffer(.74,join_style=2).intersection(LineString([(-w*3,end-.001),(w*3,end-.001)]))
  lines=[sections] if sections.geom_type=='LineString' else list(sections.geoms) if hasattr(sections,'geoms') else []
  for j,line in enumerate(lines):
   if line.length<.02:continue
   a,b=list(line.coords)[0],list(line.coords)[-1];top=Z+.05+rise*(end-y)/period
   B.mesh(f'Skylight_Industrial27_Clerestory{i}_{j}',[(a[0],end,Z+.05),(b[0],end,Z+.05),(b[0],end,top),(a[0],end,top)],[(0,1,2,3)],'WindowGlass',True,'roof_glass')
   for x in np.arange(min(a[0],b[0]),max(a[0],b[0])+.001,2.5):B.tube(f'Roof_Industrial27_Mullion{i}_{j}_{x}',[(x,end,Z+.05),(x,end,top)],.035,'Steel',True,'roof_frame')
  polys=[piece] if piece.geom_type=='Polygon' else list(piece.geoms)
  for pi,poly in enumerate(polys):
   coords=list(poly.exterior.coords)
   for k,(a,b) in enumerate(zip(coords,coords[1:])):
    if abs(a[1]-b[1])<.0001:continue
    B.mesh(f'Roof_Industrial27_BrickToothEnd{i}_{pi}_{k}',[(a[0],a[1],Z),(b[0],b[1],Z),point(*b),point(*a)],[(0,1,2,3)],'ExteriorStone',True,'roof_structure')
  roof_rows.append({'name':f'Roof_Industrial27_Sawtooth{i}','actual_projected_area_m2':piece.area,'rise_m':rise,'period_m':period})
 # A real masonry stack sits outside the gallery envelope, preserving every
 # existing display and access corridor. Hollow cap, not a painted silhouette.
 x=-w/2-1.45;y=h/2-4.;G=Geometry();outer=box(x-.7,y-.7,x+.7,y+.7);ring=outer.difference(box(x-.36,y-.36,x+.36,y+.36));extrude(G,'Industrial27_BrickStack',ring,0.,Z+6.2,'facade','ExteriorStone');extrude(G,'Industrial27_StackCap',outer.buffer(.14).difference(box(x-.36,y-.36,x+.36,y+.36)),Z+6.1,Z+6.35,'facade','Steel')
 for q in G.parts:B.mesh(q['name'],q['vertices'],q['faces'],q['material'],True,q['role'])
 B.layout.append({'name':'Industrial27_FactoryShell','kind':'architecture','roof_teeth':roof_rows,'actual_clipped_steel_ceiling_beams':ceiling_beams,'stack_center_xy_m':[x,y],'stack_top_z_m':Z+6.35,'exterior':'Exposed red old brick metric230x75mm plus mortar, dark steel windows and steel/glass open entry with metal canopy. Actual native01 must show unmistakable sawtooth silhouette and red brick; source audit alone is not appearance acceptance.'})
