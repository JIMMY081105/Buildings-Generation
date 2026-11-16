"""Shared CPU L2 preparation for exact selected current N2 worlds/variants.

Reuse completed identical render-only inputs. Otherwise call the reviewed
current-root binder/author and pose planner; no GPU or scientific gate runs.
The original author owns CPU_HEAVY internally: outer guard must not take it.
"""
import argparse,ctypes,os,signal,subprocess,sys,json
from pathlib import Path
W=Path('/cephfs/shared/yixing/outputs/factory80_20261011');I=W/'work/P1_IMPLEMENTATION_20261015'
HERE=Path(__file__).resolve().parent;R3=W/'scripts/cpu_all_serial_20261015_R3'
S=W/'scripts/l2_current_render_only_author_20261015_R5'
CAPTURE=W/'scripts/factorywide_capture_20261015_L2_R1'
sys.path[:0]=[str(I),str(R3),str(W/'scripts/current_multistair_cpu_20261015_R2')]
from owned_memory_guard import dump,self_oom_priority
from stat_hash_cache import StatHashCache
from cpu_all_serial import require,read,actual_terminal,stop
from multistair_cpu_contract import frame_author_matches
from current_l2_inputs_contract import completed_parent,preserved_physics,registered_pose
MAIN='/cephfs/shared/yixing/envs/main/bin/python'


def child(D,name,argv,extra_fds=()):
    require(not stop(),'Existing STOP before original L2 input child')
    keep=[]
    for f in Path('/proc/self/fd').iterdir():
        try:
            st=os.fstat(int(f.name))
            if(st.st_dev,st.st_ino)==(55,1100798614680):keep.append(int(f.name))
        except OSError:pass
    require(keep,'L2 CPU author must have the global owned heavy-memory lease')
    parent=os.getpid()
    def init():
        self_oom_priority()
        require(ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGTERM,0,0,0)==0,'L2 child parent-death signal failed')
        if os.getppid()!=parent:os._exit(125)
    with(D/(name+'.stdout')).open('xb')as out,(D/(name+'.stderr')).open('xb')as err:
        p=subprocess.Popen(argv,stdout=out,stderr=err,env=os.environ.copy(),pass_fds=tuple(set(keep)|set(extra_fds)),preexec_fn=init)
        dump(D/(name+'.BEGIN.json'),dict(child_pid=p.pid,argv=argv,own_parent_death_SIGTERM=True,global_heavy_lease_FD_inherited=True))
        code=p.wait()
    dump(D/(name+'.TRUE_WAIT.json'),dict(actual_child_pid=p.pid,argv=argv,actual_wait_completed=True,actual_signed_returncode=code))
    return code


