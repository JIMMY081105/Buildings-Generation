"""Create only task-owned links to actually present legacy object trees."""
import concurrent.futures,json,pathlib
WORK=pathlib.Path('/cephfs/shared/yixing/outputs/factory80_20261011/work/scene_expansion')
def one(row):
 try:
  expected=pathlib.Path(row['expected_usda'])
  if not expected.is_file() or expected.stat().st_size<=0:raise RuntimeError('legacy object became absent/empty')
  link=WORK/'objects'/row['source']/row['directory'];link.parent.mkdir(parents=True,exist_ok=True)
  if not link.exists() and not link.is_symlink():link.symlink_to(expected.parent,target_is_directory=True)
  actual=link/(row['directory']+'.usda')
  if not actual.is_file() or actual.stat().st_size<=0:raise RuntimeError('task object link unreadable')
  return {'source':row['source'],'directory':row['directory'],'status':'PASS','task_usda':str(actual),'legacy_usda':str(expected)}
 except Exception as error:return {'source':row['source'],'directory':row['directory'],'status':'FAIL','error':f'{type(error).__name__}: {error}'}
rows=[r for r in json.load(open(WORK/'current_pool_dependency_audit.json'))['rows'] if r['present']];result=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 for row in pool.map(one,rows):
  result.append(row)
  if len(result)%100==0 or len(result)==len(rows):print(json.dumps({'linked':len(result),'expected':len(rows),'passed':sum(r['status']=='PASS' for r in result)}),flush=True)
(WORK/'legacy_object_links_verified.json').write_text(json.dumps({'count':len(rows),'passed':sum(r['status']=='PASS' for r in result),'records':result},indent=2)+'\n')
