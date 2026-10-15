# Result and execution protocol

A suite is the unit of a scientific comparison. Every task names its case,
engine, variant, required output contract, selected tool pins, command, input
files and explicit environment overrides. The contract states included and
excluded work. Metric scopes are separately declared: application completion
can stop before observation or destruction while process lifetime includes
both. Software instruction/allocation profiles can cover a wider interval.
Their values must not be presented as completion wall time or added as phases.

## Correctness before ranking

Validation commands execute outside timing. Expected result fields and
semantic witnesses accompany measurements. Every ranked pair must be
qualified under the same output contract; counts alone cannot establish
identity, signs, symmetry factors, routing or full object equivalence.
A suite may need a dedicated exact cross-engine validator. Failed or absent
qualification produces a suppressed comparison, not a successful speedup.
Imported studies preserve their original gate proof rather than rerunning it.
CI gate suites may instead declare numerical tolerances and reference output
groups. Their retained proof covers only the explicitly measured pair, is
recomputed from its numeric records, and is labelled numerical qualification.
It does not establish exact symbolic identity or cross-capture pairing. See
[CI gates](ci.md).

## Versioned tools

Git source IDs are complete commits, not mutable branch names. An executable
is bound by its content hash and any supplied version command. Source archives
are verified before extraction. Local source overrides retain their declared
revision and must be clean by default. Dirty opt-in captures the actual content
with a source archive. Sources and executables are checked before and after a
run; changed inputs invalidate it. Actual toolchains and host capabilities
are recorded separately from the engine version.

## Schedule and clocks

Warmups precede measured rounds. Within each case and contract, task order
rotates/reverses to reduce order bias. Each sample is a fresh subprocess. Its input files start as independent copies
of the retained original inputs. A benchmark may modify those working files
as part of an in-place operation; its post-run files are preserved too. The
retained original inputs and adapter sources must remain unchanged.
Optional CPU affinity uses available CPUs and is recorded; it does not reserve
a core. Shared-host measurements do not claim global idleness. The default process
clock is Hyperfine 2.0.0's monotonic clock around target spawn and wait. Each
scheduled sample invokes Hyperfine once with one run, an explicit shell-free
command and no nested warmups, retaining the raw schema-2 JSON. Hyperfine startup
and JSON export are outside its target clock. Controller elapsed time and PID are
recorded separately; an unknown target PID remains null. Hyperfine diagnostics
share the retained stderr stream with the benchmark. The explicit legacy Python
backend preserves the former process clock for development and is identified in
provenance; its timing implementation is not silently substituted for Hyperfine.

Hyperfine exhausts one command's repetitions before moving to the next, so the
common runner keeps the balanced schedule rather than labelling independent
sequential series as paired rounds. Application
clocks are emitted by the benchmark driver and have declared scopes.

Timeouts terminate the sample process group and retain its raw output and
status. Failed, invalid and censored rows have no successful metric values.
Never substitute zero or silently drop them. Validation and warmup rows remain
in the snapshot but do not enter measurement summaries. Observed min/max
ranges are not confidence intervals. Paired comparisons use matching complete
rounds and display their provenance; unmatched or incomplete pairs are suppressed.

## Immutable snapshots

The runner refuses an existing destination. It writes an unfinished run while
execution is active, then seals final JSON, raw outputs and copied inputs with
`files.sha256.json`. Snapshot IDs, original suite definitions, resolved tools,
host, schedule, actual commands/overrides, process IDs/exits and timestamps
travel together. Verification rejects changed, missing or unexpected files.
Copying the directory preserves verifiability. The inventory is an integrity
check, not a signature asserting scientific correctness.

Analysis writes a separate destination. It must not mutate a sealed snapshot.
Historical imports explicitly state their original format, raw run identities,
contracts, gates and limitations. A portable inventory distinguishes bundled
files from external artifacts. Original absolute locations may be retained as
historical provenance but are never used as a promise of current availability.

## Credentials and artifacts

Ambient environment is not serialized. Only task-declared overrides are
captured, and secret-valued overrides must not be used in public definitions.
Supply licensing/authentication at runtime. Upstream source caches, executables
and large raw profile archives are not Git history. Publish those separately
with content hashes, stable download locations and explicit retention policy.
