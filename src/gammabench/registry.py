"""Register benchmark definitions and retain portable report evidence."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .schema import SchemaError, validate_suite
from .metadata import capture_metadata, field_definitions, metadata_values, same_value, semantic_metadata, validate_metadata
from .snapshot import load, verify

REGISTRY_PATH = Path("web/benchmarks.json")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RESERVED = {"default", "gammabench", "hyperfine", "nodejs", "integer-sum", "rust-sort", "qgraf", "nauty",
            "generation-count", "generation-materialized", "generation-signed-routed", "generation-full-public"}


def repository_root(start: str | Path | None = None) -> Path:
    start = Path(start or Path.cwd()).resolve()
    for path in (start, *start.parents):
        if (path / "flake.nix").is_file() and (path / REGISTRY_PATH).is_file():
            return path
    raise SchemaError("No gammabench checkout found; pass --repo PATH")



def checkout_path(root: Path, relative: str) -> Path:
    """Keep managed publication and scaffold paths out of symlinked directories."""
    path = root
    for part in Path(relative).parts:
        if part in ("", ".", "..") or Path(relative).is_absolute():
            raise SchemaError("Managed paths must be checkout-relative without traversal")
        path /= part
        if path.is_symlink():
            raise SchemaError(f"Managed paths must not contain symlinks: {path}")
    return path


def _path(value: str, prefix: str, suffix: str) -> bool:
    return (isinstance(value, str) and value.startswith(prefix) and value.endswith(suffix)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value) is not None
            and all(part not in ("", ".", "..") for part in value.split("/")))


def validate_registry(registry: dict) -> dict:
    if not isinstance(registry, dict) or type(registry.get("schema_version")) is not int or registry["schema_version"] != 1:
        raise SchemaError("Unsupported benchmark registry schema")
    if not isinstance(registry.get("families"), list) or not isinstance(registry.get("benchmarks"), list):
        raise SchemaError("Registry families and benchmarks must be arrays")
    families = set()
    for family in registry["families"]:
        if not isinstance(family, dict) or not isinstance(family.get("id"), str) or not SLUG.fullmatch(family["id"]):
            raise SchemaError("Invalid benchmark family ID")
        if family["id"] in families or not isinstance(family.get("title"), str) or not family["title"].strip():
            raise SchemaError("Duplicate family ID or missing family title")
        families.add(family["id"])
    ids = set()
    for item in registry["benchmarks"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not SLUG.fullmatch(item["id"]):
            raise SchemaError("Invalid benchmark ID")
        if item["id"] in ids or not isinstance(item.get("family"), str) or item["family"] not in families:
            raise SchemaError("Duplicate benchmark ID or unknown benchmark family")
        ids.add(item["id"])
        for field in ("title", "description", "definition_version", "default_metric"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise SchemaError(f"Benchmark requires {field}")
        analyses, reports = item.get("analyses"), item.get("reports")
        if not isinstance(analyses, list) or not isinstance(reports, list) or len(analyses) != len(reports):
            raise SchemaError("Every registered analysis requires its report")
        if any(not isinstance(path, str) for path in analyses + reports):
            raise SchemaError("Registered report paths must be strings")
        if len(set(analyses)) != len(analyses):
            raise SchemaError("An analysis cannot be registered twice")
        for analysis, report in zip(analyses, reports):
            if not _path(analysis, "docs/results/", "/analysis.json") or report != analysis.removesuffix("analysis.json") + "index.html":
                raise SchemaError("Reports and analysis must be siblings under docs/results")
        if "metadata" in item:
            if isinstance(item["metadata"], dict) and "definitions" in item["metadata"]:
                raise SchemaError("Catalog metadata cannot override the controlled field registry with frozen definitions")
            validate_metadata(item["metadata"])
        if "suite" in item and not _path(item["suite"], "suites/", ".json"):
            raise SchemaError("Benchmark suite must be a relative suites/*.json path")
        if "nix_recipe" in item:
            if not _path(item["nix_recipe"], "nix/", ".nix") or not _path(item.get("suite"), "suites/", ".json"):
                raise SchemaError("A Nix benchmark requires relative nix recipe and suite paths")
    default = registry.get("default_benchmark")
    if (not isinstance(default, str) or default not in ids) and (ids or default is not None):
        raise SchemaError("Default benchmark is missing")
    if not isinstance(registry.get("subjects", {}), dict):
        raise SchemaError("Registry subjects must be an object")
    return registry


def plan_benchmark(registry: dict, benchmark_id: str, *, family: str, title: str,
                   description: str, family_title: str | None = None,
                   nix_recipe: str | None = None, suite: str | None = None,
                   default_metric: str = "compute_ns", default_case: str = "default") -> dict:
    result = copy.deepcopy(validate_registry(registry))
    result.setdefault("subjects", {})
    if not isinstance(benchmark_id, str) or not SLUG.fullmatch(benchmark_id):
        raise SchemaError("Benchmark ID must be a lowercase URL slug")
    if benchmark_id in RESERVED or benchmark_id.startswith("bench-"):
        raise SchemaError("Benchmark ID collides with an existing Nix output")
    if any(item["id"] == benchmark_id for item in result["benchmarks"]):
        raise SchemaError("Benchmark is already registered")
    if not isinstance(family, str) or not SLUG.fullmatch(family):
        raise SchemaError("Family ID must be a lowercase URL slug")
    existing = next((item for item in result["families"] if item["id"] == family), None)
    if existing is None:
        result["families"].append({"id": family, "title": family_title or family.replace("-", " ").title(), "status": "planned"})
    elif family_title is not None and family_title != existing["title"]:
        raise SchemaError("An existing family already has a different title")
    entry = {"id": benchmark_id, "family": family, "title": title, "description": description,
             "definition_version": "1", "analyses": [], "reports": [],
             "default_metric": default_metric, "default_case": default_case}
    if suite is not None:
        entry["suite"] = suite
    if nix_recipe is not None:
        entry["nix_recipe"] = nix_recipe
    result["benchmarks"].append(entry)
    if result.get("default_benchmark") is None:
        result["default_benchmark"] = benchmark_id
    return validate_registry(result)



@contextmanager
def registry_lock(root: Path):
    """Serialize scaffolding and report publication in the same checkout."""
    state = root / ".gammabench"
    if state.is_symlink():
        raise SchemaError("Benchmark state must remain inside the checkout")
    state.mkdir(exist_ok=True)
    lock = state / "new-benchmark.lock"
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise SchemaError(f"Another benchmark catalog operation is active; if it stopped, remove the stale lock: {lock}") from error
    identity = lock.stat().st_ino
    try:
        yield
    finally:
        if lock.exists() and not lock.is_symlink() and lock.stat().st_ino == identity:
            lock.rmdir()


def write_registry(root: Path, registry: dict, expected: bytes) -> None:
    """Replace only the version inspected by this operation."""
    path = root / REGISTRY_PATH
    validate_registry(registry)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise SchemaError("Benchmark registry must remain inside the checkout")
    encoded = (json.dumps(registry, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(prefix=".benchmarks-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != expected:
            raise SchemaError("Benchmark registry changed during this operation; retry")
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _register_report(snapshot_paths, output: str | Path, benchmark_id: str, *,
                    root: str | Path | None = None, baseline: str | None = None) -> dict:
    """Retain sealed snapshots, render a report, then register it atomically."""
    from .report import analyze, render

    root = repository_root(root)
    catalog_path = root / REGISTRY_PATH
    original = catalog_path.read_bytes()
    registry = validate_registry(json.loads(original))
    entry = next((item for item in registry["benchmarks"] if item["id"] == benchmark_id), None)
    if entry is None:
        raise SchemaError("Unknown benchmark; create it with new-benchmark first")
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"report destination already exists: {output}")
    output = output.resolve()
    reports_root = checkout_path(root, "docs/results").resolve()
    if not reports_root.is_relative_to(root) or output == reports_root or not output.is_relative_to(reports_root):
        raise SchemaError("Registered reports must be under the checkout's docs/results directory")
    relative = output.relative_to(root).as_posix()
    if not _path(relative + "/analysis.json", "docs/results/", "/analysis.json"):
        raise SchemaError("Registered report path must use safe relative path segments")
    if isinstance(snapshot_paths, (str, os.PathLike)):
        snapshot_paths = [snapshot_paths]
    paths = [Path(path).resolve() for path in snapshot_paths]
    analysis = analyze(paths, baseline=baseline)
    if entry.get("suite"):
        definition = validate_suite(json.loads(checkout_path(root, entry["suite"]).read_text()))
        expected_suite = definition["id"]
        expected_definition = definition
    elif entry["analyses"]:
        previous = json.loads(checkout_path(root, entry["analyses"][0]).read_text())
        expected_suite = previous["snapshots"][0]["suite_id"]
        expected_definition = previous["snapshots"][0]
    else:
        raise SchemaError("Benchmark has no suite definition")
    if any(snapshot["suite_id"] != expected_suite for snapshot in analysis["snapshots"]):
        raise SchemaError("Snapshot suite ID does not match the registered benchmark")
    if any(snapshot[key] != expected_definition[key]
           for snapshot in analysis["snapshots"] for key in ("contracts", "metrics")):
        raise SchemaError("Capture contracts or metric definitions changed; register a new benchmark ID")
    expected_metadata = (capture_metadata(expected_definition) if "tasks" in expected_definition else
                         expected_definition.get("metadata"))
    for item in analysis["series"]:
        snapshot = next(snapshot for snapshot in analysis["snapshots"] if snapshot["id"] == item["snapshot_id"])
        expected_task = item["task"]
        receipt = snapshot.get("provenance", {}).get("nix_build")
        if isinstance(receipt, dict):
            from .history_context import _selection
            selected = _selection(receipt, {"id": item["task"], "variant": item["variant"]})
            if selected is not None:
                expected_task = selected[3]
        expected_values = metadata_values(expected_metadata, case=item["case"], task=expected_task, metric=item["metric"])
        actual_values = metadata_values(snapshot.get("metadata"), case=item["case"], task=item["task"], metric=item["metric"])
        if semantic_metadata(expected_values, field_definitions(expected_metadata)) != semantic_metadata(actual_values, field_definitions(snapshot.get("metadata"))):
            raise SchemaError("Capture semantic metadata changed; register a new benchmark ID")
        overlay = metadata_values(entry.get("metadata"), case=item["case"], task=item["task"], metric=item["metric"])
        overlay_definitions = field_definitions(entry.get("metadata"))
        if any(key in actual_values and actual_values[key] is not None and value is not None
               and not same_value(key, actual_values[key], value, overlay_definitions)
               for key, value in overlay.items()):
            raise SchemaError("Catalog metadata conflicts with captured declarations")
    if not any(item["metric"] == entry["default_metric"] for item in analysis["series"]):
        raise SchemaError("Registered default metric is absent from this capture")
    if not entry["analyses"]:
        anchor = analysis["snapshots"][0]["id"]
        for other in registry["benchmarks"]:
            if other["id"] != benchmark_id and other["analyses"]:
                previous = json.loads(checkout_path(root, other["analyses"][0]).read_text())
                if previous["snapshots"][0]["id"] == anchor:
                    raise SchemaError("This capture already anchors another benchmark")
    known = set()
    for path in entry["analyses"]:
        registered = json.loads(checkout_path(root, path).read_text())
        known.update(snapshot["id"] for snapshot in registered["snapshots"])
    if known & {snapshot["id"] for snapshot in analysis["snapshots"]}:
        raise SchemaError("A snapshot is already registered for this benchmark")
    retained = []
    created = []
    stage = None
    published = False
    try:
        for source, snapshot in zip(paths, analysis["snapshots"]):
            inventory = verify(source)
            snapshots_root = checkout_path(root, "snapshots").resolve()
            if not snapshots_root.is_relative_to(root):
                raise SchemaError("Published snapshots must remain inside the checkout")
            if source.is_relative_to(snapshots_root):
                destination = source
            else:
                destination = checkout_path(root, "snapshots/measured/" + snapshot["id"])
                if not destination.resolve().is_relative_to(snapshots_root) or destination.is_symlink():
                    raise SchemaError("Snapshot archive path escapes the retained evidence")
                if destination.exists():
                    load(destination)
                    if verify(destination) != inventory:
                        raise SchemaError("Retained snapshot ID already has different evidence")
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.mkdir()
                    created.append(destination)
                    shutil.copytree(source, destination, dirs_exist_ok=True)
                    load(destination)
                    if verify(destination) != inventory or verify(source) != inventory:
                        raise SchemaError("Snapshot changed while archiving")
            retained.append(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".report-", dir=output.parent))
        stage.rmdir()
        render(retained, stage, baseline=baseline)
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"report destination appeared during rendering: {output}")
        output.mkdir()
        published = True
        for child in stage.iterdir():
            os.rename(child, output / child.name)
        stage.rmdir()
        stage = None
        entry["analyses"].append(relative + "/analysis.json")
        entry["reports"].append(relative + "/index.html")
        for family in registry["families"]:
            if family["id"] == entry["family"]:
                family["status"] = "captured"
        write_registry(root, registry, original)
    except BaseException:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
        if published:
            shutil.rmtree(output)
        for path in reversed(created):
            shutil.rmtree(path)
        raise
    return {"output": str(output), "benchmark": benchmark_id, "status": "registered"}


def register_report(snapshot_paths, output: str | Path, benchmark_id: str, *,
                    root: str | Path | None = None, baseline: str | None = None) -> dict:
    root = repository_root(root)
    with registry_lock(root):
        return _register_report(snapshot_paths, output, benchmark_id, root=root, baseline=baseline)
