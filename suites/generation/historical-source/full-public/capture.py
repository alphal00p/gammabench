"""Direct public full-diagram capture; local fragments/projectors included, aggregate lazy."""
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent
LOCK = ROOT / 'capture.lock'
PROFILE_PARSER = Path('/tmp/feynkit-four-loop-comparison-20261011/audit-measurements.py')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def active_workers():
    found = []
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():
            continue
        try:
            executable = os.readlink(path / 'exe')
            name = Path(executable).name
            profiler = name.startswith(('callgrind-', 'dhat-', 'cachegrind-', 'massif-', 'memcheck-', 'lackey-', 'helgrind-')) or name in ('valgrind', 'perf', 'heaptrack')
            compiler = name in ('cargo', 'rustc', 'rust-lld', 'ld.lld', 'cc1', 'cc1plus', 'f951', 'gcc', 'g++', 'clang', 'clang++', 'gfortran', 'as', 'ld')
            if compiler or profiler:
                pid = int(path.name)
                found.append(dict(pid=pid, executable=executable, name=name,
                    cwd=os.readlink(path / 'cwd'), allowed_cpus=sorted(os.sched_getaffinity(pid)),
                    kind='profiler' if profiler else 'compiler'))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
    return found


def guard_workers(workers, manifest, validation=False):
    if manifest.get('host_policy') == 'reported_external_workers':
        # These are process-local software profilers, not shared hardware-event
        # counters. Preserve their activity as shared-host context; never assert
        # that an affinity mask or last processor reserves a core for our run.
        roots = [Path(path).resolve() for path in manifest['forbidden_build_roots']]
        assert roots
        if validation and manifest.get('qualification_allows_compilers'):
            return
        assert not any(row['kind'] == 'compiler' and
                       Path(row['cwd']).resolve().is_relative_to(root)
                       for row in workers for root in roots), workers
        return
    assert not any(row['kind'] == 'profiler' for row in workers), workers
    if validation:
        return
    if manifest.get('host_policy') != 'reported_external_compilers':
        assert not workers, workers
        return
    roots = [Path(path).resolve() for path in manifest['forbidden_build_roots']]
    assert roots
    assert not any(Path(row['cwd']).resolve().is_relative_to(root)
                   for row in workers for root in roots), workers


def cpu_set(task, manifest, tool=None):
    cpus = task['profile_cpus'] if tool in ('callgrind','dhat') else task.get('cpus', [manifest['cpu']])
    assert cpus and cpus == sorted(set(cpus)) and all(isinstance(cpu, int) for cpu in cpus)
    assert set(cpus).issubset(os.sched_getaffinity(0))
    return cpus


def task_environment(task, manifest):
    overrides = task.get('environment', {})
    assert set(overrides).issubset({'BENCH_THREADS', 'RAYON_NUM_THREADS', 'BENCH_EXTERNAL_CONFIG', 'BENCH_FULL_MODEL_FILE'})
    environment = {**manifest['environment'], **overrides}
    assert int(environment.get('BENCH_THREADS', '1')) <= len(cpu_set(task, manifest))
    return environment


