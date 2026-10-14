import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from gammabench.history import import_snapshot
from gammabench.snapshot import load


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class HistoryTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "old" / "final"
        gate = root / "old" / "gate.json"
        write(gate, {"status": "passed", "exact_fields": ["vertices", "symmetry_factor"]})
        task = {"id": "candidate-case", "engine": "linnet", "case": "case", "mode": "full-native",
                "variant": "candidate", "command": ["/old/binary", "case"],
                "output_contract": "full-native-diagrams-local-fragments-v1", "comparison_qualified": True,
                "correctness_gate": str(gate)}
        manifest = root / "old" / "manifest.json"
        write(manifest, {"tasks": [task], "native_rounds": 2, "files": {str(gate): hashlib.sha256(gate.read_bytes()).hexdigest()}})
        record = {"task": "candidate-case", "repeat": 0, "tool": "native", "status": "completed",
                  "returncode": 0, "command": ["/old/binary", "case"], "child_wall_ns": 900,
                  "result": {"full_diagrams_ns": 700}}
        write(source / "candidate.native.0" / "record.json", record)
        record = dict(record, repeat=1, status="failed", returncode=1)
        write(source / "candidate.native.1" / "record.json", record)
        captured = [json.loads(path.read_text()) for path in sorted(source.glob("*/record.json"))]
        write(source / "metadata.json", {"status": "complete", "inputs_unchanged": True,
              "run_id": "original-run", "manifest": str(manifest),
              "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(), "finished": "2026-10-13T00:00:00Z",
              "queue": [[record["task"], record["tool"], record["repeat"]] for record in captured], "child_statuses": captured})
        return source, manifest

    def test_import_relocates_and_does_not_invent_missing_pins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _ = self.fixture(root)
            imported = import_snapshot(source, root / "snapshot", label="preserved")
            data = load(imported)
            self.assertEqual(data["origin"], "historical-import")
            self.assertEqual(data["provenance"]["legacy_run_id"], "original-run")
            self.assertTrue(data["tools"]["candidate-case"]["missing_source_pin"])
            self.assertEqual(data["records"][1]["metrics"], {})
            imported.rename(root / "moved")
            self.assertEqual(load(root / "moved")["id"], "preserved")

    def test_manifest_tampering_and_destination_reuse_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, manifest = self.fixture(root)
            import_snapshot(source, root / "snapshot")
            with self.assertRaises(FileExistsError):
                import_snapshot(source, root / "snapshot")
            manifest.write_text("{}")
            with self.assertRaisesRegex(ValueError, "manifest hash"):
                import_snapshot(source, root / "new")

    def test_gate_and_child_record_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _ = self.fixture(root)
            gate = root / "old/gate.json"
            gate.write_text('{"status":"passed","exact_fields":["fake"]}')
            with self.assertRaisesRegex(ValueError, "correctness gate changed"):
                import_snapshot(source, root / "bad-gate")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _ = self.fixture(root)
            path = source / "candidate.native.0/record.json"
            record = json.loads(path.read_text())
            record["result"]["full_diagrams_ns"] = 1
            write(path, record)
            with self.assertRaisesRegex(ValueError, "child envelopes"):
                import_snapshot(source, root / "bad-record")

    def test_raw_stdout_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _ = self.fixture(root)
            path = source / "candidate.native.0/record.json"
            stdout = path.parent / "stdout"
            stdout.write_text("original")
            record = json.loads(path.read_text())
            record["raw_sha256"] = {str(stdout.relative_to(source)): hashlib.sha256(stdout.read_bytes()).hexdigest()}
            write(path, record)
            metadata = json.loads((source / "metadata.json").read_text())
            metadata["child_statuses"][0] = record
            write(source / "metadata.json", metadata)
            stdout.write_text("changed")
            with self.assertRaisesRegex(ValueError, "raw artifact changed"):
                import_snapshot(source, root / "bad-stdout")


if __name__ == "__main__":
    unittest.main()
