"""Factory80 v4 C1-C4 GPU predicates; one 5 cm occupancy shared by all checks.

No renderer, image scoring, graph-packing, wrapper registration, or old gate
controller is imported. CPU random generation reproduces the pinned NumPy
seed; clearance, graph connectivity, pair predicates and rates run on CUDA.
"""
from pathlib import Path
import argparse, hashlib, json, math, os, subprocess, time, resource
import numpy as np
import torch
import torch.nn.functional as F

PITCH=.05
def offsets(radius=.25,intersects=False):
    n=math.ceil(radius/PITCH+.5) if intersects else round(radius/PITCH)
    return [(x,y) for x in range(-n,n+1) for y in range(-n,n+1) if ((max(abs(x)*PITCH-PITCH/2,0)**2+max(abs(y)*PITCH-PITCH/2,0)**2)<=radius**2 if intersects else x*x+y*y<=n*n)]

def morphology(mask,off,*,erode=False,border=False):
    """GPU exact disk OR/AND with explicit closed/outside semantics."""
    result=torch.ones_like(mask) if erode else torch.zeros_like(mask)
    nx,ny=mask.shape[:2]
    for dx,dy in off:
        src=(slice(max(0,dx),nx+min(0,dx)),slice(max(0,dy),ny+min(0,dy)))
        dst=(slice(max(0,-dx),nx+min(0,-dx)),slice(max(0,-dy),ny+min(0,-dy)))
        if erode:result[dst]&=mask[src]
        else:result[dst]|=mask[src]
        if border:
            if dx>0:result[nx-dx:]=True
            elif dx<0:result[:-dx]=True
            if dy>0:result[:,ny-dy:]=True
            elif dy<0:result[:,:-dy]=True
        elif erode:
            if dx>0:result[nx-dx:]=False
            elif dx<0:result[:-dx]=False
            if dy>0:result[:,ny-dy:]=False
            elif dy<0:result[:,:-dy]=False
    return result

def graph_edges(valid,support=None,diagonal=False,max_step=4):
    nx,ny=valid.shape; ids=torch.arange(nx*ny,device=valid.device).reshape(nx,ny)
    a=[];b=[]
    dirs=[(1,0),(0,1)]+([(1,1),(1,-1)] if diagonal else [])
    for dx,dy in dirs:
        xa=slice(0,nx-1) if dx else slice(0,nx);xb=slice(1,nx) if dx else slice(0,nx)
        if dy==1:ya=slice(0,ny-1);yb=slice(1,ny)
        elif dy==-1:ya=slice(1,ny);yb=slice(0,ny-1)
        else:ya=yb=slice(0,ny)
        good=valid[xa,ya]&valid[xb,yb]
        if support is not None:good&=(support[xa,ya]-support[xb,yb]).abs()<=max_step
        if dx and dy:good&=valid[xb,ya]&valid[xa,yb]
        a.append(ids[xa,ya][good]);b.append(ids[xb,yb][good])
    return torch.cat(a),torch.cat(b)

def components(valid,support=None,diagonal=False,max_step=4):
    """Hook roots then pointer-compress; convergence checked, never truncated."""
    a,b=graph_edges(valid,support,diagonal,max_step);parent=torch.arange(valid.numel(),device=valid.device,dtype=torch.long)
    for iteration in range(1024):
        old=parent.clone(); ra=parent[a];rb=parent[b]
        high=torch.maximum(ra,rb);low=torch.minimum(ra,rb)
        parent.scatter_reduce_(0,high,low,reduce='amin',include_self=True)
        for _ in range(16):
            nxt=parent[parent]
            if torch.equal(nxt,parent):break
            parent=nxt
        if torch.equal(old,parent):
            sizes=torch.bincount(parent[valid.flatten()],minlength=valid.numel())
            return parent.reshape(valid.shape),sizes
    raise RuntimeError('GPU connected components did not converge')

def derive_support(occupancy,origin,scene_max):
    nx,ny,nz=occupancy.shape
    axes=[origin[i]+(torch.arange(occupancy.shape[i],device='cuda',dtype=torch.float64)+.5)*PITCH for i in range(3)]
    real=(axes[0][:,None,None]<scene_max[0])&(axes[1][None,:,None]<scene_max[1])&(axes[2][None,None,:]<scene_max[2])
    blocked=(~real)|occupancy
    dilated=morphology(blocked,offsets(intersects=True),border=True)
    count=nz-32
    if count<=0:raise ValueError('Grid too short for pinned 1.6m body')
    prefix=F.pad(dilated.to(torch.int32).cumsum(2,dtype=torch.int32),(1,0))
    clear=occupancy[:,:,:count]&real[:,:,:count]&(~occupancy[:,:,1:count+1])&real[:,:,1:count+1]&((prefix[:,:,33:]-prefix[:,:,1:count+1])==0)
    support=torch.where(clear.any(2),clear.to(torch.uint8).argmax(2).to(torch.int32),-1)
    labels,sizes=components(support>=0,support)
    support[(support>=0)&(sizes[labels]<400)]=-1
    return support

