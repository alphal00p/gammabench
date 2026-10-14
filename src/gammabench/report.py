"""Offline reports from sealed snapshots, with conservative comparison gates."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
from pathlib import Path
import statistics
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
    Without it, all pairs
    for each case and metric are shown. Pair direction follows input snapshot
    order, then task order. Distinct snapshots require a shared schedule
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


def _row_attrs(case: str, variant: str, metric: str, status: str) -> str:
    return " ".join(f'data-{key}="{_escape(value)}"' for key, value in (
        ("case", case), ("variant", variant), ("metric", metric), ("status", status)))


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
        comparison_rows.append(f'<tr {_row_attrs(pair["case"], right["variant"], pair["metric"], pair["status"])}>' +
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
    variant_options = "".join(f'<option>{_escape(value)}</option>' for value in sorted({item["variant"] for item in series.values()}))
    metric_options = "".join(f'<option>{_escape(value)}</option>' for value in sorted({item["metric"] for item in series.values()}))
    warnings = "".join(f"<li>{_escape(value)}</li>" for value in analysis["warnings"])
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>gammabench · benchmark evidence</title>
<style>
:root{{color-scheme:light;--ink:#162b42;--muted:#526175;--border:#dce2e8;--accent:#17647c;--paper:#f4f7fa}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:1600px;margin:auto;padding:32px}}h1{{margin:0;font-size:32px}}h2{{margin-top:32px}}h4{{margin-bottom:8px}}
a{{color:var(--accent)}}.subtle{{color:var(--muted)}}.notice,details{{background:white;border:1px solid var(--border);border-radius:8px;padding:16px;margin:16px 0}}
.controls{{display:flex;flex-wrap:wrap;gap:12px;margin:24px 0}}label{{display:flex;flex-direction:column;gap:4px}}input,select{{font:inherit;padding:8px;border:1px solid var(--border);border-radius:4px;background:white}}input{{min-width:260px}}
.tablewrap{{overflow:auto;border:1px solid var(--border);border-radius:8px;background:white}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top}}th{{background:#edf2f6;white-space:nowrap}}td:nth-child(6),td:nth-child(7),td:nth-child(8){{font-variant-numeric:tabular-nums}}tr[hidden]{{display:none}}
summary{{cursor:pointer;font-weight:600}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,monospace;background:#f4f7fa;padding:12px}}.contracts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:24px}}footer{{margin-top:32px;color:var(--muted)}}@media(max-width:700px){{main{{padding:16px}}h1{{font-size:26px}}input{{min-width:200px}}}}
</style></head><body><main><h1>gammabench</h1><p class="subtle">Verified snapshots · explicit work contracts · paired comparisons</p>
<p><a href="results.csv">Measurement CSV</a> · <a href="comparisons.csv">Comparison CSV</a> · <a href="availability.csv">Unsupported / censored CSV</a> · <a href="analysis.json">Analysis JSON</a> · <a href="files.sha256.json">Report checksums</a></p>
<div class="notice"><strong>How to read these results</strong><ul>{warnings}</ul><p>Summary medians use completed measurement samples. Warmups are excluded. Timeouts and missing samples remain visible and suppress pair rankings. Ratios are candidate / baseline; speedup above 1 favours the candidate using the metric's declared direction. Ratios require exact semantic qualification, equal contracts and metric scopes, and complete matching rounds.</p></div>
<div class="controls"><label>Search cases or text<input id="search" type="search" placeholder="Filter all rows"></label><label>Candidate / measurement variant<select id="variant"><option value="">All variants</option>{variant_options}</select></label><label>Metric<select id="metric"><option value="">All metrics</option>{metric_options}</select></label><label>Qualification / comparison status<select id="status"><option value="">All statuses</option><option>passed</option><option>failed</option><option>unqualified</option><option>comparable</option><option>suppressed</option><option>unsupported</option><option>censored_timeout</option><option>timeout</option><option>invalid</option></select></label></div>
<h2>Measurements <span id="measurement-count" class="subtle"></span></h2><div class="tablewrap"><table id="measurements"><thead><tr><th>Case</th><th>Engine / variant · snapshot</th><th>Contract</th><th>Metric</th><th>Measured scope</th><th>Median</th><th>Observed range</th><th>Unit</th><th>Samples</th><th>Qualification</th><th>Origin</th></tr></thead><tbody>{''.join(measurement_rows)}</tbody></table></div>
<h2>Paired comparisons <span id="comparison-count" class="subtle"></span></h2><div class="tablewrap"><table id="comparisons"><thead><tr><th>Case</th><th>Baseline</th><th>Candidate</th><th>Metric</th><th>Status</th><th>Median ratio</th><th>Observed ratio range</th><th>Median speedup</th><th>Pairs</th><th>Notes</th></tr></thead><tbody>{''.join(comparison_rows)}</tbody></table></div>
<h2>Unsupported, failed and censored work <span id="availability-count" class="subtle"></span></h2><p class="subtle">These entries are not zero timings. Qualification timeouts do not establish a lower bound on a different measured stage.</p><div class="tablewrap"><table id="availability"><thead><tr><th>Case</th><th>Engine</th><th>Variant</th><th>Snapshot</th><th>Stage</th><th>Status</th><th>Reason / timing interpretation</th><th>Cap (seconds)</th><th>Observed process duration (ns)</th><th>Origin</th></tr></thead><tbody>{''.join(availability_rows)}</tbody></table></div>
<h2>Contracts, versions and source evidence</h2>{''.join(details)}<footer>Generated offline by gammabench. Exported HTML, CSV and JSON share the same analysis values. Source snapshots are never changed.</footer></main>
<script>
(() => {{
 const ids = ['search', 'variant', 'metric', 'status'];
 function filter() {{
  const query = document.getElementById('search').value.toLowerCase();
  const selected = Object.fromEntries(ids.slice(1).map(id => [id, document.getElementById(id).value]));
  for (const [table, counter] of [['measurements','measurement-count'], ['comparisons','comparison-count'], ['availability','availability-count']]) {{
   const rows = [...document.querySelectorAll('#'+table+' tbody tr')];
   for (const row of rows) row.hidden = !row.textContent.toLowerCase().includes(query) || Object.entries(selected).some(([key,value]) => value && row.dataset[key] !== value);
   document.getElementById(counter).textContent = '('+rows.filter(row => !row.hidden).length+' / '+rows.length+')';
  }}
 }}
 ids.forEach(id => document.getElementById(id).addEventListener('input', filter)); filter();
}})();
</script></body></html>'''


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
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(directory.iterdir())}
    (directory / "files.sha256.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return analysis
