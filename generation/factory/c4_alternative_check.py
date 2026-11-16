"""Deterministic C4 alternative v8 using the frozen round4 layout engine.

CPU authoring only. This module never generates stairs or invokes C1-C5/SDK/GPU.
The caller retains the old failures and adopts the NEW budget receipt.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import os
import sys
import time

VERSION='c4_alternative@v8'

FIT_FAILURE_EXACT={
    'NO_MUTUALLY_COMPATIBLE_ACTUAL_STAIR_LAYOUT',
    'NO_DISTINCT_SINGLE_DETERMINISTIC_POSITION_ALTERNATIVE',
    'Actual net mezzanine geometry violates40-44% or connectivity',
    'Actual staircase opening outside selected bay-aligned mezzanine',
}


def named_fit_failure(error_type,message):
    """A search cap, bad source and arbitrary ValueError remain technical."""
    if error_type!='ValueError':return None
    if message in FIT_FAILURE_EXACT:return message
    parts=message.split(':')
    if len(parts)==3 and parts[0]=='NO_ACTUAL_STAIR_FIT' and parts[1] in ('S01','S02','S03') and parts[2] in ('straight','l_shaped','u_shaped'):
        return 'NO_ACTUAL_STAIR_FIT'
    return None


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def ref(path):
    return dict(path=str(Path(path).resolve()),sha256=digest(path))


def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as stream:
        json.dump(value,stream,indent=2,ensure_ascii=False,allow_nan=False)
        stream.write('\n')


def stable_rank(*parts):
    return hashlib.sha256('/'.join(map(str,('Factory80C4AlternativeV2',*parts))).encode()).hexdigest()


def failed_ids(checks):
    c4=checks.get('C4',{})
    if c4.get('status')!='FAIL':raise ValueError('ACTUAL_C4_FAIL_REQUIRED')
    ids=[]
    for row in c4.get('stairs',[]):
        opening=row.get('opening_coverage',{})
        if row.get('status')=='FAIL' or any(t.get('status')=='FAIL' for t in row.get('treads',[])) or (isinstance(opening,dict) and opening.get('status')=='FAIL'):
            ids.append(row['stair_id'])
    if not ids:raise ValueError('ACTUAL_FAILED_STAIR_IDS_MISSING')
    return sorted(set(ids))


def validate_budget(budget,building,index,accepted_index=None):
    if str(budget['building']).zfill(3)!=building:raise ValueError('BUDGET_BUILDING_MISMATCH')
    repairs=int(budget['postC2_C4_layout_repairs_used'])
    if repairs not in range(4):raise ValueError('POSTLAYOUT_REPAIR_COUNT_OUTSIDE_0_TO_3')
    if int(budget['C4_alternatives_used'])!=0:raise ValueError('ONE_C4_ALTERNATIVE_ALREADY_USED')
    used=sorted(set(map(int,budget['layout_seed_indices_used'])))
    if any(i not in range(20) for i in used):raise ValueError('LAYOUT_INDEX_OUTSIDE_FIXED_0_TO_19')
    if repairs!=3 and len(used)!=20:raise ValueError('POSTLAYOUT_REPAIRS_OR20_DISTINCT_SEEDS_MUST_BE_EXHAUSTED')
    accepted_index=int(budget['accepted_layout_seed_index'] if accepted_index is None else accepted_index)
    if index!=accepted_index or index not in used:raise ValueError('ALTERNATIVE_REUSES_CURRENT_ACCEPTED_FIXED_SEED_ONLY')
    return used


def approved_metadata(path,approval):
    meta=read(path);checks=meta.get('collision_checks',{})
    if (meta.get('version') not in ('stairs_collision_v8','stairs_collision_v9') or meta.get('exit_code')!=0
        or checks.get('PASS') is not True or meta.get('representation_approved') is not True
        or meta.get('contract_status')!='USER_APPROVED_NATIVE_TRIANGLES_NONBOX'):
        raise ValueError('EXISTING_NATIVE_ASSET_NOT_APPROVED:'+str(path))
    a=meta.get('approval_record',{})
    if a.get('path')!=approval['path'] or a.get('sha256')!=approval['sha256']:
        raise ValueError('EXISTING_ASSET_APPROVAL_BINDING_MISMATCH')
    if checks['volume_relative_error']>.01 or checks['max_tread_top_error_m']>.005 or checks['missing_tread_levels']:
        raise ValueError('EXISTING_ASSET_RECORDED_CONSTRUCTOR_LIMITS_FAIL')
    return meta


def donor_record(project,row,plan,approval):
    """Read existing evidence only; no generation or requalification."""
    home=project/'runs'/str(row['building']).zfill(3)/f"v4/stairs/S{row['stair_index']:02d}"
    metadata=home/'collision_v9/STAIR_COLLISION.json'
    if not metadata.is_file():metadata=home/'collision_v8/STAIR_COLLISION.json'
    meta=approved_metadata(metadata,approval)
    metrics_path=Path(row['metrics_path']);metrics=read(metrics_path)
    effective=metrics.get('style_override_trace',{}).get('effective',metrics['style'])
    if (metrics['kind']!=row['kind'] or int(metrics['seed'])!=int(row['actual_native_seed'])
        or float(metrics['adapted_dimensions']['target_height_m'])!=4.
        or effective.get('support_type')!='solid' or effective.get('handrail_type')!=plan['handrail_type']):
        raise ValueError('ACTUAL_NATIVE_DONOR_IDENTITY_STYLE_HEIGHT_MISMATCH')
    begin=Path(row['receipt']).parent/'BEGIN.json';argv=read(begin).get('argv',[])
    if not argv or any(str(a).startswith('--') and 'width' in str(a).lower() for a in argv):
        raise ValueError('DONOR_NATIVE_WIDTH_OVERRIDE_OR_MISSING_ACTUAL_ARGV')
    presentation=home/'visual_v1/PRESENTATION_RECEIPT.json';pr=read(presentation)
    visual=Path(pr['asset_stage'])
    if pr.get('style')!=plan['material'] or pr.get('source_only') is not True or pr.get('exit_code')!=0 or pr.get('collision_representation_created') is not False:
        raise ValueError('DONOR_SOURCE_ONLY_MATERIAL_MISMATCH')
    if pr.get('input_sha',{}).get(str(metrics_path))!=digest(metrics_path) or meta.get('input_sha',{}).get(str(metrics_path))!=digest(metrics_path):
        raise ValueError('DONOR_NATIVE_METRICS_NOT_BOUND_TO_BOTH_VISUAL_AND_COLLISION')
    if digest(visual)!=pr['output_sha']:raise ValueError('DONOR_VISUAL_BYTES_CHANGED')
    for key in ('collision_proxy','collision_readback'):
        bound=meta[key]
        if digest(bound['path'])!=bound['sha256']:raise ValueError('DONOR_APPROVED_BYTES_CHANGED:'+key)
    return dict(row=copy.deepcopy(row),metadata_path=metadata,metadata=meta,asset_stage=visual,
                presentation_receipt=presentation,native_begin=begin)


def choose_donors(project,raw_rows,plan,oldspec,failed,approval):
    byid={s['id']:s for s in oldspec['stairs']};chosen={};inventory=[]
    for sid in failed:
        wanted='l_shaped' if byid[sid]['kind']=='straight' else 'straight'
        candidates=[];rejected=[]
        for row in raw_rows:
            if row['kind']!=wanted or row.get('handrail_type')!=plan['handrail_type'] or row.get('planned_material')!=plan['material']:continue
            try:record=donor_record(project,row,plan,approval)
            except (OSError,ValueError,KeyError) as error:
                rejected.append(dict(building=row['building'],stair_index=row['stair_index'],reason=str(error)));continue
            rank=stable_rank(plan['building_seed'],sid,row['building'],row['stair_index'],row['actual_native_seed'],record['metadata']['collision_proxy']['sha256'])
            candidates.append((rank,record))
        candidates.sort(key=lambda x:x[0])
        inventory.append(dict(stair_id=sid,requested_kind=wanted,compatible_count=len(candidates),rejected=rejected,
                              ranked_donors=[dict(rank=r,building=q['row']['building'],stair_index=q['row']['stair_index'],native_seed=q['row']['actual_native_seed'],metadata=ref(q['metadata_path'])) for r,q in candidates]))
        if candidates:chosen[sid]=candidates[0][1]
    # Type+position combinations are not searched. Missing ANY replacement
    # causes one whole-building position-only alternative with original assets.
    return (chosen if len(chosen)==len(failed) else {}),inventory


def conditional_uses(base,project,config,domain,layout,pools):
    from shapely.geometry import box,shape
    library_path=project/'asset_library/LIBRARY.json';library={r['id']:r for r in read(library_path)['assets']}
    lookup={r['key']:r for r in pools};upper=shape(domain['floors']['Floor2']['support_geometry']);rows=[]
    for instance in layout['instances']:
        source=lookup[instance['asset_id']]
        if source.get('technical_tag')!='UNDER_MEZZ_ONLY':continue
        evidence=library[source['key']];lo,hi=instance['world_union_bounds_m']
        values={float(v['conservative_asset_support_lower_z_m']) for v in evidence['conditional_placement'].values()}
        if len(values)!=1:raise ValueError('AMBIGUOUS_EXISTING_CONDITIONAL_SUPPORT_METADATA')
        lower=next(iter(values));gap=domain['upper_slab_bottom_z_m']-(lo[2]+lower-source['actual_union_bounds_m'][0][2])
        covered=upper.covers(box(lo[0],lo[1],hi[0],hi[1]))
        good=instance['floor']=='Floor1' and covered and gap<config['standability_pinned']['body_height_m']
        rows.append(dict(prim_path=instance['prim_path'],asset_id=source['key'],actual_net_slab_covers_full_bounds=covered,
                         actual_standing_gap_upper_bound_m=gap,condition_satisfied=good,isolated_V1_original=evidence['V1_static']['status']))
    if any(not r['condition_satisfied'] for r in rows):raise ValueError('ACTUAL_NEW_DOMAIN_CONDITIONAL_ASSET_USE_FAIL')
    return dict(library=ref(library_path),rows=rows,new_V1_executed=False)


def prepare(args):
    """One mapping/position salt; reuse the accepted equipment layout seed."""
    import hall_layout as base
    import resource
    project=args.project_root.resolve();building=str(args.building).zfill(3);source=args.source_directory.resolve();out=args.output.resolve()
    base.CONFIG_PATH=args.config.resolve();started=time.monotonic()
    base.apply_layout_worker_limit(read(args.config)['v4']['layout_resource_policy'])
    master_path=project/'pipeline/v4/V4_BUILDING_PLAN.json';master=read(master_path)
    original_plan=next(r for r in master['buildings'] if r['building']==building);plan=copy.deepcopy(original_plan)
    old={name:read(source/name) for name in ('LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json')}
    actual_stairs=old['stairs_spec.json']['stairs']
    if len(actual_stairs)!=plan['stair_count'] or [s['id'] for s in actual_stairs]!=[f'S{i:02d}' for i in range(1,plan['stair_count']+1)]:
        raise ValueError('ACCEPTED_ACTUAL_STAIR_IDENTITY_OR_ORDER_DIFFERS')
    if any(s['kind'] not in ('straight','l_shaped','u_shaped') for s in actual_stairs):raise ValueError('UNKNOWN_ACCEPTED_ACTUAL_STAIR_KIND')
    plan['stair_kinds']=[s['kind'] for s in actual_stairs]
    expected_seed=str(base.layout_seed_value(read(args.config),plan,args.layout_index))
    if old['LAYOUT.json'].get('layout_tier')!=base.layout_label(read(args.config)) or old['LAYOUT.json'].get('layout_threshold_profile')!=base.tier_name(read(args.config)) or old['LAYOUT.json'].get('layout_seed_value_decimal')!=expected_seed:
        raise ValueError('ACCEPTED_TIER_LAYOUT_SEED_BINDING_MISMATCH')
    if any(str(record.get('building')).zfill(3)!=building for record in old.values()):raise ValueError('SOURCE_BUILDING_MISMATCH')
    failed=failed_ids(read(args.failed_checks));oldspec=old['stairs_spec.json'];known={s['id'] for s in oldspec['stairs']}
    if not set(failed)<=known:raise ValueError('FAILED_STAIR_NOT_IN_ACTUAL_SOURCE')
    budget=read(args.budget_state)
    if old['LAYOUT.json']['status']!='LAYOUT_PASS':raise ValueError('CURRENT_ACCEPTED_ORDINARY_LAYOUT_REQUIRED')
    used=validate_budget(budget,building,args.layout_index,old['LAYOUT.json']['layout_attempt'])
    approval=ref(args.approval_record);a=read(args.approval_record)
    if a.get('approved') is not True or a.get('policy')!='native_exact_triangles_for_nonbox':raise ValueError('EXPLICIT_NATIVE_TRIANGLE_APPROVAL_REQUIRED')
    summary_path=project/'pipeline/v4/NATIVE_STAIR_PREGEN_V1/RESULT.json';raw_rows=read(summary_path)['assets']
    if len(raw_rows)!=72 or len({(r['building'],r['stair_index']) for r in raw_rows})!=72:raise ValueError('EXISTING_FIXED72_DONOR_MAP_DIFFERS')
    donors,inventory=choose_donors(project,raw_rows,plan,oldspec,failed,approval)
    mode='TYPE' if donors else 'POSITION';selected=[];changes=[]
    for index,oldrow in enumerate(oldspec['stairs']):
        sid=oldrow['id']
        if sid in donors:
            record=donors[sid];plan['stair_kinds'][index]=record['metadata']['kind']
        else:
            raw=next((r for r in raw_rows if int(r['actual_native_seed'])==int(oldrow['seed']) and r['kind']==oldrow['kind']),None)
            if raw is None:raise ValueError('ORIGINAL_STAIR_NOT_IN_EXISTING72_MAP:'+sid)
            record=donor_record(project,raw,plan,approval)
            # Keep exact original existing physical and visual source refs.
            record['metadata_path']=Path(oldrow.get('collision_metadata') or oldrow['metadata_path'])
            record['metadata']=approved_metadata(record['metadata_path'],approval)
            record['asset_stage']=Path(oldrow['asset_stage']['path'])
            if digest(record['asset_stage'])!=oldrow['asset_stage']['sha256']:raise ValueError('ORIGINAL_FIXED_VISUAL_BYTES_CHANGED')
        selected.append(record)
        changes.append(dict(stair_id=sid,original=copy.deepcopy(oldrow),actual_native_donor_building=record['row']['building'],
            actual_native_donor_stair_index=record['row']['stair_index'],actual_native_donor_seed=record['metadata']['seed'],
            effective_kind=record['metadata']['kind'],kind_changed=record['metadata']['kind']!=oldrow['kind'],
            actual_metadata=ref(record['metadata_path']),visual_source=ref(record['asset_stage']),native_begin=ref(record['native_begin'])))
    placement_plan=copy.deepcopy(plan)
    if mode=='POSITION':placement_plan['building_seed']=int(stable_rank(plan['building_seed'],'one-position-alternative'),16)
    effective=dict(schema='Factory80SingleC4AlternativeV2',building_plan=plan,master_plan=ref(master_path),
        source_directory=str(source),source_refs={n:ref(source/n) for n in old},original_stair_kinds=original_plan['stair_kinds'],accepted_baseline_stair_kinds=[s['kind'] for s in actual_stairs],
        alternative_index=1,alternatives_used=1,alternatives_remaining=0,alternative_mode=mode,failed_stair_ids=failed,
        donor_inventory=inventory,stair_source_history=changes,master_modified=False,native_width_override=False,
        layout_seed_index=args.layout_index,fixed_layout_seed_decimal=str(base.layout_seed_value(read(args.config),plan,args.layout_index)),
        layout_tier=base.layout_label(read(args.config)),layout_threshold_profile=base.tier_name(read(args.config)),layout_engine=ref(base.__file__),
        position_candidate_rank_seed=str(placement_plan['building_seed']),equipment_master_seed_unchanged=True,
        unchanged_stair_positions_may_move='existing joint place_stairs solver; all actual before/after poses recorded',
        original_failed_checks=ref(args.failed_checks),prior_budget=ref(args.budget_state),approval_record=approval)
    out.mkdir(parents=True,exist_ok=False)
    write_new(out/'EFFECTIVE_BUILDING_PLAN.json',effective)
    current_budget=dict(schema='Factory80C4AlternativeBudgetV2',building=building,total_layout_seed_count=20,
        layout_seed_indices_used=used,postC2_C4_layout_repairs_used=int(budget['postC2_C4_layout_repairs_used']),C4_alternatives_used=1,C4_alternatives_remaining=0,
        source_budget=ref(args.budget_state),alternative_index=1,layout_index_invoked=False,accepted_layout_seed_index=args.layout_index,
        reused_existing_seed_for_type_position_only=True,unique_layout_seed_count_unchanged=True)
    write_new(out/'ALTERNATIVE_STARTED.json',dict(tool=VERSION,source=ref(__file__),budget=current_budget,effective_plan=ref(out/'EFFECTIVE_BUILDING_PLAN.json')))
    try:
        roles=[]
        for index,record in enumerate(selected,1):
            donor=record['row'];role_source=project/'runs'/str(donor['building']).zfill(3)/f"v4/c4_v9_geometry/S{donor['stair_index']:02d}/NATIVE_FACE_ROLES.json"
            role=read(role_source);blend=Path(donor['asset_dir'])/'source.blend'
            if Path(role['source_blend']).resolve()!=blend.resolve() or role['source_sha']!=digest(blend):raise ValueError('DONOR_FACE_ROLE_SOURCE_BINDING_MISMATCH')
            target=out/f'roles_root/S{index:02d}/NATIVE_FACE_ROLES.json';target.parent.mkdir(parents=True,exist_ok=True)
            target.symlink_to(role_source.resolve())
            if not target.is_file() or digest(target)!=digest(role_source):raise ValueError('DONOR_FACE_ROLE_SYMLINK_UNRESOLVED')
            roles.append(dict(stair_id=f'S{index:02d}',path=str(target),actual_target=ref(role_source),source_blend=ref(blend)))
        records=[dict(metadata_path=r['metadata_path'],metadata=r['metadata'],asset_stage=str(r['asset_stage'])) for r in selected]
        spec=base.place_stairs(placement_plan,records,old['SPATIAL_DOMAINS.json'])
        moved=[s['id'] for s,t in zip(spec['stairs'],oldspec['stairs']) if s['offset_m']!=t['offset_m'] or s['rotation_deg']!=t['rotation_deg']]
        if mode=='POSITION' and not any(s in failed for s in moved):raise ValueError('NO_DISTINCT_SINGLE_DETERMINISTIC_POSITION_ALTERNATIVE')
        recovery=summary_path.parent/'METADATA_RECOVERY_SUMMARY_V1.json'
        for index,(row,record) in enumerate(zip(spec['stairs'],selected),1):
            raw=base.raw_stair_layout_metadata(record['row'],summary_path,record['presentation_receipt'],recovery)
            path=out/f'S{index:02d}_ACTUAL_LAYOUT_METADATA.json';write_new(path,raw)
            row.update(layout_metadata=str(path),asset_stage=ref(record['asset_stage']),collision_proxy=dict(record['metadata']['collision_proxy']),
                       layout_only=False,physics_candidate_created=True)
        domain=base.make_domain(plan,old['SPATIAL_DOMAINS.json'],spec['stairs'])
        domain=base.measured_exterior_doors(project,plan,domain)
        config=read(args.config);domain=base.prepare_round4_alternative_domain(config,domain,old['SPATIAL_DOMAINS.json'],plan)
        base.layout_policy(config);pools_path=project/'pipeline/v4/ROLE_POOLS_V4.json';pools=read(pools_path)['pools']
        # Record invocation before calling the ordinary placer; source/runtime
        # failures are retained distinctly from scientific LAYOUT_FAIL.
        current_budget.update(layout_index_invoked=True)
        write_new(out/'LAYOUT_INDEX_INVOKED.json',current_budget)
        layout=base.place_instances(config,domain,pools,plan,args.layout_index)
        conditional=conditional_uses(base,project,config,domain,layout,pools)
        inputs={str(p):digest(p) for p in [args.config,master_path,pools_path,args.budget_state,args.failed_checks,out/'EFFECTIVE_BUILDING_PLAN.json',Path(base.__file__),Path(__file__)]}
        layout.update(input_sha=inputs,effective_plan=ref(out/'EFFECTIVE_BUILDING_PLAN.json'),C4_alternative_index=1)
        for value in (layout,domain,spec):
            value.update(layout_only=False,physics_candidate_created=True,collision_qualification='C1_C5_NOT_EXECUTED',
                         checks={f'C{i}':'NOT_EXECUTED' for i in range(1,6)},READY=False,C4_alternative_index=1)
        for name,value in [('LAYOUT.json',layout),('SPATIAL_DOMAINS.json',domain),('stairs_spec.json',spec)]:write_new(out/name,value)
        write_new(out/f'LAYOUT_ATTEMPT_{args.layout_index}.json',layout)
        result=dict(schema='Factory80C4AlternativeReceiptV2',tool=VERSION,building=building,status=layout['status'],
            alternative_mode=mode,alternative_index=1,failed_stair_ids=failed,moved_stair_ids=moved,
            actual_new_stair_poses=spec['stairs'],effective_plan=ref(out/'EFFECTIVE_BUILDING_PLAN.json'),budget=current_budget,
            conditional_asset_use=conditional,actual_net_mezzanine_fraction=domain['actual_net_mezzanine_fraction'],
            ordinary_layout_audit=layout['audit'],C1_C5='NOT_EXECUTED',READY=False)
        result.update(roles_root=str(out/'roles_root'),native_face_role_bindings=roles)
        if layout['status']=='LAYOUT_PASS':
            bound=base.bind_physics_inputs(project,building,out,[r['metadata_path'] for r in selected],args.approval_record,out/'physics_inputs',out/'EFFECTIVE_BUILDING_PLAN.json',[str(r['asset_stage']) for r in selected])
            result.update(status='PHYSICS_INPUTS_PREPARED_NOT_CHECKED',physics_inputs=str(out/'physics_inputs'),binding_receipt=bound)
        result.update(elapsed_s=time.monotonic()-started,peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                      input_sha=inputs,output_sha={str(out/n):digest(out/n) for n in ('LAYOUT.json','SPATIAL_DOMAINS.json','stairs_spec.json')})
        write_new(out/'C4_ALTERNATIVE_RECEIPT.json',result);write_new(out/'C4_ALTERNATIVE_BUDGET.json',current_budget)
        return result
    except Exception as error:
        import traceback
        code=named_fit_failure(type(error).__name__,str(error))
        if code is not None:
            # This was the single deterministic geometry alternative. No new
            # donor/position/seed is searched after an actual fit failure.
            failure=dict(schema='Factory80C4AlternativeReceiptV3',tool=VERSION,building=building,
                status='LAYOUT_FAIL',failure_stage='STAIR_GEOMETRY_FIT',fit_failure_code=code,
                error_type=type(error).__name__,error=str(error),alternative_index=1,
                alternatives_remaining=0,budget=current_budget,alternative_mode=mode,
                effective_plan=ref(out/'EFFECTIVE_BUILDING_PLAN.json'),elapsed_s=time.monotonic()-started,
                C1_C5='NOT_EXECUTED',READY=False)
            write_new(out/'C4_ALTERNATIVE_RECEIPT.json',failure)
            write_new(out/'C4_ALTERNATIVE_BUDGET.json',current_budget)
            return failure
        failure=dict(status='C4_ALTERNATIVE_AUTHORING_FAILURE',error_type=type(error).__name__,error=str(error),traceback=traceback.format_exc(),
            alternative_index=1,alternatives_remaining=0,budget=current_budget,elapsed_s=time.monotonic()-started,C1_C5='NOT_EXECUTED',READY=False)
        write_new(out/'AUTHORING_FAILURE.json',failure);write_new(out/'C4_ALTERNATIVE_BUDGET.json',current_budget)
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['prepare'])
    p.add_argument('--project-root',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--building',required=True);p.add_argument('--source-directory',type=Path,required=True)
    p.add_argument('--failed-checks',type=Path,required=True);p.add_argument('--budget-state',type=Path,required=True)
    p.add_argument('--layout-index',type=int,choices=range(20),required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--approval-record',type=Path,required=True)
    args=p.parse_args()
    if os.name=='posix':Path('/proc/self/oom_score_adj').write_text('1000')
    result=prepare(args)
    print(json.dumps(dict(status=result['status'],output=str(args.output),READY=False)))


if __name__=='__main__':main()
