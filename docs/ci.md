# CI gates and PR previews

GammaLoop supplies its fixture manifest, cards and numeric adapter. Gammabench
owns the paired schedule, regression policy implementation, sealed evidence and
publication. A prepared `runner: paired-gate-v1` suite has exactly two tasks,
labelled `baseline` and `candidate`, with matching case, contract and inputs.
All executables must already exist and have SHA-256 pins. Build time is outside
the measurement budget.

```sh
gammabench gate prepared/suite.json results/case \
  --policy prepared/policy.json --private-work-dir private/case
```

The policy declares schema version 1, five pairs, a total budget of at most 240
seconds by default, relative/absolute thresholds for each metric, output groups, a reference
group and numerical tolerances. Five discovery pairs alternate AB/BA; a regression
signal requests five confirmation pairs beginning BA under the original deadline.
Confirmation is retained separately and never pooled into discovery statistics.
The MAD-based screening policy and its absolute floors are preserved from
GammaLoop's schema-2 comparator. Exit codes are 0 for pass, 1 for a confirmed
regression or correctness mismatch, and 2 for incomplete, invalid or inconclusive
evidence.

Each adapter invocation receives a new private work directory, output path and
shared monotonic deadline. Its result contains `metrics`, `outputs` and numeric
`metadata`. Outputs are complex-point vectors grouped by implementation. Each
group must agree with its own reference and the baseline reference within the
declared tolerance; every reference point must be finite and nonzero. Reports
recompute this qualification from retained records rather than trusting a pass
flag. This is numerical qualification, not an exact symbolic equivalence proof.

An explicitly selected `budget_class: "slow"` permits a bounded case budget up
to 7200 seconds. It does not change the default fast limit, pair count, shared
deadline, thresholds or confirmation procedure. Preparation performed by the
adapter remains inside that shared deadline even when it is outside a reported
native metric.

Policies may opt into `correctness_format: "decimal"`. Every output component
must then be a finite decimal string (at most 1024 characters); metrics and
metadata remain finite JSON numbers. Qualification uses decimal arithmetic,
without passing small components through binary64. Invalid strings, extreme
exponents and arithmetic underflow fail qualification. The original strings
remain in sealed records and are used again when qualifying a loaded snapshot.

An adapter with an independently established analytic zero may declare
`certified_zero` with a `certificate` object containing an identifier `id` and
complete `sha256`, plus a `components` list of distinct `index` and
`absolute_bound` entries. Bounds use the output's number/decimal format. Every
adapter result must carry the exact same `certificate`; missing or different
witnesses fail. The retained adapter owns the domain proof, physical dimensions,
source/settings checks and witness construction. Gammabench checks each declared
component against its zero bound separately for each side and output group,
including when two contaminated values agree. Other components retain the
ordinary relative/absolute comparison. Reports preserve raw outputs,
`strict_correctness_mismatches` before the certificate and the number of
`certified_zero_components`. No component is rounded or replaced with zero.

For example, a dimensional source proof can produce this numeric contract:

```json
{
  "correctness_format": "decimal",
  "certified_zero": {
    "certificate": {"id": "source-zero-v1", "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"},
    "components": [{"index": 0, "absolute_bound": "1e-150"}]
  }
}
```

The bound is illustrative; the suite must derive its own bound and retain the
supporting inputs. A policy declaration alone is not proof of the physical zero.

The generic process runner remains unchanged by default. Gate suites explicitly
declare their clocks and do not acquire an implicit whole-adapter process metric.
GammaLoop generation uses Hyperfine 2.0.0 around the CLI process, with raw exports
verified against the declared command and numeric metric. Native evaluator-build
and warmed per-sample timings retain their clocks. These generation timings have
a different scope from the earlier Python-controller measurements.

Public snapshots retain inputs, adapter source, numeric records, requested order,
failures, checksums, measured commits, executable hashes, build receipts and host
conditions. GammaLoop states and process logs stay outside the public destination.
Licenses and authentication are inherited at runtime and never declared as suite
environment overrides. Snapshot destinations and private directories must be new.

## Publication

The GammaLoop default-branch publisher validates the originating Actions run and
downloads the named attempt's artifact. It does not execute candidate scripts.
`GAMMABENCH_PUBLISH_TOKEN` is a fine-grained token restricted to the gammabench
repository with Contents read/write, stored as a GammaLoop Actions secret. The
ordinary `GITHUB_TOKEN` reads the source artifact; the publisher token writes
gammabench and sends `repository_dispatch: ci-results`.

Main-push captures append to gammabench main using the existing snapshot/report
registration. PR captures append to the data-only `pr-results` branch, under
`previews/pr-N/CANDIDATE/RUN-ATTEMPT/`. The PR's catalog is separate from main.
The branch index selects the newest eligible capture for the current PR head.
Old, failed and inconclusive captures remain accessible. A merged PR is not
relabeled as a main measurement: main runs again against the previous main SHA.

History shows the candidate's discovery measurement for each run. Baselines
and independent confirmations remain in Compare and Evidence; neither is
substituted into the candidate trend. Failed or unqualified candidate captures
stay visible as gaps. Captures are ordered by their recorded measurement dates,
and changes to hardware, workloads or fixed measurement conditions split lines.

The CI history overview at `ci-history/` plots several selected workloads on
one chart. Its metric selector requires matching metric IDs, units, scope and
direction; each workload has its own line and observed ranges. Workload
checkboxes are saved in the URL. A point links to that workload's original
comparison set. The overview uses retained values without creating new ratios
between independent captures or rewriting any sealed evidence.

Each publication commits atomically, retries Git push races against the latest
branch, rejects conflicting duplicate identities, and accepts identical retries.
To recover a failed publication, manually dispatch GammaLoop's **Publish
performance results** workflow with the original run ID and attempt before its
14-day transport artifact expires. This does not rerun the benchmark. Repeating
publication also resends the Pages notification. Evidence committed to the
results branches is durable and has no automatic expiration.

One serialized Pages workflow reads the latest main and preview branch contents
and assembles the complete site. Previews never deploy a partial artifact over
the main site. A local combined build is:

```sh
bash scripts/build-pages.sh .gammabench/site
PYTHONPATH=src python3 -m gammabench.ci assemble-previews \
  PATH_TO_PR_RESULTS_CHECKOUT .gammabench/site --framework "$PWD"
```

Preview sources are data only; rendering uses the trusted framework's Typst
templates. Each PR has a landing page, permanent per-capture links and a separate
explorer with its comparisons and evidence. CI summaries link to those URLs;
publication errors are separate from numerical gate outcomes.
