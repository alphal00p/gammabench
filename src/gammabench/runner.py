"""Fresh-process, balanced benchmark execution with explicit output contracts."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import errno
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

from . import __version__, hyperfine as hyperfine_backend
from .nix import capture_receipt, load_receipt, verify_receipt
from .metadata import capture_metadata
from .history_context import controller_source_sha256
from .schema import SchemaError, dotted, validate_suite
from .snapshot import seal, sha256, staging_directory, write_json

PLACEHOLDER = re.compile(r"\{((?:source|tool|input):[A-Za-z0-9_.-]+|[A-Za-z_][A-Za-z0-9_]*)\}")


def balanced_schedule(task_ids: list[str], repeats: int, warmups: int = 1) -> list[dict]:
    """Alternate ascending/descending rotations; each sample round has every task."""
    if not task_ids or len(task_ids) != len(set(task_ids)):
        raise ValueError("schedule needs distinct task IDs")
    if repeats < 1 or warmups < 0:
        raise ValueError("repeats must be positive and warmups nonnegative")
    schedule = []
    for phase, count in (("warmup", warmups), ("measure", repeats)):
        for repeat in range(count):
            rotation = (repeat // 2) % len(task_ids)
            order = task_ids[rotation:] + task_ids[:rotation]
            if repeat % 2:
                order = list(reversed(order))
            schedule.extend({"task": task, "phase": phase, "repeat": repeat} for task in order)
    return schedule


def _expand(value: str, context: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in context:
            raise SchemaError(f"undeclared command placeholder {{{key}}}")
        return context[key]
    return PLACEHOLDER.sub(replace, value)


def _result(path: Path) -> dict | None:
    result = None
    seen = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("RESULT "):
                seen += 1
                result = json.loads(line[7:])
                if not isinstance(result, dict):
                    raise SchemaError("RESULT must be a JSON object")
    if seen > 1:
        raise SchemaError("exactly one RESULT line is allowed")
    return result


def _check_result(task: dict, result: dict | None, phase: str) -> dict:
    expected = task.get("expected", {})
    fields = task.get("equivalence_fields", [])
    if phase == "validation":
        fields = task.get("validation_equivalence_fields", fields)
    if (expected or fields) and result is None:
        raise SchemaError("RESULT is missing for semantic validation")
    for field, value in expected.items():
        if dotted(result, field) != value:
            raise SchemaError(f"expected RESULT field {field} did not match")
    return {field: dotted(result, field) for field in fields}


def _execute(task: dict, phase: str, repeat: int, staging: Path, context: dict,
             metrics: dict, environment: dict, timeout: float, cpu: int | None,
             inputs_sha256: dict, harness_sources: dict, measurement_tool: dict | None = None) -> dict:
    relative = Path("tasks") / task["id"] / f"{phase}-{repeat:04d}"
    directory = staging / relative
    workdir = directory / "work"
    workdir.mkdir(parents=True)
    names = set()
    for name, filename in task["inputs"].items():
        basename = Path(filename).name
        if basename in names:
            raise SchemaError("task input filenames must be distinct when materialized in the work directory")
        names.add(basename)
        shutil.copyfile(staging / "tasks" / task["id"] / "inputs" / name, workdir / basename)
    context = {**context, "workdir": str(workdir)}
    argv = task.get("validation_command", task["argv"]) if phase == "validation" else task["argv"]
    command = [_expand(arg, context) for arg in argv]
    explicit_environment = {key: _expand(value, context) for key, value in
                            {**task.get("environment", {}), **environment}.items()}
    record = {key: task[key] for key in ("case", "engine", "variant", "contract")}
    record.update(task=task["id"], phase=phase, repeat=repeat, status="failed", returncode=None,
                  duration_ns=0, metrics={}, command=command, pid=None, cpu=cpu,
                  environment=explicit_environment, stdin="null", timeout_seconds=timeout,
                  started_at=datetime.now(timezone.utc).isoformat(),
                  raw={name: (relative / file).as_posix() for name, file in
                       (("stdout", "stdout.txt"), ("stderr", "stderr.txt"), ("record", "record.json"))})
    child_environment = os.environ.copy()
    child_environment.update(explicit_environment)
    record["inputs_sha256"] = dict(inputs_sha256)
    record["workload_inputs_sha256"] = {name: inputs_sha256[name] for name in
                                        task.get("workload_inputs", list(inputs_sha256))}
    record["command_files_sha256"] = _bind_command_files(command, workdir, child_environment, staging, harness_sources)
    measurement_tool = measurement_tool if phase != "validation" else None
    launch = command
    record["measurement_backend"] = "hyperfine" if measurement_tool else "python"
    if measurement_tool:
        export = directory / "hyperfine.json"
        launch = hyperfine_backend.invocation(measurement_tool, command, export)
        record.update(controller_pid=None, controller_returncode=None, controller_command=launch,
                      duration_scope="Hyperfine controller lifetime; target wall time is in metrics.process_wall_ns",
                      stderr_scope="benchmark and Hyperfine diagnostics")
    started = time.perf_counter_ns()
    completed_at = None
    with (directory / "stdout.txt").open("wb") as stdout, (directory / "stderr.txt").open("wb") as stderr:
        try:
            process = subprocess.Popen(launch, cwd=workdir, env=child_environment,
                                       stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True,
                                       preexec_fn=(lambda: os.sched_setaffinity(0, {cpu})) if cpu is not None else None)
            record["controller_pid" if measurement_tool else "pid"] = process.pid
            timed_out = threading.Event()

            def deadline() -> None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    return
                timed_out.set()

            timer = threading.Timer(timeout, deadline)
            timer.start()
            try:
                record["controller_returncode" if measurement_tool else "returncode"] = process.wait()
                completed_at = time.perf_counter_ns()
            finally:
                timer.cancel()
                timer.join()
            record["status"] = "timeout" if timed_out.is_set() else "completed" if process.returncode == 0 else "failed"
        except OSError as error:
            record["reason"] = str(error)
        finally:
            record["duration_ns"] = (completed_at or time.perf_counter_ns()) - started
    process_wall_ns = record["duration_ns"]
    if measurement_tool:
        if export.exists():
            record["raw"]["hyperfine"] = (relative / "hyperfine.json").as_posix()
        if record["status"] == "completed":
            try:
                measurement = hyperfine_backend.read_measurement(export, command)
                record["returncode"] = measurement["returncode"]
                process_wall_ns = measurement["process_wall_ns"]
                if record["returncode"] != 0:
                    record.update(status="failed", reason="benchmark exited with a nonzero status")
            except (ValueError, TypeError) as error:
                record.update(status="invalid", reason=str(error))
        elif record["status"] == "failed" and "reason" not in record:
            record["reason"] = "Hyperfine controller did not complete successfully"
    if record["status"] == "completed":
        try:
            result = _result(directory / "stdout.txt")
            equivalence = _check_result(task, result, phase)
            measured = {"process_wall_ns": process_wall_ns}
            if phase != "validation":
                for name, metric in metrics.items():
                    if name == "process_wall_ns":
                        continue
                    value = dotted(result, metric["result_field"])
                    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                        raise SchemaError(f"metric {name} is not a finite number")
                    measured[name] = value
            record["metrics"] = measured
            if result is not None:
                record["result"] = result
            record["equivalence"] = equivalence
        except (ValueError, TypeError) as error:
            record.update(status="invalid", metrics={}, reason=str(error))
    write_json(directory / "record.json", record)
    return record


def _bind_command_files(command: list[str], workdir: Path, environment: dict,
                        staging: Path, bindings: dict) -> dict:
    """Bind command artifacts, retaining small source files rather than binaries."""
    program = Path(command[0])
    if "/" in command[0]:
        executable = program if program.is_absolute() else workdir / program
    else:
        found = shutil.which(command[0], path=environment.get("PATH"))
        executable = Path(found) if found else None
    candidates = [executable] if executable and executable.is_file() else []
    for argument in command[1:]:
        candidate = Path(argument)
        if not candidate.is_absolute():
            candidate = workdir / candidate
        try:
            if candidate.is_file():
                candidates.append(candidate)
        except OSError as error:
            # A command argument may be source code rather than a filesystem path.
            if error.errno != errno.ENAMETOOLONG:
                raise
    result = {}
    for candidate in candidates:
        path = candidate.resolve()
        digest = sha256(path)
        name = str(path)
        if name in bindings and bindings[name]["sha256"] != digest:
            raise SchemaError(f"command source changed during capture: {name}")
        if name not in bindings:
            binding = {"sha256": digest, "size_bytes": path.stat().st_size}
            if not path.is_relative_to(staging) and path.stat().st_size <= 4 * 1024 * 1024:
                with path.open("rb") as handle:
                    textual = b"\0" not in handle.read(8192)
                if textual:
                    archived = Path("harness-sources") / digest / path.name
                    target = staging / archived
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
                    binding["archived_path"] = archived.as_posix()
            bindings[name] = binding
        result[name] = digest
    return result


def _host(cpus: list[int] | None) -> dict:
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    return {"hostname": platform.node(), "platform": platform.platform(), "machine": platform.machine(),
            "python": platform.python_version(), "python_executable": sys.executable,
            "python_sha256": sha256(Path(sys.executable)), "cpu_count": os.cpu_count(),
            "available_affinity": affinity, "selected_affinity": cpus,
            "load_average_start": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
            "shared_host": True, "exclusive_cpu_reservation": False}


def run(suite_path: str | Path, destination: str | Path, *, repeats: int = 15, warmups: int = 1,
        selected_tasks: list[str] | None = None, cache_dir: str | Path | None = None,
        overrides: dict | None = None, allow_dirty: bool = False,
        environment: dict[str, str] | None = None, cpus: list[int] | None = None,
        timeout: float = 60, label: str | None = None, backend: str = "hyperfine",
        hyperfine: str | Path | None = None) -> Path:
    """Resolve pins, validate outputs, run serialized rounds and seal fresh evidence."""
    from . import sources

    if backend not in ("hyperfine", "python"):
        raise ValueError("measurement backend must be hyperfine or python")
    if backend == "python" and hyperfine is not None:
        raise ValueError("--hyperfine requires the hyperfine backend")

    suite_path = Path(suite_path).resolve()
    suite = validate_suite(json.loads(suite_path.read_text(encoding="utf-8")))
    if suite.get("archived_only"):
        raise SchemaError("historical suites cannot be executed; select a pinned runnable suite")
    requested = selected_tasks or [task["id"] for task in suite["tasks"]]
    if len(requested) != len(set(requested)) or not set(requested) <= {task["id"] for task in suite["tasks"]}:
        raise SchemaError("selected task IDs must be known and distinct")
    tasks = [task for task in suite["tasks"] if task["id"] in requested]
    order = balanced_schedule([task["id"] for task in tasks], repeats, warmups)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive")
    if cpus is not None:
        if not cpus or len(cpus) != len(set(cpus)) or not hasattr(os, "sched_setaffinity"):
            raise ValueError("CPU affinity requires a nonempty distinct supported CPU list")
        if not set(cpus) <= os.sched_getaffinity(0):
            raise ValueError("requested CPUs are outside the available affinity")
    environment = environment or {}
    if any(not isinstance(key, str) or not key or "=" in key or "\0" in key
           or not isinstance(value, str) or "\0" in value for key, value in environment.items()):
        raise ValueError("environment overrides must be explicit string assignments")
    if backend == "hyperfine":
        declared = set(environment) | {key for task in tasks for key in task.get("environment", {})}
        reserved = declared & hyperfine_backend.RESERVED_ENVIRONMENT
        if reserved:
            raise SchemaError("Hyperfine reserves environment variables: " + ", ".join(sorted(reserved)))
    measurement_tool = hyperfine_backend.resolve(hyperfine) if backend == "hyperfine" else None
    receipt = load_receipt(suite, suite_path)
    repo = Path(__file__).resolve().parents[2]
    selected_tools = sorted({tool for task in tasks for tool in task["tool_ids"]})
    bindings = sources.resolve_tools(suite["tools"], selected_tools,
                                     cache_dir=cache_dir or repo / ".gammabench" / "tools",
                                     overrides=overrides, allow_dirty=allow_dirty,
                                     base_dir=suite_path.parent, repo_dir=repo)
    sources.verify_tools(bindings)
    staging = staging_directory(destination)
    snapshot = {"schema_version": 1, "id": label or f"run-{uuid.uuid4()}", "suite_id": suite["id"],
                "origin": "measured", "status": "partial", "created_at": datetime.now(timezone.utc).isoformat(),
                "contracts": copy.deepcopy(suite["contracts"]), "metrics": copy.deepcopy(suite["metrics"]),
                "tools": bindings, "host": _host(cpus), "schedule": {"repeats": repeats, "warmups": warmups,
                "selected_tasks": [task["id"] for task in tasks], "order": order,
                "method": "alternating reversed cyclic rotations", "fresh_process_per_sample": True},
                "records": [], "validation": {"status": "unqualified", "tasks": {}, "equivalence": []},
                "provenance": {"suite_original_sha256": sha256(suite_path), "explicit_environment": environment,
                "gammabench_version": __version__, "controller_source_sha256": controller_source_sha256(),
                "clock": "time.perf_counter_ns",
                "process_wall_includes": "spawn, complete child lifetime and process wait",
                "measurement_backend": measurement_tool or {"name": "python", "clock": "time.perf_counter_ns"},
                "timeout_seconds": timeout}}
    metadata = capture_metadata(suite)
    if metadata is not None:
        snapshot["metadata"] = metadata
    if measurement_tool:
        snapshot["provenance"].update(
            clock="Hyperfine 2.0.0 std::time::Instant",
            record_duration_clock="time.perf_counter_ns",
            record_duration_scope="Hyperfine controller lifetime for warmups and measurements; direct target lifetime for validation",
            process_wall_source="Hyperfine schema 2 measurements[0].time_wall_clock; seconds converted to nanoseconds",
            target_pid="not reported by Hyperfine; pid is null for its samples",
            hyperfine_environment={
                "HYPERFINE_ITERATION": "0",
                "HYPERFINE_RANDOMIZED_ENVIRONMENT_OFFSET": {
                    "value_recorded": False, "source": "Hyperfine randomized padding per sample"}})
    snapshot["schedule"]["pairing_id"] = snapshot["id"]
    for section in ("unsupported", "qualification_censored"):
        if section in suite:
            snapshot["schedule"][section] = copy.deepcopy(suite[section])
    write_json(staging / "unsealed.json", {"status": "partial", "destination": str(Path(destination).resolve())})
    context = {"suite_dir": str(suite_path.parent), "repo": str(repo), "python": sys.executable}
    for tool, binding in bindings.items():
        if binding.get("source_path"):
            context[f"source:{tool}"] = str(binding["source_path"])
        if binding.get("tool_path"):
            context[f"tool:{tool}"] = str(binding["tool_path"])
    task_contexts = {}
    input_hashes = {}
    task_input_hashes = {}
    harness_sources = {}
    try:
        snapshot["provenance"].update(capture_receipt(receipt, staging))
        for tool, binding in bindings.items():
            if binding.get("source_archive"):
                source = Path(binding["source_archive"])
                artifact = Path("sources") / f"{tool}-dirty.tar.gz"
                target = staging / artifact
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                digest = sha256(target)
                if digest != binding["source_archive_sha256"]:
                    raise SchemaError(f"dirty source archive changed during capture: {tool}")
                binding["source_archive_artifact"] = artifact.as_posix()
                binding["source_archive_artifact_sha256"] = digest
        for task in tasks:
            task_contexts[task["id"]] = dict(context)
            task_input_hashes[task["id"]] = {}
            for name, filename in task["inputs"].items():
                source = (suite_path.parent / filename).resolve()
                if not source.is_file() or not source.is_relative_to(suite_path.parent):
                    raise SchemaError(f"input is missing or escapes suite directory: {filename}")
                target = staging / "tasks" / task["id"] / "inputs" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                input_hashes[target.relative_to(staging).as_posix()] = sha256(target)
                task_input_hashes[task["id"]][name] = sha256(target)
                task_contexts[task["id"]][f"input:{name}"] = str(target)
            gate = {"status": "unqualified", "expected_checked": list(task.get("expected", {})),
                    "equivalence_fields": task.get("validation_equivalence_fields", task.get("equivalence_fields", []))}
            has_checks = bool(task.get("expected") or task.get("equivalence_fields")
                              or task.get("validation_equivalence_fields"))
            if "validation_command" in task or has_checks:
                record = _execute(task, "validation", 0, staging, task_contexts[task["id"]], suite["metrics"],
                                  environment, task.get("timeout_seconds", timeout), cpus[0] if cpus else None,
                                  task_input_hashes[task["id"]], harness_sources)
                snapshot["records"].append(record)
                gate["method"] = "custom-command" if "validation_command" in task else "task-command"
                if record["status"] != "completed":
                    gate.update(status="failed", reason="validation command did not pass")
                elif has_checks:
                    gate["status"] = "passed"
            snapshot["validation"]["tasks"][task["id"]] = gate
        _validate_equivalence(snapshot, tasks, staging, validation_only=True)
        _verify_inputs(staging, input_hashes)
        lookup = {task["id"]: task for task in tasks}
        for index, item in enumerate(order):
            task = lookup[item["task"]]
            if snapshot["validation"]["tasks"][task["id"]]["status"] == "failed":
                continue
            record = _execute(task, item["phase"], item["repeat"], staging, task_contexts[task["id"]],
                              suite["metrics"], environment, task.get("timeout_seconds", timeout),
                              cpus[index % len(cpus)] if cpus else None, task_input_hashes[task["id"]], harness_sources,
                              measurement_tool)
            snapshot["records"].append(record)
            if record["status"] == "invalid":
                snapshot["validation"]["tasks"][task["id"]].update(status="failed", reason=record.get("reason"))
        _validate_equivalence(snapshot, tasks, staging)
        sources.verify_tools(bindings)
        verify_receipt(receipt)
        if measurement_tool and sha256(Path(measurement_tool["path"])) != measurement_tool["sha256"]:
            raise SchemaError("Hyperfine executable changed during capture")
        if sha256(suite_path) != snapshot["provenance"]["suite_original_sha256"]:
            raise SchemaError("suite changed during capture")
        _verify_inputs(staging, input_hashes)
        snapshot["provenance"]["input_sha256"] = input_hashes
        snapshot["provenance"]["task_inputs_sha256"] = task_input_hashes
        for name, binding in harness_sources.items():
            if sha256(Path(name)) != binding["sha256"]:
                raise SchemaError(f"command source changed during capture: {name}")
        snapshot["provenance"]["harness_sources"] = harness_sources
        statuses = [gate["status"] for gate in snapshot["validation"]["tasks"].values()]
        snapshot["validation"]["status"] = "failed" if "failed" in statuses else "unqualified" if "unqualified" in statuses else "passed"
        snapshot["status"] = "failed" if any(record["status"] != "completed" for record in snapshot["records"]) or "failed" in statuses else "completed"
        if hasattr(os, "getloadavg"):
            snapshot["host"]["load_average_end"] = list(os.getloadavg())
        (staging / "unsealed.json").unlink()
        return seal(staging, destination, snapshot, suite)
    except BaseException as error:
        snapshot["status"] = "partial"
        write_json(staging / "snapshot.json", snapshot)
        write_json(staging / "suite.json", suite)
        write_json(staging / "unsealed.json", {"status": "partial", "reason": str(error), "destination": str(destination)})
        raise


def _verify_inputs(staging: Path, hashes: dict) -> None:
    for name, digest in hashes.items():
        if sha256(staging / name) != digest:
            raise SchemaError(f"input changed during capture: {name}")


def _validate_equivalence(snapshot: dict, tasks: list[dict], staging: Path, *, validation_only: bool = False) -> None:
    if not validation_only:
        snapshot["validation"]["equivalence"] = []
    groups = {}
    for task in tasks:
        if validation_only and not any(record["task"] == task["id"] and record["phase"] == "validation"
                                       for record in snapshot["records"]):
            continue
        fields = task.get("equivalence_fields", [])
        measured = [record for record in snapshot["records"] if record["task"] == task["id"]
                    and record["phase"] != "validation" and record["status"] == "completed"]
        if fields and measured and any(record["equivalence"] != measured[0]["equivalence"] for record in measured):
            snapshot["validation"]["tasks"][task["id"]].update(status="failed", reason="task output changed between samples")
            for record in measured:
                record.update(status="invalid", metrics={}, reason="task output changed between samples")
                write_json(staging / record["raw"]["record"], record)
        fields = task.get("validation_equivalence_fields", task.get("equivalence_fields", []))
        if fields:
            key = (task["case"], task["contract"], tuple(fields))
            groups.setdefault(key, []).append(task["id"])
    for (case, contract, fields), ids in groups.items():
        proof_tasks = {task["id"] for task in tasks if "validation_equivalence_fields" in task}
        records = [record for record in snapshot["records"] if record["task"] in ids
                   and record["status"] == "completed"
                   and (not validation_only or record["phase"] == "validation")
                   and (record["task"] not in proof_tasks or record["phase"] == "validation")]
        values = [record.get("equivalence", {}) for record in records]
        passed = bool(values) and all(value == values[0] for value in values)
        snapshot["validation"]["equivalence"].append({"case": case, "contract": contract,
             "fields": list(fields), "tasks": ids, "status": "passed" if passed else "failed",
             "value": values[0] if passed else None})
        if not passed:
            for task in ids:
                snapshot["validation"]["tasks"][task].update(status="failed", reason="equivalence fields differ")
            for record in snapshot["records"]:
                if record["task"] not in ids or record["status"] != "completed":
                    continue
                record.update(status="invalid", metrics={}, reason="equivalence fields differ")
                write_json(staging / record["raw"]["record"], record)
