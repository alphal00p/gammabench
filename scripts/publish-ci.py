#!/usr/bin/env python3
"""Publish a verified GammaLoop Actions artifact from trusted default-branch CI."""

import argparse
import json
import os
import stat
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from gammabench.ci import REPOSITORY, ingest, validate_bundle


def github(endpoint, *, publisher=False, payload=None, output=None):
    command = ["gh", "api", endpoint]
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
    environment = os.environ.copy()
    if publisher:
        environment["GH_TOKEN"] = environment["GAMMABENCH_PUBLISH_TOKEN"]
    result = subprocess.run(
        command,
        input=json.dumps(payload).encode() if payload is not None else None,
        stdout=output or subprocess.PIPE,
        check=True,
        env=environment,
    )
    return json.loads(result.stdout) if result.stdout else None


def extract(archive, destination):
    with zipfile.ZipFile(archive) as source:
        if sum(info.file_size for info in source.infolist()) > 512 * 1024 * 1024:
            raise ValueError("CI artifact exceeds the numeric evidence limit")
        names = set()
        for info in source.infolist():
            path = Path(info.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in info.filename
                or info.filename in names
                or stat.S_ISLNK(info.external_attr >> 16)
            ):
                raise ValueError("Unsafe CI artifact member")
            names.add(info.filename)
        source.extractall(destination)


def publish(args):
    endpoint = f"repos/{REPOSITORY}/actions/runs/{args.run_id}"
    run = github(endpoint + f"/attempts/{args.attempt}")
    if run["path"] not in (
        ".github/workflows/nixci-readiness.yml",
        ".github/workflows/performance-main.yml",
    ):
        raise ValueError("Unexpected measurement workflow")
    if (
        run["status"] != "completed"
        or run["head_repository"]["full_name"] != REPOSITORY
    ):
        raise ValueError("Only completed same-repository runs can be published")
    event = run["event"]
    if (
        event == "push"
        and run["head_branch"] == "main"
        and run["path"].endswith("performance-main.yml")
    ):
        channel, branch, pr, current_head = "main", "main", None, None
    elif event == "pull_request" and run["path"].endswith("nixci-readiness.yml"):
        pulls = run["pull_requests"]
        if len(pulls) != 1:
            raise ValueError("PR run has no unambiguous pull request")
        pr = pulls[0]["number"]
        pull = github(f"repos/{REPOSITORY}/pulls/{pr}")
        if pull["head"]["repo"]["full_name"] != REPOSITORY:
            raise ValueError(
                "Fork measurements are not eligible for licensed publication"
            )
        channel, branch, current_head = "pr", "pr-results", pull["head"]["sha"]
    else:
        print("No main-history or PR-preview publication for this run")
        return
    expected = {
        "repository": REPOSITORY,
        "candidate": run["head_sha"],
        "event": event,
        "run_id": args.run_id,
        "run_attempt": args.attempt,
        "pr": pr,
    }
    artifacts = github(endpoint + "/artifacts?per_page=100")["artifacts"]
    matching = [
        item
        for item in artifacts
        if item["name"] == f"gammabench-{args.run_id}-{args.attempt}"
    ]
    if not matching:
        if channel == "main":
            raise ValueError("Main measurement artifact is missing")
        print("No measurement artifact; the PR gate was skipped or did not start")
        return
    if len(matching) != 1 or matching[0]["expired"]:
        raise ValueError("Measurement artifact is ambiguous or expired")
    with tempfile.TemporaryDirectory(prefix="gammabench-publish-") as temporary:
        root = Path(temporary)
        archive, bundle = root / "artifact.zip", root / "bundle"
        with archive.open("wb") as stream:
            github(
                f"repos/{REPOSITORY}/actions/artifacts/{matching[0]['id']}/zip",
                output=stream,
            )
        extract(archive, bundle)
        validate_bundle(bundle, expected)
        askpass = root / "askpass"
        askpass.write_text(
            '#!/bin/sh\ncase "$1" in *Username*) printf "%s\\n" x-access-token ;; *) printf "%s\\n" "$GAMMABENCH_PUBLISH_TOKEN" ;; esac\n'
        )
        askpass.chmod(0o700)
        environment = os.environ | {
            "GIT_ASKPASS": str(askpass),
            "GIT_TERMINAL_PROMPT": "0",
        }
        checkout = root / "checkout"

        def git(*arguments, check=True):
            return subprocess.run(
                ["git", "-c", "credential.helper=", *arguments],
                cwd=checkout,
                env=environment,
                check=check,
                capture_output=True,
                text=True,
            )

        checkout.mkdir()
        git("init")
        git("remote", "add", "origin", "https://github.com/alphal00p/gammabench.git")
        git("config", "user.name", "gammabench publisher")
        git(
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        )
        for attempt in range(5):
            fetched = git("fetch", "--depth=1", "origin", branch, check=False)
            if fetched.returncode:
                if (
                    branch != "pr-results"
                    or git("ls-remote", "--heads", "origin", branch).stdout.strip()
                ):
                    raise RuntimeError("Could not fetch publication branch")
                git("checkout", "--orphan", f"publication-{attempt}")
                git("rm", "-rf", "--ignore-unmatch", ".")
            else:
                git("checkout", "-B", "publication", "FETCH_HEAD")
                git("reset", "--hard", "FETCH_HEAD")
            git("clean", "-fd")
            if pr is not None:
                current_head = github(f"repos/{REPOSITORY}/pulls/{pr}")["head"]["sha"]
            result = ingest(
                bundle,
                checkout,
                channel=channel,
                current_head=current_head,
                expected=expected,
            )
            if result["status"] == "unchanged":
                break
            git("add", "--all")
            git(
                "commit",
                "-m",
                f"record GammaLoop {run['head_sha'][:12]} performance ({args.run_id}/{args.attempt})",
            )
            pushed = git("push", "origin", "HEAD:refs/heads/" + branch, check=False)
            if pushed.returncode == 0:
                break
            if attempt == 4:
                raise RuntimeError(
                    "Publication push failed after five retries; retry this delivery"
                )
            time.sleep(min(attempt + 1, 4))
        # Dispatch even after duplicate delivery: this also repairs a previously
        # successful commit whose Pages notification failed.
        github(
            "repos/alphal00p/gammabench/dispatches",
            publisher=True,
            payload={"event_type": "ci-results", "client_payload": {"branch": branch}},
        )
        url = "https://alphal00p.github.io/gammabench/" + result["path"] + "/"
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as stream:
                stream.write(
                    f"[Performance results]({url}) — publication committed; Pages deployment requested.\n"
                )
        print(url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    args = parser.parse_args()
    if args.run_id <= 0 or args.attempt <= 0:
        parser.error("Run and attempt must be positive")
    publish(args)
