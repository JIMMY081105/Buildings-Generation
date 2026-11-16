"""Separate verified SDK identity from unverified host NVML PID observations."""
from pathlib import Path


def observe(owner, session, before, after, native_source_sha256):
    if session.get('pid') != owner['pid'] or str(session.get('birth')) != str(owner['birth']):
        raise RuntimeError('SDK SESSION PID/birth differs from the owned process')
    if session.get('source_sha256') != native_source_sha256:
        raise RuntimeError('SDK SESSION source differs from the qualified native tool')
    device = session.get('actual_GPU', '')
    if 'NVIDIA A40' not in device or 'Yes: 0' not in device:
        raise RuntimeError('Owned SDK did not confirm actual A40 renderer device0')
    # NVML reports host PIDs; /proc in this container exposes namespace PIDs.
    # Concurrent unrelated joins and numeric PID collisions cannot prove ownership.
    return dict(native_container_pid=owner['pid'], native_birth=owner['birth'],
                native_namespace_pids=owner.get('namespace_pids', []),
                GPU_before_ids=sorted(before), GPU_after_mib=after,
                newly_observed_UNATTRIBUTED_host_pids=sorted(set(after) - set(before)),
                own_GPU_host_pids=[], host_PID_identity_status='UNRESOLVED_IN_CONTAINER_NAMESPACE',
                owned_SDK_SESSION_identity_verified=True, SDK_actual_GPU=device,
                host_PID_mapping_is_admission_gate=False,
                ownership_method='EXACT_INHERITED_TASK_TOKEN_AND_CONTAINER_PID_BIRTH',
                OOM_attribution='EXISTING_KERNEL_CGROUP_AND_OWNED_SIGKILL_CHECKS; no inferred hostPID')


def receipt_directory(house, number, worker_owner):
    if type(number) is not int or not 0 <= number <= 2:
        raise ValueError('Authorized resource attempt must be 0,1,2')
    birth = str(worker_owner['start_ticks'])
    if not birth.isdigit() or not isinstance(worker_owner['pid'], int):
        raise ValueError('Actual worker PID/birth required for immutable SDK receipt directory')
    return Path(house) / 'resource_attempts' / f"try_{number:02d}_worker_{worker_owner['pid']}_{birth}"
