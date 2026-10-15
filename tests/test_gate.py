import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gammabench.gate import qualified, run, validate_policy
from gammabench.report import analyze
from gammabench.snapshot import load, sha256, verify


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.adapter = self.root / "adapter.py"
        self.adapter.write_text("""import json, sys
from pathlib import Path
side, destination, mode = sys.argv[1:]
print("PRIVATE-LOG-SENTINEL")
value = 13 if side == "candidate" and mode == "slow" else 10
output = 2 if side == "candidate" and mode == "wrong" else 1
if mode == "fail": raise SystemExit(1)
Path(destination).write_text(json.dumps({"metrics": {"seconds": value},
    "outputs": {"reference": [output, 0]}, "metadata": {"samples": 10}}))
""")
        self.suite = {
            "schema_version": 1,
            "runner": "paired-gate-v1",
            "id": "gammaloop-ci-demo",
            "description": "Test gate",
            "contracts": {
                "numeric": {
                    "description": "Numerical equivalence",
                    "included": ["operation"],
                    "excluded": ["setup"],
                }
            },
            "metrics": {
                "seconds": {
                    "unit": "s",
                    "scope": "test operation",
                    "direction": "lower",
                    "result_field": "metrics.seconds",
                }
            },
            "tools": {
                "python": {
                    "kind": "executable",
                    "path": sys.executable,
                    "version": "test",
                    "sha256": sha256(Path(sys.executable)),
                }
            },
            "gate_adapter": "adapter.py",
            "tasks": [],
            "ci": {"repository_url": "https://github.com/alphal00p/gammaloop"},
            "gate": {
                side: {
                    "commit": char * 40,
                    "fixture_sha256": "1" * 64,
                    "corpus_sha256": "2" * 64,
                    "config_sha256": "3" * 64,
                }
                for side, char in (("baseline", "a"), ("candidate", "b"))
            },
        }
        for side in ("baseline", "candidate"):
            self.suite["tasks"].append(
                {
                    "id": side,
                    "case": "demo",
                    "engine": "example",
                    "variant": side,
                    "contract": "numeric",
                    "tool_ids": ["python"],
                    "inputs": {"adapter": "adapter.py"},
                    "argv": [
                        "{tool:python}",
                        "{suite_dir}/adapter.py",
                        side,
                        "{result}",
                        "clean",
                    ],
                }
            )
        self.policy = {
            "schema_version": 1,
            "pairs": 5,
            "budget_seconds": 10,
            "thresholds": {"seconds": [0.2, 0.5]},
            "output_groups": ["reference"],
            "reference_group": "reference",
            "correctness_tolerance": {"relative": 1e-9, "absolute": 0},
        }

    def capture(self, mode="clean"):
        for task in self.suite["tasks"]:
            task["argv"][-1] = mode
        (self.root / "suite.json").write_text(json.dumps(self.suite))
        (self.root / "policy.json").write_text(json.dumps(self.policy))
        status = run(
            self.root / "suite.json",
            self.root / "public",
            policy_path=self.root / "policy.json",
            private_work_dir=self.root / "private",
            label="test-capture",
        )
        return status, self.root / "public"

    def test_success_seals_relocatable_numeric_evidence_and_alternates_every_pair(self):
        status, public = self.capture()
        self.assertEqual(status, 0)
        snapshot = load(public / "discovery")
        self.assertTrue(qualified(snapshot))
        self.assertEqual(
            [r["variant"][0] for r in snapshot["records"]], list("bccbbccbbc")
        )
        self.assertFalse((public / "confirmation").exists())
        for path in public.rglob("*.json"):
            self.assertNotIn("PRIVATE-LOG-SENTINEL", path.read_text())
        self.assertIn(
            "PRIVATE-LOG-SENTINEL",
            next((self.root / "private").rglob("adapter.log")).read_text(),
        )
        shutil.move(public, self.root / "moved")
        verify(self.root / "moved/discovery")
        analysis = analyze(self.root / "moved/discovery", baseline="baseline")
        self.assertEqual(analysis["comparisons"][0]["status"], "comparable")
        self.assertEqual(
            {item["history"]["revision"] for item in analysis["series"]},
            {"a" * 40, "b" * 40},
        )

    def test_slow_budget_is_explicit_bounded_and_keeps_fast_default(self):
        for budget in (241, 7200):
            with self.assertRaises(ValueError):
                validate_policy(self.policy | {"budget_seconds": budget}, self.suite)
        slow = self.policy | {"budget_class": "slow", "budget_seconds": 7200}
        self.assertEqual(validate_policy(slow, self.suite), slow)
        for changed in (
            {"budget_seconds": 7201},
            {"budget_seconds": True},
            {"budget_class": "unbounded"},
        ):
            with self.assertRaises(ValueError):
                validate_policy(slow | changed, self.suite)

    def test_decimal_certificate_outputs_are_sealed_and_requalified(self):
        certificate = {"id": "source-zero", "sha256": "c" * 64}
        self.policy.update(
            correctness_format="decimal",
            certified_zero={
                "certificate": certificate,
                "components": [{"index": 0, "absolute_bound": "1e-150"}],
            },
            correctness_tolerance={"relative": 1e-5, "absolute": 0},
        )
        self.adapter.write_text("""import json, sys
from pathlib import Path
side, destination, mode = sys.argv[1:]
Path(destination).write_text(json.dumps({"metrics": {"seconds": 10},
    "outputs": {"reference": ["1e-411" if side == "baseline" else "2e-411", "3e-158"]},
    "metadata": {"precision_bits": 1000},
    "certificate": {"id": "source-zero", "sha256": "c"*64}}))
""")
        status, public = self.capture()
        self.assertEqual(status, 0)
        snapshot = load(public / "discovery")
        self.assertTrue(qualified(snapshot))
        self.assertEqual(
            snapshot["records"][0]["result"]["outputs"]["reference"],
            ["1e-411", "3e-158"],
        )
        report = snapshot["provenance"]["ci_gate"]["report"]
        self.assertEqual(report["strict_correctness_mismatches"], 5)
        verify(public / "discovery")
        snapshot["records"][0]["result"]["certificate"]["sha256"] = "d" * 64
        self.assertFalse(qualified(snapshot))

    def test_correctness_failure_does_not_skip_required_timing_confirmation(self):
        self.adapter.write_text(
            self.adapter.read_text().replace('mode == "slow"', 'mode == "wrong"')
        )
        status, public = self.capture("wrong")
        self.assertEqual(status, 1)
        self.assertTrue((public / "confirmation").exists())
        self.assertFalse(qualified(load(public / "confirmation")))

    def test_regression_confirms_independently_without_pooling_rounds(self):
        status, public = self.capture("slow")
        self.assertEqual(status, 1)
        a, b = load(public / "discovery"), load(public / "confirmation")
        self.assertNotEqual(a["schedule"]["pairing_id"], b["schedule"]["pairing_id"])
        self.assertEqual(b["records"][0]["variant"], "candidate")
        analysis = analyze(
            [public / "discovery", public / "confirmation"], baseline="baseline"
        )
        self.assertEqual(
            sum(c["status"] == "comparable" for c in analysis["comparisons"]), 2
        )
        self.assertTrue(all(item["count"] == 5 for item in analysis["series"]))

    def test_correctness_failure_suppresses_comparison(self):
        status, public = self.capture("wrong")
        self.assertEqual(status, 1)
        self.assertFalse(qualified(load(public / "discovery")))
        self.assertEqual(
            analyze(public / "discovery")["comparisons"][0]["status"], "suppressed"
        )

    def test_failed_adapter_retains_failure_without_public_logs(self):
        status, public = self.capture("fail")
        self.assertEqual(status, 2)
        snapshot = load(public / "discovery")
        self.assertEqual(snapshot["status"], "failed")
        self.assertTrue(all(not record["metrics"] for record in snapshot["records"]))

    def test_deadline_is_shared_and_seals_timeout(self):
        self.policy["budget_seconds"] = 0.001
        status, public = self.capture()
        self.assertEqual(status, 2)
        self.assertTrue(
            any(r["status"] == "timeout" for r in load(public / "discovery")["records"])
        )

    def test_confirmation_cannot_reset_the_shared_deadline(self):
        now, deadlines = [100.0], []

        def trial(command, cwd, log, deadline, environment):
            deadlines.append(deadline)
            side, output = command[2:4]
            value = 13 if side == "candidate" else 10
            Path(output).write_text(
                json.dumps(
                    {
                        "metrics": {"seconds": value},
                        "outputs": {"reference": [1, 0]},
                        "metadata": {},
                    }
                )
            )
            now[0] += 0.5 if len(deadlines) <= 10 else 6

        with (
            patch("gammabench.gate.run_private", side_effect=trial),
            patch("gammabench.gate.time.monotonic", side_effect=lambda: now[0]),
        ):
            status, public = self.capture()
        self.assertEqual(set(deadlines), {110.0})
        self.assertEqual(status, 2)
        self.assertTrue((public / "confirmation").exists())
        self.assertTrue(
            any(
                r["status"] == "timeout"
                for r in load(public / "confirmation")["records"]
            )
        )

    def test_numerical_proof_is_recomputed_from_actual_records(self):
        _, public = self.capture()
        snapshot = load(public / "discovery")
        changed = copy.deepcopy(snapshot)
        changed["records"][1]["result"]["outputs"]["reference"][0] = 999
        self.assertFalse(qualified(changed))

    def test_existing_capture_and_overlapping_private_directory_are_rejected(self):
        self.capture()
        with self.assertRaises(FileExistsError):
            run(
                self.root / "suite.json",
                self.root / "public",
                policy_path=self.root / "policy.json",
                private_work_dir=self.root / "another-private",
            )
        with self.assertRaises(ValueError):
            run(
                self.root / "suite.json",
                self.root / "other",
                policy_path=self.root / "policy.json",
                private_work_dir=self.root / "other/private",
            )
