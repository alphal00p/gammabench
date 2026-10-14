"""Replay a completed comparison. Requires the actual owning terminal exit record."""
from pathlib import Path
from collections import defaultdict
import importlib.util
import json
import math
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('capture', ROOT/'capture.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def replay(out, terminal):
    meta = c.read(out/'metadata.json')
    actual = c.read(terminal)
    assert actual['exit_code'] == 0 and actual['run_id'] == meta['run_id']
    assert meta['status'] in ('complete', 'complete_with_censored_rows') and meta['inputs_unchanged']
    assert meta['performance_eligible'] == (meta['phase'] not in ('validation','smoke'))
    manifest_path = Path(meta['manifest'])
    manifest = c.read(manifest_path)
    assert meta['manifest_sha256'] == c.sha(manifest_path)
    assert meta['controller_sha256'] == c.sha(ROOT/'capture.py')
    assert meta['profile_parser_sha256'] == c.sha(c.PROFILE_PARSER)
    assert all(c.sha(path) == digest for path,digest in manifest['files'].items())
    tasks = {task['id']:task for task in manifest['tasks']}
    queue = c.order(manifest, meta['phase'])
    assert meta['queue'] == [list(row) for row in queue]
    rows = meta['child_statuses']
    assert len(rows) == len(queue)
    seen_raw = set()
    finished = 0
    profile_finished = defaultdict(int)
    timed_out = set()
    results = defaultdict(list)
    for row, (label,tool,repeat) in zip(rows,queue):
        assert (row['task'],row['tool'],row['repeat']) == (label,tool,repeat)
        task = tasks[label]
        if row['status'] == 'skipped_after_timeout':
            assert (label,tool) in timed_out
            continue
        c.guard_workers(row['visible_workers_before'],manifest,validation=meta['phase']=='validation')
        c.guard_workers(row['visible_workers_after'],manifest,validation=meta['phase']=='validation')
        directory = out/f'{label}.{tool}.{repeat}'
        assert c.read(directory/'record.json') == row
        assert row['cwd'] == str(directory/'work')
        command = [part.replace('{workdir}',row['cwd']) for part in task['command']]
        layout = c.profile_layout(directory,tool) if tool in ('callgrind','dhat') else None
        launch = c.profiled_command(command,tool,directory,manifest) if layout else command
        assert row['cpus'] == c.cpu_set(task,manifest,tool)
        assert row['task_environment'] == c.task_environment(task,manifest)
        assert row['command'] == [manifest['tools']['taskset'],'-c',','.join(map(str,row['cpus'])),*launch]
        assert row['profile_layout'] == layout
        assert row['pid'] > 0 and row['started_ns'] <= row['finished_ns']
        assert row['child_wall_ns'] > 0 and row['max_rss_kib'] > 0
        if tool in ('callgrind','dhat'):
            assert row['started_ns'] >= finished
            if tool == 'dhat':
                assert row['started_ns'] >= profile_finished['callgrind']
            profile_finished[tool] = max(profile_finished[tool],row['finished_ns'])
        else:
            assert row['started_ns'] >= finished
            finished = row['finished_ns']
        expected_raw = {str(p.relative_to(out)) for p in directory.rglob('*')
                        if p.is_file() and p.name != 'record.json'}
        assert set(row['raw_sha256']) == expected_raw
        assert not seen_raw.intersection(expected_raw)
        seen_raw.update(expected_raw)
        assert all(c.sha(out/path) == digest for path,digest in row['raw_sha256'].items())
        for name,source in task.get('inputs',{}).items():
            assert c.sha(directory/'work'/name) == manifest['files'][source]
        if row['status'] == 'censored_timeout':
            assert row['timed_out'] and row['returncode'] != 0
            assert row['child_wall_ns']/1e9 >= row['timeout_seconds']
            timed_out.add((label,tool))
            results[label].append(row)
            continue
        assert row['status'] == 'completed' and not row['timed_out'] and row['returncode'] == 0
        stdout = directory/('parser.stdout' if task.get('parse_command') else 'stdout')
        if task.get('parse_command'):
            paths = {key:str(directory/name) for key,name in [('workdir','work'),('stdout','stdout'),('stderr','stderr'),('parser_stdout','parser.stdout'),('parser_stderr','parser.stderr')]}
            assert row['parser'] == dict(command=[part.format_map(paths) for part in task['parse_command']], returncode=0)
        matches = re.findall(r'^RESULT (.*)$',stdout.read_text(),re.M)
        assert len(matches) == 1
        result = json.loads(matches[0])
        assert result == row['result'] and result['case'] == task['case'] and result['engine'] == task['engine']
        assert all(result[key] == value for key,value in task.get('expected',{}).items())
        assert all(result['observation'][key] == value for key,value in task.get('expected_observation_totals',{}).items())
        c.validate_full_native_result(task,result,row)
        assert row['processes'] == c.validate_process_manifest(result,row['pid'],command,manifest,task)
        if tool in ('callgrind', 'dhat'):
            assert row['metrics'] == c.profile_metrics(tool,directory,row,command,manifest,task)
        results[label].append(row)
    profiled = {label for label,task in tasks.items() if task.get('profile')}
    if meta['phase'] == 'final':
        assert len(profiled) == manifest['profile_parallelism'] == 4
        assert len(tasks) == manifest['supported_task_count'] == 24
        assert manifest['matrix_variants'] == ['baseline', 'candidate']
        assert len(meta['profile_batches']) == 2
        for batch,tool in zip(meta['profile_batches'],('callgrind','dhat')):
            assert batch['tool'] == tool and not batch['failures']
            assert batch['tasks'] == [label for label,kind,_ in queue if kind == tool]
            members = [row for row in rows if row['tool'] == tool]
            assert {row['task'] for row in members} == profiled
            assert all(batch['started_ns'] <= row['started_ns'] <= row['finished_ns'] <= batch['finished_ns'] for row in members)
        assert meta['profile_batches'][0]['finished_ns'] <= meta['profile_batches'][1]['started_ns']
        cores = []
        for label in profiled:
            cpu = c.cpu_set(tasks[label],manifest,'callgrind')[0]
            topology = manifest['profile_cpu_topology'][str(cpu)]
            cores.append((topology['package'],topology['core']))
        assert len(set(cores)) == 4
        assert {tuple(c.cpu_set(tasks[label],manifest,'callgrind')) for label in profiled} == {(100,),(101,),(102,),(103,)}
        assert all(c.cpu_set(task,manifest) == [100] for task in tasks.values())
    summary = {}
    for label,children in results.items():
        native = [row for row in children if row['tool'] == 'native' and row['status'] == 'completed']
        cell = dict(engine=tasks[label]['engine'],case=tasks[label]['case'],task=label,
            boundary=tasks[label]['timing_boundary'],results_gate=tasks[label].get('correctness_gate'),
            native_rounds=len(native),censored=[dict(tool=row['tool'],timeout_seconds=row['timeout_seconds']) for row in children if row['status']=='censored_timeout'])
        if native:
            wall = [row['child_wall_ns']/1e9 for row in native]
            cell.update(process_wall_seconds=wall,process_wall_median=statistics.median(wall),
                process_wall_range=[min(wall),max(wall)],native_peak_rss_kib=[row['max_rss_kib'] for row in native])
            inner = [row['result']['full_diagrams_ns']/1e9 for row in native if 'full_diagrams_ns' in row['result']]
            if inner:
                assert len(inner) == len(native)
                cell.update(completion_seconds=inner,completion_median=statistics.median(inner),
                    completion_range=[min(inner),max(inner)],completion_above_100us=all(value>=.0001 for value in inner))
        for tool in ('callgrind','dhat'):
            profiles = [row for row in children if row['tool']==tool and row['status']=='completed']
            assert len(profiles)<=1
            cell[tool] = profiles[0]['metrics'] if profiles else None
        summary[label] = cell
    report = dict(status='replayed',performance_eligible=meta['performance_eligible'],phase=meta['phase'],run_id=meta['run_id'],manifest_sha256=meta['manifest_sha256'],
        capture_metadata_sha256=c.sha(out/'metadata.json'),actual_terminal_sha256=c.sha(terminal),
        auditor_sha256=c.sha(__file__),child_records=len(rows),raw_files=len(seen_raw),tasks=summary,
        scope='Shared-host pinned paired wall times with external profiler and compiler context recorded; our own build roots remain excluded, without an exclusive-core claim. Whole-process wall covers fresh request/model setup and direct public Process.generate_diagrams, retaining full FeynmanDiagram local fragments, automatic projectors, factors/signs/routing/validation/groups, followed by observation and destruction. Application full_diagrams_ns ends at the retained full GenerationResult; observation/drop are separate. No common records, extra input normalization or substitute lightweight output. Aggregate remains lazy through performance/profile/observer/destruction; official14field serializer only runs in correctness qualification. Instruction and total-allocation metrics sum the verified complete process tree; individual peak heap metrics are never summed. Native wait4 RSS is launcher-inclusive and is not used for engine-memory rankings. DHAT counts dynamic heap only; process peaks remain separate. Profiler elapsed time is not application timing. Censored rows are not completed runtime measurements. Semantic equality gates remain required for any cross-engine ranking.')
    destination=out/'replay.json'
    assert not destination.exists()
    c.write(destination,report)
    print(json.dumps(dict(status=report['status'],run_id=report['run_id'],tasks=len(summary),raw_files=len(seen_raw))))


if __name__=='__main__':
    replay(Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve())
