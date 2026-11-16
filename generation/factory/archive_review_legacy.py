"""Round4 remote-only archive review: existing CPU closure and real seven-image evidence."""
from pathlib import Path, PurePosixPath
import argparse, hashlib, io, json, os, time, zipfile
from PIL import Image, ImageOps, ImageDraw
from report_batch_legacy import load_cohort_plan, current_cohort, house_evidence, reference

_DIGEST_CACHE = {}

def digest(path):
    path=Path(path);before=path.stat();key=(str(path.resolve()),before.st_size,before.st_mtime_ns)
    cached=_DIGEST_CACHE.get(key)
    if cached and cached[0]==(before.st_dev,before.st_ino):return cached[1]
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024**2),b''):h.update(block)
    after=path.stat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('Source changed while hashing '+str(path))
    value=h.hexdigest();_DIGEST_CACHE[key]=((before.st_dev,before.st_ino),value);return value

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def verify_members(archive):
    """One decompressed read validates every member SHA, size and ZipExtFile CRC."""
    metadata={};images={};hashes={};source_hashes={};collision_rows=[]
    with zipfile.ZipFile(archive) as z:
        manifest=json.loads(z.read('DELIVERY_MANIFEST.json'))
        names=[r.filename for r in z.infolist()];rows=manifest['files'];declared=[r['archive_path'] for r in rows]
        if len(set(names))!=len(names) or len(set(declared))!=len(declared) or set(names)!=set(declared)|{'DELIVERY_MANIFEST.json'}:raise ValueError('Duplicate, missing or undeclared ZIP member')
        for row in rows:
            name=row['archive_path'];posix=PurePosixPath(name)
            if posix.is_absolute() or '..' in posix.parts or '\\' in name:raise ValueError('Unsafe ZIP member '+name)
            original_png=name.startswith('photos/') and name.endswith('.png') and len(posix.parts)==2
            keep=(name.endswith('.json') and not name.startswith('collision_package/')) or original_png
            blocks=[];h=hashlib.sha256();size=0
            with z.open(name) as stream:
                for block in iter(lambda:stream.read(4*1024**2),b''):
                    h.update(block);size+=len(block)
                    if keep:blocks.append(block)
            actual=h.hexdigest()
            if size!=row['bytes'] or actual!=row['sha256']:raise ValueError('ZIP member SHA/size differs '+name)
            hashes[name]=actual
            source=row.get('source_path')
            if source:
                p=Path(source)
                if not p.is_file() or p.stat().st_size!=size or digest(p)!=actual:raise ValueError('Current source bytes differ '+str(p))
                source_hashes[str(p.resolve())]=actual
            elif name!='receipts/ACTIVE_EVIDENCE_INDEX.json':raise ValueError('Unbound generated member '+name)
            if name.startswith('collision_package/'):collision_rows.append(row)
            if keep:
                data=b''.join(blocks)
                if name.endswith('.json'):metadata[name]=json.loads(data)
                elif original_png:
                    with Image.open(io.BytesIO(data)) as im:
                        im.load();images[Path(name).name]=(im.convert('RGB').copy(),dict(view=Path(name).name,size=list(im.size),format=im.format,sha256=actual))
    return manifest,metadata,images,hashes,source_hashes,collision_rows

def native_raw_binding(metadata,hashes,clean,checks):
    canonical=metadata.get('receipts/clean/c5/C5.json')
    if not canonical or canonical!=checks.get('C5') or canonical.get('status')!='PASS':raise ValueError('Canonical native C5 differs or is absent')
    raw=canonical.get('raw_query_or_capture_result') or {};path=Path(raw.get('path',''))
    if not path.resolve().is_relative_to((clean/'c5').resolve()) or path.name!='C5.json':raise ValueError('Raw C5 source escapes actual job')
    name='receipts/clean/'+path.resolve().relative_to(clean.resolve()).as_posix()
    if hashes.get(name)!=raw.get('sha256') or digest(path)!=raw.get('sha256'):raise ValueError('Raw C5 reference missing or SHA differs')
    doc=metadata[name]
    if doc.get('status')!='PASS' or doc.get('spawn_queries')!=canonical.get('spawn_queries'):raise ValueError('Raw native C5 query result differs')
    return dict(canonical_archive_path='receipts/clean/c5/C5.json',raw_archive_path=name,raw_source=raw)

