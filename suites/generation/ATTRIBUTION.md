# Generation fixtures and adapters

The Rust and Python comparison adapters, neutral requests, QGRAF styles and
nauty OUTPROC callback were authored during the gammaloop generation experiments
in October 2026. Original file identities are in `provenance.json`.

The QED/QCD model subsets derive from gammaloop's `feynkit-model` builtins.
The scalar phi3, phi34 and tree control are synthetic benchmark models.
The SM119 JSON is a converted model from
`Jens-Braun/FeynGraph/tests/resources/Standard_Model_UFO` at
`15afe2f2c701331c76a0f5d55a752dca099104be`, whose UFO metadata names
N. Christensen, C. Duhr and B. Fuks. FeynGraph uses the Mozilla Public License
2.0; a copy is preserved in `fixtures/licenses`. Model conversion source hashes
and limitations are in `fixtures/models-source-provenance.json`.

No QGRAF implementation or binary is redistributed here. Supply QGRAF yourself
under its upstream terms. Symbolica, FeynGraph and nauty also remain external
pinned inputs, rather than vendored engine implementations. Symbolica runtime
licensing is configured privately; no license credential belongs in a snapshot.

`historical-source` preserves selected original adapter sources as documentation.
In particular, `private-full-adapter.rs` used an experiment-only injected
constructor. It is not the portable public API full-diagram baseline and must
not be compared as if the output preparation were identical.
