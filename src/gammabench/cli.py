"""Command-line entry points for versioned suites and result snapshots."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .schema import validate_suite
from .snapshot import load, verify


def _assignments(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected NAME=VALUE: {value}")
        key, item = value.split("=", 1)
        if not key or key in result:
            raise ValueError(f"empty or duplicate assignment: {key}")
        result[key] = item
    return result


def _pins(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--override", action="append", default=[], metavar="TOOL=PATH")
    parser.add_argument("--allow-dirty", action="store_true", help="bind dirty checkouts to an explicit content snapshot")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gammabench", description="Versioned benchmark suites and sealed evidence snapshots")
    parser.add_argument("--version", action="version", version=f"gammabench {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate", help="validate a suite without executing commands")
    validate.add_argument("suite", type=Path)
    execute = subcommands.add_parser("run", help="run fresh paired processes and seal evidence")
    execute.add_argument("suite", type=Path)
    execute.add_argument("destination", type=Path)
    execute.add_argument("--repeats", type=int, default=15)
    execute.add_argument("--warmups", type=int, default=1)
    execute.add_argument("--task", action="append", dest="selected_tasks")
    execute.add_argument("--timeout", type=float, default=60)
    execute.add_argument("--cpu", type=int, action="append", dest="cpus")
    execute.add_argument("--env", action="append", default=[], metavar="NAME=VALUE", help="explicit override captured in evidence")
    execute.add_argument("--label", help="stable snapshot identifier")
    execute.add_argument("--backend", choices=("hyperfine", "python"), default="hyperfine",
                         help="process measurement backend (default: pinned Hyperfine 2.0.0)")
    execute.add_argument("--hyperfine", type=Path, help="Hyperfine 2.0.0 executable")
    _pins(execute)
    variants = subcommands.add_parser("variants", help="assemble realized package revisions for paired measurement")
    variants.add_argument("destination", type=Path)
    variants.add_argument("--variant", action="append", required=True, metavar="LABEL=PACKAGE")
    variants.add_argument("--task", action="append", dest="selected_tasks", help="original task ID, shared by all packages")
    scaffold = subcommands.add_parser("new-benchmark", help="scaffold a registered Rust benchmark and Nix package")
    scaffold.add_argument("benchmark_id")
    scaffold.add_argument("--family", required=True)
    scaffold.add_argument("--title")
    scaffold.add_argument("--description")
    scaffold.add_argument("--family-title")
    scaffold.add_argument("--repo", type=Path, default=Path.cwd())
    catalog = subcommands.add_parser("catalog", help="validate and project searchable benchmark metadata")
    catalog.add_argument("--repo", type=Path, default=Path.cwd())
    catalog.add_argument("--output", type=Path)
    catalog.add_argument("--include-workspaces", action="store_true", help="include presentation projections for the Pages build")
    integrity = subcommands.add_parser("verify", help="verify every file in a relocated snapshot")
    integrity.add_argument("snapshot", type=Path)
    for command in ("report", "compare"):
        report = subcommands.add_parser(command, help="analyze only equivalent, qualified contracts")
        report.add_argument("snapshots", nargs="+", type=Path)
        report.add_argument("--baseline", help="snapshot ID, variant label, or engine:NAME")
        report.add_argument("--output", type=Path, required=command == "report")
        if command == "report":
            report.add_argument("--register", metavar="BENCHMARK", help="retain evidence and register the report in the benchmark catalog")
            report.add_argument("--repo", type=Path, help="gammabench checkout for report registration")
    sources = subcommands.add_parser("sources", help="resolve selected exact source and tool pins")
    sources.add_argument("lockfile", type=Path)
    sources.add_argument("--tool", action="append", dest="selected_tools")
    _pins(sources)
    history = subcommands.add_parser("import-history", help="preserve audited historical evidence without inventing qualification")
    history.add_argument("source", type=Path)
    history.add_argument("destination", type=Path)
    history.add_argument("--label")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "validate":
            suite = validate_suite(json.loads(arguments.suite.read_text(encoding="utf-8")))
            print(json.dumps({"suite_id": suite["id"], "status": "valid", "tasks": len(suite["tasks"])}))
        elif arguments.command == "run":
            from .runner import run
            path = run(arguments.suite, arguments.destination, repeats=arguments.repeats, warmups=arguments.warmups,
                       selected_tasks=arguments.selected_tasks, cache_dir=arguments.cache_dir,
                       overrides=_assignments(arguments.override), allow_dirty=arguments.allow_dirty,
                       environment=_assignments(arguments.env), cpus=arguments.cpus,
                       timeout=arguments.timeout, label=arguments.label,
                       backend=arguments.backend, hyperfine=arguments.hyperfine)
            snapshot = load(path)
            print(json.dumps({"snapshot": str(path), "status": snapshot["status"], "id": snapshot["id"]}))
            return 0 if snapshot["status"] == "completed" else 1
        elif arguments.command == "variants":
            from .variants import assemble
            path = assemble(arguments.destination, _assignments(arguments.variant),
                            selected_tasks=arguments.selected_tasks)
            print(json.dumps({"suite": str(path / "suite.json"), "status": "assembled"}))
        elif arguments.command == "new-benchmark":
            from .scaffold import create
            print(json.dumps(create(arguments.repo, arguments.benchmark_id, family=arguments.family,
                                    title=arguments.title, description=arguments.description,
                                    family_title=arguments.family_title)))
        elif arguments.command == "catalog":
            from .catalog import build_catalog
            result = build_catalog(arguments.repo, include_workspaces=arguments.include_workspaces)
            encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
            if arguments.output:
                arguments.output.parent.mkdir(parents=True, exist_ok=True)
                with arguments.output.open("x", encoding="utf-8") as stream:
                    stream.write(encoded)
            else:
                print(encoded, end="")
        elif arguments.command == "verify":
            inventory = verify(arguments.snapshot)
            snapshot = load(arguments.snapshot)
            print(json.dumps({"snapshot_id": snapshot["id"], "status": "verified", "files": len(inventory)}))
        elif arguments.command == "report":
            if arguments.register:
                from .registry import register_report
                print(json.dumps(register_report(arguments.snapshots, arguments.output, arguments.register,
                                                 root=arguments.repo, baseline=arguments.baseline)))
            else:
                if arguments.repo is not None:
                    raise ValueError("--repo requires --register")
                from .report import render
                render(arguments.snapshots, arguments.output, baseline=arguments.baseline)
                print(json.dumps({"output": str(arguments.output), "status": "rendered"}))
        elif arguments.command == "compare":
            from .report import analyze
            output = None
            if arguments.output:
                if arguments.output.exists() or arguments.output.is_symlink():
                    raise FileExistsError(f"comparison output already exists: {arguments.output}")
                output = arguments.output.resolve()
                if any(output.is_relative_to(path.resolve()) for path in arguments.snapshots):
                    raise ValueError("comparison output must be outside input snapshots")
            result = analyze(arguments.snapshots, baseline=arguments.baseline)
            encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
            if output:
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("x", encoding="utf-8") as handle:
                    handle.write(encoded)
            else:
                print(encoded, end="")
        elif arguments.command == "sources":
            from .sources import resolve_tools
            value = json.loads(arguments.lockfile.read_text(encoding="utf-8"))
            tools = value.get("tools", value)
            result = resolve_tools(tools, arguments.selected_tools or list(tools),
                                   cache_dir=arguments.cache_dir or Path.cwd() / ".gammabench" / "tools",
                                   overrides=_assignments(arguments.override), allow_dirty=arguments.allow_dirty,
                                   base_dir=arguments.lockfile.resolve().parent,
                                   repo_dir=Path(__file__).resolve().parents[2])
            print(json.dumps({"schema_version": 1, "tools": result}, indent=2))
        elif arguments.command == "import-history":
            from .history import import_snapshot
            path = import_snapshot(arguments.source, arguments.destination, label=arguments.label)
            print(json.dumps({"snapshot": str(path), "status": "imported"}))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(f"gammabench: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
