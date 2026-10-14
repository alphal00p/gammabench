# gammabench

Browse the [published results](https://alphal00p.github.io/gammabench/).

Reproducible benchmarks for GammaLoop and related tools. A suite declares the
workloads, pinned tools, required output and measurement scopes. Each execution
produces an immutable, verifiable snapshot. Reports derive tables, comparisons,
CSV and analysis from those snapshots without changing the measurements.

The framework is generic: graph generation is the first suite, and the same
protocol can host integration, symbolic algebra, tensor contraction, drawing,
compilation and other measurements.

## Quick start

Requires Python 3.11 or later for running benchmarks and analysis, and Typst
0.15.1 for report generation. The Python framework uses only the standard library.

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

See [the protocol](docs/protocol.md), [the suite format](docs/suites.md), and
[the generation suite](suites/generation/README.md).

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
witnesses. The runner also measures total process lifetime. Declare these
scopes independently. An explicit validation command can do expensive equivalence work before
measurement. Otherwise tasks with checks run their normal command once as an
untimed validation sample. Benchmark-specific physics remains in the measured engine or
its suite adapter, not in the common runner.

## Development

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

The framework and authored adapters are MIT licensed. Fixture and third-party
provenance is retained separately; upstream tools keep their own terms.

Compare engines with `--baseline engine:linnet`, or implementation variants
with `--baseline baseline`. See [the preserved generation results](docs/results/README.md).


## GitHub Pages and Typst

The [published site](https://alphal00p.github.io/gammabench/) uses GammaLoop's
public Pages palette with compact, single-font monospace typography. The upstream CSS, JavaScript, marks and local fonts are
vendored unchanged at a complete revision with SHA-256 provenance and licenses
in `src/gammabench/assets/theme/`.

The site is a benchmark workspace. Choose a benchmark in the family navigation,
then use **History**, **Compare** or **Evidence**. Each benchmark has a stable
static URL and loads only its own captures. Families without captures are labelled
Planned. Links retain the selected view, workload, metric and reference.

History plots captured medians and observed min/max ranges against the actual
measured tool commits. It separates workload inputs, metric scope/unit/direction,
output contract, hardware, capture mode and fixed dependencies. Archive-only
sources, dirty or missing pins, failed and censored results remain evidence rather
than fabricated commit points. A single revision is shown as one point, and two
revisions remain a two-point history. Ranges are not confidence intervals.

Compare shows workloads as rows and tool/implementation versions as columns.
Select the measured stage and exact reference. Cells show readable units and the
stored paired relative result. Green means better than the reference, yellow is
equal, and red means worse; the fixed colour scale saturates at a twofold
difference. Unqualified, missing and ambiguous captures remain neutral with their
reasons. Colour compares one workload to its reference, rather than unrelated
absolute workload costs. All reference directions come from precomputed analysis;
the browser never inverts a median or computes a new statistic.

Evidence links to retained reports, analysis, CSV, snapshot records and checksums.
The workspace does not rewrite captures or create cross-capture paired comparisons.
Existing complete reports remain available at their original URLs.

The workspace is authored in `web/main.typ`. The versioned
[`web/benchmarks.json`](web/benchmarks.json) registry declares families, stable
benchmark IDs, definition versions, default metrics/workloads and paired arrays of retained
`analyses`/`reports` paths. Add a benchmark there after generating its report; append
captures to the same entry when the benchmark definition remains compatible.
Keep a new definition under a new benchmark ID when its meaning changes. The
first registered snapshot anchors the display identity, so retain capture order.

`subjects` maps engine IDs to measured tool bindings and optional source-repository
links. Normal captures with different task IDs are conservatively separate unless
`capture_modes` explicitly maps each task ID to the same capture mode (for example,
`"timing"`). Use different modes for instrumented and native execution. Historical
explicit profile provenance takes priority. Unknown modes remain separate; do not
declare equivalence just to obtain a line. Normal analysis metadata does not yet
expose complete task environment/build settings, so its points remain separate by
snapshot and task even when a mode is declared. Historical lines require the
retained controller identity as well as matching context. These are presentation declarations;
original source pins and measurement definitions still come from each snapshot.

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

The Pages workflow compiles the landing pages with pinned Typst, then copies
precomputed reports and sealed snapshots into the static artifact. It does not
install or run Python. To preview the same artifact locally:

```sh
bash scripts/build-pages.sh .gammabench/pages-preview
python -m http.server --directory .gammabench/pages-preview
```

The server command is optional local convenience; GitHub Pages hosts the static
files. Preserve the repository-relative report/evidence layout when copying the
artifact, since report links point to the original sealed snapshot files.
