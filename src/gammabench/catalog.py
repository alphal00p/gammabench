"""Build the searchable catalogue from declarations and retained analysis."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

from .metadata import (capture_metadata, field_definitions, metadata_values, project_implementation,
                       registry, same_value, validate_metadata, validate_values)
from .registry import checkout_path, repository_root, validate_registry
from .schema import SchemaError, validate_suite


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _merge_fields(target, incoming):
    """Combine discoverable historical enums without changing author validation."""
    for name, definition in incoming.items():
        if name not in target:
            target[name] = copy.deepcopy(definition)
            continue
        current = target[name]
        if any(current.get(key) != definition.get(key) for key in ("type", "scope", "semantic")):
            raise SchemaError(f"Incompatible catalog definitions for {name}; use a new field ID")
        if definition["type"] in ("enum", "tags"):
            current["values"] = list(dict.fromkeys(current["values"] + definition["values"]))
        if definition["type"] == "quantity":
            if current["unit"] != definition["unit"]:
                raise SchemaError(f"Incompatible canonical quantity unit for {name}")
            units = {unit["id"]: unit for unit in current["units"]}
            for unit in definition["units"]:
                if unit["id"] in units and units[unit["id"]]["factor"] != unit["factor"]:
                    raise SchemaError(f"Incompatible catalog unit for {name}")
                units.setdefault(unit["id"], copy.deepcopy(unit))
            current["units"] = list(units.values())


def _effective(overlay, captured, definitions):
    for name in overlay.keys() & captured.keys():
        if overlay[name] is not None and captured[name] is not None and not same_value(name, overlay[name], captured[name], definitions):
            raise SchemaError(f"Catalog metadata conflicts with captured field {name}")
    return {**overlay, **captured}


def _derived(values, additions, definitions):
    for name, value in additions.items():
        if name in values and values[name] is not None and not same_value(name, values[name], value, definitions):
            raise SchemaError(f"Declared metadata conflicts with derived field {name}")
        values[name] = value
    return values


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (timestamp.astimezone(timezone.utc) if timestamp.tzinfo else timestamp).date().isoformat()
    except ValueError:
        return None


def _source_bindings(root, benchmark, analyses):
    """Historical annotations can cite an exact retained workload input."""
    for case, source in benchmark.get("metadata_sources", {}).items():
        if not isinstance(source, dict) or set(source) != {"path", "sha256", "input"}:
            raise SchemaError("Metadata sources require path, sha256 and input")
        path = checkout_path(root, source["path"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise SchemaError(f"Metadata source changed for {case}")
        witnesses = [item["workload"]["sha256"].get(source["input"])
                     for analysis in analyses for item in analysis["series"]
                     if item["case"] == case and isinstance(item.get("workload"), dict)
                     and isinstance(item["workload"].get("sha256"), dict)]
        if not witnesses or any(value != source["sha256"] for value in witnesses):
            raise SchemaError(f"Metadata source does not match retained inputs for {case}")


def _capture_values(snapshot, item, values, definitions):
    implementation = item.get("implementation") or project_implementation(snapshot, item, values=values)
    statuses = item.get("statuses", {})
    status = item.get("status")
    if status is None and statuses:
        status = next(iter(statuses)) if len(statuses) == 1 else "partial"
    if values.get("capture.source_revision") is not None and not implementation.get("revision"):
        raise SchemaError("Declared measured source revision lacks retained source evidence")
    if values.get("capture.date") is not None and _date(snapshot.get("created_at")) is None:
        raise SchemaError("Declared capture date lacks a retained timestamp")
    additions = {
        "capture.id": snapshot["id"],
        "capture.origin": snapshot.get("origin"),
        "capture.status": status or snapshot.get("status"),
        "implementation.tool": implementation["tool_id"],
        "implementation.tool_label": implementation["tool_label"],
        "implementation.way": implementation["way_id"],
        "implementation.way_label": implementation["way_label"],
        "implementation.version": implementation["version_label"],
    }
    if (date := _date(snapshot.get("created_at"))) is not None:
        additions["capture.date"] = date
    if implementation.get("revision"):
        additions["capture.source_revision"] = implementation["revision"]
    qualification = item.get("validation_status")
    if qualification:
        additions["capture.validation"] = qualification if qualification in ("passed", "failed") else "unqualified"
    host = snapshot.get("host", {})
    if isinstance(host.get("hostname"), str) and host["hostname"]:
        additions["capture.host"] = host["hostname"]
    additions = {name: value for name, value in additions.items()
                 if value is not None and (not isinstance(value, str) or value.strip())}
    _derived(values, additions, definitions)
    validate_values(values, definitions)
    return values, implementation


def build_catalog(root: str | Path | None = None, *, include_workspaces: bool = False) -> dict:
    root = repository_root(root)
    catalog = validate_registry(json.loads((root / "web/benchmarks.json").read_text()))
    definitions = field_definitions()
    rows = []
    workspaces = []
    for benchmark in catalog["benchmarks"]:
        overlay = validate_metadata(benchmark.get("metadata", {"schema_version": 1}))
        local_definitions = field_definitions(overlay)
        _merge_fields(definitions, local_definitions)
        analyses = [json.loads(checkout_path(root, path).read_text()) for path in benchmark["analyses"]]
        _source_bindings(root, benchmark, analyses)
        declarations = None
        suite = None
        case_ids = set(overlay.get("cases", {}))
        if benchmark.get("suite"):
            suite = validate_suite(json.loads(checkout_path(root, benchmark["suite"]).read_text()))
            declarations = capture_metadata(suite)
            _merge_fields(local_definitions, field_definitions(declarations))
            _merge_fields(definitions, local_definitions)
            case_ids.update(task["case"] for task in suite["tasks"])
        by_case = {}
        for analysis, report in zip(analyses, benchmark["reports"]):
            snapshots = {snapshot["id"]: snapshot for snapshot in analysis["snapshots"]}
            seen_tasks = set()
            for item in analysis["series"]:
                case = item["case"]
                case_ids.add(case)
                snapshot = snapshots[item["snapshot_id"]]
                captured_definitions = field_definitions(snapshot.get("metadata"))
                _merge_fields(local_definitions, captured_definitions)
                _merge_fields(definitions, captured_definitions)
                declared = metadata_values(overlay, case=case, task=item["task"], metric=item["metric"])
                captured = item.get("metadata", metadata_values(snapshot.get("metadata"), case=case,
                                                               task=item["task"], metric=item["metric"]))
                values = _effective(declared, captured, local_definitions)
                identity = {"benchmark.id": benchmark["id"], "benchmark.definition_version": benchmark["definition_version"],
                            "case.id": case}
                _derived(values, identity, local_definitions)
                values, implementation = _capture_values(snapshot, item, values, local_definitions)
                item["metadata"] = copy.deepcopy(values)
                item["implementation"] = copy.deepcopy(implementation)
                context_key = (snapshot["id"], item["task"], _canonical(values), _canonical(implementation))
                contexts = by_case.setdefault(case, {})
                if context_key not in contexts:
                    digest = hashlib.sha256(_canonical(context_key).encode()).hexdigest()[:20]
                    contexts[context_key] = {
                        "id": snapshot["id"] + ":" + item["task"] + ":" + digest,
                        "snapshot_id": snapshot["id"], "label": item["engine"] + " / " + item["variant"],
                        "metadata": values, "implementation": implementation, "metrics": [],
                        "href": report, "status": snapshot.get("status"),
                    }
                contexts[context_key]["metrics"].append(item["metric"])
                seen_tasks.add((snapshot["id"], item.get("task"), case))
            for item in analysis.get("availability", []):
                case = item.get("case")
                if not isinstance(case, str) or not case:
                    continue
                case_ids.add(case)
                if (item["snapshot_id"], item.get("task"), case) in seen_tasks:
                    continue
                snapshot = snapshots[item["snapshot_id"]]
                item = {**item, "task": item.get("task", ""), "engine": item.get("engine", ""), "variant": item.get("variant", "")}
                declared = metadata_values(overlay, case=case, task=item["task"])
                captured = metadata_values(snapshot.get("metadata"), case=case, task=item["task"])
                values = _effective(declared, captured, local_definitions)
                _derived(values, {"benchmark.id": benchmark["id"], "benchmark.definition_version": benchmark["definition_version"],
                                  "case.id": case}, local_definitions)
                values, implementation = _capture_values(snapshot, item, values, local_definitions)
                context_key = (snapshot["id"], item["task"], item["engine"], item["variant"], item.get("status"))
                digest = hashlib.sha256(_canonical(context_key).encode()).hexdigest()[:20]
                by_case.setdefault(case, {}).setdefault(context_key, {
                    "id": snapshot["id"] + ":availability:" + digest, "snapshot_id": snapshot["id"],
                    "label": item["engine"] + " / " + item["variant"], "metadata": values,
                    "implementation": implementation, "metrics": [], "href": report,
                    "status": item.get("status"), "reason": item.get("reason", ""),
                })
        workspaces.append({"id": benchmark["id"], "analyses": analyses})
        if not case_ids:
            values = {**metadata_values(declarations), **metadata_values(overlay)}
            values = {name: value for name, value in values.items() if local_definitions[name]["scope"] == "workload"}
            _derived(values, {"benchmark.id": benchmark["id"], "benchmark.definition_version": benchmark["definition_version"]}, local_definitions)
            rows.append({"benchmark_id": benchmark["id"], "benchmark_title": benchmark["title"], "case_id": None,
                         "title": benchmark["title"], "description": benchmark["description"], "metadata": values,
                         "captures": [], "planned": True, "href": "benchmarks/" + benchmark["id"] + "/index.html"})
        for case in sorted(case_ids):
            contexts = list(by_case.get(case, {}).values())
            base = _effective(metadata_values(overlay, case=case), metadata_values(declarations, case=case),
                              local_definitions)
            common = {name: value for name, value in base.items() if local_definitions[name]["scope"] == "workload"}
            if not contexts and suite is not None:
                planned = [_effective(metadata_values(overlay, case=case, task=task["id"], metric=metric),
                                      metadata_values(declarations, case=case, task=task["id"], metric=metric),
                                      local_definitions)
                           for task in suite["tasks"] if task["case"] == case for metric in suite["metrics"]]
                if planned:
                    common = {name: planned[0][name] for name in set.intersection(*(set(values) for values in planned))
                              if local_definitions[name]["scope"] == "workload"
                              and all(same_value(name, planned[0][name], values[name], local_definitions) for values in planned[1:])}
            if contexts:
                common = {}
                for name in set.intersection(*(set(context["metadata"]) for context in contexts)):
                    values = [context["metadata"][name] for context in contexts]
                    if local_definitions[name]["scope"] == "workload" and all(same_value(name, values[0], value, local_definitions) for value in values[1:]):
                        common[name] = values[0]
            _derived(common, {"benchmark.id": benchmark["id"], "benchmark.definition_version": benchmark["definition_version"],
                              "case.id": case}, local_definitions)
            validate_values(common, local_definitions, scope="workload")
            rows.append({"benchmark_id": benchmark["id"], "benchmark_title": benchmark["title"], "case_id": case,
                         "title": case, "description": benchmark["description"], "metadata": common, "captures": contexts,
                         "planned": not bool(contexts), "default_metric": benchmark["default_metric"],
                         "metadata_source": benchmark.get("metadata_sources", {}).get(case),
                         "href": "benchmarks/" + benchmark["id"] + "/index.html?" + urlencode({"case": case, "tab": "compare"})})
    result = {"schema_version": 1, "vocabulary_version": registry()["vocabulary_version"],
              "fields": [{"id": name, **definition} for name, definition in definitions.items()], "cases": rows}
    if include_workspaces:
        result["workspaces"] = workspaces
    return result
