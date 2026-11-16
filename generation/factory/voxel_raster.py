"""Factory80 v4 float64 CUDA surface subdivision and conservative 5 cm raster.

The source lattice is global round-to-even, matching trimesh 4.5.1's
voxelize_subdivide(fill=False). Repeated hits are ORed before conservative
splat, so work does not scale with duplicated final sample vertices.
"""
from pathlib import Path
import argparse, hashlib, json, os, resource, subprocess, time
import numpy as np
import torch

PITCH=.05
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
    return h.hexdigest()

def load_array(row):
    return np.memmap(row['path'],mode='r',dtype=row['dtype'],offset=row['byte_offset'],shape=tuple(row['shape']))

def splat_source(source, source_min, shape, origin):
    result=torch.zeros(tuple(shape),device='cuda',dtype=torch.bool)
    points=source.nonzero().to(torch.float64)+source_min
    for start in range(0,len(points),2_000_000):
        centers=points[start:start+2_000_000]*PITCH
        lo=torch.floor((centers-PITCH/2-origin+PITCH*1e-9)/PITCH).long()
        hi=torch.floor((centers+PITCH/2-origin-PITCH*1e-9)/PITCH).long()
        for x in (0,1):
            for y in (0,1):
                for z in (0,1):
                    ijk=torch.stack([(lo,hi)[x][:,0],(lo,hi)[y][:,1],(lo,hi)[z][:,2]],1)
                    good=((ijk>=0)&(ijk<shape)).all(1); ijk=ijk[good]
                    result[ijk[:,0],ijk[:,1],ijk[:,2]]=True
    return result

