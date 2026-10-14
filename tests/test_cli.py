import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from gammabench.cli import main
from gammabench.runner import run
from gammabench.snapshot import verify


class CliPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        suite = {'schema_version': 1, 'id': 'cli-proof', 'description': 'CLI preservation test',
                 'contracts': {'value-v1': {'description': 'Exact value', 'included': ['value'], 'excluded': []}},
                 'metrics': {}, 'tools': {}, 'tasks': [{
                     'id': 'value', 'case': 'one', 'engine': 'python', 'variant': 'baseline',
                     'contract': 'value-v1', 'argv': [sys.executable, '-c', 'print(\'RESULT {"value":1}\')'],
                     'tool_ids': [], 'inputs': {}, 'expected': {'value': 1}, 'equivalence_fields': ['value']}]}
        path = self.root / 'suite.json'
        path.write_text(json.dumps(suite))
        self.snapshot = run(path, self.root / 'snapshot', repeats=1, warmups=0)

    def invoke(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(list(arguments))

    def test_compare_cannot_rewrite_a_sealed_snapshot(self):
        path = self.snapshot / 'snapshot.json'
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(self.invoke('compare', str(self.snapshot), '--output', str(path)), 2)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertEqual(self.invoke('compare', str(self.snapshot), '--output', str(self.snapshot / 'new.json')), 2)
        verify(self.snapshot)

    def test_compare_cannot_overwrite_an_existing_analysis(self):
        path = self.root / 'analysis.json'
        path.write_text('prior analysis')
        self.assertEqual(self.invoke('compare', str(self.snapshot), '--output', str(path)), 2)
        self.assertEqual(path.read_text(), 'prior analysis')
        verify(self.snapshot)

    def test_compare_exports_to_a_new_separate_file(self):
        path = self.root / 'analysis.json'
        self.assertEqual(self.invoke('compare', str(self.snapshot), '--output', str(path)), 0)
        self.assertEqual(json.loads(path.read_text())['schema_version'], 1)
        verify(self.snapshot)
