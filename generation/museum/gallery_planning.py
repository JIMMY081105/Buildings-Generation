"""Choose wide gallery views from actual placed exhibits; projections are plans."""
from pathlib import Path
import json,math,hashlib,numpy as np
from shapely import wkt
from shapely.geometry import Point

def gallery_views(candidate,model,views,required_by_view=None,required_world_points_by_view=None,view_numbers=None,excluded_principal_sources=None):
 d=Path(candidate);layout=json.loads((d/'LAYOUT.json').read_text());s=model['spec'];H=s['floor_height_m'];N=s['floors'];w,h=model['building_xy_m'];library=json.loads((d.parents[2]/'LIBRARY_MANIFEST.json').read_text());titles={x.get('source','')+':'+str(x.get('id')):x.get('title') or str(x.get('id')) for x in library['assets'].values()};allrows=[]
 if s.get('natural24') and required_by_view is None:required_by_view={6:'Theme_F1_05',7:'Theme_F2_03',8:'Theme_F3_08'}
 clear_gallery24=bool(s.get('natural24') or s.get('gallery_clear24'))
 for row in layout:
  if row.get('kind') not in ['exhibit','painting'] or not row.get('floor'):continue
  if 'actual_min_m' in row:center=(np.array(row['actual_min_m'])+row['actual_max_m'])/2;extent=np.asarray(row['actual_max_m'])-row['actual_min_m']
  elif 'anchor_world_m' in row:center=np.asarray(row['anchor_world_m']);extent=np.r_[row['canvas_dimensions_m'][0],.12,row['canvas_dimensions_m'][1]]
  else:continue
  allrows.append({'name':row['name'],'source_key':row['source_key'],'source_title':row.get('source_asset_title') or titles.get(row['source_key']) or row.get('theme',row['name']),'floor':row['floor'],'center_world_m':center.tolist(),'extent_m':extent.tolist(),'facing_normal_world':row.get('facing_normal_world')})
 casefile=d/'DISPLAY_CLEARANCE.json';casebounds=json.loads(casefile.read_text()).get('cases',{}) if casefile.exists() else {};metadata={q['name']:q for q in layout if q.get('name')}
 exact_envelopes=d/'DISPLAY_ENVELOPES23.json'
 if exact_envelopes.exists():casebounds.update(json.loads(exact_envelopes.read_text())['cases'])
 for row in allrows:row['visible_from_gallery_floors']=metadata[row['name']].get('visible_from_gallery_floors',[row['floor']])
 if s.get('glasshouse22'):
  for row in allrows:
   actual=metadata[row['name']]
   if 'gallery_focus_world_m' in actual:row['center_world_m']=actual['gallery_focus_world_m'];row['extent_m']=actual['gallery_foliage_extent_m'];row['projection_target']='Actual leaf mesh bounds, not whole-tree/trunk midpoint'
 for row in allrows:row['display_envelope_m']=casebounds.get(row['name']) or metadata[row['name']].get('display_envelope_m')
 assert len(allrows)>=15*N,'GALLERY_ACTUAL_BOUND_METADATA_MISSING'
 obstacle_file=d/'GALLERY_OCCLUDERS.json';obstacles=json.loads(obstacle_file.read_text())['objects'] if obstacle_file.exists() else [];bounds=np.asarray([q['bounds'] for q in obstacles],dtype=float)
 stair_opaque24=[]
 if s.get('gallery_clear24'):
  for part24 in model['parts']:
   if not part24['name'].startswith('Stair_') or not (part24['role'] in ['tread','stair_wall'] or any(word24 in part24['name'] for word24 in ['Landing','Bridge','Platform'])):continue
   v24=np.asarray(part24['vertices'],dtype=float);stair_opaque24.append({'name':part24['name'],'bounds':[v24.min(0).tolist(),v24.max(0).tolist()]})
  obstacles=obstacles+stair_opaque24;bounds=np.asarray([q['bounds'] for q in obstacles],dtype=float)
 if s.get('fourfloor_actual36'):
  extra36=json.loads((d/'GALLERY_CEILING_OCCLUDERS36.json').read_text());assert extra36['status']=='PASS' and extra36['source_visual_sha256']==hashlib.sha256((d/'scene/museum.usdc').read_bytes()).hexdigest();obstacles=obstacles+extra36['objects'];bounds=np.asarray([q['bounds'] for q in obstacles],dtype=float)
 principal_used=set(excluded_principal_sources or []);previous_eyes=[];retained_slabs=[(i*H,wkt.loads(q['polygon_wkt'])) for i,q in enumerate(model['floors']) if i]
 for number in (view_numbers or [6,7,8]):
  floors=[3,4] if number==8 and N==4 else [1 if number==6 else 2 if number==7 else N];floor=min(floors);poly=wkt.loads(model['floors'][floor-1]['polygon_wkt']);items=[x for x in allrows if x['floor'] in floors];candidates=[]
  eyes=[];natural24_diagnostics=[];four36_diagnostics=[]
  for x,y in [(a*w,b*h) for a in [-.43,-.22,0,.22,.43] for b in [-.41,-.22,0,.22,.41]]:
   if not poly.contains(Point(x,y).buffer(.45)) or y>h*.40-.65:continue
   if len(bounds) and ((np.asarray([x,y,(floor-1)*H+2.2])>=bounds[:,0])&(np.asarray([x,y,(floor-1)*H+2.2])<=bounds[:,1])).all(1).any():continue
   if any(np.linalg.norm(np.asarray(q['center_world_m'])[:2]-[x,y])<2.1 for q in items):continue
   if min(abs(x)/(w/2),abs(y)/(h/2))<.35 and max(abs(x)/(w/2),abs(y)/(h/2))<.75:continue
   eyes.append(np.array([x,y,(floor-1)*H+[2.1,2.3,2.5][number-6]]))
  if s.get('glasshouse22'):
   items=[x for x in allrows if any(f in x['visible_from_gallery_floors'] for f in floors)]
   if s['building_id']=='M28':
    radius=9.5 if floor==1 else 17.4
    for a in np.linspace(0,math.tau,32,endpoint=False):
     p=np.array([radius*math.cos(a),radius*math.sin(a),(floor-1)*H+2.0])
     if poly.covers(Point(*p[:2]).buffer(.45)) and not (len(bounds) and ((p>=bounds[:,0])&(p<=bounds[:,1])).all(1).any()):eyes.append(p)
   elif floor>1:
    origin=np.asarray(model['stair_placements'][0]['origin'])
    for a in np.linspace(0,math.tau,32,endpoint=False):
     xy=origin+9.*np.array([math.cos(a),math.sin(a)]);p=np.r_[xy,(floor-1)*H+2.0]
     if poly.covers(Point(*xy).buffer(.45)) and not (len(bounds) and ((p>=bounds[:,0])&(p<=bounds[:,1])).all(1).any()):eyes.append(p)
  if s.get('glasshouse22') and s['building_id']=='M29':
   # Actual authored bed/curb polygons determine supported aisle eyes.
   # Keep all original frustum, dominance, five-exhibit and distinct-major
   # predicates. Dense botany cannot use sculpture-centre clearance as an
   # aisle proxy; roots and low leaves are independently confined to beds.
   from shapely.ops import unary_union
   bed41=unary_union([wkt.loads(q['actual_polygon_wkt']) for q in layout if q.get('kind')=='planting_bed' and q.get('floor')==floor]);walk41=poly.difference(bed41)
   opaque41=[]
   for part41 in model['parts']:
    if part41['name'].startswith('Stair_') and (part41['role'] in ['tread','stair_wall'] or any(k41 in part41['name'] for k41 in ['Landing','Bridge','Platform'])):
     v41=np.asarray(part41['vertices']);opaque41.append([v41.min(0),v41.max(0)])
   for xx41 in np.arange(-w/2+.8,w/2-.7,1.2):
    for yy41 in np.arange(-h/2+.8,h/2-.7,1.2):
     p=np.array([xx41,yy41,(floor-1)*H+2.0])
     if not walk41.covers(Point(*p[:2]).buffer(.45)):continue
     if any(((p>=lo41)&(p<=hi41)).all() for lo41,hi41 in opaque41):continue
     if len(bounds) and ((p>=bounds[:,0])&(p<=bounds[:,1])).all(1).any():continue
     eyes.append(p)
  if s.get('gallery_unclutter24'):
   # Additional real floor-supported gallery eyes; keep all original wall,
   # exhibit occlusion and actual-photo acceptance requirements.
   for xx24 in np.arange(-w/2+1.,w/2-1.,2.4):
    for yy24 in np.arange(-h/2+1.,h/2-1.,2.4):
     p=np.array([xx24,yy24,(floor-1)*H+2.2])
     if not poly.contains(Point(*p[:2]).buffer(.45)) or any(np.linalg.norm(np.asarray(q['center_world_m'])[:2]-p[:2])<2.1 for q in items):continue
     if len(bounds) and ((p>=bounds[:,0])&(p<=bounds[:,1])).all(1).any():continue
     eyes.append(p)
  required_hero='Theme_F1_00' if s['building_id']=='M04' and number==6 else None
  if required_by_view:required_hero=required_by_view.get(number,required_hero)
  if required_by_view and required_hero:
   hero_center=np.asarray(next(q for q in items if q['name']==required_hero)['center_world_m'])
   for distance24 in ([4.,5.,6.,7.,8.,9.,10.,12.] if clear_gallery24 else [4.,5.5,7.,9.]):
    for angle24 in np.linspace(0,math.tau,24,endpoint=False):
     xy=hero_center[:2]+distance24*np.array([math.cos(angle24),math.sin(angle24)]);p=np.r_[xy,(floor-1)*H+2.2]
     if not poly.contains(Point(*xy).buffer(.45)) or any(np.linalg.norm(np.asarray(q['center_world_m'])[:2]-xy)<2.1 for q in items):continue
     if len(bounds) and ((p>=bounds[:,0])&(p<=bounds[:,1])).all(1).any():continue
     eyes.append(p)
  if number==8 and N==4:
   # A distant gallery eye can project fourth-floor items through an opaque
   # slab. Add elevated eyes just outside the real atrium and reject rays
   # intersecting any actual retained floor polygon below.
   opening=wkt.loads(model['opening_wkt'])
   for offset36 in ([.55,.9,1.4] if s.get('fourfloor_actual36') else [.9]):
    ring=opening.buffer(offset36).exterior
    for t in np.linspace(0,1,32 if s.get('fourfloor_actual36') else 24,endpoint=False):
     for height36 in ([min(3.7,H-.65),H-.65] if s.get('fourfloor_actual36') else [min(3.7,H-.65)]):
      a=ring.interpolate(float(t),normalized=True);p=np.array([a.x,a.y,2*H+height36])
      if poly.contains(Point(a.x,a.y).buffer(.42)) and not (len(bounds) and ((p>=bounds[:,0])&(p<=bounds[:,1])).all(1).any()):eyes.append(p)
   if s.get('fourfloor_actual36'):
    top_poly36=wkt.loads(model['floors'][3]['polygon_wkt'])
    for xx36,yy36 in [(a*w,b*h) for a in [-.43,-.22,0,.22,.43] for b in [-.41,-.22,0,.22,.41]]+[(ring.interpolate(float(t),normalized=True).x,ring.interpolate(float(t),normalized=True).y) for t in np.linspace(0,1,32,endpoint=False)]:
     for zz36 in [3*H+1.6,3*H+2.2,3*H+3.0,3*H+3.7]:
      p=np.array([xx36,yy36,zz36])
      if top_poly36.contains(Point(xx36,yy36).buffer(.42)) and not (len(bounds) and ((p>=bounds[:,0])&(p<=bounds[:,1])).all(1).any()):eyes.append(p)
  if required_hero and not required_by_view:
   for x,y in [(0,13.6),(-7.8,12.8),(7.8,12.8),(-9.8,10.8),(9.8,10.8),(0,12.4)]:
    p=np.array([x,y,2.3])
    if poly.contains(Point(x,y).buffer(.45)) and not (len(bounds) and ((p>=bounds[:,0])&(p<=bounds[:,1])).all(1).any()):eyes.append(p)
  if s.get('fourfloor_min_eye_z38') is not None and number==8:
   eyes=[eye for eye in eyes if eye[2]>=s['fourfloor_min_eye_z38']-1e-6]
  for eye in eyes:
   if s.get('glasshouse22') and number==8 and N==2 and len(previous_eyes)>=2 and np.linalg.norm(eye[:2]-previous_eyes[1][:2])<6:continue
   blocked=set()
   if len(bounds):
    centers=np.asarray([q['center_world_m'] for q in items]);delta=centers-eye;inverse=np.divide(1.,delta,out=np.full_like(delta,1e12),where=np.abs(delta)>1e-12);a=(bounds[:,None,0,:]-eye)*inverse;b=(bounds[:,None,1,:]-eye)*inverse;entry=np.minimum(a,b).max(2);exit=np.maximum(a,b).min(2);blocked={q['name'] for i,q in enumerate(items) if ((entry[:,i]<.995)&(exit[:,i]>.005)&(exit[:,i]>=entry[:,i])).any()}
   if s.get('gallery_unclutter24'):
    opaque=[q for q in items if metadata[q['name']].get('kind')=='exhibit' and 'actual_min_m' in metadata[q['name']]]
    if opaque:
     eb=np.asarray([[metadata[q['name']]['actual_min_m'],metadata[q['name']]['actual_max_m']] for q in opaque]);centers=np.asarray([q['center_world_m'] for q in items]);delta=centers-eye;inverse=np.divide(1.,delta,out=np.full_like(delta,1e12),where=np.abs(delta)>1e-12);a=(eb[:,None,0,:]-eye)*inverse;b=(eb[:,None,1,:]-eye)*inverse;entry=np.minimum(a,b).max(2);leave=np.maximum(a,b).min(2)
     for j,target_item in enumerate(items):
      if any(o['name']!=target_item['name'] and entry[k,j]<.995 and leave[k,j]>.005 and leave[k,j]>=entry[k,j] for k,o in enumerate(opaque)):blocked.add(target_item['name'])
   for yaw in np.linspace(0,math.tau,36,endpoint=False):
    focus=[q for q in items if metadata[q['name']].get('theme_category')=='original_tall_tropical'] if s.get('glasshouse22') and floor>1 else items;target_z=np.mean([q['center_world_m'][2] for q in focus])
    if s.get('east_asian13') and required_world_points_by_view and number in required_world_points_by_view:target_z=(target_z+min(point[2] for point in required_world_points_by_view[number]))/2
    target=eye+np.array([math.cos(yaw)*14,math.sin(yaw)*14,target_z-eye[2]]);forward=target-eye;forward/=np.linalg.norm(forward);right=np.cross(forward,[0,0,1]);right/=np.linalg.norm(right);up=np.cross(right,forward)
    for lens in ([14,16,18,20,22,24,28,32] if s.get('fourfloor_actual36') and number==8 and N==4 else [18,20,22,24,28,32] if clear_gallery24 else [24,28,32] if s.get('glasshouse22') and floor>1 else [20,22,24] if s.get('glasshouse22') else [14,16,18] if required_hero else [16,18] if len(floors)>1 else [18,20,22]):
     if required_world_points_by_view and number in required_world_points_by_view:
      region_rays=np.asarray(required_world_points_by_view[number])-eye;region_depth=region_rays@forward
      if (region_depth<=0).any() or (abs(region_rays@right/region_depth*lens/18)>.85).any() or (abs(region_rays@up/region_depth*lens/10.125)>.85).any():continue
     if clear_gallery24:
      central_column=False
      for obstacle in obstacles:
       if not any(word in obstacle['name'] for word in ['Pillar_','_Column']):continue
       low,high=np.asarray(obstacle['bounds'])
       if not low[2]<=eye[2]<=high[2]:continue
       corners=np.asarray([[x,y,z] for x in [low[0],high[0]] for y in [low[1],high[1]] for z in [low[2],high[2]]]);rays=corners-eye;depth=rays@forward
       if depth.min()<=.3:continue
       px=rays@right/depth*lens/18;py=rays@up/depth*lens/10.125;area=max(0,min(1,px.max())-max(-1,px.min()))*max(0,min(1,py.max())-max(-1,py.min()))/4
       if abs((px.min()+px.max())/2)<.6 and area>.03:central_column=True;break
      if central_column:continue
     projected=[];dominance=0;oversized_central_case=False
     for q in items:
      if q['name'] in blocked:continue
      delta=np.asarray(q['center_world_m'])-eye;distance=float(np.dot(delta,forward))
      if number==8 and N==4:
       slab_blocked=False
       for plane,slab_polygon in retained_slabs:
        if abs(delta[2])<1e-8:continue
        t=(plane-eye[2])/delta[2]
        if .001<t<.999 and slab_polygon.covers(Point(*(eye[:2]+t*delta[:2]))):slab_blocked=True;break
       if slab_blocked:continue
      if distance<=.3:continue
      extent_early=np.asarray(q['extent_m']);center_early=np.asarray(q['center_world_m']);corner_early=center_early+np.asarray([[a,b,c] for a in [-.5,.5] for b in [-.5,.5] for c in [-.5,.5]])*extent_early;ray_early=corner_early-eye;depth_early=ray_early@forward
      if depth_early.min()<=.3:oversized_central_case=True;continue
      px_early=ray_early@right/depth_early*lens/18;py_early=ray_early@up/depth_early*lens/10.125;clipped_area_early=float(max(0,min(1,px_early.max())-max(-1,px_early.min()))*max(0,min(1,py_early.max())-max(-1,py_early.min()))/4)
      if distance<3.4:
       if clipped_area_early>.005:oversized_central_case=True
       continue
      normal=q.get('facing_normal_world')
      if normal is not None and np.dot(-delta,normal)<=0:continue
      xx=float(np.dot(delta,right)/distance*lens/18);yy=float(np.dot(delta,up)/distance*lens/10.125)
      if abs(xx)>.90 or abs(yy)>.86:
       if clipped_area_early>.025:oversized_central_case=True
       continue
      case=q.get('display_envelope_m')
      if case:
       low,high=np.asarray(case);corners=np.asarray([[x,y,z] for x in [low[0],high[0]] for y in [low[1],high[1]] for z in [low[2],high[2]]]);delta_case=corners-eye;depth=delta_case@forward
       if depth.min()<=.3:oversized_central_case=True;continue
       px=delta_case@right/depth*lens/18;py=delta_case@up/depth*lens/10.125;casearea=float(np.ptp(px)*np.ptp(py)/4);dominance=max(dominance,casearea)
       if abs(float((px.min()+px.max())/2))<.5 and py.min()<.75 and py.max()>-.75 and (casearea>.14 or np.ptp(px)>.82 or np.ptp(py)>1.25):oversized_central_case=True
      extent=np.asarray(q['extent_m']);center=np.asarray(q['center_world_m']);corners=center+np.asarray([[a,b,c] for a in [-.5,.5] for b in [-.5,.5] for c in [-.5,.5]])*extent;dd=corners-eye;zz=dd@forward
      if zz.min()<=.3:oversized_central_case=True;continue
      projected_x=dd@right/zz*lens/18;projected_y=dd@up/zz*lens/10.125;area=float(np.ptp(projected_x)*np.ptp(projected_y)/4);dominance=max(dominance,area)
      if area<.00030:continue
      projected.append({**q,'projected_center_normalized_xy':[xx,yy],'approx_projected_area_fraction':area,'projection_only_not_occlusion_verified':True,'importance':area*(1-.25*abs(xx))})
     projected.sort(key=lambda q:-q['importance']);unique=[];keys=set()
     if s.get('natural24') and len(projected)>=5:
      natural24_diagnostics.append({'eye':eye.tolist(),'target':target.tolist(),'lens':lens,'dominance':dominance,'oversized':oversized_central_case,'hero_area':next((q['approx_projected_area_fraction'] for q in projected if q['name']==required_hero),0),'areas':[(q['name'],q['approx_projected_area_fraction']) for q in projected],'above0025':sum(q['approx_projected_area_fraction']>=.0025 for q in projected)})
     for q in projected:
      if q['source_key'] not in keys:unique.append(q);keys.add(q['source_key'])
     fresh=[q for q in unique if q['source_key'] not in principal_used]
     if s.get('fourfloor_actual36') and number==8:
      sizes36={f:sum(q['floor']==f and q['approx_projected_area_fraction']>=.0025 for q in projected) for f in [3,4]};score36=min(sizes36.values())*100+sum(sizes36.values())*3-int(oversized_central_case)*20-int(any(q['source_key'] in principal_used for q in unique[:2]))*5;four36_diagnostics.append({'score':score36,'eye':eye.tolist(),'target':target.tolist(),'lens':lens,'per_floor_above0025':sizes36,'dominance':dominance,'oversized_central_case':oversized_central_case,'prior_principal_in_first2':any(q['source_key'] in principal_used for q in unique[:2]),'specific_hero_projected':any(q['name']==required_hero for q in fresh),'specific_hero_area':next((q['approx_projected_area_fraction'] for q in fresh if q['name']==required_hero),0),'projected':[{'name':q['name'],'floor':q['floor'],'area':q['approx_projected_area_fraction'],'source_key':q['source_key']} for q in projected]});four36_diagnostics=sorted(four36_diagnostics,key=lambda q:-q['score'])[:20]
     if any(q['source_key'] in principal_used for q in unique[:2]):continue
     if required_by_view:
      future_names={name24 for number24,name24 in required_by_view.items() if number24>number};future_keys={q['source_key'] for q in allrows if q['name'] in future_names}
      if any(q['source_key'] in future_keys for q in unique[:2]):continue
     hero=next((q for q in fresh if q['name']==required_hero),None) if required_hero else None
     if s.get('fourfloor_actual36') and number==8 and not required_hero:
      # The user requires actual fourth-floor coverage, not one particular
      # painting. Choose the leading actually unoccluded fourth-floor source;
      # keep the same principal size/centrality and all other predicates.
      hero=fresh[0] if fresh and fresh[0]['floor']==4 and unique[0]['name']==fresh[0]['name'] else None
      if not hero or abs(hero['projected_center_normalized_xy'][0])>.4 or hero['approx_projected_area_fraction']<.012:continue
     if required_hero and (not hero or abs(hero['projected_center_normalized_xy'][0])>.4):continue
     if required_by_view and required_hero and (not unique or unique[0]['name']!=required_hero):continue
     if len(projected)<5 or len(fresh)<2 or dominance>(s.get('gallery_unclutter_max24',.12) if s.get('gallery_unclutter24') else .22) or oversized_central_case:continue
     if clear_gallery24 and (sum(q['approx_projected_area_fraction']>=.0025 for q in projected)<5 or (required_hero and (not hero or hero['approx_projected_area_fraction']<.012))):continue
     if number==8 and N==4 and any(sum(q['floor']==f and (not s.get('fourfloor_actual36') or q['approx_projected_area_fraction']>=.0025) for q in projected)<2 for f in [3,4]):continue
     score=len(projected)*2+len(unique)+sum(q['importance'] for q in projected)*20-dominance*20
     if previous_eyes:score+=min(np.linalg.norm(eye[:2]-p[:2]) for p in previous_eyes)/max(w,h)*3
     chosen=[hero]+[q for q in fresh if q['name']!=hero['name']][:1] if hero else fresh[:2]
     if hero:score+=hero['importance']*(100 if s.get('gallery_unclutter24') else 250)
     candidates.append((score,eye,target,lens,projected,chosen))
  if s.get('natural24') and not candidates:
   diagnostic=sorted(natural24_diagnostics,key=lambda q:(q['hero_area']>0,not q['oversized'],q['above0025'],q['hero_area']),reverse=True)[:8];(d/f'NATURAL24_GALLERY_DIAGNOSTIC_{number}.json').write_text(json.dumps(diagnostic,indent=2));print('NATURAL24_PLAN_DIAGNOSTIC',number,diagnostic[:2],flush=True)
  if s.get('fourfloor_actual36'):
   p36=d/f'GALLERY_FOUR36_DIAGNOSIS_{number}.json';assert not p36.exists();p36.write_text(json.dumps({'status':'PASS' if candidates else 'FAIL','candidate_count':len(candidates),'eye_count':len(eyes),'best_actual_source_projections':four36_diagnostics,'scope':'Source-predicate diagnosis only; actual original coffer/slab occlusion and all original source sizes/hero/principal conditions retained. No actual-image visibility claim.'},indent=2));print('FOURFLOOR36_SOURCE_DIAGNOSIS',len(candidates),four36_diagnostics[:1],flush=True)
  assert candidates,('NO_WIDE_FIVE_EXHIBIT_GALLERY_PLAN',s['building_id'],number,len(items),len(eyes))
  _,eye,target,lens,projected,principal=max(candidates,key=lambda q:q[0]);principal_used.update(q['source_key'] for q in principal);previous_eyes.append(eye);view=next(v for v in views if v['number']==number);view.update({'eye':eye.tolist(),'target':target.tolist(),'lens':lens,'floor_indices':floors,'composition':'Wide gallery depth from actual layout; >=5 visible exhibits must be confirmed in final image, no single case dominates','projected_gallery_candidates':projected,'planned_principal_exhibits':principal,'actual_gallery_review_required':True,'gallery_layout_sha256':hashlib.sha256((d/'LAYOUT.json').read_bytes()).hexdigest()})
 return {'status':'PLANNED_REQUIRES_ACTUAL_EACH_IMAGE_REVIEW','exhibit_rows_with_actual_bounds':len(allrows),'opaque_wall_column_AABB_count':len(obstacles),'actual_raw_stair_opaque_AABB_count':len(stair_opaque24),'opaque_occluder_metadata_sha256':hashlib.sha256(obstacle_file.read_bytes()).hexdigest() if obstacle_file.exists() else None,'principal_source_keys_planned_distinct':sorted(principal_used),'camera_projection_scope':'Pinhole/frustum/size plus conservative opaque wall/column and optional actual RAW solid stair/tread/platform AABB occlusion; not final visible-exhibit evidence. Actual image review must identify>=5 visible exhibits and actual principal sources per image'}
