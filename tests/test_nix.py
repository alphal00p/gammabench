"""Nix receipts stay portable evidence and cannot escape the declared suite."""
import copy
import json
from pathlib import Path
import tempfile
import sys
import subprocess
import unittest

from gammabench.nix import capture_receipt, load_receipt, verify_receipt
from gammabench.schema import SchemaError, validate_suite
from gammabench.runner import run
from gammabench.snapshot import load
from test_schema import example_suite
from unittest.mock import patch


class NixReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.suite = example_suite()
        self.suite['nix_build_receipt'] = 'nix-build-receipt.json'
        self.suite_path = self.root / 'suite.json'
        self.suite_path.write_text(json.dumps(self.suite))
        self.data = {'schema_version': 1,
                     'sources': {'subject': {'kind': 'git', 'url': 'https://example.com/tool', 'revision': 'a' * 40}},
                     'subjects': {'example': 'subject'}, 'build': {'system': 'x86_64-linux'},
                     'comparison_context': {'profile': 'release'}}
        self.receipt_path = self.root / 'nix-build-receipt.json'
        self.receipt_path.write_text(json.dumps(self.data))

    def test_receipt_is_copied_and_bound_to_original_bytes(self):
        receipt = load_receipt(self.suite, self.suite_path)
        metadata = capture_receipt(receipt, self.root / 'snapshot')
        self.assertEqual(metadata['nix_build'], self.data)
        self.assertEqual((self.root / 'snapshot' / metadata['nix_build_receipt']).read_bytes(), self.receipt_path.read_bytes())
        verify_receipt(receipt)
        self.receipt_path.write_text('{}')
        with self.assertRaisesRegex(SchemaError, 'changed'):
            verify_receipt(receipt)

    def test_missing_receipt_and_unsafe_paths_fail(self):
        for path in ('../receipt.json', '/tmp/receipt.json'):
            suite = {**self.suite, 'nix_build_receipt': path}
            with self.assertRaises(SchemaError):
                validate_suite(suite)
        self.receipt_path.unlink()
        with self.assertRaisesRegex(SchemaError, 'inside the suite'):
            load_receipt(self.suite, self.suite_path)

    def test_mutable_revision_and_unknown_subject_are_rejected(self):
        for change in ('revision', 'subject', 'schema'):
            data = copy.deepcopy(self.data)
            if change == 'revision':
                data['sources']['subject']['revision'] = 'main'
            elif change == 'subject':
                data['subjects']['example'] = 'undeclared'
            else:
                data['schema_version'] = 2
            self.receipt_path.write_text(json.dumps(data))
            with self.assertRaises(SchemaError):
                load_receipt(self.suite, self.suite_path)

    def test_realized_executable_suite_does_not_need_a_writable_source_cache(self):
        from gammabench.sources import resolve_tools, verify_tools
        cache = self.root / 'immutable-framework' / 'cache'
        bindings = resolve_tools({'python': {'kind': 'executable', 'path': sys.executable,
                                  'version': 'Python'}}, ['python'], cache_dir=cache)
        verify_tools(bindings)
        self.assertFalse(cache.parent.exists())

    def test_runner_seals_receipt_and_relocated_snapshot_verifies(self):
        with patch('gammabench.sources.resolve_tools', return_value={}), patch('gammabench.sources.verify_tools'):
            path = run(self.suite_path, self.root / 'captured', repeats=1, warmups=0, backend='python')
        moved = self.root / 'moved'
        path.rename(moved)
        snapshot = load(moved)
        self.assertEqual(snapshot['provenance']['nix_build'], self.data)
        self.assertEqual((moved / snapshot['provenance']['nix_build_receipt']).read_bytes(), self.receipt_path.read_bytes())


class GenerationPackageDefinitionTests(unittest.TestCase):
    def test_all_contracts_assemble_valid_definitions_with_existing_inputs(self):
        root = Path(__file__).resolve().parents[1]
        for contract in ('count', 'materialized', 'signed-routed', 'full-public'):
            with self.subTest(contract=contract), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                output = directory / 'package'
                output.mkdir()
                (output / 'nix-build-receipt.json').write_text('{}')
                config = directory / 'config.json'
                config.write_text(json.dumps({'contract': contract, 'binary': sys.executable,
                                              'engines': ['linnet'], 'external': {}}))
                subprocess.run([sys.executable, str(root / 'nix/generation-suite.py'),
                                str(root), str(output), str(config)], check=True,
                               capture_output=True)
                suite = validate_suite(json.loads((output / 'suite.json').read_text()))
                self.assertTrue(suite['tasks'])
                for task in suite['tasks']:
                    self.assertNotIn('nix-build-receipt.json', task['workload_inputs'])
                    for name in task['inputs'].values():
                        self.assertTrue((output / name).is_file(), name)
                if contract in ('signed-routed', 'full-public'):
                    self.assertTrue(any(row['case'] == 'qed_ee_mumu_tautau_l2'
                                        and row['reason'] for row in suite['unsupported']))
