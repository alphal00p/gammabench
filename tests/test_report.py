"""Comparability, censoring and exported-value checks for offline reports."""

import copy
import csv
import hashlib
from html.parser import HTMLParser
from importlib import resources
import json
import shutil
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from gammabench.report import analyze, render
from gammabench.schema import validate_suite
from gammabench.snapshot import IntegrityError, seal, staging_directory


class ReportElements(HTMLParser):
    """Read control options and row attributes using the browser's HTML rules."""

    def __init__(self, page):
        super().__init__()
        self.elements = []
        self.options = {}
        self.select = None
        self.option = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if tag == "select":
            self.select = attributes.get("id")
            self.options[self.select] = []
        elif tag == "option":
            self.option = {"value": attributes.get("value") or "" if "value" in attributes else None, "text": "", "selected": "selected" in attributes}

    def handle_data(self, data):
        if self.option is not None:
            self.option["text"] += data

    def handle_endtag(self, tag):
        if tag == "option":
            if self.option["value"] is None:
                self.option["value"] = self.option["text"]
            self.options[self.select].append(self.option)
            self.option = None
        elif tag == "select":
            self.select = None


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.counter = 0

    def tearDown(self):
        self.temporary.cleanup()

    def snapshot(self, a=(10, 20, 30), b=(5, 10, 15), *, change=None, identity=None):
        self.counter += 1
        identity = identity or f"snapshot-{self.counter}"
        task_ids = ("baseline-task", "candidate-task")
        contract = {"description": "Retained full diagrams", "included": ["local numerators"],
                    "excluded": ["aggregate multiplication"]}
        suite = {
            "schema_version": 1, "id": "test-suite", "description": "Report test suite",
            "contracts": {"full": contract},
            "metrics": {"completion_ns": {"unit": "ns", "direction": "lower",
                                           "scope": "generation to completion", "result_field": "work_ns"}},
            "tools": {"gammaloop": {"kind": "git", "url": "https://example.org/gammaloop",
                                     "revision": "a" * 40}},
            "tasks": [{"id": task, "case": "four-loop", "engine": "linnet", "variant": variant,
                       "contract": "full", "argv": ["python", "driver.py"], "tool_ids": ["gammaloop"],
                       "inputs": {"case": "case.json"}, "equivalence_fields": ["output"]}
                      for task, variant in zip(task_ids, ("old", "new"))],
        }
        suite = validate_suite(suite)
        records = []
        for task, variant, values in zip(task_ids, ("old", "new"), (a, b)):
            for phase, samples in (("validation", (1,)), ("warmup", (1000000000,)), ("measure", values)):
                for repeat, value in enumerate(samples):
                    records.append({"task": task, "case": "four-loop", "engine": "linnet",
                                    "variant": variant, "contract": "full", "phase": phase,
                                    "repeat": repeat, "status": "completed", "returncode": 0,
                                    "duration_ns": value + 100,
                                    "metrics": {"completion_ns": value, "process_wall_ns": value + 100},
                                    "command": ["python", "driver.py"], "raw": {}, "inputs_sha256": {"case": "c" * 64},
                                    "result": {"output": "identical-diagrams", "work_ns": value}})
        snapshot = {"schema_version": 1, "id": identity, "suite_id": suite["id"],
                    "origin": "measured", "status": "completed", "created_at": "2026-10-14T12:00:00Z",
                    "contracts": copy.deepcopy(suite["contracts"]), "metrics": copy.deepcopy(suite["metrics"]),
                    "tools": suite["tools"], "host": {"cpu": "test CPU", "threads": 1},
                    "schedule": {"repeats": max(len(a), len(b)), "warmups": 1, "pairing_id": "paired-test-capture"}, "records": records,
                    "validation": {"status": "passed", "tasks": {task: {"status": "passed",
                                   "equivalence_fields": ["output"]} for task in task_ids},
                                   "equivalence": [{"case": "four-loop", "contract": "full",
                                                    "fields": ["output"], "tasks": list(task_ids), "status": "passed"}]},
                    "provenance": {}}
        if change:
            change(snapshot, suite)
        destination = self.root / identity
        stage = staging_directory(destination)
        seal(stage, destination, snapshot, suite)
        return destination

    def completion_pair(self, analysis):
        return next(pair for pair in analysis["comparisons"] if pair["metric"] == "completion_ns")

    def test_median_is_of_paired_ratios_and_warmups_are_excluded(self):
        analysis = analyze(self.snapshot(a=(1, 100, 100), b=(2, 50, 100)), baseline="old")
        pair = self.completion_pair(analysis)
        self.assertEqual(pair["status"], "comparable")
        self.assertEqual(pair["ratio"], {"count": 3, "median": 1, "minimum": 0.5, "maximum": 2})
        self.assertEqual(pair["speedup"]["median"], 1)
        self.assertEqual([sample["repeat"] for sample in pair["pairs"]], [0, 1, 2])
        first = analysis["series"][0]
        self.assertEqual(first["median"], 100)
        self.assertEqual(first["warmups"], 1)
        self.assertEqual(first["count"], 3)

    def test_all_baselines_recalculate_both_directions_from_even_sample_rounds(self):
        path = self.snapshot(a=(10, 10), b=(10, 40))
        analysis = analyze(path)
        pairs = [pair for pair in analysis["comparisons"] if pair["metric"] == "completion_ns"]
        self.assertEqual(len(pairs), 2)
        forward, reverse = pairs
        self.assertEqual((forward["baseline"], forward["candidate"]),
                         (reverse["candidate"], reverse["baseline"]))
        self.assertEqual(forward["ratio"]["median"], 2.5)
        # median(1 / paired ratios) differs from 1 / median(ratios) for even N.
        self.assertEqual(reverse["ratio"]["median"], 0.625)
        self.assertNotEqual(reverse["ratio"]["median"], 1 / forward["ratio"]["median"])
        self.assertEqual(reverse["speedup"], forward["ratio"])
        self.assertEqual([sample["ratio"] for sample in reverse["pairs"]], [1, 0.25])
        explicit = analyze(path, baseline="new")
        explicit_pairs = [pair for pair in explicit["comparisons"] if pair["metric"] == "completion_ns"]
        self.assertEqual(explicit_pairs, [reverse])

    def test_reversed_comparisons_keep_semantic_suppression(self):
        def change(snapshot, suite):
            snapshot["validation"]["tasks"]["candidate-task"]["status"] = "unqualified"
        analysis = analyze(self.snapshot(change=change))
        self.assertEqual(len(analysis["comparisons"]), 4)
        for pair in analysis["comparisons"]:
            self.assertEqual(pair["status"], "suppressed")
            self.assertIn("One or both tasks lack passed semantic validation.", pair["reasons"])
            self.assertIsNone(pair["ratio"]["median"])
            self.assertEqual(pair["pairs"], [])

    def test_contracts_are_never_ranked_together(self):
        def change(snapshot, suite):
            suite["contracts"]["graphs"] = {"description": "Graphs only", "included": ["symmetry"],
                                             "excluded": ["numerators"]}
            snapshot["contracts"] = copy.deepcopy(suite["contracts"])
            suite["tasks"][1]["contract"] = "graphs"
            for record in snapshot["records"]:
                if record["task"] == "candidate-task":
                    record["contract"] = "graphs"
        pair = self.completion_pair(analyze(self.snapshot(change=change)))
        self.assertEqual(pair["status"], "suppressed")
        self.assertIn("Output contracts differ.", pair["reasons"])
        self.assertIsNone(pair["ratio"]["median"])
        self.assertEqual(pair["pairs"], [])

    def test_equal_contract_id_with_changed_definition_is_suppressed(self):
        first = self.snapshot()
        def change(snapshot, suite):
            suite["contracts"]["full"]["included"].append("identity normalization")
            snapshot["contracts"] = copy.deepcopy(suite["contracts"])
        second = self.snapshot(change=change)
        comparisons = analyze([first, second], baseline=first.name)["comparisons"]
        self.assertTrue(comparisons)
        self.assertTrue(all(pair["status"] == "suppressed" for pair in comparisons))
        self.assertTrue(all(any("Contract definitions differ" in reason for reason in pair["reasons"])
                            for pair in comparisons))

    def test_scope_unit_and_direction_must_match(self):
        for key, value in (("scope", "whole process"), ("unit", "seconds"), ("direction", "higher")):
            with self.subTest(key=key):
                first = self.snapshot()
                def change(snapshot, suite):
                    suite["metrics"]["completion_ns"][key] = value
                    snapshot["metrics"] = copy.deepcopy(suite["metrics"])
                second = self.snapshot(change=change)
                pairs = [pair for pair in analyze([first, second], baseline=first.name)["comparisons"]
                         if pair["metric"] == "completion_ns"]
                self.assertTrue(all(pair["status"] == "suppressed" for pair in pairs))
                self.assertTrue(all("Metric unit, scope or direction differs." in pair["reasons"] for pair in pairs))

    def test_no_qualification_no_speedup_even_with_matching_result(self):
        def change(snapshot, suite):
            snapshot["validation"]["tasks"]["candidate-task"]["status"] = "unqualified"
        pair = self.completion_pair(analyze(self.snapshot(change=change)))
        self.assertEqual(pair["status"], "suppressed")
        self.assertIn("One or both tasks lack passed semantic validation.", pair["reasons"])

    def test_equivalence_gate_or_exact_result_fields_are_required(self):
        def change(snapshot, suite):
            snapshot["validation"]["equivalence"] = []
            for record in snapshot["records"]:
                if record["task"] == "candidate-task":
                    record["result"]["output"] = "different-diagrams"
        pair = self.completion_pair(analyze(self.snapshot(change=change)))
        self.assertEqual(pair["status"], "suppressed")
        self.assertIn("Accepted RESULT equivalence fields differ.", pair["reasons"])

    def test_historical_explicit_gate_can_qualify_without_result_values(self):
        def change(snapshot, suite):
            snapshot["origin"] = "historical-import"
            snapshot["status"] = "imported"
            snapshot["provenance"] = {"run_id": "original-run"}
            for record in snapshot["records"]:
                record.pop("result")
        analysis = analyze(self.snapshot(change=change))
        pair = self.completion_pair(analysis)
        self.assertEqual(pair["status"], "comparable")
        self.assertTrue(pair["historical"])
        self.assertEqual(analysis["snapshots"][0]["provenance"]["run_id"], "original-run")
        self.assertTrue(any("not new reruns" in warning for warning in analysis["warnings"]))

    def test_timeout_is_censored_and_suppresses_whole_pair(self):
        def change(snapshot, suite):
            record = next(record for record in snapshot["records"] if record["task"] == "candidate-task"
                          and record["phase"] == "measure" and record["repeat"] == 1)
            record.update(status="timeout", returncode=None, duration_ns=999999999, metrics={})
        analysis = analyze(self.snapshot(change=change))
        pair = self.completion_pair(analysis)
        self.assertEqual(pair["status"], "suppressed")
        self.assertEqual(pair["ratio"]["count"], 0)
        candidate = next(item for item in analysis["series"] if item["task"] == "candidate-task"
                         and item["metric"] == "completion_ns")
        self.assertEqual(candidate["count"], 2)
        self.assertEqual(candidate["median"], 10)
        self.assertEqual(candidate["statuses"], {"completed": 2, "timeout": 1})
        self.assertEqual(candidate["samples"], [{"repeat": 0, "value": 5}, {"repeat": 2, "value": 15}])

    def test_incomplete_duplicate_and_missing_rounds_are_not_subset_ranked(self):
        changes = {
            "missing metric": lambda snapshot: next(record for record in snapshot["records"]
                if record["task"] == "candidate-task" and record["phase"] == "measure")["metrics"].pop("completion_ns"),
            "duplicate": lambda snapshot: snapshot["records"].append(copy.deepcopy(next(record
                for record in snapshot["records"] if record["task"] == "candidate-task" and record["phase"] == "measure"))),
            "missing round": lambda snapshot: snapshot["records"].pop(next(index for index, record in enumerate(snapshot["records"])
                if record["task"] == "candidate-task" and record["phase"] == "measure")),
        }
        for label, mutate in changes.items():
            with self.subTest(label=label):
                path = self.snapshot(change=lambda snapshot, suite: mutate(snapshot))
                pair = self.completion_pair(analyze(path))
                self.assertEqual(pair["status"], "suppressed")
                self.assertIsNone(pair["ratio"]["median"])
                self.assertEqual(pair["pairs"], [])

    def test_different_hosts_are_disclosed_and_versions_retained(self):
        first = self.snapshot()
        def change(snapshot, suite):
            snapshot["host"]["cpu"] = "Other CPU"
        second = self.snapshot(change=change)
        analysis = analyze([first, second], baseline=first.name)
        self.assertTrue(all(pair["host_differences"] == ["cpu"] for pair in analysis["comparisons"]))
        self.assertTrue(all(pair["status"] == "comparable" for pair in analysis["comparisons"]))
        self.assertEqual(analysis["snapshots"][0]["tools"]["gammaloop"]["revision"], "a" * 40)

    def test_independent_snapshots_do_not_invent_pairs_from_repeat_numbers(self):
        first = self.snapshot()
        def change(snapshot, suite):
            snapshot["schedule"]["pairing_id"] = "different-capture"
        second = self.snapshot(change=change)
        comparisons = analyze([first, second], baseline=first.name)["comparisons"]
        self.assertTrue(all(pair["status"] == "suppressed" for pair in comparisons))
        self.assertTrue(all(any("Independent snapshots" in reason for reason in pair["reasons"])
                            for pair in comparisons))

    def test_profile_tasks_can_declare_their_actual_repeat_count(self):
        def change(snapshot, suite):
            snapshot["schedule"]["repeats"] = 15
            snapshot["schedule"]["task_repeats"] = {"baseline-task": 3, "candidate-task": 3}
        pair = self.completion_pair(analyze(self.snapshot(change=change)))
        self.assertEqual(pair["status"], "comparable")

    def test_task_identity_cannot_change_between_records(self):
        def change(snapshot, suite):
            snapshot["records"][-1]["case"] = "other-case"
        with self.assertRaisesRegex(ValueError, "Inconsistent record identity"):
            analyze(self.snapshot(change=change))

    def test_auxiliary_engine_inputs_can_differ_with_shared_explicit_workload(self):
        def change(snapshot, suite):
            for task in suite["tasks"]:
                task["workload_inputs"] = ["case"]
            for record in snapshot["records"]:
                record["inputs_sha256"]["engine-config"] = ("a" if record["task"] == "baseline-task" else "b") * 64
                record["workload_inputs_sha256"] = {"case": record["inputs_sha256"]["case"]}
        analysis = analyze(self.snapshot(change=change))
        pair = self.completion_pair(analysis)
        self.assertEqual(pair["status"], "comparable")
        self.assertEqual(analysis["series"][0]["workload"]["sha256"], {"case": "c" * 64})

    def test_changed_explicit_workload_subset_is_not_ranked(self):
        def change(snapshot, suite):
            for record in snapshot["records"]:
                if record["task"] == "candidate-task":
                    record["inputs_sha256"]["case"] = "d" * 64
                record["workload_inputs_sha256"] = {"case": record["inputs_sha256"]["case"]}
        pair = self.completion_pair(analyze(self.snapshot(change=change)))
        self.assertEqual(pair["status"], "suppressed")
        self.assertIn("Workload input digests differ despite an identical case id.", pair["reasons"])

    def test_empty_or_unbound_workload_subset_does_not_qualify(self):
        for subset in ({}, {"case": "d" * 64}, {"missing": "c" * 64}):
            with self.subTest(subset=subset):
                def change(snapshot, suite):
                    for record in snapshot["records"]:
                        record["workload_inputs_sha256"] = subset
                pair = self.completion_pair(analyze(self.snapshot(change=change)))
                self.assertEqual(pair["status"], "suppressed")
                self.assertIn("Immutable workload input identity is missing or inconsistent.", pair["reasons"])

    def test_same_case_with_changed_workload_input_is_not_ranked(self):
        def change(snapshot, suite):
            for record in snapshot["records"]:
                if record["task"] == "candidate-task":
                    record["inputs_sha256"]["case"] = "d" * 64
        pair = self.completion_pair(analyze(self.snapshot(change=change)))
        self.assertEqual(pair["status"], "suppressed")
        self.assertIn("Workload input digests differ despite an identical case id.", pair["reasons"])

    def test_missing_workload_identity_does_not_silently_qualify(self):
        def change(snapshot, suite):
            for record in snapshot["records"]:
                record.pop("inputs_sha256")
        pair = self.completion_pair(analyze(self.snapshot(change=change)))
        self.assertEqual(pair["status"], "suppressed")
        self.assertIn("Immutable workload input identity is missing or inconsistent.", pair["reasons"])

    def test_explicit_canonical_workload_digest_can_bind_historical_inputs(self):
        def change(snapshot, suite):
            snapshot["provenance"]["workloads"] = {"four-loop": {"sha256": "c" * 64}}
            for record in snapshot["records"]:
                record.pop("inputs_sha256")
        pair = self.completion_pair(analyze(self.snapshot(change=change)))
        self.assertEqual(pair["status"], "comparable")

    def test_selected_unsupported_case_stays_visible_with_null_values(self):
        def change(snapshot, suite):
            snapshot["schedule"]["selected_tasks"] = ["baseline-task", "candidate-task"]
            snapshot["records"] = [record for record in snapshot["records"] if record["task"] != "candidate-task"]
            snapshot["validation"]["tasks"]["candidate-task"] = {"status": "unqualified", "reason": "Unsupported"}
        analysis = analyze(self.snapshot(change=change))
        candidate = next(item for item in analysis["series"] if item["task"] == "candidate-task"
                         and item["metric"] == "completion_ns")
        self.assertIsNone(candidate["median"])
        self.assertEqual(candidate["count"], 0)
        self.assertEqual(candidate["validation_status"], "unqualified")
        self.assertEqual(self.completion_pair(analysis)["status"], "suppressed")

    def test_portable_proof_can_be_validated_outside_measured_clock(self):
        def change(snapshot, suite):
            snapshot["validation"]["equivalence"] = []
            for record in snapshot["records"]:
                if record["phase"] != "validation":
                    record["result"].pop("output")
        self.assertEqual(self.completion_pair(analyze(self.snapshot(change=change)))["status"], "comparable")

    def test_unsupported_and_qualification_timeouts_keep_their_original_scope(self):
        def change(snapshot, suite):
            snapshot["schedule"]["unsupported"] = [{"case": "vacuum", "engine": "qgraf", "reason": "No vacuum support"}]
            snapshot["schedule"]["qualification_censored"] = [{"case": "large", "engine": "nauty",
                "status": "censored_timeout", "timeout_seconds": 60,
                "timing_interpretation": "qualification dump timeout; not a generation runtime lower bound"}]
        directory = self.root / "availability-report"
        analysis = render(self.snapshot(change=change), directory)
        self.assertEqual(analysis["availability"][0]["status"], "unsupported")
        self.assertEqual(analysis["availability"][1]["stage"], "qualification")
        with (directory / "availability.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[1]["timeout_seconds"], "60")
        self.assertEqual(rows[1]["duration_ns"], "")
        self.assertIn("not a generation runtime lower bound", rows[1]["timing_interpretation"])
        self.assertIn("No vacuum support", (directory / "index.html").read_text())

    def test_historical_counter_tasks_do_not_invent_process_or_completion_metrics(self):
        def change(snapshot, suite):
            snapshot["origin"] = "historical-import"
            snapshot["status"] = "imported"
            suite["metrics"]["whole_process_Ir"] = {"unit": "instructions", "direction": "lower",
                "scope": "complete instrumented process", "result_field": "Ir"}
            snapshot["metrics"] = copy.deepcopy(suite["metrics"])
            for record in snapshot["records"]:
                if record["task"] == "baseline-task" and record["phase"] == "measure":
                    record["metrics"] = {"whole_process_Ir": record["metrics"]["completion_ns"] * 100}
        analysis = analyze(self.snapshot(change=change))
        counter = [item for item in analysis["series"] if item["task"] == "baseline-task"]
        self.assertEqual([item["metric"] for item in counter], ["whole_process_Ir"])
        self.assertEqual(counter[0]["median"], 2000)
        self.assertEqual({item["metric"] for item in analysis["series"] if item["task"] == "candidate-task"},
                         {"completion_ns", "process_wall_ns"})
        self.assertEqual(analysis["comparisons"], [])

    def test_historical_partial_metric_rounds_still_suppress_comparison(self):
        def change(snapshot, suite):
            snapshot["origin"] = "historical-import"
            record = next(record for record in snapshot["records"] if record["task"] == "candidate-task"
                          and record["phase"] == "measure")
            record["metrics"].pop("completion_ns")
        pair = self.completion_pair(analyze(self.snapshot(change=change)))
        self.assertEqual(pair["status"], "suppressed")
        self.assertIn("A completed measurement is missing this metric.", pair["reasons"])

    def test_explicit_engine_selector_is_unambiguous_for_multibackend_comparison(self):
        def change(snapshot, suite):
            suite["tasks"][1]["engine"] = "qgraf"
            for record in snapshot["records"]:
                if record["task"] == "candidate-task":
                    record["engine"] = "qgraf"
        path = self.snapshot(change=change)
        analysis = analyze(path, baseline="engine:qgraf")
        pair = self.completion_pair(analysis)
        self.assertIn("candidate-task", pair["baseline"])
        self.assertIn("baseline-task", pair["candidate"])
        self.assertEqual(pair["ratio"]["median"], 2)
        with self.assertRaisesRegex(ValueError, "Unknown baseline"):
            analyze(path, baseline="qgraf")
        with self.assertRaisesRegex(ValueError, "Unknown baseline"):
            analyze(path, baseline="engine:missing")

    def test_higher_metrics_define_speedup_in_declared_direction(self):
        def change(snapshot, suite):
            suite["metrics"]["completion_ns"]["direction"] = "higher"
            snapshot["metrics"] = copy.deepcopy(suite["metrics"])
        pair = self.completion_pair(analyze(self.snapshot(a=(10, 20, 30), b=(20, 40, 60), change=change)))
        self.assertEqual(pair["speedup"]["median"], 2)
        self.assertEqual(pair["ratio"]["median"], 2)

    def test_zero_values_display_but_do_not_produce_ratios(self):
        analysis = analyze(self.snapshot(a=(0, 0, 0)))
        self.assertEqual(analysis["series"][0]["median"], 0)
        pair = self.completion_pair(analysis)
        self.assertEqual(pair["status"], "suppressed")
        self.assertIn("Ratios require strictly positive measurements.", pair["reasons"])

    def test_duplicate_identity_and_unknown_or_ambiguous_baseline_are_errors(self):
        path = self.snapshot(identity="old")
        with self.assertRaisesRegex(ValueError, "Duplicate snapshot identity"):
            analyze([path, path])
        with self.assertRaisesRegex(ValueError, "Unknown baseline"):
            analyze(path, baseline="absent")
        with self.assertRaisesRegex(ValueError, "both a snapshot"):
            analyze(path, baseline="old")

    def test_exports_share_actual_numbers_and_never_mutate_snapshots(self):
        def change(snapshot, suite):
            suite["contracts"]["full"]["description"] = '<script>alert("unsafe")</script>'
            snapshot["contracts"] = copy.deepcopy(suite["contracts"])
        path = self.snapshot(change=change)
        before = {file.name: file.read_bytes() for file in path.iterdir()}
        directory = self.root / "report"
        analysis = render(path, directory, baseline="old")
        self.assertEqual(analysis, json.loads((directory / "analysis.json").read_text()))
        with (directory / "results.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(float(rows[0]["median"]), analysis["series"][0]["median"])
        with (directory / "comparisons.csv").open(newline="") as stream:
            pairs = list(csv.DictReader(stream))
        self.assertEqual(float(pairs[0]["median_ratio"]), analysis["comparisons"][0]["ratio"]["median"])
        page = (directory / "index.html").read_text()
        self.assertIn("&lt;script", page)
        self.assertNotIn('<script>alert("unsafe")</script>', page)
        self.assertNotIn('src="http', page)
        self.assertTrue(any(tag == "input" and attrs.get("id") == "search"
                            for tag, attrs in ReportElements(page).elements))
        self.assertIn("aggregate multiplication", page)
        self.assertIn("generation to completion", page)
        self.assertEqual(before, {file.name: file.read_bytes() for file in path.iterdir()})
        with self.assertRaises(FileExistsError):
            render(path, directory)

    def test_offline_assets_are_packaged_copied_and_bound_by_report_inventory(self):
        directory = self.root / "assets-report"
        render(self.snapshot(), directory)
        inventory = json.loads((directory / "files.sha256.json").read_text())
        self.assertEqual(set(inventory), {path.relative_to(directory).as_posix()
                                           for path in directory.rglob("*")
                                           if path.is_file() and path.name != "files.sha256.json"})
        self.assertTrue({"index.html", "report.css", "report.js", "report.typ", "matrix.typ", "components.typ",
                         "analysis.json", "presentation.json", "rendering.json", "results.csv", "comparisons.csv",
                         "availability.csv", "theme/site.css", "theme/site.js"}.issubset(inventory))
        for name, digest in inventory.items():
            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), digest)
        for name in ("report.css", "report.js", "report.typ", "matrix.typ", "components.typ"):
            self.assertEqual((directory / name).read_bytes(),
                             resources.files("gammabench").joinpath("assets", name).read_bytes())
        document = ReportElements((directory / "index.html").read_text())
        self.assertEqual([attrs["src"] for tag, attrs in document.elements if tag == "script"], ["theme/site.js", "report.js"])
        self.assertEqual([attrs["href"] for tag, attrs in document.elements if tag == "link"], ["theme/site.css", "report.css"])
        self.assertFalse(any(tag == "style" for tag, _ in document.elements))
        self.assertEqual(set(document.options), {"case", "baseline", "variant", "metric", "status",
                                                 "matrix-case", "matrix-metric", "matrix-reference"})
        self.assertIn("four-loop", {option["value"] for option in document.options["case"]})
        self.assertIn("completion_ns", {option["value"] for option in document.options["metric"]})
        selected = [option["value"] for option in document.options["baseline"] if option["selected"]]
        self.assertEqual(selected, ["engine:linnet"])
        baseline_options = {option["value"] for option in document.options["baseline"]}
        self.assertEqual(baseline_options, {"", "engine:linnet", "variant:old", "variant:new"})
        rows = [attrs for tag, attrs in document.elements if tag == "tr" and "data-baseline-engine" in attrs]
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["data-baseline-variant"] for row in rows}, {"old", "new"})
        self.assertTrue(all(row["data-baseline-engine"] == "linnet" for row in rows))

    def test_full_diagram_baseline_variant_is_preferred_to_shared_engine(self):
        def change(snapshot, suite):
            suite["tasks"][0]["variant"] = "baseline"
            for record in snapshot["records"]:
                if record["task"] == "baseline-task":
                    record["variant"] = "baseline"
        directory = self.root / "baseline-report"
        render(self.snapshot(change=change), directory)
        document = ReportElements((directory / "index.html").read_text())
        self.assertEqual([option["value"] for option in document.options["baseline"] if option["selected"]],
                         ["variant:baseline"])

    def test_explicit_baseline_report_does_not_offer_uncomputed_reverse_variant(self):
        directory = self.root / "explicit-report"
        render(self.snapshot(), directory, baseline="new")
        document = ReportElements((directory / "index.html").read_text())
        self.assertEqual([option["value"] for option in document.options["baseline"] if option["selected"]],
                         ["variant:new"])
        self.assertNotIn("variant:old", {option["value"] for option in document.options["baseline"]})
        rows = [attrs for tag, attrs in document.elements if tag == "tr" and "data-baseline-variant" in attrs]
        self.assertTrue(all(row["data-baseline-variant"] == "new" for row in rows))

    def test_selector_attributes_roundtrip_literal_markup_without_injecting_nodes(self):
        value = '\"><script>alert("unsafe")</script>'
        analysis = analyze(self.snapshot())
        for item in analysis["series"]:
            for field in ("case", "variant", "engine", "metric", "validation_status"):
                item[field] = value
        for pair in analysis["comparisons"]:
            for field in ("case", "metric", "status"):
                pair[field] = value
        directory = self.root / "literal-report"
        with patch("gammabench.report.analyze", return_value=analysis):
            render([], directory)
        document = ReportElements((directory / "index.html").read_text())
        scripts = [attrs for tag, attrs in document.elements if tag == "script"]
        self.assertEqual([attrs["src"] for attrs in scripts], ["theme/site.js", "report.js"])
        self.assertTrue(all("defer" in attrs for attrs in scripts))
        rows = [attrs for tag, attrs in document.elements if tag == "tr" and "data-baseline-engine" in attrs]
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(all(row[name] == value for name in (
            "data-case", "data-variant", "data-metric", "data-status", "data-baseline-engine", "data-baseline-variant"))
            for row in rows))
        self.assertIn(value, {option["value"] for option in document.options["case"]})
        self.assertIn("engine:" + value, {option["value"] for option in document.options["baseline"]})

    def test_typst_is_pinned_before_creating_report_destination(self):
        path = self.snapshot()
        for executable, version in ((None, None), ("typst", "typst 0.15.0")):
            directory = self.root / ("missing-typst" if executable is None else "wrong-typst")
            with self.subTest(executable=executable), patch("gammabench.report.shutil.which", return_value=executable), \
                    patch("gammabench.report.subprocess.run", return_value=SimpleNamespace(
                        stdout=version, stderr="", returncode=0)):
                with self.assertRaisesRegex(ValueError, "requires Typst 0.15.1"):
                    render(path, directory)
            self.assertFalse(directory.exists())

    def test_exported_typst_rebuilds_identical_html_after_report_relocation(self):
        directory = self.root / "original-report"
        render(self.snapshot(), directory)
        relocated = self.root / "moved" / "report"
        shutil.copytree(directory, relocated)
        recipe = json.loads((relocated / "rendering.json").read_text())
        self.assertEqual(recipe["compiler"], {"name": "typst", "version": "0.15.1"})
        self.assertEqual(recipe["creation_timestamp"], 0)
        self.assertEqual(recipe["features"], ["html"])
        subprocess.run(recipe["command"].split(), cwd=relocated, check=True, capture_output=True)
        inventory = json.loads((directory / "files.sha256.json").read_text())
        self.assertEqual((directory / "index.html").read_bytes(), (relocated / "index.html").read_bytes())
        self.assertEqual((directory / "presentation.json").read_bytes(), (relocated / "presentation.json").read_bytes())
        self.assertTrue(all(hashlib.sha256((relocated / name).read_bytes()).hexdigest() == digest
                            for name, digest in inventory.items()))

    def test_matrix_preserves_precomputed_reverse_pair_statistics(self):
        directory = self.root / "matrix-pairs"
        analysis = render(self.snapshot(a=(10, 10), b=(10, 40)), directory)
        document = ReportElements((directory / "index.html").read_text())
        markers = [attrs for tag, attrs in document.elements
                   if attrs.get("class") == "matrix-comparison"]
        self.assertEqual(len(markers), len(analysis["comparisons"]))
        for pair in analysis["comparisons"]:
            marker = next(attrs for attrs in markers
                          if attrs["data-reference-series"] == pair["baseline"]
                          and attrs["data-candidate-series"] == pair["candidate"])
            self.assertEqual(float(marker["data-ratio"]), pair["ratio"]["median"])
            self.assertEqual(float(marker["data-speedup"]), pair["speedup"]["median"])
            self.assertEqual(int(marker["data-pairs"]), pair["ratio"]["count"])
        self.assertIn(0.625, [float(item["data-ratio"]) for item in markers])
        self.assertNotIn(0.4, [float(item["data-ratio"]) for item in markers])
        self.assertEqual(json.loads((directory / "analysis.json").read_text()), analysis)
        self.assertTrue(any(tag == "details" and attrs.get("class") == "matrix-evidence"
                            and "open" not in attrs for tag, attrs in document.elements))

    def test_matrix_explicit_baseline_only_offers_computed_reference_runs(self):
        directory = self.root / "matrix-explicit"
        analysis = render(self.snapshot(), directory, baseline="new")
        document = ReportElements((directory / "index.html").read_text())
        references = {json.loads(option["value"])[2]
                      for option in document.options["matrix-reference"] if option["value"]}
        self.assertEqual(references, {"new"})
        self.assertTrue(all(pair["baseline"].split(":")[1] == "candidate-task"
                            for pair in analysis["comparisons"]))
        views = [attrs for tag, attrs in document.elements
                 if attrs.get("class") == "matrix-view"]
        self.assertTrue(all(json.loads(view["data-default-reference"])[2] == "new" for view in views))

    def test_matrix_retains_duplicate_captures_instead_of_averaging(self):
        def change(snapshot, suite):
            suite["tasks"][1]["variant"] = "old"
            for record in snapshot["records"]:
                if record["task"] == "candidate-task":
                    record["variant"] = "old"
        directory = self.root / "matrix-duplicates"
        analysis = render(self.snapshot(change=change), directory)
        document = ReportElements((directory / "index.html").read_text())
        raw = [attrs for tag, attrs in document.elements if attrs.get("class") == "matrix-series"]
        self.assertEqual({item["data-series"] for item in raw}, {item["id"] for item in analysis["series"]})
        self.assertEqual(len(raw), len(analysis["series"]))
        self.assertEqual({item["data-task"] for item in raw}, {"baseline-task", "candidate-task"})
        self.assertEqual({float(item["data-median"]) for item in raw}, {10, 20, 110, 120})
        references = {option["value"] for option in document.options["matrix-reference"] if option["value"]}
        self.assertEqual(len(references), 1)

    def test_matrix_preserves_unavailable_cases_and_censored_reference_status(self):
        def change(snapshot, suite):
            snapshot["schedule"]["unsupported"] = [{"case": "vacuum", "engine": "qgraf", "reason": "No vacuum support"}]
            record = next(record for record in snapshot["records"]
                          if record["task"] == "baseline-task" and record["phase"] == "measure" and record["repeat"] == 1)
            record.update(status="timeout", metrics={})
        directory = self.root / "matrix-censored"
        analysis = render(self.snapshot(change=change), directory)
        document = ReportElements((directory / "index.html").read_text())
        rows = [attrs for tag, attrs in document.elements if tag == "tr" and "data-matrix-case" in attrs]
        self.assertIn("vacuum", {row["data-matrix-case"] for row in rows})
        missing = [attrs for tag, attrs in document.elements if attrs.get("class") == "matrix-availability-item"]
        self.assertEqual(len(missing), len(analysis["availability"]))
        self.assertIn("unsupported", {item["data-status"] for item in missing})
        raw = [attrs for tag, attrs in document.elements if attrs.get("class") == "matrix-series"]
        self.assertTrue(all(item["data-complete"] == "false" for item in raw
                            if item["data-task"] == "baseline-task"))
        markers = [attrs for tag, attrs in document.elements if attrs.get("class") == "matrix-comparison"]
        self.assertTrue(all(item["data-status"] == "suppressed" and not item["data-ratio"] for item in markers))
        self.assertTrue(all(item["data-median"] != "0" for item in raw))

    def test_matrix_keeps_contracts_in_separate_views_and_suppression_in_evidence(self):
        def change(snapshot, suite):
            suite["contracts"]["graphs"] = {"description": "Graphs only", "included": ["symmetry"], "excluded": ["numerators"]}
            snapshot["contracts"] = copy.deepcopy(suite["contracts"])
            suite["tasks"][1]["contract"] = "graphs"
            for record in snapshot["records"]:
                if record["task"] == "candidate-task":
                    record["contract"] = "graphs"
        directory = self.root / "matrix-contracts"
        analysis = render(self.snapshot(change=change), directory)
        page = (directory / "index.html").read_text()
        document = ReportElements(page)
        views = [attrs for tag, attrs in document.elements if attrs.get("class") == "matrix-view"]
        self.assertEqual(len(views), 4)  # Two contracts, application and process clocks.
        self.assertEqual({view["data-contract"] for view in views}, {"full", "graphs"})
        self.assertEqual(len({view["data-matrix-view"] for view in views}), 4)
        self.assertTrue(all(json.loads(view["data-matrix-view"])[-1] == view["data-contract"] for view in views))
        self.assertFalse(any(attrs.get("class") == "matrix-comparison" for tag, attrs in document.elements))
        self.assertTrue(all(pair["status"] == "suppressed" for pair in analysis["comparisons"]))
        self.assertIn("Output contracts differ.", page)

    def test_matrix_retains_an_unsupported_only_study_without_fake_measurements(self):
        def change(snapshot, suite):
            snapshot["records"] = []
            snapshot["schedule"]["selected_tasks"] = []
            snapshot["schedule"]["unsupported"] = [{
                "case": "vacuum", "engine": "qgraf", "reason": "No captured support",
            }]
        directory = self.root / "matrix-unsupported-only"
        path = self.snapshot(change=change)
        analysis = render(path, directory)
        document = ReportElements((directory / "index.html").read_text())
        self.assertEqual(analysis["series"], [])
        self.assertEqual(analysis["comparisons"], [])
        self.assertFalse(any(attrs.get("class") == "matrix-series"
                             for _, attrs in document.elements))
        entries = [attrs for _, attrs in document.elements
                   if attrs.get("class") == "matrix-availability-item"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["data-status"], "unsupported")
        self.assertEqual(entries[0]["data-matrix-case"], "vacuum")
        self.assertIn("No captured support", (directory / "index.html").read_text())

    def test_report_destination_cannot_modify_a_source_snapshot(self):
        path = self.snapshot()
        before = {file.name: file.read_bytes() for file in path.iterdir()}
        with self.assertRaisesRegex(ValueError, "outside source snapshots"):
            render(path, path / "reports" / "new")
        self.assertEqual(before, {file.name: file.read_bytes() for file in path.iterdir()})

    def test_snapshot_integrity_is_checked_before_analysis(self):
        path = self.snapshot()
        file = path / "snapshot.json"
        file.chmod(0o644)
        file.write_text(file.read_text().replace("test CPU", "tampered CPU"))
        with self.assertRaises(IntegrityError):
            analyze(path)


if __name__ == "__main__":
    unittest.main()
