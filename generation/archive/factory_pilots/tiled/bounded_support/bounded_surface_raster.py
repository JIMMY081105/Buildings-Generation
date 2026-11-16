"""Resource orchestration only: union unchanged fill=False rasterizer calls by faces.

This module does not replace or patch a frozen callable and publishes no gate verdict.
The estimated work cap groups independent faces; one face remains indivisible.
"""
import numpy as np
def face_chunks(vertices,faces,pitch,max_terminal_faces=4**10):
 vertices=np.asarray(vertices,dtype=np.float64);faces=np.asarray(faces,dtype=np.int64)
 if max_terminal_faces<1 or pitch<=0:raise ValueError('Positive resource budget and pitch required')
 if faces.ndim!=2 or faces.shape[1]!=3 or not len(faces):raise ValueError('Nonempty triangle faces required')
 triangles=vertices[faces];lengths=np.linalg.norm(np.diff(triangles[:,[0,1,2,0],:],axis=1),axis=2).max(axis=1)
 levels=np.maximum(0,np.ceil(np.log2(np.maximum(lengths/(pitch/2),1)))).astype(np.int64)
 if np.any(levels>10):raise ValueError('Original subdivide max_iter10 cannot cover this face; do not change it')
 costs=4**levels;start=0;total=0
 for i,cost in enumerate(costs):
  cost=int(cost)
  if cost>max_terminal_faces:raise ValueError('One indivisible face exceeds the reviewed resource work cap')
  if i>start and total+cost>max_terminal_faces:
   yield faces[start:i],total;start=i;total=0
  total+=cost
 if start<len(faces):yield faces[start:],total
def rasterize_surface_bounded(rasterize,occupancy,grid,vertices,faces,*,fill,max_terminal_faces=4**10):
 if fill is not False:raise ValueError('Face unions are authorized only for original fill=False surface rasterization')
 count=0;max_work=0;source_face_count=0
 for chunk,work in face_chunks(vertices,faces,grid.fine_voxel_size_m,max_terminal_faces):
  rasterize(occupancy,grid,vertices,chunk,fill=False)
  count+=1;max_work=max(max_work,work);source_face_count+=len(chunk)
 if source_face_count!=len(faces):raise RuntimeError('Face partition coverage differs')
 return dict(original_call_count=count,source_triangle_count=source_face_count,max_estimated_terminal_faces_per_call=max_work)
