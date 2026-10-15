"""Versioned, explicit contracts for suites and result snapshots."""
from __future__ import annotations

import copy
import math
import re
from typing import Any

VERSION = 1
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
PROCESS_WALL = {"unit": "ns", "direction": "lower", "scope": "complete process lifetime"}


class SchemaError(ValueError):
    """A suite or snapshot violates its declared contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SchemaError(message)


def _text(value: Any, where: str) -> None:
    _require(isinstance(value, str) and bool(value.strip()), f"{where} must be nonempty text")


def _strings(value: Any, where: str, *, nonempty: bool = False) -> None:
    _require(isinstance(value, list), f"{where} must be an array")
    if nonempty:
        _require(bool(value), f"{where} must not be empty")
    for item in value:
        _text(item, where)


def _identifier(value: Any, where: str) -> None:
    _require(isinstance(value, str) and bool(IDENTIFIER.fullmatch(value)), f"invalid {where}: {value!r}")


def _mapping(value: Any, where: str) -> None:
    _require(isinstance(value, dict), f"{where} must be an object")


def _number(value: Any, where: str) -> None:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool)
             and math.isfinite(value), f"{where} must be a finite number")


def validate_suite(value: Any) -> dict:
    """Validate and return a copy with the built-in process metric declared."""
    _mapping(value, "suite")
    _require(type(value.get("schema_version")) is int and value["schema_version"] == VERSION, "unsupported suite schema_version")
    _identifier(value.get("id"), "suite id")
    _text(value.get("description"), "suite description")
    suite = copy.deepcopy(value)
    if "nix_build_receipt" in suite:
        name = suite["nix_build_receipt"]
        _text(name, "nix_build_receipt")
        _require(not name.startswith("/") and ".." not in name.split("/"),
                 "nix_build_receipt must be a suite-relative path without traversal")
    for section in ("contracts", "metrics", "tools"):
        _mapping(suite.get(section), section)
    _require(bool(suite["contracts"]), "at least one contract is required")
    for key, contract in suite["contracts"].items():
        _identifier(key, "contract id")
        _mapping(contract, f"contract {key}")
        _text(contract.get("description"), f"contract {key} description")
        for field in ("included", "excluded"):
            _strings(contract.get(field), f"contract {key} {field}")
        _require(not set(contract["included"]) & set(contract["excluded"]),
                 f"contract {key} includes and excludes the same work")
    if "process_wall_ns" in suite["metrics"]:
        _require(suite["metrics"]["process_wall_ns"] == PROCESS_WALL,
                 "process_wall_ns has a fixed unit, direction and scope")
    suite["metrics"]["process_wall_ns"] = copy.deepcopy(PROCESS_WALL)
    for key, metric in suite["metrics"].items():
        _identifier(key, "metric id")
        _mapping(metric, f"metric {key}")
        _text(metric.get("unit"), f"metric {key} unit")
        _text(metric.get("scope"), f"metric {key} scope")
        _require(metric.get("direction") in ("lower", "higher"), f"metric {key} direction")
        if key != "process_wall_ns":
            _text(metric.get("result_field"), f"metric {key} result_field")
    for key, tool in suite["tools"].items():
        _identifier(key, "tool id")
        _mapping(tool, f"tool {key}")
        kind = tool.get("kind")
        _require(kind in ("git", "executable", "archive"), f"tool {key} kind")
        if kind == "git":
            _text(tool.get("url"), f"tool {key} url")
            _require(isinstance(tool.get("revision"), str) and bool(REVISION.fullmatch(tool["revision"])),
                     f"tool {key} requires a full 40-character Git revision")
        elif kind == "archive":
            _text(tool.get("url"), f"tool {key} url")
            _text(tool.get("version"), f"tool {key} version")
            _require(bool(SHA256.fullmatch(str(tool.get("sha256", "")))), f"tool {key} sha256")
        else:
            _text(tool.get("path"), f"tool {key} path")
            _text(tool.get("version"), f"tool {key} version")
            if "sha256" in tool:
                _require(bool(SHA256.fullmatch(str(tool["sha256"]))), f"tool {key} sha256")
    _require(isinstance(suite.get("tasks"), list) and bool(suite["tasks"]), "tasks must be a nonempty array")
    ids = set()
    for task in suite["tasks"]:
        _mapping(task, "task")
        for field in ("id", "case", "engine", "variant", "contract"):
            _identifier(task.get(field), f"task {field}")
        _require(task["id"] not in ids, f"duplicate task id {task['id']}")
        ids.add(task["id"])
        _require(task["contract"] in suite["contracts"], f"unknown contract in {task['id']}")
        _strings(task.get("argv"), f"task {task['id']} argv", nonempty=True)
        _strings(task.get("tool_ids"), f"task {task['id']} tool_ids")
        _require(len(task["tool_ids"]) == len(set(task["tool_ids"])), "duplicate task tool IDs")
        _require(set(task["tool_ids"]) <= suite["tools"].keys(), f"unknown tool in {task['id']}")
        _mapping(task.get("inputs"), f"task {task['id']} inputs")
        for name, path in task["inputs"].items():
            _identifier(name, "input name")
            _text(path, "input path")
            _require(not path.startswith("/") and ".." not in path.split("/"),
                     "inputs must be suite-relative paths without parent traversal")
        if "workload_inputs" in task:
            _strings(task["workload_inputs"], "workload_inputs", nonempty=True)
            _require(len(task["workload_inputs"]) == len(set(task["workload_inputs"])),
                     "workload_inputs must be distinct")
            _require(set(task["workload_inputs"]) <= task["inputs"].keys(),
                     "workload_inputs must name known declared inputs")
        _mapping(task.get("environment", {}), "task environment")
        for key, val in task.get("environment", {}).items():
            _text(key, "environment key")
            _require("=" not in key and "\0" not in key, "invalid environment key")
            _require(isinstance(val, str) and "\0" not in val, "environment values must be strings")
        _mapping(task.get("expected", {}), "task expected")
        _strings(task.get("equivalence_fields", []), "task equivalence_fields")
        if "validation_equivalence_fields" in task:
            _strings(task["validation_equivalence_fields"], "validation_equivalence_fields", nonempty=True)
            _require("validation_command" in task, "validation_equivalence_fields requires validation_command")
        if "validation_command" in task:
            _strings(task["validation_command"], "validation_command", nonempty=True)
        if "timeout_seconds" in task:
            _number(task["timeout_seconds"], "timeout_seconds")
            _require(task["timeout_seconds"] > 0, "timeout_seconds must be positive")
    from .metadata import capture_metadata
    capture_metadata(suite)
    return suite


def dotted(value: Any, field: str) -> Any:
    """Read a declared field; missing fields are errors, never zero measurements."""
    current = value
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            raise SchemaError(f"missing RESULT field {field}")
        current = current[part]
    return current


def validate_snapshot(snapshot: Any) -> dict:
    _mapping(snapshot, "snapshot")
    _require(type(snapshot.get("schema_version")) is int and snapshot["schema_version"] == VERSION, "unsupported snapshot schema_version")
    for field in ("id", "suite_id"):
        _identifier(snapshot.get(field), field)
    _require(snapshot.get("origin") in ("measured", "historical-import"), "invalid snapshot origin")
    _require(snapshot.get("status") in ("completed", "failed", "partial", "imported"), "invalid snapshot status")
    _text(snapshot.get("created_at"), "created_at")
    for field in ("contracts", "metrics", "tools", "host", "schedule", "validation", "provenance"):
        _mapping(snapshot.get(field), field)
    _require(isinstance(snapshot.get("records"), list), "records must be an array")
    for record in snapshot["records"]:
        _mapping(record, "record")
        for field in ("task", "case", "engine", "variant", "contract"):
            _identifier(record.get(field), f"record {field}")
        _require(record["contract"] in snapshot["contracts"], "record contract undeclared")
        _require(record.get("phase") in ("validation", "warmup", "measure"), "invalid record phase")
        _require(isinstance(record.get("repeat"), int) and not isinstance(record["repeat"], bool)
                 and record["repeat"] >= 0, "record repeat must be nonnegative")
        _require(record.get("status") in ("completed", "timeout", "failed", "invalid"), "invalid record status")
        _require(record.get("returncode") is None or (isinstance(record["returncode"], int)
                 and not isinstance(record["returncode"], bool)), "invalid returncode")
        _number(record.get("duration_ns"), "record duration_ns")
        _require(record["duration_ns"] >= 0, "negative duration")
        _strings(record.get("command"), "record command", nonempty=True)
        _mapping(record.get("metrics"), "record metrics")
        _mapping(record.get("raw"), "record raw")
        if record["status"] != "completed":
            _require(not record["metrics"], "noncompleted records must have empty metrics")
        for key, value in record["metrics"].items():
            _require(key in snapshot["metrics"], f"undeclared record metric {key}")
            _number(value, f"metric {key}")
        for path in record["raw"].values():
            _text(path, "raw path")
            _require(not path.startswith("/") and ".." not in path.split("/"), "raw paths must be snapshot-relative")
    if "metadata" in snapshot:
        from .metadata import validate_metadata
        validate_metadata(snapshot["metadata"], cases={record["case"] for record in snapshot["records"]} |
                          set(snapshot["metadata"].get("cases", {})),
                          tasks={record["task"] for record in snapshot["records"]} |
                          set(snapshot.get("schedule", {}).get("selected_tasks", [])), metrics=snapshot["metrics"])
    return snapshot
