"""Import completed legacy generation runs without relabelling them as reruns."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from .snapshot import seal


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contract(manifest: dict[str, Any], task: dict[str, Any]) -> str:
    declared = task.get("output_contract", manifest.get("output_contract"))
    if declared:
        return declared
    mode = task.get("mode", "unknown")
    return {"count": "graph-count-v1", "materialize": "materialized-incidence-and-symmetry-v1"}.get(
        mode, "legacy-" + mode + "-v1"
    )


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _verify_capture(source: Path, metadata: dict[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
    captured = []
    for path in sorted(source.glob("*/record.json")):
        record = _read(path)
        captured.append((path, record))
        for relative, digest in record.get("raw_sha256", {}).items():
            original = source / relative
            if not original.resolve().is_relative_to(source):
                raise ValueError("Legacy raw artifact path escapes the captured run")
            if not original.is_file() or _sha(original) != digest:
                raise ValueError(f"Legacy raw artifact changed or is missing: {relative}")
        for name in ("stdout", "stderr"):
            original = path.parent / name
            if original.is_file() and str(original.relative_to(source)) not in record.get("raw_sha256", {}):
                raise ValueError("Legacy output is not bound by a captured hash")
    key = lambda record: (record["task"], record["tool"], record["repeat"])
    actual = {key(record): record for _, record in captured}
    envelopes = metadata.get("child_statuses", [])
    expected = {key(record): record for record in envelopes}
    queue = {tuple(item) for item in metadata.get("queue", [])}
    if len(actual) != len(captured) or len(expected) != len(envelopes) or actual != expected or set(actual) != queue:
        raise ValueError("Legacy queue, child envelopes and captured records differ")
    return captured


def _profile_metrics(source: Path, record: dict[str, Any]) -> dict[str, int]:
    """Replay additive counters; per-process peaks remain separate metrics."""
    tool, captured = record["tool"], record.get("metrics", {})
    values = {}
    if tool == "callgrind":
        total = 0
        for relative in record.get("raw_sha256", {}):
            if not re.fullmatch(r"callgrind(?:\.\d+)?\.out", Path(relative).name):
                continue
            events, summary, self_total, skip_call = None, None, 0, False
            with (source / relative).open() as handle:
                for line in handle:
                    if line.startswith("events:"):
                        events = line.split()[1:]
                    elif line.startswith("summary:"):
                        summary = [int(value) for value in line.split()[1:]]
                    elif line.startswith("calls="):
                        if skip_call:
                            raise ValueError("Invalid nested Callgrind call-cost row")
                        skip_call = True
                    elif line and (line[0].isdigit() or line[0] in "+-*"):
                        fields = line.split()
                        if len(fields) != 2:
                            raise ValueError("Unsupported Callgrind cost format")
                        if skip_call:
                            skip_call = False
                        else:
                            self_total += int(fields[1])
            if not events or summary is None or "Ir" not in events:
                raise ValueError("Missing Callgrind events/summary")
            if events != ["Ir"] or skip_call or self_total != summary[0]:
                raise ValueError("Callgrind self-cost sum differs from summary")
            total += summary[events.index("Ir")]
        if total != captured.get("whole_process_Ir"):
            raise ValueError("Callgrind counter differs from captured envelope")
        values["whole_process_Ir"] = total
    elif tool == "dhat":
        processes = []
        for relative in record.get("raw_sha256", {}):
            if not re.fullmatch(r"dhat(?:\.\d+)?\.json", Path(relative).name):
                continue
            data = _read(source / relative)
            points = data["pps"]
            process = {"allocated_bytes": sum(point["tb"] for point in points),
                       "allocated_blocks": sum(point["tbk"] for point in points),
                       "peak_live_bytes": sum(point.get("gb", 0) for point in points),
                       "live_blocks_at_peak_bytes": sum(point.get("gbk", 0) for point in points)}
            processes.append((data["pid"], process))
        for name in ("allocated_bytes", "allocated_blocks"):
            total = sum(process[name] for _, process in processes)
            if total != captured.get(name):
                raise ValueError("DHAT additive counter differs from captured envelope")
            values[name] = total
        # A single-process peak is an actual simultaneous peak. Multiple child
        # peaks cannot be added because their peak instants may differ.
        if len(processes) == 1:
            _, process = processes[0]
            values.update({name: process[name] for name in ("peak_live_bytes", "live_blocks_at_peak_bytes")})
    return values


def import_snapshot(source: str | Path, destination: str | Path, *, label: str | None = None) -> Path:
    """Preserve raw record envelopes, controls and gates from a legacy run directory.

    Profile records are retained as provenance, rather than added to native timing
    samples. Large graph dumps, profiler binaries and external implementations
    remain hash-addressed references; they are not copied into a Git repository.
    """
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.exists():
        raise FileExistsError(destination)
    metadata_path = source / "metadata.json"
    metadata = _read(metadata_path)
    if metadata.get("status") not in {"complete", "completed", "complete_with_censored_rows"} or not metadata.get("inputs_unchanged"):
        raise ValueError("Only a completed run with unchanged captured inputs can be imported")
    manifest_path = Path(metadata["manifest"])
    if not manifest_path.is_file():
        # Supports moving a legacy evidence directory before import.
        manifest_path = source.parent / manifest_path.name
    if _sha(manifest_path) != metadata["manifest_sha256"]:
        raise ValueError("Legacy manifest hash differs from the captured identity")
    manifest = _read(manifest_path)
    captured_records = _verify_capture(source, metadata)
    tasks = {task["id"]: task for task in manifest["tasks"]}
    identifier = label or ("generation-" + source.name + "-" + metadata["run_id"][:8])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier):
        raise ValueError("Snapshot label must be a safe identifier")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".historical-import-", dir=destination.parent))
    try:
        controls = staging / "legacy"
        controls.mkdir()
        shutil.copyfile(metadata_path, controls / "metadata.json")
        shutil.copyfile(manifest_path, controls / "manifest.json")
        freezes = []
        for filename, expected_sha in manifest.get("files", {}).items():
            path = Path(filename)
            if "adapter-freeze" not in path.name or path.suffix != ".json" or not path.is_file():
                continue
            if _sha(path) != expected_sha:
                raise ValueError("Legacy adapter source freeze changed since capture")
            freeze = _read(path)
            name = f"adapter-freeze-{len(freezes):02d}.json"
            shutil.copyfile(path, controls / name)
            freezes.append((freeze, "legacy/" + name))
        records, profile_records, qualification, workload_hashes = [], [], {}, {}
        contracts, metrics, tools, imported_tasks = {}, {}, {}, []
        gates_by_case: dict[str, dict[str, Any]] = {}
        for task_id, task in tasks.items():
            contract = _contract(manifest, task)
            contracts.setdefault(contract, {"description": task.get("timing_boundary", manifest.get("scope", "Legacy output contract")),
                                            "included": [task.get("mode", "legacy operation")],
                                            "excluded": ["No new execution during historical import"]})
            engine = task["engine"]
            revision = task.get("revision")
            source_proof = None
            dependency_sources = {}
            for freeze, artifact in freezes:
                if freeze.get("binary") != task["command"][0]:
                    continue
                dependency_sources = {name: freeze[key] for name, key in (
                    ("gammaloop", "native_commit"), ("symbolica", "graphica_commit"), ("feyngraph", "feyngraph_commit")) if key in freeze}
                native_revision = freeze.get("feynkit_commit", freeze.get("source_commit"))
                if native_revision:
                    dependency_sources["gammaloop"] = native_revision
                # Legacy task.revision identifies the shared harness's native
                # gammaloop revision even when selecting an external backend.
                declared = dependency_sources.get("gammaloop")
                if revision and declared and revision != declared:
                    raise ValueError("Task revision and captured adapter source revision differ")
                revision = revision or declared
                source_proof = artifact
            tools[task_id] = {"kind": "historical", "revision": revision,
                              "missing_source_pin": revision is None,
                              "source_freeze": source_proof, "dependency_sources": dependency_sources,
                              "engine_revision": dependency_sources.get({"linnet": "gammaloop", "graphica": "symbolica", "feyngraph": "feyngraph"}.get(engine, "")),
                              "command": task["command"],
                              "binary_hashes": task.get("binary_hashes", {}),
                              "manifest_file_hashes": "legacy/manifest.json"}
            imported_tasks.append({"id": task_id, "case": task["case"], "engine": engine,
                                   "variant": task.get("variant", task.get("role", "baseline")),
                                   "contract": contract, "argv": task["command"],
                                   "tool_ids": [], "inputs": {}, "expected": task.get("expected", {})})
            workload = {}
            case_path = Path(task.get("inputs", {}).get("case.json", ""))
            if case_path.is_file():
                declared = manifest.get("files", {}).get(str(case_path))
                if declared is not None and declared != _sha(case_path):
                    raise ValueError("Legacy case input changed since capture")
                if declared is not None:
                    workload["case.json"] = declared
            environment = task.get("environment", {})
            mapping_path = environment.get("BENCH_FULL_MODEL_FILE", environment.get("BENCH_ROUTED_MODEL_FILE"))
            if mapping_path and Path(mapping_path).is_file():
                mapping_path = Path(mapping_path)
                declared = manifest.get("files", {}).get(str(mapping_path))
                if declared is not None and declared != _sha(mapping_path):
                    raise ValueError("Legacy model mapping changed since capture")
                if declared is not None:
                    workload["model-map.json"] = declared
                model_path = Path(_read(mapping_path)["model_path"])
                if model_path.is_file():
                    declared = manifest.get("files", {}).get(str(model_path))
                    if declared is not None and declared != _sha(model_path):
                        raise ValueError("Legacy model input changed since capture")
                    if declared is not None:
                        workload["model.json"] = declared
            workload_hashes[task_id] = workload
            gate_path = Path(task.get("correctness_gate", ""))
            gate = _read(gate_path) if gate_path.is_file() else None
            declared_gate = manifest.get("files", {}).get(str(gate_path))
            if gate and declared_gate is not None and declared_gate != _sha(gate_path):
                raise ValueError("Legacy correctness gate changed since capture")
            passed = bool(task.get("comparison_qualified") and gate and declared_gate and gate.get("status") == "passed")
            qualification[task_id] = {"status": "passed" if passed else "unqualified",
                                      "legacy_gate_sha256": _sha(gate_path) if gate else None}
            if gate:
                gate_name = "gate-" + task["case"] + ".json"
                shutil.copyfile(gate_path, controls / gate_name)
                qualification[task_id]["artifact"] = "legacy/" + gate_name
            if passed:
                group = gates_by_case.setdefault(task["case"], {"case": task["case"], "contract": contract,
                    "tasks": [], "fields": gate.get("exact_fields", ["complete canonical graph sets", "per-graph native symmetry factors"]), "status": "passed"})
                group["tasks"].append(task_id)
        task_repeats = {}
        for index, (path, old) in enumerate(captured_records):
            if old.get("task") not in tasks:
                raise ValueError(f"Captured record names an unknown task: {path}")
            task = tasks[old["task"]]
            raw_dir = staging / "raw" / f"{index:04d}"
            raw_dir.mkdir(parents=True)
            raw = {}
            for name in ("record.json", "stdout", "stderr"):
                original = path.parent / name
                if original.is_file():
                    shutil.copyfile(original, raw_dir / name)
                    raw[name.replace(".json", "")] = str((raw_dir / name).relative_to(staging))
            tool = old.get("tool", "native")
            if tool not in {"native", "warmup", "validation"}:
                profile_records.append({"tool": tool, "task": old["task"], "repeat": old["repeat"],
                                        "raw_record": raw["record"], "status": old.get("status")})
                if tool not in {"callgrind", "dhat"}:
                    continue
                counter_task = old["task"] + "-" + tool
                counter_values = _profile_metrics(source, old) if old.get("status") == "completed" else {}
                for name in counter_values:
                    metrics.setdefault(name, {"unit": "instructions" if name == "whole_process_Ir" else "blocks" if name == "live_blocks_at_peak_bytes" else "bytes" if name.endswith("bytes") else "blocks", "direction": "lower",
                        "scope": "Single-process live block count at the maximum live byte instant; not maximum live blocks" if name == "live_blocks_at_peak_bytes" else "Single-process peak live heap bytes; process-tree peak is not inferred" if name == "peak_live_bytes" else "Whole process tree additive software counter, including observer/drop", "result_field": "legacy_metrics." + name})
                records.append({"task": counter_task, "case": task["case"], "engine": task["engine"],
                    "variant": task.get("variant", task.get("role", "baseline")), "contract": _contract(manifest, task),
                    "phase": "measure", "repeat": old["repeat"], "status": "timeout" if old.get("timed_out") else old.get("status", "failed") if old.get("status") in {"completed", "timeout", "failed", "invalid"} else "invalid", "returncode": old.get("returncode"),
                    "duration_ns": old.get("child_wall_ns", 0), "metrics": counter_values, "command": old["command"], "raw": raw,
                    "inputs_sha256": workload_hashes[old["task"]], "result": {"legacy_metrics": counter_values}, "legacy_profile_tool": tool})
                if counter_task not in task_repeats:
                    imported_tasks.append({"id": counter_task, "case": task["case"], "engine": task["engine"],
                        "variant": task.get("variant", task.get("role", "baseline")), "contract": _contract(manifest, task), "argv": old["command"], "tool_ids": [], "inputs": {}})
                    qualification[counter_task] = qualification[old["task"]].copy()
                    for group in gates_by_case.values():
                        if old["task"] in group["tasks"]:
                            group["tasks"].append(counter_task)
                    task_repeats[counter_task] = 0
                task_repeats[counter_task] += 1
                continue
            phase = "warmup" if tool == "warmup" else "validation" if tool == "validation" else "measure"
            status = old.get("status", "failed")
            if old.get("timed_out"):
                status = "timeout"
            if status not in {"completed", "timeout", "failed", "invalid"}:
                status = "invalid"
            values = {}
            if status == "completed":
                values["process_wall_ns"] = old["child_wall_ns"]
                metrics.setdefault("process_wall_ns", {"unit": "ns", "direction": "lower", "scope": "complete process lifetime"})
                result = old.get("result", {})
                for name in ("elapsed_ns", "materialized_ns", "signed_routed_ns", "full_diagrams_ns", "setup_ns"):
                    if _numeric(result.get(name)):
                        metric = name
                        values[metric] = result[name]
                        metrics.setdefault(metric, {"unit": "ns", "direction": "lower", "scope": {
                            "full_diagrams_ns": "Fresh setup through retained public full GenerationResult, local numerators/projectors included; aggregate multiplication and observation/drop excluded",
                            "signed_routed_ns": "Fresh setup through retained signed and momentum-routed typed diagrams; numerators/projectors and observation/drop excluded",
                            "materialized_ns": "Request compilation through retained owned incidence records and native symmetry factors; observation/drop excluded",
                            "elapsed_ns": "Legacy driver inner endpoint; see task boundary and raw RESULT scope",
                            "setup_ns": "Legacy request/model/options setup only"}[name], "result_field": name})
            records.append({"task": old["task"], "case": task["case"], "engine": task["engine"],
                            "variant": task.get("variant", task.get("role", "baseline")),
                            "contract": _contract(manifest, task), "phase": phase,
                            "repeat": old["repeat"], "status": status, "returncode": old.get("returncode"),
                            "duration_ns": old.get("child_wall_ns", 0), "metrics": values,
                            "command": old["command"], "raw": raw, "result": old.get("result", {}),
                            "legacy_started_ns": old.get("started_ns"), "cpus": old.get("cpus", []),
                            "inputs_sha256": workload_hashes[old["task"]]})
        if not records:
            raise ValueError("Legacy run contains no native, warmup or validation records")
        suite = {"schema_version": 1, "id": "generation-historical", "description": "Imported legacy generation study; archived commands are provenance, not runnable tasks",
                 "contracts": contracts, "metrics": metrics, "tools": {}, "tasks": imported_tasks, "archived_only": True}
        snapshot = {"schema_version": 1, "id": identifier, "suite_id": suite["id"], "origin": "historical-import",
                    "status": "imported", "created_at": metadata.get("finished", metadata.get("started", "unknown")),
                    "contracts": contracts, "metrics": metrics, "tools": tools, "host": metadata.get("hardware", {}),
                    "schedule": {"native_rounds": manifest.get("native_rounds"), "repeats": manifest.get("native_rounds"), "pairing_id": metadata["run_id"], "task_repeats": task_repeats, "warmups_per_task": manifest.get("warmups_per_task"),
                                 "queue": metadata.get("queue", []), "unsupported": manifest.get("unsupported", []),
                                 "qualification_censored": manifest.get("qualification_censored", [])},
                    "records": records, "validation": {"tasks": qualification, "equivalence": list(gates_by_case.values())},
                    "provenance": {"legacy_run_id": metadata["run_id"], "legacy_manifest_sha256": metadata["manifest_sha256"],
                                   "legacy_controller_sha256": metadata.get("controller_sha256"), "legacy_metadata_sha256": _sha(metadata_path),
                                   "legacy_source_directory": str(source), "profile_records": profile_records,
                                   "missing_artifacts": "Large graph dumps and raw profiles not copied; original paths and hashes retained in controls and gates",
                                   "new_execution": False}}
        seal(staging, destination, snapshot, suite)
        return destination
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
