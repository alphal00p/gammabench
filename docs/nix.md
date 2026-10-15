# Packaged benchmarks and revision comparisons

A benchmark names an operation, its inputs, correctness checks and measured
quantities. It can exercise a function, a code region, a complete program or a
build. The common runner does not contain the implementation being measured.

Nix packages the harness, fixtures, validator and dependencies. Hyperfine 2.0.0
measures complete target processes. Internal code-region metrics are emitted by
the harness through `RESULT` and retain their own declared clocks. Setup,
observation and destruction belong to whichever boundary the definition states;
they are not inferred from the operation's name.

For example, the integer-sum harness parses JSON and allocates inputs before its
`compute_ns` clock. Hyperfine's separate `process_wall_ns` includes Python startup,
setup, computation, printing and teardown. The two values answer different
questions. A tensor-contraction or integration harness can use the same protocol
with its own setup and metrics.

The native Rust `rust-sort` example times only sorting an existing `Vec<u64>`;
allocation/shuffling, exact output checks and witness construction surround that
clock. Stable and unstable sorting share the same qualified output contract.

A specialised in-process benchmark harness can also export declared metrics; it need not pretend its clock is a process timer.

## Run a package

```sh
nix build .#integer-sum
nix run .#integer-sum -- snapshots/sum-run --repeats 15 --warmups 1
nix run .#gammabench -- verify snapshots/sum-run
nix run .#rust-sort -- snapshots/sort-run --repeats 15 --warmups 1
```

Nix realizes dependencies and builds the package before the runtime app starts.
The app then validates and runs the package's executable paths. Preparing the
harness and its dependencies happens before measurement. If the operation being
benchmarked is itself compilation, that compiler invocation belongs to its
declared runtime metric. The snapshot destination must be new. Measurements happen at runtime, never inside a cacheable derivation.

Packaged entrypoints clear inherited `LD_LIBRARY_PATH`, `DYLD_LIBRARY_PATH`,
`PYTHONHOME` and `PYTHONPATH` before starting the packaged interpreter. This keeps
host Python installations from changing its libraries or imported modules.
Explicit suite settings and `run --env` overrides apply afterwards and remain in
the snapshot. Other runtime variables, including private licensing, remain available.

The flake locks Nixpkgs, Crane, the Rust toolchain, Hyperfine source and measured
source inputs. Generation adapters use committed Cargo locks and offline Cargo
builds. Dependency-manifest changes require a deliberate lock update; a source
revision override does not silently resolve new dependency versions.

Available generation packages/apps are `generation-count`,
`generation-materialized`, `generation-signed-routed` and
`generation-full-public`. Their existing contracts and validators remain separate.
The full diagram clock includes local numerator fragments and excludes final
aggregate numerator multiplication.

## Change the measured revision

Override only the source input, keeping the benchmark, toolchain and other pins:

```sh
nix build .#generation-full-public \
  --override-input gammaloop github:alphal00p/gammaloop/FULL_COMMIT \
  --no-write-lock-file --out-link result-candidate
nix run .#generation-full-public \
  --override-input gammaloop github:alphal00p/gammaloop/FULL_COMMIT \
  --no-write-lock-file -- snapshots/candidate --repeats 15 --warmups 1
```

Use a complete commit ID. Substitute the same revision in both commands. These
commands change a revision within the declared upstream project. Source receipt
URLs identify that canonical project; actual commit and NAR hashes identify the
built tree. Fork-origin discovery is not implicit in this interface. Existing
packages are reused; source changes rebuild the affected outputs. An incompatible
public API requires an explicit harness adaptation, rather than patches to the
engine under measurement. Dependency locks may also need updating if that
revision changes its dependency requirements.

Each snapshot retains a checksummed Nix build receipt with actual source pins,
NAR hashes, toolchain/profile identities, executable paths and digests. The source
trees need no `.git` metadata at runtime. The original receipt is copied into the
sealed snapshot so it survives moving the results or garbage-collecting the
local Nix store. These receipts are retained evidence; the current website
history renderer does not yet consume their source-subject mappings to join new
captures into commit-history lines. Store paths identify built outputs; retaining
a path is not a promise that its contents are indefinitely available from a binary cache.

For controlled comparisons, build both revisions before measuring, run on the
same host with explicit thread/affinity settings, and preserve measurement order.
To compare two revisions in one balanced run, build both packages first, then
assemble their already realized definitions:

```sh
nix develop
# Build result-baseline and result-candidate with the respective revision overrides.
gammabench variants .gammabench/revision-pair \
  --variant baseline=result-baseline --variant candidate=result-candidate
gammabench run .gammabench/revision-pair/suite.json snapshots/revision-pair \
  --repeats 15 --warmups 1
gammabench report snapshots/revision-pair --baseline baseline \
  --output results/revision-pair-report
```

The assembly keeps each version's files and build receipt, and the existing
runner alternates their tasks within each measured round. Definitions with
incompatible suite IDs, contracts or metrics are rejected. Independent runs are
not automatically paired merely because their sample indices match. The reporter
retains its existing correctness and pairing gates.
When a package contains several implementation variants, select matching original
tasks with repeatable `--task` options, such as `--task sort-unstable`. Revision
labels must not collapse different algorithms into the same comparison column.

Nix fixes software inputs; hardware, load and runtime configuration remain part
of each measurement's evidence.

Symbolica licensing is supplied privately at runtime. It must not appear in
flake inputs, derivation environment variables or public receipts. QGRAF source
and binaries are obtained under upstream terms and are not published through
this repository or a public binary cache.

## Add another kind of benchmark

`lib.mkBenchmark` accepts a normal suite definition and its packaged files. The
suite declares argv, input files, required output, validator and arbitrary
numeric metrics with units, direction and scope. No graph-generation-specific
fields are required. A benchmark can provide several tasks, workloads or metrics.

Supply immutable executable tool paths and dependencies from Nix derivations.
Declare source identities, engine-to-source subjects and a build receipt's
comparison context separately. The context should include fixed dependencies,
harness/validator hashes, compiler, target, features and optimisation profile.
Keep full executable/store identities as evidence even when comparing a changing
subject revision. Never drop a changed dependency just to join a history line.

Build-only checks may compile the harness and validate its definition. Performance
measurements stay in the generated runtime app. The integer-sum package in
`nix/default.nix` demonstrates the generic definition; `nix/generation.nix` is one
benchmark family's implementation.

## Measurement backend

The runner retains balanced alternating rounds. It invokes Hyperfine with one
shell-free run for each scheduled sample; Hyperfine's ordinary multi-command mode
runs each command's full series consecutively. Warmups remain explicit separate
records. The raw schema-2 JSON is preserved per sample, and successful wall time
comes from the individual measurement rather than a summary of failed runs.

Hyperfine startup/export time is excluded from its process metric. The controller's
elapsed duration, PID and exit are retained separately; Hyperfine does not report
the target PID. Failed, invalid and timed-out runs have no successful metric
values. An external watchdog terminates the process group on timeout. Raw stderr
contains target diagnostics and Hyperfine diagnostics.

For legacy framework development without Hyperfine, `--backend python` explicitly
selects the old process clock. Production Nix apps select pinned Hyperfine 2.0.0.
Do not silently substitute that fallback in a comparison.
