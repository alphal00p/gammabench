"""Project the retained CI gate reports into the performance tracker.

All paths in a projected run are relative to the published site root until the
page is rendered. Only the presentation changes; original receipts stay sealed.
"""

from collections import Counter
import hashlib
import json
import os

from .registry import checkout_path


OUTCOMES = ("passed", "regression", "inconclusive")
RECORDED_OUTCOMES = ("passed", "failed", "inconclusive")


def run_outcome(rows):
    """A known failure remains actionable alongside an inconclusive case."""
    codes = {row["status"] for row in rows}
    if 1 in codes:
        return 1
    return 2 if not codes or 2 in codes else 0


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _contexts(analysis, snapshot):
    # Compare both sides' retained conditions, excluding only the moving Git
    # revision. Host-only changes are drawn separately by the tracker.
    conditions, hosts = {}, {}
    for item in analysis["series"]:
        if (
            item["snapshot_id"] != snapshot["id"]
            or item["metric"] != "generation_seconds"
        ):
            continue
        history = item.get("history") or {}
        if (
            history.get("kind") != "ci-gate"
            or item.get("validation_status") != "passed"
        ):
            return None, None
        side = item["task"]
        receipt = dict(history["context"])
        host = receipt.pop("host", None)
        if not host or not (host.get("cpu_model") or host.get("cpu")):
            return None, None
        identity = snapshot["provenance"]["ci_gate"]["identity"][side]
        conditions[side] = [
            receipt,
            item["workload"],
            item["scope"],
            item["unit"],
            item["direction"],
            item["engine"],
            snapshot["contracts"][item["contract"]],
            identity["config_sha256"],
            identity["corpus_sha256"],
        ]
        hosts[side] = host
    if set(conditions) != {"baseline", "candidate"}:
        return None, None
    return _fingerprint(conditions), _fingerprint(hosts)


def _stage(metric, phase):
    stats = metric.get(phase)
    if not stats:
        return None
    return {
        "baseline": stats["baseline_median"],
        "candidate": stats["candidate_median"],
        "delta": stats["median_relative_delta"],
        "pairs": stats["pair_count"],
        "slower_pairs": stats["slower_pairs"],
        "status": OUTCOMES[stats["status"]],
    }


def _case(content, prefix, row, ci):
    comparison = row.get("comparison")
    if comparison is None:
        comparison = (
            "within-candidate" if row["baseline"] == row["candidate"] else "revision"
        )
    within = comparison == "within-candidate"
    case = {
        "id": row["title"],
        "comparison": comparison,
        "baseline_label": "Baseline",
        "candidate_label": "Candidate" if within or not ci.get("pr") else "PR",
        "status": RECORDED_OUTCOMES[row["status"]],
        "discovery": None,
        "confirmation": None,
        "mismatches": None,
        "flagged_metrics": {},
        "evidence": None,
        "series_key": None,
        "runner_key": None,
        "reason": "",
    }
    if not row["href"]:
        case["reason"] = "No measurements were captured for this workload."
        return case
    report_path = checkout_path(content, row["href"])
    case["evidence"] = (prefix / row["href"]).as_posix()
    analysis = json.loads(report_path.with_name("analysis.json").read_text())
    phases = {}
    for snapshot in analysis["snapshots"]:
        provenance = snapshot["provenance"]
        if provenance.get("ci") != ci:
            raise ValueError("Tracker report belongs to a different CI run")
        proof = provenance.get("ci_gate") or {}
        phase = proof.get("phase")
        if phase in ("discovery", "confirmation"):
            if phase in phases or snapshot["suite_id"] != "gammaloop-ci-" + case["id"]:
                raise ValueError("Duplicate or unrelated tracker measurement")
            phases[phase] = snapshot
    if "discovery" not in phases:
        case["reason"] = "The retained report has no discovery measurement."
        return case
    discovery = phases["discovery"]
    if within:
        fixture_path = checkout_path(
            content, f"snapshots/measured/{discovery['id']}/inputs/fixture"
        )
        if fixture_path.is_file():
            try:
                fixture = json.loads(fixture_path.read_bytes())
            except (ValueError, UnicodeDecodeError):
                fixture = None  # Older suites can retain non-JSON fixtures.
            if (
                isinstance(fixture, dict)
                and fixture.get("config", {}).get("comparison") == "ltd_vs_cff"
            ):
                case.update(baseline_label="CFF", candidate_label="LTD")
    # Confirmation includes both stages' original paired summaries and their
    # joint verdict. A discovery-only gate never acquires a confirmation point.
    final = phases.get("confirmation", discovery)["provenance"]["ci_gate"].get(
        "report", {}
    )
    if final.get("status") is not None and final["status"] != row["status"]:
        raise ValueError("Tracker case outcome differs from its retained gate")
    metrics = final.get("metrics", {})
    case["mismatches"] = final.get("correctness_mismatches")
    if final.get("invalid_input") or final.get("identity_mismatch"):
        case["mismatches"] = None
        case["reason"] = "The gate could not qualify this measurement."
    elif case["mismatches"]:
        case.update(status="correctness", reason="Measured outputs differ.")
    else:
        for name, metric in metrics.items():
            status = OUTCOMES[metric["status"]]
            if status == "passed":
                continue
            if status == "regression":
                case["status"] = status
            phase = "confirmation" if "confirmation" in metric else "discovery"
            delta = metric[phase]["median_relative_delta"]
            case["flagged_metrics"][name] = {
                "phase": phase,
                "status": status,
                "delta": delta,
            }
        generation = metrics.get("generation_seconds", {})
        case.update(
            discovery=_stage(generation, "discovery"),
            confirmation=_stage(generation, "confirmation"),
        )
        if case["discovery"]:
            case["series_key"], case["runner_key"] = _contexts(analysis, discovery)
        else:
            case["reason"] = "Generation time was not measured."
    return case


