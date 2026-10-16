"""Resolve presentation links separately from immutable benchmark evidence."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from .registry import checkout_path, validate_registry
from .snapshot import write_json

GITHUB = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)")
COMMIT = re.compile(r"[0-9a-f]{40}")
PATH = "web/source-links.json"


def validate(data):
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
        or not isinstance(data.get("repositories"), dict)
    ):
        raise ValueError("Invalid source-link catalog")
    for repository, commits in data["repositories"].items():
        if (
            not GITHUB.fullmatch(repository)
            or repository.rsplit("/", 1)[-1] in (".", "..")
            or not isinstance(commits, dict)
        ):
            raise ValueError("Source links require a canonical GitHub repository")
        for commit, numbers in commits.items():
            if (
                not COMMIT.fullmatch(commit)
                or not isinstance(numbers, list)
                or any(type(number) is not int or number <= 0 for number in numbers)
            ):
                raise ValueError(
                    "Source links require complete commits and positive PR numbers"
                )
            if numbers != sorted(set(numbers)):
                raise ValueError("Source PR links must be unique and sorted")
    return data


def read(path):
    return (
        validate(json.loads(path.read_text()))
        if path.exists()
        else {"schema_version": 1, "repositories": {}}
    )


def main_commits(root):
    registry = validate_registry(
        json.loads(checkout_path(root, "web/benchmarks.json").read_text())
    )
    captures = {path for entry in registry["benchmarks"] for path in entry["analyses"]}
    commits = set()
    for path in captures:
        analysis = json.loads(checkout_path(root, path).read_text())
        for snapshot in analysis["snapshots"]:
            ci = snapshot.get("provenance", {}).get("ci", {})
            repository, candidate = (
                ci.get("repository_url", ""),
                ci.get("candidate", ""),
            )
            if (
                ci.get("event") == "push"
                and GITHUB.fullmatch(repository)
                and COMMIT.fullmatch(candidate)
            ):
                commits.add((repository, candidate))
    return sorted(commits)


def github(repository, commit):
    endpoint = f"repos/{repository.removeprefix('https://github.com/')}/commits/{commit}/pulls?per_page=100"
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", endpoint],
        check=True,
        capture_output=True,
        text=True,
    )
    pages = json.loads(result.stdout)
    return [pull for page in pages for pull in page]


def merged_pulls(pulls, repository):
    """The commit endpoint establishes association; keep merged PRs into main."""
    numbers = set()
    if not isinstance(pulls, list) or any(not isinstance(pull, dict) for pull in pulls):
        raise ValueError("GitHub returned an invalid PR association response")
    for pull in pulls:
        base = pull.get("base") or {}
        if not isinstance(base, dict) or not isinstance(base.get("repo", {}), dict):
            raise TypeError("GitHub returned an invalid PR base")
        if (
            pull.get("merged_at")
            and base.get("ref") == "main"
            and base.get("repo", {}).get("html_url") == repository
        ):
            number = pull.get("number")
            if type(number) is not int or number <= 0:
                raise ValueError("GitHub returned an invalid PR number")
            numbers.add(number)
    return sorted(numbers)


def resolve(root, *, cache=None, refresh=False, fetch=github):
    root = Path(root).resolve()
    data = read(checkout_path(root, PATH))
    # Committed, reviewed associations take precedence over the derived cache.
    if cache is not None:
        for repository, commits in read(cache)["repositories"].items():
            target = data["repositories"].setdefault(repository, {})
            for commit, numbers in commits.items():
                target.setdefault(commit, numbers)
    for repository, commit in main_commits(root):
        target = data["repositories"].setdefault(repository, {})
        if commit in target and not refresh:
            continue
        try:
            target[commit] = merged_pulls(fetch(repository, commit), repository)
        except (
            OSError,
            subprocess.CalledProcessError,
            ValueError,
            TypeError,
            KeyError,
        ):
            # A metadata outage must not prevent results publication. Do not
            # cache failures as a successful lookup that found no PR.
            print(
                f"Could not resolve PR links for {repository} {commit}; keeping existing links",
                file=sys.stderr,
            )
    validate(data)
    write_json(checkout_path(root, PATH), data)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        write_json(cache, data)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--cache", help="Checkout-relative derived metadata cache")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    root = args.repo.resolve()
    resolve(
        root,
        cache=checkout_path(root, args.cache) if args.cache else None,
        refresh=args.refresh,
    )


if __name__ == "__main__":
    main()
