"""OWN receipt checks only; no USD/SDK/scientific imports."""
def require(v,s):
    if not v:raise ValueError(s)


def completed_parent(parent,worker,wait,workerref):
    require(worker['schema']=='Factory80ActualRenderOnlyCPUAuthorResultV1'
        and worker['status']=='ACTUAL_RENDER_ONLY_CPU_LAYER_AUTHORED_NOT_RENDERED'
        and parent['building']==worker['building']
        and parent['status']=='ACTUAL_RENDER_ONLY_CPU_INPUT_PREPARATION_COMPLETED'
        and parent['actual_signed_child_returncode']==0 and parent['source_unchanged']is True
        and parent['source_refs_before']==parent['source_refs_after']
        and parent['actual_result_ref']==workerref and wait['actual_parent_wait_completed']is True
        and type(wait['actual_signed_returncode'])is int and wait['actual_signed_returncode']==0
        and wait['actual_child_pid']==worker['actual_reader_pid']
        and wait['own_parent_exception']is None and wait['observed_owned_parent_interruptions']==[]
        and wait['actual_owned_child_actions']==[],
        'Genuine clean original L2 CPU parent/worker/actual wait0 required')


def preserved_physics(worker,root):
    require(worker['canonical_absolute_sublayer']==root and worker['source_unchanged']is True
        and worker['source_refs_before']==worker['source_refs_after']
        and worker['whole_flatten_normalization']['whole_string_exact']is True
        and worker['actual_export_reopen_whole_flatten_normalization']['whole_string_exact']is True,
        'Exact physical preservation/reopened normalization required')


def registered_pose(pose,author,root,worker,profile):
    require(pose['authoring_ref']==author and pose['source_root_ref']==root
        and pose['r4_result_ref']==worker and pose['profile_ref']==profile,
        'Before-pixels pose plan must bind identical current source/profile')


def completed_capture(result,wait,expected,device,settings):
    frames=result.get('stills',[])+result.get('orbit_frames',[])
    return (type(wait.get('actual_signed_returncode'))is int and wait['actual_signed_returncode']==0
        and wait.get('actual_wait_completed')is True and result.get('actual_capture_returncode_before_close')==0
        and result.get('status')=='ACTUAL_RGB_CAPTURED_NOT_VISUALLY_REVIEWED'
        and result.get('source_unchanged')is True and bool(result.get('source_refs_before'))
        and result.get('source_refs_before')==result.get('source_refs_after')
        and len(frames)==expected and len({f['file_ref']['path']for f in frames})==expected
        and device.get('status')=='ACTUAL_STARTUP_VULKAN_A40_DEVICE0'
        and all(f.get('actual_L2_readback')==settings for f in frames))
