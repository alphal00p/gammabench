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
   cell[tool+'_new_over_reference']={key:(a[key]/value if value else None) for key,value in b.items()}
 rows[label]=cell
result=dict(status='derived_from_replayed_capture',run_id=meta['run_id'],rows=rows,
 replay_sha256=sha(out/'replay.json'),manifest_sha256=meta['manifest_sha256'],script_sha256=sha(__file__),
 scope='Ratios pair the same case and round. Whole-process wall is shared-host fresh-process task latency; library-body ratios are separately scoped and omitted below100microseconds. Ranges are observed extrema, not confidence intervals. Profiler values are whole-process dynamic instructions and heap metrics; no RSS or total-memory rankings. Missing/censored profiles are not zero.')
path=out/'paired-summary.json';assert not path.exists();path.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(dict(status=result['status'],rows=len(rows),output=str(path))))