def main():
    self_oom_priority();p=argparse.ArgumentParser(allow_abbrev=False)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--expected-input-sha256',required=True)
    p.add_argument('--expected-index-sha256',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    C=StatHashCache([W/'work/P1_HASH_CACHE_20261015.json',W/'work/P3_HASH_CACHE_20261015.json',W/'work/P5_R4_NEW_GEOMETRY_CHAIN_20261015_R3/original_subject/ACTUAL_STAT_HASH_CACHE.json'])
    def checked(r):require(C.ref(r['path'])==r,'Actual L2 source changed: '+r['path']);return r
    ix=C.ref(HERE/'SOURCE_INDEX.json');require(ix['sha256']==a.expected_index_sha256,'L2 current CPU source index differs')
    idx=read(ix['path']);before=[ix,*idx['runtime_refs'],*idx['dependency_refs']]
    for r in before:checked(r)
    ir=C.ref(a.input);require(ir['sha256']==a.expected_input_sha256,'Actual current L2 input changed');inp=read(a.input)
    wr=checked(inp['world_input_ref']);row=read(wr['path']);require(row['building']in idx['authorized_29'],'Only authorized29 N2 worlds')
    require(not stop(),'Existing STOP');D=a.output.resolve();require(D.is_relative_to(W/'work')and not D.exists(),'Fresh current L2 input output required');D.mkdir()
    before += [ir,wr,row['root_ref'],row['authoring_ref'],row['raw_authoring_ref'],row['original_n2_plan_ref']]
    root=row['root_ref'];old=read(idx['existing_L2_completed_ref']['path']);reused=None
    # This accepts the explicitly source-bound CURRENT metadata for002R4;
    # its density receipt need not itself contain structural stair fields.
    for candidate in old['rows']:
        ar=checked(candidate['current_authoring_ref']);author=read(ar['path'])
        frame=dict(original_candidate=candidate['current_root_ref'],actual_source_authoring=ar)
        if frame_author_matches(row,frame,author):
            require(reused is None,'Ambiguous identical current L2 input');reused=candidate
    if reused:
        ar=reused['current_authoring_ref'];author=read(ar['path']);workerref=checked(reused['r4_result_ref']);parentref=checked(reused['r4_parent_ref'])
        worker,parent=read(workerref['path']),read(parentref['path']);rawref=checked(parent['raw_wait_ref']);wait=read(rawref['path'])
        require(reused['render_input_prepared']is True and reused['actual_signed_parent_returncode']==0,
            'Actual completed existing L2 row required')
        completed_parent(parent,worker,wait,workerref)
        before += [idx['existing_L2_completed_ref'],ar,workerref,parentref,rawref,*parent['source_refs_before']]
        poseref=checked(reused['pose_plan_ref']);before.append(poseref)
        state='REUSED_GENUINE_COMPLETED_IDENTICAL_CURRENT_RENDER_INPUTS'
    else:
        require(inp.get('frame_manifest_ref')and inp.get('frame_result_ref')and inp.get('frame_guard'),
            'Actual current FrameR2 result needed for new L2 author')
        t=actual_terminal(inp['frame_guard']);require(t and t['code']==0,'Actual current FrameR2 guard signed0 required')
        fr=checked(inp['frame_result_ref']);finite=read(fr['path']);mf=checked(inp['frame_manifest_ref']);frame=read(mf['path'])
        require(finite['source_unchanged']is True and finite['source_refs_before']==finite['source_refs_after']
            and finite['frame_manifest_ref']==mf and finite['current_root_ref']==root,'Actual current frame provenance differs')
        ar=checked(frame['actual_source_authoring']);author=read(ar['path'])
        require(frame_author_matches(row,frame,author),'Current frame author does not bind selected physical root')
        before += [fr,mf,ar,*finite['source_refs_before'],*[C.ref(Path(inp['frame_guard'])/n)for n in ['BEGIN.json','TRUE_WAIT.json','FINITE_RESULT.json']]]
        index=idx['original_L2_author_index_ref'];asset=W/'render_inputs/OWN_PROCEDURAL_PBR_HDRI_20261014_R1';bound=D/'ACTUAL_SOURCE_INPUTS.json'
        argv=[MAIN,'-u',str(S/'read_actual_root_author_bindings.py'),'--execute','--source-index',index['path'],
            '--expected-source-index-sha256',index['sha256'],'--building',row['building'],'--root',root['path'],
            '--expected-root-sha256',root['sha256'],'--authoring',ar['path'],'--expected-authoring-sha256',ar['sha256'],'--output',str(bound)]
        require(child(D,'BINDING',argv)==0,'Original current-root L2 binding failed')
        br=C.ref(bound);out=D/'ORIGINAL_L2_AUTHOR'
        argv=[MAIN,'-u',str(S/'author_render_only_layer.py'),'--execute','--source-index',index['path'],
            '--expected-source-index-sha256',index['sha256'],'--source-inputs',br['path'],
            '--expected-source-inputs-sha256',br['sha256'],'--building',row['building'],
            '--asset-handoff',str(asset/'ACTUAL_ASSET_HANDOFF.json'),'--asset-root',str(asset/'ACTUAL_ASSETS'),'--output',str(out)]
        require(child(D,'AUTHOR',argv)==0,'Original current-root L2 author ordinary failure')
        workerref=C.ref(out/'ACTUAL_RENDER_ONLY_CPU_AUTHOR_RESULT.json');parentref=C.ref(out/'ACTUAL_PARENT_COMPLETION.json')
        worker,parent=read(workerref['path']),read(parentref['path'])
        waitref=checked(parent['raw_wait_ref']);completed_parent(parent,worker,read(waitref['path']),workerref)
        before.append(waitref)
        before += [br,workerref,parentref,*parent['source_refs_before']]
        sys.path.insert(0,str(CAPTURE));import factorywide_pose_plan as camera
        pose=camera.build_plan(author,worker,ar,workerref,idx['fixed_EV_profile_ref'],True)
        pp=D/'PREREGISTERED_LOW_CAMERA_POSES.json';dump(pp,pose);poseref=C.ref(pp)
        state='ORIGINAL_CURRENT_RENDER_ONLY_CPU_AUTHOR_COMPLETED'
    preserved_physics(worker,root)
    pose=read(poseref['path']);registered_pose(pose,ar,root,workerref,idx['fixed_EV_profile_ref'])
    before += [worker['render_layer_ref'],poseref,*worker['source_refs_before']]
    before=list({r['path']:r for r in before}.values());after=[C.ref(r['path'])for r in before]
    require(before==after,'Protected current L2 CPU sources changed')
    dump(D/'FINITE_RESULT.json',dict(schema='Factory80CurrentL2CPUInputsV1',building=row['building'],
        current_root_ref=root,current_authoring_ref=ar,status=state,r4_result_ref=workerref,
        r4_parent_ref=parentref,pose_plan_ref=poseref,profile_ref=idx['fixed_EV_profile_ref'],
        expected_poses=len(pose['poses']),source_refs_before=before,source_refs_after=after,
        source_unchanged=True,GPU_used=False,L2_pixels='NOT_EXECUTED',READY='NOT_INFERRED'))
    C.save(D/'ACTUAL_STAT_HASH_CACHE.json');C.save(W/'work/P1_HASH_CACHE_20261015.json');return 0


if __name__=='__main__':raise SystemExit(main())
