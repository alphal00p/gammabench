"""PR annotations are derived metadata, separate from sealed measurements."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from gammabench.source_links import main_commits, merged_pulls, read, resolve, validate

REPOSITORY = "https://github.com/alphal00p/gammaloop"


def pull(number=140, *, repository=REPOSITORY, branch="main", merged=True):
    return {
        "number": number,
        "merged_at": "2026-10-15T00:00:00Z" if merged else None,
        "base": {"ref": branch, "repo": {"html_url": repository}},
    }


class SourceLinkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "web").mkdir()
        analysis = self.root / "docs/results/ci/analysis.json"
        analysis.parent.mkdir(parents=True)
        self.analysis = {
            "snapshots": [
                {
                    "id": "first",
                    "provenance": {
                        "ci": {
                            "event": "push",
                            "candidate": "a" * 40,
                            "repository_url": REPOSITORY,
                        }
                    },
                },
                {
                    "id": "second-case",
                    "provenance": {
                        "ci": {
                            "event": "push",
                            "candidate": "a" * 40,
                            "repository_url": REPOSITORY,
                        }
                    },
                },
                {
                    "id": "pr",
                    "provenance": {
                        "ci": {
                            "event": "pull_request",
                            "candidate": "b" * 40,
                            "pr": 140,
                            "repository_url": REPOSITORY,
                        }
                    },
                },
            ]
        }
        analysis.write_text(json.dumps(self.analysis))
        (self.root / "web/benchmarks.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "families": [{"id": "ci", "title": "CI"}],
                    "default_benchmark": "ci",
                    "benchmarks": [
                        {
                            "id": "ci",
                            "family": "ci",
                            "title": "CI",
                            "description": "CI measurements",
                            "definition_version": "1",
                            "default_metric": "seconds",
                            "analyses": ["docs/results/ci/analysis.json"],
                            "reports": ["docs/results/ci/index.html"],
                        }
                    ],
                }
            )
        )

    def test_lookup_is_once_per_main_commit_and_cached_without_changing_reports(self):
        self.assertEqual(main_commits(self.root), [(REPOSITORY, "a" * 40)])
        before = (self.root / "docs/results/ci/analysis.json").read_bytes()
        requests = []

        def fetch(repository, commit):
            requests.append((repository, commit))
            return [pull(), pull(141), pull()]

        cache = self.root / ".gammabench/source-links.json"
        result = resolve(self.root, cache=cache, fetch=fetch)
        self.assertEqual(result["repositories"][REPOSITORY]["a" * 40], [140, 141])
        self.assertEqual(read(cache), result)
        resolve(self.root, cache=cache, fetch=fetch)
        self.assertEqual(requests, [(REPOSITORY, "a" * 40)])
        self.assertEqual(
            (self.root / "docs/results/ci/analysis.json").read_bytes(), before
        )

    def test_only_merged_prs_into_the_measured_repository_main_are_kept(self):
        self.assertEqual(
            merged_pulls(
                [
                    pull(),
                    pull(141, merged=False),
                    pull(142, branch="feature"),
                    pull(143, repository="https://github.com/other/repo"),
                ],
                REPOSITORY,
            ),
            [140],
        )

    def test_no_association_is_cached_as_a_commit_without_a_pr(self):
        result = resolve(self.root, fetch=lambda *_: [])
        self.assertEqual(result["repositories"][REPOSITORY]["a" * 40], [])
        resolve(
            self.root,
            fetch=lambda *_: self.fail("A successful empty lookup should be cached"),
        )

    def test_outage_retains_known_links_and_does_not_cache_a_failed_lookup(self):
        result = resolve(self.root, fetch=lambda *_: [pull()])

        def outage(*_):
            raise subprocess.CalledProcessError(1, "gh")

        self.assertEqual(resolve(self.root, refresh=True, fetch=outage), result)
        (self.root / "web/source-links.json").unlink()
        missing = resolve(self.root, fetch=outage)
        self.assertNotIn("a" * 40, missing["repositories"][REPOSITORY])

    def test_reviewed_annotations_take_precedence_over_the_restored_cache(self):
        resolve(self.root, fetch=lambda *_: [pull()])
        cache = self.root / "cache.json"
        cache.write_text(
            json.dumps(
                {"schema_version": 1, "repositories": {REPOSITORY: {"a" * 40: [999]}}}
            )
        )
        result = resolve(
            self.root, cache=cache, fetch=lambda *_: self.fail("Already resolved")
        )
        self.assertEqual(result["repositories"][REPOSITORY]["a" * 40], [140])

    def test_unsafe_source_links_are_rejected(self):
        for repository, commit, numbers in [
            ("https://evil.test/a/b", "a" * 40, [1]),
            (REPOSITORY, "main", [1]),
            (REPOSITORY, "a" * 40, [True]),
            (REPOSITORY, "a" * 40, [-1]),
            (REPOSITORY, "a" * 40, ["1"]),
            (REPOSITORY, "a" * 40, [2, 1]),
            ("https://github.com/a/..", "a" * 40, [1]),
        ]:
            with (
                self.subTest(repository=repository, commit=commit, numbers=numbers),
                self.assertRaises(ValueError),
            ):
                validate(
                    {
                        "schema_version": 1,
                        "repositories": {repository: {commit: numbers}},
                    }
                )
