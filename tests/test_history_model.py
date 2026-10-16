"""Exercise the browser's capture model with the CI-provided Node runtime."""

import shutil
import subprocess
import unittest
from pathlib import Path


class HistoryModelTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for browser model tests")
    def test_shared_columns_and_preserved_evidence_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(["node", str(root / "tests/history-model.cjs")], check=True)
