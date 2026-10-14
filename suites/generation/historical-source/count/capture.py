"""Frozen-command comparison runner. Preparation/parsing are outside child wall time."""
from pathlib import Path
from collections import defaultdict
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
LOCK = Path('/tmp/feynman-generation-lazy-outputs-evidence/n2-profiler.lock')
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


def cpu_set(task, manifest):
    cpus = task.get('cpus', [manifest['cpu']])
    assert cpus and cpus == sorted(set(cpus)) and all(isinstance(cpu, int) for cpu in cpus)
    assert set(cpus).issubset(os.sched_getaffinity(0))
    return cpus


def task_environment(task, manifest):
    overrides = task.get('environment', {})
    assert set(overrides).issubset({'BENCH_THREADS', 'RAYON_NUM_THREADS'})
    environment = {**manifest['environment'], **overrides}
    assert int(environment.get('BENCH_THREADS', '1')) <= len(cpu_set(task, manifest))
    return environment


def order(manifest, phase):
    tasks = manifest['tasks']
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
    if 'elapsed_ns' in result:
        assert isinstance(result['elapsed_ns'], int) and result['elapsed_ns'] >= 0
    return result


def profile_metrics(tool, path, stderr, command):
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
    profile = directory / ('callgrind.out' if tool == 'callgrind' else 'dhat.json')
    launch = command
    if tool in ('callgrind', 'dhat'):
        launch = [manifest['tools']['valgrind'], f'--tool={tool}', f'--{tool}-out-file={profile}',
                  *(['--num-callers=40'] if tool == 'dhat' else []), *command]
    cpus = cpu_set(task, manifest)
    launch = [manifest['tools']['taskset'], '-c', ','.join(map(str, cpus)), *launch]
    env = os.environ.copy()
    env.update(task_environment(task, manifest))
    timeout = manifest['timeouts_seconds']['profile' if tool in ('callgrind', 'dhat') else 'native']
    row = dict(task=task['id'], engine=task['engine'], case=task['case'], tool=tool, repeat=repeat, cpus=cpus, task_environment=task_environment(task, manifest), visible_workers_before=workers, loadavg_before=Path('/proc/loadavg').read_text().strip(),
               command=launch, cwd=str(work), timeout_seconds=timeout, profile=str(profile) if tool in ('callgrind', 'dhat') else None)
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
            if tool in ('callgrind', 'dhat'):
                row['metrics'] = profile_metrics(tool, profile, paths['stderr'].read_text(), command)
        except Exception as error:
            row['status'] = 'invalid_result'
            row['validation_error'] = f'{type(error).__name__}: {error}'
    row['raw_sha256'] = {str(p.relative_to(out)): sha(p) for p in directory.rglob('*')
                         if p.is_file() and p.name != 'record.json'}
    write(directory/'record.json', row)
    return row


def capture(manifest_path, out, phase):
    manifest = read(manifest_path)
    assert manifest['schema'] == 1 and manifest['cpu'] in os.sched_getaffinity(0)
    assert manifest.get('host_policy', 'exclusive_workers') in ('exclusive_workers', 'reported_external_compilers')
    assert phase in ('pilot', 'validation') or (manifest['native_rounds'] >= 15 and manifest['warmups_per_task'] >= 1)
    tasks = {task['id']: task for task in manifest['tasks']}
    assert len(tasks) == len(manifest['tasks'])
    for task in tasks.values():
        assert re.fullmatch(r'[a-zA-Z0-9_.-]+', task['id'])
        cpu_set(task, manifest)
        task_environment(task, manifest)
        assert task['command'][0] in manifest['files']
        assert all(path in manifest['files'] for path in task.get('inputs', {}).values())
        if phase == 'final':
            assert task['expected'] and task['correctness_gate'] in manifest['files']
    assert all(sha(path) == digest for path, digest in manifest['files'].items())
    out.mkdir()
    cpu_info = next(block for block in Path('/proc/cpuinfo').read_text().split('\n\n')
                    if re.search(r'^processor\s*:\s*' + str(manifest['cpu']) + r'\s*$', block, re.M))
    hardware = dict(uname=list(os.uname()), allowed_cpus=sorted(os.sched_getaffinity(0)),
                    selected_cpu_info=cpu_info, loadavg=Path('/proc/loadavg').read_text().strip())
    metadata = dict(status='waiting_for_lock', run_id=str(uuid.uuid4()), phase=phase, performance_eligible=phase != 'validation', native_wall_scope='shared host; balanced rounds and recorded external compiler context',
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
        for label, tool, repeat in metadata['queue']:
            if (label, tool) in censored:
                metadata['child_statuses'].append(dict(task=label, tool=tool, repeat=repeat, status='skipped_after_timeout'))
            else:
                row = run(tasks[label], tool, repeat, out, manifest, lock)
                metadata['child_statuses'].append(row)
                if row['status'] == 'censored_timeout':
                    censored.add((label, tool))
                write(out/'metadata.json', metadata)
                print(json.dumps(dict(event='child_complete', task=label, tool=tool, repeat=repeat, status=row['status'])), flush=True)
                if row['status'] in ('failed_exit', 'invalid_result'):
                    metadata['status'] = 'failed'
                    write(out/'metadata.json', metadata)
                    raise RuntimeError(f'Child failed: {label} {tool} {repeat}; preserve this capture')
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
    parser.add_argument('--phase', choices=['pilot', 'final', 'validation'], required=True)
    arguments = parser.parse_args()
    capture(arguments.manifest.resolve(), arguments.output.resolve(), arguments.phase)
