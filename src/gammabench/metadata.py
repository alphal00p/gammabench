"""Controlled discovery metadata and identities derived from retained tool evidence."""
from __future__ import annotations

import copy
from datetime import date
import hashlib
from importlib.resources import files
import json
import math
import re
from urllib.parse import urlsplit, urlunsplit

from .schema import REVISION, SHA256, SchemaError

NA = {"state": "not-applicable"}
_TYPES = {"enum", "tags", "number", "quantity", "boolean", "text", "date"}
_EXTENSION = re.compile(r"x\.[a-z][a-z0-9_-]*\.[a-z][a-z0-9_.-]*")


def registry() -> dict:
    return json.loads(files("gammabench").joinpath("assets", "metadata.json").read_text(encoding="utf-8"))


def _require(condition, message):
    if not condition:
        raise SchemaError(message)


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _definition(name, definition):
    _require(isinstance(definition, dict), f"Metadata field {name} needs a definition")
    _require(isinstance(definition.get("label"), str) and definition["label"].strip(), f"Metadata field {name} needs a label")
    _require(definition.get("type") in _TYPES, f"Unknown metadata type for {name}")
    _require(definition.get("scope") in ("workload", "capture"), f"Unknown metadata scope for {name}")
    _require(type(definition.get("semantic")) is bool, f"Metadata field {name} must declare semantic identity")
    if definition["type"] in ("enum", "tags"):
        values = definition.get("values")
        _require(isinstance(values, list) and values and all(isinstance(value, str) and value for value in values)
                 and len(values) == len(set(values)), f"Metadata field {name} needs distinct controlled values")
    if definition["type"] == "quantity":
        units = definition.get("units")
        _require(isinstance(units, list) and units, f"Metadata quantity {name} needs declared units")
        for unit in units:
            _require(isinstance(unit, dict) and isinstance(unit.get("id"), str) and unit["id"]
                     and isinstance(unit.get("label"), str) and _finite(unit.get("factor")) and unit["factor"] > 0,
                     f"Invalid declared unit for {name}")
        _require(len({unit["id"] for unit in units}) == len(units), f"Duplicate metadata unit for {name}")
        _require(any(unit["id"] == definition.get("unit") and unit["factor"] == 1 for unit in units),
                 f"Metadata quantity {name} needs its canonical unit")
    for bound in ("min", "max"):
        if bound in definition:
            _require(_finite(definition[bound]), f"Invalid metadata bound for {name}")
    if "integer" in definition:
        _require(type(definition["integer"]) is bool, f"Invalid integer constraint for {name}")
    return definition


def field_definitions(metadata=None) -> dict:
    controlled = registry()["fields"]
    metadata = metadata or {}
    frozen = metadata.get("definitions")
    if frozen is not None:
        _require(isinstance(frozen, dict), "Captured metadata definitions must be an object")
        definitions = copy.deepcopy(frozen)
        for name, definition in definitions.items():
            _definition(name, definition)
            _require(name in controlled or _EXTENSION.fullmatch(name), f"Undeclared metadata field {name}")
    else:
        definitions = copy.deepcopy(controlled)
    extensions = metadata.get("fields", {})
    _require(isinstance(extensions, dict), "Metadata extension fields must be an object")
    for name, definition in extensions.items():
        _require(isinstance(name, str) and _EXTENSION.fullmatch(name), "Extension fields require x.<namespace>.<field> names")
        _definition(name, definition)
        _require(name not in definitions or definitions[name] == definition, f"Conflicting metadata field {name}")
        definitions[name] = copy.deepcopy(definition)
    return definitions


def validate_values(values, definitions, scope=None) -> dict:
    _require(isinstance(values, dict), "Metadata values must be an object")
    for name, value in values.items():
        _require(name in definitions, f"Undeclared metadata field {name}")
        definition = definitions[name]
        _require(scope is None or definition["scope"] == scope, f"Metadata field {name} has the wrong scope")
        if value is None or value == NA:
            continue
        kind = definition["type"]
        if kind == "enum":
            valid = isinstance(value, str) and value in definition["values"]
        elif kind == "tags":
            valid = (isinstance(value, list) and all(isinstance(item, str) and item in definition["values"] for item in value)
                     and len(value) == len(set(value)))
        elif kind in ("number", "quantity"):
            number = value
            if kind == "quantity":
                _require(isinstance(value, dict) and set(value) == {"value", "unit"}, f"Invalid metadata quantity {name}")
                unit = next((unit for unit in definition["units"] if unit["id"] == value["unit"]), None)
                _require(unit is not None and _finite(value["value"]), f"Undeclared or invalid metadata unit/value for {name}")
                number = value["value"] * unit["factor"]
            valid = (_finite(number) and (not definition.get("integer") or type(number) is int)
                     and ("min" not in definition or number >= definition["min"])
                     and ("max" not in definition or number <= definition["max"]))
        elif kind == "boolean":
            valid = type(value) is bool
        elif kind == "date":
            valid = isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None
            if valid:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    valid = False
        else:
            valid = isinstance(value, str) and bool(value.strip()) and "\0" not in value
        _require(valid, f"Invalid {kind} value for metadata field {name}")
    return copy.deepcopy(values)


