"""Validate CI deliveries and publish append-only main or PR result catalogs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from importlib import resources
from pathlib import Path

from .gate import comparator, measurement, phase_pairs, validate_policy
from .registry import checkout_path, plan_benchmark, register_report, validate_registry
from .schema import IDENTIFIER, REVISION
from .snapshot import load, sha256, verify, write_json

REPOSITORY = "alphal00p/gammaloop"


def validate_identity(ci):
    if (
        ci.get("repository") != REPOSITORY
        or ci.get("repository_url") != "https://github.com/" + REPOSITORY
    ):
        raise ValueError("Unexpected CI source repository")
    for key in ("candidate", "baseline", "framework_revision"):
        if not REVISION.fullmatch(str(ci.get(key, ""))):
            raise ValueError("CI revisions must be complete commits")
    for key in ("run_id", "run_attempt"):
        if type(ci.get(key)) is not int or ci[key] <= 0:
            raise ValueError("CI run identifiers must be positive integers")
    if ci.get("event") not in (
        "push",
        "pull_request",
        "merge_group",
        "workflow_dispatch",
    ):
        raise ValueError("Unsupported CI event")
    if ci.get("event") == "pull_request" and (
        type(ci.get("pr")) is not int or ci["pr"] <= 0
    ):
        raise ValueError("PR capture requires its pull request number")
    return ci


def inventory(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise ValueError("CI delivery must not contain symlinks")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
        elif not path.is_dir():
            raise ValueError("Unsupported CI delivery entry")
    return result


def _report_matches(recorded, recomputed, policy):
    if recorded == recomputed:
        return True
    # Early schema-2 gates predate these additive diagnostics. Their ordinary
    # numeric qualification has no certificate that could change the mismatch
    # count. Keep every outcome and statistic exact; never strip arbitrary keys.
    diagnostics = {"strict_correctness_mismatches", "certified_zero_components"}
    if (
        isinstance(recorded, dict)
        and not diagnostics.intersection(recorded)
        and policy.get("correctness_format", "number") == "number"
        and policy.get("certified_zero") is None
        and recomputed["certified_zero_components"] == 0
        and recomputed["strict_correctness_mismatches"]
        == recomputed["correctness_mismatches"]
    ):
        return recorded == {
            key: value for key, value in recomputed.items() if key not in diagnostics
        }
    return False


def validate_bundle(bundle, expected=None):
    bundle = Path(bundle)
    files = inventory(bundle)
    run = json.loads((bundle / "run.json").read_text())
    ci = validate_identity(run["ci"])
    if expected is not None and any(ci.get(k) != v for k, v in expected.items()):
        raise ValueError("Delivery does not belong to the triggering workflow run")
    cases = run.get("cases")
    if (
        run.get("schema_version") != 1
        or run.get("status") not in (0, 1, 2)
        or not isinstance(cases, list)
        or len(cases) != len(set(cases))
        or any(not isinstance(c, str) or not IDENTIFIER.fullmatch(c) for c in cases)
    ):
        raise ValueError("Malformed CI run manifest")
    allowed = {"run.json"}
    captures = []
    for case in cases:
        root = bundle / case
        if (root / "failure.json").is_file():
            failure = json.loads((root / "failure.json").read_text())
            if failure != {"schema_version": 1, "case": case, "status": 2}:
                raise ValueError("Malformed case failure")
            allowed.add(case + "/failure.json")
            captures.append((case, [], 2))
            continue
        gate = json.loads((root / "gate.json").read_text())
        if gate.get("ci") != ci or gate.get("snapshots") not in (
            ["discovery"],
            ["discovery", "confirmation"],
        ):
            raise ValueError("Malformed gate capture")
        allowed.add(case + "/gate.json")
        snapshots = []
        data = None
        original = None
        recomputed = None
        for phase in gate["snapshots"]:
            path = root / phase
            snapshot = load(path)
            if (
                snapshot["provenance"].get("ci") != ci
                or snapshot["suite_id"] != "gammaloop-ci-" + case
                or snapshot["provenance"].get("ci_gate", {}).get("phase") != phase
            ):
                raise ValueError("Snapshot source identity differs from its delivery")
            proof = snapshot["provenance"]["ci_gate"]
            suite = json.loads((path / "suite.json").read_text())
            subjects = proof["identity"]
            if (
                subjects != suite.get("gate")
                or subjects["candidate"]["commit"] != ci["candidate"]
                or subjects["baseline"]["commit"]
                not in (ci["baseline"], ci["candidate"])
            ):
                raise ValueError("Measured commit differs from its delivery")
            policy = validate_policy(proof["policy"], suite)
            if original is None:
                original = (policy, proof["identity"])
                data = measurement(*original)
            elif original != (policy, proof["identity"]):
                raise ValueError("Confirmation changed policy or measured identities")
            checker = comparator(*original)
            try:
                data[phase] = phase_pairs(snapshot)
                recomputed = checker.analyze(data)
            except ValueError:
                recomputed = checker.analyze(None)
            if not _report_matches(proof.get("report"), recomputed, policy):
                raise ValueError(
                    "Recorded gate outcome disagrees with its numeric evidence"
                )
            allowed.update(case + "/" + phase + "/" + name for name in verify(path))
            allowed.add(case + "/" + phase + "/files.sha256.json")
            snapshots.append(path)
        status = gate["report"]["status"]
        if not _report_matches(gate["report"], recomputed, policy):
            raise ValueError("Final gate outcome disagrees with its sealed phases")
        if status not in (0, 1, 2):
            raise ValueError("Unknown gate status")
        captures.append((case, snapshots, status))
    if set(files) != allowed:
        raise ValueError("Unexpected files in CI delivery")
    if run["status"] != max((status for _, _, status in captures), default=2):
        raise ValueError("Run outcome does not match its cases")
    return run, captures, files


def render_index(data, destination, *, assets, home):
    from .report import _typst

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gammabench-ci-index-") as temporary:
        root = Path(temporary)
        for name in ("ci.typ", "components.typ"):
            (root / name).write_bytes(
                resources.files("gammabench").joinpath("assets", name).read_bytes()
            )
        write_json(root / "ci.json", {**data, "assets": assets, "home": home})
        subprocess.run(
            [
                _typst(),
                "compile",
                "--root",
                str(root),
                "--features",
                "html",
                "--format",
                "html",
                "--creation-timestamp",
                "0",
                str(root / "ci.typ"),
                str(destination),
            ],
            check=True,
        )


def _register(root, case, snapshots, capture_id, *, preview=False):
    if not snapshots:
        return None
    definition = json.loads((snapshots[0] / "suite.json").read_text())
    registry_path = root / "web/benchmarks.json"
    registry = json.loads(registry_path.read_text())
    benchmark = definition["id"]
    if not any(item["id"] == benchmark for item in registry["benchmarks"]):
        suite_path = "suites/" + benchmark + "/suite.json"
        write_json(root / suite_path, {**definition, "archived_only": True})
        registry = plan_benchmark(
            registry,
            benchmark,
            family="gammaloop-ci",
            family_title="GammaLoop CI",
            title=case,
            description="Fresh paired generation and evaluator measurements",
            suite=suite_path,
            default_metric="generation_seconds"
            if "generation_seconds" in definition["metrics"]
            else next(iter(definition["metrics"])),
            default_case=case,
        )
        write_json(registry_path, registry)
    relative = "docs/results/" + capture_id + "-" + case
    register_report(
        snapshots,
        root / relative,
        benchmark,
        root=root,
        baseline="baseline",
        home=os.path.relpath(root / "index.html", root / relative) if preview else None,
    )
    return relative + "/index.html"


def ingest(bundle, root, *, channel, current_head=None, expected=None):
    """Append in a disposable checkout; Git publication commits the transaction."""
    root = Path(root).resolve()
    run, captures, files = validate_bundle(bundle, expected)
    ci = run["ci"]
    if channel == "main" and ci["event"] != "push":
        raise ValueError("Only main-push captures belong in main history")
    if channel == "pr" and ci["event"] != "pull_request":
        raise ValueError("Only PR captures belong in PR previews")
    identity = ci["candidate"] + "-" + str(ci["run_id"]) + "-" + str(ci["run_attempt"])
    capture_id = "gammaloop-" + identity
    relative = (
        Path("previews")
        / ("pr-" + str(ci["pr"]))
        / ci["candidate"]
        / f"{ci['run_id']}-{ci['run_attempt']}"
        if channel == "pr"
        else Path("docs/results/ci-runs") / capture_id
    )
    receipt = root / relative / "delivery.json"
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    if receipt.exists():
        if json.loads(receipt.read_text())["digest"] != digest:
            raise ValueError("Conflicting evidence for an existing CI capture")
        return {"status": "unchanged", "path": relative.as_posix()}
    if (root / relative).exists():
        raise ValueError("Incomplete existing publication; retry in a fresh checkout")
    content = root / relative if channel == "pr" else root
    if channel == "pr":
        content.mkdir(parents=True)
        (content / "flake.nix").write_text("{}\n")
        write_json(
            content / "web/benchmarks.json",
            {
                "schema_version": 1,
                "families": [],
                "benchmarks": [],
                "default_benchmark": None,
                "subjects": {},
            },
        )
        (content / "docs/results").mkdir(parents=True)
        (content / "snapshots").mkdir()
    rows = []
    for case, snapshots, status in captures:
        report = _register(
            content, case, snapshots, capture_id, preview=channel == "pr"
        )
        subjects = (
            load(snapshots[0])["provenance"]["ci_gate"]["identity"]
            if snapshots
            else None
        )
        baseline = subjects["baseline"]["commit"] if subjects else ci["baseline"]
        rows.append(
            {
                "title": case,
                "status": status,
                "href": report or "",
                "candidate": ci["candidate"],
                "baseline": baseline,
                "comparison": "within-candidate"
                if subjects and baseline == ci["candidate"]
                else "revision",
            }
        )
    data = {
        "title": "GammaLoop performance"
        + (" · PR #" + str(ci["pr"]) if channel == "pr" else " · main"),
        "ci": ci,
        "status": run["status"],
        "rows": rows,
        "captured_at": run.get("created_at", ""),
        "preview": channel == "pr",
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    write_json(receipt, {"digest": digest, "ci": ci, "files": files})
    write_json(receipt.with_name("run.json"), data)
    if channel == "main":
        for row in rows:
            if row["href"]:
                row["href"] = os.path.relpath(root / row["href"], receipt.parent)
        render_index(
            data,
            receipt.with_name("index.html"),
            assets="../../../../assets/",
            home="../../../../index.html",
        )
    else:
        index_path = root / "previews/index.json"
        index = (
            json.loads(index_path.read_text())
            if index_path.exists()
            else {"schema_version": 1, "pull_requests": {}}
        )
        entry = index["pull_requests"].setdefault(
            str(ci["pr"]), {"captures": [], "latest": None}
        )
        item = {
            "path": relative.as_posix(),
            "candidate": ci["candidate"],
            "baseline": ci["baseline"],
            "run_id": ci["run_id"],
            "run_attempt": ci["run_attempt"],
            "status": run["status"],
        }
        entry["captures"].append(item)
        entry["captures"].sort(
            key=lambda value: (value["run_id"], value["run_attempt"])
        )
        eligible = [
            value for value in entry["captures"] if value["candidate"] == current_head
        ]
        entry["latest"] = eligible[-1]["path"] if eligible else None
        write_json(index_path, index)
    return {"status": "registered", "path": relative.as_posix()}


def prepare_preview(source, checkout, main_root):
    """Compose retained main and PR evidence without changing either archive."""
    registry = {
        "schema_version": 1,
        "families": [],
        "benchmarks": [],
        "subjects": {},
        "default_benchmark": None,
    }
    entries = {}
    proposals = []
    for root, proposed in ((main_root, False), (source, True)):
        catalog = validate_registry(
            json.loads((root / "web/benchmarks.json").read_text())
        )
        selected = [
            entry
            for entry in catalog["benchmarks"]
            if entry["family"] == "gammaloop-ci"
        ]
        for family in catalog["families"]:
            if family["id"] == "gammaloop-ci" and not registry["families"]:
                registry["families"].append(copy.deepcopy(family))
        for name, subject in catalog.get("subjects", {}).items():
            if name in registry["subjects"] and registry["subjects"][name] != subject:
                raise ValueError("Preview subject mapping conflicts with main")
            registry["subjects"][name] = copy.deepcopy(subject)
        for entry in selected:
            definition = json.loads(checkout_path(root, entry["suite"]).read_text())
            if entry["id"] not in entries:
                entries[entry["id"]] = copy.deepcopy(entry)
                entries[entry["id"]].update(analyses=[], reports=[])
                target = checkout_path(checkout, entry["suite"])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(checkout_path(root, entry["suite"]), target)
            retained = entries[entry["id"]]
            expected = json.loads(
                checkout_path(checkout, retained["suite"]).read_text()
            )
            if entry["definition_version"] != retained["definition_version"] or any(
                definition[key] != expected[key]
                for key in ("id", "contracts", "metrics")
            ):
                raise ValueError("Preview benchmark definition conflicts with main")
            for analysis_path, report_path in zip(entry["analyses"], entry["reports"]):
                origin = checkout_path(root, analysis_path)
                target = checkout_path(checkout, analysis_path).parent
                if target.exists():
                    if inventory(origin.parent) != inventory(target):
                        raise ValueError("Conflicting retained report in preview")
                else:
                    inventory(origin.parent)
                    shutil.copytree(origin.parent, target)
                analysis = json.loads(origin.read_text())
                for snapshot in analysis["snapshots"]:
                    identifier = snapshot["id"]
                    if not IDENTIFIER.fullmatch(identifier):
                        raise ValueError("Unsafe preview snapshot identity")
                    relative = "snapshots/measured/" + identifier
                    original = checkout_path(root, relative)
                    copied = checkout_path(checkout, relative)
                    hashes = verify(original)
                    if copied.exists():
                        if verify(copied) != hashes:
                            raise ValueError("Conflicting retained snapshot in preview")
                    else:
                        shutil.copytree(original, copied)
                    if proposed and identifier not in proposals:
                        proposals.append(identifier)
                if analysis_path not in retained["analyses"]:
                    retained["analyses"].append(analysis_path)
                    retained["reports"].append(report_path)
    registry["benchmarks"] = list(entries.values())
    registry["default_benchmark"] = next(iter(entries), None)
    write_json(checkout / "web/benchmarks.json", validate_registry(registry))
    return registry, proposals


def assemble_previews(data_root, destination, framework, *, main_root=None):
    """Render only trusted templates; preview branch files are never executed."""
    data_root, destination, framework = (
        Path(path).resolve() for path in (data_root, destination, framework)
    )
    main_root = Path(main_root).resolve() if main_root else framework
    inventory(data_root)
    index_path = data_root / "previews/index.json"
    index = (
        json.loads(index_path.read_text())
        if index_path.exists()
        else {"pull_requests": {}}
    )
    overview = []
    for number, pr in sorted(
        index["pull_requests"].items(), key=lambda pair: int(pair[0])
    ):
        if not number.isdecimal() or int(number) <= 0:
            raise ValueError("Invalid PR preview identity")
        rows = []
        for capture in pr["captures"]:
            relative = Path(capture["path"])
            expected = (
                Path("previews")
                / ("pr-" + number)
                / capture["candidate"]
                / f"{capture['run_id']}-{capture['run_attempt']}"
            )
            if relative != expected or not REVISION.fullmatch(capture["candidate"]):
                raise ValueError("Unsafe preview path")
            source = data_root / relative
            inventory(source)
            with tempfile.TemporaryDirectory(prefix="gammabench-preview-") as temporary:
                checkout = Path(temporary) / "source"
                checkout.mkdir()
                for name in ("src", "scripts", "web"):
                    shutil.copytree(framework / name, checkout / name)
                (checkout / "docs/results").mkdir(parents=True)
                (checkout / "snapshots").mkdir()
                registry, proposals = prepare_preview(source, checkout, main_root)
                shutil.copyfile(
                    framework / "artifacts.json", checkout / "artifacts.json"
                )
                (checkout / "flake.nix").write_text("{}\n")
                target = destination / relative
                data = json.loads((source / "run.json").read_text())
                preview = {
                    "ci": data["ci"],
                    "status": data["status"],
                    "snapshots": proposals,
                    "missing_cases": [
                        row["title"] for row in data["rows"] if not row["href"]
                    ],
                    "captured_at": data.get("captured_at", ""),
                }
                write_json(checkout / "web/preview.json", preview)
                if registry["benchmarks"]:
                    subprocess.run(
                        [
                            "bash",
                            str(framework / "scripts/build-pages.sh"),
                            str(target),
                        ],
                        cwd=checkout,
                        env=os.environ | {"GAMMABENCH_CONTENT_ROOT": str(checkout)},
                        check=True,
                    )
                    (target / "index.html").rename(target / "explore.html")
                else:
                    target.mkdir(parents=True)
                    shutil.copytree(
                        framework / "src/gammabench/assets/theme",
                        target / "assets/theme",
                    )
                    shutil.copyfile(
                        framework / "src/gammabench/assets/report.css",
                        target / "assets/report.css",
                    )
                for row in data["rows"]:
                    if row["href"]:
                        row["href"] = (
                            "explore.html?benchmark=gammaloop-ci-"
                            + row["title"]
                            + "&case=" + row["title"] + "&tab=history"
                        )
                render_index(
                    data,
                    target / "summary.html",
                    assets="assets/",
                    home="explore.html"
                    if registry["benchmarks"]
                    else "../../index.html",
                )
                render_index(
                    {
                        **data,
                        "redirect": "explore.html?tab=history"
                        if registry["benchmarks"]
                        else "summary.html",
                    },
                    target / "index.html",
                    assets="assets/",
                    home="summary.html",
                )
            rows.append(
                {
                    "title": capture["candidate"][:12]
                    + f" · run {capture['run_id']}/{capture['run_attempt']}"
                    + (" · latest" if pr["latest"] == capture["path"] else ""),
                    "href": relative.relative_to(
                        Path("previews") / ("pr-" + number)
                    ).as_posix()
                    + "/index.html",
                    "status": capture["status"],
                    "candidate": capture["candidate"],
                    "baseline": capture["baseline"],
                }
            )
        landing = {
            "title": "GammaLoop PR #" + number,
            "rows": list(reversed(rows)),
            "preview": True,
        }
        render_index(
            landing,
            destination / "previews" / ("pr-" + number) / "runs.html",
            assets="../../assets/",
            home="../index.html",
        )
        latest = pr.get("latest")
        if latest is not None:
            if latest not in {capture["path"] for capture in pr["captures"]}:
                raise ValueError("Latest preview does not name a retained capture")
            landing["redirect"] = (
                Path(latest).relative_to(Path("previews") / ("pr-" + number)).as_posix()
                + "/index.html"
            )
        render_index(
            landing,
            destination / "previews" / ("pr-" + number) / "index.html",
            assets="../../assets/",
            home="../index.html",
        )
        overview.append(
            {
                "title": "PR #" + number,
                "href": "pr-" + number + "/index.html",
                "status": None,
                "candidate": "",
                "baseline": "",
            }
        )
    render_index(
        {"title": "GammaLoop PR previews", "rows": overview, "preview": True},
        destination / "previews/index.html",
        assets="../assets/",
        home="../index.html",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("ingest")
    publish.add_argument("bundle", type=Path)
    publish.add_argument("--repo", required=True, type=Path)
    publish.add_argument("--channel", choices=("main", "pr"), required=True)
    publish.add_argument("--current-head")
    publish.add_argument("--expected", type=Path, required=True)
    assemble = commands.add_parser("assemble-previews")
    assemble.add_argument("data", type=Path)
    assemble.add_argument("destination", type=Path)
    assemble.add_argument("--framework", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "ingest":
        print(
            json.dumps(
                ingest(
                    args.bundle,
                    args.repo,
                    channel=args.channel,
                    current_head=args.current_head,
                    expected=json.loads(args.expected.read_text()),
                )
            )
        )
    else:
        assemble_previews(args.data, args.destination, args.framework)


if __name__ == "__main__":
    main()
