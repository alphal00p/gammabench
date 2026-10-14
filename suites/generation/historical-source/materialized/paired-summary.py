"""Derive per-round ratios only from an already replayed final capture."""
from pathlib import Path
import hashlib
import json
import statistics
import sys

out=Path(sys.argv[1]).resolve()
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
meta=read(out/'metadata.json');audit=read(out/'replay.json');manifest=read(meta['manifest'])
assert audit['status']=='replayed' and audit['capture_metadata_sha256']==sha(out/'metadata.json')
assert meta['phase']=='final' and meta['performance_eligible']
tasks={task['id']:task for task in manifest['tasks']}
references=manifest['reference_task_by_case']
native={label:{} for label in tasks};profiles={label:{} for label in tasks}
for row in meta['child_statuses']:
 if row['status']!='completed':continue
 if row['tool']=='native':
  assert row['repeat'] not in native[row['task']]
  native[row['task']][row['repeat']]=row
 elif row['tool'] in ('callgrind','dhat'):
  assert row['tool'] not in profiles[row['task']]
  profiles[row['task']][row['tool']]=row['metrics']

# Reuse only complete, replayed, exact-binary-and-request parent profiles.
import importlib.util
spec=importlib.util.spec_from_file_location('capture_helpers',out.parent/'capture.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
reused={}
for label,reference in manifest.get('reused_profiles',{}).items():
 assert label in tasks and not profiles[label]
 assert Path(reference['capture']).parent == out.parent, 'No count-only profile reuse'
 source=Path(reference['capture'])
 source_meta=read(source/'metadata.json');source_audit=read(source/'replay.json')
 source_terminal=read(reference['terminal'])
 assert source_meta['status'] in ('complete','complete_with_censored_rows')
 assert source_audit['status']=='replayed' and source_audit['capture_metadata_sha256']==sha(source/'metadata.json')
 assert source_terminal['exit_code']==0 and source_terminal['run_id']==source_meta['run_id']
 for path in [source/'metadata.json',source/'replay.json',Path(reference['terminal'])]:
  assert manifest['files'][str(path)]==sha(path)
 source_manifest=read(source_meta['manifest']);assert source_meta['manifest_sha256']==sha(source_meta['manifest'])
 assert source_manifest['schema']==2 and source_meta['phase']=='final' and source_meta['performance_eligible']
 assert source_manifest['tools']==manifest['tools']
 assert source_manifest['runtime']==manifest['runtime']
 assert source_meta['controller_sha256']==meta['controller_sha256']
 source_task=next(task for task in source_manifest['tasks'] if task['id']==reference['task'])
 task=tasks[label]
 for key in ['engine','case','command','expected','expected_observation_totals','profiler_scope']:
  assert source_task[key]==task[key],(label,key)
 assert c.cpu_set(source_task,source_manifest)==c.cpu_set(task,manifest)
 assert c.task_environment(source_task,source_manifest)==c.task_environment(task,manifest)
 assert set(source_task['inputs'])==set(task['inputs'])
 for name in task['inputs']:
  assert sha(source_task['inputs'][name])==sha(task['inputs'][name])
 assert source_manifest['files'][task['command'][0]]==manifest['files'][task['command'][0]]==sha(task['command'][0])
 provenance=[]
 for row in source_meta['child_statuses']:
  if row['task']!=reference['task'] or row['tool'] not in ('callgrind','dhat'):
   continue
  assert row['status']=='completed' and row['returncode']==0 and not row['timed_out']
  assert all(sha(source/name)==digest for name,digest in row['raw_sha256'].items())
  record=source/f"{row['task']}.{row['tool']}.{row['repeat']}"/'record.json'
  assert read(record)==row
  metrics=c.profile_metrics(row['tool'],record.parent,row,task['command'],source_manifest,source_task)
  assert metrics==row['metrics'] and row['tool'] not in profiles[label]
  profiles[label][row['tool']]=metrics
  provenance.append(dict(tool=row['tool'],record=str(record),record_sha256=sha(record),raw_sha256=row['raw_sha256']))
 assert set(profiles[label])=={'callgrind','dhat'}
 reused[label]=provenance

def stats(values):
 assert values
 return dict(median=statistics.median(values),range=[min(values),max(values)],values=values)

rows={}
for label,task in tasks.items():
 reference=task.get('reference_task',references[task['case']]);records=native[label];old=native[reference]
 cell=dict(case=task['case'],engine=task['engine'],reference_task=reference,
  revision=task.get('revision'),expected=task['expected'],correctness_gate=task['correctness_gate'],
  native_rounds=len(records),profiles=profiles[label],timing_boundary=task['timing_boundary'])
 if len(records)==len(old)==manifest['native_rounds']:
  order=range(manifest['native_rounds'])
  wall=[records[i]['child_wall_ns']/1e9 for i in order]
  ratios=[records[i]['child_wall_ns']/old[i]['child_wall_ns'] for i in order]
  cell.update(process_wall_seconds=stats(wall),paired_process_new_over_reference=stats(ratios))
  if all('elapsed_ns' in records[i]['result'] and 'elapsed_ns' in old[i]['result'] for i in order):
   body=[records[i]['result']['elapsed_ns']/1e9 for i in order]
   old_body=[old[i]['result']['elapsed_ns']/1e9 for i in order]
   cell['body_above_100us']=min(body+old_body)>=.0001
   if cell['body_above_100us']:
    cell.update(body_seconds=stats(body),paired_body_new_over_reference=stats([a/b for a,b in zip(body,old_body)]))
   else:
    cell['body_timing_conclusion']='Suppressed: at least one body sample below100microseconds.'
 else:
  cell['native_timing_conclusion']='No paired comparison: a native row is incomplete/censored.'
 for tool in ('callgrind','dhat'):
  a,b=profiles[label].get(tool),profiles[reference].get(tool)
  if a is not None and b is not None:
   cell[tool+'_new_over_reference']={key:(a[key]/b[key] if b[key] else None) for key in (('whole_process_Ir',) if tool=='callgrind' else ('allocated_bytes','allocated_blocks'))}
 rows[label]=cell
result=dict(status='derived_from_replayed_capture',run_id=meta['run_id'],rows=rows,reused_profile_provenance=reused,
 replay_sha256=sha(out/'replay.json'),manifest_sha256=meta['manifest_sha256'],script_sha256=sha(__file__),
 scope='Ratios pair the same case and round. Whole-process wall is shared-host fresh-process task latency; library-body ratios are separately scoped and omitted below100microseconds. Ranges are observed extrema, not confidence intervals. Profiler values are verified complete-process-tree dynamic instructions and cumulative allocation bytes/events; per-process heap peaks are not aggregated; no RSS or total-memory rankings. Missing/censored profiles are not zero.')
path=out/'paired-summary.json';assert not path.exists();path.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(dict(status=result['status'],rows=len(rows),output=str(path))))
