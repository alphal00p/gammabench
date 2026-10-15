"""Assemble realized benchmark packages into one genuinely paired execution suite."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import shutil

from . import __version__
from .nix import load_receipt
from .schema import IDENTIFIER, SHA256, SchemaError, validate_suite
from .snapshot import sha256, write_json

BINDING = re.compile(r"\{(tool|source):([A-Za-z0-9_.-]+)\}")


def _inventory(root: Path) -> dict[str, str]:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SchemaError(f"variant package fixtures must not contain symlinks: {path}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = sha256(path)
        elif not path.is_dir():
            raise SchemaError(f"unsupported variant package fixture: {path}")
    return files


def _rewrite(value, label: str, package: Path):
    """Keep fixture paths relative to the assembled suite so it can be relocated."""
    if isinstance(value, str):
        copied = "{suite_dir}/variants/" + label
        value = value.replace("{suite_dir}", copied).replace(str(package) + "/", copied + "/")
        if value == str(package):
            value = copied
        return BINDING.sub(lambda match: "{" + match[1] + ":" + label + "." + match[2] + "}", value)
    if isinstance(value, list):
        return [_rewrite(item, label, package) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite(item, label, package) for key, item in value.items()}
    return value


def assemble(destination: str | Path, variants: dict[str, str | Path], *,
             selected_tasks: list[str] | None = None) -> Path:
    """Copy compatible packages without building engines or changing their inputs."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"variant destination already exists: {destination}")
    destination = destination.resolve()
    if len(variants) < 2 or any(not isinstance(label, str) or not IDENTIFIER.fullmatch(label)
                              for label in variants):
        raise SchemaError("variants require at least two distinct identifier labels")
    if selected_tasks is not None and (not selected_tasks or len(selected_tasks) != len(set(selected_tasks))):
        raise SchemaError("selected variant tasks must be nonempty and distinct")
    packages = []
    reference = None
    task_ids = None
    for label, path in variants.items():
        root = Path(path).resolve()
        if not root.is_dir() or destination.is_relative_to(root):
            raise SchemaError("variant packages must be directories outside the destination")
        definition = validate_suite(json.loads((root / "suite.json").read_text(encoding="utf-8")))
        if definition.get("archived_only"):
            raise SchemaError("variant packages must be runnable rather than historical imports")
        receipt = load_receipt(definition, root / "suite.json")
        if receipt is None:
            raise SchemaError("variant packages require their Nix build receipt")
        if reference is None:
            reference = definition
        elif any(definition[key] != reference[key] for key in ("id", "contracts", "metrics")):
            raise SchemaError("variant packages require identical suite IDs, contracts and metric definitions")
        available = {task["id"] for task in definition["tasks"]}
        selected = set(selected_tasks) if selected_tasks is not None else available
        if not selected <= available or (task_ids is not None and selected != task_ids):
            raise SchemaError("variant packages must provide the same selected task IDs")
        task_ids = selected
        tasks = [task for task in definition["tasks"] if task["id"] in selected]
        identities = [(task["case"], task["engine"], task["contract"]) for task in tasks]
        if len(identities) != len(set(identities)):
            raise SchemaError("revision labels would collapse implementation variants; select an original task with --task")
        for tool in definition["tools"].values():
            path = Path(tool.get("path", ""))
            if (tool["kind"] != "executable" or not path.is_absolute()
                    or not SHA256.fullmatch(str(tool.get("sha256", ""))) or not path.is_file()):
                raise SchemaError("variant packages require realized absolute executable paths and SHA-256 pins")
            if sha256(path) != tool["sha256"]:
                raise SchemaError("variant package executable does not match its SHA-256 pin")
        packages.append({"label": label, "root": root, "definition": definition,
                         "receipt": receipt, "tasks": tasks, "files": _inventory(root)})
    task_identity = {task["id"]: (task["case"], task["engine"], task["contract"])
                     for task in packages[0]["tasks"]}
    if any({task["id"]: (task["case"], task["engine"], task["contract"])
            for task in package["tasks"]} != task_identity for package in packages[1:]):
        raise SchemaError("selected task case, engine and contract identities must match across variants")
    suite = copy.deepcopy(reference)
    suite.update(tools={}, tasks=[], unsupported=[], qualification_censored=[],
                 nix_build_receipt="nix-build-receipt.json")
    receipt = {"schema_version": 1, "sources": {}, "subjects": {},
               "build": {"assembler": f"gammabench {__version__}", "variants": {}},
               "comparison_context": {"assembly": "packaged-variants-v1", "suite_id": suite["id"],
                                      "selected_tasks": sorted(task_ids)}}
    used_tasks = set()
    for package in packages:
        label, root = package["label"], package["root"]
        original = package["receipt"]
        for section in ("unsupported", "qualification_censored"):
            entries = package["definition"].get(section, [])
            if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                raise SchemaError(f"variant {section} records must be objects in an array")
            for entry in entries:
                entry = copy.deepcopy(entry)
                entry["variant"] = label
                if "task" in entry:
                    if not isinstance(entry["task"], str):
                        raise SchemaError("variant availability task must be an identifier")
                    entry["task"] = label + "." + entry["task"]
                suite[section].append(entry)
        for name, tool in package["definition"]["tools"].items():
            key = label + "." + name
            if key in suite["tools"]:
                raise SchemaError("variant tool namespaces collide")
            suite["tools"][key] = copy.deepcopy(tool)
        for original_task in package["tasks"]:
            task = copy.deepcopy(original_task)
            task.update(id=label + "." + task["id"], variant=label,
                        tool_ids=[label + "." + name for name in task["tool_ids"]],
                        inputs={name: "variants/" + label + "/" + path for name, path in task["inputs"].items()})
            if task["id"] in used_tasks:
                raise SchemaError("variant task namespaces collide")
            used_tasks.add(task["id"])
            for field in ("argv", "validation_command", "environment"):
                if field in task:
                    task[field] = _rewrite(task[field], label, root)
            suite["tasks"].append(task)
        for name, source in original["data"]["sources"].items():
            key = label + "." + name
            if key in receipt["sources"]:
                raise SchemaError("variant source namespaces collide")
            receipt["sources"][key] = source
        receipt["build"]["variants"][label] = {
            "package_path": str(root), "files_sha256": package["files"],
            "receipt_sha256": original["sha256"], "receipt": original["data"],
            "original_task_variants": {task["id"]: task["variant"] for task in package["tasks"]}}
    validate_suite(suite)
    json.dumps(receipt, allow_nan=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    try:
        for package in packages:
            target = destination / "variants" / package["label"]
            target.mkdir(parents=True)
            for path in sorted(package["root"].rglob("*")):
                copied = target / path.relative_to(package["root"])
                if path.is_dir():
                    copied.mkdir(parents=True, exist_ok=True)
                else:
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, copied)
            if _inventory(target) != package["files"] or _inventory(package["root"]) != package["files"]:
                raise SchemaError("variant package changed during assembly")
        write_json(destination / "nix-build-receipt.json", receipt)
        write_json(destination / "suite.json", suite)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination
