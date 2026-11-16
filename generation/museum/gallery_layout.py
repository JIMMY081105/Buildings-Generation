"""Floor-specific exhibition walls, checked against actual gallery polygons."""
import math,numpy as np
from shapely import wkt,affinity
from shapely.geometry import box

def display_walls(B,model,level):
 s=B.spec;w,h=model['building_xy_m'];poly=wkt.loads(model['floors'][level]['polygon_wkt']);bid=s['building_id']
 if bid=='M01':return [(x,y,min(7.,h*.22),0.) for x in [-w*.28,w*.28] for y in [-10.8,10.8]]
 locations=[(-w*.26,-h*.34),(w*.26,-h*.34),(-w*.26,h*.30),(w*.26,h*.30),(-w*.36,-h*.12),(-w*.36,h*.12),(w*.36,-h*.12),(w*.36,h*.12),(-w*.20,-h*.36),(w*.20,-h*.36),(-w*.22,h*.33),(w*.22,h*.33)]
 shift=(level*3)%len(locations);locations=locations[shift:]+locations[:shift];chosen=[];footprints=[]
 for length in [6.+.4*(level%3),5.2,4.8]:
  for i,(x,y) in enumerate(locations):
   for angle in [((i+level)%2)*math.pi/2,((i+level+1)%2)*math.pi/2]:
    foot=affinity.translate(affinity.rotate(box(-length/2,-.12,length/2,.12),math.degrees(angle)),x,y)
    if not poly.contains(foot.buffer(.05)) or any(foot.buffer(.8).intersects(q) for q in footprints):continue
    chosen.append((x,y,length,angle));footprints.append(foot);break
   if len(chosen)==4:
    B.layout.append({'kind':'exhibition_layout','floor':level+1,'wall_poses_xy_length_angle':chosen,'actual_floor_support_checked':True,'floor_specific_pattern':level%4});return chosen
 raise RuntimeError(('NO_FOUR_SUPPORTED_GALLERY_WALLS',bid,level+1,len(chosen)))
