"""Registration publishes portable evidence without replacing existing work."""
import copy
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from gammabench.registry import plan_benchmark, register_report, registry_lock, validate_registry
from gammabench.schema import SchemaError
from gammabench.snapshot import load, verify
import gammabench.registry as registry_module
import test_report as report_fixtures


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = report_fixtures.ReportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.source = self.fixture.snapshot()
        self.repo = self.fixture.root / "checkout"
        for relative in ("web", "nix", "suites/contract-test", "docs/results"):
            (self.repo / relative).mkdir(parents=True)
        (self.repo / "flake.nix").write_text("{}\n")
        (self.repo / "nix/contract-test.nix").write_text("{}\n")
        shutil.copyfile(self.source / "suite.json", self.repo / "suites/contract-test/suite.json")
        self.base = {
            "schema_version": 1, "default_benchmark": "existing",
            "families": [{"id": "examples", "title": "Examples", "status": "planned"}],
            "subjects": {}, "benchmarks": [{
                "id": "existing", "family": "examples", "title": "Existing", "description": "Existing definition",
                "definition_version": "1", "analyses": [], "reports": [], "default_metric": "compute_ns",
            }],
        }
        self.registry = plan_benchmark(self.base, "contract-test", family="examples", title="Contract test",
                                       description="Qualified report fixture", nix_recipe="nix/contract-test.nix",
                                       suite="suites/contract-test/suite.json", default_metric="completion_ns")
        self.catalog = self.repo / "web/benchmarks.json"
        self.catalog.write_text(json.dumps(self.registry))
        self.original = self.catalog.read_bytes()
        self.source_bytes = self.contents(self.source)
        self.report = self.repo / "docs/results/first"
        self.archive = self.repo / "snapshots/measured" / load(self.source)["id"]

    @staticmethod
    def contents(directory):
        return {path.relative_to(directory).as_posix(): path.read_bytes()
                for path in directory.rglob("*") if path.is_file()}

    def publish(self, source=None, output=None):
        return register_report([source or self.source], output or self.report, "contract-test", root=self.repo)

    def assert_pristine(self):
        self.assertEqual(self.catalog.read_bytes(), self.original)
        self.assertEqual(self.contents(self.source), self.source_bytes)
        self.assertFalse(self.report.exists())
        self.assertFalse(self.archive.exists())
        self.assertFalse((self.repo / ".gammabench/new-benchmark.lock").exists())
        self.assertFalse(list((self.repo / "docs/results").glob(".report-*")))

    def test_planning_retains_the_existing_default_and_accepts_an_empty_catalog(self):
        before = copy.deepcopy(self.base)
        self.assertEqual(self.registry["default_benchmark"], "existing")
        self.assertEqual(self.registry["benchmarks"][0], self.base["benchmarks"][0])
        self.assertEqual(self.base, before)
        empty = {**self.base, "default_benchmark": None, "benchmarks": [], "families": []}
        planned = plan_benchmark(empty, "new-operation", family="algebra", title="Algebra",
                                 description="A planned operation", family_title="Symbolic algebra")
        self.assertEqual(planned["default_benchmark"], "new-operation")
        self.assertEqual(planned["benchmarks"][0]["analyses"], [])
        self.assertEqual(planned["benchmarks"][0]["reports"], [])
        self.assertEqual(planned["families"], [{"id": "algebra", "title": "Symbolic algebra", "status": "planned"}])

    def test_duplicate_unsafe_and_reserved_registrations_preserve_the_input(self):
        original = copy.deepcopy(self.registry)
        for identifier in ("contract-test", "../other", "a/b", "a name", "Upper", "default", "rust-sort", "bench-other"):
            with self.subTest(identifier=identifier), self.assertRaises(SchemaError):
                plan_benchmark(self.registry, identifier, family="examples", title="Test", description="Test")
        for recipe, suite in (("nix/../other.nix", "suites/a.json"),
                              ("/tmp/other.nix", "suites/a.json"),
                              ("nix/other.nix", "suites/../private.json")):
            with self.subTest(recipe=recipe, suite=suite), self.assertRaises(SchemaError):
                plan_benchmark(self.registry, "other", family="examples", title="Test", description="Test",
                               nix_recipe=recipe, suite=suite)
        self.assertEqual(self.registry, original)
        duplicate = copy.deepcopy(self.registry)
        duplicate["benchmarks"].append(copy.deepcopy(duplicate["benchmarks"][0]))
        with self.assertRaisesRegex(SchemaError, "Duplicate benchmark"):
            validate_registry(duplicate)

    def test_internal_publication_symlinks_are_rejected_without_touching_their_targets(self):
        for relative in ('snapshots', 'snapshots/measured', 'docs/results'):
            with self.subTest(relative=relative):
                path = self.repo / relative
                target = self.repo / ('internal-' + relative.replace('/', '-'))
                target.mkdir()
                if path.exists():
                    path.rmdir()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(target, target_is_directory=True)
                with self.assertRaisesRegex(SchemaError, 'symlinks'):
                    self.publish()
                self.assertTrue(path.is_symlink())
                self.assertEqual(list(target.iterdir()), [])
                path.unlink()
                path.mkdir()
                target.rmdir()
        self.assert_pristine()

    def test_real_report_archives_immutable_sources_and_relocates_with_its_evidence(self):
        result = self.publish()
        self.assertEqual(result["status"], "registered")
        self.assertEqual(self.contents(self.source), self.source_bytes)
        self.assertEqual(self.contents(self.archive), self.source_bytes)
        self.assertEqual(verify(self.archive), verify(self.source))
        registry = json.loads(self.catalog.read_text())
        self.assertEqual(registry["default_benchmark"], "existing")
        self.assertEqual(registry["benchmarks"][0], self.base["benchmarks"][0])
        entry = registry["benchmarks"][-1]
        self.assertEqual(entry["analyses"], ["docs/results/first/analysis.json"])
        self.assertEqual(entry["reports"], ["docs/results/first/index.html"])
        self.assertEqual(registry["families"][0]["status"], "captured")
        analysis = json.loads((self.report / "analysis.json").read_text())
        self.assertEqual(Path(analysis["snapshots"][0]["path"]), self.archive)
        self.assertEqual(analysis["series"][0]["median"], 20)
        moved = self.fixture.root / "relocated-checkout"
        self.repo.rename(moved)
        report = moved / "docs/results/first"
        presentation = json.loads((report / "presentation.json").read_text())
        for artifact in presentation["snapshots"][load(self.source)["id"]]["artifacts"]:
            target = (report / unquote(artifact["href"])).resolve()
            self.assertTrue(target.is_relative_to(moved), target)
            self.assertTrue(target.is_file(), target)
        checked = 0
        elements = report_fixtures.ReportElements((report / "index.html").read_text())
        for _, attributes in elements.elements:
            for field in ("href", "src"):
                url = urlsplit(attributes.get(field, ""))
                if url.scheme or url.netloc or not url.path:
                    continue
                target = (report / unquote(url.path)).resolve()
                self.assertTrue(target.is_relative_to(moved), target)
                self.assertTrue(target.exists(), target)
                checked += 1
        self.assertGreater(checked, 5)

    def test_a_later_capture_appends_without_rewriting_prior_evidence(self):
        self.publish()
        previous_report = self.contents(self.report)
        previous_archive = self.contents(self.archive)
        second = self.fixture.snapshot(a=(20, 30, 40), b=(10, 15, 20))
        original_second = self.contents(second)
        second_report = self.repo / "docs/results/second"
        self.publish(source=second, output=second_report)
        entry = json.loads(self.catalog.read_text())["benchmarks"][-1]
        self.assertEqual(entry["analyses"], ["docs/results/first/analysis.json", "docs/results/second/analysis.json"])
        self.assertEqual(entry["reports"], ["docs/results/first/index.html", "docs/results/second/index.html"])
        self.assertEqual(self.contents(self.report), previous_report)
        self.assertEqual(self.contents(self.archive), previous_archive)
        self.assertEqual(self.contents(second), original_second)

    def test_duplicate_snapshot_registration_preserves_every_published_byte(self):
        self.publish()
        before = self.contents(self.repo)
        with self.assertRaisesRegex(SchemaError, "already registered"):
            self.publish(output=self.repo / "docs/results/duplicate")
        self.assertEqual(self.contents(self.repo), before)
        self.assertEqual(self.contents(self.source), self.source_bytes)

    def test_a_capture_cannot_anchor_two_benchmark_workspaces(self):
        directory = self.repo / 'docs/results/existing'
        directory.mkdir()
        (directory / 'analysis.json').write_text(json.dumps({'snapshots': [{'id': load(self.source)['id']}]}))
        catalog = copy.deepcopy(self.registry)
        catalog['benchmarks'][0]['analyses'] = ['docs/results/existing/analysis.json']
        catalog['benchmarks'][0]['reports'] = ['docs/results/existing/index.html']
        self.catalog.write_text(json.dumps(catalog))
        before = self.contents(self.repo)
        with self.assertRaisesRegex(SchemaError, 'anchors another benchmark'):
            self.publish()
        self.assertEqual(self.contents(self.repo), before)
        self.assertFalse(self.archive.exists())

    def test_failed_and_unqualified_captures_remain_registered_evidence(self):
        def fail_candidate(snapshot, suite):
            snapshot["validation"]["tasks"]["candidate-task"].update(status="failed", reason="Exact output differs")
            for record in snapshot["records"]:
                if record["task"] == "candidate-task" and record["phase"] == "measure":
                    record.update(status="failed", returncode=1, metrics={})

        failed = self.fixture.snapshot(change=fail_candidate)
        original = self.contents(failed)
        self.publish(source=failed)
        analysis = json.loads((self.report / "analysis.json").read_text())
        candidate = next(item for item in analysis["series"]
                         if item["task"] == "candidate-task" and item["metric"] == "completion_ns")
        self.assertEqual(candidate["count"], 0)
        self.assertIsNone(candidate["median"])
        self.assertEqual(candidate["statuses"], {"failed": 3})
        self.assertEqual(candidate["validation_status"], "failed")
        self.assertTrue(analysis["availability"])
        self.assertTrue(all(pair["status"] == "suppressed" for pair in analysis["comparisons"]))
        self.assertEqual(self.contents(failed), original)

    def test_wrong_suite_and_absent_default_metric_fail_before_publication(self):
        suite_path = self.repo / "suites/contract-test/suite.json"
        suite = json.loads(suite_path.read_text())
        suite["id"] = "another-suite"
        suite_path.write_text(json.dumps(suite))
        with self.assertRaisesRegex(SchemaError, "suite ID"):
            self.publish()
        shutil.copyfile(self.source / "suite.json", suite_path)
        catalog = copy.deepcopy(self.registry)
        catalog["benchmarks"][-1]["default_metric"] = "absent_ns"
        self.catalog.write_text(json.dumps(catalog))
        with self.assertRaisesRegex(SchemaError, "default metric"):
            self.publish()
        self.catalog.write_bytes(self.original)
        self.assert_pristine()

    def test_changed_contract_requires_a_new_benchmark_id_even_when_suite_id_matches(self):
        def change_contract(snapshot, suite):
            suite["contracts"]["full"]["included"].append("aggregate multiplication")
            suite["contracts"]["full"]["excluded"] = []
            snapshot["contracts"] = copy.deepcopy(suite["contracts"])

        changed = self.fixture.snapshot(change=change_contract)
        before = self.contents(changed)
        with self.assertRaisesRegex(SchemaError, "[Nn]ew benchmark ID"):
            self.publish(source=changed)
        self.assertEqual(self.contents(changed), before)
        self.assertFalse((self.repo / "snapshots/measured" / load(changed)["id"]).exists())
        self.assert_pristine()

    def test_changed_metric_scope_is_rejected_against_the_first_retained_definition(self):
        self.publish()
        catalog = json.loads(self.catalog.read_text())
        catalog["benchmarks"][-1].pop("suite")
        catalog["benchmarks"][-1].pop("nix_recipe")
        self.catalog.write_text(json.dumps(catalog))
        before = self.contents(self.repo)

        def change_scope(snapshot, suite):
            suite["metrics"]["completion_ns"]["scope"] = "generation plus aggregate multiplication"
            snapshot["metrics"] = copy.deepcopy(suite["metrics"])

        changed = self.fixture.snapshot(change=change_scope)
        with self.assertRaisesRegex(SchemaError, "[Nn]ew benchmark ID"):
            self.publish(source=changed, output=self.repo / "docs/results/changed")
        self.assertEqual(self.contents(self.repo), before)
        self.assertFalse((self.repo / "snapshots/measured" / load(changed)["id"]).exists())

    def test_existing_destinations_and_escaping_report_paths_are_preserved(self):
        for output in (self.repo / "results/report", self.repo / "docs/results", self.fixture.root / "outside-report"):
            with self.subTest(output=output), self.assertRaises((SchemaError, FileExistsError)):
                self.publish(output=output)
        self.report.mkdir()
        (self.report / "notes").write_text("user work")
        with self.assertRaises(FileExistsError):
            self.publish()
        self.assertEqual((self.report / "notes").read_text(), "user work")
        shutil.rmtree(self.report)
        self.report.symlink_to(self.fixture.root / "missing-report", target_is_directory=True)
        with self.assertRaises(FileExistsError):
            self.publish()
        self.assertTrue(self.report.is_symlink())
        self.assertFalse((self.fixture.root / "missing-report").exists())
        self.report.unlink()
        outside = self.fixture.root / "outside"
        outside.mkdir()
        alias = self.repo / "docs/results/alias"
        alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(SchemaError, "docs/results"):
            self.publish(output=alias / "report")
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_pristine()

    def test_existing_snapshot_id_with_different_evidence_is_never_replaced(self):
        identity = load(self.source)["id"]
        external = self.fixture.root / "external-source"
        self.source.rename(external)
        self.source = external
        different = self.fixture.snapshot(a=(100, 200, 300), identity=identity)
        self.archive.parent.mkdir(parents=True)
        different.rename(self.archive)
        original_archive = self.contents(self.archive)
        with self.assertRaisesRegex(SchemaError, "different evidence"):
            self.publish()
        self.assertEqual(self.contents(self.archive), original_archive)
        self.assertEqual(self.contents(self.source), self.source_bytes)
        self.assertEqual(self.catalog.read_bytes(), self.original)
        self.assertFalse(self.report.exists())

    def test_failed_render_removes_only_new_outputs_and_retains_existing_snapshots(self):
        unrelated = self.repo / "docs/results/user-work"
        unrelated.mkdir()
        (unrelated / "notes").write_text("keep")

        def fail_render(paths, output, **options):
            output.mkdir()
            (output / "partial").write_text("unfinished")
            raise ValueError("simulated renderer failure")

        with patch("gammabench.report.render", side_effect=fail_render):
            with self.assertRaisesRegex(ValueError, "renderer failure"):
                self.publish()
            self.assert_pristine()
            self.archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(self.source, self.archive)
            before = self.contents(self.archive)
            with self.assertRaisesRegex(ValueError, "renderer failure"):
                self.publish()
            self.assertEqual(self.contents(self.archive), before)
        self.assertEqual((unrelated / "notes").read_text(), "keep")
        self.assertEqual(self.catalog.read_bytes(), self.original)
        self.assertFalse(self.report.exists())

    def test_failed_evidence_copy_removes_the_partially_created_owned_archive(self):
        def fail_copy(source, destination, **options):
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "partial").write_text("unfinished")
            raise OSError("simulated copy failure")

        with patch("gammabench.registry.shutil.copytree", side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, "copy failure"):
                self.publish()
        self.assert_pristine()

    def test_manual_catalog_change_rolls_back_new_outputs_and_preserves_the_edit(self):
        writer = registry_module.write_registry
        manual = self.original + b"\n"

        def change_then_write(root, registry, expected):
            self.catalog.write_bytes(manual)
            writer(root, registry, expected)

        with patch("gammabench.registry.write_registry", side_effect=change_then_write):
            with self.assertRaisesRegex(SchemaError, "changed during"):
                self.publish()
        self.assertEqual(self.catalog.read_bytes(), manual)
        self.assertEqual(self.contents(self.source), self.source_bytes)
        self.assertFalse(self.report.exists())
        self.assertFalse(self.archive.exists())
        self.assertFalse(list((self.repo / "docs/results").glob(".report-*")))

    def test_shared_active_catalog_lock_prevents_registration_without_removing_the_owner(self):
        with registry_lock(self.repo):
            with self.assertRaisesRegex(SchemaError, "operation is active"):
                self.publish()
            self.assertTrue((self.repo / ".gammabench/new-benchmark.lock").is_dir())
        self.assert_pristine()

    def test_external_catalog_symlink_is_not_replaced_and_new_outputs_are_rolled_back(self):
        external = self.fixture.root / "external-catalog.json"
        self.catalog.rename(external)
        self.catalog.symlink_to(external)
        with self.assertRaisesRegex(SchemaError, "registry must remain inside"):
            self.publish()
        self.assertTrue(self.catalog.is_symlink())
        self.assertEqual(external.read_bytes(), self.original)
        self.assertEqual(self.contents(self.source), self.source_bytes)
        self.assertFalse(self.report.exists())
        self.assertFalse(self.archive.exists())
