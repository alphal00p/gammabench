import importlib.util
import json
import os
import shutil
import subprocess
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_gate

from gammabench.ci import (
    assemble_previews,
    ingest,
    inventory,
    prepare_preview,
    validate_bundle,
)
from gammabench.snapshot import write_json

ROOT = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_gate.GateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        (self.checkout / "flake.nix").write_text("{}")
        (self.checkout / "snapshots").mkdir()
        (self.checkout / "docs/results").mkdir(parents=True)
        write_json(
            self.checkout / "web/benchmarks.json",
            {
                "schema_version": 1,
                "families": [],
                "benchmarks": [],
                "subjects": {},
                "default_benchmark": None,
            },
        )

    def bundle(
        self,
        *,
        event="pull_request",
        run_id=10,
        mode="clean",
        baseline="a" * 40,
        candidate="b" * 40,
    ):
        ci = {
            "repository": "alphal00p/gammaloop",
            "repository_url": "https://github.com/alphal00p/gammaloop",
            "candidate": candidate,
            "framework_revision": "f" * 40,
            "baseline": baseline,
            "run_id": run_id,
            "run_attempt": 1,
            "event": event,
            "pr": 123 if event == "pull_request" else None,
        }
        self.fixture.suite["ci"] = ci
        self.fixture.suite["gate"]["baseline"]["commit"] = baseline
        self.fixture.suite["gate"]["candidate"]["commit"] = candidate
        status, public = self.fixture.capture(mode, label=f"ci-{event}-{run_id}")
        bundle = self.root / "bundle"
        bundle.mkdir()
        shutil.move(public, bundle / "demo")
        write_json(
            bundle / "run.json",
            {"schema_version": 1, "ci": ci, "cases": ["demo"], "status": status},
        )
        return bundle

    def test_pr_capture_is_isolated_idempotent_and_renders_nested_site(self):
        bundle = self.bundle()
        result = ingest(bundle, self.checkout, channel="pr", current_head="b" * 40)
        self.assertEqual(result["status"], "registered")
        self.assertEqual(
            ingest(bundle, self.checkout, channel="pr", current_head="b" * 40)[
                "status"
            ],
            "unchanged",
        )
        self.assertEqual(
            json.loads((self.checkout / "web/benchmarks.json").read_text())[
                "benchmarks"
            ],
            [],
        )
        site = self.root / "site"
        site.mkdir()
        assemble_previews(self.checkout, site, ROOT, main_root=self.checkout)
        page = site / result["path"] / "index.html"
        self.assertTrue(page.exists())
        self.assertIn("PR #123", page.read_text())
        self.assertTrue((site / "previews/pr-123/index.html").exists())
        self.assertTrue((site / "previews/index.html").exists())
        self.assertTrue((page.parent / "explore.html").exists())
        self.assertIn('http-equiv="refresh"', page.read_text())
        self.assertIn("ci-history/index.html", page.read_text())
        self.assertTrue((page.parent / "summary.html").exists())
        history = (page.parent / "ci-history/index.html").read_text()
        self.assertIn('data-history-role="proposed"', history)
        self.assertIn("Proposed PR #123", history)
        self.assertTrue(
            list((page.parent / "snapshots/measured").glob("*/files.sha256.json"))
        )

    def test_main_registration_preserves_gate_failure(self):
        bundle = self.bundle(event="push", mode="wrong")
        result = ingest(bundle, self.checkout, channel="main")
        catalog = json.loads((self.checkout / "web/benchmarks.json").read_text())
        self.assertEqual(catalog["benchmarks"][0]["id"], "gammaloop-ci-demo")
        self.assertIn(
            "Regression or correctness failure",
            (self.checkout / result["path"] / "index.html").read_text(),
        )

    def test_stale_completion_cannot_become_current_head(self):
        bundle = self.bundle()
        ingest(bundle, self.checkout, channel="pr", current_head="c" * 40)
        entry = json.loads((self.checkout / "previews/index.json").read_text())[
            "pull_requests"
        ]["123"]
        self.assertIsNone(entry["latest"])
        self.assertEqual(len(entry["captures"]), 1)
        site = self.root / "stale-site"
        assemble_previews(self.checkout, site, ROOT, main_root=self.checkout)
        self.assertNotIn(
            'http-equiv="refresh"', (site / "previews/pr-123/index.html").read_text()
        )

    def test_preview_without_measurements_still_shows_main_and_failure(self):
        main = self.root / "main"
        shutil.copytree(self.checkout, main)
        bundle = self.bundle(event="push")
        ingest(bundle, main, channel="main")
        manifest = json.loads((bundle / "run.json").read_text())
        manifest.update(cases=[], status=2)
        manifest["ci"].update(event="pull_request", pr=123, run_id=20)
        failed = self.root / "failed"
        failed.mkdir()
        write_json(failed / "run.json", manifest)
        result = ingest(failed, self.checkout, channel="pr", current_head="b" * 40)
        site = self.root / "failed-site"
        assemble_previews(self.checkout, site, ROOT, main_root=main)
        page = (site / result["path"] / "ci-history/index.html").read_text()
        self.assertIn("Inconclusive", page)
        self.assertIn("No candidate measurements are available", page)
        self.assertIn('data-history-role="main"', page)
        self.assertNotIn('data-history-role="proposed"', page)

    def test_multiple_prs_and_older_runs_preserve_latest_and_build_failure_page(self):
        bundle = self.bundle()
        manifest = json.loads((bundle / "run.json").read_text())
        empty = self.root / "build-failure"
        empty.mkdir()
        manifest.update(cases=[], status=2)
        ci = manifest["ci"]
        for pr, run in ((123, 20), (124, 30), (123, 10)):
            ci.update(pr=pr, run_id=run)
            write_json(empty / "run.json", manifest)
            ingest(empty, self.checkout, channel="pr", current_head="b" * 40)
        index = json.loads((self.checkout / "previews/index.json").read_text())[
            "pull_requests"
        ]
        self.assertEqual(len(index), 2)
        self.assertTrue(index["123"]["latest"].endswith("/20-1"))
        site = self.root / "failure-site"
        # Match the workflow's relative destination, which must be resolved
        # before the builder changes cwd to its temporary content checkout.
        assemble_previews(
            self.checkout, os.path.relpath(site), ROOT, main_root=self.checkout
        )
        self.assertTrue((site / "previews/pr-123/index.html").is_file())
        self.assertTrue((site / "previews/pr-124/index.html").is_file())
        self.assertIn(
            "Inconclusive", (site / index["123"]["latest"] / "index.html").read_text()
        )

    def test_unknown_files_or_wrong_source_run_are_rejected(self):
        bundle = self.bundle()
        with self.assertRaises(ValueError):
            validate_bundle(bundle, {"run_id": 999})
        (bundle / "private.log").write_text("must not be published")
        with self.assertRaises(ValueError):
            validate_bundle(bundle)

    def test_main_history_rejects_pr_delivery(self):
        with self.assertRaises(ValueError):
            ingest(self.bundle(), self.checkout, channel="main")

    def test_preview_combines_main_history_without_changing_either_archive(self):
        main = self.root / "main"
        shutil.copytree(self.checkout, main)
        ingest(self.bundle(event="push"), main, channel="main")
        fixture = test_gate.GateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with (
            patch.object(self, "fixture", fixture),
            patch.object(self, "root", fixture.root),
        ):
            bundle = self.bundle(run_id=20, baseline="b" * 40, candidate="c" * 40)
        result = ingest(bundle, self.checkout, channel="pr", current_head="c" * 40)
        source = self.checkout / result["path"]
        before_main, before_pr = inventory(main), inventory(source)
        prepared = self.root / "prepared"
        prepared.mkdir()
        registry, proposals = prepare_preview(source, prepared, main)
        entry = registry["benchmarks"][0]
        self.assertEqual(len(entry["analyses"]), 2)
        self.assertEqual(proposals, ["ci-pull_request-20-discovery"])
        self.assertTrue(
            (
                prepared / "snapshots/measured/ci-push-10-discovery/files.sha256.json"
            ).exists()
        )
        site = self.root / "combined-site"
        assemble_previews(self.checkout, site, ROOT, main_root=main)
        from test_history_view import HistoryElements

        page = site / result["path"] / "benchmarks/gammaloop-ci-demo/index.html"
        history = HistoryElements(page.read_text())
        self.assertEqual(
            {point["data-commit"] for point in history.points}, {"b" * 40, "c" * 40}
        )
        proposed = [
            point
            for point in history.points
            if point["data-history-role"] == "proposed"
        ]
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0]["data-commit"], "c" * 40)
        self.assertEqual(proposed[0]["data-baseline-commit"], "b" * 40)
        self.assertEqual(proposed[0]["data-pr"], "123")
        self.assertIn('data-default-tab="history"', page.read_text())
        self.assertEqual(inventory(main), before_main)
        self.assertEqual(inventory(source), before_pr)
        stable = (site / "previews/pr-123/index.html").read_text()
        self.assertIn('http-equiv="refresh"', stable)
        self.assertIn("20-1/index.html", stable)
        self.assertTrue((site / "previews/pr-123/runs.html").is_file())

    def test_conflicting_duplicate_is_rejected(self):
        bundle = self.bundle()
        ingest(bundle, self.checkout, channel="pr", current_head="b" * 40)
        run = json.loads((bundle / "run.json").read_text())
        run["created_at"] = "2026-10-15T00:00:00+00:00"
        write_json(bundle / "run.json", run)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            ingest(bundle, self.checkout, channel="pr", current_head="b" * 40)

    def test_publisher_retries_git_race_and_preserves_concurrent_commit(self):
        spec = importlib.util.spec_from_file_location(
            "publish_ci", ROOT / "scripts/publish-ci.py"
        )
        publisher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publisher)
        bundle = self.bundle()
        archive = self.root / "artifact.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for path in bundle.rglob("*"):
                if path.is_file():
                    output.write(path, path.relative_to(bundle))
        remote, racer = self.root / "remote.git", self.root / "racer"
        real_run = subprocess.run

        def git(cwd, *args):
            return real_run(
                ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
            )

        git(self.root, "init", "--bare", str(remote))
        racer.mkdir()
        git(racer, "init")
        git(racer, "config", "user.name", "Test publisher")
        git(racer, "config", "user.email", "test@example.invalid")
        git(racer, "checkout", "-b", "pr-results")
        (racer / "concurrent-capture.txt").write_text("retained")
        git(racer, "add", ".")
        git(racer, "commit", "-m", "concurrent publication")
        raced = []

        def command(argv, **kwargs):
            argv = list(argv)
            if (
                argv[:1] == ["git"]
                and "https://github.com/alphal00p/gammabench.git" in argv
            ):
                argv[-1] = str(remote)
            if argv[:1] == ["git"] and "push" in argv and not raced:
                git(racer, "push", str(remote), "HEAD:refs/heads/pr-results")
                raced.append(True)
            return real_run(argv, **kwargs)

        dispatches = []

        def github(endpoint, *, publisher=False, payload=None, output=None):
            if endpoint.endswith("/attempts/1"):
                return {
                    "path": ".github/workflows/nixci-readiness.yml",
                    "status": "completed",
                    "event": "pull_request",
                    "head_repository": {"full_name": "alphal00p/gammaloop"},
                    "head_sha": "b" * 40,
                    "pull_requests": [{"number": 123}],
                }
            if endpoint.endswith("/pulls/123"):
                return {
                    "head": {
                        "repo": {"full_name": "alphal00p/gammaloop"},
                        "sha": "b" * 40,
                    }
                }
            if "/artifacts?" in endpoint:
                return {
                    "artifacts": [
                        {"id": 1, "name": "gammabench-10-1", "expired": False}
                    ]
                }
            if endpoint.endswith("/zip"):
                output.write(archive.read_bytes())
                return None
            if endpoint.endswith("/dispatches"):
                dispatches.append(payload)
                return None
            raise AssertionError(endpoint)

        with (
            patch.object(publisher, "github", side_effect=github),
            patch.object(publisher.subprocess, "run", side_effect=command),
            patch.object(publisher.time, "sleep"),
            patch.dict(
                os.environ, {"GAMMABENCH_PUBLISH_TOKEN": "test-only-placeholder"}
            ),
        ):
            publisher.publish(SimpleNamespace(run_id=10, attempt=1))
        self.assertTrue(raced)
        self.assertEqual(len(dispatches), 1)
        listing = git(
            self.root,
            "--git-dir=" + str(remote),
            "ls-tree",
            "-r",
            "--name-only",
            "pr-results",
        ).stdout
        self.assertIn("concurrent-capture.txt", listing)
        self.assertIn("previews/index.json", listing)

    def test_archive_rejects_traversal(self):
        spec = importlib.util.spec_from_file_location(
            "publish_ci", ROOT / "scripts/publish-ci.py"
        )
        publisher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publisher)
        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("../escaped", "unsafe")
        with self.assertRaises(ValueError):
            publisher.extract(archive, self.root / "extracted")
