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
import test_gate as gate_fixtures
from test_history_context import nix_capture
from gammabench.metadata import project_implementation
from gammabench.snapshot import load


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

    def compile(self, analysis, *, reports=None, capture_modes=None, proposals=None):
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
        proposed = self.root / (stem + "-proposals.json")
        proposed.write_text(json.dumps(proposals or {}))
        template.write_text('#import "history.typ": benchmark-history\n'
                            f'#benchmark-history((json("{data.name}"),), reports: json("{links.name}"), capture-modes: json("{modes.name}"), proposals: json("{proposed.name}"), subjects: '
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

    def ci_analysis(self):
        fixture = gate_fixtures.GateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        _, root = fixture.capture("slow")
        paths = [root / "discovery", root / "confirmation"]
        snapshots = {load(path)["id"]: load(path) for path in paths}
        analysis = analyze(paths)
        for snapshot in analysis["snapshots"]:
            snapshot["created_at"] = "2026-10-14T12:00:00Z"
        for item in analysis["series"]:
            item["implementation"] = project_implementation(snapshots[item["snapshot_id"]], item,
                                                              values={"implementation.way": "native"})
        return analysis

    def append_ci_capture(self, analysis, identifier, revision, date, *, case="demo", suite=None):
        snapshot = copy.deepcopy(analysis["snapshots"][0])
        original = snapshot["id"]
        snapshot.update(id=identifier, created_at=date)
        if suite:
            snapshot["suite_id"] = suite
        analysis["snapshots"].append(snapshot)
        rows = []
        for item in list(analysis["series"]):
            if item["snapshot_id"] == original:
                row = copy.deepcopy(item)
                row.update(id=identifier + ":" + item["task"] + ":" + item["metric"], snapshot_id=identifier, case=case)
                row["history"]["revision"] = revision
                rows.append(row)
        analysis["series"].extend(rows)
        return rows

    def test_ci_history_selects_candidates_and_keeps_confirmation_out_of_capture_order(self):
        analysis = self.ci_analysis()
        self.append_ci_capture(analysis, "next", "c" * 40, "2026-10-15T12:00:00Z")
        page, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 1)
        self.assertEqual([point["data-commit"] for point in document.points], ["b" * 40, "c" * 40])
        self.assertEqual([point["data-capture-index"] for point in document.points], ["0", "1"])
        self.assertTrue(all(point["data-task"] == "candidate" for point in document.points))
        self.assertIn("Paired baselines and confirmation measurements remain in Compare and Evidence", page)
        self.assertEqual(len(analysis["snapshots"]), 3)
        self.assertEqual(len(analysis["series"]), 6)

    def test_ci_candidate_failures_remain_gaps_and_context_changes_stay_separate(self):
        analysis = self.ci_analysis()
        failed = self.append_ci_capture(analysis, "failed", "c" * 40, "2026-10-15T12:00:00Z")
        for item in failed:
            if item["variant"] == "candidate":
                item.update(validation_status="failed", validation_reason="numerical mismatch")
        changed = self.append_ci_capture(analysis, "changed", "d" * 40, "2026-10-16T12:00:00Z")
        for item in changed:
            item["history"]["context"]["host"]["cpu"] = "different CPU"
        _, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 2)
        self.assertEqual(len(document.unplotted), 1)
        self.assertEqual(document.unplotted[0]["data-task"], "candidate")
        self.assertEqual(document.unplotted[0]["data-capture-index"], "1")
        self.assertEqual([point["data-capture-index"] for point in document.points], ["0", "2"])

    def test_ci_workloads_keep_independent_capture_indices_and_sort_late_publications(self):
        analysis = self.ci_analysis()
        self.append_ci_capture(analysis, "other-new", "c" * 40, "2026-10-15T12:00:00Z", case="other", suite="other-suite")
        self.append_ci_capture(analysis, "new", "c" * 40, "2026-10-15T12:00:00Z")
        self.append_ci_capture(analysis, "other-old", "b" * 40, "2026-10-14T12:00:00Z", case="other", suite="other-suite")
        _, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 2)
        for group in document.groups:
            points = [point for point in document.points if point["data-series-id"] == group["data-series-id"]]
            self.assertEqual([point["data-capture-index"] for point in points], ["0", "1"])
            self.assertEqual([point["data-commit"] for point in points], ["b" * 40, "c" * 40])

    def test_ci_corpus_and_settings_changes_split_the_tool_history(self):
        for field in ("corpus_sha256", "config_sha256"):
            with self.subTest(field=field):
                analysis = self.ci_analysis()
                self.append_ci_capture(analysis, "changed-input", "c" * 40, "2026-10-15T12:00:00Z")
                analysis["snapshots"][-1]["provenance"]["ci_gate"]["identity"]["candidate"][field] = "f" * 64
                _, document = self.compile(analysis)
                self.assertEqual(len(document.points), 2)
                self.assertEqual(len(document.groups), 2)

    def test_proposed_candidate_is_marked_without_interrupting_main_capture_indices(self):
        analysis = self.ci_analysis()
        self.append_ci_capture(analysis, "proposal", "c" * 40, "2026-10-15T12:00:00Z")
        self.append_ci_capture(analysis, "next-main", "d" * 40, "2026-10-16T12:00:00Z")
        proposals = {"proposal": {"pr": 123, "baseline": "b" * 40}}
        page, document = self.compile(analysis, proposals=proposals)
        self.assertEqual([point["data-commit"] for point in document.points], ["b" * 40, "c" * 40, "d" * 40])
        self.assertEqual([point["data-capture-index"] for point in document.points], ["0", "-1", "1"])
        self.assertEqual([point["data-history-role"] for point in document.points], ["main", "proposed", "main"])
        self.assertIn("Proposed PR #123", page)

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

    def test_semantic_metadata_splits_history_but_descriptive_metadata_does_not(self):
        analysis = self.simple_analysis()
        _, item = self.with_next_capture(analysis)
        item["metadata"] = {"description": "Updated display label"}
        _, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 1)
        item["metadata_semantics"] = {"operation": {"value": "a different operation", "definition": {"type": "enum", "scope": "workload"}}}
        _, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 2)

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

    def test_declared_implementations_have_distinct_contexts_and_shared_overview_controls(self):
        analysis = analyze(nix_capture(self.fixture, paired=True))
        analysis["series"] = [item for item in analysis["series"] if item["metric"] == "completion_ns"]
        for item in analysis["series"]:
            way = "map" if item["variant"] == "candidate" else "iterate"
            item["implementation"] = {"tool_id": "linnet", "tool_label": "GammaLoop", "way_id": way,
                                      "way_label": way, "version_id": item["history"]["revision"],
                                      "version_label": item["history"]["revision"][:10],
                                      "revision": item["history"]["revision"], "source_kind": "git",
                                      "repository": item["history"]["repository"]}
        page, document = self.compile(analysis)
        self.assertEqual({group["data-way"] for group in document.groups}, {"map", "iterate"})
        self.assertEqual(len(document.groups), 2)
        self.assertEqual({point["data-created-at"] for point in document.points},
                         {analysis["snapshots"][0]["created_at"]})
        self.assertIn('id="history-overview-chart"', page)
        self.assertIn('id="history-visible-series"', page)
        self.assertIn("measured commit/run columns", page)
        self.assertIn('id="history-workloads"', page)
        self.assertIn('class="history-methodology"', page)

    def test_ci_run_identity_and_paired_ratio_reach_the_history_without_rewriting_analysis(self):
        analysis = self.ci_analysis()
        for snapshot in analysis["snapshots"]:
            snapshot["provenance"]["ci"] = {
                "run_id": 12345678, "run_attempt": 2, "baseline": "a" * 40,
            }
        _, document = self.compile(analysis)
        point = document.points[0]
        self.assertEqual(point["data-run-id"], "12345678")
        self.assertEqual(point["data-run-attempt"], "2")
        self.assertEqual(point["data-baseline-commit"], "a" * 40)
        paired = next(item for item in analysis["comparisons"] if item["candidate"] == point["data-source-series"])
        self.assertEqual(float(point["data-paired-ratio"]), paired["ratio"]["median"])
        # Suppressed and independent comparisons must never acquire a ratio in History.
        paired["status"] = "suppressed"
        _, suppressed = self.compile(analysis)
        self.assertNotIn("data-paired-ratio", suppressed.points[0])

    def test_repository_changes_cannot_share_a_history_context(self):
        analysis = analyze(nix_capture(self.fixture, paired=True))
        analysis["series"] = [item for item in analysis["series"] if item["metric"] == "completion_ns"]
        analysis["series"][1]["history"]["repository"] = "https://github.com/other/project"
        _, document = self.compile(analysis)
        self.assertEqual(len(document.groups), 2)
        self.assertEqual({point["data-repository"] for point in document.points},
                         {"https://github.com/alphal00p/gammaloop", "https://github.com/other/project"})

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