def b1(support,origin_xy,seed=20261014,sample_mask=None):
    passable=morphology(support>=0,offsets(),erode=True)
    labels,sizes=components(passable,support,diagonal=True)
    # C2v2: connectivity remains over ALL eroded body-passable cells.
    eligible=passable if sample_mask is None else passable&sample_mask
    cells=eligible.flatten().nonzero().flatten(); n=len(cells)
    if not n:
        classes={k:dict(pairs=0,reachable=0,reachability_rate=0.,threshold=.95,status='FAIL') for k in ('same_tile','cross_tile')}
        return dict(status='FAIL',reason='No eligible body-passable floor-band cells',classes=classes,body_passable_cells=int(passable.sum()),eligible_sample_cells=0,main_label=int(torch.argmax(sizes)),C2_definition_version=2 if sample_mask is not None else 1),passable,labels
    rng=np.random.default_rng(seed);sources=rng.choice(n,size=min(64,n),replace=False)
    source_list=[];target_list=[]
    for s in sources:
        t=rng.choice(n,size=min(512,n),replace=False);t=t[t!=s]
        source_list.append(np.full(len(t),s,dtype=np.int64));target_list.append(t)
    s=torch.tensor(np.concatenate(source_list),device='cuda');t=torch.tensor(np.concatenate(target_list),device='cuda')
    sf=cells[s];tf=cells[t];ny=support.shape[1]
    def tile(flat):
        x=(flat//ny).double()*PITCH+origin_xy[0];y=(flat%ny).double()*PITCH+origin_xy[1]
        return torch.floor(x/20).long()*10000+torch.floor(y/21).long()
    same=tile(sf)==tile(tf);reached=labels.flatten()[sf]==labels.flatten()[tf];classes={}
    for name,select in [('same_tile',same),('cross_tile',~same)]:
        pairs=int(select.sum());num=int((reached&select).sum());rate=num/max(pairs,1)
        classes[name]=dict(pairs=pairs,reachable=num,reachability_rate=rate,threshold=.95,status='PASS' if pairs and rate>=.95 else 'FAIL')
    valid_labels=labels[passable];main=int(torch.argmax(sizes))
    return dict(status='PASS' if all(x['status']=='PASS' for x in classes.values()) else 'FAIL',classes=classes,standable_cells=int((support>=0).sum()),body_passable_cells=int(passable.sum()),eligible_sample_cells=n,C2_definition_version=2 if sample_mask is not None else 1,main_component_cells=int(sizes[main]),main_label=main),passable,labels

def c1(manifest):
    rows=sum([manifest.get(x,[]) for x in ['instances','slabs','stairs']],[])
    if not rows:return dict(status='FAIL',reason='Missing actual collision manifest')
    exp=torch.tensor([r['expected_colliders'] for r in rows],device='cuda')
    got=torch.tensor([r['enabled_colliders'] for r in rows],device='cuda')
    bad=(got<1)|(got!=exp)
    bad_indices=bad.nonzero().flatten().cpu().tolist()
    return dict(status='FAIL' if bad_indices or len(manifest.get('slabs',[]))!=2 or not manifest.get('stairs') else 'PASS',actual_colliders=int(got.sum()),expected_colliders=int(exp.sum()),errors=[dict(prim_path=rows[i]['prim_path'],actual=rows[i]['enabled_colliders'],expected=rows[i]['expected_colliders']) for i in bad_indices])

def sample_spawn(support,passable,labels,main,origin,seed,count=20):
    flat=(passable&(labels==main)).flatten().nonzero().flatten()
    if len(flat)<count:raise ValueError('Insufficient actual B1 main-component points')
    ids=np.random.default_rng(seed).choice(len(flat),count,replace=False);cells=flat[torch.tensor(ids,device='cuda')];ny=support.shape[1]
    # 1 mm above the actual support top; no arbitrary free-space sampling.
    pts=torch.stack([origin[0]+(cells//ny+.5)*PITCH,origin[1]+(cells%ny+.5)*PITCH,origin[2]+(support.flatten()[cells]+1)*PITCH+.001],1)
    return pts.cpu().tolist()

def polygon_mask(poly,origin,nx,ny):
    p=torch.tensor(poly,device='cuda',dtype=torch.float64)
    x=origin[0]+(torch.arange(nx,device='cuda',dtype=torch.float64)+.5)*PITCH
    y=origin[1]+(torch.arange(ny,device='cuda',dtype=torch.float64)+.5)*PITCH
    inside=torch.zeros((nx,ny),device='cuda',dtype=torch.bool)
    for a,b in zip(p,torch.roll(p,-1,0)):
        crossing=((a[1]>y)!=(b[1]>y))[None,:]
        intersect=(b[0]-a[0])*(y-a[1])/(b[1]-a[1]+1e-30)+a[0]
        inside^=crossing&(x[:,None]<intersect[None,:])
    return inside

def standing_nodes_3d(occupancy):
    """Every exposed support height, circular body above 0.2m foot relief."""
    count=occupancy.shape[2]-32
    if count<=0:raise ValueError('Grid shorter than body')
    dilated=morphology(occupancy,offsets(intersects=True),border=True)
    # Reuse one exact int32 prefix; do not retain two whole-world scans.
    prefix=torch.empty((*occupancy.shape[:2],occupancy.shape[2]+1),device=occupancy.device,dtype=torch.int32)
    prefix[:,:,0]=0
    torch.cumsum(dilated,2,dtype=torch.int32,out=prefix[:,:,1:])
    clear=(prefix[:,:,33:]-prefix[:,:,5:count+5])==0
    del dilated
    # The central column must be clear immediately above the standing top;
    # relief only accommodates neighbouring uphill terrain, never a ceiling.
    torch.cumsum(occupancy,2,dtype=torch.int32,out=prefix[:,:,1:])
    clear&=(prefix[:,:,33:]-prefix[:,:,1:count+1])==0
    return occupancy[:,:,:count]&clear

def components_3d(valid):
    """Sparse active standing nodes; dense int32 lookup avoids a CPU graph."""
    xyz=valid.nonzero();n=len(xyz)
    lookup=torch.full(valid.shape,-1,device='cuda',dtype=torch.int32)
    ids=torch.arange(n,device='cuda',dtype=torch.int64)
    lookup[xyz[:,0],xyz[:,1],xyz[:,2]]=ids.to(torch.int32)
    aa=[];bb=[];shape=torch.tensor(valid.shape,device='cuda')
    for dx,dy in [(1,0),(0,1)]:
        for dz in range(-4,5):
            other=xyz+torch.tensor([dx,dy,dz],device='cuda')
            good=((other>=0)&(other<shape)).all(1);there=other[good]
            targets=lookup[there[:,0],there[:,1],there[:,2]].long();exists=targets>=0
            aa.append(ids[good][exists]);bb.append(targets[exists])
    a=torch.cat(aa);b=torch.cat(bb);parent=ids.clone()
    for iteration in range(1024):
        old=parent.clone();ra=parent[a];rb=parent[b]
        parent.scatter_reduce_(0,torch.maximum(ra,rb),torch.minimum(ra,rb),reduce='amin',include_self=True)
        for _ in range(16):
            nxt=parent[parent]
            if torch.equal(nxt,parent):break
            parent=nxt
        if torch.equal(old,parent):return lookup,parent
    raise RuntimeError('GPU 3D standing graph did not converge')

def c3(nonstair_occupancy,stair_masks,floor_main_masks,origin,stairs):
    """Connect each stair through its full exposed-surface height graph.

    3D standing nodes use 0.2m foot relief then 1.6m circular body clearance.
    A compact bounded stair region is combined with the actual two largest
    B1 floor components. No per-stair adjacency edge is invented.
    """
    # nonstair_occupancy is rasterized independently during the same geometry
    # pass. Boolean world & ~stair would erase overlapping floors/obstacles.
    def connection(occ):
        valid=standing_nodes_3d(occ);lookup,labels=components_3d(valid);roots=[]
        for floor in floor_main_masks:
            xy=floor['main_mask'].nonzero();z=floor['support_global_z'][xy[:,0],xy[:,1]].long()
            inbounds=(z>=0)&(z<valid.shape[2]);xy=xy[inbounds];z=z[inbounds]
            ids=lookup[xy[:,0],xy[:,1],z].long();ids=ids[ids>=0]
            roots.append(torch.unique(labels[ids]))
        if len(roots)!=2:raise ValueError('C3 requires exactly two actual floor main components')
        joined=bool(torch.isin(roots[0],roots[1]).any()) if all(len(x) for x in roots) else False
        return dict(connected=joined,standing_nodes=int(valid.sum()),floor_anchor_components=[len(x) for x in roots])
    removed=connection(nonstair_occupancy);results=[]
    for stair in stairs:
        sid=stair['stair_id']
        if sid not in stair_masks:results.append(dict(stair_id=sid,status='FAIL',reason='Missing actual stair source mask'));continue
        actual=connection(nonstair_occupancy|stair_masks[sid])
        results.append(dict(stair_id=sid,status='PASS' if actual['connected'] else 'FAIL',**actual))
    return dict(status='PASS' if results and all(x['status']=='PASS' for x in results) and not removed['connected'] else 'FAIL',stairs=results,all_stairs_removed=removed)

def reference_run(grid_dir,quick,out):
    """005 qualification reuses this one GPU grid, computes pinned B1 twice."""
    quick=Path(quick);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    scope=json.loads((quick/'ACTUAL_RASTER_SCOPE.json').read_text());g=scope['all_enabled_meshes'];origin=g['grid_scene_bounds_min_m'];maximum=g['grid_scene_bounds_max_m']
    occ=torch.tensor(np.load(Path(grid_dir)/'occupancy.npy'),device='cuda',dtype=torch.bool)
    counts=occ.sum((0,1));centers=origin[2]+(torch.arange(occ.shape[2],device='cuda')+.5)*PITCH
    eligible=(centers>2).nonzero().flatten();peak=int(eligible[torch.argmax(counts[eligible])]);threshold=float(counts[peak])*.35;cut=peak
    while cut>0 and int(counts[cut-1])>threshold:cut-=1
    footprint=occ[:,:,peak];labels,sizes=components(~footprint)
    exterior_ids=torch.unique(torch.cat([labels[0],labels[-1],labels[:,0],labels[:,-1]]))
    filled=footprint|~torch.isin(labels,exterior_ids);terrain=int(round(-origin[2]/PITCH))-1
    tops=origin[2]+(torch.arange(occ.shape[2],device='cuda')+1)*PITCH;terrain=int(torch.argmin(tops.abs()))
    results={};spawns=[]
    for i,(lo,hi) in enumerate([(0,cut+1),(cut,occ.shape[2])]):
        floor=i+1;band=occ[:,:,lo:hi].clone()
        if i==0:band[:,:,terrain]|=filled
        if band.shape[2]%2:band=F.pad(band,(0,1))
        local_origin=list(origin);local_origin[2]+=lo*PITCH
        # The original _crop_grid intentionally uses padded grid maxima for
        # xy. Preserve that detail when qualifying the original CPU example.
        local_max=[origin[j]+occ.shape[j]*PITCH for j in range(3)];local_max[2]=min(maximum[2],origin[2]+hi*PITCH)
        support=derive_support(band,local_origin,local_max)
        report,passable,labels=b1(support,origin[:2]);cpu=np.load(quick/f'Floor{floor}_SUPPORT.npz')['support_z_index']
        report['support_mismatch_cells']=int((support!=torch.tensor(cpu,device='cuda')).sum())
        ref=json.loads((quick/f'Floor{floor}_B1_B3.json').read_text())['baseline']['classes']
        for name,row in report['classes'].items():
            row['reference_rate']=ref[name]['reachability_rate'];row['absolute_error_percentage_points']=abs(row['reachability_rate']-row['reference_rate'])*100
            row['qualification_status']='PASS' if row['absolute_error_percentage_points']<=.5 else 'FAIL'
        np.save(out/f'Floor{floor}_SUPPORT.npy',support.cpu().numpy())
        results[f'Floor{floor}']=report
        spawns.append(dict(floor_index=floor,points_xyz_m=sample_spawn(support,passable,labels,report['main_label'],local_origin,20261031+floor)))
        print('GPU_B1',floor,json.dumps(report),flush=True)
    result=dict(status='PASS' if all(all(x['qualification_status']=='PASS' for x in r['classes'].values()) for r in results.values()) else 'FAIL',floors=results,peak_layer=peak,cut_layer=cut)
    (out/'B1_REFERENCE_RESULT.json').write_text(json.dumps(result,indent=2));(out/'SPAWN_POINTS_REFERENCE_ONLY.json').write_text(json.dumps(dict(seed=20261031,floors=spawns,stair_platforms=[]),indent=2));return result

def world_stairs(stair_spec):
    """Transform generator's actual tread metadata into the assembled frame."""
    result=[]
    for row in stair_spec['stairs']:
        meta_path=row.get('collision_metadata') or row.get('metadata_path') or str(Path(row['asset_stage']).parent/'STAIR_COLLISION.json')
        meta=json.loads(Path(meta_path).read_text());angle=math.radians(row.get('rotation_deg',0));c,s=math.cos(angle),math.sin(angle);xy=np.array([[c,s],[-s,c]]);offset=np.array(row['offset_m']);treads=[]
        for i,tread in enumerate(meta['tread_boxes']):
            poly=np.array(tread['xy_polygon_m'])@xy+offset[:2]
            treads.append(dict(tread_id=f"L{tread['level_index']}_{i}",polygon_xy_m=poly.tolist(),top_z_m=tread['top_z_m']+offset[2],collider_ids=tread['collider_ids']))
        envelope=meta.get('walking_envelope_polygon_xy_m') or meta.get('walking_envelope_xy_m')
        if envelope is None:
            # This is the actual union's bounding rectangle only when the
            # source explicitly supplies no turn polygon. Fail rather than
            # quietly substitute an invented L/U walking envelope.
            if meta['kind']!='straight':raise ValueError('L/U actual walking envelope missing')
            pts=np.vstack([np.array(x['xy_polygon_m']) for x in meta['tread_boxes']]);lo=pts.min(0);hi=pts.max(0);envelope=[[lo[0],lo[1]],[hi[0],lo[1]],[hi[0],hi[1]],[lo[0],hi[1]]]
        envelope=(np.array(envelope)@xy+offset[:2]).tolist()
        platforms=meta.get('platforms',meta.get('landing_platforms',[]))
        result.append(dict(stair_id=row['id'],treads=treads,walking_envelope_polygon_xy_m=envelope,platforms=platforms,offset_m=offset.tolist(),xy_rotation=xy.tolist()))
    return result

def geometry_parts(geometry):
    if geometry.is_empty:return []
    if geometry.geom_type=='Polygon':return [geometry]
    if geometry.geom_type in ('MultiPolygon','GeometryCollection'):
        return [part for g in geometry.geoms for part in geometry_parts(g)]
    return []

def geometry_mask(geometry,origin,nx,ny):
    """All polygon components and holes; exact world-grid centers on CUDA."""
    from shapely.geometry import shape
    geometry=shape(geometry) if isinstance(geometry,dict) else geometry
    result=torch.zeros((nx,ny),device='cuda',dtype=torch.bool)
    for polygon in geometry_parts(geometry):
        mask=polygon_mask(list(polygon.exterior.coords),origin,nx,ny)
        for hole in polygon.interiors:mask&=~polygon_mask(list(hole.coords),origin,nx,ny)
        result|=mask
    return result

def c4_above_top_projection(vertices,faces,top):
    """Closure of actual native triangles at z>top, projected to XY.

    No height cap is imposed on a boundary projection. Degenerate XY
    projections of real vertical faces remain lines, not guessed widths.
    """
    from shapely.geometry import Polygon,LineString
    from shapely.ops import unary_union
    projected=[]
    for triangle in vertices[faces]:
        if float(triangle[:,2].max())<=top:continue
        clipped=[]
        for p,q in zip(triangle,np.roll(triangle,-1,axis=0)):
            if p[2]>=top:clipped.append(p)
            if (p[2]<top<q[2]) or (q[2]<top<p[2]):
                clipped.append(p+(q-p)*((top-p[2])/(q[2]-p[2])))
        if not clipped:continue
        xy=np.asarray(clipped)[:,:2];polygon=Polygon(xy) if len(xy)>=3 else None
        if polygon is not None and polygon.area>0:
            projected.append(polygon)
        else:
            # A projected vertical triangle is a line. Its two farthest
            # actual projected endpoints represent it without any hull.
            delta=xy[:,None,:]-xy[None,:,:];i,j=np.unravel_index(np.sum(delta*delta,axis=2).argmax(),(len(xy),len(xy)))
            if np.any(xy[i]!=xy[j]):projected.append(LineString([xy[i],xy[j]]))
    return unary_union(projected)


def c4_projection_parts(geometry):
    if geometry.is_empty:return []
    if geometry.geom_type in ('Polygon','LineString'):return [geometry]
    if hasattr(geometry,'geoms'):
        return [part for g in geometry.geoms for part in c4_projection_parts(g)]
    return []


def c4_longitudinal_core_bands(projected,a,tangent,normal,length):
    """Exact constant-depth bands supported throughout a local native run.

    A rail's longitudinal trunk defines its side boundary; a local inward
    spur cannot move that boundary to the spur's most-inboard point. The
    core is the intersection of all normal cross-sections, obtained from
    the complement's projected intervals. Every returned depth also has
    a full-length line covered by the actual projection.
    """
    from shapely.geometry import box,LineString
    from shapely.affinity import affine_transform
    local=affine_transform(projected,[*tangent,*normal,-float(a@tangent),-float(a@normal)])
    if local.is_empty:return []
    u0,d0,u1,d1=local.bounds;u0=max(0.,u0);u1=min(length,u1)
    if u1<=u0:return []
    if d1==d0:
        line=LineString([(u0,d0),(u1,d0)])
        return [dict(outer_depth_m=d0,inner_depth_m=d1,along_range_m=[u0,u1])] if local.covers(line) else []
    local=local.intersection(box(u0,d0,u1,d1));result=[]
    for part in c4_projection_parts(local):
        lo,outer,hi,inner=part.bounds
        if hi<=lo:continue
        if inner==outer:
            if part.covers(LineString([(lo,outer),(hi,outer)])):
                result.append(dict(outer_depth_m=outer,inner_depth_m=inner,along_range_m=[lo,hi]))
            continue
        missing=box(lo,outer,hi,inner).difference(part)
        breaks={outer,inner}
        for gap in c4_projection_parts(missing):
            breaks.update([gap.bounds[1],gap.bounds[3]])
        breaks=sorted(breaks);bands=[]
        for near,far in zip(breaks,breaks[1:]):
            if part.covers(LineString([(lo,(near+far)/2),(hi,(near+far)/2)])):
                # The complement has no projected interval inside this
                # band. Retain only endpoints actually covered as well.
                if part.covers(LineString([(lo,near),(hi,near)])) and part.covers(LineString([(lo,far),(hi,far)])):
                    bands.append((near,far))
        for depth in breaks:
            if not any(x<=depth<=y for x,y in bands) and part.covers(LineString([(lo,depth),(hi,depth)])):
                bands.append((depth,depth))
        for near,far in sorted(bands):
            result.append(dict(outer_depth_m=near,inner_depth_m=far,along_range_m=[lo,hi]))
    return result


def c4_native_lateral_boundary(projected_by_id,role_by_component,a,b,part,ring):
    """Existing edge/outer-row policy with actual side/post/rail projections.

    No occupancy or inserted control prim can enter this function. Interior
    rail islands and local inward spurs remain obstacles in unchanged C4.
    """
    from shapely.geometry import LineString,Point
    a=np.asarray(a);b=np.asarray(b);tangent=(b-a)/np.linalg.norm(b-a);length=float(np.linalg.norm(b-a))
    normal=np.array([-tangent[1],tangent[0]])
    if not part.covers(Point((a+b)/2+normal*1e-7)):normal=-normal
    edge=LineString([a,b]);edges=[LineString([u,w]) for u,w in zip(ring,ring[1:])]
    parallel=[]
    for u,w in zip(ring,ring[1:]):
        direction=np.asarray(w)-u
        if abs(float(direction[0]*tangent[1]-direction[1]*tangent[0]))<=1e-12:
            parallel.append(LineString([u,w]))
    side=[];rows={'posts':[],'handrails':[]}
    for cid,projected in projected_by_id.items():
        role=role_by_component[cid]
        if role not in ('sides','posts','handrails') or projected.is_empty:continue
        if projected.intersection(part).is_empty:continue
        for core in c4_longitudinal_core_bands(projected,a,tangent,normal,length):
            near=core['outer_depth_m'];depth=core['inner_depth_m'];u0,u1=core['along_range_m']
            if depth<=0:continue
            center=Point(a+tangent*((u0+u1)/2)+normal*((near+depth)/2))
            row=dict(core,collider_id=cid,role=role)
            if role=='sides' and near<-1e-9 and depth>1e-9 and depth-near<u1-u0:
                side.append(row)
            elif role=='posts' and near>=-1e-6:
                if center.distance(edge)<=min(center.distance(e) for e in edges)+1e-8:rows[role].append(row)
            elif role=='handrails' and depth-near<u1-u0:
                # Associate a longitudinal rail with the nearest parallel
                # side, not a nearby front/riser or another L/U flight.
                if center.distance(edge)<=min(center.distance(e) for e in parallel)+1e-8:rows[role].append(row)
    chosen=list(side)
    for role,candidates in rows.items():
        if candidates:
            first=min(x['outer_depth_m'] for x in candidates)
            chosen.extend(x for x in candidates if abs(x['outer_depth_m']-first)<1e-6)
    if not chosen:return None
    depth=max(x['inner_depth_m'] for x in chosen)
    return dict(edge_xy_m=[a.tolist(),b.tolist()],inward_normal=normal.tolist(),inner_depth_m=depth,
        collider_ids=sorted({x['collider_id'] for x in chosen}),native_projection_sources=chosen,
        geometry='actual above-top native side/post/handrail longitudinal core; outermost row per role',
        projection_lower_z='strictly above physical tread top; closure for boundary only',projection_upper_z=None,
        local_intrusions_change_boundary=False)


def prepare_c4_geometry(stair_spec,manifest,roles_root,cache_dir):
    """Derive level faces/boundaries from actual cached collision vertices.

    Native FACE role identities are checked against whole-source component
    bounds, independently of convex/exact partition indices. Local native
    side/post/handrail longitudinal runs define lateral boundaries. Bounds matching is an input identity check,
    not a clearance allowance. The final inset is exactly PITCH.
    """
    import hashlib
    from shapely.geometry import Polygon,box,mapping,shape
    from shapely.ops import unary_union
    from shapely.affinity import affine_transform
    from voxel_extract import proxy_geometry
    from voxel_raster import load_array
    sources={s['stair_id']:s for s in manifest['collision_sources'] if s.get('stair_id')}
    result=[]
    for row in stair_spec['stairs']:
        sid=row['id'];source=sources[sid]
        meta_path=Path(row.get('collision_metadata') or row.get('metadata_path') or str(Path(row['asset_stage']).parent/'STAIR_COLLISION.json'))
        meta=json.loads(meta_path.read_text());role_path=Path(roles_root)/sid/'NATIVE_FACE_ROLES.json';roles=json.loads(role_path.read_text())
        geo=proxy_geometry(source,cache_dir);cache_rows={r['path'].rsplit('/',1)[-1]:r for r in geo['records']}
        role_by_component={};bindings=[]
        native_bounds=np.array([[*r['bounds_min_m'],*r['bounds_max_m']] for r in roles['components']])
        for component in meta['components']:
            bound=np.array([*component['source_component_bounds_min_m'],*component['source_component_bounds_max_m']])
            distances=np.max(np.abs(native_bounds-bound),axis=1)
            candidate_ids=np.flatnonzero(distances<=5e-6).tolist()
            candidates=[roles['components'][j] for j in candidate_ids]
            native_roles={native['role'] for native in candidates}
            if not candidates or len(native_roles)!=1:
                raise ValueError('Ambiguous native FACE component binding '+sid+':'+component['collider_id'])
            # C4 consumes the semantic role, not an arbitrary native identity.
            # Coincident native surfaces may share bounds; accept only role
            # consensus and disclose every candidate. Mixed roles still fail.
            role=next(iter(native_roles));native_ids=[native['native_component_id'] for native in candidates]
            role_by_component[component['collider_id']]=role
            bindings.append(dict(collider_id=component['collider_id'],
                native_component_id=native_ids[0] if len(native_ids)==1 else None,
                native_component_ids=native_ids,unique_native_identity=len(native_ids)==1,
                role=role,max_bound_difference_m=float(max(distances[j] for j in candidate_ids))))
        vertices={cid:np.array(load_array(r['vertices'])) for cid,r in cache_rows.items()}
        faces={cid:np.array(load_array(r['faces'])) for cid,r in cache_rows.items()}
        levels={}
        for tread in meta['tread_boxes']:levels.setdefault(int(tread['level_index']),[]).append(tread)
        expected=list(range(1,max(levels)+1))
        if sorted(levels)!=expected:raise ValueError('Missing actual native level '+sid)
        angle=math.radians(row.get('rotation_deg',0));c,s=math.cos(angle),math.sin(angle)
        xy=np.array([[c,s],[-s,c]]);offset=np.asarray(row['offset_m']);world_matrix=np.asarray(source['world_transform'])
        if not np.allclose(world_matrix[:2,:2],xy,atol=1e-12) or not np.allclose(world_matrix[3,:3],offset,atol=1e-9):raise ValueError('Stair metadata/manifest transform mismatch '+sid)
        treads=[]
        for level,raw in sorted(levels.items()):
            cids=sorted({cid for t in raw for cid in t['collider_ids'] if role_by_component[cid] in ('steps','treads')})
            if not cids:raise ValueError('No semantic support at native level '+sid+':'+str(level))
            source_top=[float(t['top_z_m']) for t in raw]
            if max(source_top)-min(source_top)>.005:raise ValueError('Same native level exceeds existing 5mm construction tolerance')
            tops={cid:float(vertices[cid][:,2].max()) for cid in cids};top=max(tops.values());polys=[]
            for cid in cids:
                v=vertices[cid];f=faces[cid];tri=v[f]
                horizontal=np.ptp(tri[:,:,2],axis=1)<1e-12
                actual_top=np.max(tri[:,:,2],axis=1)==tops[cid]
                for triangle in tri[horizontal&actual_top]:
                    p=Polygon(triangle[:,:2])
                    if p.area>1e-14:polys.append(p)
            raw_face=unary_union(polys)
            if not raw_face.is_valid or raw_face.is_empty:raise ValueError('Invalid actual top-face union '+sid+':'+str(level))
            # The next actual support footprint is the riser/back boundary;
            # a cap's forward overhang on the lower top is retained.
            next_ids=sorted({cid for t in levels.get(level+1,[]) for cid in t['collider_ids'] if role_by_component[cid]=='steps'})
            next_support=[]
            for cid in next_ids:
                v=vertices[cid];next_support.append(Polygon(v[:,:2]).convex_hull if cache_rows[cid].get('solid_box_corners_m') else unary_union([Polygon(t[:,:2]) for t in v[faces[cid]] if Polygon(t[:,:2]).area>1e-14]))
            face=raw_face.difference(unary_union(next_support)) if next_support else raw_face
            # This is source-native geometry only. Occupancy and the added
            # panel are deliberately not inputs to the boundary operation.
            projection={cid:c4_above_top_projection(v,faces[cid],top) for cid,v in vertices.items()
                if role_by_component[cid] in ('sides','posts','handrails')}
            # An orthogonal actual top polygon supplies its own edge frames.
            # Only lateral edges receive actual above-top side/post/handrail
            # natural boundary planes. Local inward branches do not become
            # a new boundary; their occupancy is queried by unchanged C4.
            cuts=[];boundaries=[]
            from shapely.geometry import LineString
            path=np.asarray(meta['traversal']['points_xyz_m'])
            level_path=np.flatnonzero(np.abs(path[:,2]-min(source_top))<=.005)
            if not len(level_path):raise ValueError('Native traversal has no point for actual level '+sid+':'+str(level))
            first,last=int(level_path[0]),int(level_path[-1])
            before=path[first-1,:2] if first else 2*path[first,:2]-path[last+1,:2]
            if last+1>=len(path):raise ValueError('Actual final riser path point missing')
            transitions=[LineString([before,path[first,:2]]),LineString([path[last,:2],path[last+1,:2]])]
            for part in geometry_parts(face):
                ring=list(part.exterior.coords)
                for a,b in zip(ring,ring[1:]):
                    a=np.asarray(a);b=np.asarray(b);d=b-a;length=np.linalg.norm(d)
                    if length<1e-10:continue
                    tangent=d/length
                    if min(abs(tangent[0]),abs(tangent[1]))>1e-6:raise ValueError('Non-cardinal native walking edge requires exact plane clipping')
                    # Real traversal crosses the front/riser edge. Those
                    # longitudinal boundaries are supplied by the tread
                    # top/next support, never by a side panel end face.
                    if any(LineString([a,b]).intersects(t) for t in transitions):continue
                    boundary=c4_native_lateral_boundary(projection,role_by_component,a,b,part,ring)
                    if boundary is None:continue
                    normal=np.asarray(boundary['inward_normal']);depth=boundary['inner_depth_m']
                    cut=Polygon([a,b,b+normal*depth,a+normal*depth]);cuts.append(cut)
                    boundaries.append(boundary)
            inner=face.difference(unary_union(cuts)) if cuts else face
            walking=inner.buffer(-PITCH,join_style=2)
            world=lambda g:affine_transform(g,[c,-s,s,c,offset[0],offset[1]])
            # Physical collision readback maxima select the representative
            # level plane; no source-top+height tolerance enters clearance.
            actual_world_tops={cid:float((vertices[cid]@world_matrix[:3,:3]+world_matrix[3,:3])[:,2].max()) for cid in cids}
            world_top=max(actual_world_tops.values())
            treads.append(dict(tread_id='L'+str(level),level_index=level,top_z_m=world_top,collider_ids=cids,
                polygon_xy_m=list(world(raw_face).exterior.coords) if raw_face.geom_type=='Polygon' else None,
                walking_geometry=mapping(world(walking)),raw_top_geometry=mapping(world(raw_face)),inner_geometry=mapping(world(inner)),
                raw_row_count=len(raw),source_top_range_m=[min(source_top),max(source_top)],actual_collision_top_range_m=[min(actual_world_tops.values()),world_top],
                alias_eligible_colliders=actual_world_tops,boundary_sources=boundaries,walking_area_m2=float(walking.area),inner_area_m2=float(inner.area),inset_each_boundary_m=PITCH))
        old=next(s for s in world_stairs(stair_spec) if s['stair_id']==sid)
        old.update(treads=treads,collision_source=source,collision_source_sha256=geo['source_sha256'],role_binding=bindings,native_roles_path=str(role_path),native_roles_sha256=hashlib.sha256(role_path.read_bytes()).hexdigest(),collision_metadata_path=str(meta_path),collision_metadata_sha256=hashlib.sha256(meta_path.read_bytes()).hexdigest(),native_level_count=len(levels),raw_tread_row_count=sum(map(len,levels.values())))
        result.append(old)
    return result

def load_c4_geometry(path,manifest,cache_dir):
    from voxel_extract import proxy_geometry
    doc=json.loads(Path(path).read_text())
    sources={s['stair_id']:s for s in manifest['collision_sources'] if s.get('stair_id')}
    for stair in doc['stairs']:
        current=sources[stair['stair_id']];prior=stair['collision_source']
        for key in ('proxy_path','proxy_root_path','world_transform'):
            if current[key]!=prior[key]:raise ValueError('C4 geometry source/transform binding changed: '+stair['stair_id']+':'+key)
        geo=proxy_geometry(current,cache_dir)
        if geo['source_sha256']!=stair['collision_source_sha256']:raise ValueError('C4 collision source SHA changed')
        for file_key,sha_key in [('native_roles_path','native_roles_sha256'),('collision_metadata_path','collision_metadata_sha256')]:
            if hashlib.sha256(Path(stair[file_key]).read_bytes()).hexdigest()!=stair[sha_key]:raise ValueError('C4 semantic input SHA changed')
    return doc['stairs']


def source_composed_path(source,source_prim,manifest):
    """Reference-relative prim identity, checked on the actual composed USD."""
    root=source['proxy_root_path'];suffix=source_prim[len(root):]
    if not source_prim.startswith(root+'/') and source_prim!=root:raise ValueError('Proxy prim outside its explicit root')
    physical=source.get('composed_proxy_root_path') or source.get('_resolved_composed_root_path')
    if physical:return physical+suffix
    # Older manifests did not record the actual reference child. Read only
    # this instance, not the whole visual stage. Match the actual reference
    # asset/root; a prim-name or AABB guess is not an identity proof.
    from pxr import Usd,Sdf
    mask=Usd.StagePopulationMask();mask.Add(Sdf.Path(source['instance_root_path']))
    stage=Usd.Stage.OpenMasked(str(manifest['root']),mask)
    matches=[];target=str(Path(source['proxy_path']).resolve())
    for p in Usd.PrimRange(stage.GetPrimAtPath(source['instance_root_path']),Usd.TraverseInstanceProxies()):
        refs=p.GetMetadata('references')
        if not refs:continue
        for ref in refs.GetAddedOrExplicitItems():
            if str(ref.primPath)==root and ref.assetPath and str((Path(manifest['root']).parent/ref.assetPath).resolve())==target:matches.append(str(p.GetPath()))
    if not matches:
        # Prepared equipment references its visual/physics root rather than
        # the collision-only file directly. Prim stacks prove the exact
        # original collision layer and prim, through those nested arcs.
        exact=[]
        for p in Usd.PrimRange(stage.GetPrimAtPath(source['instance_root_path']),Usd.TraverseInstanceProxies()):
            if any(str(Path(spec.layer.realPath).resolve())==target and str(spec.path)==source_prim for spec in p.GetPrimStack() if spec.layer.realPath):exact.append(str(p.GetPath()))
        if len(exact)==1 and exact[0].endswith(suffix):matches=[exact[0][:-len(suffix)] if suffix else exact[0]]
    if len(matches)!=1:
        # Retained shell/slab roots are composed at their original paths.
        if source['instance_root_path']==root and stage.GetPrimAtPath(source_prim):
            source['_resolved_composed_root_path']=root
            return source_prim
        raise ValueError('Actual composed proxy root absent/ambiguous; use scene_compose manifest: '+source['instance_root_path'])
    source['_resolved_composed_root_path']=matches[0]
    return matches[0]+suffix

def attach_provenance(stairs,grid_dir,shape,manifest=None):
    """Distinct collider ownership, retaining every coincident non-self hit."""
    with np.load(Path(grid_dir)/'stair_collision_provenance.npz',allow_pickle=False) as f:
        metadata=json.loads(bytes(f['metadata_json_utf8']).decode());arrays={key:np.array(f[key]) for key in metadata}
    def linear(a):return (a[:,0]*shape[1]+a[:,1])*shape[2]+a[:,2]
    source_linear={key:np.unique(linear(a)) for key,a in arrays.items()}
    global_linear,global_count=np.unique(np.concatenate(list(source_linear.values())),return_counts=True)
    sources={s['stair_id']:s for s in (manifest or {}).get('collision_sources',[]) if s.get('stair_id')}
    records=[]
    for key,row in metadata.items():
        sid=row['stair_id'];source=sources.get(sid)
        actual=source_composed_path(source,row['prim_path'],manifest) if source else row['prim_path']
        records.append(dict(prim_path=actual,source_prim_path=row['prim_path'],stair_id=sid,ijk=arrays[key],linear=source_linear[key]))
    for stair in stairs:
        stair['_source_records']=records
        for tread in stair['treads']:
            eligible=tread.get('alias_eligible_colliders')
            own_keys=[key for key,row in metadata.items() if row['stair_id']==stair['stair_id'] and row['prim_path'].rsplit('/',1)[-1] in (eligible or tread['collider_ids'])]
            if not own_keys:raise ValueError('Actual tread collision provenance missing '+stair['stair_id']+':'+tread['tread_id'])
            allowed=[]
            for key in own_keys:
                a=arrays[key]
                if eligible is not None:
                    cid=metadata[key]['prim_path'].rsplit('/',1)[-1];maximum=eligible[cid]
                    if maximum>tread['top_z_m']:continue
                    # Bound follows voxel_raster's exact round-even surface
                    # lattice/splat and authored solid-box intersection.
                    oz=tread['_grid_origin_z'];epsilon=PITCH*1e-9
                    alias_max=math.floor((np.rint(maximum/PITCH)*PITCH+PITCH/2-oz-epsilon)/PITCH)
                    solid_max=math.ceil((maximum-oz-epsilon)/PITCH)-1
                    a=a[a[:,2]<=max(alias_max,solid_max)]
                allowed.append(a)
            own=np.unique(np.concatenate(allowed),axis=0) if allowed else np.empty((0,3),dtype=np.int64)
            own_linear,own_count=np.unique(np.concatenate([source_linear[key] for key in own_keys]),return_counts=True)
            at=np.searchsorted(global_linear,own_linear)
            other=own_linear[global_count[at]>own_count]
            tread['self_occupancy_indices']=own
            tread['other_source_indices_at_self']=other

def c4_source_attribution(stairs,manifest,cache_dir,origin,shape,out):
    """Raster only non-stair collider candidates once with the SAME v3 math.

    AABBs only prefilter. Reported ownership is actual per-collider raster
    membership, counted on CUDA. The baseline world occupancy is untouched.
    """
    from shapely.geometry import shape as geom_shape
    from voxel_extract import proxy_geometry
    from voxel_raster import load_array,raster_spool
    regions=[]
    for stair in stairs:
        for tread in stair['treads']:
            g=geom_shape(tread['walking_geometry'])
            if not g.is_empty:
                b=g.bounds;regions.append((np.array([b[0],b[1],tread['top_z_m']]),np.array([b[2],b[3],tread['top_z_m']+1.6])))
    def intersects(lo,hi):return any(bool(np.all(hi>=a-PITCH)&np.all(lo<=b+PITCH)) for a,b in regions)
    records=[];all_lo=np.full(3,np.inf);all_hi=-all_lo;mapping=[]
    for source in manifest['collision_sources']:
        if source.get('stair_id') or source.get('category')=='stair':continue
        geo=proxy_geometry(source,cache_dir);matrix=np.asarray(source['world_transform'])
        lo=np.array(geo['bounds_min_m']);hi=np.array(geo['bounds_max_m']);corners=np.array([[x,y,z] for x in [lo[0],hi[0]] for y in [lo[1],hi[1]] for z in [lo[2],hi[2]]])@matrix[:3,:3]+matrix[3,:3]
        if not intersects(corners.min(0),corners.max(0)):continue
        for row in geo['records']:
            v=np.array(load_array(row['vertices']));v=v@matrix[:3,:3]+matrix[3,:3];lo=v.min(0);hi=v.max(0)
            if not intersects(lo,hi):continue
            path=source_composed_path(source,row['path'],manifest)
            records.append(dict(row,path=path,world_transform=matrix.tolist(),stair=True,stair_id='C4_ATTRIBUTION'))
            all_lo=np.minimum(all_lo,lo);all_hi=np.maximum(all_hi,hi)
            mapping.append(dict(prim_path=path,source_prim_path=row['path'],source_proxy=source['proxy_path']))
    target=Path(out)/'c4_source_attribution';target.mkdir(exist_ok=False)
    if records:
        raster=raster_spool(dict(records=records,bounds_min_m=all_lo.tolist(),bounds_max_m=all_hi.tolist()),shape,origin,target)
        with np.load(target/'stair_collision_provenance.npz',allow_pickle=False) as f:
            metadata=json.loads(bytes(f['metadata_json_utf8']).decode())
            added=[dict(prim_path=row['prim_path'],source_prim_path=next(m['source_prim_path'] for m in mapping if m['prim_path']==row['prim_path']),stair_id=None,ijk=np.array(f[key])) for key,row in metadata.items()]
        for r in added:r['linear']=np.unique((r['ijk'][:,0]*shape[1]+r['ijk'][:,1])*shape[2]+r['ijk'][:,2])
        for stair in stairs:stair['_source_records'].extend(added) if stair is stairs[0] else None
    else:raster=dict(mesh_count=0,occupied_voxels=0)
    (target/'SOURCE_BINDINGS.json').write_text(json.dumps(dict(raster=raster,sources=mapping),indent=2))
    return dict(candidate_colliders=len(records),raster=raster,source_bindings=str(target/'SOURCE_BINDINGS.json'))

def c4(occupancy,origin,stairs,opening_polygons,nonstair_occupancy=None):
    """Unique native levels; all obstacles in (physical top,top+1.60]."""
    nx,ny,nz=occupancy.shape;results=[]
    opening=torch.zeros((nx,ny),device='cuda',dtype=torch.bool)
    for poly in opening_polygons:opening|=polygon_mask(poly,origin,nx,ny)
    z_centers=origin[2]+(torch.arange(nz,device='cuda',dtype=torch.float64)+.5)*PITCH
    for stair in stairs:
        steps=[]
        for tread in stair['treads']:
            mask=geometry_mask(tread['walking_geometry'],origin,nx,ny);cells=mask.nonzero();top=float(tread['top_z_m'])
            zs=((z_centers>top)&(z_centers<=top+1.6)).nonzero().flatten()
            head_complete=origin[2]<=top and origin[2]+nz*PITCH>=top+1.6
            base=dict(tread_id=tread['tread_id'],level_index=tread['level_index'],top_z_m=top,raw_row_count=tread['raw_row_count'],walking_cells=len(cells),walking_area_m2=tread['walking_area_m2'],inset_each_boundary_m=PITCH,head_interval_m=[top,top+1.6],head_interval='open_bottom_closed_top_grid_centers',head_indices=zs.cpu().tolist(),actual_collision_top_range_m=tread['actual_collision_top_range_m'])
            if not len(cells) or not len(zs) or not head_complete:
                steps.append(dict(**base,status='FAIL',reason='Empty inset walking face or incomplete clearance grid',top_blocking_prims=[]));continue
            column=occupancy[cells[:,0,None],cells[:,1,None],zs[None,:]].clone()
            all_flat=(cells[:,0,None]*ny+cells[:,1,None])*nz+zs[None,:]
            own=torch.as_tensor(tread['self_occupancy_indices'],device='cuda',dtype=torch.long)
            own_flat=(own[:,0]*ny+own[:,1])*nz+own[:,2]
            remove=torch.isin(all_flat,own_flat)
            other=tread['other_source_indices_at_self']
            if len(other):remove&=~torch.isin(all_flat,torch.as_tensor(other,device='cuda'))
            if nonstair_occupancy is not None:remove&=~nonstair_occupancy[cells[:,0,None],cells[:,1,None],zs[None,:]]
            column[remove]=False
            clear=~column.any(1);fraction=float(clear.to(torch.float64).mean());blockers=[]
            if not bool(clear.all()):
                queried=all_flat[column]
                for source in stair.get('_source_records',[]):
                    ijk=source['ijk']
                    if not len(ijk) or ijk[:,0].max()<int(cells[:,0].min()) or ijk[:,0].min()>int(cells[:,0].max()) or ijk[:,1].max()<int(cells[:,1].min()) or ijk[:,1].min()>int(cells[:,1].max()) or ijk[:,2].max()<int(zs.min()) or ijk[:,2].min()>int(zs.max()):continue
                    ids=torch.as_tensor(source['linear'],device='cuda',dtype=torch.long)
                    hits=ids[torch.isin(ids,queried)]
                    if not len(hits):continue
                    # A same-level support's aliased cells were removed only
                    # if no other source occupies them. It does not become
                    # a blocker attribution merely through that overlap.
                    cid=source['source_prim_path'].rsplit('/',1)[-1]
                    if source.get('stair_id')==stair['stair_id'] and cid in tread['alias_eligible_colliders']:
                        hits=hits[~torch.isin(hits,own_flat)]
                    count=len(torch.unique(hits//nz))
                    if count:blockers.append(dict(prim_path=source['prim_path'],source_prim_path=source['source_prim_path'],blocked_walking_cells=count))
                blockers.sort(key=lambda b:(-b['blocked_walking_cells'],b['prim_path']))
            steps.append(dict(**base,clear_cells=int(clear.sum()),total_cells=len(cells),clear_fraction=fraction,blocked_cells=int((~clear).sum()),foot_alias_removed_voxels=int(remove.sum()),status='PASS' if fraction>=.9 else 'FAIL',top_blocking_prims=blockers[:3],all_blocking_prims=blockers))
        envelope=polygon_mask(stair['walking_envelope_polygon_xy_m'],origin,nx,ny)
        overlap=int((envelope&opening).sum())/max(int(envelope.sum()),1)
        results.append(dict(stair_id=stair['stair_id'],native_level_count=len(steps),raw_tread_row_count=stair.get('raw_tread_row_count'),treads=steps,opening_coverage=overlap,status='PASS' if steps and all(x['status']=='PASS' for x in steps) and overlap>=.9 else 'FAIL'))
    return dict(status='PASS' if results and all(x['status']=='PASS' for x in results) else 'FAIL',threshold_clear_fraction=.9,head_height_m=1.6,inset_each_boundary_m=PITCH,stairs=results)


def validate_blocker_paths(c4_result,scene):
    from pxr import Usd,UsdPhysics,Sdf
    paths=sorted({b['prim_path'] for s in c4_result['stairs'] for t in s['treads'] for b in t.get('all_blocking_prims',[])})
    if not paths:return
    mask=Usd.StagePopulationMask()
    for path in paths:mask.Add(Sdf.Path(path))
    stage=Usd.Stage.OpenMasked(str(scene),mask)
    for path in paths:
        p=stage.GetPrimAtPath(path)
        if not p or not p.HasAPI(UsdPhysics.CollisionAPI) or UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is False:
            raise ValueError('Blocker does not resolve to an actual enabled composed prim: '+path)

def c4_panel_control(scene,baseline,occupancy,nonstairs,origin,stairs,opening_polygons,out):
    """Physical +1.20m panel, evaluated even when baseline C4 already fails."""
    from pxr import Usd,UsdGeom,UsdPhysics,Gf
    from shapely.geometry import shape
    out=Path(out)/'c4_panel_negative_control';out.mkdir(exist_ok=False)
    candidates=[(s,t) for s in baseline['stairs'] for t in s['treads'] if t['status']=='PASS']
    if not candidates:candidates=[(s,t) for s in baseline['stairs'] for t in s['treads'] if t.get('total_cells',0)>0]
    if not candidates:raise ValueError('C4 panel control has no nonempty queried walking level')
    selected,prior=candidates[0];stair=next(s for s in stairs if s['stair_id']==selected['stair_id']);tread=next(t for t in stair['treads'] if t['tread_id']==prior['tread_id'])
    b=shape(tread['walking_geometry']).bounds;z=tread['top_z_m']+1.2;lo=np.array([b[0],b[1],z]);hi=np.array([b[2],b[3],z+.1])
    layer=out/'C4_low_headroom_panel.usda';stage=Usd.Stage.CreateNew(str(layer));UsdGeom.SetStageMetersPerUnit(stage,1.);UsdGeom.SetStageUpAxis(stage,'Z');world=UsdGeom.Xform.Define(stage,'/World');stage.SetDefaultPrim(world.GetPrim())
    panel_path='/World/C4_low_headroom_panel';prim=UsdGeom.Cube.Define(stage,panel_path);prim.CreateSizeAttr(2.)
    xf=UsdGeom.Xformable(prim);xf.AddTranslateOp().Set(Gf.Vec3d(*((lo+hi)/2)));xf.AddScaleOp().Set(Gf.Vec3f(*((hi-lo)/2)));UsdPhysics.CollisionAPI.Apply(prim.GetPrim()).CreateCollisionEnabledAttr(True);stage.GetRootLayer().Save()
    stage=Usd.Stage.Open(str(layer));p=stage.GetPrimAtPath(panel_path);bbox=UsdGeom.BBoxCache(Usd.TimeCode.Default(),[UsdGeom.Tokens.default_]).ComputeWorldBound(p).ComputeAlignedRange();lo=np.array(bbox.GetMin());hi=np.array(bbox.GetMax())
    # Exact authored cube intersection, read back from physical USD.
    a=torch.floor((torch.tensor(lo,device='cuda')-torch.tensor(origin,device='cuda'))/PITCH).long().clamp_min(0);b=torch.ceil((torch.tensor(hi,device='cuda')-torch.tensor(origin,device='cuda'))/PITCH).long()-1;b=torch.minimum(b,torch.tensor(occupancy.shape,device='cuda')-1)
    panel=torch.zeros_like(occupancy);panel[int(a[0]):int(b[0])+1,int(a[1]):int(b[1])+1,int(a[2]):int(b[2])+1]=True
    ijk=panel.nonzero().cpu().numpy();linear=np.unique((ijk[:,0]*occupancy.shape[1]+ijk[:,1])*occupancy.shape[2]+ijk[:,2]);record=dict(prim_path=panel_path,source_prim_path=panel_path,stair_id=None,ijk=ijk,linear=linear)
    stairs[0]['_source_records'].append(record)
    controlled=out/'controlled_scene.usda';stage=Usd.Stage.CreateNew(str(controlled));stage.GetRootLayer().subLayerPaths=[str(Path(scene).resolve()),str(layer.resolve())];stage.GetRootLayer().Save()
    actual=c4(occupancy|panel,origin,stairs,opening_polygons,nonstairs|panel)
    tested=next(t for s in actual['stairs'] if s['stair_id']==stair['stair_id'] for t in s['treads'] if t['tread_id']==tread['tread_id'])
    attributed=next((p for p in tested.get('all_blocking_prims',[]) if p['prim_path']==panel_path),None)
    receipt=dict(control='C4_low_headroom_panel',expected='FAIL',baseline_status=baseline['status'],target_baseline=prior,target_actual=tested,actual=actual,physical_scene=str(controlled),control_geometry=str(layer),actual_panel_prim=panel_path,bounds_min_m=lo.tolist(),bounds_max_m=hi.tolist(),real_panel_detected=bool(attributed and attributed['blocked_walking_cells']>0),panel_blocked_walking_cells=attributed['blocked_walking_cells'] if attributed else 0)
    validate_blocker_paths(actual,controlled)
    escaped=tested['status']!='FAIL' or not receipt['real_panel_detected']
    receipt['status']='NEGATIVE_ESCAPE' if escaped else 'PASS_EXPECTED_TARGET_FAILURE_WITH_REAL_PANEL_DETECTION'
    (out/'C4_PANEL_CONTROL.json').write_text(json.dumps(receipt,indent=2))
    stairs[0]['_source_records'].pop()
    if escaped:
        (out/'NEGATIVE_ESCAPE.json').write_text(json.dumps(receipt,indent=2));raise SystemExit(80)
    return receipt

def c4_only_run(grid_dir,manifest_path,geometry_path,openings_path,out,scene,cache_dir,panel_control=False):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);grid_dir=Path(grid_dir)
    grid=json.loads((grid_dir/'GRID.json').read_text());origin=grid['grid_scene_bounds_min_m'];manifest=json.loads(Path(manifest_path).read_text());stairs=load_c4_geometry(geometry_path,manifest,cache_dir)
    for stair in stairs:
        for tread in stair['treads']:tread['_grid_origin_z']=origin[2]
    occ=torch.tensor(np.load(grid_dir/'occupancy.npy'),device='cuda',dtype=torch.bool);nonstairs=torch.tensor(np.load(grid_dir/'nonstair_occupancy.npy'),device='cuda',dtype=torch.bool)
    attach_provenance(stairs,grid_dir,occ.shape,manifest)
    attribution=c4_source_attribution(stairs,manifest,cache_dir,origin,tuple(occ.shape),out)
    doc=json.loads(Path(openings_path).read_text());opening=[p.get('polygon_xy_m') if isinstance(p,dict) else p for p in doc['opening_polygons_xy_m']]
    actual=c4(occ,origin,stairs,opening,nonstairs);validate_blocker_paths(actual,scene or manifest['root'])
    result=dict(status=actual['status'],checks=dict(C4=actual),scope='C4_ONLY_DIAGNOSTIC_C1_C3_C5_NOT_EXECUTED',source_attribution=attribution)
    (out/'CHECKS.json').write_text(json.dumps(result['checks'],indent=2))
    if panel_control:result['C4_panel_negative_control']=c4_panel_control(scene or manifest['root'],actual,occ,nonstairs,origin,stairs,opening,out)
    return result


def negative_controls(scene,manifest,checks,occupancy,nonstairs,stair_masks,floor_main,origin,stairs,opening_polygons,out):
    """Actual USD mutations and the same GPU predicates as the baseline."""
    from pxr import Usd,UsdGeom,UsdPhysics,Gf
    out=Path(out)/'negative_controls';out.mkdir(exist_ok=False)
    stage=Usd.Stage.Open(str(Path(scene).resolve()))
    if not stage:raise ValueError('Actual pilot root missing for controls')
    stage.SetEditTarget(stage.GetSessionLayer());results=[]
    def enabled(p):return p.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False
    def actual_inventory():
        doc={k:[dict(r) for r in manifest[k]] for k in ['instances','slabs','stairs']}
        for rows in doc.values():
            for row in rows:
                root=stage.GetPrimAtPath(row['prim_path']);row['enabled_colliders']=sum(enabled(p) for p in Usd.PrimRange(root,Usd.TraverseInstanceProxies())) if root else 0
        return doc
    if c1(actual_inventory())['status']!='PASS':raise ValueError('Actual C1 baseline failed before controls')
    targets=[('C1_upper_slab_collision_disabled',next(r for r in manifest['slabs'] if 'Floor2' in r['prim_path'])),('C1_equipment_collision_disabled',next(r for r in manifest['instances'] if r.get('asset_class')!='STRUCTURE'))]
    for name,row in targets:
        root=stage.GetPrimAtPath(row['prim_path']);count=0
        for p in list(Usd.PrimRange(root,Usd.TraverseInstanceProxies())):
            if enabled(p):
                if p.IsInstanceProxy():raise ValueError('Control target is instance proxy; author local override before scientific control')
                UsdPhysics.CollisionAPI(p).CreateCollisionEnabledAttr(False);count+=1
        if not count:raise ValueError('C1 control disabled no actual collider')
        (out/(name+'.session.usda')).write_text(stage.GetSessionLayer().ExportToString())
        actual=c1(actual_inventory());results.append(dict(control=name,expected='FAIL',actual=actual,disabled_colliders=count))
        stage.GetSessionLayer().Clear()
        if actual['status']!='FAIL':
            (out/'NEGATIVE_ESCAPE.json').write_text(json.dumps(results,indent=2));raise SystemExit(80)
    if c1(actual_inventory())['status']!='PASS':raise ValueError('C1 baseline not restored')
    def box(name,lo,hi):
        path=out/(name+'.usda');st=Usd.Stage.CreateNew(str(path));UsdGeom.SetStageMetersPerUnit(st,1.);UsdGeom.SetStageUpAxis(st,'Z')
        world=UsdGeom.Xform.Define(st,'/World');st.SetDefaultPrim(world.GetPrim());prim=UsdGeom.Cube.Define(st,'/World/'+name);prim.CreateSizeAttr(2.)
        xf=UsdGeom.Xformable(prim);xf.AddTranslateOp().Set(Gf.Vec3d(*((np.array(lo)+hi)/2)));xf.AddScaleOp().Set(Gf.Vec3f(*((np.array(hi)-lo)/2)))
        UsdPhysics.CollisionAPI.Apply(prim.GetPrim()).CreateCollisionEnabledAttr(True);st.GetRootLayer().Save()
        # Re-read actual authored bounds, including float32 scale storage;
        # the GPU mask is derived from that physical USD mutation.
        st=Usd.Stage.Open(str(path));pr=st.GetPrimAtPath('/World/'+name);bbox=UsdGeom.BBoxCache(Usd.TimeCode.Default(),[UsdGeom.Tokens.default_]).ComputeWorldBound(pr).ComputeAlignedRange()
        lo=np.array(bbox.GetMin());hi=np.array(bbox.GetMax());a=torch.floor((torch.tensor(lo,device='cuda')-torch.tensor(origin,device='cuda'))/PITCH).long().clamp_min(0)
        b=torch.ceil((torch.tensor(hi,device='cuda')-torch.tensor(origin,device='cuda'))/PITCH).long()-1;b=torch.minimum(b,torch.tensor(occupancy.shape,device='cuda')-1)
        mask=torch.zeros_like(occupancy);mask[int(a[0]):int(b[0])+1,int(a[1]):int(b[1])+1,int(a[2]):int(b[2])+1]=True
        # The full physical controlled scene is also retained, immutable.
        scene_path=out/(name+'_scene.usda');controlled=Usd.Stage.CreateNew(str(scene_path));controlled.GetRootLayer().subLayerPaths=[str(Path(scene).resolve()),str(path.resolve())];controlled.GetRootLayer().Save()
        return mask,dict(physical_scene=str(scene_path),control_geometry=str(path),bounds_min_m=lo.tolist(),bounds_max_m=hi.tolist())
    stair=stairs[0];points=np.array(stair['walking_envelope_polygon_xy_m']);lo=np.r_[points.min(0)-.3,-.3];hi=np.r_[points.max(0)+.3,6.]
    blocking,physical=box('C3_stair_block',lo,hi);actual=c3(nonstairs|blocking,stair_masks,floor_main,origin,stairs)
    row=dict(control='C3_stair_block',stair_id=stair['stair_id'],expected='FAIL',actual=actual,**physical);results.append(row)
    blocked=next((r for r in actual['stairs'] if r['stair_id']==stair['stair_id']),{})
    if actual['status']!='FAIL' or blocked.get('status')!='FAIL':
        (out/'NEGATIVE_ESCAPE.json').write_text(json.dumps(results,indent=2));raise SystemExit(80)
    results.append(c4_panel_control(scene,checks['C4'],occupancy,nonstairs,origin,stairs,opening_polygons,out))
    receipt=dict(status='PASS_EXPECTED_SEMANTIC_FAILURES',controls=results,C5='NOT_EXECUTED_ROOT_OWNS_NATIVE_QUERY')
    (out/'NEGATIVE_CONTROLS.json').write_text(json.dumps(receipt,indent=2));return receipt

def authored_convex_interior(vertices,faces):
    """Certify one selected original closed convex proxy, without changing it."""
    v=np.asarray(vertices,dtype=np.float64);f=np.asarray(faces,dtype=np.int64)
    center=v.mean(0)
    if np.linalg.matrix_rank(v-center,tol=1e-10)<3:return None
    edges=np.sort(np.vstack([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]),axis=1)
    _,counts=np.unique(edges,axis=0,return_counts=True)
    if not len(counts) or np.any(counts!=2):return None
    tri=v[f];normals=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);length=np.linalg.norm(normals,axis=1);good=length>1e-12
    if not good.any():return None
    normals=normals[good]/length[good,None];anchors=tri[good,0]
    sign=np.where(np.einsum('ij,ij->i',normals,anchors-center)<0,-1.,1.);normals*=sign[:,None]
    planes=np.einsum('ij,ij->i',normals,anchors);tol=max(float(np.ptp(v,axis=0).max()),1.)*1e-7
    # The dense V-by-F product can exceed 100GB for an unchanged real asset.
    # Identical half-space comparisons are bounded to an 8MiB float64 block.
    for first_vertex in range(0,len(v),4096):
        block=v[first_vertex:first_vertex+4096]
        for first_plane in range(0,len(normals),256):
            distance=block@normals[first_plane:first_plane+256].T
            distance-=planes[first_plane:first_plane+256]
            if np.any(distance>tol):return None
    margin=planes-normals@center
    if np.min(margin)<=tol:return None
    return float(np.min(margin))

def negative_machine_point(manifest,spawn_floors,cache_dir):
    """Place the capsule at positive floor height with a convex interior witness.

    A convex collider's full-dimensional vertex centroid lies inside that
    actual convex shape. With witness XY on the capsule axis and witness Z
    2 cm inside either end, a detected machine cannot be explained by lowering
    the capsule through the floor. No asset-union AABB center is used.
    """
    from voxel_extract import proxy_geometry
    floor1=next((f for f in spawn_floors if f['floor_index']==1),None)
    if floor1 is None or not floor1['points_xyz_m']:raise ValueError('Missing actual Floor1 B1 positive spawn for negative control')
    positive=list(map(float,floor1['points_xyz_m'][0]));feet_z=positive[2]
    if not math.isfinite(feet_z):raise ValueError('Nonfinite actual positive floor feet height')
    for instance in manifest['instances']:
        if instance.get('asset_class')!='MACHINE':continue
        source=next((s for s in manifest['collision_sources'] if s['instance_root_path']==instance['prim_path']),None)
        if source is None:continue
        geometry=proxy_geometry(source,cache_dir)
        for collider in geometry['records']:
            approximation=collider.get('approximation')
            if approximation not in ['convexHull','box','none']:continue
            r=collider['vertices'];v=np.memmap(r['path'],mode='r',dtype=r['dtype'],offset=r['byte_offset'],shape=tuple(r['shape']))
            local_center=np.asarray(v).mean(0)
            if np.linalg.matrix_rank(np.asarray(v)-local_center,tol=1e-10)<3:continue
            margin=None
            if approximation=='none':
                fr=collider['faces'];faces=np.memmap(fr['path'],mode='r',dtype=fr['dtype'],offset=fr['byte_offset'],shape=tuple(fr['shape']))
                margin=authored_convex_interior(v,faces)
                if margin is None:continue
            witness=(np.r_[local_center,1.]@np.asarray(source['world_transform'],dtype=np.float64))[:3]
            if not np.isfinite(witness).all() or not feet_z+.02<=witness[2]<=feet_z+1.58:continue
            feet=[float(witness[0]),float(witness[1]),feet_z]
            return dict(machine_prim=instance['prim_path'],collider_source_prim=collider['path'],point_xyz_m=feet,interior_witness_xyz_m=witness.tolist(),positive_floor_feet_z=feet_z,positive_floor_anchor_xyz_m=positive,witness_height_above_feet_m=float(witness[2]-feet_z),source_collision_approximation=approximation,source_convex_witness_local_face_margin_m=margin,witness_geometry='closed_authored_convex_mesh' if approximation=='none' else 'native_convex_collision',selection='Actual full-dimensional convex collider vertex centroid on capsule axis; feet height equals actual Floor1 B1 positive spawn')
    raise ValueError('No actual convex MACHINE centroid lies inside the positive-floor-height capsule; negative control NOT EXECUTED')

def scene_run(grid_dir,manifest_path,stairs_path,openings_path,out,run_controls=False,scene=None,cache_dir=None,geometry_path=None,panel_control=False):
    grid_dir=Path(grid_dir);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    grid=json.loads((grid_dir/'GRID.json').read_text());origin=grid['grid_scene_bounds_min_m'];maximum=grid['grid_scene_bounds_max_m'];manifest=json.loads(Path(manifest_path).read_text())
    occ=torch.tensor(np.load(grid_dir/'occupancy.npy'),device='cuda',dtype=torch.bool)
    nonstairs=torch.tensor(np.load(grid_dir/'nonstair_occupancy.npy'),device='cuda',dtype=torch.bool)
    masks={sid:torch.tensor(np.load(grid_dir/name),device='cuda',dtype=torch.bool) for sid,name in json.loads((grid_dir/'STAIR_MASKS.json').read_text()).items()}
    if geometry_path is None:raise ValueError('v9 requires CPU-prepared --c4-geometry from actual native FACE roles')
    stairs=load_c4_geometry(geometry_path,manifest,cache_dir)
    for stair in stairs:
        for tread in stair['treads']:tread['_grid_origin_z']=origin[2]
    attach_provenance(stairs,grid_dir,occ.shape,manifest)
    c4_attribution=c4_source_attribution(stairs,manifest,cache_dir,origin,tuple(occ.shape),out)
    opening_doc=json.loads(Path(openings_path).read_text());opening_polygons=[p.get('polygon_xy_m') if isinstance(p,dict) else p for p in opening_doc['opening_polygons_xy_m']]
    # Exact 4.0 m upper level / authored slab bottom, rather than a highest-
    # tread heuristic. Grid sharing does not inject any new physical terrain.
    slab_bottom=opening_doc['upper_slab_bottom_z_m'];cut=max(0,math.floor((slab_bottom-origin[2])/PITCH))
    floor_results={};floor_main=[];spawn_floors=[]
    for i,(lo,hi) in enumerate([(0,cut+1),(cut,occ.shape[2])]):
        band=occ[:,:,lo:hi].clone()
        if band.shape[2]%2:band=F.pad(band,(0,1))
        local_origin=list(origin);local_origin[2]+=lo*PITCH;local_max=list(maximum);local_max[2]=min(maximum[2],origin[2]+hi*PITCH)
        support=derive_support(band,local_origin,local_max)
        authored_top_z_m=(0.,4.)[i]  # User C2v2 contract; scene_compose authors Floor2Slab top at4m.
        support_top_z_m=origin[2]+(lo+support.to(torch.float64)+1)*PITCH
        sample_mask=(support>=0)&((support_top_z_m-authored_top_z_m).abs()<=.20)
        report,passable,labels=b1(support,origin[:2],sample_mask=sample_mask)
        report['sampling_floor_band']=dict(authored_floor_top_z_m=authored_top_z_m,tolerance_m=.20,origin_z_m=origin[2],lo=lo,z_formula='origin_z+(lo+support+1)*0.05',connectivity_scope='ALL_BODY_PASSABLE_UNCHANGED')
        floor_results[f'Floor{i+1}']=report
        floor_main.append(dict(main_mask=passable&(labels==report['main_label']),support_global_z=torch.where(support>=0,support+lo,-1)))
        spawn_floors.append(dict(floor_index=i+1,points_xyz_m=sample_spawn(support,passable,labels,report['main_label'],local_origin,20261032+i)))
        np.save(out/f'Floor{i+1}_SUPPORT.npy',support.cpu().numpy())
    checks=dict(C1=c1(manifest),C2=dict(status='PASS' if all(r['status']=='PASS' for r in floor_results.values()) else 'FAIL',floors=floor_results))
    checks['C3']=c3(nonstairs,masks,floor_main,origin,stairs)
    checks['C4']=c4(occ,origin,stairs,opening_polygons,nonstairs)
    validate_blocker_paths(checks['C4'],scene or manifest['root'])
    # Preserve the measured baseline before optional C5 point preparation.
    # A later preparation/resource error must not erase C1-C4 evidence.
    (out/'CHECKS.json').write_text(json.dumps(checks,indent=2))
    # Platform point generation waits for measured platform metadata. Never
    # promote a plan camera or a free voxel to a measured landing.
    platform_points=[]
    for stair in stairs:
        xy=np.array(stair['xy_rotation']);offset=np.array(stair['offset_m'])
        platforms=stair['platforms']
        preferred=[p for p in platforms if p.get('provider')=='native_turning_platform']
        if not preferred:preferred=[p for p in platforms if p.get('provider')=='integrated_upper_floor']
        if not preferred:raise ValueError('No measured stair platform '+stair['stair_id'])
        platform=preferred[0]
        center=np.array(platform['center_xyz_m']);worldxy=center[:2]@xy+offset[:2]
        platform_points.append(dict(stair_id=stair['stair_id'],point_xyz_m=[*worldxy.tolist(),float(center[2]+offset[2]+.001)],provider=platform['provider']))
    if len({p['stair_id'] for p in platform_points})!=len(stairs):raise ValueError('Duplicate stair platform identities')
    (out/'SPAWN_POINTS.json').write_text(json.dumps(dict(seed=20261031,floors=spawn_floors,stair_platforms=platform_points),indent=2))
    negative=negative_machine_point(manifest,spawn_floors,Path(cache_dir) if cache_dir else grid_dir.parent/'proxy_geometry_cache')
    (out/'NEGATIVE_MACHINE_POINT.json').write_text(json.dumps(negative,indent=2))
    result=dict(status='PASS' if all(c['status']=='PASS' for c in checks.values()) else 'FAIL',checks=checks,source_attribution=c4_attribution)
    if panel_control:result['C4_panel_negative_control']=c4_panel_control(scene or manifest['root'],checks['C4'],occ,nonstairs,origin,stairs,opening_polygons,out)
    if run_controls and result['status']=='PASS':result['negative_controls']=negative_controls(scene or manifest.get('root'),manifest,checks,occ,nonstairs,masks,floor_main,origin,stairs,opening_polygons,out)
    return result

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--grid',type=Path);ap.add_argument('--reference-quick',type=Path);ap.add_argument('--manifest',type=Path);ap.add_argument('--stairs',type=Path);ap.add_argument('--openings',type=Path);ap.add_argument('--scene',type=Path);ap.add_argument('--negative-controls',action='store_true');ap.add_argument('--cache',type=Path);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--prepare-c4-geometry',action='store_true');ap.add_argument('--roles-root',type=Path);ap.add_argument('--c4-geometry',type=Path);ap.add_argument('--c4-only',action='store_true');ap.add_argument('--c4-panel-control',action='store_true')
    ap.add_argument('--user-contract',type=Path,default=Path(__file__).parent/'C4_V11_USER_CONTRACT_20261031.md')
    a=ap.parse_args();Path('/proc/self/oom_score_adj').write_text('1000');start=time.monotonic()
    contract_hash=hashlib.sha256(a.user_contract.read_bytes()).hexdigest()
    if a.c4_geometry and json.loads(a.c4_geometry.read_text())['user_contract_sha256']!=contract_hash:raise ValueError('C4 geometry user-contract SHA mismatch')
    if a.prepare_c4_geometry:
        if not all([a.stairs,a.manifest,a.roles_root,a.cache]):ap.error('CPU preparation requires --stairs --manifest --roles-root --cache')
        a.output.mkdir(parents=True,exist_ok=False)
        stairs=prepare_c4_geometry(json.loads(a.stairs.read_text()),json.loads(a.manifest.read_text()),a.roles_root,a.cache)
        result=dict(status='CPU_GEOMETRY_PREPARED_C4_NOT_EXECUTED',tool='checks_gpu_v13_C2v2',user_contract_sha256=contract_hash,stairs=stairs,elapsed_s=time.monotonic()-start)
        (a.output/'C4_GEOMETRY.json').write_text(json.dumps(result,indent=2));print(json.dumps(dict(status=result['status'],levels={s['stair_id']:s['native_level_count'] for s in stairs})),flush=True);return
    if not a.grid:ap.error('GPU predicates require --grid')
    torch.set_num_threads(8);subprocess.run(['nvidia-smi'],check=True)
    if a.reference_quick:result=reference_run(a.grid,a.reference_quick,a.output)
    elif a.c4_only:result=c4_only_run(a.grid,a.manifest,a.c4_geometry,a.openings,a.output,a.scene,a.cache,a.c4_panel_control)
    else:result=scene_run(a.grid,a.manifest,a.stairs,a.openings,a.output,a.negative_controls,a.scene,a.cache,a.c4_geometry,a.c4_panel_control)
    result.update(tool='checks_gpu_v13_C2v2',checker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),user_contract_sha256=contract_hash,c4_geometry_sha256=hashlib.sha256(a.c4_geometry.read_bytes()).hexdigest() if a.c4_geometry else None,peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,elapsed_s=time.monotonic()-start)
    (a.output/'CHECK_RESULT.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2),flush=True)
    raise SystemExit(0 if result['status']=='PASS' else 1)
if __name__=='__main__':main()
