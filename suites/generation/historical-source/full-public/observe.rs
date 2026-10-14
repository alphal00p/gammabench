//! Cached structural witness plus process-local local-Atom traversal; no aggregate demand.
use super::*;
use feynkit_graph::FeynmanDiagram;
use std::hash::{Hash, Hasher};
use symbolica::atom::AtomCore;

pub fn observe(diagrams: &[FeynmanDiagram]) -> Value {
    let (mut sum, mut xor, mut vertices, mut edges, mut half_edges) =
        (0u64, 0u64, 0usize, 0usize, 0usize);
    for diagram in std::hint::black_box(diagrams) {
        let mut hash = std::collections::hash_map::DefaultHasher::new();
        diagram.name().hash(&mut hash);
        diagram.symmetry_factor().hash(&mut hash);
        diagram.overall_factor().hash(&mut hash);
        diagram.numerator_prefactor().hash(&mut hash);
        diagram.projector().hash(&mut hash);
        for (id, data) in diagram.vertices() {
            vertices += 1;
            (id, &data.name, data.interaction, &data.numerator).hash(&mut hash);
        }
        for (id, ends, data) in diagram.edges() {
            edges += 1;
            linnet::half_edge::EdgeAccessors::orientation(
                diagram.underlying(),
                linnet::half_edge::involution::EdgeIndex(id.0),
            )
            .hash(&mut hash);
            (
                id,
                ends,
                &data.external,
                data.is_dummy,
                data.particle,
                data.directed,
                &data.numerator,
                data.source_slot(),
                data.target_slot(),
            )
                .hash(&mut hash);
        }
        for half_edge in diagram.half_edges() {
            half_edges += 1;
            half_edge.hash(&mut hash);
        }
        let routing = diagram.loop_momentum_basis();
        (
            &routing.tree_edges,
            &routing.loop_edges,
            &routing.external_edges,
            &routing.dependent_externals,
            &routing.edge_signatures,
        )
            .hash(&mut hash);
        diagram.cuts().hash(&mut hash);
        diagram.topology_threshold_candidates().hash(&mut hash);
        let digest = hash.finish();
        sum = sum.wrapping_add(digest);
        xor ^= digest;
    }
    json!({"checksum_sum":format!("{sum:016x}"),"checksum_xor":format!("{xor:016x}"),"vertices":vertices,"edges":edges,"half_edges":half_edges})
}

/// Stable witness for topology, routing and small scalar bookkeeping factors.
/// Numerator and projector semantics are certified by exact full-object dumps;
/// `observe` consumes local fragments and the three scalar/projector fields
/// without demanding the aggregate numerator.
pub fn observe_structure(diagrams: &[FeynmanDiagram]) -> Value {
    let (mut sum, mut xor, mut vertices, mut edges, mut half_edges) =
        (0u64, 0u64, 0usize, 0usize, 0usize);
    let mut symbolic_fields_observed = 0usize;
    for diagram in std::hint::black_box(diagrams) {
        let mut hash = std::collections::hash_map::DefaultHasher::new();
        diagram.model().fingerprint().hash(&mut hash);
        diagram.name().hash(&mut hash);
        diagram.symmetry_factor().hash(&mut hash);
        diagram
            .overall_factor()
            .to_canonical_string()
            .hash(&mut hash);
        diagram
            .numerator_prefactor()
            .to_canonical_string()
            .hash(&mut hash);
        // Overall factor, scalar prefactor and projector; no aggregate demand.
        // Local fragments are consumed by the process-local observer.
        symbolic_fields_observed += 3;
        for (id, data) in diagram.vertices() {
            vertices += 1;
            symbolic_fields_observed += 1;
            (id, &data.name, data.interaction).hash(&mut hash);
        }
        for (id, ends, data) in diagram.edges() {
            edges += 1;
            symbolic_fields_observed += 1;
            linnet::half_edge::EdgeAccessors::orientation(
                diagram.underlying(),
                linnet::half_edge::involution::EdgeIndex(id.0),
            )
            .hash(&mut hash);
            (
                id,
                ends,
                &data.external,
                data.is_dummy,
                data.particle,
                data.directed,
                data.source_slot(),
                data.target_slot(),
            )
                .hash(&mut hash);
        }
        for half_edge in diagram.half_edges() {
            half_edges += 1;
            half_edge.hash(&mut hash);
        }
        let routing = diagram.loop_momentum_basis();
        (
            &routing.tree_edges,
            &routing.loop_edges,
            &routing.external_edges,
            &routing.dependent_externals,
            &routing.edge_signatures,
        )
            .hash(&mut hash);
        diagram.cuts().hash(&mut hash);
        diagram.topology_threshold_candidates().hash(&mut hash);
        let digest = hash.finish();
        sum = sum.wrapping_add(digest);
        xor ^= digest;
    }
    json!({"schema":"stable-structure-routing-local-fragments-v1", "checksum_sum":format!("{sum:016x}"),
        "checksum_xor":format!("{xor:016x}"), "vertices":vertices, "edges":edges,
        "half_edges":half_edges, "symbolic_fields_observed":symbolic_fields_observed})
}

pub fn observe_process_local(diagrams: &[FeynmanDiagram]) -> Value {
    let mut observation = observe(diagrams);
    observation["scope"] = json!(
        "process-local Symbolica local-fragment representation; aggregate numerator excluded; not a cross-process semantic fingerprint"
    );
    observation
}
