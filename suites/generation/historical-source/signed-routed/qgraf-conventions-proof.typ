= Native QGRAF signed and routed output convention

QGRAF source is the immutable `qgraf-3.6.10.f08` under
`/tmp/feynkit-generator-comparison-evidence/external/qgraf-3.6.10/`.
The benchmark uses native `<sign>`, `<symmetry_number>`, `<momentum>` and
`<dual-momentum>` keywords. It does not recover any of those data through
FeynKit construction.

== Operator sign convention

The selected nine supported models have zero or two Grassmann ports at each
interaction, with opposite native stub flows. In each QGRAF model declaration
we order those two ports as `anti_name` followed by `field.name`, retaining their
original two positions among bosonic ports. This prepares a fixed operator
convention before generation. The particles, interaction multisets, coupling
weights, selected interaction IDs, graph family and external labels do not
change. No per-diagram sign is fitted or replaced.

Native `g43` first maps each vertex's ports into its declared model order
(lines 10239–10258), then forms one ordered Grassmann token list
(lines 10264–10307). Internal propagator ends receive paired positive odd/even
labels; external incoming operators receive `1-2*external_index` labels.
The contraction loop removes pairs in decreasing internal label order and
updates `dis` on exchanges and pair reversal (lines 10310–10331).
Finally it removes incoming operators in increasing request order
(lines 10333–10343). `<sign>` serializes that native `dis` (lines 3637–3645).

For two-port, consistently oriented Grassmann chains, the Wick contraction
parity reduces to the following relation. Let `C` be the number of closed
Grassmann components, `P` the number of external Grassmann pairs, and `pi` the
permutation formed by concatenating each native `(anti_name,field.name)`
external pair. With the declared anti-before-particle vertex convention,

$ s_("QGRAF") = (-1)^(C+P) "parity"(pi). $

Contracting adjacent ports along an open chain leaves its anti/particle external
pair. Each open pair contributes one exchange against QGRAF's ordered incoming
operator removal, giving the factor `(-1)^P`. Closing a chain contributes the
usual additional Grassmann loop exchange `(-1)^C`. Reordering whole two-port
interaction blocks or whole external pairs is even. The reduction is therefore
independent of the number of internal vertices and the order in which the
nonbranching chains are visited. Exchanging a vertex's two Grassmann declarations
would flip its native Wick sign once; the canonical preparation removes that
convention difference at its source.

FeynKit's amplitude sign is `(-1)^C` times the permutation of physical
`(antiparticle,particle)` external pairs. Source: `generation.rs`
`normalize_fermion_flows`, especially the concatenated pair list and
`permutation_is_odd` call, plus `closed_fermion_loop_count`. The common endpoint
uses amplitude diagrams; cross-section sewing and antifermion spin-sum factors
are not requested.

Let `F` be one if the accepted model bridge has `flow_multiplier=-1`, zero
otherwise. This exchanges the two physical roles within every external pair,
contributing `(-1)^(F*P)`. Let `sigma` map native external labels to physical
request indices, restricted to the Grassmann external legs. Then the single,
request-fixed conversion coefficient is

$ c_("request") = (-1)^((1+F)P) "parity"(sigma), $

and the common physical sign is `c_request * s_QGRAF` for every diagram. The
coefficient is derived solely from the model/request mapping and operator
convention, before looking at native diagram signs. For these workloads it is
`-1` for QED asymmetric and QED six-photon emission, and `+1` for the other seven
supported cases. Native raw output remains archived with its original sign.

The correctness-only probes verify the identity for all 300 QED asymmetric
and all 15,966 SM diagrams, including exact directed rule incidence. The
independent diagnostic derives closed components and external pairings directly
from native incidence; it does not construct FeynmanDiagram objects and does
not supply the exported signs. The later cross-backend gate must additionally
compare the actual FeynKit signs under graph isomorphism.

== Momentum convention

The emitted internal momentum enters the first emitted endpoint. Its dual
enters the second endpoint and is exactly its negative. Native external
incoming-leg momentum enters the attached vertex. `out_loop` emits its
`dual-momentum` so that its external row also uses all-incoming momentum.
All current supported decks declare every benchmark leg in QGRAF's `in` list,
with an empty `out` list; crossing to the physical incoming/outgoing request
belongs to the explicit model-map conversion.

The native basis is `k1` through `kL`, then `p1` through `pN`, with
`p1+...+pN=0`. Parsing retains integer coefficients in the independent basis
`k1..kL,p1..p(N-1)` by substituting `pN=-sum(p1..p(N-1))`. Native emitted
coefficients are zero or signed unit terms. The accepted textual grammar is
literal `0` or a fully covered concatenation of optional signs, `k`/`p`, and a
positive decimal index. No symbolic normalization is required.

The native spanning-tree flow construction is in `g21`, lines 9299–9422.
`compac` emits the internal incoming momentum from `flow` at lines 3700–3811,
including loop and external coefficients. A self-loop receives one independent
loop momentum and its negative at the two ends. Every routed smoke diagram
passes exact vertex momentum conservation, full loop rank, exact opposite dual
momenta and native rule-incidence checks.

For the existing FeynKit input bridge, let `f = raw_flow*flow_multiplier` and
let `q` enter native first endpoint `a` of `[a,b]`. If `f<0`, the physical
edge goes from `b` to `a` and carries `q` along its direction. Otherwise it goes
from `a` to `b` and carries `-q`. An external edge with `f<0` goes from external
to interaction vertex and carries `q`; otherwise it goes from interaction to
external and carries `-q`. This also covers undirected edge frame choices.
The corresponding typed particle is the declared model base particle.

== Coverage

All nine previously accepted external-leg QGRAF cases are prepared. The three
selected zero-external workloads remain unsupported by the native QGRAF API:
`inputs` explicitly rejects `nleg==0` with `no external particles` at lines
6903–6905. A bounded theta vacuum request reproduces that rejection with actual
native output and an exit-zero error record. Artificial external attachments
would change the requested graph family and symmetry-factor endpoint.

QGRAF success requires a successful final connected-diagram count, empty error
messages, a present complete `END` record, and exactly the declared count.
Native exit zero by itself is insufficient. Every measured execution requires
a fresh output directory: QGRAF refuses to overwrite an existing message file.
