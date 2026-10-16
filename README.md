# gammabench

Browse the [published results](https://alphal00p.github.io/gammabench/).

Reproducible benchmarks for GammaLoop and related tools. Search the published
case catalog by keywords and controlled metadata, then open a case to compare
tools, ways and source versions. A suite declares the
workloads, pinned tools, required output and measurement scopes. Each execution
produces an immutable, verifiable snapshot. Reports derive tables, comparisons,
CSV and analysis from those snapshots without changing the measurements.

The framework is generic: graph generation is the first suite, and the same
protocol can host integration, symbolic algebra, tensor contraction, drawing,
compilation and other measurements.

GammaLoop CI uses `gammabench gate` for fresh paired performance checks. Main
captures join the benchmark history; premerge captures have separate
[PR previews](https://alphal00p.github.io/gammabench/previews/). See the
[CI gate and publication protocol](docs/ci.md) for local execution, private
adapter outputs, publishing credentials, and recovery.

## Quick start

Use the Nix packages for a locked benchmark, dependencies and Hyperfine 2.0.0:

```sh
nix run .#integer-sum -- results/demo --repeats 15 --warmups 1
nix run .#gammabench -- verify results/demo
```

See [Nix benchmarks and revision overrides](docs/nix.md). The package is built
before measurement; timing runs execute its realized store executables. Each
benchmark declares its operation, setup, checks and metric boundaries. The example
measures an internal summation region as well as complete process lifetime.

For direct development, use Python 3.11 or later, Hyperfine **2.0.0** on PATH and
Typst 0.15.1. Python orchestration and analysis use only the standard library.

```sh
python -m pip install -e .
gammabench validate suites/examples/sum.json
gammabench run suites/examples/sum.json results/demo --repeats 15 --warmups 1
gammabench verify results/demo
gammabench report results/demo --output results/demo-report --baseline loop
```

Open `results/demo-report/index.html` for the offline interactive report. The
same analysis is exported as JSON and CSV. The example is a framework demo;
it is not a physics performance claim. Without installing the package, use
`PYTHONPATH=src python -m gammabench.cli` in place of `gammabench`.

## Benchmark protocol

- **Definitions:** suites own cases, inputs, correctness checks, argv commands,
  output contracts and metric scopes. Runs never invent equivalent output from
  equal counts.
- **Versions:** Git sources use complete commit IDs. Downloaded source archives
  use SHA-256 pins. Actual executable hashes and version output are captured.
  A local checkout must match its pin; dirty experiments require explicit
  opt-in and a content archive.
- **Snapshots:** raw output, input copies, exits, failures, timeout records,
  sample order, host, tool bindings and checksums stay together. Existing
  snapshots cannot be overwritten. Verification works after moving a snapshot.
- **Analysis:** warmups and validation runs are excluded. Paired ratios use
  matched rounds with the same case, output contract, metric unit/scope and
  semantic validation. Different outputs and missing results are disclosed.

See [the protocol](docs/protocol.md), [the suite format](docs/suites.md),
[searchable metadata and implementation identities](docs/metadata.md), and
[the generation suite](suites/generation/README.md).

## Create and register a benchmark

```sh
nix run .#gammabench -- new-benchmark my-operation --family tensor-contraction
jj status  # Git checkouts: add the generated suite, recipe and catalog before Nix builds.
nix run .#my-operation -- results/my-operation --repeats 15 --warmups 1
nix run .#gammabench -- report results/my-operation \
  --output docs/results/my-operation --register my-operation
```

The command creates a native Rust starter, inputs, suite JSON, Nix recipe and
instructions. Its catalog entry supplies the Nix package/app automatically and
appears on the site as planned. Minimal controlled metadata lives in its suite,
with fields and values validated before execution. Replace the starter operation, timing contract
and exact checks with the intended benchmark. Report registration retains sealed
evidence under `snapshots/measured/`, renders the report and appends its catalog
paths. Existing files and captures are never overwritten.

Nix source receipts supply commit-history points, including composed revision
comparisons. Histories join only compatible build, workload, correctness and
explicit runtime contexts. A starter has no invented Git source; wire its measured
source and subject mapping into the recipe when benchmarking an upstream library.
See [the complete workflow](docs/nix.md).

## Pin and acquire tools

`tools.lock.json` records the tool revisions used by the preserved generation
studies. Resolve only the tools needed by a suite:

```sh
mkdir -p .gammabench
gammabench sources tools.lock.json --tool gammaloop --cache-dir .gammabench/sources > .gammabench/resolved-tools.json
```

The generation preparation command builds drivers from those source bindings
and writes executable pins into a runnable suite. It does not copy GammaLoop's
workspace packages into this repository. QGRAF source/binaries are obtained
separately from upstream or an explicitly supplied archive; they are not
redistributed here. Symbolica credentials stay in the runtime environment.

To benchmark another GammaLoop revision, update the complete `revision` in a
copy of the pin file, resolve it, and build a driver for that version. Preserve
the suite contract when comparing it with an earlier snapshot. API changes
may require a version-specific adapter; its source belongs to that snapshot's
provenance too.

## Historical measurements

The graph-generation studies imported under `snapshots/historical/` preserve
original run IDs, tool/source identities, correctness-gate provenance and timing
boundaries. They are marked `historical-import`, not fresh runs of gammabench.
Older experiments with private injected constructors remain historical evidence;
new drivers use public APIs. Full rich diagrams and signed/routed records have
separate contracts and are not directly ranked against each other.

Large raw profiles and qualification dumps are preserved in the
[v0.1.0 evidence release](https://github.com/alphal00p/gammabench/releases/tag/v0.1.0),
with an [artifact index](artifacts.json) and SHA-256 inventories. Downloaded
engines and build outputs remain outside Git history. Committed snapshots retain concise measurements, hashes and original
artifact indexes. A missing external artifact is reported as unavailable;
its hash is not proof that it can still be downloaded.

## Add a suite

Start from `suites/examples/sum.json`. A command may emit a single JSON object
on a line beginning `RESULT ` to provide application metrics and semantic
witnesses. Pinned Hyperfine also measures total process lifetime. Declare these
scopes independently. An explicit validation command can do expensive equivalence work before
measurement. Otherwise tasks with checks run their normal command once as an
untimed validation sample. Benchmark-specific physics remains in the measured engine or
its suite adapter, not in the common runner.

## Development

```sh
nix develop
PYTHONPATH=src python -m unittest discover -s tests -v
```

This supplies pinned Hyperfine and Typst plus Node for the JavaScript query
semantics tests. Node is a development/test tool; the published site is static. The explicit Python backend remains available for legacy framework tests.

The framework and authored adapters are MIT licensed. Fixture and third-party
provenance is retained separately; upstream tools keep their own terms.

Compare engines with `--baseline engine:linnet`, or implementation variants
with `--baseline baseline`. See [the preserved generation results](docs/results/README.md).


## GitHub Pages and Typst

The [published site](https://alphal00p.github.io/gammabench/) uses GammaLoop's
public Pages palette with compact, single-font monospace typography. The upstream CSS, JavaScript, marks and local fonts are
vendored unchanged at a complete revision with SHA-256 provenance and licenses
in `src/gammabench/assets/theme/`.

The landing page is a generic benchmark explorer. Keywords search case names,
descriptions and metadata; **Add filter** offers declared fields and typed
conditions. Typing also suggests controlled tags, values and fields; choosing a
value adds a filter, while choosing a field opens its value control. Filter chips,
grouping and query URLs work across benchmark families.
Capture conditions always match the same retained configuration. Unknown and
not-applicable values remain distinct. The registered framework examples are
clearly labelled and have no published captures until measurements are registered.

Each case opens a stable benchmark workspace with **History**, **Compare** and
**Evidence**. Old root links still route to their selected benchmark and case.
The explorer and reports use one monospace font and responsive layouts.

History shows small workload/implementation charts with aligned measured commit
columns. CI workloads from the same Actions run and attempt share one position;
reruns keep separate positions. Other captures retain their snapshot and source
identities. Columns follow capture order, with proposed PR captures in a separate
final column. Exact timestamps remain in tooltips and evidence.

Pin charts to overlay their captured medians and observed min/max ranges on a
shared scale. Mini charts use individual vertical scales. Fixed configurations
remain separate solid segments. CI hardware changes have dotted connectors
labelled **Runner changed**, with CPU and runner image details in point tooltips.
These connectors show capture order; the paired baseline ratios assess software
changes. A single history appears once at full size. The shared comparison
appears only when at least two histories are pinned. Missing, unsuccessful,
unqualified and undated captures break lines. Archive-only, dirty or missing pins
remain unplotted evidence. A source commit date is never inferred from a capture.
Ranges are not confidence intervals.

CI histories use each run's candidate discovery measurement, so successive
main commits trace the evolution of GammaLoop. Paired baselines and independent
confirmation measurements remain in Compare and Evidence. The
[CI history overview](https://alphal00p.github.io/gammabench/ci-history/) shows
all recorded workloads for one metric, unit and scope. Pin selections and
the selected measurement are retained in the URL; clicking a point opens its
original paired comparison.

PR previews open History by default and include the published main history.
The PR candidate appears as a hollow diamond labeled **Proposed PR #N**; a
dashed connector links it to its recorded base when measurement contexts match.
Main's trend and the original capture dates remain intact. Any baseline ratio
comes from the stored paired discovery comparison, not independent history
points. **Measurement details** and **Captured values and provenance** hold the
qualification rules and retained evidence. **Run summary** and
**All runs for this PR** retain access to gate outcomes and older captures.
Preview points do not enter main's catalog; merging triggers a fresh main run.

Compare groups workloads against tools, ways and measured versions. Select an
original recorded comparison set and exact reference. Cells use the stored paired
relative result, with the multiplier prominent and the captured median underneath.
Green means better, yellow equal, red worse. The fixed colour
scale saturates at a twofold difference. Latest independent captures show absolute
values without ratios or ranking colours. No browser statistics are recomputed.
Hide a column with ×; **Columns** restores it. Hiding the reference clears ranking
until explicit reselection. Per-set visibility is retained in the URL.

Evidence links to retained reports, analysis, CSV, snapshot records and checksums.
The workspace does not rewrite captures or create cross-capture paired comparisons.
Existing complete reports remain available at their original URLs.

The workspace and explorer are authored in `web/main.typ` and `web/explorer.typ`. The versioned
[`web/benchmarks.json`](web/benchmarks.json) registry declares families, stable
benchmark IDs, definition versions, default metrics/workloads and paired arrays of retained
`analyses`/`reports` paths. Add a benchmark there after generating its report; append
captures to the same entry when the benchmark definition remains compatible.
An optional controlled metadata overlay can annotate legacy captures; exact
fixture annotations retain input hash bindings. It cannot contradict known
captured metadata. See [the metadata protocol](docs/metadata.md).
Keep a new definition under a new benchmark ID when its meaning changes. The
first registered snapshot anchors the display identity, so retain capture order.

Nix captures use their retained source-to-engine subject mapping, including the
original receipts inside a composed revision comparison. The plotted commit is
the measured source revision. Changing adapter binaries remain evidence; fixed
helpers, inputs, compiler/profile, metric/contract, validation witness, controller,
hardware and declared runtime settings define separate configurations. Missing
pins, dirty sources and archive-only revisions retain explicit unplotted evidence.

For older captures, `subjects` maps engine IDs to measured tool bindings and
optional repository links. Historical profile provenance takes priority over a
`capture_modes` declaration. Captures without adequate configuration metadata
remain separate. Neither the browser nor Typst invents cross-capture pairing.

Report content, controls and evidence are authored in
`src/gammabench/assets/report.typ`, `matrix.typ` and `history.typ`, with a shared
semantic HTML boundary in `components.typ`. Pinned Typst 0.15.1 compiles these to
ordinary static HTML. CSS handles responsive layout and light/dark styling;
JavaScript switches views and plots the frozen values. No browser Python runtime
or application server is needed.

Python produces the offline analysis JSON and CSV using the existing correctness
gates. Typst consumes that JSON and a separate relative-link map; it does not
rerun measurements or recompute statistics. Both ratio directions are computed
from qualified raw sample pairs before publication. Every generated report retains
its Typst sources, analysis, link map, compiler version and checksummed assets,
so the presentation can be rebuilt directly from the report directory.

The benchmark engines and kernels remain Rust/native programs. Python currently
handles orchestration, source acquisition, snapshot integrity, analysis and
historical imports. The versioned JSON protocol allows those responsibilities to
move to another language without changing archived result formats or Typst pages.

The Pages workflow validates and projects the catalog using the framework’s
Python standard-library code, then compiles authored pages with pinned Typst and
copies precomputed reports and sealed snapshots into the static artifact.
Presentation remains Typst, CSS and browser JavaScript; there is no browser Python
runtime or application server. To preview the same artifact locally:

```sh
bash scripts/build-pages.sh .gammabench/pages-preview
python -m http.server --directory .gammabench/pages-preview
```

The server command is optional local convenience; GitHub Pages hosts the static
files. Preserve the repository-relative report/evidence layout when copying the
artifact, since report links point to the original sealed snapshot files.
