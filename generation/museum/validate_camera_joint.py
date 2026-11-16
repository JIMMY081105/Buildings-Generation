"""Reject an internal platform point masquerading as the retained slab joint."""
from pathlib import Path
import sys,json,hashlib,datetime
import numpy as np
from shapely import wkt
from shapely.geometry import Point
d=Path(sys.argv[1]);name=sys.argv[2] if len(sys.argv)>2 else 'PRE_CAPTURE24.json';expected=sys.argv[3] if len(sys.argv)>3 else 'PASS';sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();q=json.loads((d/name).read_text());m=json.loads((d/'RAW_MODEL.json').read_text());j=q['junction'];errors=[];names=j.get('sampled_tread_names',[]);exits=[e for e in m['exits'] if e['last_tread'] in names];details=[]
if len(exits)!=1:errors.append('EXACT_EXIT_FROM_SAMPLED_LAST_TREAD_NOT_UNIQUE')
else:
 e=exits[0];floor=wkt.loads(m['floors'][e['floor_index']-1]['polygon_wkt']);start=np.asarray(e['start_xy']);direction=np.asarray(e['direction_xy']);station=next(float(t) for t in np.arange(0,e['landing_length_m']+2.,.01) if floor.covers(Point(*(start+direction*t)).buffer(.02)));target_station=station+.1;actual=float(j['actual_floor_joint_axis_station_m']);details={'exit':e['name'],'floor':e['floor_index'],'retained_slab_first_axis_station_m':station,'required_joint_target_station_m':target_station,'actual_reported_joint_station_m':actual,'joint_target_xy_m':(start+direction*actual).tolist(),'joint_in_actual_retained_slab':floor.covers(Point(*(start+direction*actual)))}
 if abs(actual-target_station)>.0101 or not details['joint_in_actual_retained_slab']:errors.append('INTERNAL_PLATFORM_POINT_IS_NOT_RETAINED_GALLERY_SLAB_JOINT')
if any(sha(d/n)!=digest for n,digest in q['source_bytes_identical'].items()):errors.append('GEOMETRY_SOURCE_CHANGED')
if expected=='PASS' and q['new_plan_sha256']!=sha(d/'PHOTO_PLAN.json'):errors.append('PLAN_BINDING_CHANGED')
doc={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'FAIL' if errors else 'PASS','validator_sha256':sha(__file__),'source_collision_sha256':sha(d/'collision_package/collision_only.usdc'),'source_raw_sha256':sha(d/'RAW_MODEL.json'),'reviewed_camera_receipt':name,'reviewed_camera_receipt_sha256':sha(d/name),'details':details,'violations':errors,'negative_control_status':'FAIL_AS_REQUIRED' if errors and expected=='FAIL' else None,'scope':'Independent exact exit/retained RAW slab polygon axis recomputation, source bytes bound to actual exported CP. Camera may stand on real retained CP landing triangles but the witness target must remain inside the retained gallery slab after the entire landing;>=4 clear tread rays/span/angle requirements unchanged. Actual final paired05/11 still mandatory.'};out=d/('CAMERA_JOINT24.json' if expected=='PASS' else 'CAMERA_JOINT24_NEGATIVE.json')
if out.exists():
 old=json.loads(out.read_text());assert all(old[k]==doc[k] for k in ['status','validator_sha256','source_collision_sha256','source_raw_sha256','reviewed_camera_receipt_sha256','details','violations']),'EXISTING_CAMERA_JOINT_AUDIT_MUST_BE_PRESERVED'
else:out.write_text(json.dumps(doc,indent=2))
print('CAMERA_JOINT24',doc['status'],details,errors,flush=True);sys.exit(0 if doc['status']==expected else 2)