def tracker_run(content, prefix, page, data):
    ci = data["ci"]
    cases = [_case(content, prefix, row, ci) for row in data["rows"]]
    return {
        "id": f"{ci['run_id']}-{ci['run_attempt']}",
        "sha": ci["candidate"],
        "baseline": ci["baseline"],
        "captured_at": data.get("captured_at", ""),
        "url": page.as_posix(),
        "actions_url": f"{ci['repository_url']}/actions/runs/{ci['run_id']}/attempts/{ci['run_attempt']}",
        "status": RECORDED_OUTCOMES[run_outcome(data["rows"])],
        "cases": cases,
        "reason": "" if cases else "No workloads produced measurements.",
    }


def tracker_page(runs, *, selected, directory, pr=None, explorer):
    def relative(path):
        return os.path.relpath(path, directory) if path else None

    return {
        "title": "GammaLoop performance · " + (f"PR #{pr}" if pr else "main"),
        "preview": pr is not None,
        "pr": pr,
        "explorer": explorer,
        "initial_view": "suite",
        "selected_run": selected,
        "runs": [
            {
                **run,
                "url": relative(run["url"]),
                "cases": [
                    {**case, "evidence": relative(case["evidence"])}
                    for case in run["cases"]
                ],
            }
            for run in runs
        ],
    }


def summary(data, run, directory):
    """Make the same exact verdict and paired measurements readable without JS."""
    rows = []
    labels = {
        "passed": "Passed",
        "failed": "Failed",
        "correctness": "Correctness failure",
        "regression": "Regression",
        "inconclusive": "Inconclusive",
    }
    for row, case in zip(data["rows"], run["cases"], strict=True):
        phase = "confirmation" if case["confirmation"] is not None else "discovery"
        stage = case[phase]
        details = []
        if stage:
            delta = stage["delta"]
            details.append(
                f"Generation {delta:+.2%} in {phase} · {stage['baseline']:.4g} → "
                f"{stage['candidate']:.4g} s · {stage['pairs']} pairs"
            )
        for name, issue in case["flagged_metrics"].items():
            if name != "generation_seconds":
                detail = f"{name} {issue['delta']:+.2%} in {issue['phase']} ({issue['status']})"
                details.append(detail)
        if case["mismatches"] is not None:
            details.append(f"{case['mismatches']} correctness mismatches")
        rows.append(
            {
                **row,
                "outcome": labels[case["status"]],
                "detail": " · ".join(details) or case["reason"],
                "href": os.path.relpath(case["evidence"], directory)
                if case["evidence"]
                else "",
            }
        )
    rows.sort(key=lambda row: {1: 0, 2: 1, 0: 2}[row["status"]])
    tracker = (
        "index.html"
        if data.get("preview")
        else os.path.relpath("ci-history/index.html", directory)
        + "?view=suite&run="
        + run["id"]
    )
    return {
        **data,
        "status": run_outcome(data["rows"]),
        "rows": rows,
        "tracker": tracker,
        "headline": counts_text(run),
    }


def counts_text(run):
    counts = Counter(case["status"] for case in run["cases"])
    parts = []
    for key in ("correctness", "regression", "failed", "inconclusive", "passed"):
        if key in counts:
            n = counts[key]
            label = {
                "correctness": "correctness failure",
                "regression": "regression",
            }.get(key, key)
            parts.append(
                f"{n} {label}"
                + ("s" if key in ("correctness", "regression") and n != 1 else "")
            )
    return " · ".join(parts) or "Inconclusive · no measured workloads"
