# Gammabench contributor guidance

Read README.md and docs/protocol.md before editing. Keep benchmark definitions,
correctness checks, output contracts, tool pins, raw records and display consistent.
Use the Python standard library for orchestration and analysis, pinned Hyperfine
2.0.0 for whole-process measurements, and Nix for benchmark packages. Internal
code-region metrics retain their declared harness clocks. Report presentation
uses pinned Typst. Suite-specific drivers may use pinned external tools. Keep production physics in the tool being measured.

Before each new jj change ask for the author name/email and set it explicitly.
Do not rewrite a sealed result snapshot; publish a new snapshot with provenance.
Never infer equivalent output from equal counts alone. Do not silently remove
failures, censored rows, warmups or unsupported cases. Compare only explicitly
matching metric scopes and correctness-qualified output contracts.

Do not commit downloaded engines, build caches, private credentials or licenses.
Retain attribution for fixture data and archived adapters. Test with
`PYTHONPATH=src python -m unittest discover -s tests -v` and run the example
suite plus snapshot verification/report rendering before publishing framework changes.
Do not claim a historical import is a fresh run. Exact source pins, toolchain and
host metadata belong to snapshots; measurements cannot be overwritten by analysis.
