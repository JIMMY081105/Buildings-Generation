"""Reuse actual author/wait0; only alias the byte-identical frozen EV profile."""
import argparse,copy,json,sys
from pathlib import Path
from current_l2_cpu_inputs import W,I,HERE,read,require,stop
from current_l2_inputs_contract import registered_pose,preserved_physics
from owned_memory_guard import self_oom_priority,dump
from stat_hash_cache import StatHashCache


def equivalent_profile_and_pose(old,new,oldprofile,newprofile):
    require((oldprofile['bytes'],oldprofile['sha256'])==(newprofile['bytes'],newprofile['sha256']), 'Frozen profile bytes differ')
    require(old['profile_ref']==oldprofile and new['profile_ref']==newprofile,'Actual pose profile binding differs')
    require({k:v for k,v in old.items()if k!='profile_ref'}=={k:v for k,v in new.items()if k!='profile_ref'},'Pose/ROI/source changed beyond identical profile path alias')


def main():
    self_oom_priority();p=argparse.ArgumentParser(allow_abbrev=False);p.add_argument('--input',type=Path,required=True);p.add_argument('--expected-input-sha256',required=True)
    p.add_argument('--expected-index-sha256',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    C=StatHashCache([W/'work/P1_HASH_CACHE_20261015.json']);ix=C.ref(HERE/'SOURCE_INDEX.json')
    require(ix['sha256']==a.expected_index_sha256,'L2 reuse source index differs');idx=read(ix['path']);before=[ix,*idx['runtime_refs'],*idx['dependency_refs']]
    for r in before:require(C.ref(r['path'])==r,'Actual L2 reuse source changed')
    ir=C.ref(a.input);require(ir['sha256']==a.expected_input_sha256,'Actual L2 reuse descriptor differs');inp=read(a.input)
    from cpu_all_serial import actual_terminal
    t=actual_terminal(inp['original_CPU_guard']);require(t and t['code']==0,'Only clean original CPU wait0 can be reused')
    rr=inp['original_CPU_result_ref'];require(C.ref(rr['path'])==rr,'Original completed CPU result changed');d=read(rr['path'])
    require(d['schema']=='Factory80CurrentL2CPUInputsV1'and d['source_unchanged']is True and d['source_refs_before']==d['source_refs_after'],'Actual original L2 CPU finite required')
    require(not stop(),'Existing STOP');D=a.output.resolve();require(D.is_relative_to(W/'work')and not D.exists(),'Fresh L2 metadata reuse output');D.mkdir()
    oldref=d['pose_plan_ref'];oldprofile=d['profile_ref'];newprofile=idx['fixed_EV_profile_ref']
    for r in [oldref,oldprofile,newprofile]:require(C.ref(r['path'])==r,'Actual before-pixels profile/pose changed')
    old=read(oldref['path']);new=copy.deepcopy(old);new['profile_ref']=newprofile;equivalent_profile_and_pose(old,new,oldprofile,newprofile)
    registered_pose(new,d['current_authoring_ref'],d['current_root_ref'],d['r4_result_ref'],newprofile)
    preserved_physics(read(d['r4_result_ref']['path']),d['current_root_ref'])
    pp=D/'PREREGISTERED_IDENTICAL_POSES_PROFILE_PATH_ALIAS.json';dump(pp,new)
    before += [ir,rr,*d['source_refs_before'],oldref,oldprofile,newprofile,C.ref(pp),*[C.ref(Path(inp['original_CPU_guard'])/n)for n in ['BEGIN.json','TRUE_WAIT.json','FINITE_RESULT.json']]]
    before=list({r['path']:r for r in before}.values());after=[C.ref(r['path'])for r in before];require(before==after,'Completed L2 CPU source changed during metadata alias')
    result=dict(d,status='REUSED_ACTUAL_CPU_AUTHOR_IDENTICAL_PROFILE_BYTES_NEW_PATH_ONLY',profile_ref=newprofile,pose_plan_ref=C.ref(pp),
        source_refs_before=before,source_refs_after=after,source_unchanged=True,actual_original_CPU_result_ref=rr,
        actual_original_CPU_terminal=t,original_CPU_author_rerun=False,physics_camera_ROI_EV_pixels_changed=False)
    dump(D/'FINITE_RESULT.json',result);C.save(D/'ACTUAL_STAT_HASH_CACHE.json');return 0


if __name__=='__main__':raise SystemExit(main())
