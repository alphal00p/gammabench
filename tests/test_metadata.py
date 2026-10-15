"""Typed discovery metadata never substitutes for captured scientific identity."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from gammabench.metadata import (NA, capture_metadata, field_definitions, metadata_values,
    project_implementation, registry, same_value, semantic_metadata, validate_metadata, validate_values)
from gammabench.report import analyze
from gammabench.schema import SchemaError, validate_suite
from gammabench.snapshot import load, seal, staging_directory
import test_report as report_fixtures
from test_history_context import nix_capture
import test_variants as variant_fixtures
from gammabench.variants import assemble
from gammabench.runner import run
from test_schema import example_suite
import test_registry as registry_fixtures


def declaration():
    return {"schema_version": 1, "values": {"topic": ["evaluation"], "purpose": "performance",
            "operation": "sample-evaluation", "target": "sample-batch", "numeric.precision": "f64"},
            "cases": {"four-loop": {"physics.loops.amplitude": 4, "numeric.batch_size": 32}}}


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.fixture = report_fixtures.ReportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def capture(self, *, change=None):
        def prepare(snapshot, suite):
            suite["metadata"] = declaration()
            suite["tasks"][0]["metadata"] = {"implementation.way": "iterative", "implementation.way_label": "Iterative"}
            suite["tasks"][1]["metadata"] = {"implementation.way": "summed", "implementation.way_label": "Summed function map"}
            if change:
                change(snapshot, suite)
            snapshot["metrics"] = copy.deepcopy(suite["metrics"])
            snapshot["metadata"] = capture_metadata(suite)
        return self.fixture.snapshot(change=prepare)

    def test_controlled_values_and_namespaced_extensions_are_validated(self):
        definitions = field_definitions()
        self.assertIn("numerators", definitions["topic"]["values"])
        self.assertIn("representative", definitions["purpose"]["values"])
        self.assertIn("integrand-generation", definitions["operation"]["values"])
        for values in ({"operation": "another-spelling"}, {"new_field": 3}, {"topic": ["uv", "uv"]}):
            with self.subTest(values=values), self.assertRaises(SchemaError):
                validate_values(values, definitions)
        extension = {"schema_version": 1, "fields": {"x.demo.count": {
            "label": "Demo count", "type": "number", "scope": "workload", "semantic": True,
            "integer": True, "min": 0}}, "values": {"x.demo.count": 0}}
        self.assertEqual(validate_metadata(extension), extension)
        extension["fields"]["operation"] = extension["fields"].pop("x.demo.count")
        with self.assertRaisesRegex(SchemaError, "Extension fields"):
            validate_metadata(extension)

    def test_types_units_zero_unknown_and_not_applicable_remain_distinct(self):
        definitions = field_definitions({"fields": {"x.demo.enabled": {
            "label": "Enabled", "type": "boolean", "scope": "workload", "semantic": True}}})
        values = {"physics.loops.amplitude": 0, "physics.model": NA, "physics.kind": None,
                  "x.demo.enabled": False, "capture.date": "2026-10-15",
                  "capture.duration": {"value": 500, "unit": "ms"}}
        self.assertEqual(validate_values(values, definitions), values)
        for name, value in [("physics.loops.amplitude", True), ("physics.loops.amplitude", -1),
                            ("physics.loops.amplitude", 4.5), ("capture.date", "2026-02-30"),
                            ("capture.date", "2026-10-15T00:00:00Z"), ("x.demo.enabled", 0),
                            ("capture.duration", {"value": 1, "unit": "days"}),
                            ("capture.duration", {"value": float("inf"), "unit": "s"}),
                            ("physics.model", {"state": "not-applicable", "extra": "bad"})]:
            with self.subTest(field=name, value=value), self.assertRaises(SchemaError):
                validate_values({name: value}, definitions)
        with self.assertRaisesRegex(SchemaError, "wrong scope"):
            validate_values({"capture.date": "2026-10-15"}, definitions, "workload")

    def test_suite_case_task_and_metric_metadata_resolve_without_mutation(self):
        path = self.capture(change=lambda snapshot, suite: suite["metrics"]["completion_ns"].update(
            metadata={"operation": "tensor-contraction"}))
        snapshot = load(path)
        before = copy.deepcopy(snapshot)
        values = metadata_values(snapshot["metadata"], case="four-loop", task="baseline-task", metric="completion_ns")
        self.assertEqual(values["operation"], "tensor-contraction")
        self.assertEqual(values["physics.loops.amplitude"], 4)
        self.assertEqual(values["implementation.way"], "iterative")
        analysis = analyze(path)
        item = analysis["series"][0]
        self.assertEqual(item["metadata"], values)
        self.assertEqual(item["implementation"]["tool_id"], "linnet")
        self.assertEqual(item["implementation"]["way_id"], "iterative")
        self.assertEqual(item["implementation"]["revision"], "a" * 40)
        self.assertEqual(analysis["snapshots"][0]["metadata"], snapshot["metadata"])
        self.assertEqual(load(path), before)

    def test_unknown_case_task_metric_and_core_overrides_fail_before_capture(self):
        path = self.capture()
        suite = json.loads((path / "suite.json").read_text())
        for mutate in (lambda value: value["metadata"]["cases"].update(missing={"physics.model": "QCD"}),
                       lambda value: value["metadata"].update(tasks={"missing": {"target": "kernel"}}),
                       lambda value: value["metadata"].update(metrics={"missing": {"target": "kernel"}}),
                       lambda value: value["metadata"].update(definitions=field_definitions()),
                       lambda value: value["tasks"][0].update(metadata={"physics.loops.amplitude": "4"})):
            changed = copy.deepcopy(suite)
            mutate(changed)
            with self.assertRaises(SchemaError):
                validate_suite(changed)

    def test_metadata_free_snapshots_keep_original_analysis_shape(self):
        analysis = analyze(self.fixture.snapshot())
        self.assertTrue(all("metadata" not in snapshot for snapshot in analysis["snapshots"]))
        self.assertTrue(all(not {"metadata", "metadata_semantics", "implementation"} & item.keys() for item in analysis["series"]))

    def test_later_vocabulary_additions_do_not_rewrite_or_invalidate_old_snapshots(self):
        path = self.capture()
        snapshot = load(path)
        before = copy.deepcopy(snapshot["metadata"])
        evolved = registry()
        evolved["vocabulary_version"] = 2
        evolved["fields"]["operation"]["values"].append("future-operation")
        evolved["fields"]["operation"]["label"] = "Updated operation label"
        evolved["fields"]["x.new.field"] = {"label": "Future field", "type": "text", "scope": "workload", "semantic": False}
        with patch("gammabench.metadata.registry", return_value=evolved):
            self.assertEqual(load(path)["metadata"], before)
            self.assertEqual(analyze(path)["snapshots"][0]["metadata"], before)
        self.assertEqual(load(path)["metadata"], before)

    def test_runner_freezes_declared_metadata_before_measurements(self):
        suite = example_suite()
        suite["metadata"] = {"schema_version": 1, "values": {"operation": "integer-sum", "purpose": "illustrative"},
                             "cases": {"sum": {"physics.model": NA}}}
        suite["tasks"][0]["metadata"] = {"implementation.way": "direct"}
        path = self.fixture.root / "runnable-suite.json"
        path.write_text(json.dumps(suite))
        result = run(path, self.fixture.root / "actual-run", repeats=1, warmups=0, backend="python")
        captured = load(result)
        self.assertEqual(captured["metadata"], capture_metadata(validate_suite(suite)))
        self.assertEqual(analyze(result)["series"][0]["metadata"]["operation"], "integer-sum")

    def test_registration_rejects_semantic_changes_and_conflicting_overlays(self):
        fixture = registry_fixtures.RegistryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        path = self.capture()
        definition = json.loads((path / "suite.json").read_text())
        expected = copy.deepcopy(definition)
        expected["metadata"]["values"]["operation"] = "integration"
        suite_path = fixture.repo / "suites/contract-test/suite.json"
        suite_path.write_text(json.dumps(expected))
        with self.assertRaisesRegex(SchemaError, "semantic metadata changed"):
            fixture.publish(source=path)
        suite_path.write_text(json.dumps(definition))
        catalog = copy.deepcopy(fixture.registry)
        catalog["benchmarks"][-1]["metadata"] = {"schema_version": 1, "values": {"physics.model": "contradictory"}}
        # A known captured value, including N/A, is not replaced by a curated overlay.
        def known(snapshot, suite):
            suite["metadata"]["values"]["physics.model"] = "QCD"
        known_path = self.capture(change=known)
        suite_path.write_text((known_path / "suite.json").read_text())
        fixture.catalog.write_text(json.dumps(catalog))
        with self.assertRaisesRegex(SchemaError, "conflicts with captured"):
            fixture.publish(source=known_path)
        self.assertFalse(fixture.report.exists())
        self.assertFalse((fixture.repo / "snapshots/measured").exists())

    def test_snapshot_metadata_must_match_retained_suite_declarations(self):
        path = self.capture()
        snapshot = load(path)
        suite = json.loads((path / "suite.json").read_text())
        snapshot["metadata"]["values"]["operation"] = "integration"
        target = self.fixture.root / "invalid-metadata"
        with self.assertRaisesRegex(SchemaError, "retain the suite metadata"):
            seal(staging_directory(target), target, snapshot, suite)

    def test_semantic_metadata_changes_suppress_ratios_but_labels_do_not(self):
        def operation(snapshot, suite):
            suite["tasks"][1]["metadata"]["operation"] = "integration"
        analysis = analyze(self.capture(change=operation))
        self.assertTrue(all(pair["status"] == "suppressed" for pair in analysis["comparisons"]))
        self.assertTrue(all(any("Semantic" in reason for reason in pair["reasons"]) for pair in analysis["comparisons"]))
        analysis = analyze(self.capture())
        self.assertTrue(all(pair["status"] == "comparable" for pair in analysis["comparisons"]))
        self.assertNotEqual(analysis["series"][0]["implementation"]["way_id"], analysis["series"][2]["implementation"]["way_id"])

    def test_semantic_quantities_use_declared_units_and_definition_types(self):
        definition = {"label": "Input time", "type": "quantity", "scope": "workload", "semantic": True,
                      "unit": "s", "units": [{"id": "s", "label": "s", "factor": 1}, {"id": "ms", "label": "ms", "factor": .001}]}
        definitions = {"x.demo.time": definition}
        a = semantic_metadata({"x.demo.time": {"value": 500, "unit": "ms"}}, definitions)
        b = semantic_metadata({"x.demo.time": {"value": .5, "unit": "s"}}, definitions)
        self.assertEqual(a, b)
        self.assertTrue(same_value("x.demo.time", {"value": 500, "unit": "ms"}, {"value": .5, "unit": "s"}, definitions))
        self.assertFalse(same_value("x.demo.time", None, NA, definitions))
        self.assertNotEqual(semantic_metadata({"x.demo.time": NA}, definitions), semantic_metadata({"x.demo.time": None}, definitions))

    def test_implementation_labels_never_supply_or_change_source_identity(self):
        analysis = analyze(self.fixture.snapshot())
        snapshot, item = analysis["snapshots"][0], analysis["series"][0]
        snapshot["tools"] = {item["task"]: {"kind": "historical", "engine_revision": "b" * 40, "revision": "c" * 40}}
        a = project_implementation(snapshot, item, values={"implementation.way": "native"})
        b = project_implementation(snapshot, item, values={"implementation.way": "native", "implementation.version_label": "Friendly"})
        self.assertEqual(a["revision"], "b" * 40)
        self.assertEqual(a["version_id"], b["version_id"])
        self.assertEqual(b["version_label"], "Friendly")
        self.assertNotIn("c" * 40, a["version_label"])
        snapshot["tools"][item["task"]].update(kind="archive", revision="v1", engine_revision=None, version="v1")
        archived = project_implementation(snapshot, item)
        self.assertIsNone(archived["revision"])
        self.assertEqual(archived["repository"], "")

    def test_archive_without_version_never_displays_a_stringified_null(self):
        analysis = analyze(self.fixture.snapshot())
        snapshot, item = analysis["snapshots"][0], analysis["series"][0]
        for version in (None, ""):
            snapshot["tools"] = {item["task"]: {"kind": "archive", "version": version, "archive_sha256": "f" * 64}}
            implementation = project_implementation(snapshot, item)
            self.assertEqual(implementation["version_label"], "Archive / version unknown")
            self.assertIsNone(implementation["revision"])
            self.assertEqual(implementation["repository"], "")

    def test_historical_profile_sources_use_unique_explicit_mapping_not_suffixes(self):
        analysis = analyze(self.fixture.snapshot())
        snapshot, native = analysis["snapshots"][0], analysis["series"][0]
        base = native["task"]
        snapshot["tools"] = {base: {"kind": "historical", "engine_revision": "b" * 40,
                                    "revision": "c" * 40, "dependency_sources": {"symbolica": "d" * 40},
                                    "binary_hashes": {"driver": "e" * 64}}}
        profile = {**native, "task": base + "-callgrind"}
        values = {"implementation.way": "default"}
        unknown = project_implementation(snapshot, profile, values=values)
        self.assertIsNone(unknown["revision"])
        snapshot["provenance"]["profile_records"] = [{"task": base, "tool": "callgrind", "repeat": repeat} for repeat in range(3)]
        original = project_implementation(snapshot, native, values=values)
        resolved = project_implementation(snapshot, profile, values=values)
        self.assertEqual(resolved["revision"], "b" * 40)
        self.assertEqual(resolved["source_binding"], base)
        self.assertEqual(resolved["version_id"], original["version_id"])
        self.assertEqual(resolved["source_identity"]["dependency_sources"], {"symbolica": "d" * 40})
        self.assertEqual(resolved["source_identity"]["binary_hashes"], {"driver": "e" * 64})
        ambiguous = {**native, "task": "base-a-b"}
        snapshot["tools"] = {"base-a": snapshot["tools"][base], "base": {"kind": "historical", "engine_revision": "a" * 40}}
        snapshot["provenance"]["profile_records"] = [{"task": "base-a", "tool": "b"}, {"task": "base", "tool": "a-b"}]
        self.assertIsNone(project_implementation(snapshot, ambiguous, values=values)["revision"])

    def test_preserved_historical_profile_versions_match_their_retained_native_bindings(self):
        repository = Path(__file__).resolve().parents[1]
        checked = 0
        for name in ("generation-count-20261011", "generation-materialized-20261012", "generation-full-public-20261014"):
            analysis = json.loads((repository / "docs/results" / name / "analysis.json").read_text())
            snapshot = analysis["snapshots"][0]
            for record in snapshot["provenance"]["profile_records"]:
                task = record["task"] + "-" + record["tool"]
                item = next((item for item in analysis["series"] if item["task"] == task), None)
                native = next((item for item in analysis["series"] if item["task"] == record["task"]), None)
                if item is None or native is None:
                    continue
                values = {"implementation.way": "default"}
                profile = project_implementation(snapshot, item, values=values)
                source = project_implementation(snapshot, native, values=values)
                self.assertEqual(profile["version_id"], source["version_id"], (name, task))
                self.assertEqual(profile["revision"], source["revision"], (name, task))
                self.assertEqual(profile["source_binding"], record["task"])
                checked += 1
        self.assertGreater(checked, 100)

    def test_composed_nix_receipts_keep_original_way_and_distinct_versions(self):
        path = nix_capture(self.fixture, paired=True)
        analysis = analyze(path)
        values = [project_implementation(analysis["snapshots"][0], item) for item in analysis["series"] if item["metric"] == "completion_ns"]
        self.assertEqual([value["way_id"] for value in values], ["native", "native"])
        self.assertEqual([value["revision"] for value in values], ["a" * 40, "b" * 40])
        self.assertNotEqual(values[0]["version_id"], values[1]["version_id"])

    def test_variant_assembly_preserves_each_packages_declared_metadata(self):
        fixture = variant_fixtures.VariantTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for label, package in fixture.packages.items():
            path = package / "suite.json"
            suite = json.loads(path.read_text())
            suite["metadata"] = {"schema_version": 1, "values": {"operation": "integer-sum", "target": "kernel",
                                  "implementation.way": "same-way", "implementation.version_label": label},
                                  "metrics": {"compute_ns": {"operation": "symbolic-simplification", "numeric.precision": "exact"}}}
            path.write_text(json.dumps(suite))
        assembled = assemble(fixture.destination, fixture.packages)
        suite = validate_suite(json.loads((assembled / "suite.json").read_text()))
        metadata = capture_metadata(suite)
        for task in suite["tasks"]:
            values = metadata_values(metadata, case=task["case"], task=task["id"])
            self.assertEqual(values["implementation.way"], "same-way")
            self.assertEqual(values["implementation.version_label"], task["variant"])
            metric_values = metadata_values(metadata, case=task["case"], task=task["id"], metric="compute_ns")
            self.assertEqual(metric_values["operation"], "symbolic-simplification")
            self.assertEqual(metric_values["numeric.precision"], "exact")
            process_values = metadata_values(metadata, case=task["case"], task=task["id"], metric="process_wall_ns")
            self.assertEqual(process_values["operation"], "integer-sum")
        self.assertEqual(suite["metadata"]["metrics"], {"compute_ns": {"operation": "symbolic-simplification", "numeric.precision": "exact"}})

    def test_variant_assembly_rejects_incompatible_metric_metadata_before_creating_destination(self):
        fixture = variant_fixtures.VariantTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for label, package in fixture.packages.items():
            path = package / "suite.json"
            suite = json.loads(path.read_text())
            suite["metadata"] = {"schema_version": 1, "metrics": {"compute_ns": {
                "operation": "integer-sum" if label == "baseline" else "tensor-contraction"}}}
            path.write_text(json.dumps(suite))
        before = {path: path.read_bytes() for package in fixture.packages.values() for path in package.iterdir() if path.is_file()}
        with self.assertRaisesRegex(SchemaError, "per-metric metadata"):
            assemble(fixture.destination, fixture.packages)
        self.assertFalse(fixture.destination.exists())
        self.assertTrue(all(path.read_bytes() == value for path, value in before.items()))


if __name__ == "__main__":
    unittest.main()
