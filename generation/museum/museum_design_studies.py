"""Original Scandinavian-style design studies; no designer/provenance claim."""
import math,json,hashlib
import numpy as np,bpy

def furniture(B,name,kind,x,y,z,yaw):
 B.material('DesignAsh24',(.57,.43,.25),'long_board',1.6,.50);B.material('DesignWool24',(.24,.27,.26),'stone',.35,.92);B.material('DesignShell24',(.42,.29,.15),'long_board',1.6,.54)
 obs=[]
 def box(label,lo,hi,mat,bevel=.012):
  ob=B.box(name+'_'+label,lo,hi,mat,True,'procedural_exhibit')
  if bevel:
   mod=ob.modifiers.new('actual rounded timber or cushion edges','BEVEL');mod.width=bevel;mod.segments=3
   normals=ob.modifiers.new('weighted face normals','WEIGHTED_NORMAL');normals.keep_sharp=True
  obs.append(ob);return ob
 def tube(label,points,radius,mat='DesignAsh24'):
  ob=B.tube(name+'_'+label,points,radius,mat,True,'procedural_exhibit');obs.append(ob);return ob
 if kind=='slatted_bench':
  for i in range(7):box('SeatSlat'+str(i),(-.80,-.26+i*.077,.405),(.80,-.20+i*.077,.45),'DesignAsh24',.009)
  for side in [-1,1]:
   box('Rail'+str(side),(-.69,side*.205-.023,.335),(.69,side*.205+.023,.405),'DesignAsh24')
   for end in [-1,1]:tube(f'SplayedLeg{side}_{end}',[(end*.66,side*.22,.025),(end*.57,side*.17,.42)],.034)
 elif kind=='bentwood_armchair':
  box('SeatCushion',(-.31,-.28,.37),(.31,.30,.47),'DesignWool24',.035)
  # Solid bent shell, swept as a finite-thickness curved section.
  profile=[(-.30,.36),(.20,.36),(.30,.41),(.37,.58),(.40,.90)];verts=[]
  for side in [-.34,.34]:
   verts += [(side,yy,zz) for yy,zz in profile]+[(side,yy+.022,zz-.022) for yy,zz in profile]
  n=len(profile);faces=[]
  for side in [0,2*n]:
   for i in range(n-1):faces.append((side+i,side+i+1,side+n+i+1,side+n+i))
  for layer in [0,n]:
   for i in range(n-1):faces.append((layer+i,layer+i+1,2*n+layer+i+1,2*n+layer+i))
  faces += [(0,n,3*n,2*n),(n-1,2*n-1,4*n-1,3*n-1)]
  ob=B.mesh(name+'_BentPlywoodShell',verts,faces,'DesignShell24',True,'procedural_exhibit');obs.append(ob)
  for side in [-1,1]:
   tube('Arm'+str(side),[(side*.38,-.25,.63),(side*.38,.20,.66),(side*.34,.33,.69)],.028)
   for end in [-1,1]:tube(f'Leg{side}_{end}',[(side*.35,end*.29,.025),(side*.29,end*.21,.39)],.028)
 elif kind=='linen_settee':
  box('TimberBase',(-.73,-.33,.20),(.73,.33,.29),'DesignAsh24',.025)
  for side in [-1,1]:
   box('Seat'+str(side),(side*.35-.32,-.30,.29),(side*.35+.32,.24,.41),'DesignWool24',.045)
   back=box('Back'+str(side),(side*.35-.32,.20,.39),(side*.35+.32,.36,.77),'DesignWool24',.045)
   for end in [-1,1]:tube(f'Leg{side}_{end}',[(side*.63,end*.24,.025),(side*.59,end*.22,.22)],.030)
   tube('OpenArm'+str(side),[(side*.73,-.29,.30),(side*.73,-.24,.59),(side*.73,.32,.64)],.035)
 else:raise ValueError(kind)
 bpy.context.view_layer.update();digest=hashlib.sha256()
 for ob in obs:
  evaluated=ob.evaluated_get(bpy.context.evaluated_depsgraph_get());me=evaluated.to_mesh();digest.update(np.asarray([v.co[:] for v in me.vertices],np.float32).tobytes());digest.update(json.dumps([list(p.vertices) for p in me.polygons]).encode());evaluated.to_mesh_clear()
 source=digest.hexdigest();key='procedural:NordicDesign24:'+kind
 for ob in obs:ob.rotation_euler.z=yaw;ob.location=(x,y,z)
 bpy.context.view_layer.update();bounds=[B.bounds(ob) for ob in obs];lo=np.min([q[0] for q in bounds],0);hi=np.max([q[1] for q in bounds],0)
 B.layout.append({'kind':'exhibit','name':name,'floor':round((z-.215)/B.spec['floor_height_m'])+1,'source_key':key,'source_model_sha256':source,'source_asset_title':'Original Scandinavian-style '+kind+' design study, no historical designer attribution','actual_min_m':lo.tolist(),'actual_max_m':hi.tolist(),'support_z_m':z,'support_embed_m':0,'theme_category':'original_modern_design','collision':'Actual closed beveled or swept wooden/cushion meshes; no approximate display-only collision.'})
 B.licenses.setdefault(key,{'source':'procedural','id':key,'source_sha256':source,'license':'procedural original','title':B.layout[-1]['source_asset_title'],'method':'Original metric ash/linen design, exact finite bent shell, rounded cushions/timber and splayed legs; no upstream factory invocation.'})
