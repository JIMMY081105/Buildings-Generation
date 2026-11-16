"""Original+revised image tables and actual photographic negative controls."""
import argparse,json,sys,subprocess,hashlib,math
from pathlib import Path
from current_l2_cpu_inputs import W,I,HERE,MAIN,read,require,child,stop
from owned_memory_guard import dump,self_oom_priority
from stat_hash_cache import StatHashCache
sys.path.insert(0,str(W/'scripts/image_quality_20261015'))


def main():
    self_oom_priority();p=argparse.ArgumentParser(allow_abbrev=False)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--expected-input-sha256',required=True)
    p.add_argument('--expected-index-sha256',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    C=StatHashCache([W/'work/P1_HASH_CACHE_20261015.json']);ix=C.ref(HERE/'SOURCE_INDEX.json')
    require(ix['sha256']==a.expected_index_sha256,'Current L2 image source index differs');idx=read(ix['path'])
    before=[ix,*idx['runtime_refs'],*idx['dependency_refs']]
    for r in before:require(C.ref(r['path'])==r,'Current L2 image source changed')
    ir=C.ref(a.input);require(ir['sha256']==a.expected_input_sha256,'Current L2 capture finite changed');inp=read(a.input)
    require(inp['capture_completed']is True and inp['source_unchanged']is True and inp['source_refs_before']==inp['source_refs_after'],
        'Genuine complete current L2 capture required')
    before += [ir,*inp['source_refs_before'],inp['actual_capture_result_ref'],inp['actual_renderer_wait_ref']]
    for r in before:require(C.ref(r['path'])==r,'Actual L2 frame source changed')
    require(not stop(),'Existing STOP');D=a.output.resolve();require(D.is_relative_to(W/'work')and not D.exists(),'Fresh L2 image output');D.mkdir()
    gp=D/'ORIGINAL_AND_REVISED_IMAGE_TABLE.json'
    argv=[MAIN,'-u',str(W/'scripts/image_quality_20261015/grade_factory80_capture.py'),
        '--result',inp['actual_capture_result_ref']['path'],'--true-wait',inp['actual_renderer_wait_ref']['path'],
        '--phase','L2','--expected-frames',str(inp['expected_poses']),'--output',str(gp)]
    require(child(D,'GRADE',argv)==0 and gp.exists(),'Original shared L2 image grader failed')
    grade=read(gp);capture=read(inp['actual_capture_result_ref']['path']);items=capture['stills']+capture['orbit_frames']
    # Source-independent rule is frozen before examining any new L2 pixels.
    # First lexicographic filename among ALL revised-PASS frames with genuine
    # paired depth and actual L2 per-frame readback; never adjust ROI/exposure.
    dump(D/'PHOTOGRAPHIC_CONTROL_PREREGISTRATION.json',dict(selection='FIRST_LEXICOGRAPHIC_REVISED_ALL_PASS_REAL_DEPTH_ACTUAL_L2_FRAME',
        capture_result_ref=inp['actual_capture_result_ref'],ROI='EXACT_PRE_PIXEL_CAPTURE_REGISTRATION',
        controls=idx['photographic_controls'],required_L2_profile_ref=idx['L2_profile_ref'],
        transformed_copies_are_not_production_images=True))
    eligible=sorted((r for r in grade['rows']if r['metrics']['revised_all_pass']is True
        and r['actual_AOV_association']=='SAME_EXPLICIT_COMPLETED_REPLICATOR_STEP_RGB_DISTANCE_METRES'),key=lambda r:r['file_ref']['path'])
    control=dict(status='NOT_EXECUTABLE_NO_QUALIFIED_REAL_L2_CARRIER',negative_controls_executed=False)
    if eligible:
        import numpy as np
        from PIL import Image
        from grade_factory80_capture import qualify_image,checked
        from image_quality_20261015 import evaluate,gray_encoded_srgb,decode_png_bytes
        selected=eligible[0];item=next(x for x in items if x['file_ref']==selected['file_ref'])
        require(item['actual_L2_readback']==read(idx['L2_profile_ref']['path'])['required_settings'],'Carrier actual L2 setting differs')
        require(qualify_image(item)['metrics']==selected['metrics'],'Fresh carrier image qualification differs')
        raw={r['annotator_name']:r for r in item['actual_raw_annotator_outputs']}
        rgb=np.load(checked(raw['rgb']['file_ref']),allow_pickle=False);depth=np.load(checked(raw['distance_to_camera']['file_ref']),allow_pickle=False)
        roi=item['registered_roi'];x0,y0,x1,y1=roi['xyxy_halfopen'];gray=gray_encoded_srgb(rgb)
        bright=rgb.copy();k=int(math.ceil(gray.size*.1));ids=np.argpartition(gray.ravel(),-k)[-k:];bright.reshape(-1,3)[ids]=255
        near=depth.copy();blocked=rgb.copy();h,w=gray.shape
        # Exact central 40%: width80% x height50%, 1280x720 => 1024x360.
        blocked[h//4:3*h//4,w//10:9*w//10]=0;near[h//4:3*h//4,w//10:9*w//10]=.5
        black=rgb.copy();black[y0:y1,x0:x1]=0
        binary=np.repeat(np.where(gray>=np.median(gray),255,0).astype(np.uint8)[:,:,None],3,axis=2)
        cases=[('NV1prime',np.rint(rgb.astype(np.float64)*.1).astype(np.uint8),depth,['V1prime','V6']),
            ('NV2prime',bright,depth,['V3']),('NV3prime',np.full_like(rgb,128),depth,['V4prime']),
            ('NV4',blocked,near,['V5']),('NV5prime',black,depth,['V1prime_OR_V2prime']),('NV6prime',binary,depth,['V6'])]
        rows=[]
        for name,pixels,dep,keys in cases:
            f=D/(name+'.png');Image.fromarray(pixels).save(f);df=D/(name+'.depth.npy');np.save(df,dep,allow_pickle=False)
            decoded,meta=decode_png_bytes(f.read_bytes());v=evaluate(decoded,roi,np.load(df,allow_pickle=False))
            failed=(v['revised']['V1prime']=='FAIL'or v['revised']['V2prime']=='FAIL')if name=='NV5prime'else all(v['revised'][q]=='FAIL'for q in keys)
            require(name!='NV3prime'or v['revised']['V6']=='PASS','Expected NV3prime V6 PASS')
            if name=='NV5prime':
                mask=np.ones(gray.shape,dtype=bool);mask[y0:y1,x0:x1]=False
                require(np.array_equal(pixels[mask],rgb[mask])and np.all(pixels[y0:y1,x0:x1]==0),'NV5 changed background or failed black ROI')
            rec=dict(control=name,required_failures=keys,status='FAIL_AS_REQUIRED'if failed else'DID_NOT_FAIL_CHECK_IS_INVALID',
                actual=v,image_ref=C.ref(f),depth_ref=C.ref(df),carrier_ref=selected['file_ref'],registered_ROI=roi)
            dump(D/(name+'.RESULT.json'),rec);rows.append(rec)
            if not failed:
                shared=W/'scripts/cpu_all_serial_20261015_R2/CRITICAL_STOP.json'
                if not shared.exists():dump(shared,dict(entire_task_stop=True,reason='Actual photographic negative control escaped',actual_control=rec))
                dump(D/'CRITICAL_STOP_NEGATIVE_ESCAPE.json',rec);return 80
        control=dict(status='ACTUAL_PHOTOGRAPHIC_NEGATIVE_CONTROLS_COMPLETE',rows=rows,carrier_ref=selected['file_ref'],
            actual_qualified_L2=True,production_ROI_or_exposure_modified=False)
    after=[C.ref(r['path'])for r in before];require(before==after,'L2 image sources changed')
    dump(D/'FINITE_RESULT.json',dict(schema='Factory80CurrentL2ImageGatesV1',current_root_ref=inp['current_root_ref'],
        original_and_revised_report_ref=C.ref(gp),image_summary=grade['summary'],photographic_controls=control,
        actual_frame_denominator=inp['expected_poses'],source_refs_before=before,source_refs_after=after,source_unchanged=True,
        physical_G25_navigation_READY='NOT_INFERRED',human_view03_or_CLASH_approved=False))
    C.save(D/'ACTUAL_STAT_HASH_CACHE.json');return 0


if __name__=='__main__':raise SystemExit(main())
