import copy
import json
import math
import unittest

from gammabench.gate_policy import PairedGate

EVALUATOR_METHODS = ("Iterative", "SingleParametric", "Summed", "SummedFunctionMap")
THRESHOLDS = {
    "generation_seconds": (0.20, 0.5),
    "load_seconds": (0.20, 0.1),
    "generation_peak_ram_bytes": (0.20, 64 * 1024**2),
    **{
        f"{metric}.{method}": limits
        for metric, limits in {
            "evaluator_build_seconds": (0.20, 0.1),
            "warm_runtime_seconds_per_sample": (0.15, 1e-6),
        }.items()
        for method in EVALUATOR_METHODS
    },
}


BASELINE = "a" * 40
CANDIDATE = "b" * 40


def measurements(
    generation=10.0, warm=10e-6, *, confirmation=False, methods=("SingleParametric",)
):
    def phase(run_id, first):
        return {
            "run_id": run_id,
            "pairs": [
                {
                    "order": first
                    if index % 2 == 0
                    else "BA"
                    if first == "AB"
                    else "AB",
                    "baseline": {
                        "generation_seconds": 10.0,
                        **{
                            f"warm_runtime_seconds_per_sample.{method}": 10e-6
                            for method in methods
                        },
                    },
                    "candidate": {
                        "generation_seconds": generation,
                        **{
                            f"warm_runtime_seconds_per_sample.{method}": warm
                            for method in methods
                        },
                    },
                    "baseline_output": {
                        method: [1.25, -0.4, 0.1, 0.0] for method in methods
                    },
                    "candidate_output": {
                        method: [1.25, -0.4, 0.1, 0.0] for method in methods
                    },
                }
                for index in range(5)
            ],
        }

    identity = {
        "fixture_sha256": "1" * 64,
        "corpus_sha256": "2" * 64,
        "config_sha256": "3" * 64,
    }
    data = {
        "schema_version": 2,
        "output_groups": list(methods),
        "reference_group": methods[0],
        "baseline": {"commit": BASELINE, **identity},
        "candidate": {"commit": CANDIDATE, **identity},
        "required_metrics": [
            "generation_seconds",
            *[f"warm_runtime_seconds_per_sample.{method}" for method in methods],
        ],
        "correctness_tolerance": {"absolute": 1e-12, "relative": 1e-9},
        "discovery": phase(1, "AB"),
    }
    if confirmation:
        data["confirmation"] = phase(2, "BA")
    return data


class PerformanceGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = PairedGate(
            BASELINE,
            CANDIDATE,
            THRESHOLDS,
            EVALUATOR_METHODS,
            ("evaluator_build_seconds", "warm_runtime_seconds_per_sample"),
        )

    def test_clean_measurement_and_speedup_pass_without_confirmation(self):
        for data in (measurements(), measurements(8.0, 7e-6)):
            with self.subTest(data=data):
                self.assertEqual(self.gate.analyze(data)["status"], 0)

    def test_decimal_qualification_preserves_tiny_components_and_rejects_rounding(self):
        data = measurements(methods=EVALUATOR_METHODS)
        data["correctness_tolerance"] = {"relative": 1e-5, "absolute": 0}
        for pair in data["discovery"]["pairs"]:
            for side in ("baseline", "candidate"):
                pair[side + "_output"] = {
                    m: ["1e-411", "3e-158"] for m in EVALUATOR_METHODS
                }
        gate = PairedGate(
            BASELINE,
            CANDIDATE,
            THRESHOLDS,
            EVALUATOR_METHODS,
            correctness_format="decimal",
        )
        self.assertEqual(gate.analyze(data)["status"], 0)
        original = copy.deepcopy(data)
        for changed in ("0", "1.01e-411"):
            data["discovery"]["pairs"][0]["candidate_output"]["Summed"][0] = changed
            self.assertEqual(gate.analyze(data)["correctness_mismatches"], 1)
        for invalid in (0.0, "NaN", "Infinity", "1e-1005000", "1e1005000", "1" * 1025):
            data["discovery"]["pairs"][0]["candidate_output"]["Summed"][0] = invalid
            self.assertEqual(gate.analyze(data)["invalid_input"], 1)
        self.assertEqual(
            original["discovery"]["pairs"][0]["candidate_output"]["Summed"],
            ["1e-411", "3e-158"],
        )
        self.assertEqual(self.gate.analyze(original)["invalid_input"], 1)

    def test_certified_zero_is_component_local_and_requires_the_exact_witness(self):
        data = measurements()
        data["correctness_tolerance"] = {"relative": 1e-5, "absolute": 0}
        certificate = {"id": "analytic-zero-v1", "sha256": "a" * 64}
        policy = {
            "certificate": certificate,
            "components": [{"index": 0, "absolute_bound": "1e-150"}],
        }
        gate = PairedGate(
            BASELINE,
            CANDIDATE,
            THRESHOLDS,
            EVALUATOR_METHODS,
            correctness_format="decimal",
            certified_zero=policy,
        )
        for pair in data["discovery"]["pairs"]:
            for side in ("baseline", "candidate"):
                pair[side + "_certificate"] = certificate.copy()
                pair[side + "_output"] = {
                    "SingleParametric": [
                        "1e-411" if side == "baseline" else "2e-411",
                        "3e-158",
                    ]
                }
        original = copy.deepcopy(data)
        report = gate.analyze(data)
        self.assertEqual(report["status"], 0)
        self.assertEqual(report["strict_correctness_mismatches"], 5)
        self.assertEqual(report["certified_zero_components"], 1)
        self.assertEqual(data, original)
        for before, after in (("2e-150", "0"), ("0", "2e-150"), ("2e-150", "2e-150")):
            changed = copy.deepcopy(data)
            pair = changed["discovery"]["pairs"][0]
            pair["baseline_output"]["SingleParametric"][0] = before
            pair["candidate_output"]["SingleParametric"][0] = after
            self.assertGreater(gate.analyze(changed)["correctness_mismatches"], 0)
        changed = copy.deepcopy(data)
        changed["discovery"]["pairs"][0]["candidate_output"]["SingleParametric"][1] = (
            "3.01e-158"
        )
        self.assertEqual(gate.analyze(changed)["correctness_mismatches"], 1)
        for wrong in (
            None,
            {"id": "different", "sha256": "a" * 64},
            {"id": "analytic-zero-v1", "sha256": "b" * 64},
        ):
            changed = copy.deepcopy(data)
            changed["discovery"]["pairs"][0]["candidate_certificate"] = wrong
            self.assertEqual(gate.analyze(changed)["invalid_input"], 1)
        changed = copy.deepcopy(data)
        del changed["discovery"]["pairs"][0]["candidate_certificate"]
        self.assertEqual(gate.analyze(changed)["invalid_input"], 1)
        for invalid in (
            [],
            [{"index": True, "absolute_bound": "1e-150"}],
            [{"index": -1, "absolute_bound": "1e-150"}],
            [{"index": 0, "absolute_bound": "-1e-150"}],
            [{"index": 0, "absolute_bound": 0.0}],
            policy["components"] * 2,
        ):
            with self.assertRaises(ValueError):
                PairedGate(
                    BASELINE,
                    CANDIDATE,
                    THRESHOLDS,
                    EVALUATOR_METHODS,
                    correctness_format="decimal",
                    certified_zero=policy | {"components": invalid},
                )
        absent = PairedGate(
            BASELINE,
            CANDIDATE,
            THRESHOLDS,
            EVALUATOR_METHODS,
            correctness_format="decimal",
            certified_zero=policy
            | {"components": [{"index": 2, "absolute_bound": "1e-150"}]},
        )
        self.assertEqual(absent.analyze(data)["invalid_input"], 1)

    def test_regression_needs_independent_confirmation(self):
        report = self.gate.analyze(measurements(13.0, 13e-6))
        self.assertEqual(report["status"], 2)
        self.assertEqual(report["confirmation_required"], 1)
        self.assertEqual(report["invalid_input"], 0)
        confirmed = self.gate.analyze(measurements(13.0, 13e-6, confirmation=True))
        self.assertEqual(confirmed["status"], 1)

    def test_metric_thresholds_have_relative_and_absolute_floors(self):
        # Large percentage changes on tiny operations must not fail the gate.
        for metric, before, after in (
            ("generation_seconds", 0.1, 0.2),
            ("load_seconds", 0.01, 0.02),
            ("warm_runtime_seconds_per_sample.SingleParametric", 1e-7, 2e-7),
            ("evaluator_build_seconds.SingleParametric", 0.01, 0.02),
            ("generation_peak_ram_bytes", 1024**2, 2 * 1024**2),
        ):
            data = measurements(confirmation=True)
            data["required_metrics"] = [metric]
            for phase in (data["discovery"], data["confirmation"]):
                for pair in phase["pairs"]:
                    pair["baseline"] = {metric: before}
                    pair["candidate"] = {metric: after}
            with self.subTest(metric=metric):
                self.assertEqual(self.gate.analyze(data)["status"], 0)
        data = measurements(confirmation=True)
        data["required_metrics"] = ["load_seconds"]
        for phase in (data["discovery"], data["confirmation"]):
            for pair in phase["pairs"]:
                pair["baseline"] = {"load_seconds": 1.0}
                pair["candidate"] = {"load_seconds": 1.3}
        self.assertEqual(self.gate.analyze(data)["status"], 1)

    def test_build_and_memory_regressions_require_independent_confirmation(self):
        for metric, before, after in (
            ("evaluator_build_seconds.Iterative", 1.0, 1.3),
            ("generation_peak_ram_bytes", 256 * 1024**2, 384 * 1024**2),
        ):
            data = measurements(methods=("Iterative",), confirmation=True)
            data["required_metrics"] = [metric]
            for phase in (data["discovery"], data["confirmation"]):
                for pair in phase["pairs"]:
                    pair["baseline"] = {metric: before}
                    pair["candidate"] = {metric: after}
            with self.subTest(metric=metric):
                self.assertEqual(self.gate.analyze(data)["status"], 1)
                del data["confirmation"]
                report = self.gate.analyze(data)
                self.assertEqual(report["status"], 2)
                self.assertEqual(report["confirmation_required"], 1)

    def test_one_slow_method_is_not_hidden_by_faster_other_methods(self):
        data = measurements(warm=5e-6, methods=EVALUATOR_METHODS, confirmation=True)
        metric = "warm_runtime_seconds_per_sample.SummedFunctionMap"
        for phase in (data["discovery"], data["confirmation"]):
            for pair in phase["pairs"]:
                pair["candidate"][metric] = 14e-6
        report = self.gate.analyze(data)
        self.assertEqual(report["status"], 1)
        self.assertEqual(report["metrics"][metric]["status"], 1)
        self.assertEqual(
            report["metrics"]["warm_runtime_seconds_per_sample.Summed"]["status"], 0
        )

    def test_method_error_shared_by_both_revisions_fails_against_reference(self):
        data = measurements(methods=EVALUATOR_METHODS)
        data["reference_group"] = "SingleParametric"
        pair = data["discovery"]["pairs"][0]
        for side in ("baseline", "candidate"):
            pair[side + "_output"]["SummedFunctionMap"][0] = 2.0
        report = self.gate.analyze(data)
        self.assertEqual(report["status"], 1)
        self.assertEqual(report["correctness_mismatches"], 2)

    def test_method_outputs_and_reference_are_required_and_exact(self):
        changes = (
            lambda data: data.update(reference_group="missing"),
            lambda data: data.update(
                output_groups=["SingleParametric", "SingleParametric"]
            ),
            lambda data: data["required_metrics"].remove(
                "warm_runtime_seconds_per_sample.Iterative"
            ),
            lambda data: data["discovery"]["pairs"][0]["candidate_output"].pop(
                "Iterative"
            ),
            lambda data: data["discovery"]["pairs"][0]["candidate_output"].update(
                Other=[1.0, 0.0]
            ),
            lambda data: data["discovery"]["pairs"][0]["candidate_output"].update(
                Iterative=[math.nan, 0.0, 1.0, 0.0]
            ),
        )
        for change in changes:
            data = measurements(methods=EVALUATOR_METHODS)
            change(data)
            with self.subTest(data=data):
                self.assertEqual(self.gate.analyze(data)["invalid_input"], 1)

    def test_large_absolute_delta_below_relative_threshold_passes(self):
        self.assertEqual(self.gate.analyze(measurements(11.0))["status"], 0)

    def test_noisy_small_operations_respect_each_absolute_floor(self):
        for metric, (_, absolute_limit) in self.gate.THRESHOLDS.items():
            data = measurements(
                methods=(
                    metric.split(".")[-1] if "." in metric else "SingleParametric",
                )
            )
            data["required_metrics"] = [metric]
            before = absolute_limit / 10
            for pair, ratio in zip(
                data["discovery"]["pairs"], [0.7, 1.5, 1.0, 1.5, 0.7]
            ):
                pair["baseline"] = {metric: before}
                pair["candidate"] = {metric: before * ratio}
            with self.subTest(metric=metric):
                self.assertEqual(self.gate.analyze(data)["status"], 0)
                data["discovery"]["pairs"][0]["candidate_output"][
                    data["reference_group"]
                ][0] = 1.3
                report = self.gate.analyze(data)
                self.assertEqual(report["status"], 1)
                self.assertEqual(report["correctness_mismatches"], 1)

    def test_noisy_absolute_boundary_still_requires_an_inconclusive_result(self):
        # The same relative spread puts the absolute upper screening bound
        # just below the budget at scale 2.2, and just above it at scale 2.3.
        for metric, (_, absolute_limit) in self.gate.THRESHOLDS.items():
            for scale, expected in ((2.2, 0), (2.3, 2)):
                data = measurements(
                    methods=(
                        metric.split(".")[-1] if "." in metric else "SingleParametric",
                    )
                )
                data["required_metrics"] = [metric]
                before = absolute_limit * scale
                for pair, ratio in zip(
                    data["discovery"]["pairs"], [0.9, 1.2, 1.0, 1.2, 0.9]
                ):
                    pair["baseline"] = {metric: before}
                    pair["candidate"] = {metric: before * ratio}
                with self.subTest(metric=metric, scale=scale):
                    self.assertEqual(self.gate.analyze(data)["status"], expected)

    def test_single_slow_outlier_does_not_gate(self):
        data = measurements()
        data["discovery"]["pairs"][-1]["candidate"]["generation_seconds"] = 100.0
        self.assertEqual(self.gate.analyze(data)["status"], 0)

    def test_noisy_measurements_do_not_silently_pass(self):
        data = measurements(confirmation=True)
        for phase in (data["discovery"], data["confirmation"]):
            for pair, value in zip(phase["pairs"], [7.0, 15.0, 10.0, 15.0, 7.0]):
                pair["candidate"]["generation_seconds"] = value
        report = self.gate.analyze(data)
        self.assertEqual(report["status"], 2)
        self.assertEqual(report["invalid_input"], 0)

    def test_uncertainty_crossing_threshold_is_inconclusive(self):
        data = measurements()
        for pair, value in zip(
            data["discovery"]["pairs"], [11.9, 11.8, 12.0, 11.8, 12.1]
        ):
            pair["candidate"]["generation_seconds"] = value
        self.assertEqual(self.gate.analyze(data)["status"], 2)

    def test_at_least_four_pairs_must_be_slower(self):
        data = measurements(confirmation=True)
        for phase in (data["discovery"], data["confirmation"]):
            for pair, value in zip(phase["pairs"], [9.0, 9.0, 12.5, 12.6, 12.7]):
                pair["candidate"]["generation_seconds"] = value
        self.assertEqual(self.gate.analyze(data)["status"], 2)

    def test_confirmation_must_reproduce_discovery(self):
        data = measurements(13.0, confirmation=True)
        for pair in data["confirmation"]["pairs"]:
            pair["candidate"]["generation_seconds"] = 10.0
        self.assertEqual(self.gate.analyze(data)["status"], 2)

    def test_incomplete_phase_and_missing_metrics_are_invalid(self):
        cases = []
        data = measurements()
        data["discovery"]["pairs"].pop()
        cases.append(data)
        data = measurements()
        del data["discovery"]["pairs"][2]["candidate"]["generation_seconds"]
        cases.append(data)
        data = measurements()
        data["required_metrics"] = []
        cases.append(data)
        data = measurements()
        data["required_metrics"].append("generation_seconds")
        cases.append(data)
        for data in cases:
            with self.subTest(data=data):
                report = self.gate.analyze(data)
                self.assertEqual(report["status"], 2)
                self.assertEqual(report["invalid_input"], 1)

    def test_invalid_nonfinite_and_overflowing_timings_are_inconclusive(self):
        for value in (math.nan, math.inf, -math.inf, 0.0, -1.0, True, "1.0", 10**400):
            data = measurements()
            data["discovery"]["pairs"][0]["candidate"]["generation_seconds"] = value
            with self.subTest(value=value):
                report = self.gate.analyze(data)
                self.assertEqual(report["status"], 2)
                self.assertEqual(report["invalid_input"], 1)
                json.dumps(report, allow_nan=False)
        data = measurements()
        pair = data["discovery"]["pairs"][0]
        pair["baseline"]["generation_seconds"] = 1e-308
        pair["candidate"]["generation_seconds"] = 1e308
        self.assertEqual(self.gate.analyze(data)["invalid_input"], 1)

    def test_correctness_mismatch_fails_without_timing_confirmation(self):
        data = measurements(8.0, 7e-6)
        data["discovery"]["pairs"][0]["candidate_output"][data["reference_group"]][
            0
        ] = 1.3
        report = self.gate.analyze(data)
        self.assertEqual(report["status"], 1)
        self.assertEqual(report["correctness_mismatches"], 1)

    def test_numerical_tolerance_allows_roundoff(self):
        data = measurements()
        data["discovery"]["pairs"][0]["candidate_output"][data["reference_group"]][
            0
        ] += 1e-11
        self.assertEqual(self.gate.analyze(data)["status"], 0)

    def test_scalar_tolerance_detects_changes_smaller_than_physical_absolute_floor(
        self,
    ):
        data = measurements()
        data["correctness_tolerance"] = {"absolute": 0.0, "relative": 1e-9}
        for pair in data["discovery"]["pairs"]:
            pair["baseline_output"][data["reference_group"]] = [8e-12, 4e-14]
            pair["candidate_output"][data["reference_group"]] = [8.001e-12, 4e-14]
        self.assertEqual(self.gate.analyze(data)["status"], 1)
        for pair in data["discovery"]["pairs"]:
            pair["candidate_output"][data["reference_group"]] = [
                8e-12 * (1 + 1e-11),
                4e-14,
            ]
        self.assertEqual(self.gate.analyze(data)["status"], 0)

    def test_zero_physical_reference_is_invalid_and_zero_candidate_fails(self):
        data = measurements()
        pair = data["discovery"]["pairs"][0]
        pair["baseline_output"][data["reference_group"]] = [0.0] * 4
        pair["candidate_output"][data["reference_group"]] = [0.0] * 4
        report = self.gate.analyze(data)
        self.assertEqual(report["status"], 2)
        self.assertEqual(report["invalid_input"], 1)
        pair["baseline_output"][data["reference_group"]] = [1e-15, 0.0, 1e-15, 0.0]
        report = self.gate.analyze(data)
        self.assertEqual(report["status"], 1)
        self.assertEqual(report["correctness_mismatches"], 2)

    def test_invalid_correctness_data_does_not_pass(self):
        for value in ([], [math.nan], [1.0], "bad"):
            data = measurements()
            data["discovery"]["pairs"][0]["candidate_output"][
                data["reference_group"]
            ] = value
            with self.subTest(value=value):
                self.assertEqual(self.gate.analyze(data)["invalid_input"], 1)
        data = measurements()
        data["correctness_tolerance"]["absolute"] = -1.0
        self.assertEqual(self.gate.analyze(data)["invalid_input"], 1)

    def test_pin_and_shared_workload_identity_must_match(self):
        for key, value in (("commit", "c" * 40), ("corpus_sha256", "4" * 64)):
            data = measurements()
            data["candidate"][key] = value
            with self.subTest(key=key):
                report = self.gate.analyze(data)
                self.assertEqual(report["status"], 2)
                self.assertEqual(report["identity_mismatch"], 1)

    def test_pair_order_and_confirmation_independence_are_required(self):
        data = measurements()
        data["discovery"]["pairs"][1]["order"] = "AB"
        self.assertEqual(self.gate.analyze(data)["invalid_input"], 1)
        data = measurements(confirmation=True)
        data["confirmation"]["run_id"] = 1
        self.assertEqual(self.gate.analyze(data)["invalid_input"], 1)
        data = measurements(confirmation=True)
        data["confirmation"] = copy.deepcopy(data["discovery"])
        data["confirmation"]["run_id"] = 2
        self.assertEqual(self.gate.analyze(data)["invalid_input"], 1)

    def test_unknown_schema_or_fields_are_invalid(self):
        data = measurements()
        data["schema_version"] = 1
        self.assertEqual(self.gate.analyze(data)["invalid_input"], 1)
        data = measurements()
        data["debug_output"] = "This must never reach the report"
        self.assertEqual(self.gate.analyze(data)["invalid_input"], 1)

    def test_report_values_are_numeric_only(self):
        def check(value):
            if isinstance(value, dict):
                for child in value.values():
                    check(child)
            else:
                self.assertIn(type(value), (int, float))
                self.assertTrue(math.isfinite(value))

        check(self.gate.analyze(measurements(13.0, confirmation=True)))
