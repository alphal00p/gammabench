# Suite format

Suite JSON uses `schema_version: 1`, a stable `id` and description, `contracts`,
`metrics`, `tools`, and `tasks`. Paths in `inputs` are relative to the suite file.
Commands are argv arrays: no implicit shell. Supported placeholders are
`{source:ID}`, `{tool:ID}`, `{suite_dir}`, `{workdir}`, `{python}` and `{repo}`.

A contract has a description plus `included` and `excluded` arrays. A metric
has `unit`, `direction` (`lower` or `higher`), `scope` and optional dotted
`result_field`. `process_wall_ns` is supplied by the runner. A task has `id`,
`case`, `engine`, `variant`, `contract`, `argv`, `tool_ids` and `inputs`,
with optional explicit environment, expected result fields, equivalence fields
and validation command. `workload_inputs` can select the shared semantic input
keys when engines have different auxiliary configuration; every input is still
captured and guarded. `validation_equivalence_fields` may name canonical proof
fields produced only by the validation command, keeping expensive equivalence
work outside the timer. See the complete executable example in
[`sum.json`](../suites/examples/sum.json).

Tool kinds:

- `git`: `url` and complete `revision`; sources resolve to `{source:ID}`.
- `archive`: `url`, exact `sha256`, `version`, optional `subdir`.
- `executable`: `path`, declared `version`, optional exact `sha256` and argv
  `version_command` using `{tool}`. Executables resolve to `{tool:ID}`.

Select tools per task. A topology benchmark need not install Symbolica or a
full GammaLoop rich-diagram driver. Generation preparation generates the
Cargo/build configuration against resolved immutable source checkouts.

To benchmark a new category, add another directory under `suites/`, with its
own cases, driver, validator and contracts. Keep hardware/compiler controls
in the execution protocol and benchmark semantics in that suite. Changing
output guarantees or a timer boundary requires a new contract or metric scope.

## Discovery metadata and implementation identity

Suites may declare controlled metadata, per-case values and namespaced extension
fields. Task and metric metadata override suite/case defaults at their declared
scopes. New snapshots freeze their vocabulary and effective declarations.
Semantic metadata supplements existing workload, contract and validation gates;
discovery labels do not establish output equivalence. See
[metadata, search and versioned implementations](metadata.md) for the schema,
quantity units, Unknown/N/A handling and examples.
