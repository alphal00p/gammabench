"""Create a runnable starter and register it without replacing existing work."""
from __future__ import annotations

from importlib.resources import files
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .registry import checkout_path as _managed, RESERVED, plan_benchmark, registry_lock, repository_root, write_registry
from .schema import validate_suite


_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError(f"{name} must be nonempty text without control characters")
    return value


def _suite(benchmark_id: str, description: str) -> dict:
    contract = "exact-vector-transform-v1"
    return {
        "schema_version": 1,
        "id": benchmark_id,
        "description": description,
        "contracts": {contract: {
            "description": "Starter operation: retain every transformed u64 in the exact expected sequence.",
            "included": ["Transform every value of an existing Vec<u64> with value * 3 + 1", "Retain the transformed vector"],
            "excluded": ["Read and parse the workload", "Allocate and fill the input vector", "Validate every output element and compute the witness", "Print results and destroy the vector", "Process startup"],
        }},
        "metrics": {"compute_ns": {
            "unit": "ns", "direction": "lower", "scope": "Transform an existing Vec<u64> through the retained transformed vector",
            "result_field": "compute_ns",
        }},
        "tools": {"driver": {
            "kind": "executable", "path": f"./{benchmark_id}", "version": f"gammabench-{benchmark_id} starter 1",
            "version_command": ["{tool}", "--version"],
        }},
        "tasks": [{
            "id": "transform", "case": "100000-u64", "engine": benchmark_id, "variant": "baseline", "contract": contract,
            "tool_ids": ["driver"], "inputs": {"workload.txt": "workload.txt"},
            "expected": {"items": 100000, "total": 14999950000}, "equivalence_fields": ["validated_output"],
            "argv": ["{tool:driver}"],
        }],
    }


def _json(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def create(root: Path, benchmark_id: str, *, family: str, title: str | None = None,
           description: str | None = None, family_title: str | None = None) -> dict:
    """Scaffold a Rust starter, Nix recipe and planned catalog entry in a checkout.

    Validate the complete plan first. New paths never replace existing paths;
    the catalog is atomically replaced only after installing both new outputs.
    Any ordinary failure before registration removes the new outputs again.
    """
    if not isinstance(benchmark_id, str) or not _IDENTIFIER.fullmatch(benchmark_id):
        raise ValueError("benchmark ID must be lowercase words separated by hyphens")
    if benchmark_id in RESERVED or benchmark_id.startswith("bench-"):
        raise ValueError(f"benchmark ID conflicts with a built-in Nix output: {benchmark_id}")
    root = repository_root(root)
    title = _text(title or benchmark_id.replace("-", " ").capitalize(), "title")
    description = _text(description or "Runnable Rust starter: replace the vector transform, contract and exact checks with the operation being measured.", "description")
    if family_title is not None:
        _text(family_title, "family title")
    suite_relative = f"suites/{benchmark_id}/suite.json"
    recipe_relative = f"nix/{benchmark_id}.nix"
    registry = _managed(root, "web/benchmarks.json")
    suite_directory = _managed(root, f"suites/{benchmark_id}")
    recipe = _managed(root, recipe_relative)
    state = _managed(root, ".gammabench")
    for path in (suite_directory, recipe):
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"scaffold output already exists: {path}")
    with registry_lock(root):
        original = registry.read_bytes()
        catalog = plan_benchmark(json.loads(original), benchmark_id, family=family, title=title,
                                 description=description, family_title=family_title,
                                 nix_recipe=recipe_relative, suite=suite_relative,
                                 default_metric="compute_ns", default_case="100000-u64")
        suite = _suite(benchmark_id, description)
        validate_suite(suite)
        template_root = files("gammabench").joinpath("templates/new-benchmark")
        substitutions = {"@@BENCHMARK_ID@@": benchmark_id, "@@TITLE@@": title, "@@FAMILY@@": family}

        def template(name: str) -> bytes:
            value = template_root.joinpath(name).read_text(encoding="utf-8")
            for key, replacement in substitutions.items():
                value = value.replace(key, replacement)
            return value.encode("utf-8")

        suite_installed = recipe_installed = False
        with tempfile.TemporaryDirectory(prefix="new-benchmark-", dir=state) as temporary:
            staging = Path(temporary)
            staged_suite = staging / "suite"
            staged_suite.mkdir()
            for name in ("main.rs", "README.md"):
                (staged_suite / name).write_bytes(template(name))
            (staged_suite / "suite.json").write_bytes(_json(suite))
            (staged_suite / "workload.txt").write_text("100000\n", encoding="utf-8")
            staged_recipe = staging / "recipe.nix"
            staged_recipe.write_bytes(template("package.nix"))
            try:
                # Recheck under the lock: preflight can race another invocation.
                for path in (suite_directory, recipe):
                    if path.exists() or path.is_symlink():
                        raise FileExistsError(f"scaffold output already exists: {path}")
                if registry.read_bytes() != original:
                    raise ValueError("benchmark catalog changed during scaffolding; retry with the current catalog")
                suite_directory.parent.mkdir(exist_ok=True)
                # mkdir is exclusive, unlike renaming onto an empty user directory.
                suite_directory.mkdir()
                suite_installed = True
                for child in staged_suite.iterdir():
                    os.link(child, suite_directory / child.name)
                os.link(staged_recipe, recipe)
                recipe_installed = True
                if registry.read_bytes() != original:
                    raise ValueError("benchmark catalog changed during scaffolding; retry with the current catalog")
                write_registry(root, catalog, original)
            except BaseException:
                if recipe_installed:
                    recipe.unlink()
                if suite_installed:
                    shutil.rmtree(suite_directory)
                raise
    return {"status": "created", "benchmark": benchmark_id, "suite": str(root / suite_relative),
            "nix_recipe": str(recipe), "registry": str(registry)}
