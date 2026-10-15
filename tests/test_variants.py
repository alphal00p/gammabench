import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from gammabench.cli import main
from gammabench.report import analyze
from gammabench.runner import run
from gammabench.schema import SchemaError, validate_suite
from gammabench.snapshot import load, sha256, verify, write_json
from gammabench.variants import assemble
from test_schema import example_suite


class VariantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.packages = {}
        for label, revision in (('baseline', 'a' * 40), ('candidate', 'b' * 40)):
            package = self.root / label
            package.mkdir()
            suite = example_suite()
            suite['tools'] = {'python': {'kind': 'executable', 'path': str(Path(sys.executable).resolve()),
                'version': 'Python', 'sha256': sha256(Path(sys.executable))}}
            suite['tasks'][0].update(tool_ids=['python'], inputs={'value': 'input.txt'},
                argv=['{tool:python}', '{suite_dir}/driver.py', '{input:value}'],
                validation_command=['{tool:python}', str(package / 'validate.py'), '{input:value}'])
            suite['nix_build_receipt'] = 'nix-build-receipt.json'
            (package / 'input.txt').write_text('6')
            driver = ('import json,pathlib,sys; '
                      'print("RESULT "+json.dumps({"sum":int(pathlib.Path(sys.argv[1]).read_text()),"compute_ns":10}))\n')
            (package / 'driver.py').write_text(driver)
            (package / 'validate.py').write_text(driver)
            write_json(package / 'suite.json', suite)
            write_json(package / 'nix-build-receipt.json', {
                'schema_version': 1, 'sources': {'engine': {
                    'kind': 'git', 'url': 'https://example.org/engine', 'revision': revision}},
                'subjects': {'python': 'engine'}, 'build': {'profile': 'release'},
                'comparison_context': {'workload': 'sum'}})
            self.packages[label] = package
        self.destination = self.root / 'assembled'

    def change(self, label, operation):
        path = self.packages[label] / 'suite.json'
        suite = json.loads(path.read_text())
        operation(suite)
        write_json(path, suite)

    def test_copies_and_namespaces_without_changing_package_evidence(self):
        before = {label: {path.name: path.read_bytes() for path in package.iterdir()}
                  for label, package in self.packages.items()}
        self.change('baseline', lambda suite: suite['tasks'][0].update(
            environment={'SOURCE_LITERAL': '{source:python}'}))
        before['baseline']['suite.json'] = (self.packages['baseline'] / 'suite.json').read_bytes()
        path = assemble(self.destination, self.packages)
        suite = json.loads((path / 'suite.json').read_text())
        self.assertEqual(suite['metrics'], validate_suite(example_suite())['metrics'])
        self.assertEqual(set(suite['tools']), {'baseline.python', 'candidate.python'})
        for label, task in zip(self.packages, suite['tasks']):
            self.assertEqual(task['id'], label + '.a')
            self.assertEqual(task['variant'], label)
            self.assertEqual(task['tool_ids'], [label + '.python'])
            self.assertEqual(task['inputs']['value'], 'variants/' + label + '/input.txt')
            self.assertEqual(task['argv'][1], '{suite_dir}/variants/' + label + '/driver.py')
            self.assertEqual(task['validation_command'][1], '{suite_dir}/variants/' + label + '/validate.py')
            for name, data in before[label].items():
                self.assertEqual((path / 'variants' / label / name).read_bytes(), data)
                self.assertEqual((self.packages[label] / name).read_bytes(), data)
        self.assertEqual(suite['tasks'][0]['environment']['SOURCE_LITERAL'], '{source:baseline.python}')
        receipt = json.loads((path / 'nix-build-receipt.json').read_text())
        self.assertEqual(receipt['subjects'], {})
        self.assertEqual(set(receipt['sources']), {'baseline.engine', 'candidate.engine'})
        for label in self.packages:
            self.assertEqual(receipt['build']['variants'][label]['receipt']['subjects'], {'python': 'engine'})

    def test_assembled_suite_relocates_and_produces_qualified_paired_rounds(self):
        path = assemble(self.destination, self.packages)
        relocated = self.root / 'relocated'
        shutil.move(path, relocated)
        snapshot_path = run(relocated / 'suite.json', self.root / 'snapshot', backend='python', repeats=3, warmups=1)
        snapshot = load(snapshot_path)
        self.assertEqual(snapshot['status'], 'completed')
        self.assertEqual(snapshot['validation']['status'], 'passed')
        self.assertEqual([row['task'] for row in snapshot['records'] if row['phase'] == 'measure'],
                         ['baseline.a', 'candidate.a', 'candidate.a', 'baseline.a', 'candidate.a', 'baseline.a'])
        analysis = analyze([snapshot_path], baseline='baseline')
        self.assertTrue(all(pair['status'] == 'comparable' and pair['ratio']['count'] == 3
                            for pair in analysis['comparisons']))
        self.assertEqual(next(pair for pair in analysis['comparisons'] if pair['metric'] == 'compute_ns')['ratio']['median'], 1)
        self.assertEqual(snapshot['provenance']['nix_build']['subjects'], {})
        verify(snapshot_path)

    def test_all_variant_unsupported_and_censored_records_remain_visible(self):
        for label in self.packages:
            self.change(label, lambda suite: suite.update(
                unsupported=[{'case': 'unsupported-' + label, 'engine': 'python',
                              'task': 'unsupported', 'reason': 'capability absent'}],
                qualification_censored=[{'case': 'censored-' + label, 'engine': 'python',
                    'task': 'censored', 'status': 'censored_timeout', 'timeout_seconds': 60,
                    'timing_interpretation': 'qualification only, not a generation lower bound'}]))
        path = assemble(self.destination, self.packages)
        snapshot = run(path / 'suite.json', self.root / 'availability-snapshot', backend='python',
                       repeats=1, warmups=0)
        availability = analyze([snapshot])['availability']
        self.assertEqual({(row['case'], row['variant'], row['task']) for row in availability}, {
            (kind + '-' + label, label, label + '.' + kind)
            for kind in ('unsupported', 'censored') for label in self.packages})
        self.assertEqual({row['stage'] for row in availability}, {'capability', 'qualification'})
        self.assertTrue(all('duration_ns' not in row for row in availability))

    def test_task_selection_avoids_collapsing_implementation_variants(self):
        for label in self.packages:
            def add_task(suite):
                task = copy.deepcopy(suite['tasks'][0])
                task.update(id='other', variant='other')
                suite['tasks'].append(task)
            self.change(label, add_task)
        with self.assertRaisesRegex(SchemaError, '--task'):
            assemble(self.destination, self.packages)
        self.assertFalse(self.destination.exists())
        path = assemble(self.destination, self.packages, selected_tasks=['a'])
        suite = json.loads((path / 'suite.json').read_text())
        self.assertEqual([task['id'] for task in suite['tasks']], ['baseline.a', 'candidate.a'])

    def test_rejects_incompatible_definitions_before_creating_destination(self):
        original = (self.packages['candidate'] / 'suite.json').read_bytes()
        changes = [lambda suite: suite.update(id='different'),
                   lambda suite: suite['contracts']['compute']['included'].append('extra work'),
                   lambda suite: suite['metrics']['compute_ns'].update(scope='different interval'),
                   lambda suite: suite['tasks'][0].update(case='different'),
                   lambda suite: suite['tasks'][0].update(id='different')]
        for operation in changes:
            with self.subTest(change=operation):
                (self.packages['candidate'] / 'suite.json').write_bytes(original)
                self.change('candidate', operation)
                with self.assertRaises(SchemaError):
                    assemble(self.destination, self.packages)
                self.assertFalse(self.destination.exists())

    def test_requires_realized_hash_pinned_executables_and_receipts(self):
        original = (self.packages['candidate'] / 'suite.json').read_bytes()
        changes = [lambda suite: suite['tools']['python'].pop('sha256'),
                   lambda suite: suite['tools']['python'].update(sha256='0' * 64),
                   lambda suite: suite['tools']['python'].update(path='./python'),
                   lambda suite: suite.pop('nix_build_receipt')]
        for operation in changes:
            with self.subTest(change=operation):
                (self.packages['candidate'] / 'suite.json').write_bytes(original)
                self.change('candidate', operation)
                with self.assertRaises(SchemaError):
                    assemble(self.destination, self.packages)
                self.assertFalse(self.destination.exists())

    def test_refuses_existing_output_invalid_labels_and_symlinked_fixtures(self):
        self.destination.mkdir()
        marker = self.destination / 'keep'
        marker.write_text('existing work')
        with self.assertRaises(FileExistsError):
            assemble(self.destination, self.packages)
        self.assertEqual(marker.read_text(), 'existing work')
        dangling = self.root / 'dangling-output'
        target = self.root / 'absent-target'
        dangling.symlink_to(target, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            assemble(dangling, self.packages)
        self.assertTrue(dangling.is_symlink())
        self.assertFalse(target.exists())
        other = self.root / 'fresh'
        for variants in ({'only': self.packages['baseline']},
                         {'../escape': self.packages['baseline'], 'candidate': self.packages['candidate']}):
            with self.assertRaises(SchemaError):
                assemble(other, variants)
        (self.packages['candidate'] / 'outside').symlink_to(self.root)
        with self.assertRaisesRegex(SchemaError, 'symlinks'):
            assemble(other, self.packages)
        self.assertFalse(other.exists())

    def test_cli_assembles_and_rejects_duplicate_labels(self):
        arguments = ['variants', str(self.destination)]
        for label, package in self.packages.items():
            arguments += ['--variant', f'{label}={package}']
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(main(arguments), 0)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'assembled')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['variants', str(self.root / 'duplicate'),
                                   '--variant', f'a={self.packages["baseline"]}',
                                   '--variant', f'a={self.packages["candidate"]}']), 2)

    def test_real_hyperfine_runs_assembled_variants_as_paired_samples(self):
        executable = os.environ.get('GAMMABENCH_HYPERFINE') or shutil.which('hyperfine')
        if executable is None:
            self.skipTest('Hyperfine 2.0.0 is supplied by the Nix development environment')
        path = assemble(self.destination, self.packages)
        snapshot = run(path / 'suite.json', self.root / 'hyperfine-snapshot', hyperfine=executable,
                       repeats=2, warmups=1)
        pairs = analyze([snapshot], baseline='baseline')['comparisons']
        self.assertTrue(pairs)
        self.assertTrue(all(pair['status'] == 'comparable' and pair['ratio']['count'] == 2 for pair in pairs))
        verify(snapshot)


if __name__ == '__main__':
    unittest.main()
