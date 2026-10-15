"""Project Nix source and runtime evidence for conservative commit histories.

Only the explicitly mapped subject moves along the x axis. Its built binary and
store identities remain evidence, while declared harness inputs, other sources,
compiler settings and actual runtime controls define a history configuration.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .schema import REVISION, SHA256
from .snapshot import sha256


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def controller_source_sha256() -> str:
    """Match the Nix framework inventory, including the actual imported runner."""
    inventory = [{"name": path.name, "sha256": sha256(path)}
                 for path in sorted(Path(__file__).parent.glob("*.py"))]
    return hashlib.sha256(_canonical(inventory).encode()).hexdigest()


def _selection(receipt: dict, task: dict):
    build = receipt.get("build", {})
    if not isinstance(build, dict):
        return None
    if "variants" not in build:
        return receipt, "", "", task["id"], task["variant"]
    variants = build["variants"]
    if not isinstance(variants, dict):
        return None
    matches = []
    for label, variant in variants.items():
        if not isinstance(variant, dict):
            continue
        originals = variant.get("original_task_variants", {})
        if not isinstance(originals, dict):
            continue
        for original, implementation in originals.items():
            package = variant.get("package_path", "")
            if (isinstance(implementation, str) and isinstance(package, str)
                    and task["id"] == label + "." + original):
                matches.append((variant.get("receipt"), label, package, original, implementation))
    if len(matches) != 1 or not isinstance(matches[0][0], dict):
        return None
    return matches[0]


def _source(source: dict, *, moving: bool = False) -> dict:
    # Do not strip arbitrary hashes: only the declared subject's source identity
    # can vary. Fixed dependency NAR/archive hashes and all unknown fields remain.
    omitted = {"store_path"}
    if moving:
        omitted |= {"revision", "nar_hash"}
    return {key: value for key, value in source.items() if key not in omitted}


def _repository(source: dict) -> str:
    url = source.get("canonical_project_url", source.get("url", ""))
    if not isinstance(url, str):
        return ""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    # Source archive and flake locators are evidence, never commit-page roots.
    if source.get("kind") != "git":
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.removesuffix(".git").rstrip("/"), "", ""))


def _host(snapshot: dict) -> dict:
    host = copy.deepcopy(snapshot.get("host", {}))
    for key in ("loadavg", "load_average_start", "load_average_end"):
        host.pop(key, None)
    if isinstance(host.get("selected_cpu_info"), str):
        host["selected_cpu_info"] = "\n".join(line for line in host["selected_cpu_info"].split("\n")
            if not line.startswith(("cpu MHz", "bogomips")))
    return host


def _declaration(value, label: str, package: str):
    if isinstance(value, str):
        if label:
            for kind in ("tool", "source"):
                value = value.replace("{" + kind + ":" + label + ".", "{" + kind + ":")
            value = value.replace("{suite_dir}/variants/" + label, "{suite_dir}")
        if package:
            value = value.replace(package + "/", "{suite_dir}/")
            if value == package:
                value = "{suite_dir}"
        return value
    if isinstance(value, list):
        return [_declaration(item, label, package) for item in value]
    if isinstance(value, dict):
        return {key: _declaration(item, label, package) for key, item in value.items()}
    return value


def _runtime_value(value: str, task: dict, record: dict, bindings: dict, label: str, package: str) -> str:
    # Resolve only retained bindings. Random staging roots are normalized only
    # when the complete value names this task's staged input/work directory.
    for name in task["inputs"]:
        suffix = "/tasks/" + task["id"] + "/inputs/" + name
        if value.startswith("/") and value.endswith(suffix) and name in record.get("inputs_sha256", {}):
            return "{input:" + name + "}"
    work = f"/tasks/{task['id']}/{record['phase']}-{record['repeat']:04d}/work"
    if value.startswith("/") and value.endswith(work):
        return "{workdir}"
    for name in task["tool_ids"]:
        binding = bindings.get(name, {})
        original = name.removeprefix(label + ".") if label else name
        for field, kind in (("tool_path", "tool"), ("source_path", "source")):
            path = binding.get(field)
            if isinstance(path, str) and path:
                value = value.replace(path, "{" + kind + ":" + original + "}")
    return _declaration(value, label, package)


def project_history(snapshot: dict, task: dict, metric: str, definition: dict,
                    records: list[dict], workload: dict | None, qualification: dict) -> dict | None:
    """Add source/context metadata, without altering any measurement or statistic."""
    receipt = snapshot.get("provenance", {}).get("nix_build")
    if not isinstance(receipt, dict):
        return None
    result = {"schema_version": 1, "kind": "nix-build", "revision": None, "repository": "",
              "capture": "timing", "reason": "", "context": {}, "evidence": {}}
    errors = []
    if not all(key in task for key in ("id", "variant", "engine", "inputs", "tool_ids", "argv")):
        result["reason"] = "Nix capture lacks its retained task declaration."
        result["context"] = {"unknown": snapshot["id"] + ":" + str(task.get("task", "unknown"))}
        return result
    selected = _selection(receipt, task)
    if selected is None:
        result["reason"] = "Nix variant does not have an unambiguous retained task-to-receipt mapping."
        result["context"] = {"unknown": snapshot["id"] + ":" + task["id"]}
        return result
    outer_receipt = receipt
    receipt, label, package, original_task, implementation = selected
    receipt_digest = (outer_receipt["build"]["variants"][label].get("receipt_sha256") if label else
                      snapshot.get("provenance", {}).get("nix_build_receipt_sha256"))
    if not isinstance(receipt_digest, str) or not SHA256.fullmatch(receipt_digest):
        receipt_digest = None
    sources = receipt.get("sources", {})
    subjects = receipt.get("subjects", {})
    if not isinstance(sources, dict) or not isinstance(subjects, dict) or any(not isinstance(value, dict) for value in sources.values()):
        result["reason"] = "Nix receipt does not retain valid source and subject mappings."
        result["context"] = {"unknown": snapshot["id"] + ":" + task["id"]}
        return result
    subject = subjects.get(task["engine"])
    if not isinstance(subject, str):
        subject = None
    source = sources.get(subject, {})
    for value in sources.values():
        kind = value.get("kind")
        if (kind == "git" and not REVISION.fullmatch(str(value.get("revision", "")))
                or kind == "archive" and not SHA256.fullmatch(str(value.get("archive_sha256", "")))
                or kind not in ("git", "archive")):
            errors.append("Nix sources do not all retain immutable Git or archive identities.")
    if not source:
        errors.append("No Nix source subject is declared for the measured engine.")
    elif source.get("kind") != "git" or not REVISION.fullmatch(str(source.get("revision", ""))):
        errors.append("No complete Git commit for the Nix subject; archive/version evidence is retained.")
    else:
        result.update(revision=source["revision"], repository=_repository(source))
    if any(value.get("dirty", False) for value in sources.values() if isinstance(value, dict)):
        errors.append("Dirty Nix source evidence does not identify an immutable measured context.")
    context = copy.deepcopy(receipt.get("comparison_context", {}))
    build = receipt.get("build", {})
    if not isinstance(context, dict):
        context = {}
    if not isinstance(build, dict):
        build = {}
    harness = any(context.get(key) for key in ("adapter_sources", "adapter_sha256", "harness_sha256"))
    infrastructure = all(build.get(key) for key in ("system", "nixpkgs", "framework_sha256", "hyperfine"))
    compiler = build.get("toolchain") or context.get("toolchain") or context.get("python")
    profile = context.get("profile") or build.get("profile") or context.get("python")
    if not context or not harness or not infrastructure or not compiler or not profile:
        errors.append("Nix receipt lacks complete fixed harness, toolchain or build-profile context.")
    dependencies = context.get("dependencies", {})
    if isinstance(dependencies, dict) and subject in dependencies:
        dependency = dependencies[subject]
        if not isinstance(dependency, dict) or any(key in dependency and key in source and dependency[key] != source[key]
                for key in ("kind", "url", "revision", "nar_hash", "archive_sha256")):
            errors.append("Nix subject and declared dependency identities disagree.")
        else:
            dependencies[subject] = _source(dependency, moving=True)
    fixed_build = {key: value for key, value in build.items() if key not in ("adapter", "executables", "variants")}
    if isinstance(build.get("adapter"), dict):
        fixed_build["adapter"] = {key: value for key, value in build["adapter"].items()
                                  if key not in ("path", "derivation")}
    bindings = snapshot.get("tools", {})
    executables = build.get("executables", {})
    if not isinstance(executables, dict) or any(not isinstance(value, dict) for value in executables.values()):
        executables = {}
    bound_executables = {}
    fixed_executables = {}
    adapter_path = build.get("adapter", {}).get("path", "") if isinstance(build.get("adapter"), dict) else ""
    for name in task["tool_ids"]:
        original = name.removeprefix(label + ".") if label else name
        executable, binding = executables.get(original, {}), bindings.get(name, {})
        if (not SHA256.fullmatch(str(executable.get("sha256", "")))
                or executable.get("path") not in (binding.get("path"), binding.get("tool_path"))
                or executable.get("sha256") != binding.get("sha256")):
            errors.append("Measured executable bindings do not match the retained Nix receipt.")
        elif binding.get("tool_path"):
            bound_executables[binding["tool_path"]] = executable
            path = executable["path"]
            # Only this explicitly declared compiled adapter output may move
            # with the subject. Independent helper executables stay fixed.
            moving = isinstance(adapter_path, str) and adapter_path and (path == adapter_path or path.startswith(adapter_path + "/"))
            if not moving:
                fixed_executables[original] = {**executable, "resolved_path": binding["tool_path"]}
    runtime = []
    measured = [record for record in records if record.get("phase") == "measure"]
    for record in measured:
        if (not all(key in record for key in ("environment", "cpu", "measurement_backend", "command_files_sha256"))
                or not isinstance(record.get("environment"), dict)
                or any(not isinstance(value, str) for value in record.get("environment", {}).values())
                or not isinstance(record.get("command_files_sha256"), dict)):
            errors.append("Task-specific runtime environment, affinity, backend or executable evidence is missing.")
            continue
        normalize = lambda value: _runtime_value(value, task, record, bindings, label, package)
        inputs = record.get("inputs_sha256", {})
        if (not isinstance(inputs, dict) or not set(task["inputs"]) <= inputs.keys()
                or any(not SHA256.fullmatch(str(value)) for value in inputs.values())):
            errors.append("Declared input content hashes are missing or invalid.")
            inputs = {}
        workload_names = task.get("workload_inputs", list(task["inputs"]))
        # A metadata receipt is the sole input allowed to follow the subject.
        # Its retained byte digest identifies it, regardless of its filename.
        receipt_inputs = sorted(name for name, digest in inputs.items()
                                if name not in workload_names and receipt_digest is not None and digest == receipt_digest)
        fixed_inputs = {name: digest for name, digest in inputs.items() if name not in receipt_inputs}
        files = {}
        program_digest = record["command_files_sha256"].get(record["command"][0])
        if not SHA256.fullmatch(str(program_digest or "")):
            errors.append("The measured command lacks its actual executable content hash.")
        for path, digest in record["command_files_sha256"].items():
            built = bound_executables.get(path)
            if built is not None:
                if built.get("sha256") != digest:
                    errors.append("Measured command content differs from its Nix executable receipt.")
            else:
                files[normalize(path)] = digest
        runtime.append({"environment": {key: normalize(value) for key, value in record["environment"].items()},
                        "cpu": record["cpu"], "backend": record["measurement_backend"],
                        "command": [normalize(value) for value in record["command"]],
                        "command_files": files, "input_hashes": fixed_inputs, "receipt_inputs": receipt_inputs,
                        "stdin": record.get("stdin"),
                        "timeout_seconds": record.get("timeout_seconds")})
    contexts = {_canonical(value): value for value in runtime}
    if len(contexts) != 1:
        errors.append("Measurement runtime settings are missing or differ within this capture.")
    if workload is None:
        errors.append("Immutable workload input identity is missing or inconsistent.")
    if not qualification.get("fields") or qualification.get("value") is None:
        errors.append("An exact accepted semantic witness is missing from the retained validation evidence.")
    host = _host(snapshot)
    if not any(key in host for key in ("selected_cpu_info", "uname", "hostname", "platform", "machine", "cpu", "cpu_count")):
        errors.append("Stable hardware identity is missing from the capture.")
    provenance = snapshot.get("provenance", {})
    controller = provenance.get("controller_source_sha256")
    if not isinstance(controller, str) or not SHA256.fullmatch(controller):
        # Old receipts can still join the variants of one actual controller run.
        # Independent captures without its source identity remain separate.
        controller = {"unknown_controller_snapshot": snapshot["id"]}
    declaration = {key: task[key] for key in ("argv", "validation_command", "environment", "expected",
                   "equivalence_fields", "validation_equivalence_fields", "timeout_seconds") if key in task}
    capture = context.get("capture_mode", "timing")
    result["capture"] = capture if isinstance(capture, str) and capture else "unspecified"
    result["context"] = {"subject": subject, "project": _source(source, moving=True),
        "fixed_sources": {name: _source(value) for name, value in sources.items() if name != subject},
        "build": fixed_build, "fixed_executables": fixed_executables,
        "comparison_context": context, "controller": controller,
        "suite": snapshot["suite_id"], "task": original_task, "implementation": implementation,
        "declaration": _declaration(declaration, label, package),
        "explicit_environment": _declaration(provenance.get("explicit_environment", {}), label, package),
        "runtime": list(contexts.values()), "host": host, "workload": workload,
        "metric": {"id": metric, **definition}, "qualification": qualification,
        "contract": snapshot.get("contracts", {}).get(task["contract"]),
        "clock": {key: provenance.get(key) for key in ("clock", "measurement_backend", "process_wall_includes",
                   "process_wall_source", "hyperfine_environment")}}
    result["evidence"] = {"receipt": provenance.get("nix_build_receipt"),
        "receipt_sha256": provenance.get("nix_build_receipt_sha256"), "variant": label,
        "original_task": original_task, "original_variant": implementation,
        "subject": subject, "source": copy.deepcopy(source), "executables": copy.deepcopy(executables),
        "runtime_records": [{key: record.get(key) for key in ("phase", "repeat", "command", "environment", "cpu",
                             "measurement_backend", "command_files_sha256", "inputs_sha256", "workload_inputs_sha256")} for record in measured]}
    result["reason"] = " ".join(dict.fromkeys(errors))
    if errors:
        result["context"]["unknown"] = snapshot["id"] + ":" + task["id"]
    return result
