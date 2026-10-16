"""The default list freezes evidence and preserves independent measurement identities."""
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ListElements(HTMLParser):
    def __init__(self, value):
        super().__init__()
        self.model = None
        self.catalog = None
        self.ids = []
        self.redirect = None
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if attrs.get("id") == "benchmark-explorer":
            self.catalog = json.loads(attrs["data-catalog"])
            self.model = json.loads(attrs["data-list-model"])
            for record in self.model["records"]:
                for field in ("median", "min", "max", "pairedRatio"):
                    if record[field] is not None:
                        record[field] = float(record[field])
            for pair in self.model["comparisons"]:
                for field in ("ratio", "reverse"):
                    if pair[field] is not None:
                        pair[field] = float(pair[field])
        if attrs.get("id") == "list-redirect":
            self.redirect = {"target": attrs["data-target"], "defaults": json.loads(attrs["data-defaults"])}


@unittest.skipUnless(shutil.which("node"), "Node is required for list semantics")
class InlineListModelTests(unittest.TestCase):
    def test_independent_histories_and_exact_comparison_semantics(self):
        result = subprocess.run([shutil.which("node"), str(ROOT / "tests/list-model.cjs")], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
