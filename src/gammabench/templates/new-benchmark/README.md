# @@TITLE@@

This is a **runnable starter**, registered in the `@@FAMILY@@` family. It measures
an exact vector transform, not a domain-specific performance claim. Replace the
operation, fixtures, exact correctness checks, output contract and timer scope
together before collecting results for the intended benchmark.

`compute_ns` times the operation on an existing vector through retention of its
output. Input parsing/allocation, exact validation, printing and destruction are
outside that clock. Hyperfine 2.0.0 separately measures complete process lifetime.

```sh
# Git-backed Nix flakes include tracked files: stage this scaffold first.
git add suites/@@BENCHMARK_ID@@ nix/@@BENCHMARK_ID@@.nix web/benchmarks.json
nix build .#@@BENCHMARK_ID@@
nix run .#@@BENCHMARK_ID@@ -- snapshots/@@BENCHMARK_ID@@-run --repeats 15 --warmups 1
nix run .#gammabench -- verify snapshots/@@BENCHMARK_ID@@-run
nix run .#gammabench -- report snapshots/@@BENCHMARK_ID@@-run \
  --output docs/results/@@BENCHMARK_ID@@-run --register @@BENCHMARK_ID@@
```

In a jj checkout, `jj status` snapshots new files without the Git staging step.

The catalog creates the benchmark's Nix package/app and planned website page.
Registering a report retains the captured evidence and adds its results to that
page. It does not publish or push the repository.

`nix/@@BENCHMARK_ID@@.nix` packages the harness, workload and dependencies. For an
upstream-library benchmark, add its locked flake source and record `sources` and
engine-to-source `subjects` in `mkBenchmark`. These receipts supply actual source
commits for history plots; this self-contained starter does not invent a commit.
Use `--override-input` for revisions and `gammabench variants` for paired runs, as
described in `docs/nix.md`. Measurements happen in the runtime app, outside builds.
