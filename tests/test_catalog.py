"""Discovery describes retained workloads and captures without changing evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from gammabench.catalog import build_catalog
from gammabench.metadata import NA, capture_metadata, registry
from gammabench.report import analyze
from gammabench.registry import register_report
from gammabench.schema import SchemaError
import test_report as report_fixtures


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.fixture = report_fixtures.ReportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root / "checkout"
        (self.root / "web").mkdir(parents=True)
        (self.root / "docs/results").mkdir(parents=True)
        (self.root / "flake.nix").write_text("{}\n")
        self.benchmark = {"id": "expression-kernel", "family": "evaluation", "title": "Expression kernel",
                          "description": "An equivalent retained expression batch.", "definition_version": "1",
                          "analyses": [], "reports": [], "default_metric": "completion_ns"}
        self.catalog = {"schema_version": 1, "default_benchmark": "expression-kernel",
                        "families": [{"id": "evaluation", "title": "Evaluation", "status": "captured"}],
                        "subjects": {}, "benchmarks": [self.benchmark]}
        self.write_registry()

    def write_registry(self):
        (self.root / "web/benchmarks.json").write_text(json.dumps(self.catalog))

    def capture(self, *, metadata=True, change=None):
        def prepare(snapshot, suite):
            if metadata:
                suite["metadata"] = {"schema_version": 1,
                    "values": {"topic": ["evaluation"], "purpose": "performance", "operation": "sample-evaluation",
                               "target": "sample-batch", "numeric.precision": "f64"},
                    "cases": {"four-loop": {"physics.loops.amplitude": 4}}}
                for index, task in enumerate(suite["tasks"]):
                    task["metadata"] = {"implementation.way": ("iterative", "summed")[index],
                                        "capture.threads": (1, 8)[index]}
            if change:
                change(snapshot, suite)
            snapshot["metrics"] = copy.deepcopy(suite["metrics"])
            if metadata or "metadata" in suite:
                snapshot["metadata"] = capture_metadata(suite)
        return self.fixture.snapshot(change=prepare)

    def add_report(self, source, *, slug=None):
        data = analyze(source)
        slug = slug or data["snapshots"][0]["id"]
        directory = self.root / "docs/results" / slug
        directory.mkdir()
        (directory / "analysis.json").write_text(json.dumps(data))
        (directory / "index.html").write_text("<!doctype html><title>Local report fixture</title>")
        (directory / "results.csv").write_text("Preserved fixture CSV\n")
        self.benchmark["analyses"].append(f"docs/results/{slug}/analysis.json")
        self.benchmark["reports"].append(f"docs/results/{slug}/index.html")
        self.write_registry()
        return data, directory

    def row(self, result, case="four-loop"):
        return next(row for row in result["cases"] if row["case_id"] == case)

    def test_captures_are_deduplicated_per_task_not_per_metric(self):
        self.add_report(self.capture())
        row = self.row(build_catalog(self.root))
        self.assertEqual(len(row["captures"]), 2)
        self.assertEqual({capture["implementation"]["way_id"] for capture in row["captures"]}, {"iterative", "summed"})
        self.assertTrue(all(set(capture["metrics"]) == {"completion_ns", "process_wall_ns"} for capture in row["captures"]))
        self.assertEqual(row["metadata"]["physics.loops.amplitude"], 4)
        self.assertNotIn("capture.threads", row["metadata"])
        self.assertNotIn("capture.source_revision", row["metadata"])

    def test_effective_task_and_revision_descriptors_stay_on_the_same_capture(self):
        first, _ = self.add_report(self.capture())
        def candidate(snapshot, suite):
            snapshot["created_at"] = "2026-10-15T12:00:00Z"
            snapshot["tools"]["gammaloop"]["revision"] = "b" * 40
            for task in suite["tasks"]:
                task["metadata"]["capture.threads"] = 4
        second, _ = self.add_report(self.capture(change=candidate))
        captures = self.row(build_catalog(self.root))["captures"]
        self.assertEqual(len(captures), 4)
        tuples = {(capture["metadata"]["capture.id"], capture["metadata"]["implementation.way"],
                   capture["metadata"]["capture.threads"], capture["metadata"]["capture.source_revision"])
                  for capture in captures}
        self.assertEqual(tuples, {(first["snapshots"][0]["id"], "iterative", 1, "a" * 40),
                                 (first["snapshots"][0]["id"], "summed", 8, "a" * 40),
                                 (second["snapshots"][0]["id"], "iterative", 4, "b" * 40),
                                 (second["snapshots"][0]["id"], "summed", 4, "b" * 40)})
        self.assertEqual(len({capture["id"] for capture in captures}), 4)

    def test_case_common_metadata_does_not_claim_a_disagreeing_task_override(self):
        def different(snapshot, suite):
            suite["tasks"][1]["metadata"]["physics.loops.amplitude"] = 3
            suite["tasks"][1]["metadata"]["operation"] = "tensor-contraction"
        self.add_report(self.capture(change=different))
        row = self.row(build_catalog(self.root))
        self.assertNotIn("physics.loops.amplitude", row["metadata"])
        self.assertNotIn("operation", row["metadata"])
        self.assertEqual({capture["metadata"]["physics.loops.amplitude"] for capture in row["captures"]}, {3, 4})
        self.assertEqual({capture["metadata"]["operation"] for capture in row["captures"]},
                         {"sample-evaluation", "tensor-contraction"})

    def test_planned_benchmark_without_cases_has_one_honest_placeholder(self):
        self.benchmark["metadata"] = {"schema_version": 1, "values": {"topic": ["builds"], "operation": "workspace-build"}}
        self.write_registry()
        result = build_catalog(self.root)
        self.assertEqual(len(result["cases"]), 1)
        row = result["cases"][0]
        self.assertIsNone(row["case_id"])
        self.assertTrue(row["planned"])
        self.assertEqual(row["captures"], [])
        self.assertNotIn("case.id", row["metadata"])
        self.assertEqual(row["metadata"]["operation"], "workspace-build")
        self.assertNotIn("workspaces", result)

    def test_planned_suite_uses_authoritative_declarations_without_inventing_captures(self):
        source = self.capture()
        directory = self.root / "suites/expression-kernel"
        directory.mkdir(parents=True)
        (directory / "suite.json").write_text((source / "suite.json").read_text())
        self.benchmark["suite"] = "suites/expression-kernel/suite.json"
        self.write_registry()
        row = self.row(build_catalog(self.root))
        self.assertTrue(row["planned"])
        self.assertEqual(row["captures"], [])
        self.assertEqual(row["metadata"]["operation"], "sample-evaluation")
        self.assertEqual(row["metadata"]["physics.loops.amplitude"], 4)
        self.assertNotIn("capture.date", row["metadata"])

    def test_legacy_overlay_is_explicit_and_cannot_replace_captured_known_values(self):
        self.add_report(self.capture(metadata=False))
        self.benchmark["metadata"] = {"schema_version": 1, "values": {"purpose": "regression"},
                                      "cases": {"four-loop": {"physics.loops.amplitude": 4}}}
        self.write_registry()
        row = self.row(build_catalog(self.root))
        self.assertEqual(row["metadata"]["physics.loops.amplitude"], 4)
        self.assertTrue(all(capture["metadata"]["purpose"] == "regression" for capture in row["captures"]))
        self.add_report(self.capture())
        self.benchmark["metadata"]["cases"]["four-loop"]["physics.loops.amplitude"] = 5
        self.write_registry()
        with self.assertRaisesRegex(SchemaError, "conflicts with captured"):
            build_catalog(self.root)

    def test_unknown_null_values_do_not_conflict_and_only_trusted_derived_facts_fill_them(self):
        def unknown(snapshot, suite):
            suite["metadata"]["values"].update({"capture.date": None, "physics.model": None})
        self.add_report(self.capture(change=unknown))
        self.benchmark["metadata"] = {"schema_version": 1, "values": {"physics.model": "QCD"}}
        self.write_registry()
        row = self.row(build_catalog(self.root))
        self.assertIsNone(row["metadata"]["physics.model"])
        self.assertTrue(all(capture["metadata"]["physics.model"] is None for capture in row["captures"]))
        self.assertTrue(all(capture["metadata"]["capture.date"] == "2026-10-14" for capture in row["captures"]))

    def test_declared_measured_revision_requires_retained_source_evidence(self):
        def no_pin(snapshot, suite):
            snapshot["tools"] = {}
        self.add_report(self.capture(metadata=False, change=no_pin))
        self.benchmark["metadata"] = {"schema_version": 1, "values": {"capture.source_revision": "f" * 40}}
        self.write_registry()
        with self.assertRaisesRegex(SchemaError, "derived|source|retained|evidence"):
            build_catalog(self.root)

    def test_planned_suite_resolves_task_workload_metadata_without_fake_captures(self):
        def task_operation(snapshot, suite):
            suite["metadata"]["values"].pop("operation")
            for task in suite["tasks"]:
                task["metadata"]["operation"] = "tensor-contraction"
        source = self.capture(change=task_operation)
        directory = self.root / "suites/expression-kernel"
        directory.mkdir(parents=True)
        (directory / "suite.json").write_text((source / "suite.json").read_text())
        self.benchmark["suite"] = "suites/expression-kernel/suite.json"
        self.write_registry()
        row = self.row(build_catalog(self.root))
        self.assertEqual(row["metadata"]["operation"], "tensor-contraction")
        self.assertTrue(row["planned"])
        self.assertEqual(row["captures"], [])

    def test_registration_accepts_unknown_overlay_without_erasing_captured_known_metadata(self):
        def known(snapshot, suite):
            suite["metadata"]["values"]["physics.model"] = "QCD"
        source = self.capture(change=known)
        directory = self.root / "suites/expression-kernel"
        directory.mkdir(parents=True)
        (directory / "suite.json").write_text((source / "suite.json").read_text())
        self.benchmark["suite"] = "suites/expression-kernel/suite.json"
        self.benchmark["metadata"] = {"schema_version": 1, "values": {"physics.model": None}}
        self.write_registry()
        register_report(source, self.root / "docs/results/registered", "expression-kernel", root=self.root)
        row = self.row(build_catalog(self.root))
        self.assertEqual(row["metadata"]["physics.model"], "QCD")
        self.assertTrue(all(capture["metadata"]["physics.model"] == "QCD" for capture in row["captures"]))

    def test_registration_accepts_equivalent_quantity_units_without_rewriting_values(self):
        def duration(snapshot, suite):
            suite["metadata"]["values"]["capture.duration"] = {"value": 500, "unit": "ms"}
        source = self.capture(change=duration)
        directory = self.root / "suites/expression-kernel"
        directory.mkdir(parents=True)
        (directory / "suite.json").write_text((source / "suite.json").read_text())
        self.benchmark["suite"] = "suites/expression-kernel/suite.json"
        self.benchmark["metadata"] = {"schema_version": 1, "values": {"capture.duration": {"value": .5, "unit": "s"}}}
        self.write_registry()
        register_report(source, self.root / "docs/results/registered-units", "expression-kernel", root=self.root)
        captures = self.row(build_catalog(self.root))["captures"]
        self.assertTrue(all(capture["metadata"]["capture.duration"] == {"value": 500, "unit": "ms"} for capture in captures))
        retained = json.loads((source / "snapshot.json").read_text())
        self.assertEqual(retained["metadata"]["values"]["capture.duration"], {"value": 500, "unit": "ms"})

    def test_authored_catalog_overlay_cannot_override_controlled_values_with_frozen_definitions(self):
        definitions = registry()["fields"]
        definitions["operation"]["values"].append("uncontrolled-operation")
        self.benchmark["metadata"] = {"schema_version": 1, "definitions": definitions,
                                      "values": {"operation": "uncontrolled-operation"}}
        self.write_registry()
        before = (self.root / "web/benchmarks.json").read_bytes()
        with self.assertRaisesRegex(SchemaError, "cannot override.*controlled"):
            build_catalog(self.root)
        self.assertEqual((self.root / "web/benchmarks.json").read_bytes(), before)

    def test_not_applicable_is_not_overwritten_by_a_known_overlay(self):
        def physics_na(snapshot, suite):
            suite["metadata"]["cases"]["four-loop"]["physics.loops.amplitude"] = NA
        self.add_report(self.capture(change=physics_na))
        self.benchmark["metadata"] = {"schema_version": 1, "cases": {"four-loop": {"physics.loops.amplitude": 0}}}
        self.write_registry()
        with self.assertRaisesRegex(SchemaError, "conflicts with captured"):
            build_catalog(self.root)

    def test_frozen_enums_survive_current_vocabulary_label_and_option_additions(self):
        original, _ = self.add_report(self.capture())
        frozen = copy.deepcopy(original["snapshots"][0]["metadata"])
        evolved = registry()
        evolved["vocabulary_version"] = 2
        evolved["fields"]["operation"]["label"] = "New operation label"
        evolved["fields"]["operation"]["values"].append("future-operation")
        with patch("gammabench.metadata.registry", return_value=evolved), patch("gammabench.catalog.registry", return_value=evolved):
            result = build_catalog(self.root, include_workspaces=True)
        operation = next(field for field in result["fields"] if field["id"] == "operation")
        self.assertEqual(operation["label"], "New operation label")
        self.assertIn("future-operation", operation["values"])
        self.assertEqual(result["workspaces"][0]["analyses"][0]["snapshots"][0]["metadata"], frozen)
        self.assertEqual(self.row(result)["metadata"]["operation"], "sample-evaluation")

    def test_availability_and_task_failures_keep_their_specific_statuses(self):
        def statuses(snapshot, suite):
            snapshot["status"] = "failed"
            snapshot["schedule"]["unsupported"] = [{"case": "unsupported-case", "engine": "other-tool", "variant": "native",
                                                        "status": "unsupported", "reason": "No equivalent operation"}]
            for record in snapshot["records"]:
                if record["task"] == "candidate-task" and record["phase"] == "measure":
                    record.update(status="timeout", metrics={})
        self.add_report(self.capture(change=statuses))
        result = build_catalog(self.root)
        captures = self.row(result)["captures"]
        self.assertEqual({capture["implementation"]["way_id"]: capture["metadata"]["capture.status"] for capture in captures},
                         {"iterative": "completed", "summed": "timeout"})
        unsupported = self.row(result, "unsupported-case")["captures"][0]
        self.assertEqual(unsupported["metadata"]["capture.status"], "unsupported")
        self.assertEqual(unsupported["reason"], "No equivalent operation")

    def source_case(self, *, missing_workload=False):
        path = self.root / "fixtures/case.json"
        path.parent.mkdir()
        path.write_text('{"loops":4}\n')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        def inputs(snapshot, suite):
            for record in snapshot["records"]:
                if missing_workload:
                    record.pop("inputs_sha256")
                else:
                    record["inputs_sha256"]["case"] = digest
        self.add_report(self.capture(metadata=False, change=inputs))
        self.benchmark["metadata_sources"] = {"four-loop": {"path": "fixtures/case.json", "sha256": digest, "input": "case"}}
        self.benchmark["metadata"] = {"schema_version": 1, "cases": {"four-loop": {"physics.loops.amplitude": 4}}}
        self.write_registry()
        return path, digest

    def test_legacy_metadata_sources_require_both_file_and_retained_input_hash(self):
        path, digest = self.source_case()
        row = self.row(build_catalog(self.root))
        self.assertEqual(row["metadata_source"]["sha256"], digest)
        path.write_text('{"loops":5}\n')
        with self.assertRaisesRegex(SchemaError, "Metadata source changed"):
            build_catalog(self.root)
        self.benchmark["metadata_sources"]["four-loop"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write_registry()
        with self.assertRaisesRegex(SchemaError, "does not match retained inputs"):
            build_catalog(self.root)

    def test_missing_workload_source_proof_is_a_schema_error_not_an_attribute_error(self):
        self.source_case(missing_workload=True)
        with self.assertRaisesRegex(SchemaError, "does not match retained inputs"):
            build_catalog(self.root)

    def test_source_identity_uses_engine_revision_without_invented_threads_or_private_metadata(self):
        secret = "PRIVATE-CATALOG-TEST-SENTINEL"
        def evidence(snapshot, suite):
            snapshot["tools"] = {task: {"kind": "historical", "engine_revision": revision,
                                        "revision": "c" * 40, "unrelated_private_field": secret}
                                 for task, revision in (("baseline-task", "a" * 40), ("candidate-task", "b" * 40))}
            snapshot["host"] = {"cpu": "test CPU", "threads": 32, "unrelated_private_field": secret}
            snapshot["provenance"] = {"ambient_environment": {"RAYON_NUM_THREADS": "32", "PRIVATE_LICENSE": secret}}
        self.add_report(self.capture(metadata=False, change=evidence))
        self.benchmark["metadata"] = {"schema_version": 1, "values": {"implementation.way": "default"}}
        self.write_registry()
        with patch.dict(os.environ, {"PRIVATE_LICENSE": secret, "RAYON_NUM_THREADS": "32"}):
            result = build_catalog(self.root)
        captures = self.row(result)["captures"]
        self.assertEqual({capture["implementation"]["revision"] for capture in captures}, {"a" * 40, "b" * 40})
        self.assertTrue(all("capture.threads" not in capture["metadata"] for capture in captures))
        self.assertNotIn(secret, json.dumps(result))
        self.assertTrue(all(capture["metadata"]["implementation.way"] == "default" for capture in captures))

    def test_workspace_projection_preserves_all_statistics_and_never_writes_reports(self):
        source = self.capture()
        original, directory = self.add_report(source)
        before = {path: path.read_bytes() for root in (directory, source) for path in root.rglob("*") if path.is_file()}
        result = build_catalog(self.root, include_workspaces=True)
        projected = result["workspaces"][0]["analyses"][0]
        self.assertEqual(projected["comparisons"], original["comparisons"])
        self.assertEqual(projected["availability"], original["availability"])
        self.assertEqual(projected["snapshots"], original["snapshots"])
        for captured, rendered in zip(original["series"], projected["series"]):
            for key, value in captured.items():
                if key not in ("metadata", "implementation"):
                    self.assertEqual(rendered[key], value, key)
        self.assertTrue(all(path.read_bytes() == value for path, value in before.items()))


if __name__ == "__main__":
    unittest.main()
