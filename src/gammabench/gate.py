"""Fresh paired CI trials with private adapters and sealed numeric evidence."""

from __future__ import annotations

import copy
import json
import math
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .gate_policy import PairedGate
from .history_context import controller_source_sha256
from .metadata import capture_metadata
from .runner import _expand, _host
from .schema import IDENTIFIER, REVISION, SchemaError, validate_suite
from .snapshot import seal, sha256, staging_directory, write_json


def validate_policy(policy, suite):
    if suite.get("runner") != "paired-gate-v1":
        raise SchemaError("Gate requires a paired-gate-v1 suite")
    if policy.get("schema_version") != 1 or policy.get("pairs") != 5:
        raise SchemaError("A CI gate requires policy schema 1 and five pairs")
    budget = policy.get("budget_seconds")
    if (
        type(budget) not in (int, float)
        or not math.isfinite(budget)
        or not 0 < budget <= 240
    ):
        raise SchemaError("Gate budget must be positive and at most 240 seconds")
    thresholds = policy.get("thresholds", {})
    if not thresholds or set(thresholds) != set(suite["metrics"]):
        raise SchemaError("Every gate metric requires a threshold")
    if any(metric["direction"] != "lower" for metric in suite["metrics"].values()):
        raise SchemaError(
            "Paired regression screening requires lower-is-better metrics"
        )
    for limits in thresholds.values():
        if (
            not isinstance(limits, list)
            or len(limits) != 2
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or v < 0
                for v in limits
            )
        ):
            raise SchemaError(
                "Thresholds must be finite nonnegative relative/absolute pairs"
            )
    groups = policy.get("output_groups")
    if (
        not isinstance(groups, list)
        or not groups
        or any(not isinstance(g, str) or not IDENTIFIER.fullmatch(g) for g in groups)
        or len(groups) != len(set(groups))
        or policy.get("reference_group") not in groups
    ):
        raise SchemaError("Gate requires distinct output groups and a reference group")
    tolerance = policy.get("correctness_tolerance", {})
    if set(tolerance) != {"absolute", "relative"} or any(
        type(v) not in (int, float) or not math.isfinite(v) or v < 0
        for v in tolerance.values()
    ):
        raise SchemaError("Gate requires finite nonnegative numerical tolerances")
    tasks = suite["tasks"]
    if len(tasks) != 2 or {t["variant"] for t in tasks} != {"baseline", "candidate"}:
        raise SchemaError("A gate suite requires exactly baseline and candidate tasks")
    if any(
        tasks[0][key] != tasks[1][key]
        for key in ("case", "contract", "engine", "inputs")
    ):
        raise SchemaError(
            "Gate tasks must share their case, contract, engine and inputs"
        )
    for side in ("baseline", "candidate"):
        if not REVISION.fullmatch(
            str(suite.get("gate", {}).get(side, {}).get("commit", ""))
        ):
            raise SchemaError("Gate subjects require complete measured commits")
    return policy


def numeric_tree(value):
    """The adapter's public evidence cannot contain arbitrary process strings."""
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and IDENTIFIER.fullmatch(k) and numeric_tree(v)
            for k, v in value.items()
        )
    if isinstance(value, list):
        return all(numeric_tree(v) for v in value)
    return type(value) in (int, float) and math.isfinite(value)


