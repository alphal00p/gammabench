"""Commit histories preserve source identities and separate measurement contexts."""

import copy
from html.parser import HTMLParser
from importlib import resources
import json
from pathlib import Path
import subprocess
import unittest

from gammabench.report import analyze
import test_report as report_fixtures
from test_history_context import nix_capture


class HistoryElements(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.groups = []
        self.points = []
        self.unplotted = []
        self.availability = []
        self.links = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs["href"])
        destinations = {"history-series": self.groups, "history-point": self.points,
                        "history-unplotted": self.unplotted,
                        "history-availability": self.availability}
        if attrs.get("class") in destinations:
            destinations[attrs["class"]].append(attrs)


class HistoryViewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = report_fixtures.ReportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.compilations = 0
        for name in ("history.typ", "matrix.typ", "components.typ"):
            (self.root / name).write_bytes(resources.files("gammabench").joinpath("assets", name).read_bytes())

    def compile(self, analysis, *, reports=None, capture_modes=None):
        self.compilations += 1
        stem = f"history-{self.compilations}"
        data = self.root / (stem + ".json")
        before = json.dumps(analysis, sort_keys=True)
        data.write_text(before)
        template = self.root / (stem + ".typ")
        links = self.root / (stem + "-links.json")
        links.write_text(json.dumps(reports or {}))
        modes = self.root / (stem + "-modes.json")
        modes.write_text(json.dumps(capture_modes or {}))
        template.write_text('#import "history.typ": benchmark-history\n'
                            f'#benchmark-history((json("{data.name}"),), reports: json("{links.name}"), capture-modes: json("{modes.name}"), subjects: '
                            '(linnet: (tool: "gammaloop", repository: "https://github.com/alphal00p/gammaloop")))\n')
        output = self.root / (stem + ".html")
        result = subprocess.run(["typst", "compile", "--root", str(self.root), "--features", "html",
                                 "--format", "html", str(template), str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data.read_text(), before)
        self.assertEqual(json.dumps(analysis, sort_keys=True), before)
        rendered = output.read_text()
        self.assertNotIn('else { item.snapshot_id', rendered)
        self.assertNotIn('else { [ · Report link unavailable]', rendered)
        return rendered, HistoryElements(rendered)

    def simple_analysis(self):
        analysis = analyze(self.fixture.snapshot())
        analysis["series"] = [next(item for item in analysis["series"]
                                  if item["task"] == "baseline-task" and item["metric"] == "completion_ns")]
        # Model the retained controller context of an imported historical study.
        analysis["snapshots"][0]["origin"] = "historical-import"
        analysis["snapshots"][0]["provenance"].update(legacy_controller_sha256="f" * 64, profile_records=[])
        analysis["series"][0]["origin"] = "historical-import"
        return analysis

    def with_next_capture(self, analysis):
        next_snapshot = copy.deepcopy(analysis["snapshots"][0])
        next_snapshot["id"] = "next-capture"
        next_snapshot["created_at"] = "2026-10-15T12:00:00Z"
        next_snapshot["tools"]["gammaloop"]["revision"] = "b" * 40
        next_item = copy.deepcopy(analysis["series"][0])
        next_item["snapshot_id"] = next_snapshot["id"]
        next_item["id"] = next_snapshot["id"] + ":baseline-task:completion_ns"
        analysis["snapshots"].append(next_snapshot)
        analysis["series"].append(next_item)
        return next_snapshot, next_item

    def test_measured_tool_commits_join_across_captures_without_mutating_analysis(self):
        analysis = self.simple_analysis()
        snapshot, item = self.with_next_capture(analysis)
        snapshot["host"]["load_average_start"] = [100, 200, 300]
        item["variant"] = "candidate"
        _, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 1)
        self.assertEqual([point["data-commit"] for point in document.points], ["a" * 40, "b" * 40])
        self.assertEqual([point["data-median"] for point in document.points], ["20", "20"])
        self.assertEqual([point["data-count"] for point in document.points], ["3", "3"])
        self.assertEqual({point["data-repository"] for point in document.points},
                         {"https://github.com/alphal00p/gammaloop"})

    def test_registered_evidence_links_follow_actual_capture_ids(self):
        analysis = self.simple_analysis()
        self.with_next_capture(analysis)
        first_id = analysis["snapshots"][0]["id"]
        reports = {first_id: "captures/original/index.html", "next-capture": "versions/next/report.html"}
        _, document = self.compile(analysis, reports=reports)
        self.assertIn(reports[first_id], document.links)
        self.assertIn(reports["next-capture"], document.links)
        self.assertIn("captures/original/analysis.json", document.links)
        page, document = self.compile(analysis)
        self.assertIn("Report link unavailable", page)
        self.assertFalse(any(href.startswith("docs/results/") for href in document.links))

    def test_hardware_contract_inputs_scope_and_fixed_dependencies_split_lines(self):
        def hardware(snapshot, item):
            snapshot["host"]["cpu"] = "different CPU"

        def contract(snapshot, item):
            snapshot["contracts"]["full"]["excluded"] = ["different exclusion"]

        def inputs(snapshot, item):
            item["workload"]["sha256"]["case"] = "d" * 64

        def scope(snapshot, item):
            item["scope"] = "different timing boundary"

        def dependency(snapshot, item):
            snapshot["tools"]["symbolica"] = {"kind": "git", "revision": "c" * 40}

        def controller(snapshot, item):
            snapshot["provenance"]["legacy_controller_sha256"] = "e" * 64

        def environment(snapshot, item):
            snapshot["provenance"]["explicit_environment"] = {"RAYON_NUM_THREADS": "8"}

        def clock(snapshot, item):
            snapshot["provenance"]["clock"] = "different clock source"

        for mutate in (hardware, contract, inputs, scope, dependency, controller, environment, clock):
            with self.subTest(context=mutate.__name__):
                analysis = self.simple_analysis()
                snapshot, item = self.with_next_capture(analysis)
                mutate(snapshot, item)
                _, document = self.compile(analysis)
                self.assertEqual(len(document.groups), 2)
                self.assertEqual(len(document.points), 2)

    def test_explicit_profile_provenance_resolves_source_and_separates_capture_mode(self):
        analysis = self.simple_analysis()
        snapshot = analysis["snapshots"][0]
        snapshot["tools"] = {"baseline-task": {"kind": "historical", "engine_revision": "c" * 40,
                             "revision": "d" * 40, "dependency_sources": {"gammaloop": "c" * 40}}}
        snapshot["provenance"]["profile_records"] = [{"tool": "callgrind", "task": "baseline-task", "repeat": 0}]
        profile = copy.deepcopy(analysis["series"][0])
        profile["task"] = "baseline-task-callgrind"
        profile["id"] += "-profile"
        analysis["series"].append(profile)
        _, document = self.compile(analysis)
        self.assertEqual({group["data-capture"] for group in document.groups}, {"timing", "callgrind"})
        self.assertEqual({point["data-commit"] for point in document.points}, {"c" * 40})
        # Neither the adapter revision nor the display repository supplies x values.
        self.assertNotIn("d" * 40, {point["data-commit"] for point in document.points})

    def test_declared_modes_do_not_assume_missing_normal_task_environment_and_build_context(self):
        analysis = analyze(self.fixture.snapshot())
        analysis["series"] = [item for item in analysis["series"] if item["metric"] == "completion_ns"]
        _, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 2)
        self.assertTrue(all(group["data-capture"].startswith("Unspecified capture") for group in document.groups))
        identity = analysis["snapshots"][0]["id"]
        modes = {identity: {"baseline-task": "native", "candidate-task": "callgrind"}}
        _, document = self.compile(analysis, capture_modes=modes)
        self.assertEqual({group["data-capture"] for group in document.groups}, {"native", "callgrind"})
        modes[identity]["candidate-task"] = "native"
        _, document = self.compile(analysis, capture_modes=modes)
        self.assertEqual(len(document.groups), 2)
        self.assertEqual(len(document.points), 2)
        # Even the same task across commits cannot form a trend while its
        # task-level runtime/build settings are absent from analysis metadata.
        analysis = analyze(self.fixture.snapshot())
        analysis["series"] = [item for item in analysis["series"]
                              if item["task"] == "baseline-task" and item["metric"] == "completion_ns"]
        self.with_next_capture(analysis)
        _, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 2)
        self.assertEqual(len(document.points), 2)

    def test_missing_dirty_unqualified_and_censored_records_are_retained_without_zero_points(self):
        scenarios = ("archive", "dirty", "unqualified", "timeout", "missing")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                analysis = self.simple_analysis()
                item = analysis["series"][0]
                source = analysis["snapshots"][0]["tools"]["gammaloop"]
                if scenario == "archive":
                    source.update(kind="archive", revision="v1.2")
                elif scenario == "dirty":
                    source["dirty"] = True
                elif scenario == "unqualified":
                    item["validation_status"] = "unqualified"
                elif scenario == "timeout":
                    item["statuses"] = {"completed": 2, "timeout": 1}
                else:
                    item.update(count=0, median=None, minimum=None, maximum=None, samples=[])
                analysis["availability"] = [{"case": "four-loop", "engine": "linnet", "status": "censored",
                                             "snapshot_id": item["snapshot_id"], "reason": "retained failure"}]
                page, document = self.compile(analysis)
                self.assertEqual(len(document.points), 0)
                self.assertEqual(len(document.unplotted), 1)
                self.assertEqual(len(document.availability), 1)
                self.assertIn("retained failure", page)
                self.assertNotIn('data-median="0"', page)

    def test_unsafe_metadata_is_literal_and_unknown_contexts_do_not_join(self):
        analysis = self.simple_analysis()
        snapshot, item = self.with_next_capture(analysis)
        for capture in analysis["snapshots"]:
            capture["host"] = {}
        _, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 2)
        item["engine"] = '<script>alert("unsafe")</script>'
        item["validation_reason"] = '<img src=x onerror="unsafe">'
        item["validation_status"] = "unqualified"
        page, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 2)
        self.assertIn("&lt;script", page)
        self.assertNotIn('<script>alert("unsafe")</script>', page)
        self.assertIn("&lt;img", page)

    def test_nix_variants_plot_actual_subject_commits_with_full_build_evidence(self):
        analysis = analyze(nix_capture(self.fixture, paired=True))
        analysis["series"] = [item for item in analysis["series"] if item["metric"] == "completion_ns"]
        page, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 1)
        self.assertEqual([point["data-commit"] for point in document.points], ["a" * 40, "b" * 40])
        self.assertEqual([point["data-median"] for point in document.points], ["20", "10"])
        self.assertEqual({point["data-repository"] for point in document.points},
                         {"https://github.com/alphal00p/gammaloop"})
        self.assertIn("Nix build and measured runtime evidence", page)
        self.assertIn("/nix/store/" + "a" * 40 + "-driver/bin/driver", page)
        self.assertIn("RAYON_NUM_THREADS", page)

    def test_nix_failed_censored_dirty_archive_and_incomplete_context_stay_as_evidence(self):
        for scenario in ("unqualified", "timeout", "dirty", "archive", "missing-context"):
            with self.subTest(capture=scenario):
                def change(snapshot, suite):
                    if scenario == "dirty":
                        snapshot["provenance"]["nix_build"]["sources"]["engine"]["dirty"] = True
                    elif scenario == "archive":
                        snapshot["provenance"]["nix_build"]["sources"]["engine"].update(kind="archive", revision="v1")
                    elif scenario == "missing-context":
                        snapshot["provenance"]["nix_build"]["comparison_context"] = {}
                    elif scenario == "unqualified":
                        snapshot["validation"]["tasks"]["baseline-task"]["status"] = "unqualified"
                    else:
                        record = next(record for record in snapshot["records"]
                                      if record["task"] == "baseline-task" and record["phase"] == "measure")
                        record.update(status="timeout", metrics={})
                analysis = analyze(nix_capture(self.fixture, change=change))
                analysis["series"] = [item for item in analysis["series"]
                                      if item["task"] == "baseline-task" and item["metric"] == "completion_ns"]
                page, document = self.compile(analysis)
                self.assertEqual(len(document.points), 0)
                self.assertEqual(len(document.unplotted), 1)
                self.assertNotIn('data-median="0"', page)

    def test_preserved_full_diagram_history_uses_actual_parent_and_candidate_pins(self):
        repository = Path(__file__).resolve().parents[1]
        analysis = json.loads((repository / "docs/results/generation-full-public-20261014/analysis.json").read_text())
        _, document = self.compile(analysis)
        group = next(group for group in document.groups
                     if group["data-case"] == "qcd_2pt_l4" and group["data-metric"] == "full_diagrams_ns")
        points = [point for point in document.points if point["data-series-id"] == group["data-series-id"]]
        self.assertEqual([point["data-commit"] for point in points],
                         ["014b40fef7d9dc06a1f82dd48446b050b9e9f466", "d83bfc69055908a64808ffbbe7ea345b5f5a8ec5"])
        self.assertEqual([point["data-median"] for point in points], ["3084733011", "2965711102"])
        self.assertEqual([point["data-count"] for point in points], ["15", "15"])


if __name__ == "__main__":
    unittest.main()