def validate_metadata(metadata, *, cases=None, tasks=None, metrics=None) -> dict:
    _require(isinstance(metadata, dict), "Metadata must be an object")
    _require(type(metadata.get("schema_version")) is int and metadata["schema_version"] == 1, "Unsupported metadata schema")
    allowed = {"schema_version", "vocabulary_version", "definitions", "fields", "values", "cases", "tasks", "metrics"}
    _require(not set(metadata) - allowed, "Unknown metadata declaration section")
    if "vocabulary_version" in metadata:
        _require(type(metadata["vocabulary_version"]) is int and 1 <= metadata["vocabulary_version"] <= registry()["vocabulary_version"], "Unsupported metadata vocabulary")
    definitions = field_definitions(metadata)
    validate_values(metadata.get("values", {}), definitions)
    for section, known in (("cases", cases), ("tasks", tasks), ("metrics", metrics)):
        entries = metadata.get(section, {})
        _require(isinstance(entries, dict), f"Metadata {section} must be an object")
        _require(known is None or set(entries) <= set(known), f"Metadata references unknown {section}")
        for identity, values in entries.items():
            _require(isinstance(identity, str) and identity, f"Invalid metadata {section} identifier")
            validate_values(values, definitions)
    return copy.deepcopy(metadata)


def metadata_values(metadata=None, *, case=None, task=None, metric=None) -> dict:
    metadata = metadata or {}
    result = copy.deepcopy(metadata.get("values", {}))
    for section, identity in (("cases", case), ("tasks", task), ("metrics", metric)):
        result.update(copy.deepcopy(metadata.get(section, {}).get(identity, {})))
    return result


def capture_metadata(suite, *, definitions=None, vocabulary_version=None) -> dict | None:
    if not any("metadata" in owner for owner in (suite, *suite["tasks"], *suite["metrics"].values())):
        return None
    declared = copy.deepcopy(suite.get("metadata", {"schema_version": 1}))
    _require("definitions" not in declared, "Suite metadata cannot override the controlled field registry")
    if definitions is None:
        definitions = field_definitions(declared)
    else:
        definitions = field_definitions({"definitions": definitions, "fields": declared.get("fields", {})})
    for section, entries in (("tasks", suite["tasks"]), ("metrics", suite["metrics"].items())):
        target = declared.setdefault(section, {})
        for owner in entries:
            identity, value = (owner["id"], owner.get("metadata")) if section == "tasks" else (owner[0], owner[1].get("metadata"))
            if value is not None:
                validate_values(value, definitions)
                _require(identity not in target or target[identity] == value, f"Duplicate metadata declaration for {section} {identity}")
                target[identity] = copy.deepcopy(value)
    version = declared.get("vocabulary_version", registry()["vocabulary_version"])
    if vocabulary_version is not None:
        _require("vocabulary_version" not in declared or version == vocabulary_version, "Metadata vocabulary differs from retained suite")
        version = vocabulary_version
    declared.update(vocabulary_version=version, definitions=definitions)
    return validate_metadata(declared, cases={task["case"] for task in suite["tasks"]},
                             tasks={task["id"] for task in suite["tasks"]}, metrics=suite["metrics"])


def semantic_metadata(values, definitions) -> dict:
    result = {}
    for name, value in values.items():
        definition = definitions[name]
        if not definition["semantic"] or value is None:
            continue
        if definition["type"] == "quantity" and value != NA:
            factor = next(unit["factor"] for unit in definition["units"] if unit["id"] == value["unit"])
            value = {"value": value["value"] * factor, "unit": definition["unit"]}
        elif definition["type"] == "tags" and value != NA:
            value = sorted(value)
        result[name] = {"value": copy.deepcopy(value), "definition": {key: definition[key] for key in
                        ("type", "scope", "integer", "unit") if key in definition}}
    return result


