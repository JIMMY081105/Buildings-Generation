"""OWN candidate packing only; no Standability/G25/navigation predicate."""
from __future__ import annotations
import math
from collections import Counter
from shapely.geometry import box, shape


POLICY = {
    'version': 5, 'area_per_piece_min_m2': 10.0,
    'area_per_piece_max_m2': 15.0, 'preferred_area_per_piece_m2': 12.5,
    'prefer_actual_Z_up_height_at_least_m': 3.0,
    'column_bay_aisle_width_m': 2.0, 'equipment_gap_m': 0.30,
    'production_cell_half_column_bay_spans': [2, 2],
    'same_building_max_instances_per_canonical_asset': 8,
    'rotation_choices_deg': [0, 90], 'scale': 1.0,
    'floorstanding_is_descriptive_not_an_admission_rule': True,
    'density_is_candidate_design_not_a_frozen_gate': True,
}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def bounds(record):
    lo, hi = record['bbox_min'], record['bbox_max']
    require(len(lo) == len(hi) == 3 and all(
        math.isfinite(v) for v in (*lo, *hi)), 'Finite XYZ bounds required')
    require(all(hi[i] > lo[i] for i in range(3)), 'Nonempty object bounds required')
    return [hi[i] - lo[i] for i in range(3)]


def catalog_objects(catalog, screen):
    """Reuse exactly original industrial/operational/warehouse ID evidence."""
    require(catalog['screen_sha256'], 'Catalog original screen pin required')
    screens = {r['scene'].removesuffix('_ceiling'): r
               for r in screen['ranked_scenes']}
    result = []
    for scene in catalog['scenes']:
        evidence = {e['asset_id']: e
                    for group in ('industrial', 'operational', 'warehouse')
                    for e in screens[scene['scene']]['evidence'].get(group, [])}
        for obj in scene['objects']:
            if obj['asset_id'] not in evidence:
                continue
            record = dict(obj, canonical_asset_id=obj['asset_id'],
                          source_scene_id=scene['scene'],
                          source_ref=scene['source'],
                          original_screen_evidence=evidence[obj['asset_id']],
                          dimensions_evidence='HISTORICAL_CATALOG_AABB_NOT_COLLIDER_READBACK')
            bounds(record)
            result.append(record)
    return result


def clashes(lo, hi, obstacles):
    polygon = box(lo[0], lo[1], hi[0], hi[1])
    return [o['reason'] for o in obstacles
            if hi[2] > o['z_min'] and lo[2] < o['z_max']
            and polygon.intersects(shape(o['polygon']))]


