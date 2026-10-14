# Generation suite

This suite separates four output contracts. A count, an owned incidence graph,
a signed momentum-routed diagram, and a complete `FeynmanDiagram` are different
outputs and must not share a speed ranking. The aggregate numerator product is
excluded from the public full-object completion clock. Qualification uses the
official serializer after that clock, including all fourteen fields and complete
group/report metadata.

The portable adapters use immutable external tool checkouts. They do not inject
private constructors or copy gammaloop's packages into this repository. Original
private-constructor adapters are explicitly archival in `historical-source`.

Prepare sources using the repository's lock file, overriding local checkouts when
available. A local override must be clean and match the exact declared revision:

```sh
gammabench sources tools.lock.json --tool gammaloop --tool symbolica --tool feyngraph \
  --override gammaloop=/path/to/gammaloop --override symbolica=/path/to/symbolica \
  --override feyngraph=/path/to/FeynGraph > resolved-tools.json
python suites/generation/prepare.py --resolved-tools resolved-tools.json \
  --contract materialized --engines linnet graphica feyngraph \
  --cases qed_photon_2pt_l3 --output .gammabench/generation --build
gammabench run .gammabench/generation/suite.json snapshots/my-generation-run \
  --repeats 15 --warmups 1
```

For `qgraf` or `nauty`, resolve those tool pins as well. QGRAF is supplied from
upstream under its terms, and can also be supplied as `--qgraf-binary`; binaries
are hash-bound in the prepared suite. The nauty archive is compiled with the
public OUTPROC callbacks to materialize actual adjacency and native automorphism
orders. The callback is bounded to at most fourteen vertices and rejects inexact
group orders. No engine implementation is redistributed.

Use `--contract signed-routed --engines linnet qgraf`, or
`--contract full-public --engines linnet`. The full and signed adapters require
Symbolica at runtime; configure any required license privately. Credentials are
not explicit snapshot environment fields. `--contract count` currently builds
the Linnet, Graphica and FeynGraph public count adapters; the original native
QGRAF/nauty suppressed-output controls are retained with the historical sources
and measurements.

`--build` creates an independent Cargo package with dependencies pointing to the
resolved source roots. Its Cargo lock, binary digest, inputs and prepared
manifest are inspectable. Without `--build`, preparation emits the source/build
plan only. Use a new output directory each time. Builds require Rust, C/C++/Fortran
toolchains and GMP/MPFR, as appropriate to the selected adapters.

All cross-engine validation runs outside the completion clock. Incidence output
is certified independently with Linnet and Graphica canonicalizers, including
native automorphism factors. Signed output additionally checks all-edge momentum
conservation and a primitive integer cycle lattice; the canonical physics
certificate contains graph identities and signed rational factors, independent
of the legal loop basis. Validation fingerprints establish comparisons only for
the same immutable workload and output contract.

Twelve primary workloads exercise simple cubic graphs, trees, vacuum multigraphs,
scalar mixtures, many photon attachments, directed QED/QCD, coupling budgets and
a 119-vertex Standard Model. Additional small variants remain available in the
fixtures. Unsupported combinations are listed explicitly in the prepared suite.
FeynGraph's public partition API cannot represent the degree-one tree control;
QGRAF 3.6.10 rejects the zero-external families. See `ATTRIBUTION.md` for fixture
origins and licensing.

Completed historical studies are in `snapshots/historical`. They preserve the
original run/manifest/source identities, matched native rounds, warmups, timeouts,
semantic gates and software-counter envelopes. They are marked
`historical-import`, not fresh reruns. Large graph dumps and raw profiles are
external hash-addressed references; imported counter values are replayed from
those originals before sealing. Whole-process software counters include
observation/drop and are wider than the diagram completion endpoint. Per-process
heap peaks are never added to claim a simultaneous process-tree peak.
