import copy
import json
import hashlib
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from gammabench.runner import balanced_schedule, run
from gammabench.schema import SchemaError
from gammabench.snapshot import load, verify
from test_schema import example_suite


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.resolve = patch("gammabench.sources.resolve_tools", return_value={})
        self.verify = patch("gammabench.sources.verify_tools")
        self.resolver = self.resolve.start()
        self.verify.start()

    def tearDown(self):
        self.resolve.stop()
        self.verify.stop()
        self.temporary.cleanup()

    def execute(self, suite, **kwargs):
        path = self.directory / "suite.json"
        path.write_text(json.dumps(suite))
        output = run(path, self.directory / "result", repeats=kwargs.pop("repeats", 2),
                     warmups=kwargs.pop("warmups", 1), **kwargs)
        return load(output)

    def test_balanced_rounds_are_matched_and_warmups_separate(self):
        schedule = balanced_schedule(["a", "b", "c"], 4, 1)
        self.assertEqual([entry["task"] for entry in schedule[:3]], ["a", "b", "c"])
        rounds = [[item["task"] for item in schedule if item["phase"] == "measure" and item["repeat"] == repeat]
                  for repeat in range(4)]
        self.assertEqual(rounds, [["a", "b", "c"], ["c", "b", "a"], ["b", "c", "a"], ["a", "c", "b"]])

    def test_fresh_processes_metrics_and_qualification(self):
        suite = example_suite()
        second = copy.deepcopy(suite["tasks"][0])
        second.update(id="b", variant="b")
        suite["tasks"].append(second)
        snapshot = self.execute(suite, environment={"EXPLICIT_TEST_OVERRIDE": "visible"})
        self.assertEqual(len(snapshot["records"]), 8)
        self.assertEqual(len({row["pid"] for row in snapshot["records"]}), 8)
        self.assertEqual(snapshot["validation"]["status"], "passed")
        self.assertTrue(all(row["metrics"]["compute_ns"] == 10 for row in snapshot["records"]
                            if row["phase"] != "validation"))
        self.assertTrue(all(row["environment"] == {"EXPLICIT_TEST_OVERRIDE": "visible"} for row in snapshot["records"]))
        self.assertNotIn("HOME", snapshot["provenance"]["explicit_environment"])
        self.assertEqual(len([row for row in snapshot["records"] if row["phase"] == "measure"]), 4)
        verify(self.directory / "result")

    def test_timeout_has_pid_exit_and_no_metrics_even_if_result_was_emitted(self):
        suite = example_suite()
        suite["tasks"][0]["argv"] = ["{python}", "-u", "-c",
            "import time; print('RESULT {\"sum\":6,\"compute_ns\":10}'); time.sleep(10)"]
        snapshot = self.execute(suite, repeats=1, warmups=0, timeout=0.15)
        record = snapshot["records"][0]
        self.assertEqual(record["status"], "timeout")
        self.assertEqual(record["metrics"], {})
        self.assertGreater(record["pid"], 0)
        self.assertLess(record["returncode"], 0)
        self.assertEqual(snapshot["status"], "failed")

    def test_nonzero_exit_and_missing_metric_never_produce_zero(self):
        suite = example_suite()
        suite["tasks"][0]["argv"] = ["{python}", "-c", "raise SystemExit(3)"]
        snapshot = self.execute(suite, repeats=1, warmups=0)
        self.assertEqual(snapshot["records"][0]["metrics"], {})
        self.assertEqual(snapshot["records"][0]["returncode"], 3)

    def test_semantic_discrepancy_invalidates_entire_comparison(self):
        suite = example_suite()
        second = copy.deepcopy(suite["tasks"][0])
        second.update(id="b", variant="b", expected={"sum": 7},
                      argv=["{python}", "-c", "print('RESULT {\"sum\":7,\"compute_ns\":1}')"])
        suite["tasks"].append(second)
        snapshot = self.execute(suite, repeats=1, warmups=0)
        self.assertEqual(snapshot["validation"]["status"], "failed")
        self.assertTrue(all(record["status"] == "invalid" and not record["metrics"] for record in snapshot["records"]))
        for record in snapshot["records"]:
            raw = json.loads((self.directory / "result" / record["raw"]["record"]).read_text())
            self.assertEqual(raw, record)

    def test_validation_failure_prevents_measurement(self):
        suite = example_suite()
        suite["tasks"][0]["validation_command"] = ["{python}", "-c", "raise SystemExit(2)"]
        snapshot = self.execute(suite)
        self.assertEqual([row["phase"] for row in snapshot["records"]], ["validation"])
        self.assertEqual(snapshot["validation"]["tasks"]["a"]["status"], "failed")

    def test_command_has_no_implicit_shell_and_input_is_snapshotted(self):
        suite = example_suite()
        (self.directory / "input.txt").write_text("6")
        suite["tasks"][0]["inputs"] = {"value": "input.txt"}
        suite["tasks"][0]["argv"] = ["{python}", "-c",
            "import json,pathlib,sys; print('RESULT '+json.dumps({'sum':int(pathlib.Path(sys.argv[1]).read_text()),'compute_ns':10}))",
            "{input:value}", "$(touch unexpected-file)"]
        snapshot = self.execute(suite, repeats=1, warmups=0)
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual((self.directory / "result/tasks/a/inputs/value").read_text(), "6")
        self.assertFalse(any((self.directory / "result").rglob("unexpected-file")))

    def test_mutated_input_leaves_unsealed_partial(self):
        suite = example_suite()
        (self.directory / "input.txt").write_text("6")
        suite["tasks"][0]["inputs"] = {"value": "input.txt"}
        suite["tasks"][0]["argv"] = ["{python}", "-c",
            "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('7'); print('RESULT {\"sum\":6,\"compute_ns\":10}')",
            "{input:value}"]
        with self.assertRaisesRegex(SchemaError, "input changed"):
            self.execute(suite, repeats=1, warmups=0)
        self.assertFalse((self.directory / "result").exists())
        partial = list(self.directory.glob(".result.unsealed-*"))
        self.assertEqual(len(partial), 1)
        self.assertTrue((partial[0] / "unsealed.json").is_file())
        self.assertFalse((partial[0] / "files.sha256.json").exists())

    def test_inputs_are_fresh_files_in_each_child_working_directory(self):
        suite = example_suite()
        (self.directory / "workload.json").write_text("6")
        suite["tasks"][0]["inputs"] = {"value": "workload.json"}
        suite["tasks"][0]["argv"] = ["{python}", "-c",
            "import pathlib; p=pathlib.Path('workload.json'); v=int(p.read_text()); p.write_text('7'); print('RESULT {\"sum\":'+str(v)+',\"compute_ns\":10}')"]
        snapshot = self.execute(suite)
        self.assertEqual(snapshot["status"], "completed")
        self.assertTrue(all(row["result"]["sum"] == 6 for row in snapshot["records"]))
        self.assertEqual((self.directory / "result/tasks/a/inputs/value").read_text(), "6")

    def test_validation_only_equivalence_avoids_timing_normalization(self):
        suite = example_suite()
        second = copy.deepcopy(suite["tasks"][0])
        second.update(id="b", variant="b")
        for task, native in ((suite["tasks"][0], "first-numbering"), (second, "second-numbering")):
            task["equivalence_fields"] = []
            task["validation_equivalence_fields"] = ["canonical_sha256"]
            task["validation_command"] = ["{python}", "-c",
                "print('RESULT {\"sum\":6,\"canonical_sha256\":\"same-proof\"}')"]
            task["argv"] = ["{python}", "-c",
                "print('RESULT {\"sum\":6,\"compute_ns\":10,\"native_order\":\""+native+"\"}')"]
        suite["tasks"].append(second)
        snapshot = self.execute(suite, repeats=1, warmups=0)
        self.assertEqual(snapshot["validation"]["status"], "passed")
        self.assertEqual(snapshot["validation"]["equivalence"][0]["value"], {"canonical_sha256": "same-proof"})
        measured = [row for row in snapshot["records"] if row["phase"] == "measure"]
        self.assertNotEqual(measured[0]["result"]["native_order"], measured[1]["result"]["native_order"])
        self.assertEqual(snapshot["validation"]["tasks"]["a"]["equivalence_fields"], ["canonical_sha256"])

    def test_adapter_script_is_copied_and_bound(self):
        suite = example_suite()
        adapter = self.directory / "adapter.py"
        adapter.write_text("print('RESULT {\"sum\":6,\"compute_ns\":10}')\n")
        suite["tasks"][0]["argv"] = ["{python}", "{suite_dir}/adapter.py"]
        snapshot = self.execute(suite, repeats=1, warmups=0)
        binding = snapshot["provenance"]["harness_sources"][str(adapter)]
        self.assertEqual((self.directory / "result" / binding["archived_path"]).read_text(), adapter.read_text())
        self.assertIn(str(adapter), snapshot["records"][0]["command_files_sha256"])

    def test_failed_canonical_proof_prevents_all_measured_children(self):
        suite = example_suite()
        second = copy.deepcopy(suite["tasks"][0])
        second.update(id="b", variant="b")
        for task, proof in ((suite["tasks"][0], "proof-a"), (second, "proof-b")):
            task["validation_equivalence_fields"] = ["proof"]
            task["validation_command"] = ["{python}", "-c",
                "print('RESULT {\"sum\":6,\"proof\":\""+proof+"\"}')"]
        suite["tasks"].append(second)
        snapshot = self.execute(suite)
        self.assertEqual(snapshot["validation"]["status"], "failed")
        self.assertEqual([row["phase"] for row in snapshot["records"]], ["validation", "validation"])
        self.assertTrue(all(row["status"] == "invalid" and not row["metrics"] for row in snapshot["records"]))

    def test_dirty_source_archive_is_preserved_in_portable_snapshot(self):
        suite = example_suite()
        suite["tools"]["dirty"] = {"kind": "git", "url": "https://example.org/source", "revision": "a" * 40}
        suite["tasks"][0]["tool_ids"] = ["dirty"]
        archive = self.directory / "source.tar.gz"
        archive.write_bytes(b"explicit dirty source archive")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.resolver.return_value = {"dirty": {**suite["tools"]["dirty"], "dirty": True,
            "source_path": str(self.directory), "source_archive": str(archive), "source_archive_sha256": digest}}
        snapshot = self.execute(suite, repeats=1, warmups=0)
        binding = snapshot["tools"]["dirty"]
        preserved = self.directory / "result" / binding["source_archive_artifact"]
        self.assertEqual(preserved.read_bytes(), archive.read_bytes())
        self.assertEqual(binding["source_archive_artifact_sha256"], digest)
        archive.unlink()
        verify(self.directory / "result")

    def test_wrong_expected_without_custom_validation_prevents_measurement(self):
        suite = example_suite()
        suite["tasks"][0]["expected"] = {"sum": 7}
        snapshot = self.execute(suite)
        self.assertEqual([row["phase"] for row in snapshot["records"]], ["validation"])
        self.assertEqual(snapshot["records"][0]["status"], "invalid")
        self.assertEqual(snapshot["records"][0]["metrics"], {})
        self.assertEqual(snapshot["validation"]["tasks"]["a"]["status"], "failed")
        self.assertEqual(snapshot["validation"]["tasks"]["a"]["method"], "task-command")

    def test_tasks_without_checks_remain_unqualified_and_have_no_validation_child(self):
        suite = example_suite()
        suite["tasks"][0].pop("expected")
        suite["tasks"][0].pop("equivalence_fields")
        snapshot = self.execute(suite, repeats=1, warmups=1)
        self.assertEqual([row["phase"] for row in snapshot["records"]], ["warmup", "measure"])
        self.assertEqual(snapshot["validation"]["status"], "unqualified")
        self.assertEqual(snapshot["status"], "completed")

    def test_selected_workload_hashes_retain_all_auxiliary_inputs(self):
        suite = example_suite()
        (self.directory / "case.json").write_text("shared semantic workload")
        (self.directory / "backend.cfg").write_text("engine-specific configuration")
        suite["tasks"][0]["inputs"] = {"case": "case.json", "config": "backend.cfg"}
        suite["tasks"][0]["workload_inputs"] = ["case"]
        snapshot = self.execute(suite, repeats=1, warmups=1)
        expected = hashlib.sha256((self.directory / "case.json").read_bytes()).hexdigest()
        self.assertEqual([row["phase"] for row in snapshot["records"]], ["validation", "warmup", "measure"])
        for row in snapshot["records"]:
            self.assertEqual(row["workload_inputs_sha256"], {"case": expected})
            self.assertEqual(set(row["inputs_sha256"]), {"case", "config"})
        self.assertEqual((self.directory / "result/tasks/a/inputs/config").read_text(), "engine-specific configuration")
        self.assertEqual(snapshot["validation"]["status"], "passed")

    def test_default_workload_hashes_include_every_declared_input(self):
        suite = example_suite()
        (self.directory / "case.json").write_text("shared workload")
        suite["tasks"][0]["inputs"] = {"case": "case.json"}
        snapshot = self.execute(suite, repeats=1, warmups=0)
        for row in snapshot["records"]:
            self.assertEqual(row["workload_inputs_sha256"], row["inputs_sha256"])

    def test_validation_and_measurement_wait_block_without_timeout_polling(self):
        suite = example_suite()
        calls = []
        original_wait = subprocess.Popen.wait

        def observed_wait(process, timeout=None):
            calls.append(timeout)
            return original_wait(process, timeout=timeout)

        with patch("gammabench.runner.subprocess.Popen.wait", new=observed_wait):
            snapshot = self.execute(suite, repeats=1, warmups=0)
        self.assertEqual([row["phase"] for row in snapshot["records"]], ["validation", "measure"])
        self.assertEqual(calls, [None, None])
        self.assertEqual(snapshot["status"], "completed")

    def test_outside_affinity_is_rejected(self):
        if not hasattr(os, "sched_getaffinity"):
            self.skipTest("CPU affinity unavailable")
        suite = example_suite()
        with self.assertRaisesRegex(ValueError, "outside"):
            self.execute(suite, cpus=[max(os.sched_getaffinity(0)) + 1])


if __name__ == "__main__":
    unittest.main()
