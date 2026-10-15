"""Paired regression screening, ported from GammaLoop's schema-2 CI gate.

The caller owns metric thresholds and numerical output groups. This preserves
five-pair discovery/confirmation and the original MAD screening policy.
"""

from __future__ import annotations

import math
import re
import statistics


class InvalidMeasurements(ValueError):
    pass


class PairedGate:
    MAD_SCALE = 1.4826
    NOISE_MULTIPLIER = 3

    def __init__(
        self, baseline_commit, candidate_commit, thresholds, groups, metric_families=()
    ):
        self.baseline_commit = baseline_commit
        self.candidate_commit = candidate_commit
        self.THRESHOLDS = thresholds
        self.groups = groups
        self.metric_families = metric_families

    @staticmethod
    def number(value: object) -> float:
        if type(value) not in (int, float):
            raise InvalidMeasurements("Expected a finite number")
        try:
            number = float(value)
        except OverflowError as error:
            raise InvalidMeasurements("Number is out of range") from error
        if not math.isfinite(number):
            raise InvalidMeasurements("Expected a finite number")
        return number

    @staticmethod
    def fields(value: object, required: set[str], optional: set[str] = frozenset()):
        if not isinstance(value, dict) or not required <= value.keys():
            raise InvalidMeasurements("Missing required fields")
        if value.keys() - required - optional:
            raise InvalidMeasurements("Unexpected fields")
        return value

    @staticmethod
    def fingerprint(value: object, length: int):
        if not isinstance(value, str) or not re.fullmatch(
            f"[0-9a-f]{{{length}}}", value
        ):
            raise InvalidMeasurements("Invalid identity fingerprint")

    def validate_identity(self, data: dict, report: dict):
        keys = {"commit", "fixture_sha256", "corpus_sha256", "config_sha256"}
        baseline = self.fields(data["baseline"], keys)
        candidate = self.fields(data["candidate"], keys)
        for metadata in (baseline, candidate):
            self.fingerprint(metadata["commit"], 40)
            for key in keys - {"commit"}:
                self.fingerprint(metadata[key], 64)
        if (
            baseline["commit"] != self.baseline_commit
            or candidate["commit"] != self.candidate_commit
            or any(baseline[key] != candidate[key] for key in keys - {"commit"})
        ):
            report["identity_mismatch"] = 1
            raise InvalidMeasurements("Measurement identities do not match")

    def validate_phase(
        self,
        phase: object,
        metrics: list[str],
        methods: list[str],
        reference: str,
        tolerance: dict,
    ):
        phase = self.fields(phase, {"run_id", "pairs"})
        if type(phase["run_id"]) is not int or phase["run_id"] <= 0:
            raise InvalidMeasurements("Invalid run identifier")
        pairs = phase["pairs"]
        if not isinstance(pairs, list) or len(pairs) != 5:
            raise InvalidMeasurements("Exactly five pairs are required")
        results = {metric: [] for metric in metrics}
        mismatches = 0
        previous_order = None
        output_size = None
        for pair in pairs:
            pair = self.fields(
                pair,
                {
                    "order",
                    "baseline",
                    "candidate",
                    "baseline_output",
                    "candidate_output",
                },
            )
            order = pair["order"]
            if order not in ("AB", "BA") or order == previous_order:
                raise InvalidMeasurements("Pair order must alternate AB and BA")
            previous_order = order
            baseline = self.fields(pair["baseline"], set(metrics))
            candidate = self.fields(pair["candidate"], set(metrics))
            for metric in metrics:
                before = self.number(baseline[metric])
                after = self.number(candidate[metric])
                if before <= 0 or after <= 0:
                    raise InvalidMeasurements("Measurements must be positive")
                results[metric].append((before, after))
            outputs = {}
            for side in ("baseline", "candidate"):
                output = self.fields(pair[side + "_output"], set(methods))
                outputs[side] = {}
                for method in methods:
                    values = output[method]
                    if (
                        not isinstance(values, list)
                        or not values
                        or len(values) % 2
                        or (output_size is not None and len(values) != output_size)
                    ):
                        raise InvalidMeasurements(
                            "Correctness output vectors must match in complex-point count"
                        )
                    output_size = len(values)
                    outputs[side][method] = [self.number(value) for value in values]
            baseline_reference = outputs["baseline"][reference]
            if any(
                not any(baseline_reference[index : index + 2])
                for index in range(0, output_size, 2)
            ):
                raise InvalidMeasurements(
                    "Every physical correctness reference point must be nonzero"
                )
            for side in ("baseline", "candidate"):
                for values in outputs[side].values():
                    pair_mismatches = sum(
                        not math.isclose(
                            value,
                            own_reference,
                            rel_tol=tolerance["relative"],
                            abs_tol=tolerance["absolute"],
                        )
                        or not math.isclose(
                            value,
                            before_reference,
                            rel_tol=tolerance["relative"],
                            abs_tol=tolerance["absolute"],
                        )
                        for value, own_reference, before_reference in zip(
                            values, outputs[side][reference], baseline_reference
                        )
                    )
                    # Absolute tolerance must not accept a zeroed physical point.
                    zero_points = sum(
                        not any(values[index : index + 2])
                        for index in range(0, output_size, 2)
                    )
                    mismatches += max(pair_mismatches, zero_points)
        return results, mismatches, output_size

    def summarize(self, samples: list[tuple[float, float]], metric: str) -> dict:
        relative_limit, absolute_limit = self.THRESHOLDS[metric]
        deltas = [self.number(after - before) for before, after in samples]
        relative = [self.number(after / before - 1) for before, after in samples]
        delta = statistics.median(deltas)
        effect = statistics.median(relative)
        noise = self.number(
            self.MAD_SCALE
            * statistics.median(abs(value - effect) for value in relative)
        )
        absolute_noise = self.number(
            self.MAD_SCALE * statistics.median(abs(value - delta) for value in deltas)
        )
        margin = self.number(self.NOISE_MULTIPLIER * noise)
        absolute_margin = self.number(self.NOISE_MULTIPLIER * absolute_noise)
        slower = sum(after > before for before, after in samples)
        exceeds_limits = effect > relative_limit and delta > absolute_limit
        qualified = exceeds_limits and slower >= 4 and effect > margin
        # Noise must respect the absolute floor too: a large relative spread
        # on a tiny operation need not put the permitted absolute cost at risk.
        # Also flag a near-threshold uncertainty interval.
        uncertain = (
            (margin >= relative_limit and delta + absolute_margin > absolute_limit)
            or (exceeds_limits and not qualified)
            or (
                not qualified
                and effect + margin > relative_limit
                and delta + absolute_margin > absolute_limit
            )
        )
        return {
            "status": 1 if qualified else 2 if uncertain else 0,
            "baseline_median": statistics.median(before for before, _ in samples),
            "candidate_median": statistics.median(after for _, after in samples),
            "median_absolute_delta": delta,
            "median_relative_delta": effect,
            "paired_noise": noise,
            "paired_absolute_noise": absolute_noise,
            "slower_pairs": slower,
            "pair_count": len(samples),
            "relative_threshold": relative_limit,
            "absolute_threshold": absolute_limit,
        }

    def analyze(self, data: object) -> dict:
        report = {
            "schema_version": 2,
            "status": 2,
            "confirmation_required": 0,
            "invalid_input": 0,
            "identity_mismatch": 0,
            "correctness_mismatches": 0,
            "metrics": {},
        }
        try:
            data = self.fields(
                data,
                {
                    "schema_version",
                    "baseline",
                    "candidate",
                    "required_metrics",
                    "output_groups",
                    "reference_group",
                    "correctness_tolerance",
                    "discovery",
                },
                {"confirmation"},
            )
            if type(data["schema_version"]) is not int or data["schema_version"] != 2:
                raise InvalidMeasurements("Unsupported schema version")
            self.validate_identity(data, report)
            methods = data["output_groups"]
            reference = data["reference_group"]
            if (
                not isinstance(methods, list)
                or not methods
                or any(not isinstance(method, str) for method in methods)
                or len(set(methods)) != len(methods)
                or not set(methods) <= set(self.groups)
                or reference not in methods
            ):
                raise InvalidMeasurements("Invalid output groups or reference")
            metrics = data["required_metrics"]
            if (
                not isinstance(metrics, list)
                or not metrics
                or any(not isinstance(metric, str) for metric in metrics)
                or len(set(metrics)) != len(metrics)
                or not set(metrics) <= self.THRESHOLDS.keys()
                or any(
                    "." in metric and metric.split(".")[1] not in methods
                    for metric in metrics
                )
            ):
                raise InvalidMeasurements("Invalid required metrics")
            for family in self.metric_families:
                selected = {
                    metric for metric in metrics if metric.startswith(family + ".")
                }
                if selected and selected != {
                    f"{family}.{method}" for method in methods
                }:
                    raise InvalidMeasurements(
                        "Every requested method needs its scoped metric"
                    )
            tolerance = self.fields(
                data["correctness_tolerance"], {"absolute", "relative"}
            )
            tolerance = {key: self.number(value) for key, value in tolerance.items()}
            if any(value < 0 for value in tolerance.values()):
                raise InvalidMeasurements("Correctness tolerances cannot be negative")
            discovery, mismatch, output_size = self.validate_phase(
                data["discovery"], metrics, methods, reference, tolerance
            )
            report["correctness_mismatches"] = mismatch
            confirmation = None
            if "confirmation" in data:
                confirmation, mismatch, confirmation_size = self.validate_phase(
                    data["confirmation"], metrics, methods, reference, tolerance
                )
                if (
                    data["confirmation"]["run_id"] == data["discovery"]["run_id"]
                    or data["confirmation"]["pairs"][0]["order"]
                    == data["discovery"]["pairs"][0]["order"]
                    or output_size != confirmation_size
                ):
                    raise InvalidMeasurements(
                        "Confirmation must be an independent matched run"
                    )
                report["correctness_mismatches"] += mismatch
            statuses = []
            for metric in metrics:
                first = self.summarize(discovery[metric], metric)
                entry = {"discovery": first}
                if confirmation is not None:
                    second = self.summarize(confirmation[metric], metric)
                    entry["confirmation"] = second
                    status = (
                        1
                        if first["status"] == second["status"] == 1
                        else 0
                        if first["status"] == second["status"] == 0
                        else 2
                    )
                else:
                    status = 0 if first["status"] == 0 else 2
                    report["confirmation_required"] |= first["status"] == 1
                entry["status"] = status
                report["metrics"][metric] = entry
                statuses.append(status)
            report["status"] = (
                1
                if report["correctness_mismatches"] or 1 in statuses
                else 2
                if 2 in statuses
                else 0
            )
        except (InvalidMeasurements, ArithmeticError):
            report["invalid_input"] = 1
            report["status"] = 2
            report["metrics"] = {}
        return report
