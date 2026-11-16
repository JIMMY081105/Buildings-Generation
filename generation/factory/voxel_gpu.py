"""Factory80 v6 typed CUDA raster with source-declared exact native stairs.

The source lattice is global round-to-even, matching trimesh 4.5.1's
voxelize_subdivide(fill=False). Repeated hits are ORed before conservative
splat, so work does not scale with duplicated final sample vertices.
Certified solid exact boxes bypass that surface path: their occupancy is
only positive-volume intersection with the actual transformed box. Every
non-stair triangle uses v3 math. Source-declared exact native stair meshes use
complete triangle/cell SAT and closed-solid signed scanline interiors.
Undeclared legacy SURFACE spools retain their original sample/splat contract.
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

def certified_box_world_bounds(row):
    """Only cache-certified exact_box corners, never an arbitrary mesh AABB."""
    if 'solid_box_corners_m' not in row:return None
    corners=np.asarray(row['solid_box_corners_m'],dtype=np.float64)
    if corners.shape!=(8,3) or not np.isfinite(corners).all():raise ValueError('Invalid source-certified exact box corners')
    matrix=np.asarray(row.get('world_transform',np.eye(4)),dtype=np.float64)
    corners=corners@matrix[:3,:3]+matrix[3,:3]
    lower=corners.min(0);upper=corners.max(0)
    expected=np.array([[x,y,z] for x in [lower[0],upper[0]] for y in [lower[1],upper[1]] for z in [lower[2],upper[2]]])
    if len(np.unique(corners,axis=0))!=8 or any(np.linalg.norm(expected-p,axis=1).min()>1e-6 for p in corners):raise ValueError('Exact solid box rotated off authored cardinal axes; exact OBB voxel intersection required')
    if not bool(np.all(upper>lower)):raise ValueError('Certified solid box has no positive volume')
    return lower,upper

def box_cell_indices(lower,upper,shape,origin,device='cuda'):
    """CUDA strict positive-volume intersection; no geometric epsilon."""
    lower=torch.as_tensor(lower,device=device,dtype=torch.float64);upper=torch.as_tensor(upper,device=device,dtype=torch.float64)
    # Expanded candidates protect a floor/ceil rounding endpoint. The final
    # predicate uses actual float64 cell boundaries and excludes zero touch.
    lo=(torch.floor((lower-origin)/PITCH).long()-1).clamp_min(0)
    hi=torch.minimum(torch.ceil((upper-origin)/PITCH).long(),shape-1)
    axes=[torch.arange(int(lo[j]),int(hi[j])+1,device=device) for j in range(3)]
    ijk=torch.stack(torch.meshgrid(*axes,indexing='ij'),-1).reshape(-1,3)
    cell_lower=origin+ijk.to(torch.float64)*PITCH;cell_upper=cell_lower+PITCH
    positive=((cell_upper>lower)&(cell_lower<upper)).all(1)
    return ijk[positive]

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

def mesh_topology(vertices,faces):
    """Exact weld/edge incidence; numerical rank separates actual planes."""
    unique,weld=np.unique(vertices,axis=0,return_inverse=True);f=weld[faces]
    edges=np.concatenate([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]])
    _,inverse,counts=np.unique(np.sort(edges,axis=1),axis=0,return_inverse=True,return_counts=True)
    balance=np.bincount(inverse,weights=np.where(edges[:,0]<edges[:,1],1,-1),minlength=len(counts))
    rank=int(np.linalg.matrix_rank(unique-unique[0]))
    if rank<3:kind='zero_thickness_surface'
    elif bool((counts==2).all()) and bool((balance==0).all()):kind='closed_oriented'
    elif bool((counts>=2).all()):kind='closed_ambiguous'
    else:kind='open_surface'
    return dict(kind=kind,rank=rank,boundary_edges=int((counts==1).sum()),nonmanifold_edges=int((counts>2).sum()))

def candidate_cell_pairs(minimum,maximum,shape,origin,device='cuda',pair_limit=131072):
    """Stream complete triangle-AABB candidate cells; never an M x F array.

    The extra index on both ends guards floor arithmetic at an exact cell
    boundary. SAT/half-open ray predicates, not this AABB, determine hits.
    """
    lo=(torch.floor((minimum-origin)/PITCH).long()-1).clamp_min(0)
    hi=torch.minimum(torch.floor((maximum-origin)/PITCH).long()+1,shape-1)
    counts=(hi-lo+1).clamp_min(0);sizes=counts.prod(1);cpu=sizes.cpu().numpy();prefix=np.r_[0,np.cumsum(cpu)]
    start=0
    while start<len(cpu):
        if cpu[start]>pair_limit:
            end=start+1
            for offset in range(0,int(cpu[start]),pair_limit):
                q=torch.arange(offset,min(int(cpu[start]),offset+pair_limit),device=device);ids=torch.full_like(q,start)
                coords=[];remain=q
                for j in range(counts.shape[1]-1,-1,-1):coords.append(remain%counts[start,j]);remain=remain//counts[start,j]
                yield ids,lo[ids]+torch.stack(coords[::-1],1)
        else:
            end=int(np.searchsorted(prefix,prefix[start]+pair_limit,side='right')-1);end=max(start+1,end)
            total=int(prefix[end]-prefix[start])
            if total:
                ids=torch.repeat_interleave(torch.arange(start,end,device=device),sizes[start:end]);q=torch.arange(total,device=device)-torch.repeat_interleave(torch.cumsum(sizes[start:end],0)-sizes[start:end],sizes[start:end]);coords=[];remain=q
                for j in range(counts.shape[1]-1,-1,-1):coords.append(remain%counts[ids,j]);remain=remain//counts[ids,j]
                yield ids,lo[ids]+torch.stack(coords[::-1],1)
        start=end

def eft_two_sum(x,y):
    total=x+y;virtual=total-x
    return total,(x-(total-virtual))+(y-virtual)

def eft_two_diff(x,y):
    total=x-y;virtual=x-total
    return total,(x-(total+virtual))+(virtual-y)

def eft_product(x,y):
    total=x*y;split=134217729.
    c=split*x;highx=c-(c-x);lowx=x-highx
    c=split*y;highy=c-(c-y);lowy=y-highy
    error=lowx*lowy-(((total-highx*highy)-lowx*highy)-highx*lowy)
    return total,error

def eft_expansion_sign(terms):
    expansion=[];finite=torch.ones_like(terms[0],dtype=torch.bool)
    for term in terms:
        finite&=torch.isfinite(term);q=term;new=[]
        for component in expansion:q,tail=eft_two_sum(q,component);new.append(tail)
        new.append(q);expansion=new
    result=torch.zeros_like(terms[0])
    for component in expansion:
        finite&=torch.isfinite(component);result=torch.where(component!=0,torch.sign(component),result)
    return result,finite

def exact_dot_difference_sign(a,b,axis):
    terms=[]
    for j in range(3):
        high,low=eft_product(a[:,j],axis[:,j]);terms.extend([low,high])
        high,low=eft_product(b[:,j],axis[:,j]);terms.extend([-low,-high])
    return eft_expansion_sign(terms)

def triangle_cell_hits(triangles,cells,origin,return_diagnostics=False):
    """13-axis SAT; adaptive exact projection comparisons at contacts.

    Rounded axes are still valid separating directions. Dot-product error
    bounds only select an exact expansion fallback, never a fuzzy overlap.
    A genuinely unresolved/nonfinite comparison is retained conservatively.
    """
    lower=origin+cells.to(torch.float64)*PITCH;upper=lower+PITCH
    edges=torch.roll(triangles,-1,1)-triangles
    hit=((triangles.amax(1)>=lower)&(triangles.amin(1)<=upper)).all(1)
    gamma=8*np.finfo(np.float64).eps/(1-8*np.finfo(np.float64).eps)
    refinements=0;unresolved=torch.zeros(len(cells),device=triangles.device,dtype=torch.bool)
    def overlap(axis):
        nonlocal refinements
        products=triangles*axis[:,None,:];projected=products.sum(2);error=gamma*products.abs().sum(2)
        minimum_corner=torch.where(axis>=0,lower,upper);maximum_corner=torch.where(axis>=0,upper,lower)
        pmin=(minimum_corner*axis).sum(1);pmax=(maximum_corner*axis).sum(1)
        emin=gamma*(minimum_corner*axis).abs().sum(1);emax=gamma*(maximum_corner*axis).abs().sum(1)
        low=projected-pmin[:,None];high=projected-pmax[:,None]
        bound_low=error+emin[:,None];bound_high=error+emax[:,None]
        separated_low=(low < -bound_low).all(1);separated_high=(high>bound_high).all(1)
        active=hit&(axis!=0).any(1)
        possible_low=(low<=bound_low).all(1)&~separated_low&active
        possible_high=(high>=-bound_high).all(1)&~separated_high&active
        for candidates,is_low in [(possible_low,True),(possible_high,False)]:
            ids=candidates.nonzero().flatten()
            if not len(ids):continue
            separated=torch.ones(len(ids),device=triangles.device,dtype=torch.bool)
            corner=minimum_corner[ids] if is_low else maximum_corner[ids]
            for j in range(3):
                sign,finite=exact_dot_difference_sign(triangles[ids,j],corner,axis[ids]);refinements+=len(ids);unresolved[ids]|=~finite;separated&=finite&((sign<0) if is_low else (sign>0))
            if is_low:separated_low[ids]=separated
            else:separated_high[ids]=separated
        return ~(separated_low|separated_high)
    hit&=overlap(torch.linalg.cross(edges[:,0],edges[:,1]))
    for j in range(3):
        for k in range(3):
            axis=torch.zeros_like(edges[:,j]);axis[:,k]=1
            hit&=overlap(torch.linalg.cross(edges[:,j],axis))
    return (hit,dict(sat_exact_projection_comparisons=refinements,sat_unresolved_retained_pairs=int((unresolved&hit).sum()))) if return_diagnostics else hit

def exact_orientation_sign(a,b,p):
    """Adaptive exact sign for binary64 input points, using GPU expansions.

    Error-free TwoDiff/TwoProduct and grow-expansion resolve shared edges;
    there is no geometric epsilon or near-depth merger.
    """
    dx,dxt=eft_two_diff(b[:,0],a[:,0]);dy,dyt=eft_two_diff(b[:,1],a[:,1]);rx,rxt=eft_two_diff(p[:,0],a[:,0]);ry,ryt=eft_two_diff(p[:,1],a[:,1])
    high1,low1=eft_product(dx,ry);high2,low2=eft_product(dy,rx)
    sign,finite=eft_expansion_sign([low1,high1,-low2,-high2])
    tails=(dxt!=0)|(dyt!=0)|(rxt!=0)|(ryt!=0)
    if bool(tails.any()):
        terms=[]
        for x in [dxt[tails],dx[tails]]:
            for y in [ryt[tails],ry[tails]]:
                high,low=eft_product(x,y);terms.extend([low,high])
        for x in [dyt[tails],dy[tails]]:
            for y in [rxt[tails],rx[tails]]:
                high,low=eft_product(x,y);terms.extend([-low,-high])
        refined,valid=eft_expansion_sign(terms);sign[tails]=refined;finite[tails]&=valid
    return sign,finite

def scanline_interior(triangles,shape,origin,topology,device='cuda',pair_limit=131072):
    """Signed closed-solid winding with half-open projected triangle edges.

    Shared diagonals are owned once by the top-left rule. No depth epsilon
    merges intersections, so real thin gaps/cavities remain distinct.
    """
    if topology['kind'] in ('zero_thickness_surface','open_surface'):
        return torch.empty((0,3),device=device,dtype=torch.int64),dict(ray_events=0,ambiguous_columns=0)
    columns=[];depths=[];deltas=[];uncertain=[]
    def cross2(a,b):return a[:,0]*b[:,1]-a[:,1]*b[:,0]
    minimum=triangles[:,:,:2].amin(1);maximum=triangles[:,:,:2].amax(1)
    for ids,xy in candidate_cell_pairs(minimum,maximum,shape[:2],origin[:2],device,pair_limit):
        tri=triangles[ids];p=origin[:2]+(xy.to(torch.float64)+.5)*PITCH
        e=tri[:,1,:2]-tri[:,0,:2];f=tri[:,2,:2]-tri[:,0,:2];left=e[:,0]*f[:,1];right=e[:,1]*f[:,0];original_area=left-right;area_sign=torch.sign(original_area)
        adaptive_area=(original_area.abs()<=4*np.finfo(np.float64).eps*(left.abs()+right.abs()))&((left!=0)|(right!=0))
        if bool(adaptive_area.any()):
            exact,finite=exact_orientation_sign(tri[adaptive_area,0,:2],tri[adaptive_area,1,:2],tri[adaptive_area,2,:2]);area_sign[adaptive_area]=exact
            if bool((~finite).any()):uncertain.append((xy[adaptive_area,0]*shape[1]+xy[adaptive_area,1])[~finite])
        nonvertical=area_sign!=0
        if not bool(nonvertical.any()):continue
        tri=tri[nonvertical];xy=xy[nonvertical];p=p[nonvertical];area=area_sign[nonvertical]
        # CCW only for projected ownership; original normal still supplies
        # the signed winding event, including inward cavity surfaces.
        swapped=tri[:,[0,2,1]];tri=torch.where((area<0)[:,None,None],swapped,tri)
        inside=torch.ones(len(tri),device=device,dtype=torch.bool);numeric_uncertain=torch.zeros_like(inside)
        for j in range(3):
            a=tri[:,j,:2];b=tri[:,(j+1)%3,:2];edge=b-a;rel=p-a
            left=edge[:,0]*rel[:,1];right=edge[:,1]*rel[:,0];value=left-right
            top_left=(edge[:,1]>0)|((edge[:,1]==0)&(edge[:,0]<0))
            # The bound triggers exact arithmetic, never a fuzzy inside test.
            bound=4*np.finfo(np.float64).eps*(left.abs()+right.abs())
            adaptive=(value.abs()<=bound)&((left!=0)|(right!=0))
            sign=torch.sign(value)
            if bool(adaptive.any()):
                exact,finite=exact_orientation_sign(a[adaptive],b[adaptive],p[adaptive]);sign[adaptive]=exact;numeric_uncertain[adaptive]|=~finite
            inside&=(sign>0)|((sign==0)&top_left)
        col=xy[:,0]*shape[1]+xy[:,1]
        if bool(numeric_uncertain.any()):uncertain.append(col[numeric_uncertain])
        if not bool(inside.any()):continue
        tri=tri[inside];p=p[inside];col=col[inside];area=area[inside]
        normal=torch.linalg.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);z=tri[:,0,2]-(normal[:,:2]*(p-tri[:,0,:2])).sum(1)/normal[:,2]
        finite=torch.isfinite(z)
        if bool((~finite).any()):uncertain.append(col[~finite])
        columns.append(col[finite]);depths.append(z[finite]);deltas.append(-torch.sign(area[finite]).to(torch.int64))
    if not columns or not sum(len(x) for x in columns):
        ambiguous=torch.unique(torch.cat(uncertain)) if uncertain else torch.empty(0,device=device,dtype=torch.int64)
        if not len(ambiguous):return torch.empty((0,3),device=device,dtype=torch.int64),dict(ray_events=0,ambiguous_columns=0)
        # Unresolved arithmetic still preserves possible closed interior on
        # those columns, even when no finite accepted event survived.
        chunks=[];lower=triangles[:,:,2].amin().expand(len(ambiguous));upper=triangles[:,:,2].amax().expand(len(ambiguous))
        for ids,zz in candidate_cell_pairs(lower[:,None],upper[:,None],shape[2:],origin[2:],device,pair_limit):
            center=origin[2]+(zz[:,0].to(torch.float64)+.5)*PITCH;keep=(center>lower[ids])&(center<upper[ids]);ids=ids[keep];zz=zz[keep]
            if len(ids):chunks.append(torch.stack([ambiguous[ids]//shape[1],ambiguous[ids]%shape[1],zz[:,0]],1))
        return torch.unique(torch.cat(chunks),dim=0) if chunks else torch.empty((0,3),device=device,dtype=torch.int64),dict(ray_events=0,ambiguous_columns=len(ambiguous))
    col=torch.cat(columns);z=torch.cat(depths);delta=torch.cat(deltas)
    order=torch.argsort(z,stable=True);order=order[torch.argsort(col[order],stable=True)];col=col[order];z=z[order];delta=delta[order]
    # Combine only exactly equal (column,depth); no near-z merger.
    begin=torch.ones(len(col),device=device,dtype=torch.bool);begin[1:]=(col[1:]!=col[:-1])|(z[1:]!=z[:-1]);group=begin.cumsum(0)-1
    grouped=torch.zeros(int(group[-1])+1,device=device,dtype=torch.int64);grouped.scatter_add_(0,group,delta)
    col=col[begin];z=z[begin];delta=grouped
    new_column=torch.ones(len(col),device=device,dtype=torch.bool);new_column[1:]=col[1:]!=col[:-1];col_group=new_column.cumsum(0)-1;starts=new_column.nonzero().flatten()
    cumulative=delta.cumsum(0);base=cumulative[starts]-delta[starts];winding=cumulative-base[col_group]
    last=torch.ones(len(col),device=device,dtype=torch.bool);last[:-1]=col[:-1]!=col[1:]
    bad=col[last&(winding!=0)]
    if len(bad):uncertain.append(bad)
    interval=torch.zeros(len(col),device=device,dtype=torch.bool);interval[:-1]=(col[:-1]==col[1:])&(winding[:-1]!=0)&(z[1:]>z[:-1])
    if topology['kind']=='closed_ambiguous':
        # Only topologically closed but ambiguously oriented sources use a
        # conservative span between actual ray events, never a mesh AABB.
        interval[:-1]=(col[:-1]==col[1:])&(z[1:]>z[:-1])
    at=interval.nonzero().flatten();interval_col=col[at];lower=z[at];upper=z[at+1]
    ambiguous=torch.unique(torch.cat(uncertain)) if uncertain else torch.empty(0,device=device,dtype=torch.int64)
    if len(ambiguous):
        interval_col=torch.cat([interval_col,ambiguous]);lower=torch.cat([lower,triangles[:,:,2].amin().expand(len(ambiguous))]);upper=torch.cat([upper,triangles[:,:,2].amax().expand(len(ambiguous))])
    chunks=[]
    for ids,zz in candidate_cell_pairs(lower[:,None],upper[:,None],shape[2:],origin[2:],device,pair_limit):
        center=origin[2]+(zz[:,0].to(torch.float64)+.5)*PITCH;keep=(center>lower[ids])&(center<upper[ids]);ids=ids[keep];zz=zz[keep]
        if len(ids):chunks.append(torch.stack([interval_col[ids]//shape[1],interval_col[ids]%shape[1],zz[:,0]],1))
    inside=torch.unique(torch.cat(chunks),dim=0) if chunks else torch.empty((0,3),device=device,dtype=torch.int64)
    return inside,dict(ray_events=sum(len(x) for x in columns),ambiguous_columns=len(ambiguous))

def exact_stair_cell_indices(vertices,faces,shape,origin,device='cuda',pair_limit=131072):
    """All true native triangle/cell surfaces plus genuine closed interiors."""
    topology=mesh_topology(vertices,faces);triangles=torch.as_tensor(np.array(vertices[faces]),device=device,dtype=torch.float64)
    shape=torch.as_tensor(shape,device=device,dtype=torch.int64);origin=torch.as_tensor(origin,device=device,dtype=torch.float64)
    chunks=[];pairs=0;refinements=0;unresolved=0
    for ids,cells in candidate_cell_pairs(triangles.amin(1),triangles.amax(1),shape,origin,device,pair_limit):
        keep,numeric=triangle_cell_hits(triangles[ids],cells,origin,return_diagnostics=True);pairs+=len(ids);refinements+=numeric['sat_exact_projection_comparisons'];unresolved+=numeric['sat_unresolved_retained_pairs']
        if bool(keep.any()):chunks.append(cells[keep])
    surface=torch.unique(torch.cat(chunks),dim=0) if chunks else torch.empty((0,3),device=device,dtype=torch.int64)
    interior,ray=scanline_interior(triangles,shape,origin,topology,device,pair_limit)
    all_cells=torch.unique(torch.cat([surface,interior]),dim=0)
    return all_cells,dict(**topology,triangle_count=len(faces),candidate_triangle_cell_pairs=pairs,surface_cells=len(surface),interior_center_cells=len(interior),occupied_cells=len(all_cells),sat_exact_projection_comparisons=refinements,sat_unresolved_retained_pairs=unresolved,**ray)


def is_exact_native_stair(row):
    """Dispatch by actual source-declared type, never building/path/seed."""
    return bool(row.get('stair')) and row.get('native_proxy_type') in ('exact_mesh','exact_mesh_open_plane')

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
    analytic_boxes={i:bounds for i,row in enumerate(records) if (bounds:=certified_box_world_bounds(row)) is not None}
    exact_stairs={i for i,row in enumerate(records) if is_exact_native_stair(row) and i not in analytic_boxes}
    legacy_stairs={i for i,row in enumerate(records) if row['stair'] and i not in analytic_boxes and i not in exact_stairs}
    raster_faces=sum(row['face_count'] for i,row in enumerate(records) if i not in analytic_boxes and i not in exact_stairs)
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
            if mesh_index in analytic_boxes or mesh_index in exact_stairs:continue
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
                print(json.dumps(dict(meshes_done=mesh_index,meshes_total=len(records),raster_faces_done=consumed,raster_faces_total=raster_faces,source_faces_total=total_faces,analytic_box_count=len(analytic_boxes),exact_stair_mesh_count=len(exact_stairs),legacy_stair_mesh_count=len(legacy_stairs),elapsed_s=time.monotonic()-start)),flush=True);last_print=time.monotonic()
    world=splat_source(surface,low_gpu,shape_tensor,origin)
    stair=splat_source(stairs,low_gpu,shape_tensor,origin)
    nonstair=splat_source(nonstairs,low_gpu,shape_tensor,origin)
    solid_extra={}
    analytic_box_voxels=0
    for i,(lower,upper) in analytic_boxes.items():
        row=records[i];ijk=box_cell_indices(lower,upper,shape_tensor,origin);analytic_box_voxels+=len(ijk)
        world[ijk[:,0],ijk[:,1],ijk[:,2]]=True
        if row['stair']:
            stair[ijk[:,0],ijk[:,1],ijk[:,2]]=True;solid_extra[owner_index[i]]=ijk
        else:nonstair[ijk[:,0],ijk[:,1],ijk[:,2]]=True
    exact_sources=[]
    for i in sorted(exact_stairs):
        row=records[i];vertices=np.array(load_array(row['vertices']));faces=np.array(load_array(row['faces']))
        if row.get('world_transform') is not None:
            matrix=np.asarray(row['world_transform']);vertices=vertices@matrix[:3,:3]+matrix[3,:3]
        ijk,diagnostics=exact_stair_cell_indices(vertices,faces,shape_tensor,origin)
        stair[ijk[:,0],ijk[:,1],ijk[:,2]]=True;solid_extra[owner_index[i]]=ijk
        exact_sources.append(dict(prim_path=row['path'],stair_id=row.get('stair_id') or 'all_stairs',**diagnostics))
    # Rebuild only by source unions. Never subtract a whole old stair mask
    # from world occupancy, which could erase a coincident other collider.
    world=nonstair|stair
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    (out/'EXACT_STAIR_SOURCES.json').write_text(json.dumps(exact_sources,indent=2))
    np.save(out/'occupancy.npy',world.cpu().numpy());np.save(out/'stair_mask.npy',stair.cpu().numpy());np.save(out/'nonstair_occupancy.npy',nonstair.cpu().numpy())
    by_stair={};arrays={};metadata={}
    for owner,chunks in provenance.items():
        if not chunks and owner not in solid_extra:continue
        ijk=splat_cells(chunks,low_gpu,shape_tensor,origin,lattice_shape) if chunks else torch.empty((0,3),device=device,dtype=torch.int64)
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
    return dict(mesh_count=len(records),face_count=total_faces,raster_face_count=raster_faces,analytic_box_count=len(analytic_boxes),analytic_box_source_face_count=sum(records[i]['face_count'] for i in analytic_boxes),exact_stair_mesh_count=len(exact_stairs),legacy_stair_mesh_count=len(legacy_stairs),native_type_policy='actual_authored_factory80_proxyType; undeclared_surface_sources_keep_legacy',exact_stair_source_face_count=sum(records[i]['face_count'] for i in exact_stairs),exact_stair_voxel_contributions=sum(x['occupied_cells'] for x in exact_sources),exact_stair_ambiguous_columns=sum(x['ambiguous_columns'] for x in exact_sources),exact_stair_sat_projection_comparisons=sum(x['sat_exact_projection_comparisons'] for x in exact_sources),exact_stair_sat_unresolved_retained_pairs=sum(x['sat_unresolved_retained_pairs'] for x in exact_sources),analytic_box_voxel_contributions=analytic_box_voxels,analytic_box_semantics='strict_positive_volume_world_cell_intersection_no_surface_splat',occupied_voxels=int(world.sum()),stair_voxels=int(stair.sum()),elapsed_s=time.monotonic()-start)

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
        from voxel_scene import scene_spool,cached_sha
        manifest=json.loads(a.manifest.read_text());spool=scene_spool(manifest,a.cache or a.output.parent/'proxy_geometry_cache')
        low=np.asarray(spool['bounds_min_m']);high=np.asarray(spool['bounds_max_m']);shape=(np.ceil((high-low)/PITCH).astype(np.int64)+1)//2*2
        grid=dict(grid_fine_shape=shape.tolist(),grid_scene_bounds_min_m=low.tolist(),grid_scene_bounds_max_m=high.tolist());scope=dict(root=dict(sha256=cached_sha(a.manifest,a.cache or a.output.parent/'proxy_geometry_cache')))
    else:
        scope=json.loads(a.scope.read_text());spool=json.loads(a.spool.read_text());grid=scope['all_enabled_meshes']
    a.output.mkdir(parents=True,exist_ok=False)
    result=raster_spool(spool,grid['grid_fine_shape'],grid['grid_scene_bounds_min_m'],a.output)
    (a.output/'GRID.json').write_text(json.dumps(grid,indent=2))
    if a.reference:result['comparison']=compare(a.reference,a.output/'occupancy.npy')
    result.update(tool='voxel_gpu',source_sha256=sha(Path(__file__)),geometry_metadata_helper_sha256=sha(Path(__file__).with_name('voxel_scene.py')) if a.manifest else None,input_sha=scope['root']['sha256'],reference_sha=sha(a.reference) if a.reference else None,output_sha=sha(a.output/'occupancy.npy'),exit_code=0 if result.get('comparison',{}).get('status','PASS')=='PASS' else 1,peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,elapsed_s=time.monotonic()-start)
    (a.output/'VOXEL_RESULT.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2),flush=True)
    raise SystemExit(result['exit_code'])
if __name__=='__main__':main()