def review_archive(project_root,house_directory,cohort_plan_path,archive,verified_archive_sha=None,output_directory=None):
    """Called in the pack process before canonical release, or by this remote CLI."""
    import resource
    started=time.monotonic();root=Path(project_root);house=Path(house_directory);archive=Path(archive)
    plan=load_cohort_plan(root,cohort_plan_path);bid=house.parent.parent.name;cohort=current_cohort(root,bid,plan)
    if cohort['kind']=='PRIOR_FROZEN_READY' or Path(cohort['directory']).resolve()!=house.resolve():raise ValueError('Review is outside current Round4 job')
    evidence=house_evidence(root,bid,house,cohort)
    if not evidence['READY_V4']:raise ValueError('Current house lacks actual READY gates/strictest R-tier binding')
    before=archive.stat();archive_sha=verified_archive_sha or digest(archive)
    manifest,metadata,images,hashes,sources,collision_rows=verify_members(archive)
    status=metadata['STATUS.json'];checks_doc=metadata['CHECKS.json'];checks=checks_doc.get('checks',checks_doc);e2e=metadata['E2E_RESULT.json'];photos=metadata['photos/PHOTOS.json']
    if manifest.get('building')!=bid or manifest.get('phase')!='round4' or manifest.get('source_directory')!=str(house):raise ValueError('Archive identity differs')
    if manifest.get('cohort_plan')!=plan['_reference'] or manifest.get('delivery')!=plan['delivery']:raise ValueError('Archive cohort/delivery differs')
    if status!=evidence['status'] or checks_doc!=evidence['checks'] or e2e!=evidence['e2e']:raise ValueError('Current canonical metadata differs from package')
    if not all(checks['C'+str(i)]['status']=='PASS' for i in range(1,6)):raise ValueError('A current scientific gate is not PASS')
    raw_checks=metadata['receipts/clean/checks/CHECKS.json'];raw_checks=raw_checks.get('checks',raw_checks)
    if any(raw_checks.get(key)!=checks[key] for key in ('C1','C2','C3','C4')):raise ValueError('Raw final C1-C4 differs from aggregate')
    if status['source_tool_shas']!=e2e['source_tool_shas'] or status['source_tool_shas']!=manifest['source_tool_shas']:raise ValueError('Tool-source identity differs')
    if status['config_sha256']!=e2e['config_sha256'] or status['config_sha256']!=manifest['config_sha256']:raise ValueError('Actual config differs')
    if e2e['elapsed_s']!=(e2e['monotonic_end_ns']-e2e['monotonic_start_ns'])/1e9 or not 0<=e2e['elapsed_s']<=3600:raise ValueError('Invalid single clean clock')
    if hashes.get('receipts/active/ROUND4_PLAN.json')!=plan['_reference']['sha256']:raise ValueError('Actual cohort plan missing from archive')
    clean=Path(status['clean_directory']).resolve()
    if not clean.is_relative_to(house.resolve()):raise ValueError('Final clean escapes current job')
    for path,expected in e2e['input_sha256'].items():
        if digest(path)!=expected or hashes.get('receipts/active/inputs/'+Path(path).name)!=expected:raise ValueError('Actual repair input differs '+path)
    if hashes.get('receipts/active/CONFIG.json')!=status['config_sha256']:raise ValueError('Active config not byte-bound')
    selection=status['tier_selection']
    if hashes.get('receipts/active/TIER_SELECTION.json')!=selection['sha256']:raise ValueError('Actual R-tier selection not byte-bound')
    cam=photos['automatic_camera_plan'];cam=cam['path'] if isinstance(cam,dict) else cam
    if digest(cam)!=photos['input_sha']['cameras'] or hashes.get('receipts/active/CAMERA_PLAN.json')!=photos['input_sha']['cameras']:raise ValueError('Actual final camera plan differs')
    if photos.get('camera_plan_input_sha256')!=e2e['input_sha256']:raise ValueError('Photos use another repair input')
    if digest(clean/'root.usda')!=photos['input_sha']['stage']:raise ValueError('Actual visual source differs')
    sdk=metadata['receipts/resource/SDK_RSS.json']
    if hashes['receipts/resource/SDK_RSS.json']!=status['SDK_peak_RSS']['sha256']:raise ValueError('SDK RSS evidence is not byte-bound')
    session_identity=checks['C5']['resident_session'];matched=[]
    for i,row in enumerate(sdk['instances']):
        raw=metadata[f'receipts/resource/SDK_EXIT_{i:02d}.json']
        if hashes[f'receipts/resource/SDK_EXIT_{i:02d}.json']!=row['sha256'] or raw.get('oom_score_adj_readback')!=1000 or not raw.get('no_cross_house_reuse') or raw.get('building')!=bid or not raw.get('wait4'):raise ValueError('SDK exit/RSS/priority evidence differs')
        if raw['pid']==session_identity['pid'] and str(raw['birth'])==str(session_identity['birth']):matched.append(raw)
    if len(matched)!=1 or matched[0]['actual_exit_code']!=0:raise ValueError('Successful house SDK did not exit0')
    for label in ['WORKER_OWNER','SUPERVISOR_OWNER']:
        if metadata['receipts/resource/'+label+'.json']['oom_score_adj_readback']!=1000:raise ValueError('Owned controller priority differs')
    if metadata['receipts/resource/PACK_PRIORITY.json']['oom_score_adj_readback']!=1000:raise ValueError('Pack priority differs')
    if any(p['oom_score_adj']!=1000 for p in metadata['receipts/resource/WORKER_PRIORITY.json']['processes']):raise ValueError('Owned process priority journal differs')
    raw_c5=native_raw_binding(metadata,hashes,clean,checks)
    spawns=metadata['receipts/clean/checks/SPAWN_POINTS.json']
    if hashes['receipts/clean/checks/SPAWN_POINTS.json']!=checks['C5']['input_sha']['spawns']:raise ValueError('Actual native spawn input differs')
    expected_queries=[dict(kind='floor',floor_index=row['floor_index'],index=i,point_xyz_m=point) for row in spawns['floors'] for i,point in enumerate(row['points_xyz_m'])]
    expected_queries += [dict(kind='stair_platform',stair_id=row['stair_id'],point_xyz_m=row['point_xyz_m']) for row in spawns['stair_platforms']]
    actual_queries=checks['C5']['spawn_queries']
    if len(expected_queries)!=len(actual_queries) or any(any(actual.get(k)!=value for k,value in expected.items()) for actual,expected in zip(actual_queries,expected_queries)):raise ValueError('Actual native query coverage differs from its source input')
    if digest(clean/'collision_package/collision_only.usdc')!=checks['C5']['input_sha']['stage']:raise ValueError('Actual C5 collision source differs')
    raw_photos=photos['raw_query_or_capture_result'];raw_photo_name='photos/'+Path(raw_photos['path']).resolve().relative_to((clean/'photos').resolve()).as_posix()
    if hashes.get(raw_photo_name)!=raw_photos['sha256']:raise ValueError('Raw capture receipt missing or changed')
    if photos['resident_session']['pid']!=checks['C5']['resident_session']['pid'] or str(photos['resident_session']['birth'])!=str(checks['C5']['resident_session']['birth']):raise ValueError('C5 and photos do not share actual resident SDK')
    names=photos.get('image_files',[])
    if photos.get('status')!='PASS' or len(names)!=7 or len(set(names))!=7 or set(names)!=set(images) or len({row[1]['sha256'] for row in images.values()})!=7 or not {'stair_01.png','collision_gray.png'}<=set(names):raise ValueError('Seven real distinct original views missing')
    package=(clean/'collision_package').resolve()
    for row in collision_rows:
        path=(package/row['archive_path'][len('collision_package/'):]).resolve()
        if not path.is_relative_to(package) or sources.get(str(path))!=row['sha256']:raise ValueError('Collision member is not the actual package source')
    from pxr import Sdf,Usd,UsdPhysics,UsdUtils
    layers,resources,missing=UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(package/'collision_only.usdc')))
    paths=[Path(l.realPath).resolve() for l in layers if not l.anonymous]+[Path(str(p)).resolve() for p in resources]
    if missing or not all(p.is_file() and p.is_relative_to(package) for p in paths):raise ValueError('Portable package dependency closure failed')
    stage=Usd.Stage.Open(str(package/'collision_only.usdc'))
    if not stage:raise ValueError('Actual package cannot be opened on CPU')
    enabled=sum(p.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False for p in Usd.PrimRange(stage.GetPseudoRoot(),Usd.TraverseInstanceProxies()))
    collision=metadata['receipts/clean/COLLISION_MANIFEST.json'];inventory=[r for key in ('instances','slabs','stairs') for r in collision.get(key,[])]
    inv_actual=sum(r['enabled_colliders'] for r in inventory);inv_expected=sum(r['expected_colliders'] for r in inventory)
    if enabled!=collision['actual_enabled_colliders'] or enabled!=checks['C5']['actual_enabled_colliders'] or inv_actual!=checks['C1']['actual_colliders'] or inv_expected!=checks['C1']['expected_colliders']:raise ValueError('Actual collider count/scope differs')
    after=archive.stat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('Archive changed during review')
    result=dict(schema='Factory80Round4RemoteArchiveReviewV7',status='PASS',phase='round4',building=bid,cohort_plan=plan['_reference'],actual_house_directory=str(house),actual_clean_directory=str(clean),zip_path=str(archive),zip_sha256=archive_sha,zip_bytes=after.st_size,
        manifest_files_verified=len(hashes),CRC='PASS_ALL_MEMBERS_STREAMED_TO_EOF',all_current_source_member_SHA_verified=True,raw_C5=raw_c5,raw_PHOTOS=raw_photos,active_camera_plan=dict(path=cam,sha256=photos['input_sha']['cameras']),
        C1_C5={key:checks[key]['status'] for key in ('C1','C2','C3','C4','C5')},clean_E2E_elapsed_s=e2e['elapsed_s'],source_metadata={name:hashes[name] for name in ('CHECKS.json','STATUS.json','E2E_RESULT.json')},
        count_definitions=dict(C1_inventory_actual=inv_actual,C1_inventory_expected=inv_expected,manifest_C5_CPU_enabled_total=enabled,native_shapes_including_probe=checks['C5'].get('actual_native_shapes_including_probe'),total_minus_inventory=enabled-inv_actual),
        collision_dependency_closure=dict(layers=len(layers),resources=len(resources),missing=[],all_within_package=True,collision_files_SHA_matched=len(collision_rows)),decoded_images=[images[name][1] for name in names],
        source_tool_shas=status['source_tool_shas'],config_sha256=status['config_sha256'],reviewer_sha256=digest(Path(__file__)),scope='REMOTE_CPU_EXISTING_PACKAGE_AND_ORIGINAL_IMAGES_ONLY; no new science/SDK/GPU or Windows download',appearance_scoring='NOT_EXECUTED',
        elapsed_s=time.monotonic()-started,peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024)
    if output_directory:
        out=Path(output_directory);out.mkdir(parents=True,exist_ok=True);sheet=Image.new('RGB',(1000,1220),(244,244,244));draw=ImageDraw.Draw(sheet)
        for i,name in enumerate(names):
            tile=ImageOps.contain(images[name][0],(500,280));x=(i%2)*500;y=(i//2)*305;sheet.paste(tile,(x+(500-tile.width)//2,y));draw.text((x+10,y+283),name,fill='black')
        contact=out/(bid+'_7views.jpg');sheet.save(contact,quality=92)
        with Image.open(contact) as im:im.load()
        result['contact_sheet']=str(contact);result['contact_sheet_replaces_originals']=False
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project-root',type=Path,default=Path('/cephfs/shared/yixing/outputs/factory80_20261011'));ap.add_argument('--building');ap.add_argument('--cohort-plan',type=Path,required=True);ap.add_argument('--house-directory',type=Path,required=True)
    ap.add_argument('--receipt',type=Path);ap.add_argument('--output-directory',type=Path);ap.add_argument('--cpu-core',type=int,default=63)
    args=ap.parse_args();house=args.house_directory;receipt=read(args.receipt or house/'delivery/DELIVERY_RECEIPT.json')
    if receipt.get('schema') not in ('Factory80Round4PackedDeliveryV7','Factory80Round4RemoteDeliveryV7') or receipt.get('status') not in ('PACKED','SUCCESS') or receipt.get('phase')!='round4':raise ValueError('Actual remote Round4 receipt required')
    if args.building and args.building!=receipt['building']:raise ValueError('Building differs from receipt')
    if receipt['source_directory']!=str(house) or receipt['cohort_plan']!=reference(args.cohort_plan):raise ValueError('Receipt source/cohort differs')
    if hasattr(os,'sched_setaffinity'):os.sched_setaffinity(0,{args.cpu_core})
    if Path('/proc/self/oom_score_adj').is_file():Path('/proc/self/oom_score_adj').write_text('1000')
    out=args.output_directory or house/'delivery/review'
    result=review_archive(args.project_root,house,args.cohort_plan,receipt['zip_path'],output_directory=out)
    if result['zip_sha256']!=receipt['zip_sha256'] or result['source_metadata']!=receipt['source_metadata']:raise ValueError('Actual archive differs from delivery receipt')
    path=out/'REVIEW.json';path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result))

if __name__=='__main__':main()
