# Searchable metadata and implementations

The explorer searches benchmark cases across all families. Keywords match names,
descriptions and effective metadata values. Add filter offers the fields declared
by the controlled registry or the benchmark, with conditions appropriate to each
type. All filters and keyword terms must match one case and one retained
configuration. A revision from one capture cannot satisfy a query alongside a
thread count from another.

The same query can be grouped by any declared field. Grouping changes
presentation; it does not authorize comparisons. Search, filters and grouping
are retained in the URL. Existing benchmark/case links and older root links
continue to select their original workspace.

## Declare metadata

The controlled vocabulary lives in
[src/gammabench/assets/metadata.json](../src/gammabench/assets/metadata.json).
It defines field IDs, labels, types, workload/capture scope, semantic identity,
enum/tag choices, bounds and units. Unknown field names and enum values fail
validation. New shared concepts require a reviewed registry change rather than
another spelling of an existing field.

Suite metadata is optional for historical compatibility. New scaffolds include
minimal metadata in the suite, with no duplicate defaults in the website catalog:

~~~json
{
  "metadata": {
    "schema_version": 1,
    "values": {
      "operation": "sample-evaluation",
      "purpose": "regression",
      "topic": ["evaluation", "numerators"],
      "target": "sample-batch",
      "numeric.precision": "f64"
    },
    "cases": {
      "fixed-points": {
        "problem.id": "example-process",
        "numeric.batch_size": 4096
      }
    }
  }
}
~~~

Declare implementation labels on tasks when useful:

~~~json
{
  "metadata": {
    "implementation.tool_label": "GammaLoop",
    "implementation.way": "iterative",
    "implementation.way_label": "Iterative"
  }
}
~~~

An implementation's source revision and executable identity come from retained
tool bindings and Nix receipts. A display label cannot invent a Git revision.
For composed revision runs, the original task-variant mapping identifies the
way; comparison labels such as baseline/candidate identify the source selection.

Effective values resolve in order: suite defaults, case, task, then metric.
Tasks and metric definitions can each carry a flat metadata object. The suite's
tasks/metrics metadata maps offer the same declarations; duplicate declarations must agree exactly. A metric override supports a harness that measures
several operations, each retaining its own measurement scope.

Semantic fields describe the work, output or required numerical parameters.
Changing them suppresses comparisons and separates histories. Labels, topics
and purpose are discovery fields; they never establish equivalent output.
Existing fixture hashes, output contracts, validation and execution-context
checks remain required.

## Specialized fields and values

Declare suite-specific fields under x.<namespace>.<field>:

~~~json
{
  "metadata": {
    "schema_version": 1,
    "fields": {
      "x.kernel.expression_terms": {
        "label": "Expression terms",
        "type": "number",
        "scope": "workload",
        "semantic": true,
        "integer": true,
        "min": 0
      },
      "x.kernel.memory_limit": {
        "label": "Memory limit",
        "type": "quantity",
        "scope": "capture",
        "semantic": false,
        "unit": "B",
        "units": [
          {"id": "B", "label": "B", "factor": 1},
          {"id": "MiB", "label": "MiB", "factor": 1048576},
          {"id": "GiB", "label": "GiB", "factor": 1073741824}
        ],
        "min": 0
      }
    },
    "values": {
      "x.kernel.expression_terms": 2400,
      "x.kernel.memory_limit": {"value": 4, "unit": "GiB"}
    }
  }
}
~~~

Types are enum, tags, number, quantity, boolean, text and date. Dates use
YYYY-MM-DD. Quantities carry a value and a declared unit; filters and semantic
comparison normalize compatible units. A tags field uses a controlled set of
strings; an enum selects one declared value. Identifiers and revisions use text
fields instead of enormous enum lists.

Omitted/null values are unknown. Explicit not-applicable values are represented
as {"state": "not-applicable"}. Zero and false are known values.
Unknown/N/A do not satisfy ordinary inequalities or "is not"; both have explicit
filter conditions. A task's explicit null remains unknown even when its case has
a default. Automatically derived IDs/dates require retained evidence.

The collector never copies ambient environment variables into searchable
metadata. Licensing and credentials belong in private runtime environment, not
declarations, fields, labels or recipes.

## Captures and historical annotations

New snapshots freeze the effective metadata declarations and vocabulary alongside
their suite. Verification checks consistency against that frozen vocabulary;
later additions or label changes do not rewrite old snapshots.

The optional metadata object on a web/benchmarks.json entry supplies curated
annotations for legacy captures that lacked declarations. Known captured values
cannot be contradicted. An explicit captured null stays unknown. Legacy fixture
annotations can retain metadata_sources entries binding a case path and SHA-256
to an actual retained input digest. These annotations add discovery context;
they do not rewrite archived reports, metrics or correctness gates.

Planned entries may refer to an existing suite. Its declared cases and metadata
are discoverable with no invented captures. An entry with no cases stays an
explicit planned benchmark, excluded from matching-case counts.

Validate and inspect the real catalog without measuring anything:

~~~sh
gammabench catalog --repo . --output results/catalog.json
~~~

## Compare tools, ways and versions

A benchmark retains one definition and cases. Implementations identify a tool
and way; each capture pins the actual sources, binary and build evidence.
A new way can cover only some cases. Missing, unsupported, failed and censored
results remain visible in their own cells.

Comparison-set selection retains each registered analysis's stored pairing
boundaries. Latest available selects retained dated results for each
implementation and case; ties or missing dates remain explicit. That view has no
cross-capture ratios or performance colours.

The reference picker switches precomputed qualified pair directions. No browser
statistics are derived from displayed medians. Green/red always describes one
row against its selected reference, using the metric's declared direction.

Hide a version column with its header's × control. Columns restores individual
columns or shows all, with visibility retained per comparison set in the URL.
Hiding the reference clears it and leaves values neutral until explicit
reselection. All-hidden views remain recoverable. No measurements are deleted.

Active, experimental and retired labels require explicit declarations. Include
retired can hide configurations declared retired throughout the selected set;
absence from a newer capture never retires an implementation.

## History

History puts selected implementations on a shared measurement-capture-date axis.
Each point retains its actual source commit, median, observed min/max and evidence.
Tool/way selection is shareable. Fixed measurement contexts remain separate
segments; missing, unsuccessful, unqualified and undated captures break lines.
Versions measured at the same time are not given invented dates or joined into
an arbitrary vertical trend.

Archive-only and unidentified source captures remain accessible as unplotted
evidence. Unknown dates and revisions never become fabricated points. Activating
an unambiguous point opens the original recorded comparison set.

Source or workload changes require deliberate versioned declarations. Amplitude
loops, forward-graph loops and integration dimensions are separate fields.
Precision escalation, sampling, subtraction, compiler and cache settings should
be declared at their actual scope. The generic runner contains no physics rules.
