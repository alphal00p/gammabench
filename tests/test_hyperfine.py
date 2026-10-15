import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gammabench.cli import main
from gammabench.hyperfine import invocation, read_measurement, resolve
from gammabench.runner import run
from gammabench.schema import SchemaError
from gammabench.snapshot import load, verify
from test_schema import example_suite


class HyperfineExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'hyperfine.json'
        self.argv = ['/benchmark path/run', 'literal $(touch never)', 'a"b', '']
        self.document = {
            'schema_version': 2, 'metadata': {'hyperfine_version': '2.0.0'},
            'primary_metric': 'time_wall_clock', 'results': [{
                'command': shlex.join(self.argv), 'measurements': [{
                    'time_wall_clock': {'value': 0.123456789, 'unit': 'second'}, 'exit_code': 0}],
                'summary': {'time_wall_clock': {'median': 999}}}]}

    def read(self, document):
        self.path.write_text(json.dumps(document))
        return read_measurement(self.path, self.argv)

    def test_reads_raw_measurement_and_preserves_literal_arguments(self):
        self.assertEqual(self.read(self.document), {'process_wall_ns': 123456789, 'returncode': 0})
        command = invocation({'path': '/hyperfine'}, self.argv, self.path)
        self.assertEqual(shlex.split(command[-1]), self.argv)
        self.assertIn('--shell=none', command)
        self.assertIn('--ignore-failure=all-non-zero', command)

    def test_rejects_incompatible_exports_instead_of_using_summary(self):
        variants = []
        for version in (1, True, 3):
            doc = copy.deepcopy(self.document)
            doc['schema_version'] = version
            variants.append(doc)
        for version in ('1.20.0', '2.0.1'):
            doc = copy.deepcopy(self.document)
            doc['metadata']['hyperfine_version'] = version
            variants.append(doc)
        for key, value in (('primary_metric', 'time_cpu'), ('results', [])):
            doc = copy.deepcopy(self.document)
            doc[key] = value
            variants.append(doc)
        for key, value in (('command', '/other'), ('measurements', []),
                           ('measurements', self.document['results'][0]['measurements'] * 2)):
            doc = copy.deepcopy(self.document)
            doc['results'][0][key] = value
            variants.append(doc)
        for doc in variants:
            with self.subTest(document=doc), self.assertRaises(SchemaError):
                self.read(doc)

    def test_rejects_missing_bad_units_and_nonfinite_wall_values(self):
        for wall in (None, {}, {'value': 1, 'unit': 'ns'},
                     *({'value': value, 'unit': 'second'} for value in
                       (None, True, -1, '1', float('nan'), float('inf'), 10 ** 400))):
            doc = copy.deepcopy(self.document)
            doc['results'][0]['measurements'][0]['time_wall_clock'] = wall
            with self.subTest(wall=wall), self.assertRaises(SchemaError):
                self.read(doc)
        for exit_code in (None, True, -9, 0.5):
            doc = copy.deepcopy(self.document)
            doc['results'][0]['measurements'][0]['exit_code'] = exit_code
            with self.subTest(exit_code=exit_code), self.assertRaises(SchemaError):
                self.read(doc)

    def test_missing_or_truncated_export_is_not_zero(self):
        with self.assertRaises(SchemaError):
            read_measurement(self.path, self.argv)
        self.path.write_text('{')
        with self.assertRaises(SchemaError):
            read_measurement(self.path, self.argv)

    def test_reserved_hyperfine_overrides_are_rejected_before_staging(self):
        for key in ('HYPERFINE_ITERATION', 'HYPERFINE_RANDOMIZED_ENVIRONMENT_OFFSET'):
            for location in ('task', 'run'):
                definition = example_suite()
                environment = {}
                if location == 'task':
                    definition['tasks'][0]['environment'] = {key: 'requested-value'}
                else:
                    environment[key] = 'requested-value'
                suite = self.root / 'suite.json'
                suite.write_text(json.dumps(definition))
                output = self.root / 'result'
                with self.subTest(variable=key, location=location), self.assertRaisesRegex(
                        SchemaError, 'Hyperfine reserves environment variables'):
                    run(suite, output, environment=environment, hyperfine=self.root / 'missing')
                self.assertFalse(output.exists())
                self.assertFalse(list(self.root.glob('.result.unsealed-*')))

    def test_controller_without_export_cannot_supply_successful_wall_time(self):
        suite = self.root / 'suite.json'
        suite.write_text(json.dumps(example_suite()))
        for code, status in ((0, 'invalid'), (3, 'failed')):
            # Deliberately broken controller: version succeeds, no measurement is exported.
            executable = self.root / f'broken-hyperfine-{code}'
            executable.write_text(f'#!{sys.executable}\nimport sys\n'
                'if sys.argv[1:] == ["--version"]: print("hyperfine 2.0.0")\n'
                f'else: sys.exit({code})\n')
            executable.chmod(0o755)
            output = run(suite, self.root / f'result-{code}', repeats=1, warmups=0,
                         hyperfine=executable)
            snapshot = load(output)
            row = next(row for row in snapshot['records'] if row['phase'] == 'measure')
            with self.subTest(controller_exit=code):
                self.assertEqual(row['status'], status)
                self.assertEqual(row['metrics'], {})
                self.assertIsNone(row['returncode'])
                self.assertEqual(row['controller_returncode'], code)
                self.assertGreater(row['duration_ns'], 0)
                self.assertEqual(snapshot['status'], 'failed')
            verify(output)

    def test_preflight_requires_exact_version_not_any_hyperfine_two(self):
        for version in ('hyperfine 1.20.0', 'hyperfine 2.0.1'):
            with self.subTest(version=version), patch('gammabench.hyperfine.subprocess.run',
                    return_value=SimpleNamespace(returncode=0, stdout=version, stderr='')):
                with self.assertRaisesRegex(ValueError, 'Hyperfine 2.0.0'):
                    resolve(sys.executable)

    def test_missing_tool_fails_before_snapshot_staging(self):
        suite = self.root / 'suite.json'
        suite.write_text(json.dumps(example_suite()))
        output = self.root / 'result'
        with self.assertRaisesRegex(ValueError, 'Hyperfine 2.0.0'):
            run(suite, output, hyperfine=self.root / 'missing')
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob('.result.unsealed-*')))
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['run', str(suite), str(output), '--hyperfine', str(self.root / 'missing')]), 2)


class HyperfineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = os.environ.get('GAMMABENCH_HYPERFINE') or shutil.which('hyperfine')
        if executable is None:
            raise unittest.SkipTest('Hyperfine 2.0.0 is supplied by the Nix development environment')
        cls.tool = resolve(executable)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def execute(self, suite, **kwargs):
        path = self.root / 'suite.json'
        path.write_text(json.dumps(suite))
        output = run(path, self.root / 'result', repeats=kwargs.pop('repeats', 2),
                     warmups=kwargs.pop('warmups', 1), hyperfine=self.tool['path'], **kwargs)
        return load(output)

    def test_real_hyperfine_preserves_rounds_application_metrics_and_evidence(self):
        suite = example_suite()
        other = copy.deepcopy(suite['tasks'][0])
        other.update(id='b', variant='b')
        suite['tasks'].append(other)
        snapshot = self.execute(suite)
        self.assertEqual(snapshot['status'], 'completed')
        self.assertEqual(snapshot['validation']['status'], 'passed')
        self.assertEqual(snapshot['provenance']['measurement_backend']['version'], '2.0.0')
        samples = [row for row in snapshot['records'] if row['phase'] != 'validation']
        self.assertEqual([(row['task'], row['phase'], row['repeat']) for row in samples],
                         [(row['task'], row['phase'], row['repeat']) for row in snapshot['schedule']['order']])
        self.assertEqual(len({row['controller_pid'] for row in samples}), len(samples))
        for row in samples:
            self.assertIsNone(row['pid'])
            self.assertEqual(row['controller_returncode'], 0)
            self.assertEqual(row['returncode'], 0)
            self.assertEqual(row['metrics']['compute_ns'], 10)
            self.assertGreater(row['metrics']['process_wall_ns'], row['metrics']['compute_ns'])
            raw = self.root / 'result' / row['raw']['hyperfine']
            self.assertEqual(row['metrics']['process_wall_ns'], read_measurement(raw, row['command'])['process_wall_ns'])
            self.assertGreaterEqual(row['duration_ns'], row['metrics']['process_wall_ns'])
        verify(self.root / 'result')

    def test_literals_working_files_environment_and_stderr_survive(self):
        suite = example_suite()
        (self.root / 'input.txt').write_text('6')
        task = suite['tasks'][0]
        task['inputs'] = {'value': 'input.txt'}
        task['environment'] = {'BENCH_LITERAL': '$(touch forbidden)'}
        task['argv'] = [sys.executable, '-c',
            'import os,pathlib,sys; assert sys.argv[1] == "$(touch forbidden)"; '
            'assert os.environ["BENCH_LITERAL"] == sys.argv[1]; '
            'p=pathlib.Path("input.txt"); assert p.read_text()=="6"; p.write_text("changed"); '
            'print("child diagnostic",file=sys.stderr); print(\'RESULT {"sum":6,"compute_ns":10}\')',
            '$(touch forbidden)']
        snapshot = self.execute(suite)
        self.assertEqual(snapshot['status'], 'completed')
        self.assertFalse(any((self.root / 'result').rglob('forbidden')))
        for row in snapshot['records']:
            self.assertIn('child diagnostic', (self.root / 'result' / row['raw']['stderr']).read_text())

    def test_nonzero_exit_is_retained_but_not_a_successful_metric(self):
        suite = example_suite()
        task = suite['tasks'][0]
        task.pop('expected')
        task.pop('equivalence_fields')
        task['argv'] = [sys.executable, '-c', 'raise SystemExit(3)']
        snapshot = self.execute(suite, repeats=1, warmups=0)
        row = snapshot['records'][0]
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['returncode'], 3)
        self.assertEqual(row['controller_returncode'], 0)
        self.assertEqual(row['metrics'], {})
        self.assertIn('hyperfine', row['raw'])

    def test_signal_exit_uses_hyperfine_status_without_inventing_a_target_pid(self):
        suite = example_suite()
        task = suite['tasks'][0]
        task.pop('expected')
        task.pop('equivalence_fields')
        task['argv'] = [sys.executable, '-c',
                        'import os,signal; os.kill(os.getpid(),signal.SIGTERM)']
        snapshot = self.execute(suite, repeats=1, warmups=0)
        row = snapshot['records'][0]
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['returncode'], 128 + signal.SIGTERM)
        self.assertIsNone(row['pid'])
        self.assertEqual(row['controller_returncode'], 0)
        self.assertEqual(row['metrics'], {})

    def test_timeout_kills_supervisor_and_descendant_process_group(self):
        suite = example_suite()
        task = suite['tasks'][0]
        task.pop('expected')
        task.pop('equivalence_fields')
        task['argv'] = [sys.executable, '-u', '-c',
            'import subprocess,sys,time; '
            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
            'print("DESCENDANT "+str(p.pid),flush=True); '
            'print(\'RESULT {"sum":6,"compute_ns":10}\',flush=True); time.sleep(30)']
        snapshot = self.execute(suite, repeats=1, warmups=0, timeout=0.5)
        row = snapshot['records'][0]
        self.assertEqual(row['status'], 'timeout')
        self.assertIsNone(row['pid'])
        self.assertIsNone(row['returncode'])
        self.assertEqual(row['controller_returncode'], -signal.SIGKILL)
        self.assertEqual(row['metrics'], {})
        text = (self.root / 'result' / row['raw']['stdout']).read_text()
        child = int(next(line.split()[1] for line in text.splitlines() if line.startswith('DESCENDANT ')))
        # Linux can briefly retain a killed orphan as a zombie; it cannot keep running.
        stat = Path(f'/proc/{child}/stat')
        deadline = time.monotonic() + 1
        state = None
        while time.monotonic() < deadline:
            try:
                state = stat.read_text().split(') ', 1)[1].split()[0]
            except FileNotFoundError:
                state = None
                break
            if state == 'Z':
                break
            time.sleep(0.01)
        self.assertIn(state, (None, 'Z'))
        if state is None:
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)
        verify(self.root / 'result')


if __name__ == '__main__':
    unittest.main()
