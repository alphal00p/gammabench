import copy
import unittest

from gammabench.schema import SchemaError, dotted, validate_snapshot, validate_suite


def example_suite():
    return {"schema_version": 1, "id": "demo", "description": "A deterministic benchmark",
            "contracts": {"compute": {"description": "Compute and retain a sum", "included": ["sum"], "excluded": ["startup"]}},
            "metrics": {"compute_ns": {"unit": "ns", "direction": "lower", "scope": "sum only", "result_field": "compute_ns"}},
            "tools": {}, "tasks": [{"id": "a", "case": "sum", "engine": "python", "variant": "a", "contract": "compute",
                "argv": ["{python}", "-c", "print('RESULT {\"sum\":6,\"compute_ns\":10}')"],
                "tool_ids": [], "inputs": {}, "expected": {"sum": 6}, "equivalence_fields": ["sum"]}]}


def example_snapshot():
    suite = validate_suite(example_suite())
    return {"schema_version": 1, "id": "test-run", "suite_id": suite["id"], "origin": "measured",
            "status": "completed", "created_at": "2026-10-14T00:00:00Z", "contracts": suite["contracts"],
            "metrics": suite["metrics"], "tools": {}, "host": {}, "schedule": {}, "records": [],
            "validation": {"status": "passed", "tasks": {}}, "provenance": {}}


class SchemaTests(unittest.TestCase):
    def test_builtin_clock_is_declared_without_mutating_input(self):
        suite = example_suite()
        validated = validate_suite(suite)
        self.assertIn("process_wall_ns", validated["metrics"])
        self.assertNotIn("process_wall_ns", suite["metrics"])

    def test_cannot_relabel_process_clock_as_completion(self):
        suite = example_suite()
        suite["metrics"]["process_wall_ns"] = {"unit": "ns", "direction": "lower", "scope": "construction"}
        with self.assertRaisesRegex(SchemaError, "fixed"):
            validate_suite(suite)

    def test_full_git_revision_is_required(self):
        suite = example_suite()
        suite["tools"]["gammaloop"] = {"kind": "git", "url": "https://example.org/source", "revision": "abcdef"}
        with self.assertRaisesRegex(SchemaError, "full 40"):
            validate_suite(suite)

    def test_contract_includes_and_excludes_cannot_overlap(self):
        suite = example_suite()
        suite["contracts"]["compute"]["excluded"] = ["sum"]
        with self.assertRaisesRegex(SchemaError, "same work"):
            validate_suite(suite)

    def test_duplicate_task_and_unknown_tools_rejected(self):
        suite = example_suite()
        suite["tasks"].append(copy.deepcopy(suite["tasks"][0]))
        with self.assertRaisesRegex(SchemaError, "duplicate task"):
            validate_suite(suite)
        suite = example_suite()
        suite["tasks"][0]["tool_ids"] = ["missing"]
        with self.assertRaisesRegex(SchemaError, "unknown tool"):
            validate_suite(suite)

    def test_inputs_cannot_escape_suite(self):
        suite = example_suite()
        suite["tasks"][0]["inputs"] = {"x": "../private"}
        with self.assertRaisesRegex(SchemaError, "suite-relative"):
            validate_suite(suite)

    def test_workload_selector_must_name_declared_inputs(self):
        suite = example_suite()
        suite["tasks"][0]["inputs"] = {"case": "case.json"}
        suite["tasks"][0]["workload_inputs"] = ["unknown"]
        with self.assertRaisesRegex(SchemaError, "known declared inputs"):
            validate_suite(suite)

    def test_workload_selector_cannot_be_empty_or_duplicate(self):
        for selector in ([], ["case", "case"]):
            suite = example_suite()
            suite["tasks"][0]["inputs"] = {"case": "case.json"}
            suite["tasks"][0]["workload_inputs"] = selector
            with self.assertRaises(SchemaError):
                validate_suite(suite)

    def test_missing_result_field_is_never_zero(self):
        with self.assertRaisesRegex(SchemaError, "missing RESULT"):
            dotted({"phase": {}}, "phase.ns")

    def test_noncompleted_sample_cannot_have_metrics(self):
        snapshot = example_snapshot()
        snapshot["records"] = [{"task": "a", "case": "sum", "engine": "python", "variant": "a",
            "contract": "compute", "phase": "measure", "repeat": 0, "status": "timeout", "returncode": -9,
            "duration_ns": 100, "metrics": {"compute_ns": 10}, "command": ["python"], "raw": {}}]
        with self.assertRaisesRegex(SchemaError, "empty metrics"):
            validate_snapshot(snapshot)

    def test_nonfinite_and_boolean_metrics_rejected(self):
        for value in (float("nan"), float("inf"), True):
            snapshot = example_snapshot()
            snapshot["records"] = [{"task": "a", "case": "sum", "engine": "python", "variant": "a",
                "contract": "compute", "phase": "measure", "repeat": 0, "status": "completed", "returncode": 0,
                "duration_ns": 100, "metrics": {"compute_ns": value}, "command": ["python"], "raw": {}}]
            with self.assertRaisesRegex(SchemaError, "finite"):
                validate_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
