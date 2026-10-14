import json
from pathlib import Path
import shutil
import tempfile
import unittest

from gammabench.snapshot import IntegrityError, load, seal, staging_directory, verify
from test_schema import example_snapshot, example_suite


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def bundle(self, name="run"):
        destination = self.directory / name
        staging = staging_directory(destination)
        (staging / "raw").mkdir()
        (staging / "raw" / "stdout.txt").write_text("result\n")
        return seal(staging, destination, example_snapshot(), example_suite())

    def test_relocated_snapshot_verifies(self):
        original = self.bundle()
        moved = self.directory / "moved"
        shutil.move(original, moved)
        self.assertEqual(load(moved)["id"], "test-run")
        self.assertEqual(len(verify(moved)), 3)

    def test_tampered_file_is_detected(self):
        path = self.bundle()
        raw = path / "raw" / "stdout.txt"
        raw.chmod(0o644)
        raw.write_text("changed\n")
        with self.assertRaisesRegex(IntegrityError, "SHA-256 mismatch"):
            load(path)

    def test_added_and_missing_files_are_detected(self):
        path = self.bundle()
        (path / "extra.txt").write_text("untracked\n")
        with self.assertRaisesRegex(IntegrityError, "added"):
            verify(path)
        (path / "extra.txt").unlink()
        (path / "raw" / "stdout.txt").unlink()
        with self.assertRaisesRegex(IntegrityError, "missing"):
            verify(path)

    def test_existing_destination_is_never_overwritten(self):
        path = self.bundle()
        with self.assertRaises(FileExistsError):
            staging_directory(path)
        self.assertEqual(load(path)["id"], "test-run")

    def test_partial_run_is_explicitly_unsealed(self):
        staging = staging_directory(self.directory / "run")
        (staging / "snapshot.json").write_text("{}")
        with self.assertRaisesRegex(IntegrityError, "unsealed"):
            verify(staging)

    def test_symlink_and_nested_inventory_names(self):
        staging = staging_directory(self.directory / "run")
        (staging / "link").symlink_to("/etc/hosts")
        with self.assertRaisesRegex(IntegrityError, "symlinks"):
            seal(staging, self.directory / "run", example_snapshot(), example_suite())
        (staging / "link").unlink()
        (staging / "nested").mkdir()
        (staging / "nested" / "files.sha256.json").write_text("nested raw file")
        path = seal(staging, self.directory / "run", example_snapshot(), example_suite())
        self.assertIn("nested/files.sha256.json", verify(path))


if __name__ == "__main__":
    unittest.main()
