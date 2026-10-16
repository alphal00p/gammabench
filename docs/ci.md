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
By default a numerical mismatch already fails the gate and stops further timing
confirmation. An explicit `confirm_on_correctness_failure: true` retains the
independent confirmation when a timing regression signal accompanies that
mismatch; the numerical failure remains a failure throughout.

Policies may opt into `correctness_format: "decimal"`. Every output component
must then be a finite decimal string (at most 1024 characters); metrics and
metadata remain finite JSON numbers. Qualification uses decimal arithmetic,
without passing small components through binary64. Invalid strings, extreme
exponents and arithmetic underflow fail qualification. The original strings
remain in sealed records and are used again when qualifying a loaded snapshot.

An adapter with an independently established analytic zero may declare
`certified_zero` with a `certificate` object containing an identifier `id` and
complete `sha256`, plus a `components` list of distinct `index` and
`absolute_bound` entries. Bounds use the output's number/decimal format; decimal
bounds may contain up to 2048 characters to retain full derivation precision. Every
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

The trusted publisher must support the numerical policy used by the measurement
before that capture can be published. It recomputes every sealed phase using its
own framework code; it never loads executable code from the artifact or fetches
the artifact's declared framework revision to execute it. Early schema-2 reports
without the two additive certificate diagnostics remain valid only for ordinary
numeric qualification without a certificate. All recorded outcomes and numeric
statistics must still agree exactly, and partially missing diagnostics fail.

Each case's candidate must match the originating run's candidate commit. Its
measured baseline may be the run's base commit or the candidate commit itself,
as retained consistently in the suite and sealed gate identity. The latter is a
comparison within the candidate, such as two implementation modes at one commit;
the summary labels it explicitly and records the actual measured baseline. The
run's base SHA remains source provenance and is not substituted into that case.
Unrelated measured revisions are rejected. Discovery and confirmation must keep
the same policy and measured identities for either kind of comparison.

History in the standard benchmark list shows the candidate's discovery
measurement for each run. Baselines and independent confirmations remain in
Compare and Evidence; neither is substituted into the candidate trend. Each
tool and implementation has its own capture/version axis. Context changes split
solid lines, failed or missing captures retain their evidence, and CI host-only
changes have dotted **Runner changed** connectors. Retained paired baseline ratios
assess software changes; independent history points do not establish a speedup.

PR links open this same list in History and include the published main history.
Hollow diamonds marked **Proposed PR #N** distinguish the PR candidate. A dashed
connector leads to the actual measured base only when it exists in the same
measurement context. Comparisons within the candidate use that candidate as the
measured baseline, without acquiring a misleading connector to main's base SHA.
All source commits, capture dates, original pairing identities and evidence links
remain available. Unqualified or unavailable PR captures remain explicit rather
than falling back to a historical main point.

The **Pinned measurements** workspace is above search and the list. Pins identify
exact metric measurements, including their snapshot, task, source version and
phase. Filtering and switching History/Compare do not replace them. Selecting a
compatible subset shows stored paired multipliers with a 1× reference, or only
absolute values for matching independent contexts. Different workloads or
conditions stay in separate panels. No samples are pooled and no browser
statistics or ratios are recomputed. Discovery and confirmation remain separate
comparison sets. Expand **Evidence and captured values** to inspect both sides,
failed cases, source reports, sealed snapshots and checksums.

The stable `previews/pr-N/` URL opens the latest eligible capture. **All runs for
this PR** lists older captures, and **Run summary** retains gate outcomes, source
commits and each case's actual measured identities. Archived previews compose
main's currently published CI history with their own proposed capture. Preview
catalogs and sealed evidence remain separate from main; the combined list is a
build artifact. Existing benchmark and `/ci-history/` browsing links redirect to
list selections with their query state preserved; original reports retain their
URLs. After merge, main records a fresh measurement under its actual commit.

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
templates. Each PR has the standard inline History/Compare list, permanent per-capture
links and expandable evidence. CI summaries link to those URLs;
publication errors are separate from numerical gate outcomes.

History axes and captured-value rows link to their originating PRs. Preview links
use the PR identity already retained in CI provenance. Main links use GitHub's
commit-to-PR associations, restricted to merged PRs into the source repository's
main branch. The Pages workflow resolves new commits and caches validated JSON;
reviewed annotations in `web/source-links.json` also support offline builds.
Lookups never modify snapshots or reports. API failures retain known links and
leave unresolved commits linked to their source commits. A direct main push can
therefore have a commit link without a PR link.

To refresh the display annotations locally with the GitHub CLI:

```sh
PYTHONPATH=src python3 -m gammabench.source_links --repo . \
  --cache .gammabench/source-links.json --refresh
```
