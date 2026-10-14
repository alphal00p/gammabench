"""Offline reports from sealed snapshots, with conservative comparison gates."""

from __future__ import annotations

import csv
import hashlib
import html
from importlib import resources
import json
import math
import os
from pathlib import Path
import statistics
from string import Template
from typing import Any
from urllib.parse import quote

from . import __version__
from .snapshot import load


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _stats(values: list[int | float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "minimum": None, "maximum": None}
    return {
        "count": len(values),
        "median": statistics.median(values),
        "minimum": min(values),
        "maximum": max(values),
    }


def _field(value: dict[str, Any], field: str) -> Any:
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(field)
        value = value[part]
    return value


def _validation(snapshot: dict[str, Any], task: str) -> dict[str, Any]:
    return snapshot.get("validation", {}).get("tasks", {}).get(task, {})


def _semantic_values(snapshot: dict[str, Any], task: str, fields: list[str]) -> str | None:
    """Use accepted RESULT values; an expected count alone is not equivalence."""
    if not fields:
        return None
    values = []
    records = [record for record in snapshot.get("records", [])
               if record.get("task") == task and record.get("status") == "completed"]
    validation = [record for record in records if record.get("phase") == "validation"]
    for record in validation or records:
        result = record.get("result")
        if not isinstance(result, dict):
            continue
        try:
            values.append(_canonical({field: _field(result, field) for field in fields}))
        except KeyError:
            return None
    if values and len(set(values)) == 1:
        return values[0]
    return None


def _pair_gate(left: dict[str, Any], right: dict[str, Any], snapshots: dict[str, Any]) -> str | None:
    a, b = snapshots[left["snapshot_id"]], snapshots[right["snapshot_id"]]
    va, vb = _validation(a, left["task"]), _validation(b, right["task"])
    if va.get("status") != "passed" or vb.get("status") != "passed":
        return "One or both tasks lack passed semantic validation."
    if left["snapshot_id"] == right["snapshot_id"]:
        for gate in a.get("validation", {}).get("equivalence", []):
            if (
                gate.get("status") == "passed"
                and gate.get("case") == left["case"]
                and gate.get("contract") == left["contract"]
                and gate.get("fields")
                and {left["task"], right["task"]}.issubset(set(gate.get("tasks", [])))
            ):
                return None
    fields_a = va.get("equivalence_fields", [])
    fields_b = vb.get("equivalence_fields", [])
    if fields_a and sorted(fields_a) == sorted(fields_b):
        values_a = _semantic_values(a, left["task"], sorted(fields_a))
        values_b = _semantic_values(b, right["task"], sorted(fields_b))
        if values_a is not None and values_a == values_b:
            return None
        if values_a is not None and values_b is not None:
            return "Accepted RESULT equivalence fields differ."
    return "No exact semantic equivalence gate covers this pair."


def _workload(snapshot: dict[str, Any], case: str, records: list[dict[str, Any]]) -> dict[str, Any] | None:
    witness = snapshot.get("provenance", {}).get("workloads", {}).get(case, {})
    digest = witness.get("sha256") if isinstance(witness, dict) else None
    if isinstance(digest, str) and len(digest) == 64 and all(char in "0123456789abcdef" for char in digest):
        return {"kind": "canonical-workload", "sha256": digest}
    if not records:
        return None
    field = ("workload_inputs_sha256" if any("workload_inputs_sha256" in record for record in records)
             else "inputs_sha256")
    inputs = []
    for record in records:
        selected = record.get(field)
        full = record.get("inputs_sha256")
        if not isinstance(selected, dict) or not selected or not isinstance(full, dict):
            return None
        if any(full.get(name) != digest for name, digest in selected.items()):
            return None
        if any(not isinstance(digest, str) or len(digest) != 64
               or any(char not in "0123456789abcdef" for char in digest) for digest in selected.values()):
            return None
        inputs.append(_canonical(selected))
    if len(set(inputs)) == 1:
        return {"kind": "inputs", "sha256": records[0][field]}
    return None


def _series(snapshot: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in snapshot.get("records", []):
        grouped.setdefault(record["task"], []).append(record)
    definitions = {task["id"]: task for task in tasks}
    selected = snapshot.get("schedule", {}).get("selected_tasks", list(grouped))
    for task in selected:
        if task in definitions:
            grouped.setdefault(task, [])
    result = []
    for task, records in grouped.items():
        identity = records[0] if records else definitions[task]
        if any(any(record[key] != identity[key] for key in ("case", "engine", "variant", "contract"))
               for record in records):
            raise ValueError(f"Inconsistent record identity for task: {task}")
        measured = [record for record in records if record.get("phase") == "measure"]
        status_counts: dict[str, int] = {}
        for record in measured:
            status = record.get("status", "invalid")
            status_counts[status] = status_counts.get(status, 0) + 1
        metrics = snapshot.get("metrics", {})
        if snapshot.get("origin") == "historical-import":
            observed = {metric for record in measured if record.get("status") == "completed"
                        for metric in record.get("metrics", {})}
            if observed:
                metrics = {metric: definition for metric, definition in metrics.items() if metric in observed}
        workload = _workload(snapshot, identity["case"], records)
        for metric, definition in metrics.items():
            samples = []
            missing = 0
            for record in measured:
                value = record.get("metrics", {}).get(metric)
                if record.get("status") == "completed" and _numeric(value):
                    samples.append({"repeat": record.get("repeat"), "value": value})
                elif record.get("status") == "completed":
                    missing += 1
            result.append({
                "id": f"{snapshot['id']}:{task}:{metric}",
                "snapshot_id": snapshot["id"],
                "suite_id": snapshot["suite_id"],
                "task": task,
                "case": identity["case"],
                "engine": identity["engine"],
                "variant": identity["variant"],
                "contract": identity["contract"],
                "metric": metric,
                "unit": definition["unit"],
                "scope": definition["scope"],
                "direction": definition["direction"],
                "origin": snapshot.get("origin", "unknown"),
                "workload": workload,
                "validation_status": _validation(snapshot, task).get("status", "unqualified"),
                "validation_reason": _validation(snapshot, task).get("reason", ""),
                "warmups": sum(record.get("phase") == "warmup" for record in records),
                "measurement_records": len(measured),
                "statuses": status_counts,
                "missing_values": missing,
                "samples": samples,
                **_stats([sample["value"] for sample in samples]),
            })
    return result


def _rounds(series: dict[str, Any], snapshot: dict[str, Any]) -> tuple[dict[int, Any], list[str]]:
    errors = []
    rounds = {}
    for sample in series["samples"]:
        repeat = sample["repeat"]
        if not isinstance(repeat, int) or isinstance(repeat, bool) or repeat < 0:
            errors.append("Sample rounds are not nonnegative integers.")
        elif repeat in rounds:
            errors.append("Duplicate measurement rounds.")
        else:
            rounds[repeat] = sample["value"]
    if any(status != "completed" and count for status, count in series["statuses"].items()):
        errors.append("Failed, invalid or timeout measurements make the pair incomplete.")
    if series["missing_values"]:
        errors.append("A completed measurement is missing this metric.")
    schedule = snapshot.get("schedule", {})
    expected = schedule.get("task_repeats", {}).get(series["task"], schedule.get("repeats"))
    if isinstance(expected, int) and not isinstance(expected, bool) and set(rounds) != set(range(expected)):
        errors.append("Measurement rounds do not cover the scheduled repeats.")
    if not rounds:
        errors.append("No completed measurement rounds.")
    return rounds, errors


def _compare(left: dict[str, Any], right: dict[str, Any], snapshots: dict[str, Any]) -> dict[str, Any]:
    a, b = snapshots[left["snapshot_id"]], snapshots[right["snapshot_id"]]
    reasons = []
    if left["suite_id"] != right["suite_id"]:
        reasons.append("Suite identities differ.")
    if left["contract"] != right["contract"]:
        reasons.append("Output contracts differ.")
    elif _canonical(a.get("contracts", {}).get(left["contract"])) != _canonical(b.get("contracts", {}).get(right["contract"])):
        reasons.append("Contract definitions differ despite an identical contract id.")
    if any(left[key] != right[key] for key in ("unit", "scope", "direction")):
        reasons.append("Metric unit, scope or direction differs.")
    if left["workload"] is None or right["workload"] is None:
        reasons.append("Immutable workload input identity is missing or inconsistent.")
    elif _canonical(left["workload"]) != _canonical(right["workload"]):
        reasons.append("Workload input digests differ despite an identical case id.")
    gate_reason = _pair_gate(left, right, snapshots)
    if gate_reason:
        reasons.append(gate_reason)
    rounds_a, errors_a = _rounds(left, a)
    rounds_b, errors_b = _rounds(right, b)
    reasons.extend(errors_a + errors_b)
    if left["snapshot_id"] != right["snapshot_id"]:
        pairing_a = a.get("schedule", {}).get("pairing_id")
        pairing_b = b.get("schedule", {}).get("pairing_id")
        if not pairing_a or pairing_a != pairing_b:
            reasons.append("Independent snapshots do not establish paired rounds; a shared pairing_id is required.")
    if set(rounds_a) != set(rounds_b):
        reasons.append("Measurement rounds do not match; no subset is ranked.")
    if any(value <= 0 for value in list(rounds_a.values()) + list(rounds_b.values())):
        reasons.append("Ratios require strictly positive measurements.")
    host_a, host_b = a.get("host", {}), b.get("host", {})
    host_differences = sorted(key for key in set(host_a) | set(host_b) if host_a.get(key) != host_b.get(key))
    pair = {
        "baseline": left["id"], "candidate": right["id"],
        "case": left["case"], "metric": left["metric"],
        "unit": left["unit"], "scope": left["scope"],
        "baseline_contract": left["contract"], "candidate_contract": right["contract"],
        "status": "suppressed" if reasons else "comparable",
        "reasons": list(dict.fromkeys(reasons)),
        "host_differences": host_differences,
        "historical": left["origin"] == "historical-import" or right["origin"] == "historical-import",
        "ratio_definition": "candidate / baseline",
        "pairs": [], "ratio": _stats([]), "speedup": _stats([]),
    }
    if reasons:
        return pair
    pairs = [
        {"repeat": repeat, "baseline": rounds_a[repeat], "candidate": rounds_b[repeat],
         "ratio": rounds_b[repeat] / rounds_a[repeat],
         "speedup": (rounds_a[repeat] / rounds_b[repeat] if left["direction"] == "lower"
                     else rounds_b[repeat] / rounds_a[repeat])}
        for repeat in sorted(rounds_a)
    ]
    pair["pairs"] = pairs
    pair["ratio"] = _stats([sample["ratio"] for sample in pairs])
    pair["speedup"] = _stats([sample["speedup"] for sample in pairs])
    return pair


def analyze(snapshot_paths: Any, *, baseline: str | None = None) -> dict[str, Any]:
    """Summarize verified snapshots; never rank incomplete or unequal work.

    ``baseline`` selects a variant label, snapshot id, or ``engine:NAME``.
    Without it, both directions of each pair are calculated from the raw
    matched rounds, so reports can select any available engine or variant as
    baseline without calculating statistics in the browser. Distinct snapshots
    require a shared schedule
    ``pairing_id``; equal repeat numbers in independent runs are not pairs.
    Warmups never contribute to summary statistics.
    """
    if isinstance(snapshot_paths, (str, os.PathLike)):
        snapshot_paths = [snapshot_paths]
    sources = [Path(path).resolve() for path in snapshot_paths]
    if not sources:
        raise ValueError("At least one sealed snapshot is required.")
    snapshots = {}
    metadata = []
    series = []
    availability = []
    for path in sources:
        snapshot = load(path, verify=True)
        suite = json.loads((path / "suite.json").read_text(encoding="utf-8"))
        if snapshot["id"] in snapshots:
            raise ValueError(f"Duplicate snapshot identity: {snapshot['id']}")
        snapshots[snapshot["id"]] = snapshot
        directory = path
        metadata.append({
            "id": snapshot["id"], "path": str(directory),
            "suite_id": snapshot["suite_id"], "origin": snapshot.get("origin", "unknown"),
            "status": snapshot.get("status"), "created_at": snapshot.get("created_at"),
            "contracts": snapshot.get("contracts", {}), "metrics": snapshot.get("metrics", {}),
            "tools": snapshot.get("tools", {}), "host": snapshot.get("host", {}),
            "schedule": snapshot.get("schedule", {}), "provenance": snapshot.get("provenance", {}),
            "validation": snapshot.get("validation", {}),
            "artifacts": {"snapshot": "snapshot.json", "suite": "suite.json", "hashes": "files.sha256.json"},
            "raw_artifacts": [{"task": record["task"], "phase": record["phase"],
                               "repeat": record["repeat"], "status": record["status"], "files": record["raw"]}
                              for record in snapshot.get("records", []) if record.get("raw")],
        })
        series.extend(_series(snapshot, suite["tasks"]))
        for section in ("unsupported", "qualification_censored"):
            for entry in snapshot.get("schedule", {}).get(section, []):
                availability.append({**entry, "snapshot_id": snapshot["id"], "origin": snapshot["origin"],
                                     "status": entry.get("status", "unsupported" if section == "unsupported" else "censored"),
                                     "stage": "qualification" if section == "qualification_censored" else "capability"})
        for record in snapshot.get("records", []):
            if record.get("status") != "completed":
                availability.append({"snapshot_id": snapshot["id"], "origin": snapshot["origin"],
                                     "task": record["task"], "case": record["case"], "engine": record["engine"],
                                     "variant": record["variant"], "status": record["status"], "stage": record["phase"],
                                     "repeat": record["repeat"], "duration_ns": record["duration_ns"],
                                     "reason": _validation(snapshot, record["task"]).get("reason", ""), "raw": record["raw"]})
    baseline_ids = set()
    if baseline is not None:
        engine = baseline.removeprefix("engine:") if baseline.startswith("engine:") else None
        matches_snapshot = engine is None and baseline in snapshots
        matches_variant = engine is None and any(item["variant"] == baseline for item in series)
        if matches_snapshot and matches_variant:
            raise ValueError("Baseline matches both a snapshot id and a variant; use an unambiguous name.")
        baseline_ids = {item["id"] for item in series if (
            item["engine"] == engine if engine is not None else
            item["snapshot_id"] == baseline if matches_snapshot else item["variant"] == baseline
        )}
        if not baseline_ids:
            raise ValueError(f"Unknown baseline: {baseline}")
    comparisons = []
    for index, left in enumerate(series):
        for right in series[index + 1:]:
            if left["case"] != right["case"] or left["metric"] != right["metric"]:
                continue
            if baseline is not None:
                left_selected = left["id"] in baseline_ids
                right_selected = right["id"] in baseline_ids
                if left_selected == right_selected:
                    continue
                first, second = (left, right) if left_selected else (right, left)
            else:
                first, second = left, right
            comparisons.append(_compare(first, second, snapshots))
            if baseline is None:
                comparisons.append(_compare(second, first, snapshots))
    warnings = []
    if any(item["origin"] == "historical-import" for item in metadata):
        warnings.append("Historical imports retain original evidence; these values are not new reruns.")
    if any(pair["host_differences"] for pair in comparisons):
        warnings.append("Some comparisons use different recorded hosts or host conditions; inspect host differences.")
    warnings.append("Observed ranges describe the captured samples, not confidence intervals.")
    warnings.append("Profile scopes may cover more work than completion metrics; inclusive costs are not additive phases.")
    return {
        "schema_version": 1, "gammabench_version": __version__, "baseline": baseline,
        "method": {"summary": "median and observed minimum/maximum of completed measurement samples",
                   "comparison": "median of candidate/baseline ratios from complete matched rounds",
                   "warmups": "excluded", "timeouts": "censored; never replaced by zero",
                   "direction": "speedup greater than one favours the candidate"},
        "snapshots": metadata, "series": series, "comparisons": comparisons,
        "availability": availability, "warnings": warnings,
    }


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.6g}"


def _range(stats: dict[str, Any]) -> str:
    if stats["median"] is None:
        return "—"
    return f"{_number(stats['minimum'])} – {_number(stats['maximum'])}"


def _label(series: dict[str, Any]) -> str:
    return f"{series['engine']} / {series['variant']} · {series['snapshot_id']}"


def _row_attrs(case: str, variant: str, metric: str, status: str, *, baseline: dict[str, Any] | None = None) -> str:
    attributes = [("case", case), ("variant", variant), ("metric", metric), ("status", status)]
    if baseline is not None:
        attributes.extend((("baseline-engine", baseline["engine"]), ("baseline-variant", baseline["variant"])))
    return " ".join(f'data-{key}="{_escape(value)}"' for key, value in attributes)


def _html(analysis: dict[str, Any], output: Path) -> str:
    series = {item["id"]: item for item in analysis["series"]}
    measurement_rows = []
    for item in analysis["series"]:
        counts = ", ".join(f"{count} {status}" for status, count in sorted(item["statuses"].items())) or "no measurements"
        if item["missing_values"]:
            counts += f"; {item['missing_values']} missing metric"
        status = item["validation_status"]
        qualification = status + (": " + item["validation_reason"] if item["validation_reason"] else "")
        cells = [item["case"], _label(item), item["contract"], item["metric"], item["scope"],
                 _number(item["median"]), _range(item), item["unit"],
                 f"{item['count']} ({counts}); {item['warmups']} warmups excluded", qualification, item["origin"]]
        measurement_rows.append(f'<tr {_row_attrs(item["case"], item["variant"], item["metric"], status)}>' +
                                "".join(f"<td>{_escape(cell)}</td>" for cell in cells) + "</tr>")
    comparison_rows = []
    for pair in analysis["comparisons"]:
        left, right = series[pair["baseline"]], series[pair["candidate"]]
        notes = list(pair["reasons"])
        if pair["host_differences"]:
            notes.append("Host fields differ: " + ", ".join(pair["host_differences"]))
        if pair["historical"]:
            notes.append("Contains historical evidence")
        cells = [pair["case"], _label(left), _label(right), pair["metric"], pair["status"],
                 _number(pair["ratio"]["median"]), _range(pair["ratio"]),
                 _number(pair["speedup"]["median"]), str(pair["ratio"]["count"]), "; ".join(notes) or "Same recorded host"]
        comparison_rows.append(f'<tr {_row_attrs(pair["case"], right["variant"], pair["metric"], pair["status"], baseline=left)}>' +
                               "".join(f"<td>{_escape(cell)}</td>" for cell in cells) + "</tr>")
    availability_rows = []
    for item in analysis["availability"]:
        cells = [item.get("case", ""), item.get("engine", ""), item.get("variant", ""),
                 item["snapshot_id"], item["stage"], item["status"],
                 item.get("reason", item.get("timing_interpretation", "")),
                 item.get("timeout_seconds", ""), item.get("duration_ns", ""), item["origin"]]
        availability_rows.append(f'<tr {_row_attrs(item.get("case", ""), item.get("variant", ""), "", item["status"])}>' +
                                 "".join(f"<td>{_escape(cell)}</td>" for cell in cells) + "</tr>")
    details = []
    for snapshot in analysis["snapshots"]:
        directory = Path(snapshot["path"])
        links = []
        for name, relative in snapshot["artifacts"].items():
            target = os.path.relpath(directory / relative, output)
            # URL quote literal path components, without letting a path become a URI scheme.
            links.append(f'<a href="{_escape(quote(target, safe="/"))}">{_escape(name)}</a>')
        raw_links = []
        for artifact in snapshot["raw_artifacts"]:
            record_links = []
            for name, relative in artifact["files"].items():
                target = os.path.relpath(directory / relative, output)
                record_links.append(f'<a href="{_escape(quote(target, safe="/"))}">{_escape(name)}</a>')
            label = f"{artifact['task']} · {artifact['phase']} {artifact['repeat']} · {artifact['status']}"
            raw_links.append(f"<li>{_escape(label)}: {' · '.join(record_links)}</li>")
        contracts = []
        for name, contract in snapshot["contracts"].items():
            included = "".join(f"<li>{_escape(value)}</li>" for value in contract.get("included", []))
            excluded = "".join(f"<li>{_escape(value)}</li>" for value in contract.get("excluded", []))
            contracts.append(f'<article><h4>{_escape(name)}</h4><p>{_escape(contract.get("description", ""))}</p>'
                             f'<strong>Included</strong><ul>{included}</ul><strong>Excluded</strong><ul>{excluded}</ul></article>')
        details.append(f'<details><summary>{_escape(snapshot["id"])} · {_escape(snapshot["origin"])} · '
                       f'{_escape(snapshot["created_at"])}</summary><p>{" · ".join(links)}</p>'
                       f'<div class="contracts">{"".join(contracts)}</div>'
                       f'<h4>Pinned tools</h4><pre>{_escape(json.dumps(snapshot["tools"], indent=2, sort_keys=True))}</pre>'
                       f'<h4>Recorded host</h4><pre>{_escape(json.dumps(snapshot["host"], indent=2, sort_keys=True))}</pre>'
                       f'<h4>Schedule and provenance</h4><pre>{_escape(json.dumps({"schedule": snapshot["schedule"], "provenance": snapshot["provenance"]}, indent=2, sort_keys=True))}</pre>'
                       f'<details><summary>Raw records and logs ({len(raw_links)})</summary><ul>{"".join(raw_links)}</ul></details></details>')
    variants = {item["variant"] for item in series.values()} | {item.get("variant", "") for item in analysis["availability"]}
    variant_options = "".join(f'<option>{_escape(value)}</option>' for value in sorted(variants) if value)
    metric_options = "".join(f'<option>{_escape(value)}</option>' for value in sorted({item["metric"] for item in series.values()}))
    cases = {item["case"] for item in series.values()} | {item.get("case", "") for item in analysis["availability"]}
    case_options = "".join(f'<option>{_escape(value)}</option>' for value in sorted(cases) if value)
    baseline_series = [series[pair["baseline"]] for pair in analysis["comparisons"]]
    selectors = [("engine:" + value, "Engine: " + value) for value in sorted({item["engine"] for item in baseline_series})]
    selectors += [("variant:" + value, "Variant: " + value) for value in sorted({item["variant"] for item in baseline_series})]
    available = {value for value, _ in selectors}
    selected = analysis["baseline"]
    if selected is not None:
        default_baseline = selected if selected.startswith("engine:") else "variant:" + selected
        if default_baseline not in available:
            default_baseline = ""
    else:
        default_baseline = next((value for value in ("variant:baseline", "engine:linnet") if value in available), "")
    baseline_options = "".join(f'<option value="{_escape(value)}"' +
                               (' selected' if value == default_baseline else '') +
                               f'>{_escape(label)}</option>' for value, label in selectors)
    statuses = ({item["validation_status"] for item in series.values()} |
                {pair["status"] for pair in analysis["comparisons"]} |
                {item["status"] for item in analysis["availability"]})
    status_options = "".join(f'<option>{_escape(value)}</option>' for value in sorted(statuses))
    warnings = "".join(f"<li>{_escape(value)}</li>" for value in analysis["warnings"])
    return Template(resources.files("gammabench").joinpath("assets", "report.html").read_text(encoding="utf-8")).substitute(
        warnings=warnings, variant_options=variant_options, metric_options=metric_options,
        case_options=case_options, baseline_options=baseline_options, status_options=status_options,
        measurement_rows="".join(measurement_rows), comparison_rows="".join(comparison_rows),
        availability_rows="".join(availability_rows), details="".join(details),
    )


def _csv(path: Path, fields: list[str], rows: Any) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def render(snapshot_paths: Any, output: str | os.PathLike[str], *, baseline: str | None = None) -> dict[str, Any]:
    """Write a new, offline report directory; an existing destination is an error."""
    analysis = analyze(snapshot_paths, baseline=baseline)
    directory = Path(output).resolve()
    if any(Path(snapshot["path"]) == directory or Path(snapshot["path"]) in directory.parents
           for snapshot in analysis["snapshots"]):
        raise ValueError("Report output must be outside source snapshots.")
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "analysis.json").write_text(json.dumps(analysis, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    _csv(directory / "results.csv", [
        "snapshot_id", "suite_id", "task", "case", "engine", "variant", "contract", "metric", "unit", "scope",
        "direction", "origin", "validation_status", "validation_reason", "count", "median", "minimum", "maximum", "warmups",
        "measurement_records", "missing_values", "statuses",
    ], ({**item, "statuses": _canonical(item["statuses"])} for item in analysis["series"]))
    _csv(directory / "comparisons.csv", [
        "case", "metric", "unit", "scope", "baseline", "candidate", "baseline_contract", "candidate_contract",
        "status", "pairs", "median_ratio", "minimum_ratio", "maximum_ratio", "median_speedup",
        "minimum_speedup", "maximum_speedup", "reasons", "host_differences", "historical",
    ], ({**pair, "pairs": pair["ratio"]["count"],
         **{f"{stat}_ratio": pair["ratio"][stat] for stat in ("median", "minimum", "maximum")},
         **{f"{stat}_speedup": pair["speedup"][stat] for stat in ("median", "minimum", "maximum")},
         "reasons": _canonical(pair["reasons"]), "host_differences": _canonical(pair["host_differences"])}
        for pair in analysis["comparisons"]))
    _csv(directory / "availability.csv", [
        "snapshot_id", "case", "engine", "variant", "task", "stage", "status", "origin", "repeat",
        "timeout_seconds", "duration_ns", "reason", "timing_interpretation",
    ], analysis["availability"])
    (directory / "index.html").write_text(_html(analysis, directory), encoding="utf-8")
    for name in ("report.css", "report.js"):
        (directory / name).write_bytes(resources.files("gammabench").joinpath("assets", name).read_bytes())
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(directory.iterdir())}
    (directory / "files.sha256.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return analysis
