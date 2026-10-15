"""Scaffolds are runnable, registered plans that preserve existing work."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from gammabench.scaffold import create
from gammabench.schema import validate_suite


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("nix", "web", "suites"):
            (self.root / name).mkdir()
        (self.root / "pyproject.toml").write_text('[project]\nname = "gammabench"\n')
        for name in ("flake.nix", "nix/default.nix", "nix/mk-benchmark.nix"):
            (self.root / name).write_text("{}\n")
        self.catalog = self.root / "web/benchmarks.json"
        self.catalog.write_text(json.dumps({
            "schema_version": 1, "default_benchmark": "existing",
            "families": [{"id": "examples", "title": "Examples", "status": "captured"}],
            "subjects": {}, "benchmarks": [{
                "id": "existing", "family": "examples", "title": "Existing", "description": "Existing result",
                "definition_version": "1", "analyses": ["docs/results/existing/analysis.json"],
                "reports": ["docs/results/existing/index.html"], "default_metric": "compute_ns",
            }],
        }))
        self.original = self.catalog.read_bytes()

    def add(self, benchmark_id="tensor-demo", **options):
        return create(self.root, benchmark_id, family="examples", **options)

    def test_scaffold_registers_a_planned_runnable_suite_and_nix_recipe(self):
        result = self.add(title='A "quoted" title', description="A starter operation")
        self.assertEqual(result["status"], "created")
        suite = validate_suite(json.loads(Path(result["suite"]).read_text()))
        self.assertEqual(suite["id"], "tensor-demo")
        self.assertEqual(suite["tasks"][0]["expected"]["total"], 14999950000)
        self.assertIn("process_wall_ns", suite["metrics"])
        self.assertEqual((self.root / "suites/tensor-demo/workload.txt").read_text(), "100000\n")
        entry = json.loads(self.catalog.read_text())["benchmarks"][-1]
        self.assertEqual(entry["id"], "tensor-demo")
        self.assertEqual(entry["nix_recipe"], "nix/tensor-demo.nix")
        self.assertEqual(entry["suite"], "suites/tensor-demo/suite.json")
        self.assertEqual(entry["analyses"], [])
        self.assertEqual(entry["reports"], [])
        self.assertEqual(entry["default_case"], "100000-u64")
        self.assertIn("nix run .#tensor-demo", (self.root / "suites/tensor-demo/README.md").read_text())
        self.assertIn("--register tensor-demo", (self.root / "suites/tensor-demo/README.md").read_text())
        recipe = Path(result["nix_recipe"]).read_text()
        self.assertIn('sourceRoot + "/suites/tensor-demo"', recipe)
        self.assertIn("mkBenchmark", recipe)
        self.assertNotIn("@@", recipe)
        self.assertFalse((self.root / ".gammabench/new-benchmark.lock").exists())

    def test_discovers_checkout_from_a_nested_working_directory(self):
        nested = self.root / "suites/nested"
        nested.mkdir()
        result = create(nested, "nested-bench", family="examples")
        self.assertEqual(Path(result["suite"]), self.root / "suites/nested-bench/suite.json")
        with self.assertRaisesRegex(ValueError, "checkout"):
            create(Path(self.temporary.name).parent, "not-a-checkout", family="examples")

    def test_new_family_can_be_registered_with_the_starter(self):
        create(self.root, "tensor-demo", family="contraction", family_title="Tensor contraction")
        catalog = json.loads(self.catalog.read_text())
        family = next(item for item in catalog["families"] if item["id"] == "contraction")
        self.assertEqual(family["title"], "Tensor contraction")
        self.assertEqual(family["status"], "planned")

    def test_existing_paths_or_registration_are_never_overwritten(self):
        for existing in ("suites/tensor-demo", "nix/tensor-demo.nix"):
            with self.subTest(path=existing):
                path = self.root / existing
                if path.suffix == ".nix":
                    path.write_text("user recipe")
                else:
                    path.mkdir()
                    (path / "notes").write_text("user work")
                with self.assertRaises(FileExistsError):
                    self.add()
                self.assertEqual(self.catalog.read_bytes(), self.original)
                if path.is_dir():
                    self.assertEqual((path / "notes").read_text(), "user work")
                    shutil.rmtree(path)
                else:
                    self.assertEqual(path.read_text(), "user recipe")
                    path.unlink()
        with self.assertRaises(ValueError):
            self.add("existing")
        self.assertEqual(self.catalog.read_bytes(), self.original)
        self.assertFalse((self.root / "suites/existing").exists())

    def test_rejects_traversal_unsafe_names_reserved_outputs_and_symlinks(self):
        for value in ("../outside", "a/b", "A-name", "a_name", "bad name", "-name", "x\nname", "default", "gammabench", "bench-other", "generation-count"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.add(value)
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "suites").rmdir()
        (self.root / "suites").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.add()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.catalog.read_bytes(), self.original)

    def test_invalid_metadata_and_catalog_leave_no_outputs(self):
        for options in ({"title": "bad\ntitle"}, {"description": "bad\0description"}):
            with self.assertRaises(ValueError):
                self.add(**options)
        self.catalog.write_text("not JSON")
        with self.assertRaises(ValueError):
            self.add()
        self.assertFalse((self.root / "suites/tensor-demo").exists())
        self.assertFalse((self.root / "nix/tensor-demo.nix").exists())
        self.assertEqual(self.catalog.read_text(), "not JSON")
        self.assertFalse((self.root / ".gammabench/new-benchmark.lock").exists())

    def test_catalog_replacement_failure_rolls_back_only_new_outputs(self):
        with patch("gammabench.scaffold.os.replace", side_effect=OSError("simulated publication failure")):
            with self.assertRaisesRegex(OSError, "publication failure"):
                self.add()
        self.assertEqual(self.catalog.read_bytes(), self.original)
        self.assertFalse((self.root / "suites/tensor-demo").exists())
        self.assertFalse((self.root / "nix/tensor-demo.nix").exists())
        self.assertFalse((self.root / ".gammabench/new-benchmark.lock").exists())
        self.add()  # A corrected retry can succeed without clearing partial work.

    def test_concurrent_catalog_edit_is_preserved_and_new_outputs_rolled_back(self):
        original_link = os.link
        changed = self.original + b"\n"

        def link_then_edit(source, destination):
            original_link(source, destination)
            if Path(destination) == self.root / "nix/tensor-demo.nix":
                self.catalog.write_bytes(changed)

        with patch("gammabench.scaffold.os.link", side_effect=link_then_edit):
            with self.assertRaisesRegex(ValueError, "catalog changed"):
                self.add()
        self.assertEqual(self.catalog.read_bytes(), changed)
        self.assertFalse((self.root / "suites/tensor-demo").exists())
        self.assertFalse((self.root / "nix/tensor-demo.nix").exists())
        self.assertFalse((self.root / ".gammabench/new-benchmark.lock").exists())

    def test_active_scaffold_lock_is_preserved(self):
        lock = self.root / ".gammabench/new-benchmark.lock"
        lock.mkdir(parents=True)
        (lock / "owner").write_text("other operation")
        with self.assertRaisesRegex(ValueError, "catalog operation is active"):
            self.add()
        self.assertEqual((lock / "owner").read_text(), "other operation")
        self.assertEqual(self.catalog.read_bytes(), self.original)

    @unittest.skipUnless(shutil.which("rustc"), "Rust compiler required for generated harness integration")
    def test_generated_harness_builds_checks_exact_output_and_rejects_invalid_inputs(self):
        self.add()
        directory = self.root / "suites/tensor-demo"
        executable = directory / "tensor-demo"
        subprocess.run(["rustc", "--edition=2024", "-Copt-level=3", str(directory / "main.rs"),
                        "-o", str(executable)], check=True, capture_output=True, text=True)
        version = subprocess.run([str(executable), "--version"], check=True, capture_output=True, text=True)
        self.assertEqual(version.stdout.strip(), "gammabench-tensor-demo starter 1")
        completed = subprocess.run([str(executable)], cwd=directory, check=True, capture_output=True, text=True)
        result = json.loads(completed.stdout.removeprefix("RESULT "))
        self.assertEqual(result["items"], 100000)
        self.assertEqual(result["total"], 14999950000)
        self.assertEqual(result["validated_output"], {"kind": "u64-affine-sequence", "first": 1, "step": 3, "length": 100000})
        self.assertGreater(result["compute_ns"], 0)
        (directory / "workload.txt").write_text("0\n")
        invalid = subprocess.run([str(executable)], cwd=directory, capture_output=True, text=True)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertNotIn("RESULT ", invalid.stdout)
        invalid = subprocess.run([str(executable), "unexpected"], cwd=directory, capture_output=True, text=True)
        self.assertNotEqual(invalid.returncode, 0)