def row_plan(floors, objects, pitch, grid_n, obstacles, policy=POLICY):
    """Rows in original tile modules (two half-column bays); aisles stay clear."""
    require(len(floors) == 2 and [f['floor_id'] for f in floors] == [0, 1],
            'Only the selected two-floor scope')
    require(type(grid_n) is int and 3 <= grid_n <= 7, 'Original grid3..7 required')
    require(len(pitch) == 2 and min(pitch) > 0, 'Original positive tile pitch')
    polys = [shape(f['polygon']) for f in floors]
    require(all(p.geom_type == 'Polygon' and p.is_valid and p.area > 0 for p in polys),
            'Measured single authored slab polygons required')
    gap = policy['equipment_gap_m']; inset = policy['column_bay_aisle_width_m']/2
    targets = [math.ceil(p.area/policy['preferred_area_per_piece_m2']) for p in polys]
    minima = [math.ceil(p.area/policy['area_per_piece_max_m2']) for p in polys]
    maxima = [math.floor(p.area/policy['area_per_piece_min_m2']) for p in polys]
    bins = []
    for f in floors:
        for by in range(grid_n):
            for bx in range(grid_n):
                x0=bx*pitch[0]+inset; x1=(bx+1)*pitch[0]-inset
                y0=by*pitch[1]+inset; y1=(by+1)*pitch[1]-inset
                bins.append(dict(floor_id=f['floor_id'], bay=[bx,by],
                                 rect=[x0,y0,x1,y1], shelves=[]))
    # Prioritize real Z-up height >=3m, never a wide but short piece. Source Z is descriptive, not
    # new classification tests. All original-screen industrial IDs remain eligible.
    ranked = sorted(objects, key=lambda o: (
        bounds(o)[2] < policy['prefer_actual_Z_up_height_at_least_m'],
        -bounds(o)[2], -max(bounds(o)[:2]), o['source_scene_id'], o['name']))
    require(policy['same_building_max_instances_per_canonical_asset']==8 and policy['scale']==1.0,
            'User authorized max8 instances and scale1 only')
    unique={}
    for obj in ranked:unique.setdefault(obj['canonical_asset_id'],obj)
    # Adjacent candidates share a model. No source scale or asset identity changes.
    ranked=[obj for obj in unique.values() for repeat in range(8)]
    used=Counter(); placed=[]; skipped=[]; counts=[0,0]; occupied=[]
    for obj in ranked:
        asset=obj['canonical_asset_id']
        if used[asset]>=8:raise ValueError('Repeat budget exhausted')
        size = bounds(obj)
        found = None
        floor_order = sorted(range(2), key=lambda i: (-((targets[i]-counts[i])/targets[i]), i))
        for fi in floor_order:
            if counts[fi] >= min(targets[fi], maxima[fi]):
                continue
            for b in (v for v in bins if v['floor_id'] == fi):
                x0,y0,x1,y1=b['rect']
                for rotation in policy['rotation_choices_deg']:
                    width,depth=(size[0],size[1]) if rotation==0 else (size[1],size[0])
                    shelves=[*b['shelves'],dict(y=max([s['y']+s['depth']+gap for s in b['shelves']] or [y0]),depth=depth,cursor=x0,new=True)]
                    for shelf in shelves:
                        if depth > shelf['depth'] or shelf['y']+depth > y1:
                            continue
                        xx=shelf['cursor']
                        # Advancing over rejected positions remains within the
                        # registered shelf. No connectivity/component answer is used.
                        while xx+width <= x1:
                            lo=[xx,shelf['y'],floors[fi]['z_m']]
                            hi=[xx+width,shelf['y']+depth,lo[2]+size[2]]
                            if polys[fi].covers(box(lo[0],lo[1],hi[0],hi[1])) and not clashes(lo,hi,[*obstacles,*occupied]):
                                found=(fi,b,shelf,rotation,lo,hi,width,depth);break
                            xx += max(gap, width/2)
                        if found:break
                    if found:break
                if found:break
            if found:break
        if not found:
            skipped.append(dict(asset_id=asset,source_scene_id=obj['source_scene_id'],
                                reason='NO_RESERVED_BAY_SLOT_WITH_CURRENT_SOURCE_BOUND_DIMENSIONS'))
            continue
        fi,b,shelf,rotation,lo,hi,width,depth=found
        if shelf.get('new'):
            shelf.pop('new');b['shelves'].append(shelf)
        shelf['cursor']=hi[0]+gap
        record=dict(obj,floor_id=fi,rotation_deg=rotation,
                    world_bbox_min=lo,world_bbox_max=hi,column_bay=b['bay'],scale=1.0,repetition_index=used[asset])
        placed.append(record);used[asset]+=1;counts[fi]+=1
        occupied.append(dict(reason='PLACED_EQUIPMENT_'+asset,
                             polygon=box(lo[0]-gap/2,lo[1]-gap/2,hi[0]+gap/2,hi[1]+gap/2).__geo_interface__,
                             z_min=lo[2],z_max=hi[2]))
    measures=[dict(floor_id=i,authored_slab_area_m2=polys[i].area,
                   target_count=targets[i],minimum_count=minima[i],maximum_count=maxima[i],
                   actual_planned_count=counts[i],shortfall=max(0,minima[i]-counts[i]),
                   area_per_actual_piece_m2=polys[i].area/counts[i] if counts[i] else None,
                   actual_Z_up_height_ge3m_count=sum(bounds(p)[2]>=3 for p in placed if p['floor_id']==i))
              for i in range(2)]
    counts_by_asset=verify_instance_policy(placed,policy)
    return dict(schema='Factory80OwnedN2DensityRowPlanV1',
                status='CANDIDATE_PLAN_ONLY_NOT_PHYSICAL_VALIDATION',policy=policy,
                placements=placed,skipped=skipped,floor_density=measures,
                actual_independent_canonical_asset_count=len(counts_by_asset),
                actual_instance_total=len(placed),
                actual_max_instances_per_canonical_asset=max(counts_by_asset.values(),default=0),
                actual_additional_repeated_instances=len(placed)-len(counts_by_asset),
                subset_unique_actual_ids=len({o['canonical_asset_id'] for o in objects}),
                historical_full_library_text_wordmatch_deduplicated_total=2279,
                wordmatch_is_not_metre_scale_industrial_geometry_proof=True,
                subset_shortfall_does_not_measure_full_library_sufficiency=True,
                actual_main_aisle_width_m=policy['column_bay_aisle_width_m'],
                verification_state='SCRATCH_V1_G25_NAV_STAGE_D_VISUAL_NOT_EXECUTED')


def verify_instance_policy(placements,policy=POLICY):
    require(policy['same_building_max_instances_per_canonical_asset']==8 and policy['scale']==1.0,
            'Only user authorized max8/scale1 policy')
    counts=Counter(p['canonical_asset_id']for p in placements)
    require(max(counts.values(),default=0)<=8,'More than8 instances of one canonical asset')
    require(all(p['scale']==1.0 for p in placements),'Every placement scale must be1')
    require(all(type(p['repetition_index'])is int and 0<=p['repetition_index']<8 for p in placements),'Repeat index must be0..7')
    require(len({(p['canonical_asset_id'],p['repetition_index'])for p in placements})==len(placements),'Duplicate canonical/repetition identity')
    return counts
