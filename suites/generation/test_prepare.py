"""Source pin guard regressions; these never invoke an engine build."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class PreparationTests(unittest.TestCase):
    def reject(self, mutate, *, engine="linnet", message="source"):
        lock = json.loads((HERE.parents[1] / "tools.lock.json").read_text())
        bindings = {}
        for name, declared in lock["tools"].items():
            bindings[name] = {**declared, "source_path": "/missing-source", "dirty": False}
            if declared["kind"] == "git":
                bindings[name]["resolved_revision"] = declared["revision"]
            else:
                bindings[name]["source_archive_sha256"] = declared["sha256"]
        mutate(bindings)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolved = root / "resolved.json"
            resolved.write_text(json.dumps({"tools": bindings}))
            output = root / "must-not-be-created"
            result = subprocess.run([sys.executable, str(HERE / "prepare.py"), "--resolved-tools", str(resolved),
                "--output", str(output), "--contract", "materialized", "--engines", engine, "--build"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)
            self.assertFalse(output.exists())

    def test_declared_source_revision_mismatch_rejected_before_build(self):
        self.reject(lambda bindings: bindings["gammaloop"].update(revision="a" * 40), message="tools-lock declaration")

    def test_actual_git_revision_mismatch_rejected_before_build(self):
        self.reject(lambda bindings: bindings["gammaloop"].update(resolved_revision="a" * 40), message="Actual gammaloop Git revision")

    def test_actual_archive_hash_mismatch_rejected_before_build(self):
        self.reject(lambda bindings: bindings["qgraf"].update(source_archive_sha256="a" * 64), engine="qgraf", message="Actual qgraf archive digest")

    def test_archive_subdirectory_mismatch_rejected_before_build(self):
        self.reject(lambda bindings: bindings["nauty"].update(subdir="wrong"), engine="nauty", message="different source subdirectory")


if __name__ == "__main__":
    unittest.main()
