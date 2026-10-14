# Initial release validation

The initial framework passes 83 unit/integration tests covering exact source
pins, archive content checks, dirty source capture, automatic correctness
qualification before timing, blocking process waits with deadlines, censored
rows, workload selectors, snapshot relocation/tampering, safe analysis exports,
paired ratios and historical imports. An independent review reproduced the
full-public QCD median paired ratio0.9607013363659952 from the preserved study.

The Python package installs and its CLI runs. The example suite completed
15 measured rounds plus excluded warmups, verified its snapshot and rendered
an offline report. Reports were inspected in headless Chromium.

## Portable generation adapters

Actual release builds against the declared clean pinned checkouts passed.
Receipts capture full source/archive pins, compiler versions, Cargo.lock,
resolved Cargo metadata, commands, helper hashes and binaries.

- Materialized graphs: seven representative tasks across Linnet, Graphica,
  FeynGraph, QGRAF and nauty passed. Cubic10 has19 graphs; QED photon2pt at
  three loops has20. Exact canonical-record and symmetry-factor gates passed.
- Signed/routed output: Linnet and QGRAF each yielded20 QED diagrams, with
  matching exact physical weights and primitive routing/conservation proofs.
- Public full objects:20 FeynmanDiagram objects and20 groups passed the
  official fourteen-field qualification path, with local numerator/projector
  construction included and aggregate multiplication outside completion.

FeynGraph's cubic10 validation hit the60s cap in the initial smoke. Its failed
snapshot is retained locally and was not replaced by a zero result. The final
successful smoke selected the seven qualifying tasks. These are adapter and
framework smoke checks, not a new statistically robust performance study.
Historical native count studies cover all five engines; the portable count
preparation currently exposes the three Rust engines. Older private injected
full constructors are archived source, not the new public-full baseline.

## Preservation

Four imported studies retain2,306 records, original run/control/source IDs,
semantic gates and software counters. The snapshots verify after relocation.
Raw evidence release archives preserve profiles and referenced physics dumps,
with checksums and their original captured paths. Source implementations and
engine executables are excluded from those evidence archives.
