"""Pinned Hyperfine execution and strict ingestion of its per-run JSON evidence."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shlex
import shutil
import subprocess

from .schema import SchemaError
from .snapshot import sha256

VERSION = "2.0.0"
JSON_SCHEMA = 2
RESERVED_ENVIRONMENT = {"HYPERFINE_ITERATION", "HYPERFINE_RANDOMIZED_ENVIRONMENT_OFFSET"}


def resolve(executable: str | Path | None = None) -> dict:
    """Check the measurement tool before creating an unfinished snapshot."""
    found = shutil.which(str(executable) if executable is not None else "hyperfine")
    if found is None:
        raise ValueError("Hyperfine 2.0.0 is required; use the Nix benchmark app or --hyperfine PATH")
    path = Path(found).resolve()
    try:
        version = subprocess.run([str(path), "--version"], capture_output=True, text=True,
                                 timeout=10, check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError("Hyperfine version check timed out") from error
    if version.returncode != 0 or version.stdout.strip() != f"hyperfine {VERSION}":
        raise ValueError("Hyperfine 2.0.0 is required; found " +
                         (version.stdout.strip() or version.stderr.strip() or "an unreadable version"))
    return {"name": "hyperfine", "version": VERSION, "path": str(path), "sha256": sha256(path),
            "json_schema_version": JSON_SCHEMA, "shell": "none", "runs_per_invocation": 1}


def invocation(binding: dict, argv: list[str], export: Path) -> list[str]:
    """Keep the scheduled target itself inside Hyperfine's process timer."""
    return [binding["path"], "--shell=none", "--runs", "1", "--warmup", "0",
            "--metrics", "time_wall_clock", "--style", "none", "--output", "inherit",
            "--ignore-failure=all-non-zero", "--export-json", str(export), "--", shlex.join(argv)]


def read_measurement(path: Path, argv: list[str]) -> dict:
    """Read the sole target run; never derive successful values from summaries."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SchemaError(f"Hyperfine JSON is missing or unreadable: {error}") from error
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int or document["schema_version"] != JSON_SCHEMA:
        raise SchemaError("Hyperfine export requires JSON schema_version 2")
    metadata = document.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("hyperfine_version") != VERSION:
        raise SchemaError("Hyperfine export requires version 2.0.0")
    if document.get("primary_metric") != "time_wall_clock":
        raise SchemaError("Hyperfine export primary metric must be time_wall_clock")
    results = document.get("results")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise SchemaError("Hyperfine export must contain exactly one command")
    result = results[0]
    if result.get("command") != shlex.join(argv):
        raise SchemaError("Hyperfine export command does not match the scheduled target")
    measurements = result.get("measurements")
    if not isinstance(measurements, list) or len(measurements) != 1 or not isinstance(measurements[0], dict):
        raise SchemaError("Hyperfine export must contain exactly one measurement")
    measurement = measurements[0]
    wall = measurement.get("time_wall_clock")
    if not isinstance(wall, dict) or wall.get("unit") != "second":
        raise SchemaError("Hyperfine wall time requires unit second")
    value = wall.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SchemaError("Hyperfine wall time must be finite and nonnegative")
    try:
        nanoseconds = value * 1e9
        valid = math.isfinite(value) and value >= 0 and math.isfinite(nanoseconds)
    except OverflowError:
        valid = False
    if not valid:
        raise SchemaError("Hyperfine wall time must be finite and nonnegative")
    exit_code = measurement.get("exit_code")
    if type(exit_code) is not int or exit_code < 0:
        raise SchemaError("Hyperfine measurement requires a nonnegative integer exit_code")
    return {"process_wall_ns": round(nanoseconds), "returncode": exit_code}
