"""Deterministic geometric proposals from complete, real-asset recipe units.

No admission, class, density or physics verdict is inferred here. All dimensions
are the measured union of collision and visual bounds; artistic scale is one.
"""
import collections,hashlib,math
import numpy as np
from shapely.affinity import rotate,translate
from shapely.geometry import box,Point,shape,mapping
from shapely.ops import unary_union

def ranking(seed,*parts):return hashlib.sha256('|'.join(map(str,(seed,*parts))).encode()).digest()

def turned_bounds(bounds,yaw):
    lo,hi=np.asarray(bounds,dtype=float);angle=math.radians(yaw);c,s=math.cos(angle),math.sin(angle)
    points=np.asarray([[x,y,z] for x in [lo[0],hi[0]] for y in [lo[1],hi[1]] for z in [lo[2],hi[2]]])
    points[:,:2]=points[:,:2]@np.asarray([[c,s],[-s,c]])
    return [points.min(0).tolist(),points.max(0).tolist()]

def actual_bounds(asset):
    bounds=asset['actual_collision_and_visual_bounds_m'];lo,hi=np.asarray(bounds,dtype=float)
    if lo.shape!=(3,) or hi.shape!=(3,) or not np.isfinite([lo,hi]).all() or not np.all(hi>lo):raise ValueError('ACTUAL_ASSET_ENVELOPE_INVALID')
    return bounds

def make_units(zones,count_plan,pools,seed):
    """Split exact aggregate allocations without changing any counts or classes."""
    assert count_plan['status'] in ['OPTIMAL_INTEGER_RECIPE_COUNTS_ONLY','DIAGNOSTIC_INTEGER_FUNCTIONAL_UNIT_COUNTS_RAW_REQUIREMENTS_FAIL']
    if count_plan['status'].startswith('DIAGNOSTIC_'):assert count_plan['raw_requirement_status']=='FAIL' and count_plan['scope']['acceptance']=='FAIL_ORIGINAL_STRICT_REQUIREMENTS_NOT_RELAXED'
    assets={a['key']:a for a in pools};remaining=collections.defaultdict(collections.Counter)
    for row in count_plan['allocations']:remaining[(row['zone'],row['role'])][row['pool']]+=row['count']
    units=[];extra=count_plan.get('extra_robot_arms',0)
    for zone in zones:
        n=count_plan['unit_multiplicities'][zone['name']]
        for index in range(n):
            ingredients=collections.defaultdict(list);counts=dict(zone['roles'])
            if zone['name']=='double_height_welding':
                addition=min(extra,2);counts['robot_arm']+=addition;extra-=addition
            for role,amount in counts.items():
                choices=remaining[(zone['name'],role)]
                for j in range(amount):
                    candidates=[key for key,count in choices.items() if count>0]
                    assert candidates,'RECIPE_ALLOCATION_UNDERRUN'
                    aid=min(candidates,key=lambda key:ranking(seed,zone['name'],role,index,j,key));choices[aid]-=1
                    ingredients[role].append(assets[aid])
            units.append(dict(id=zone['name']+'_%03d'%(index+1),zone=zone['name'],floor=zone['floor'],domain=zone['domain'],ingredients=dict(ingredients)))
    assert extra==0 and all(not any(v.values()) for v in remaining.values()),'RECIPE_ALLOCATION_REMAINDER'
    return units

def blueprint(unit):
    """Make coherent role rows inside one rigidly placed whole recipe unit."""
    roles=unit['ingredients'];members=[]
    def orientation(asset,major_x=True):
        lo,hi=np.asarray(actual_bounds(asset));w,h=(hi-lo)[:2]
        return 0 if (w>=h)==major_x else 90
    def dims(asset,yaw):
        lo,hi=np.asarray(turned_bounds(actual_bounds(asset),yaw));return hi-lo
    def add(asset,role,cx,cy,yaw=None,base_z=0):
        if yaw is None:yaw=orientation(asset)
        bounds=turned_bounds(actual_bounds(asset),yaw);lo,hi=np.asarray(bounds);center=(lo+hi)/2
        pose=[float(cx-center[0]),float(cy-center[1]),float(base_z-lo[2])]
        bounds=[(lo+pose).tolist(),(hi+pose).tolist()]
        row=dict(asset_id=asset['key'],asset_class=asset['class'],role=role,source_root=asset['root'],artistic_scale=1.0,yaw_deg=yaw,translation_m=pose,local_union_bounds_m=bounds)
        members.append(row);return row
    def row(items,role,y=0,gap=.35,x0=0):
        x=x0;depth=0
        for asset in items:
            yaw=orientation(asset);size=dims(asset,yaw);add(asset,role,x+size[0]/2,y+size[1]/2,yaw);x+=size[0]+gap;depth=max(depth,size[1])
        return x-gap if items else x,depth
    def grouped_rows(order,columns=4):
        y=0
        for role in order:
            items=roles.get(role,[])
            for start in range(0,len(items),columns):
                _,depth=row(items[start:start+columns],role,y);y+=depth+.75
    zone=unit['zone']
    if zone in ['double_height_warehouse','mezz_storage']:
        warehouse=zone=='double_height_warehouse';rackrole='high_rack_group' if warehouse else 'light_rack_group';loadrole='wrapped_pallet_load' if warehouse else 'pallet'
        racks=roles[rackrole];loads=roles[loadrole];assert len(racks)==len(loads) and len(racks)%4==0
        spans=[]
        for col in range(4):
            spans.append(max(max(dims(racks[j],orientation(racks[j]))[0],dims(loads[j],orientation(loads[j]))[0]) for j in range(col,len(racks),4)))
        y=0;total_x=sum(spans)+.25*3
        for start in range(0,len(racks),4):
            rd=max(dims(a,orientation(a))[1] for a in racks[start:start+4]);ld=max(dims(a,orientation(a))[1] for a in loads[start:start+4]);x=0
            for col,(rack,load) in enumerate(zip(racks[start:start+4],loads[start:start+4])):
                add(rack,rackrole,x+spans[col]/2,y+rd/2);add(load,loadrole,x+spans[col]/2,y+rd+.25+ld/2);x+=spans[col]+.25
            y+=rd+.25+ld+2.0
        if warehouse:
            # Keep both real vehicles beside the rack-row ends. Placing them
            # after all three rows needlessly extends the complete unit across
            # the fixed cross aisle. Source poses/counts/classes stay unchanged.
            vehicle_y=0
            for role in ['forklift_or_stacker','AMR_trolley']:
                for asset in roles[role]:
                    yaw=orientation(asset);size=dims(asset,yaw)
                    add(asset,role,total_x+.75+size[0]/2,vehicle_y+size[1]/2,yaw)
                    vehicle_y+=size[1]+.75
        else:
            # Bins remain separate real assets on the actual upper slab.
            for role in ['bin','storage_cabinet']:
                for start in range(0,len(roles[role]),4):
                    _,depth=row(roles[role][start:start+4],role,y);y+=depth+.75
    elif zone=='double_height_welding':
        fixtures=roles['body_fixture'];assert len(fixtures)==2
        line=roles['straight_conveyor_line'];assert len(line)==1
        arms=roles['robot_arm'];assert 4<=len(arms)<=6
        guards=roles['machine_guard_segment'];assert len(guards)==8
        arm_groups=[arms[:(len(arms)+1)//2],arms[(len(arms)+1)//2:]];cells=[]
        for i,(fixture,cell_arms) in enumerate(zip(fixtures,arm_groups)):
            assert 2<=len(cell_arms)<=3
            fd=dims(fixture,orientation(fixture));aw=sum(dims(a,orientation(a))[0] for a in cell_arms)+.35*(len(cell_arms)-1);ad=max(dims(a,orientation(a))[1] for a in cell_arms)
            gs=guards[i*4:i*4+4];ys=[orientation(gs[0]),orientation(gs[1]),orientation(gs[2],False),orientation(gs[3],False)];gd=[dims(a,y) for a,y in zip(gs,ys)]
            innerw=max(fd[0],aw);innerd=fd[1]+.6+ad
            width=max(innerw,gd[0][0],gd[1][0])+2*(.5+max(gd[2][0],gd[3][0]));depth=max(innerd,gd[2][1],gd[3][1])+2*(.5+max(gd[0][1],gd[1][1]))
            cells.append(dict(fixture=fixture,arms=cell_arms,guards=gs,guard_yaws=ys,guard_dims=gd,width=width,depth=depth,fixture_dims=fd,arm_width=aw,arm_depth=ad,inner_depth=innerd))
        width=max(sum(c['width'] for c in cells)+.75,dims(line[0],orientation(line[0]))[0]);line_depth=dims(line[0],orientation(line[0]))[1]
        add(line[0],'straight_conveyor_line',width/2,line_depth/2);x0=0;y0=line_depth+.75
        for index,cell in enumerate(cells,1):
            begin=len(members);w,d=cell['width'],cell['depth'];fd=cell['fixture_dims'];gd=cell['guard_dims'];inner_y=y0+(d-cell['inner_depth'])/2
            add(cell['fixture'],'body_fixture',x0+w/2,inner_y+fd[1]/2)
            row(cell['arms'],'robot_arm',inner_y+fd[1]+.6,x0=x0+(w-cell['arm_width'])/2)
            centers=[(x0+w/2,y0+gd[0][1]/2),(x0+w/2,y0+d-gd[1][1]/2),(x0+gd[2][0]/2,y0+d/2),(x0+w-gd[3][0]/2,y0+d/2)]
            # Four actual unscaled segments flank each cell. Deliberate gaps
            # between short segments preserve a service entrance; no ring is
            # claimed. The final B1/B3 uses the authored collision geometry.
            for asset,yaw,(cx,cy) in zip(cell['guards'],cell['guard_yaws'],centers):add(asset,'machine_guard_segment',cx,cy,yaw)
            for member in members[begin:]:member['welding_cell_id']=unit['id']+'_cell_%02d'%index
            x0+=w+.75
        row(roles['electrical_cabinet'],'electrical_cabinet',y0+max(c['depth'] for c in cells)+.75)
    elif zone=='double_height_power':
        assert len(roles['non_flat_top_tank'])==4
        _,tank_depth=row(roles['non_flat_top_tank'],'non_flat_top_tank',0,gap=.75)
        _,ibc_depth=row(roles['IBC'],'IBC',tank_depth+.9)
        gy=tank_depth+.9+ibc_depth+.9
        gen_width,gen_depth=row(roles['generator_set'],'generator_set',gy,gap=.75)
        row(roles['fire_suppression_cylinder'],'fire_suppression_cylinder',gy,x0=gen_width+.75)
        # Existing column-side pipe is handled by its separately verified actual
        # column attachment, never floated into this free equipment cluster.
        if roles.get('column_side_pipe_gallery'):
            assert len(roles['column_side_pipe_gallery'])==1
    else:
        orders={
            'under_mezz_sort_pack':['straight_conveyor','curved_conveyor','pallet_load','tool_cabinet','IBC'],
            'under_mezz_maintenance':[('conditional_flat_top_parts_washer_or_spray_booth' if 'conditional_flat_top_parts_washer_or_spray_booth' in roles else 'parts_washer_or_spray_booth'),'welding_station','air_compressor'],
            'under_mezz_pumps':['air_compressor','electrical_cabinet','tool_cabinet','IBC'],
            'mezz_control':['control_or_electrical_cabinet','workbench','storage_cabinet'],
        }
        grouped_rows(orders[zone])
    expected=collections.Counter({role:len(a) for role,a in roles.items() if role!='column_side_pipe_gallery'})
    assert collections.Counter(x['role'] for x in members)==expected,'WHOLE_RECIPE_BLUEPRINT_COUNT_CHANGED'
    for i,a in enumerate(members):
        al,ah=np.asarray(a['local_union_bounds_m'])
        for b in members[i+1:]:
            bl,bh=np.asarray(b['local_union_bounds_m'])
            if np.all(np.minimum(ah,bh)-np.maximum(al,bl)>2e-5):raise ValueError('INTERNAL_RECIPE_CONSERVATIVE_ENVELOPES_OVERLAP: '+a['role']+' / '+b['role'])
    pts=np.asarray([p for r in members for p in r['local_union_bounds_m']]);lo=pts.min(0);hi=pts.max(0)
    return dict(**{k:v for k,v in unit.items() if k!='ingredients'},members=members,bounds_m=[lo.tolist(),hi.tolist()],external_column_pipe_assets=[a['key'] for a in roles.get('column_side_pipe_gallery',[])])

def place_units(blueprints,domain_document,seed):
    """Conservative whole-unit placement; authoritative final gates run later."""
    output=[];occupied=collections.defaultdict(list);failures=[]
    static_bounds=np.asarray([x['bounds_xyz_m'] for x in domain_document['static_collider_world_aabbs']],dtype=float)
    for unit in sorted(blueprints,key=lambda x:(-np.prod(np.subtract(x['bounds_m'][1],x['bounds_m'][0])[:2]),ranking(seed,x['id']))):
        region=shape(domain_document['regions'][unit['domain']]['geometry']);floor=unit['floor'];floorz=domain_document['floors'][floor]['top_z_m']
        obstacles=[box(*a['bounds']) for a in domain_document['reserved_aisles'] if a['floor']==floor]
        obstacles += [Point(*a['center_xy']).buffer(a['radius_m'],quad_segs=32) for a in domain_document['stair_entry_exit_keepouts'] if a['floor']==floor]
        obstacles += [shape(a['geometry']) for a in domain_document.get('proposal_camera_view_corridors',[]) if a['floor']==floor]
        obstacles += [shape(a['geometry']) for a in domain_document.get('proposal_preplaced_unit_footprints',[]) if a['floor']==floor]
        free=region.difference(unary_union(obstacles))
        if free.is_empty:
            failures.append(dict(unit_id=unit['id'],status='NO_FREE_DOMAIN_AFTER_KEEP_OUTS',candidate_count=0));continue
        minx,miny,maxx,maxy=free.bounds
        candidates=[]
        for yaw in [0,90,180,270]:
            rb=turned_bounds(unit['bounds_m'],yaw);lo,hi=np.asarray(rb);sx,sy=(hi-lo)[:2]
            for x in np.arange(minx+.25,maxx-sx-.25+1e-9,.5):
                for y in np.arange(miny+.25,maxy-sy-.25+1e-9,.5):
                    footprint=box(x,y,x+sx,y+sy)
                    if not free.covers(footprint) or any(footprint.intersects(o) for o in occupied[floor]):continue
                    candidates.append((ranking(seed,unit['id'],yaw,round(x,4),round(y,4)),yaw,np.asarray([x-lo[0],y-lo[1],floorz]),footprint))
        candidates.sort(key=lambda x:x[0]);chosen=None
        for _,yaw,shift,footprint in candidates:
            members=[];blocked=False
            for item in unit['members']:
                rb=turned_bounds(item['local_union_bounds_m'],yaw);lo,hi=np.asarray(rb)+shift
                if len(static_bounds) and np.any(np.all(np.minimum(hi,static_bounds[:,1])-np.maximum(lo,static_bounds[:,0])>2e-5,axis=1)):blocked=True
                if blocked:break
                angle=math.radians(yaw);c,s=math.cos(angle),math.sin(angle);t=np.asarray(item['translation_m']);t[:2]=t[:2]@np.asarray([[c,s],[-s,c]]);t+=shift
                members.append(dict(item,translation_m=t.tolist(),yaw_deg=(item['yaw_deg']+yaw)%360,world_union_bounds_m=[lo.tolist(),hi.tolist()]))
            if not blocked:chosen=dict(unit,members=members,unit_yaw_deg=yaw,unit_translation_m=shift.tolist(),world_footprint=mapping(footprint));occupied[floor].append(footprint.buffer(.25));break
        if chosen:output.append(chosen)
        else:failures.append(dict(unit_id=unit['id'],status='NO_CONSERVATIVE_WHOLE_UNIT_PLACEMENT',candidate_count=len(candidates)))
    return dict(status='ALL_WHOLE_RECIPE_UNITS_PROPOSED' if not failures else 'PARTIAL_WHOLE_UNIT_PACKING_FAILED',units=output,failures=failures,unplaced_count=len(failures),scientific_collision_and_B1_B3_verdict='NOT_EXECUTED',physical_source_geometry_modified=False)