def splat_cells(cells,source_min,shape,origin,lattice_shape):
    cells=torch.unique(torch.cat(cells))
    yz=lattice_shape[1]*lattice_shape[2]
    xyz=torch.stack([cells//yz,(cells//lattice_shape[2])%lattice_shape[1],cells%lattice_shape[2]],1)
    centers=(xyz+source_min).double()*PITCH
    lo=torch.floor((centers-PITCH/2-origin+PITCH*1e-9)/PITCH).long();hi=torch.floor((centers+PITCH/2-origin-PITCH*1e-9)/PITCH).long();all_indices=[]
    for x in (0,1):
        for y in (0,1):
            for z in (0,1):
                ijk=torch.stack([(lo,hi)[x][:,0],(lo,hi)[y][:,1],(lo,hi)[z][:,2]],1)
                good=((ijk>=0)&(ijk<shape)).all(1);all_indices.append(ijk[good])
    return torch.unique(torch.cat(all_indices),dim=0)

def raster_spool(spool,shape,origin,out,batch_triangles=180_000):
    device='cuda'; origin=torch.tensor(origin,device=device,dtype=torch.float64)
    shape_tensor=torch.tensor(shape,device=device,dtype=torch.int64)
    low=np.floor(np.array(spool['bounds_min_m'])/PITCH).astype(np.int64)-2
    high=np.ceil(np.array(spool['bounds_max_m'])/PITCH).astype(np.int64)+3
    lattice_shape=tuple((high-low).tolist()); low_gpu=torch.tensor(low,device=device)
    surface=torch.zeros(lattice_shape,device=device,dtype=torch.bool)
    stairs=torch.zeros_like(surface)
    nonstairs=torch.zeros_like(surface)
    records=spool['records']; total_faces=sum(x['face_count'] for x in records)
    owners={};provenance={};owner_index={}
    for i,row in enumerate(records):
        if row['stair']:
            n=len(owners)+1;owner_index[i]=n;owners[n]=dict(prim_path=row['path'],stair_id=row.get('stair_id') or 'all_stairs');provenance[n]=[]
    consumed=0; start=time.monotonic(); last_print=start
    def mark(points,target,owner_ids=None):
        cells=torch.round(points.reshape(-1,3)/PITCH).long()-low_gpu
        target[cells[:,0],cells[:,1],cells[:,2]]=True
        if owner_ids is not None:
            ids=owner_ids.repeat_interleave(3)
            for owner in torch.unique(owner_ids).cpu().tolist():
                if not owner:continue
                selected=cells[ids==owner]
                linear=(selected[:,0]*lattice_shape[1]+selected[:,1])*lattice_shape[2]+selected[:,2]
                provenance[owner].append(torch.unique(linear))
    def batches():
        chunks=[]; flags=[]; count=0
        for mesh_index,row in enumerate(records):
            verts=load_array(row['vertices']); faces=load_array(row['faces'])
            for face_start in range(0,len(faces),batch_triangles):
                f=faces[face_start:face_start+batch_triangles]
                tri=np.array(verts[f],copy=True)
                if row.get('world_transform') is not None:
                    matrix=np.array(row['world_transform']);tri=tri@matrix[:3,:3]+matrix[3,:3]
                chunks.append(tri);flags.append(np.full(len(f),owner_index.get(mesh_index,0),dtype=np.int32));count+=len(f)
                if count>=batch_triangles:
                    yield np.concatenate(chunks),np.concatenate(flags),mesh_index+1
                    chunks=[];flags=[];count=0
        if chunks:yield np.concatenate(chunks),np.concatenate(flags),len(records)
    for cpu_tri,cpu_stair,mesh_index in batches():
            triangles=torch.as_tensor(cpu_tri,device=device,dtype=torch.float64)
            stair_flags=torch.as_tensor(cpu_stair,device=device)
            # A queue bounds exponential growth without a CPU numerical path.
            queue=[(triangles,stair_flags,0)]
            while queue:
                tri,stair_flags,level=queue.pop()
                lengths=((tri[:,[1,2,0]]-tri)**2).sum(2)
                long=(lengths>(PITCH/2)**2).any(1)
                if (~long).any():
                    mark(tri[~long],surface)
                    selected=(~long)&(stair_flags>0)
                    if selected.any():mark(tri[selected],stairs,stair_flags[selected])
                    selected=(~long)&(stair_flags==0)
                    if selected.any():mark(tri[selected],nonstairs)
                stair_flags=stair_flags[long]
                tri=tri[long]
                if not len(tri):continue
                if level>=10:raise ValueError('Original trimesh max_iter=10 exceeded')
                # Trimesh midpoint arithmetic: mean(two float64 endpoints).
                for j in range(0,len(tri),180_000):
                    t=tri[j:j+180_000]; a,b,c=t.unbind(1)
                    ab=(a+b)/2;bc=(b+c)/2;ca=(c+a)/2
                    children=torch.stack([torch.stack([a,ab,ca],1),torch.stack([ab,b,bc],1),torch.stack([ca,bc,c],1),torch.stack([ab,bc,ca],1)],1).reshape(-1,3,3)
                    queue.append((children,stair_flags[j:j+180_000].repeat_interleave(4),level+1))
            consumed+=len(cpu_tri)
            if time.monotonic()-last_print>15:
                print(json.dumps(dict(meshes_done=mesh_index,meshes_total=len(records),faces_done=consumed,faces_total=total_faces,elapsed_s=time.monotonic()-start)),flush=True);last_print=time.monotonic()
    world=splat_source(surface,low_gpu,shape_tensor,origin)
    stair=splat_source(stairs,low_gpu,shape_tensor,origin)
    nonstair=splat_source(nonstairs,low_gpu,shape_tensor,origin)
    solid_extra={}
    for i,row in enumerate(records):
        if not row['stair'] or 'solid_box_corners_m' not in row:continue
        corners=np.asarray(row['solid_box_corners_m']);matrix=np.asarray(row.get('world_transform',np.eye(4)));corners=corners@matrix[:3,:3]+matrix[3,:3]
        lower=corners.min(0);upper=corners.max(0)
        expected=np.array([[x,y,z] for x in [lower[0],upper[0]] for y in [lower[1],upper[1]] for z in [lower[2],upper[2]]])
        if any(np.linalg.norm(expected-p,axis=1).min()>1e-6 for p in corners):raise ValueError('Exact solid stair box rotated off the authored cardinal axes; exact OBB voxel intersection required')
        lo=torch.floor((torch.tensor(lower,device=device)-origin+PITCH*1e-9)/PITCH).long().clamp_min(0)
        hi=torch.ceil((torch.tensor(upper,device=device)-origin-PITCH*1e-9)/PITCH).long()-1;hi=torch.minimum(hi,shape_tensor-1)
        axes=[torch.arange(int(lo[j]),int(hi[j])+1,device=device) for j in range(3)]
        ijk=torch.stack(torch.meshgrid(*axes,indexing='ij'),-1).reshape(-1,3)
        world[ijk[:,0],ijk[:,1],ijk[:,2]]=True;stair[ijk[:,0],ijk[:,1],ijk[:,2]]=True;solid_extra[owner_index[i]]=ijk
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    np.save(out/'occupancy.npy',world.cpu().numpy());np.save(out/'stair_mask.npy',stair.cpu().numpy());np.save(out/'nonstair_occupancy.npy',nonstair.cpu().numpy())
    by_stair={};arrays={};metadata={}
    for owner,chunks in provenance.items():
        if not chunks:continue
        ijk=splat_cells(chunks,low_gpu,shape_tensor,origin,lattice_shape)
        if owner in solid_extra:ijk=torch.unique(torch.cat([ijk,solid_extra[owner]]),dim=0)
        key='collider_'+str(owner);arrays[key]=ijk.cpu().numpy();metadata[key]=owners[owner]
        sid=owners[owner]['stair_id'];by_stair.setdefault(sid,[]).append(ijk)
    arrays['metadata_json_utf8']=np.frombuffer(json.dumps(metadata).encode(),dtype=np.uint8)
    np.savez(out/'stair_collision_provenance.npz',**arrays)
    stair_files={}
    for i,(sid,chunks) in enumerate(by_stair.items()):
        mask=torch.zeros_like(world);ijk=torch.cat(chunks);mask[ijk[:,0],ijk[:,1],ijk[:,2]]=True
        name=f'stair_{i:02d}_occupancy.npy';np.save(out/name,mask.cpu().numpy());stair_files[sid]=name
    (out/'STAIR_MASKS.json').write_text(json.dumps(stair_files,indent=2))
    torch.cuda.synchronize()
    return dict(mesh_count=len(records),face_count=total_faces,occupied_voxels=int(world.sum()),stair_voxels=int(stair.sum()),elapsed_s=time.monotonic()-start)

def compare(reference,candidate):
    ref=np.load(reference,mmap_mode='r'); got=np.load(candidate,mmap_mode='r')
    if ref.shape!=got.shape or ref.dtype!=got.dtype:raise ValueError('Reference shape/dtype mismatch')
    occupied=0;different=0;far=0
    # An XOR cell is within one grid-face voxel if an occupied/free transition
    # exists in its 3x3x3 reference neighbourhood; both boundary sides count.
    import torch.nn.functional as F
    for x in range(0,ref.shape[0],48):
        lo=max(0,x-1); hi=min(ref.shape[0],x+49)
        a=torch.tensor(np.array(ref[lo:hi]),device='cuda',dtype=torch.bool)
        b=torch.tensor(np.array(got[lo:hi]),device='cuda',dtype=torch.bool)
        ap=a.float()[None,None]; mx=F.max_pool3d(ap,3,1,1)[0,0]>0
        mn=F.max_pool3d(1-ap,3,1,1)[0,0]>0
        z=slice(x-lo,min(x+48,ref.shape[0])-lo)
        diff=(a!=b)[z]; occupied+=int(a[z].sum()); different+=int(diff.sum());far+=int((diff&~(mx&mn)[z]).sum())
    return dict(occupied_voxels=occupied,different_voxels=different,difference_fraction_of_occupied=different/max(occupied,1),non_boundary_differences=far,status='PASS' if different<=occupied*.001 and far==0 else 'FAIL')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--spool',type=Path);ap.add_argument('--scope',type=Path);ap.add_argument('--reference',type=Path);ap.add_argument('--manifest',type=Path);ap.add_argument('--cache',type=Path);ap.add_argument('--output',required=True,type=Path);a=ap.parse_args()
    Path('/proc/self/oom_score_adj').write_text('1000');torch.set_num_threads(8)
    subprocess.run(['nvidia-smi'],check=True)
    start=time.monotonic()
    if a.manifest:
        from voxel_extract import scene_spool,cached_sha
        manifest=json.loads(a.manifest.read_text());spool=scene_spool(manifest,a.cache or a.output.parent/'proxy_geometry_cache')
        low=np.asarray(spool['bounds_min_m']);high=np.asarray(spool['bounds_max_m']);shape=(np.ceil((high-low)/PITCH).astype(np.int64)+1)//2*2
        grid=dict(grid_fine_shape=shape.tolist(),grid_scene_bounds_min_m=low.tolist(),grid_scene_bounds_max_m=high.tolist());scope=dict(root=dict(sha256=cached_sha(a.manifest,a.cache or a.output.parent/'proxy_geometry_cache')))
    else:
        scope=json.loads(a.scope.read_text());spool=json.loads(a.spool.read_text());grid=scope['all_enabled_meshes']
    a.output.mkdir(parents=True,exist_ok=False)
    result=raster_spool(spool,grid['grid_fine_shape'],grid['grid_scene_bounds_min_m'],a.output)
    (a.output/'GRID.json').write_text(json.dumps(grid,indent=2))
    if a.reference:result['comparison']=compare(a.reference,a.output/'occupancy.npy')
    result.update(input_sha=scope['root']['sha256'],reference_sha=sha(a.reference) if a.reference else None,output_sha=sha(a.output/'occupancy.npy'),exit_code=0 if result.get('comparison',{}).get('status','PASS')=='PASS' else 1,peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,elapsed_s=time.monotonic()-start)
    (a.output/'VOXEL_RESULT.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2),flush=True)
    raise SystemExit(result['exit_code'])
if __name__=='__main__':main()