def order(manifest, phase):
    tasks = manifest['tasks']
    if phase == 'smoke':
        return [(task['id'], tool, 0) for tool in ('native', 'callgrind', 'dhat') for task in tasks]
    if phase in ('pilot', 'validation'):
        tool = 'validation' if phase == 'validation' else 'native'
        return [(task['id'], tool, 0) for task in tasks]
    groups = defaultdict(list)
    for task in tasks:
        groups[task['case']].append(task['id'])
    queue = []
    for case, labels in groups.items():
        for warmup in range(manifest['warmups_per_task']):
            queue.extend((label, 'warmup', warmup) for label in labels)
        for repeat in range(manifest['native_rounds']):
            rotated = labels[repeat % len(labels):] + labels[:repeat % len(labels)]
            if (repeat // len(labels)) % 2:
                rotated.reverse()
            queue.extend((label, 'native', repeat) for label in rotated)
    for tool in ('callgrind', 'dhat'):
        queue.extend((task['id'], tool, 0) for task in tasks if task.get('profile', False))
    return queue


def wait_child(child, timeout):
    """A single blocking wait4 also records this child's RSS, including static data."""
    completed = threading.Event()
    result = {}
    def waiter():
        _, status, usage = os.wait4(child.pid, 0)
        finished = time.perf_counter_ns()
        finished_ns = time.time_ns()
        result.update(wait_finished_perf_ns=finished, finished_ns=finished_ns, returncode=os.waitstatus_to_exitcode(status), max_rss_kib=usage.ru_maxrss,
                      user_seconds=usage.ru_utime, system_seconds=usage.ru_stime)
        completed.set()
    worker = threading.Thread(target=waiter, daemon=True)
    worker.start()
    timed_out = not completed.wait(timeout)
    if timed_out:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if not completed.wait(2):
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    worker.join()
    child.returncode = result['returncode']
    return dict(result, timed_out=timed_out)


def parse_result(task, paths, row):
    if task.get('parse_command'):
        substitutions = {key: str(value) for key, value in paths.items()}
        command = [part.format_map(substitutions) for part in task['parse_command']]
        parsed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        paths['parser_stdout'].write_text(parsed.stdout)
        paths['parser_stderr'].write_text(parsed.stderr)
        row['parser'] = dict(command=command, returncode=parsed.returncode)
        assert parsed.returncode == 0, row['parser']
        text = parsed.stdout
    else:
        text = paths['stdout'].read_text()
    matches = re.findall(r'^RESULT (.*)$', text, re.M)
    assert len(matches) == 1, 'Require exactly one RESULT JSON line'
    result = json.loads(matches[0])
    assert result['engine'] == task['engine'] and result['case'] == task['case']
    for key, expected in task.get('expected', {}).items():
        assert result[key] == expected, (task['id'], key, result[key], expected)
    for key, expected in task.get('expected_observation_totals', {}).items():
        assert key in ('vertices', 'edges', 'legs') and result['observation'][key] == expected
    validate_full_native_result(task, result, row)
    return result


def validate_full_native_result(task, result, row):
    """Actual public full-result endpoint; no old normalized-input assumptions."""
    assert result['output_contract'] == 'full-native-diagrams-local-fragments-v1'
    assert result['mode'] in ('full-native', 'full-native-dump')
    count = result['full_diagram_count']
    assert count == result['accepted_count'] == result['emitted_count'] == result['group_count']
    report = result['generation_report']
    assert report['completed'] and report['retained_count'] == count and report['zero_numerator_count'] == 0
    observation = result['full_diagram_observation']
    assert observation['schema'] == 'stable-structure-routing-local-fragments-v1'
    assert observation['symbolic_fields_observed'] == 3 * count + observation['vertices'] + observation['edges']
    atoms = result['process_local_atom_observation']
    assert atoms['scope'] == 'process-local Symbolica local-fragment representation; aggregate numerator excluded; not a cross-process semantic fingerprint'
    for field in ('vertices', 'edges', 'half_edges'):
        assert isinstance(observation[field], int) and observation[field] >= 0
        assert atoms[field] == observation[field]
    for witness in (observation, atoms):
        for field in ('checksum_sum', 'checksum_xor'):
            assert re.fullmatch(r'[0-9a-f]{16}', witness[field])
    if 'expected_full_diagram_observation' in task:
        assert observation == task['expected_full_diagram_observation']
    checkpoints = [result[field] for field in ('setup_ns', 'full_diagrams_ns', 'observed_ns', 'destroyed_ns', 'elapsed_ns')]
    assert all(isinstance(value, int) and value > 0 for value in checkpoints)
    assert checkpoints == sorted(checkpoints) and checkpoints[-1] <= row['child_wall_ns']


def single_process_metrics(tool, path, stderr, command):
    spec = importlib.util.spec_from_file_location('accepted_profile_parser', PROFILE_PARSER)
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    if tool == 'callgrind':
        return parser.instruction_total(path, stderr)
    data = read(path)
    assert data['dhatFileVersion'] == 2 and data['mode'] == 'heap'
    assert data['cmd'] == ' '.join(command)
    metrics = {}
    for label, fields, prefix in [('Total', ('tb', 'tbk'), 'allocated'),
                                  ('At t-gmax', ('gb', 'gbk'), 'peak_live'),
                                  ('At t-end', ('eb', 'ebk'), 'end_live')]:
        matches = re.findall(re.escape(label) + r':\s+([\d,]+) bytes in ([\d,]+) blocks', stderr)
        assert len(matches) == 1
        values = [sum(point[field] for point in data['pps']) for field in fields]
        assert values == [int(value.replace(',', '')) for value in matches[0]]
        metrics[prefix + '_bytes'], metrics[prefix + '_blocks'] = values
    for label, field in [('Reads', 'rb'), ('Writes', 'wb')]:
        matches = re.findall(re.escape(label) + r':\s+([\d,]+) bytes', stderr)
        assert len(matches) == 1
        assert int(matches[0].replace(',', '')) == sum(point[field] for point in data['pps'])
    return metrics


def profile_layout(directory, tool):
    return dict(profile=str(directory / ('callgrind.%p.out' if tool == 'callgrind' else 'dhat.%p.json')),
                log=str(directory / 'valgrind.%p.log'))


def profiled_command(command, tool, directory, manifest):
    layout = profile_layout(directory, tool)
    return [manifest['tools']['valgrind'], f'--tool={tool}', '--trace-children=yes',
            f'--log-file={layout["log"]}', f'--{tool}-out-file={layout["profile"]}',
            *(['--num-callers=40'] if tool == 'dhat' else []), *command]


def validate_process_manifest(result, driver_pid, command, manifest, task):
    assert result['driver_pid'] == driver_pid, (result['driver_pid'], driver_pid)
    children = result['process_manifest']
    assert isinstance(children, list)
    processes = [dict(pid=driver_pid, parent_pid=None, command=command, exit_code=0, success=True)]
    seen = {driver_pid}
    for child in children:
        assert set(child) == {'pid', 'parent_pid', 'command', 'exit_code', 'success'}, child
        assert isinstance(child['pid'], int) and child['pid'] > 0 and child['pid'] not in seen
        assert child['parent_pid'] == driver_pid and child['exit_code'] == 0 and child['success'] is True
        assert child['command'] and isinstance(child['command'], list)
        assert child['command'][0] in manifest['files'], child
        assert sha(child['command'][0]) == manifest['files'][child['command'][0]]
        seen.add(child['pid'])
        processes.append(child)
    if 'expected_child_commands' in task:
        assert [child['command'] for child in children] == task['expected_child_commands']
    else:
        assert not children, 'External tasks must declare every expected child command'
    return processes


def profile_metrics(tool, directory, row, command, manifest, task):
    processes = validate_process_manifest(row['result'], row['pid'], command, manifest, task)
    layout = profile_layout(directory, tool)
    profiles = set(directory.glob('callgrind.*.out' if tool == 'callgrind' else 'dhat.*.json'))
    logs = set(directory.glob('valgrind.*.log'))
    expected_profiles = {Path(layout['profile'].replace('%p', str(p['pid']))) for p in processes}
    expected_logs = {Path(layout['log'].replace('%p', str(p['pid']))) for p in processes}
    assert profiles == expected_profiles, (profiles, expected_profiles)
    assert logs == expected_logs, (logs, expected_logs)
    metrics = []
    for process in processes:
        pid = process['pid']
        path = Path(layout['profile'].replace('%p', str(pid)))
        log = Path(layout['log'].replace('%p', str(pid)))
        stderr = log.read_text()
        assert re.findall(r'^==' + str(pid) + r'== Command: (.*)$', stderr, re.M) == [' '.join(process['command'])], str(log)
        assert not re.search(r'Process terminating|fatal signal|SIG(SEGV|ABRT|KILL)', stderr, re.I), str(log)
        if tool == 'callgrind':
            source = path.read_text()
            assert re.findall(r'^pid: (\d+)$', source, re.M) == [str(pid)]
            assert [value.strip() for value in re.findall(r'^cmd: (.*)$', source, re.M)] == [' '.join(process['command'])]
        value = single_process_metrics(tool, path, stderr, process['command'])
        metrics.append(dict(pid=pid, command=process['command'], metrics=value))
    aggregate = dict(scope='complete_process_tree', process_count=len(processes), processes=metrics)
    if tool == 'callgrind':
        aggregate['whole_process_Ir'] = sum(p['metrics']['whole_process_Ir'] for p in metrics)
    else:
        for field in ('allocated_bytes', 'allocated_blocks'):
            aggregate[field] = sum(p['metrics'][field] for p in metrics)
        aggregate['peak_scope'] = 'Per-process peaks only; no simultaneous process-tree peak is inferred'
    return aggregate


def run(task, tool, repeat, out, manifest, lock):
    workers = active_workers()
    guard_workers(workers, manifest, validation=tool == 'validation')
    directory = out / f"{task['id']}.{tool}.{repeat}"
    directory.mkdir()
    work = directory / 'work'
    work.mkdir()
    for name, source in task.get('inputs', {}).items():
        destination = work / name
        assert destination.resolve().is_relative_to(work.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    paths = dict(workdir=work, stdout=directory/'stdout', stderr=directory/'stderr',
                 parser_stdout=directory/'parser.stdout', parser_stderr=directory/'parser.stderr')
    command = [part.replace('{workdir}', str(work)) for part in task['command']]
    assert sha(command[0]) == manifest['files'][command[0]]
    layout = profile_layout(directory, tool) if tool in ('callgrind', 'dhat') else None
    launch = profiled_command(command, tool, directory, manifest) if layout else command
    cpus = cpu_set(task, manifest, tool)
    launch = [manifest['tools']['taskset'], '-c', ','.join(map(str, cpus)), *launch]
    env = os.environ.copy()
    env.update(task_environment(task, manifest))
    timeout = manifest['timeouts_seconds']['profile' if tool in ('callgrind', 'dhat') else 'native']
    row = dict(task=task['id'], engine=task['engine'], case=task['case'], tool=tool, repeat=repeat, cpus=cpus, task_environment=task_environment(task, manifest), visible_workers_before=workers, loadavg_before=Path('/proc/loadavg').read_text().strip(),
               command=launch, cwd=str(work), timeout_seconds=timeout, profile_layout=layout)
    write(directory/'record.json', row)
    with paths['stdout'].open('x') as stdout, paths['stderr'].open('x') as stderr:
        row['started_ns'] = time.time_ns()
        start = time.perf_counter_ns()
        child = subprocess.Popen(launch, cwd=work, env=env, stdout=stdout, stderr=stderr,
                                 start_new_session=True, pass_fds=(lock.fileno(),))
        row['pid'] = child.pid
        row.update(wait_child(child, timeout))
        row['child_wall_ns'] = row.pop('wait_finished_perf_ns') - start
    row['visible_workers_after'] = active_workers()
    row['loadavg_after'] = Path('/proc/loadavg').read_text().strip()
    secret = env.get('SYMBOLICA_LICENSE', '')
    if secret:
        for path in (paths['stdout'], paths['stderr']):
            text = path.read_text()
            if secret in text:
                path.write_text(text.replace(secret, '<redacted>'))
    row['status'] = 'censored_timeout' if row['timed_out'] else ('completed' if row['returncode'] == 0 else 'failed_exit')
    write(directory/'record.json', row)
    if row['status'] == 'completed':
        try:
            guard_workers(row['visible_workers_after'], manifest, validation=tool == 'validation')
            row['result'] = parse_result(task, paths, row)
            row['processes'] = validate_process_manifest(row['result'], row['pid'], command, manifest, task)
            if tool in ('callgrind', 'dhat'):
                row['metrics'] = profile_metrics(tool, directory, row, command, manifest, task)
        except Exception as error:
            row['status'] = 'invalid_result'
            row['validation_error'] = f'{type(error).__name__}: {error}'
    row['raw_sha256'] = {str(p.relative_to(out)): sha(p) for p in directory.rglob('*')
                         if p.is_file() and p.name != 'record.json'}
    write(directory/'record.json', row)
    return row


def capture(manifest_path, out, phase):
    manifest = read(manifest_path)
    assert manifest['schema'] == 2 and manifest['cpu'] in os.sched_getaffinity(0)
    assert manifest['output_contract'] == 'full-native-diagrams-local-fragments-v1'
    assert not manifest.get('reused_profiles'), 'This boundary requires fresh direct-full-output profiles'
    assert manifest.get('host_policy', 'exclusive_workers') in ('exclusive_workers', 'reported_external_compilers', 'reported_external_workers')
    assert phase in ('pilot', 'validation') or (manifest['native_rounds'] >= 15 and manifest['warmups_per_task'] >= 1)
    tasks = {task['id']: task for task in manifest['tasks']}
    if phase == 'final':
        assert manifest['profile_parallelism'] == 4 and manifest['cpu'] == 100
        assert len([t for t in tasks.values() if t['profile']]) == 4
        assert len(tasks) == manifest['supported_task_count'] == 24
        assert manifest['matrix_engines'] == ['linnet'] and manifest['matrix_variants'] == ['baseline', 'candidate']
        assert {(t['case'],t['variant']) for t in tasks.values() if t['profile']} == {
            (case,variant) for case in ('qcd_2pt_l4','sm_uubar_HH_uubar_l1') for variant in ('baseline','candidate')}
        info = {}
        for block in Path('/proc/cpuinfo').read_text().split('\n\n'):
            fields = {key.strip():value.strip() for key,value in
                      (line.split(':',1) for line in block.splitlines() if ':' in line)}
            if 'processor' in fields:
                info[int(fields['processor'])] = fields
        placements = [manifest['profile_cpu_topology'][str(cpu_set(t,manifest,'callgrind')[0])] for t in tasks.values() if t['profile']]
        assert len({(p['package'],p['core']) for p in placements}) == 4
        assert len({tuple(p['architecture'].items()) for p in placements}) == 1
        for task in tasks.values():
            assert cpu_set(task,manifest) == [100]
            assert task_environment(task,manifest)['BENCH_THREADS'] == '1'
            assert task_environment(task,manifest)['RAYON_NUM_THREADS'] == '1'
            if task['profile']:
                assert len(cpu_set(task,manifest,'callgrind')) == 1
                cpu = cpu_set(task,manifest,'callgrind')[0]
                topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
                assert int((topology/'physical_package_id').read_text()) == manifest['profile_cpu_topology'][str(cpu)]['package']
                assert int((topology/'core_id').read_text()) == manifest['profile_cpu_topology'][str(cpu)]['core']
                saved = manifest['profile_cpu_topology'][str(cpu)]
                assert (topology/'thread_siblings_list').read_text().strip() == saved['siblings']
                assert {key:info[cpu][key] for key in saved['architecture']} == saved['architecture']
    assert len(tasks) == len(manifest['tasks'])
    for task in tasks.values():
        assert re.fullmatch(r'[a-zA-Z0-9_.-]+', task['id'])
        cpu_set(task, manifest)
        task_environment(task, manifest)
        assert task['command'][0] in manifest['files']
        assert task_environment(task, manifest)['BENCH_FULL_MODEL_FILE'] in manifest['files']
        assert not task.get('parse_command'), 'Materialization/parsing must be inside the timed driver'
        for command in task.get('expected_child_commands', []):
            assert command and command[0] in manifest['files']
        assert all(path in manifest['files'] for path in task.get('inputs', {}).values())
        if phase != 'validation':
            assert task['mode'] == 'full-native', 'Dump modes are qualification-only'
        if phase == 'final':
            assert task['expected'] and task['correctness_gate'] in manifest['files']
            assert task['comparison_qualified'] and task['expected_full_diagram_observation']
            gate = read(task['correctness_gate'])
            assert gate['status'] == 'passed' and gate['output_contract'] == manifest['output_contract']
    assert all(sha(path) == digest for path, digest in manifest['files'].items())
    out.mkdir()
    cpu_info = next(block for block in Path('/proc/cpuinfo').read_text().split('\n\n')
                    if re.search(r'^processor\s*:\s*' + str(manifest['cpu']) + r'\s*$', block, re.M))
    hardware = dict(uname=list(os.uname()), allowed_cpus=sorted(os.sched_getaffinity(0)),
                    selected_cpu_info=cpu_info, loadavg=Path('/proc/loadavg').read_text().strip())
    metadata = dict(status='waiting_for_lock', run_id=str(uuid.uuid4()), phase=phase, performance_eligible=phase not in ('validation', 'smoke'), native_wall_scope='shared host; pinned paired rounds; external profilers and compilers recorded; no exclusive-core claim',
        manifest=str(manifest_path), manifest_sha256=sha(manifest_path), controller_sha256=sha(__file__), hardware=hardware,
        profile_parser_sha256=sha(PROFILE_PARSER), started=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        queue=order(manifest, phase), child_statuses=[], shared_lock=str(LOCK))
    write(out/'metadata.json', metadata)
    print(json.dumps(dict(event='waiting_for_lock', run_id=metadata['run_id'])), flush=True)
    with LOCK.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        workers = active_workers()
        guard_workers(workers, manifest, validation=phase == 'validation')
        metadata['visible_workers_after_lock'] = workers
        metadata['status'] = 'running'
        censored = set()
        def record_row(row):
            metadata['child_statuses'].append(row)
            if row['status'] == 'censored_timeout':
                censored.add((row['task'], row['tool']))
            write(out/'metadata.json', metadata)
            print(json.dumps(dict(event='child_complete', task=row['task'], tool=row['tool'],
                                  repeat=row['repeat'], status=row['status'])), flush=True)

        queue = metadata['queue']
        serial = [item for item in queue if item[1] not in ('callgrind', 'dhat')]
        for label, tool, repeat in serial:
            if (label, tool) in censored:
                row = dict(task=label,tool=tool,repeat=repeat,status='skipped_after_timeout')
            else:
                row = run(tasks[label],tool,repeat,out,manifest,lock)
            record_row(row)
            if row['status'] in ('failed_exit','invalid_result'):
                metadata['status']='failed'
                write(out/'metadata.json',metadata)
                raise RuntimeError(f'Child failed: {label} {tool} {repeat}; preserve this capture')
        # The main thread alone writes metadata, in the immutable queue order.
        # Each child retains a private folder, wait4(pid), PID/profile identity,
        # timeout and result checks. All CG children finish before DHAT starts.
        metadata['profile_batches'] = []
        for tool in ('callgrind','dhat'):
            batch = [item for item in queue if item[1] == tool]
            if not batch:
                continue
            started = time.time_ns()
            failures = []
            with ThreadPoolExecutor(max_workers=manifest['profile_parallelism']) as pool:
                futures = [pool.submit(run,tasks[label],kind,repeat,out,manifest,lock)
                           for label,kind,repeat in batch]
                for item,future in zip(batch,futures):
                    try:
                        row = future.result()
                    except Exception as error:
                        failures.append(dict(task=item[0],tool=tool,error_type=type(error).__name__))
                    else:
                        record_row(row)
                        if row['status'] in ('failed_exit','invalid_result'):
                            failures.append(dict(task=row['task'],tool=tool,status=row['status']))
            metadata['profile_batches'].append(dict(tool=tool,started_ns=started,
                finished_ns=time.time_ns(),tasks=[item[0] for item in batch],failures=failures))
            write(out/'metadata.json',metadata)
            if failures:
                metadata['status']='failed'
                write(out/'metadata.json',metadata)
                raise RuntimeError(f'Profile batch failed: {tool}; preserve all child records')
        assert all(sha(path) == digest for path, digest in manifest['files'].items())
        metadata['inputs_unchanged'] = True
        metadata['status'] = 'complete_with_censored_rows' if censored else 'complete'
        metadata['finished'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write(out/'metadata.json', metadata)
    print(json.dumps(dict(event='capture_complete', run_id=metadata['run_id'], status=metadata['status'])), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--phase', choices=['pilot', 'final', 'validation', 'smoke'], required=True)
    arguments = parser.parse_args()
    capture(arguments.manifest.resolve(), arguments.output.resolve(), arguments.phase)