def run_private(command, cwd, log, deadline, environment=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("gate budget exhausted")
    with Path(log).open("w") as stream:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            # communicate uses waitpid without timeout polling when a separate
            # watchdog is used; the wait here is outside every reported clock.
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise TimeoutError("gate budget exhausted") from None
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    if process.returncode:
        raise RuntimeError("adapter failed")


def comparator(policy, identity):
    return PairedGate(
        identity["baseline"]["commit"],
        identity["candidate"]["commit"],
        policy["thresholds"],
        policy["output_groups"],
        policy.get("metric_families", []),
    )


def measurement(policy, identity):
    return {
        "schema_version": 2,
        "required_metrics": list(policy["thresholds"]),
        **{
            k: policy[k]
            for k in ("output_groups", "reference_group", "correctness_tolerance")
        },
        **{side: identity[side] for side in ("baseline", "candidate")},
    }


def phase_pairs(snapshot):
    """Reconstruct qualification from the actual retained records, not a pass flag."""
    records = snapshot["records"]
    pairs = []
    for repeat in range(5):
        rows = [r for r in records if r["phase"] == "measure" and r["repeat"] == repeat]
        if len(rows) != 2 or any(r["status"] != "completed" for r in rows):
            raise ValueError("incomplete numerical evidence")
        pair = {
            "order": "".join("A" if r["variant"] == "baseline" else "B" for r in rows)
        }
        for row in rows:
            pair[row["variant"]] = row["metrics"]
            pair[row["variant"] + "_output"] = row["result"]["outputs"]
        pairs.append(pair)
    return {
        "run_id": 1 if snapshot["provenance"]["ci_gate"]["phase"] == "discovery" else 2,
        "pairs": pairs,
    }


def qualified(snapshot):
    try:
        info = snapshot["provenance"]["ci_gate"]
        policy = info["policy"]
        comparator(policy, info["identity"]).validate_identity(info["identity"], {})
        _, mismatches, _ = comparator(policy, info["identity"]).validate_phase(
            phase_pairs(snapshot),
            list(policy["thresholds"]),
            policy["output_groups"],
            policy["reference_group"],
            policy["correctness_tolerance"],
        )
        return mismatches == 0
    except (ValueError, KeyError, TypeError, ArithmeticError):
        return False


def run(suite_path, destination, *, policy_path, private_work_dir, label=None):
    suite_path, destination = Path(suite_path).resolve(), Path(destination).resolve()
    private = Path(private_work_dir).resolve()
    if private.is_relative_to(destination) or destination.is_relative_to(private):
        raise SchemaError("Private adapter work must be outside published evidence")
    if destination.exists() or private.exists():
        raise FileExistsError("Gate evidence and private directories must be new")
    suite = validate_suite(json.loads(suite_path.read_text()))
    policy = validate_policy(json.loads(Path(policy_path).read_text()), suite)
    identity = copy.deepcopy(suite["gate"])
    for side in ("baseline", "candidate"):
        comparator(policy, identity).fingerprint(identity[side]["commit"], 40)
        for key in ("fixture_sha256", "corpus_sha256", "config_sha256"):
            comparator(policy, identity).fingerprint(identity[side][key], 64)
    tasks = {t["variant"]: t for t in suite["tasks"]}
    bindings = {}
    for name, tool in suite["tools"].items():
        if tool["kind"] != "executable":
            raise SchemaError("Gate executables must be realized before measurement")
        path = Path(tool["path"]).resolve()
        if (
            not path.is_file()
            or not os.access(path, os.X_OK)
            or tool.get("sha256") != sha256(path)
        ):
            raise SchemaError("Gate executable pin does not match")
        bindings[name] = {**tool, "tool_path": str(path)}
    inputs = {}
    for name, path in tasks["baseline"]["inputs"].items():
        source = (suite_path.parent / path).resolve()
        if not source.is_relative_to(suite_path.parent) or not source.is_file():
            raise SchemaError("Gate input escapes its prepared suite")
        inputs[name] = (source, sha256(source))
    adapter = suite.get("gate_adapter")
    if not isinstance(adapter, str) or not (suite_path.parent / adapter).is_file():
        raise SchemaError("Gate suite must retain its adapter source")
    adapter_path = (suite_path.parent / adapter).resolve()
    if not adapter_path.is_relative_to(suite_path.parent):
        raise SchemaError("Adapter escapes its prepared suite")
    guarded = {
        suite_path: sha256(suite_path),
        Path(policy_path).resolve(): sha256(Path(policy_path)),
        adapter_path: sha256(adapter_path),
        **{path: digest for path, digest in inputs.values()},
        **{path: sha256(path) for path in Path(__file__).parent.glob("*.py")},
    }
    capture = label or "gate-" + str(uuid.uuid4())
    if not IDENTIFIER.fullmatch(capture):
        raise SchemaError("Invalid capture label")
    destination.mkdir(parents=True)
    private.mkdir(parents=True)
    data = measurement(policy, identity)
    snapshots = []
    gate = comparator(policy, identity)
    report = gate.analyze(None)
    deadline = time.monotonic() + policy["budget_seconds"]
    for phase, first in (("discovery", "AB"), ("confirmation", "BA")):
        if phase == "confirmation" and not (
            report["status"] == 2 and report["confirmation_required"]
        ):
            break
        target = destination / phase
        staging = staging_directory(target)
        host = _host(None)
        host.update(
            cpu=platform.processor(),
            runner_image=os.environ.get("ImageVersion", "unknown"),
        )
        if Path("/proc/cpuinfo").is_file():
            host["cpu_model"] = next(
                (
                    line.split(":", 1)[1].strip()
                    for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")
                ),
                "unknown",
            )
        snapshot = {
            "schema_version": 1,
            "id": capture + "-" + phase,
            "suite_id": suite["id"],
            "origin": "measured",
            "status": "partial",
            "created_at": datetime.now(UTC).isoformat(),
            "contracts": suite["contracts"],
            "metrics": suite["metrics"],
            "tools": bindings,
            "host": host,
            "schedule": {
                "repeats": 5,
                "warmups": 0,
                "pairing_id": capture + "-" + phase,
                "selected_tasks": [t["id"] for t in suite["tasks"]],
            },
            "records": [],
            "validation": {"tasks": {}, "equivalence": []},
            "provenance": {
                "controller_source_sha256": controller_source_sha256(),
                "ci_gate": {"policy": policy, "identity": identity, "phase": phase},
                "ci": suite.get("ci", {}),
                "build": suite.get("build", {}),
                "workloads": {
                    tasks["baseline"]["case"]: {
                        "sha256": identity["baseline"]["fixture_sha256"]
                    }
                },
                "adapter_sha256": guarded[adapter_path],
            },
        }
        metadata = capture_metadata(suite)
        if metadata is not None:
            snapshot["metadata"] = metadata
        for name, (source, digest) in inputs.items():
            (staging / "inputs").mkdir(exist_ok=True)
            (staging / "inputs" / name).write_bytes(source.read_bytes())
        (staging / "adapter.py").write_bytes(adapter_path.read_bytes())
        for repeat in range(5):
            order = first if repeat % 2 == 0 else first[::-1]
            for side in order:
                task = tasks["baseline" if side == "A" else "candidate"]
                work = private / phase / str(repeat) / task["variant"]
                work.mkdir(parents=True)
                context = {
                    "suite_dir": str(suite_path.parent),
                    "workdir": str(work),
                    "python": sys.executable,
                    "result": str(work / "result.json"),
                    "deadline": str(deadline),
                    **{"tool:" + k: v["tool_path"] for k, v in bindings.items()},
                }
                command = [_expand(arg, context) for arg in task["argv"]]
                record = {
                    key: task[key]
                    for key in ("id", "case", "engine", "variant", "contract")
                }
                record.update(
                    task=record.pop("id"),
                    phase="measure",
                    repeat=repeat,
                    status="failed",
                    returncode=None,
                    duration_ns=0,
                    command=command,
                    metrics={},
                    raw={},
                    started_at=datetime.now(UTC).isoformat(),
                    inputs_sha256={k: v[1] for k, v in inputs.items()},
                    environment=task.get("environment", {}),
                )
                started = time.monotonic_ns()
                try:
                    run_private(
                        command,
                        suite_path.parent,
                        work / "adapter.log",
                        deadline,
                        os.environ | task.get("environment", {}),
                    )
                    value = json.loads((work / "result.json").read_text())
                    if set(value) != {
                        "metrics",
                        "outputs",
                        "metadata",
                    } or not numeric_tree(value):
                        raise ValueError("invalid numeric adapter output")
                    if set(value["metrics"]) != set(policy["thresholds"]):
                        raise ValueError("missing gate metric")
                    if any(v <= 0 for v in value["metrics"].values()):
                        raise ValueError("nonpositive gate metric")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("gate budget exhausted")
                    for name, process_metric in task.get(
                        "process_measurements", {}
                    ).items():
                        from .hyperfine import read_measurement

                        export = (work / process_metric["path"]).resolve()
                        if not export.is_relative_to(
                            work.resolve()
                        ) or not IDENTIFIER.fullmatch(name):
                            raise ValueError("Unsafe process measurement path")
                        native_command = [
                            _expand(arg, context) for arg in process_metric["argv"]
                        ]
                        native = read_measurement(export, native_command)
                        metric = process_metric["metric"]
                        if suite["metrics"][metric]["unit"] not in ("ns", "s"):
                            raise ValueError("Unsupported process measurement unit")
                        expected = native["process_wall_ns"] / (
                            1e9 if suite["metrics"][metric]["unit"] == "s" else 1
                        )
                        if native["returncode"] or value["metrics"][metric] != expected:
                            raise ValueError(
                                "Adapter timing differs from its Hyperfine evidence"
                            )
                        relative = f"records/{repeat}-{task['variant']}-{name}.json"
                        (staging / "records").mkdir(exist_ok=True)
                        shutil.copyfile(export, staging / relative)
                        record["raw"][name] = relative
                    result = copy.deepcopy(value)
                    result["metrics"] = {}
                    for name, metric_value in value["metrics"].items():
                        fields = name.split(".")
                        parent = result["metrics"]
                        for field in fields[:-1]:
                            parent = parent.setdefault(field, {})
                        parent[fields[-1]] = metric_value
                    record.update(
                        status="completed",
                        returncode=0,
                        metrics=value["metrics"],
                        result=result,
                    )
                except TimeoutError:
                    record.update(
                        status="timeout", reason="shared case budget exhausted"
                    )
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    RuntimeError,
                    ArithmeticError,
                ):
                    record.update(
                        status="failed",
                        reason="adapter failed or returned invalid numeric evidence",
                    )
                record["duration_ns"] = time.monotonic_ns() - started
                record["raw"]["record"] = f"records/{repeat}-{task['variant']}.json"
                snapshot["records"].append(record)
                write_json(staging / record["raw"]["record"], record)
            if any(r["status"] != "completed" for r in snapshot["records"]):
                break
        try:
            stable = all(
                path.is_file() and sha256(path) == digest
                for path, digest in guarded.items()
            )
            stable &= all(
                sha256(Path(tool["tool_path"])) == tool["sha256"]
                for tool in bindings.values()
            )
        except OSError:
            stable = False
        if not stable:
            for record in snapshot["records"]:
                record.update(
                    status="invalid",
                    metrics={},
                    reason="pinned source changed during measurement",
                )
                write_json(staging / record["raw"]["record"], record)
        passed = qualified(snapshot)
        snapshot["validation"]["tasks"] = {
            t["id"]: {
                "status": "passed" if passed else "failed",
                "method": "numeric-tolerance-v1",
                "reason": "" if passed else "incomplete or mismatched numeric evidence",
            }
            for t in suite["tasks"]
        }
        snapshot["validation"]["status"] = "passed" if passed else "failed"
        snapshot["status"] = "completed" if passed else "failed"
        try:
            data[phase] = phase_pairs(snapshot)
            report = gate.analyze(data)
        except ValueError:
            report = gate.analyze(None)
        snapshot["provenance"]["ci_gate"]["report"] = report
        seal(staging, target, snapshot, suite)
        snapshots.append(phase)
    write_json(
        destination / "gate.json",
        {
            "schema_version": 1,
            "id": capture,
            "snapshots": snapshots,
            "report": report,
            "ci": suite.get("ci", {}),
        },
    )
    return report["status"]


