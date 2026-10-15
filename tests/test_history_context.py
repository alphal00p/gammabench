"""Nix histories vary one declared source while retaining build/runtime controls."""
import copy
import unittest

from gammabench.report import analyze
from gammabench.snapshot import load
import test_report as report_fixtures


def receipt(revision):
    source = {"kind": "git", "url": "https://github.com/alphal00p/gammaloop.git",
              "canonical_project_url": "https://github.com/alphal00p/gammaloop.git",
              "url_kind": "canonical-project", "revision": revision,
              "nar_hash": "subject-nar-" + revision, "store_path": "/nix/store/" + revision + "-source"}
    dependency = {"kind": "git", "url": "https://github.com/symbolica-dev/symbolica.git",
                  "revision": "d" * 40, "nar_hash": "fixed-dependency-nar"}
    executable = {"path": "/nix/store/" + revision + "-driver/bin/driver", "sha256": revision + "a" * 24}
    profile = {"opt_level": 3, "lto": "thin"}
    return {"schema_version": 1, "sources": {"engine": source, "symbolica": dependency},
            "subjects": {"linnet": "engine"},
            "build": {"system": "x86_64-linux", "nixpkgs": {"revision": "e" * 40},
                      "toolchain": {"path": "/nix/store/fixed-rust"}, "framework_sha256": "f" * 64,
                      "hyperfine": {"version": "2.0.0", "revision": "c" * 40},
                      "profile": profile, "adapter": {"path": "/nix/store/" + revision + "-driver"},
                      "executables": {"driver": executable}},
            "comparison_context": {"adapter_sha256": "1" * 64, "profile": profile,
                                   "dependencies": {"engine": copy.deepcopy(source), "symbolica": dependency}}}


def nix_capture(fixture, revision="a" * 40, *, change=None, paired=False):
    def prepare(snapshot, suite):
        versions = [("parent", "a" * 40), ("candidate", "b" * 40)] if paired else [("", revision)]
        receipts = {label: receipt(pin) for label, pin in versions}
        suite["tools"] = {}
        snapshot["tools"] = {}
        task_map = {}
        for index, task in enumerate(suite["tasks"]):
            label = versions[index][0] if paired else ""
            original = task["id"]
            task["id"] = label + ".generate" if paired else original
            task_map[original] = task
            name = label + ".driver" if paired else "driver"
            executable = receipts[label]["build"]["executables"]["driver"]
            suite["tools"][name] = {"kind": "executable", "version": "driver 1", **executable}
            snapshot["tools"][name] = {"kind": "executable", "tool_path": executable["path"], **executable}
            task.update(tool_ids=[name], argv=["{tool:" + name + "}", "native"],
                        environment={"RAYON_NUM_THREADS": "1", "BENCH_CASE_FILE": "{input:case}"})
            if paired:
                task["variant"] = label
        for record in snapshot["records"]:
            task = task_map[record["task"]]
            name = task["tool_ids"][0]
            executable = snapshot["tools"][name]
            record.update(task=task["id"], variant=task["variant"], cpu=2, measurement_backend="python",
                command=[executable["path"], "native"], command_files_sha256={executable["path"]: executable["sha256"]},
                environment={"RAYON_NUM_THREADS": "1", "BENCH_CASE_FILE": "/tmp/" + snapshot["id"] +
                             "/tasks/" + task["id"] + "/inputs/case"}, stdin="null", timeout_seconds=60)
        if paired:
            snapshot["validation"]["tasks"] = {task["id"]: {"status": "passed", "equivalence_fields": ["output"]}
                for task in suite["tasks"]}
            snapshot["validation"]["equivalence"][0]["tasks"] = [task["id"] for task in suite["tasks"]]
            build_receipt = {"schema_version": 1, "sources": {}, "subjects": {},
                "comparison_context": {"assembly": "packaged-variants-v1"},
                "build": {"variants": {label: {"package_path": "/nix/store/" + label + "-package",
                    "receipt": value, "original_task_variants": {"generate": "native"}}
                    for label, value in receipts.items()}}}
        else:
            build_receipt = receipts[""]
        snapshot["provenance"].update(nix_build=build_receipt, nix_build_receipt="build/nix-build-receipt.json",
            nix_build_receipt_sha256="2" * 64, controller_source_sha256="3" * 64,
            clock="time.perf_counter_ns", measurement_backend={"name": "python", "clock": "time.perf_counter_ns"})
        if change:
            change(snapshot, suite)
    return fixture.snapshot(change=prepare)



class NixHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = report_fixtures.ReportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def capture(self, *args, **kwargs):
        return nix_capture(self.fixture, *args, **kwargs)

    def first(self, path):
        return next(item for item in analyze(path)["series"] if item["metric"] == "completion_ns")

    def test_actual_source_is_used_instead_of_executable_or_adapter_identity(self):
        path = self.capture()
        original = load(path)
        analysis = analyze(path)
        history = analysis["series"][0]["history"]
        self.assertEqual(history["revision"], "a" * 40)
        self.assertEqual(history["repository"], "https://github.com/alphal00p/gammaloop")
        self.assertEqual(history["reason"], "")
        self.assertEqual(history["evidence"]["source"], original["provenance"]["nix_build"]["sources"]["engine"])
        self.assertEqual(history["evidence"]["executables"], original["provenance"]["nix_build"]["build"]["executables"])
        self.assertEqual(history["evidence"]["runtime_records"][0]["environment"]["RAYON_NUM_THREADS"], "1")
        self.assertEqual(load(path), original)
        # Existing statistics are still derived from their original raw samples.
        self.assertEqual({key: analysis["series"][0][key] for key in ("count", "median", "minimum", "maximum")},
                         {"count": 3, "median": 20, "minimum": 10, "maximum": 30})

    def test_symlinked_declared_executables_keep_their_resolved_runtime_binding(self):
        def symlink(snapshot, suite):
            original = snapshot["tools"]["driver"]["tool_path"]
            resolved = "/nix/store/resolved-driver/bin/driver"
            snapshot["tools"]["driver"]["tool_path"] = resolved
            for record in snapshot["records"]:
                record["command"][0] = resolved
                record["command_files_sha256"] = {resolved: record["command_files_sha256"][original]}
        history = self.first(self.capture(change=symlink))["history"]
        self.assertEqual(history["reason"], "")
        self.assertEqual(history["context"]["runtime"][0]["command"][0], "{tool:driver}")
        self.assertNotEqual(history["evidence"]["executables"]["driver"]["path"],
                            history["evidence"]["runtime_records"][0]["command"][0])

    def test_receipt_project_identity_controls_commit_links(self):
        def fork(snapshot, suite):
            source = snapshot["provenance"]["nix_build"]["sources"]["engine"]
            source["canonical_project_url"] = "https://github.com/example/fork.git"
            source["url"] = source["canonical_project_url"]
            snapshot["provenance"]["nix_build"]["comparison_context"]["dependencies"]["engine"] = copy.deepcopy(source)
        history = self.first(self.capture(change=fork))["history"]
        self.assertEqual(history["repository"], "https://github.com/example/fork")
        self.assertEqual(history["reason"], "")
        def locator(snapshot, suite):
            source = snapshot["provenance"]["nix_build"]["sources"]["engine"]
            source.pop("canonical_project_url")
            source["url"] = "github:example/fork"
            snapshot["provenance"]["nix_build"]["comparison_context"]["dependencies"]["engine"] = copy.deepcopy(source)
        history = self.first(self.capture(change=locator))["history"]
        self.assertEqual(history["repository"], "")
        self.assertEqual(history["revision"], "a" * 40)

    def test_missing_task_declaration_retains_values_without_a_fabricated_context(self):
        def missing(snapshot, suite):
            suite['tasks'] = [task for task in suite['tasks'] if task['id'] != 'baseline-task']
        analysis = analyze(self.capture(change=missing))
        item = next(item for item in analysis['series'] if item['task'] == 'baseline-task' and item['metric'] == 'completion_ns')
        self.assertEqual(item['median'], 20)
        self.assertEqual(item['count'], 3)
        self.assertIn('task declaration', item['history']['reason'])
        self.assertIsNone(item['history']['revision'])

    def test_changing_only_subject_revision_joins_across_captures(self):
        a = self.first(self.capture())
        b = self.first(self.capture("b" * 40, change=lambda snap, _: snap["host"].update(load_average_end=[99, 99, 99])))
        self.assertEqual(a["history"]["context"], b["history"]["context"])
        self.assertNotEqual(a["history"]["evidence"], b["history"]["evidence"])
        self.assertEqual(b["history"]["context"]["runtime"][0]["environment"]["BENCH_CASE_FILE"], "{input:case}")

    def test_composed_packages_resolve_inner_subjects_and_original_task_roles(self):
        analysis = analyze(self.capture(paired=True))
        a, b = [item for item in analysis["series"] if item["metric"] == "completion_ns"]
        self.assertEqual(a["history"]["context"], b["history"]["context"])
        self.assertEqual([item["history"]["revision"] for item in (a, b)], ["a" * 40, "b" * 40])
        self.assertEqual([item["history"]["evidence"]["variant"] for item in (a, b)], ["parent", "candidate"])
        self.assertEqual(a["history"]["evidence"]["original_task"], "generate")
        self.assertEqual(a["history"]["evidence"]["original_variant"], "native")

    def test_fixed_sources_build_harness_metric_and_actual_runtime_changes_split(self):
        reference = self.first(self.capture())["history"]["context"]
        def dependency(snap, suite):
            snap["provenance"]["nix_build"]["sources"]["symbolica"]["revision"] = "9" * 40
        def harness(snap, suite):
            snap["provenance"]["nix_build"]["comparison_context"]["adapter_sha256"] = "4" * 64
        def profile(snap, suite):
            snap["provenance"]["nix_build"]["build"]["profile"]["opt_level"] = 2
        def toolchain(snap, suite):
            snap["provenance"]["nix_build"]["build"]["toolchain"]["path"] += "-changed"
        def controller(snap, suite):
            snap["provenance"]["controller_source_sha256"] = "4" * 64
        def hardware(snap, suite):
            snap["host"]["cpu"] = "different CPU"
        def threads(snap, suite):
            for record in snap["records"]:
                record["environment"]["RAYON_NUM_THREADS"] = "8"
        def affinity(snap, suite):
            for record in snap["records"]:
                record["cpu"] = 3
        def overrides(snap, suite):
            snap["provenance"]["explicit_environment"] = {"OTHER_CONTROL": "1"}
        def boundary(snap, suite):
            snap["metrics"]["completion_ns"]["result_field"] = "different_region_ns"
            suite["metrics"] = copy.deepcopy(snap["metrics"])
        def declaration(snap, suite):
            suite["tasks"][0]["argv"].append("different-operation")
        def implementation(snap, suite):
            suite["tasks"][0]["variant"] = "different-algorithm"
        def contract(snap, suite):
            snap["contracts"]["full"]["included"].append("additional-operation")
            suite["contracts"] = copy.deepcopy(snap["contracts"])
        def workload(snap, suite):
            for record in snap["records"]:
                record["inputs_sha256"]["case"] = "5" * 64
        def capture(snap, suite):
            snap["provenance"]["nix_build"]["comparison_context"]["capture_mode"] = "callgrind"
        def semantic_output(snap, suite):
            for record in snap["records"]:
                record["result"]["output"] = "different-diagrams"
        for mutate in (dependency, harness, profile, toolchain, controller, hardware, threads, affinity,
                       overrides, boundary, declaration, implementation, contract, workload, capture, semantic_output):
            with self.subTest(context=mutate.__name__):
                changed = self.first(self.capture("b" * 40, change=mutate))["history"]
                self.assertEqual(changed["reason"], "")
                self.assertNotEqual(reference, changed["context"])

    def test_fixed_helper_executable_changes_split_even_with_identical_source_pins(self):
        def helper(digit):
            def change(snapshot, suite):
                executable = {"path": "/nix/store/" + digit + "-helper/bin/helper", "sha256": digit * 64}
                snapshot["provenance"]["nix_build"]["build"]["executables"]["helper"] = executable
                snapshot["tools"]["helper"] = {"kind": "executable", "tool_path": executable["path"], **executable}
                suite["tools"]["helper"] = {"kind": "executable", "version": "helper 1", **executable}
                for task in suite["tasks"]:
                    task["tool_ids"].append("helper")
                    task["argv"].append("{tool:helper}")
                for record in snapshot["records"]:
                    record["command"].append(executable["path"])
                    record["command_files_sha256"][executable["path"]] = executable["sha256"]
            return change
        a = self.first(self.capture(change=helper("7")))["history"]
        b = self.first(self.capture("b" * 40, change=helper("8")))["history"]
        self.assertEqual(a["reason"], b["reason"])
        self.assertEqual(a["reason"], "")
        self.assertNotEqual(a["context"], b["context"])
        self.assertEqual(a["context"]["fixed_executables"]["helper"]["sha256"], "7" * 64)
        self.assertNotIn("driver", a["context"]["fixed_executables"])

    def test_auxiliary_input_changes_split_including_receipt_like_filenames(self):
        def auxiliary(digit, name):
            def change(snapshot, suite):
                for task in suite["tasks"]:
                    task["inputs"][name] = name
                    task["workload_inputs"] = ["case"]
                for record in snapshot["records"]:
                    record["inputs_sha256"][name] = digit * 64
                    record["workload_inputs_sha256"] = {"case": record["inputs_sha256"]["case"]}
            return change
        for name in ("aux.json", "nix-build-receipt.json"):
            with self.subTest(input=name):
                a = self.first(self.capture(change=auxiliary("7", name)))["history"]
                b = self.first(self.capture("b" * 40, change=auxiliary("8", name)))["history"]
                self.assertEqual(a["reason"], b["reason"])
                self.assertEqual(a["reason"], "")
                self.assertNotEqual(a["context"], b["context"])
                self.assertEqual(a["context"]["runtime"][0]["input_hashes"][name], "7" * 64)
                self.assertEqual(a["context"]["runtime"][0]["receipt_inputs"], [])

    def test_only_byte_identified_nonworkload_receipts_follow_subject_revision(self):
        def metadata(snapshot, suite):
            paired = "variants" in snapshot["provenance"]["nix_build"]["build"]
            for task in suite["tasks"]:
                task["inputs"]["metadata.json"] = "metadata.json"
                task["workload_inputs"] = ["case"]
            for record in snapshot["records"]:
                digit = "7" if record["variant"] in ("parent", "old") else "8"
                digest = digit * 64
                if paired:
                    variant = snapshot["provenance"]["nix_build"]["build"]["variants"][record["variant"]]
                    variant["receipt_sha256"] = digest
                else:
                    digest = snapshot["provenance"]["nix_build_receipt_sha256"]
                record["inputs_sha256"]["metadata.json"] = digest
                record["workload_inputs_sha256"] = {"case": record["inputs_sha256"]["case"]}
        for paired in (False, True):
            with self.subTest(composed=paired):
                analysis = analyze(self.capture(change=metadata, paired=paired))
                values = [item["history"] for item in analysis["series"] if item["metric"] == "completion_ns"]
                self.assertTrue(all(value["reason"] == "" for value in values))
                self.assertTrue(all(value["context"]["runtime"][0]["receipt_inputs"] == ["metadata.json"] for value in values))
                self.assertTrue(all("metadata.json" not in value["context"]["runtime"][0]["input_hashes"] for value in values))
                if paired:
                    self.assertEqual(values[0]["context"], values[1]["context"])

    def test_missing_or_inconsistent_source_build_and_runtime_provenance_remains_unplotted(self):
        def archive(snap, suite):
            snap["provenance"]["nix_build"]["sources"]["engine"].update(kind="archive", revision="v1")
        def dirty(snap, suite):
            snap["provenance"]["nix_build"]["sources"]["engine"]["dirty"] = True
        def missing_subject(snap, suite):
            snap["provenance"]["nix_build"]["subjects"] = {}
        def missing_harness(snap, suite):
            snap["provenance"]["nix_build"]["comparison_context"].pop("adapter_sha256")
        def missing_environment(snap, suite):
            snap["records"][2].pop("environment")
        def varying_environment(snap, suite):
            snap["records"][3]["environment"]["RAYON_NUM_THREADS"] = "2"
        def changed_binary(snap, suite):
            snap["records"][2]["command_files_sha256"] = {snap["records"][2]["command"][0]: "9" * 64}
        def missing_executable(snap, suite):
            snap["records"][2]["command_files_sha256"] = {}
        def missing_witness(snap, suite):
            snap["validation"]["tasks"]["baseline-task"]["equivalence_fields"] = []
        def mismatched_subject(snap, suite):
            snap["provenance"]["nix_build"]["comparison_context"]["dependencies"]["engine"]["revision"] = "7" * 40
        for mutate in (archive, dirty, missing_subject, missing_harness, missing_environment,
                       varying_environment, changed_binary, missing_executable, missing_witness, mismatched_subject):
            with self.subTest(missing=mutate.__name__):
                history = self.first(self.capture(change=mutate))["history"]
                self.assertTrue(history["reason"])
                self.assertIn("unknown", history["context"])

    def test_old_controllers_do_not_join_independent_captures(self):
        def old(snapshot, suite):
            snapshot["provenance"].pop("controller_source_sha256")
        a = self.first(self.capture(change=old))["history"]["context"]
        b = self.first(self.capture("b" * 40, change=old))["history"]["context"]
        self.assertNotEqual(a, b)

    def test_missing_variant_mapping_and_archive_links_are_explicit(self):
        def broken(snapshot, suite):
            snapshot["provenance"]["nix_build"]["build"]["variants"]["parent"]["original_task_variants"] = {}
        history = self.first(self.capture(change=broken, paired=True))["history"]
        self.assertIn("unambiguous", history["reason"])
        self.assertIsNone(history["revision"])
        def archived(snapshot, suite):
            value = snapshot["provenance"]["nix_build"]["sources"]["engine"]
            value.update(kind="archive", revision="v1", url="https://example.org/source.tar.gz")
            value.pop("canonical_project_url")
        history = self.first(self.capture(change=archived))["history"]
        self.assertEqual(history["repository"], "")
        self.assertIsNone(history["revision"])


if __name__ == "__main__":
    unittest.main()
