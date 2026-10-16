"""Shared bounded resource orchestration around unchanged original scientific APIs."""
import json,time
from pathlib import Path
def rasterize_storey_bands_bounded(source,tempdir,log_path,max_terminal_faces=4**10):
 import numpy as np
 from pxr import Usd,UsdGeom,UsdPhysics
 from protomotions.simulator.base_simulator import scene_storey_bands as bands
 from protomotions.simulator.base_simulator import scene_geometry_cache as gridmod
 from protomotions.simulator.isaaclab.utils import scene_geometry_baking as baking
 from bounded_surface_raster import rasterize_surface_bounded
 source=Path(source).expanduser().resolve();tempdir=Path(tempdir);log_path=Path(log_path)
 if not source.is_file()or not tempdir.is_dir()or log_path.exists():raise ValueError('Actual source, owned local temporary directory and fresh call log required')
 stage=Usd.Stage.Open(str(source))
 if stage is None:raise ValueError('Could not open actual current source')
 mpu=float(UsdGeom.GetStageMetersPerUnit(stage));xc=UsdGeom.XformCache(Usd.TimeCode.Default());paths=[];stairs=set();bmin=np.full(3,np.inf);bmax=np.full(3,-np.inf)
 for prim in stage.Traverse():
  if not(prim.IsA(UsdGeom.Mesh)and prim.HasAPI(UsdPhysics.CollisionAPI))or UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()is False:continue
  v,f=baking._authored_mesh_geometry_m(stage,prim,mpu,xc);path=str(prim.GetPath());paths.append(path)
  if path=='/World/Stair'or path.startswith('/World/Stair/'):stairs.add(path)
  bmin=np.minimum(bmin,v.min(axis=0));bmax=np.maximum(bmax,v.max(axis=0))
 if not paths or not stairs:raise ValueError('Actual enabled world and stair meshes required')
 grid=gridmod.make_aligned_grid_spec(bmin,bmax)
 occupancy=np.lib.format.open_memmap(tempdir/'bounded_full_occupancy.npy',mode='w+',dtype=np.bool_,shape=grid.fine_shape);occupancy[:]=False
 stair=np.lib.format.open_memmap(tempdir/'bounded_full_stair_mask.npy',mode='w+',dtype=np.bool_,shape=grid.fine_shape);stair[:]=False
 allcalls=0;maxwork=0;splitmeshes=0;start=time.monotonic()
 with log_path.open('x')as log:
  for i,path in enumerate(paths):
   prim=stage.GetPrimAtPath(path);v,f=baking._authored_mesh_geometry_m(stage,prim,mpu,xc);calls=rasterize_surface_bounded(baking.rasterize_triangle_mesh,occupancy,grid,v,f,fill=False,max_terminal_faces=max_terminal_faces)
   staircalls=rasterize_surface_bounded(baking.rasterize_triangle_mesh,stair,grid,v,f,fill=False,max_terminal_faces=max_terminal_faces)if path in stairs else None
   allcalls+=calls['original_call_count']+(staircalls or{}).get('original_call_count',0);maxwork=max(maxwork,calls['max_estimated_terminal_faces_per_call']);splitmeshes+=int(calls['original_call_count']>1)
   log.write(json.dumps(dict(mesh_index=i,prim_path=path,surface_calls=calls,stair_calls=staircalls,elapsed_s=time.monotonic()-start))+'\n');log.flush()
   if(i+1)%50==0:occupancy.flush();stair.flush();print('BOUNDED_ORIGINAL_SURFACE_MESHES',i+1,'OF',len(paths),'CALLS',allcalls,flush=True)
 occupancy.flush();stair.flush()
 peak,cut,split=bands._split_storeys(occupancy,grid);f2,g2=bands._crop_grid(grid,occupancy,cut,occupancy.shape[2]);s2,_=bands._crop_grid(grid,stair,cut,occupancy.shape[2]);terrain,top,modelling=bands._bounded_floor1_terrain(occupancy,grid,peak);stop=min(cut+1,occupancy.shape[2]);f1,g1=bands._crop_grid(grid,terrain,0,stop);s1,_=bands._crop_grid(grid,stair,0,stop);del terrain
 raster=dict(enabled_mesh_collider_count=len(paths),enabled_stair_mesh_collider_count=len(stairs),grid_scene_bounds_min_m=list(grid.scene_bounds_min_m),grid_scene_bounds_max_m=list(grid.scene_bounds_max_m),grid_fine_shape=list(grid.fine_shape))
 extraction=bands.StoreyBandExtraction(bands=(bands.StoreyBand(g1,f1,s1,'Floor1'),bands.StoreyBand(g2,f2,s2,'Floor2')),full_occupancy=occupancy,full_stair_mask=stair,full_grid_spec=grid,peak_z_index=peak,cut_z_index=cut,floor1_support_top_z_m=top,rasterization=raster,storey_split=split,floor1_modelling=modelling)
 extraction.validate()
 return extraction,dict(original_mesh_count=len(paths),split_mesh_count=splitmeshes,total_original_surface_function_calls=allcalls,max_estimated_terminal_faces_per_call=maxwork,local_ephemeral_temp_path=str(tempdir),original_precompute_one_called=False,original_rasterize_storey_bands_called=False,original_scientific_functions_modified=False,original_scientific_thresholds_modified=False)
