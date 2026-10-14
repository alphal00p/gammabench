"""Derive direct-public full completion ratios only from a replayed final capture."""
from pathlib import Path
import hashlib
import json
import statistics
import sys

out = Path(sys.argv[1]).resolve()
read = lambda path: json.loads(Path(path).read_text())
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
meta = read(out / 'metadata.json')
audit = read(out / 'replay.json')
manifest = read(meta['manifest'])
assert audit['status'] == 'replayed' and audit['capture_metadata_sha256'] == sha(out / 'metadata.json')
assert meta['phase'] == 'final' and meta['performance_eligible']
assert manifest['output_contract'] == 'full-native-diagrams-local-fragments-v1'
assert not manifest.get('reused_profiles'), 'Previous full-object profiles cannot certify this endpoint'
tasks = {task['id']: task for task in manifest['tasks']}
native = {label: {} for label in tasks}
profiles = {label: {} for label in tasks}
for row in meta['child_statuses']:
    if row['status'] != 'completed':
        continue
    if row['tool'] == 'native':
        assert row['repeat'] not in native[row['task']]
        native[row['task']][row['repeat']] = row
    elif row['tool'] in ('callgrind', 'dhat'):
        assert row['tool'] not in profiles[row['task']]
        profiles[row['task']][row['tool']] = row['metrics']


def stats(values):
    assert values
    return dict(median=statistics.median(values), range=[min(values), max(values)], values=values)


def phases(result):
    marks = [0] + [result[key] for key in
                   ('setup_ns', 'full_diagrams_ns', 'observed_ns', 'destroyed_ns', 'elapsed_ns')]
    assert marks == sorted(marks)
    names = ('setup', 'public_full_generation', 'observer',
             'collection_destruction', 'after_destruction')
    return dict(zip(names, (end - start for start, end in zip(marks, marks[1:]))))


rows = {}
for label, task in tasks.items():
    reference = task.get('reference_task')
    records = native[label]
    cell = dict(case=task['case'], engine=task['engine'], variant=task['variant'], reference_task=reference,
                revision=task['revision'], correctness_gate=task['correctness_gate'],
                native_rounds=len(records), profiles=profiles[label],
                timing_boundary=task['timing_boundary'])
    if len(records) == manifest['native_rounds']:
        order = range(manifest['native_rounds'])
        wall = [records[i]['child_wall_ns'] / 1e9 for i in order]
        completion = [records[i]['result']['full_diagrams_ns'] / 1e9 for i in order]
        cell.update(process_wall_seconds=stats(wall), application_completion_seconds=stats(completion),
                    completion_above_100us=min(completion) >= .0001)
        partitions = [phases(records[i]['result']) for i in order]
        cell['phase_seconds'] = {key: stats([partition[key] / 1e9 for partition in partitions])
                                 for key in partitions[0]}
        cell['completion_phase_shares'] = {
            key: stats([partitions[i][key] / records[i]['result']['full_diagrams_ns']
                        for i in order])
            for key in ('setup', 'public_full_generation')}
        cell['process_after_completion_seconds'] = stats([
            (records[i]['child_wall_ns'] - records[i]['result']['full_diagrams_ns']) / 1e9
            for i in order])
    else:
        cell['timing_conclusion'] = 'Native rows incomplete/censored; no completed timing substituted.'
    if reference is None:
        cell['paired_comparison_conclusion'] = 'No matched parent reference; no variant ratio.'
    else:
        old = native[reference]
        if len(records) == len(old) == manifest['native_rounds']:
            order = range(manifest['native_rounds'])
            cell['paired_process_new_over_reference'] = stats([
                records[i]['child_wall_ns'] / old[i]['child_wall_ns'] for i in order])
            values = [records[i]['result']['full_diagrams_ns'] for i in order]
            old_values = [old[i]['result']['full_diagrams_ns'] for i in order]
            if min(values + old_values) >= 100_000:
                cell['paired_completion_new_over_reference'] = stats([
                    value / baseline for value, baseline in zip(values, old_values)])
            else:
                cell['completion_timing_conclusion'] = 'Ratio suppressed: sample below100microseconds.'
        else:
            cell['paired_comparison_conclusion'] = 'No paired comparison: native row incomplete/censored.'
        for tool in ('callgrind', 'dhat'):
            a, b = profiles[label].get(tool), profiles[reference].get(tool)
            if a is not None and b is not None:
                keys = ('whole_process_Ir',) if tool == 'callgrind' else ('allocated_bytes', 'allocated_blocks')
                cell[tool + '_new_over_reference'] = {key: a[key] / b[key] if b[key] else None for key in keys}
    rows[label] = cell
result = dict(status='derived_from_replayed_direct_full_capture', output_contract=manifest['output_contract'],
              run_id=meta['run_id'], rows=rows, unsupported=manifest['unsupported'],
              replay_sha256=sha(out / 'replay.json'), manifest_sha256=meta['manifest_sha256'],
              script_sha256=sha(__file__),
              scope='Application completion ends at the actual full GenerationResult with local fragments/projectors; aggregate is lazy and observer/drop separate. Ratios pair same case and round on a shared host, observed ranges are not confidence intervals. Whole-process/software profiles cover the verified direct-public driver process, including observation/destruction; no peak/RSS/total-memory ranking. Unsupported or missing/censored cells are never zero.')
path = out / 'paired-summary.json'
assert not path.exists()
path.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(dict(status=result['status'], rows=len(rows), output=str(path))))
