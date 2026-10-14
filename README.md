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

Requires Python 3.11 or later. The framework uses only the standard library.

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


## GitHub Pages and language boundaries

The published site serves precomputed reports and their evidence. Browser filters
select case, baseline engine/variant, metric and status; both ratio directions are
computed from qualified raw sample pairs before publication. The browser does
not recompute statistics or relax correctness gates.

HTML, CSS and JavaScript live in `src/gammabench/assets/`. The Python report
module derives analysis and fills the template; it does not implement a web
server. The benchmark engines and kernels are Rust/native programs. Python
currently handles the runner, source acquisition, snapshot integrity, analysis
and historical imports using the standard library. Those responsibilities are
not tied to Python: the versioned JSON protocol can be consumed by a future Rust
CLI without changing the browser or archived result formats.

The Pages workflow copies the existing reports and snapshots into a static
artifact using Bash and GitHub Actions. It does not install or run Python.
To preview the same artifact locally:

```sh
bash scripts/build-pages.sh .gammabench/pages-preview
python -m http.server --directory .gammabench/pages-preview
```

The server command is optional local convenience; GitHub Pages hosts the static
files. Keep the repository-relative report/evidence layout when copying the
artifact, since report links point to the original sealed snapshot files.