def history(snapshot, task, metric, definition, workload):
    info = snapshot["provenance"]["ci_gate"]
    side = task["variant"]
    host = {
        k: v
        for k, v in snapshot["host"].items()
        if k not in ("hostname", "load_average_start", "load_average_end")
    }
    return {
        "schema_version": 1,
        "kind": "ci-gate",
        "capture": "timing",
        "revision": info["identity"][side]["commit"],
        "repository": snapshot["provenance"].get("ci", {}).get("repository_url", ""),
        "reason": ""
        if qualified(snapshot)
        else "Numerical qualification failed or is incomplete.",
        "context": {
            "host": host,
            "workload": workload,
            "policy": info["policy"],
            "phase": info["phase"],
            "controller": snapshot["provenance"]["controller_source_sha256"],
            "adapter": snapshot["provenance"]["adapter_sha256"],
            "fixed_tools": {
                name: value
                for name, value in snapshot["tools"].items()
                if name not in ("baseline", "candidate")
            },
            "metric": {"id": metric, **definition},
            "build": snapshot["provenance"]
            .get("build", {})
            .get(side, {})
            .get("context", {}),
            "environment": task.get("environment", {}),
            "contract": snapshot["contracts"][task["contract"]],
        },
        "evidence": {
            "ci": snapshot["provenance"].get("ci", {}),
            "build": snapshot["provenance"].get("build", {}).get(side, {}),
            "tools": snapshot["tools"],
            "qualification": "numeric-tolerance-v1",
        },
    }