def same_value(name, left, right, definitions) -> bool:
    """Compare typed retained values, including declared quantity conversions."""
    definition = {**definitions[name], "semantic": True}
    return semantic_metadata({name: left}, {name: definition}) == semantic_metadata({name: right}, {name: definition})


def _repository(source):
    url = source.get("canonical_project_url", source.get("url", ""))
    if not isinstance(url, str):
        return ""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.removesuffix(".git").rstrip("/"), "", ""))


def project_implementation(snapshot, task_or_series, *, values=None) -> dict:
    """Use captured source/binary pins; labels never supply a measured version."""
    from .history_context import _selection

    item = task_or_series
    task = item.get("task", item.get("id", ""))
    engine, variant = item["engine"], item["variant"]
    values = values if values is not None else metadata_values(snapshot.get("metadata"), case=item.get("case"), task=task, metric=item.get("metric"))
    text = lambda name, fallback: values.get(name) if isinstance(values.get(name), str) else fallback
    bindings = snapshot.get("tools", {})
    source, binaries, way = {}, {}, variant
    source_binding = ""
    profile_bindings = {(record["task"], record["tool"])
        for record in snapshot.get("provenance", {}).get("profile_records", [])
        if isinstance(record, dict) and isinstance(record.get("task"), str) and isinstance(record.get("tool"), str)
        and record["task"] + "-" + record["tool"] == task}
    receipt = snapshot.get("provenance", {}).get("nix_build")
    if isinstance(receipt, dict):
        selected = _selection(receipt, {"id": task, "variant": variant})
        if selected is not None:
            receipt, label, _, _, way = selected
            subject = receipt.get("subjects", {}).get(engine)
            source = receipt.get("sources", {}).get(subject, {})
            if source:
                source_binding = "sources." + subject
            binaries = receipt.get("build", {}).get("executables", {})
    if not source:
        source_tool = text("implementation.source_tool", "")
        selected_names = item.get("tool_ids", [])
        if source_tool:
            candidates = [name for name in (source_tool, *(name for name in selected_names if name.endswith("." + source_tool))) if name in bindings]
        elif task in bindings:
            candidates = [task]
        elif profile_bindings:
            # Only explicit retained profile provenance identifies a base task.
            # Repeated rounds are one mapping; ambiguous mappings remain unknown.
            base = next(iter(profile_bindings))[0] if len(profile_bindings) == 1 else None
            candidates = [base] if base in bindings else []
        elif engine in bindings:
            candidates = [engine]
        else:
            candidates = [name for name in selected_names if bindings.get(name, {}).get("kind") in ("git", "archive")]
        if len(set(candidates)) == 1:
            source_binding = candidates[0]
            source = copy.deepcopy(bindings[source_binding])
        binaries = binaries or {name: {key: bindings[name][key] for key in ("kind", "sha256", "tool_path", "declared_version") if key in bindings[name]}
                                for name in selected_names if name in bindings}
    kind = source.get("kind", "unknown")
    revision = source.get("engine_revision") if kind == "historical" else source.get("revision")
    revision = revision if isinstance(revision, str) and REVISION.fullmatch(revision) and not source.get("dirty") else None
    if kind == "historical":
        kind = "git" if revision else "archive"
    source_identity = {key: source[key] for key in ("kind", "url", "canonical_project_url", "engine_revision", "revision", "nar_hash",
                       "archive_sha256", "sha256", "dirty", "dirty_source_sha256", "source_sha256",
                       "binary_hashes", "dependency_sources") if key in source}
    way = text("implementation.way", way)
    identity = {"tool": engine, "way": way, "source": source_identity, "executables": binaries}
    if not source_identity and not binaries:
        identity["unknown_capture"] = snapshot["id"]
        identity["unknown_task"] = task
    version_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if revision:
        version_label = revision[:10]
    elif source.get("dirty"):
        version_label = "Dirty source"
    elif kind == "archive":
        version = source.get("version") or source.get("engine_revision")
        version_label = version if isinstance(version, str) and version.strip() else "Archive / version unknown"
    elif binaries:
        version_label = "Binary " + version_id[:10]
        kind = "executable"
    else:
        version_label = "Unknown version"
    return {"tool_id": engine, "tool_label": text("implementation.tool_label", engine),
            "way_id": way, "way_label": text("implementation.way_label", way),
            "version_id": version_id, "version_label": text("implementation.version_label", version_label),
            "revision": revision, "repository": _repository(source) if revision else "", "source_kind": kind,
            "source_binding": source_binding, "source_identity": source_identity, "executables": copy.deepcopy(binaries)}
