//! Direct public production API, projecting native geometry without rule tensors.
use super::*;
use feynkit_generator::{
    GenerationFilter, GenerationOptions, GenerationOutput, Process, VertexSelector,
};
use feynkit_graph::ExternalState;
use feynkit_model::{Model, ParticleId, VertexRuleId};
use std::sync::Arc;

#[derive(Deserialize)]
struct MappedField {
    id: u8,
    particle: String,
}
#[derive(Deserialize)]
struct MappedExternal {
    label: usize,
    index: usize,
    state: ExternalState,
    particle: String,
}
#[derive(Deserialize)]
struct Specification {
    model_path: String,
    fields: Vec<MappedField>,
    rules: Vec<String>,
    externals: Vec<MappedExternal>,
    incoming: Vec<String>,
    outgoing: Vec<String>,
    flow_multiplier: i8,
}
pub(super) struct NativeModel {
    model: Arc<Model>,
    process: Process,
    fields: BTreeMap<u8, ParticleId>,
    rules: Vec<VertexRuleId>,
    externals: Vec<MappedExternal>,
    options: GenerationOptions,
    flow_multiplier: i8,
}
impl NativeModel {
    pub(super) fn new(case: &Case) -> Self {
        let mapping_path = std::path::PathBuf::from(std::env::var("BENCH_ROUTED_MODEL_FILE").unwrap());
        let specification: Specification = serde_json::from_reader(BufReader::new(
            File::open(std::env::var("BENCH_ROUTED_MODEL_FILE").unwrap()).unwrap(),
        ))
        .unwrap();
        let model = Arc::new(
            Model::from_json(&std::fs::read_to_string(mapping_path.parent().unwrap().join(&specification.model_path)).unwrap()).unwrap(),
        );
        assert!(matches!(specification.flow_multiplier, -1 | 1));
        let fields: BTreeMap<_, _> = specification
            .fields
            .into_iter()
            .map(|field| (field.id, model.particle_id(&field.particle).unwrap()))
            .collect();
        let rules: Vec<_> = specification
            .rules
            .iter()
            .map(|name| model.vertex_rule_id(name).unwrap())
            .collect();
        assert_eq!(fields.len(), case.fields.len());
        assert_eq!(rules.len(), case.signatures.len());
        for (signature, &rule) in case.signatures.iter().zip(&rules) {
            let mut expected: Vec<_> = signature
                .iter()
                .map(|stub| {
                    let field = fields[&stub.field];
                    if stub.flow * specification.flow_multiplier > 0 {
                        model.particle_by_id(field).unwrap().antiparticle
                    } else {
                        field
                    }
                })
                .collect();
            expected.sort();
            let mut actual = model.vertex_rule_by_id(rule).unwrap().particles.clone();
            actual.sort();
            assert_eq!(actual, expected);
        }
        let process = Process::new(
            specification.incoming.clone(),
            specification.outgoing.clone(),
        );
        let process_legs: Vec<_> = specification
            .incoming
            .iter()
            .map(|particle| (ExternalState::Incoming, particle))
            .chain(
                specification
                    .outgoing
                    .iter()
                    .map(|particle| (ExternalState::Outgoing, particle)),
            )
            .collect();
        assert_eq!(process_legs.len(), case.legs.len());
        assert_eq!(specification.externals.len(), case.legs.len());
        let mut indices = BTreeSet::new();
        for external in &specification.externals {
            assert!(indices.insert(external.index));
            let (state, particle) = process_legs[external.index];
            assert_eq!(external.state, state);
            assert_eq!(external.particle, *particle);
            let physical = model.particle_id(particle).unwrap();
            let entering = if state == ExternalState::Incoming {
                physical
            } else {
                model.particle_by_id(physical).unwrap().antiparticle
            };
            let leg = case
                .legs
                .iter()
                .find(|leg| leg.label == external.label)
                .unwrap();
            let field = fields[&leg.field];
            let expected = if leg.flow * specification.flow_multiplier > 0 {
                model.particle_by_id(field).unwrap().antiparticle
            } else {
                field
            };
            assert_eq!(entering, expected);
        }
        let mut options = GenerationOptions::default()
            .threads(1)
            .with_loop_count(case.loops, case.loops)
            .unwrap()
            .max_vertices(case.max_vertices())
            .allow_self_loops(case.allow_self_loops)
            .allow_zero_flow_edges(case.zero_flow_edges)
            .output(GenerationOutput::Minimal)
            .filter_zero_color(false)
            .with_graph_filter(GenerationFilter::VertexAllow(
                rules
                    .iter()
                    .map(|&rule| VertexSelector::by_id(&model, rule).unwrap())
                    .collect(),
            ));
        if case.one_pi {
            options = options.with_graph_filter(GenerationFilter::MaxNumberOfBridges(0));
        }
        let simple = case.simple;
        let exact_vertices = case.exact_vertices;
        let degree_weights = case.weights.as_ref().map(|weights| {
            let mut table = BTreeMap::new();
            for (signature, &weight) in case.signatures.iter().zip(weights) {
                if let Some(previous) = table.insert(signature.len(), weight) {
                    assert_eq!(
                        previous, weight,
                        "case weight cannot be expressed by degree"
                    );
                }
            }
            (table, case.weight_exact.unwrap())
        });
        if simple || exact_vertices.is_some() || degree_weights.is_some() {
            options = options.filter(move |view| {
                if !view.is_topology_complete() {
                    return true;
                }
                if exact_vertices.is_some_and(|exact| view.n_vertices() != exact) {
                    return false;
                }
                if simple
                    && (0..view.n_vertices()).any(|a| {
                        view.self_loops(a) > 0
                            || (a + 1..view.n_vertices()).any(|b| view.multiplicity(a, b) > 1)
                    })
                {
                    return false;
                }
                if let Some((weights, exact)) = &degree_weights
                    && (0..view.n_vertices())
                        .map(|vertex| weights[&view.degree(vertex)])
                        .sum::<i32>()
                        != *exact
                {
                    return false;
                }
                true
            });
        }
        Self {
            model,
            process,
            fields,
            rules,
            externals: specification.externals,
            options,
            flow_multiplier: specification.flow_multiplier,
        }
    }

    pub(super) fn generate(&self, case: &Case) -> Vec<Record> {
        let result = self
            .process
            .generate_routed_diagrams(Arc::clone(&self.model), &self.options)
            .unwrap();
        assert!(result.report.completed);
        assert_eq!(result.report.zero_numerator_count, 0);
        let records = result
            .diagrams
            .iter()
            .map(|diagram| {
                let mut rules = vec![usize::MAX; diagram.vertices().count()];
                for (vertex, data) in diagram.vertices() {
                    rules[vertex.0] = self
                        .rules
                        .iter()
                        .position(|&rule| Some(rule) == data.interaction)
                        .unwrap();
                }
                let native_edges: Vec<_> = diagram.edges().collect();
                let basis = diagram.loop_momentum_basis();
                assert_eq!(basis.loop_edges.len(), case.loops);
                let external_columns: Vec<_> = basis
                    .external_edges
                    .iter()
                    .map(|edge| {
                        let (_, endpoints, data) =
                            native_edges.iter().find(|(id, _, _)| id == edge).unwrap();
                        let external = data.external.as_ref().unwrap();
                        let label = self
                            .externals
                            .iter()
                            .find(|leg| leg.index == external.index)
                            .unwrap()
                            .label;
                        let sign = if endpoints.target.is_some() { 1 } else { -1 };
                        (label, sign)
                    })
                    .collect();
                let mut edges = Vec::new();
                let mut legs = Vec::new();
                for (id, endpoints, data) in native_edges {
                    let signature = &basis.edge_signatures[&id];
                    let mut q: Vec<i32> = signature
                        .loops
                        .integer_coefficients()
                        .into_iter()
                        .map(|value| value as i32)
                        .chain(std::iter::repeat_n(0, case.legs.len().saturating_sub(1)))
                        .collect();
                    for (column, value) in signature
                        .external
                        .integer_coefficients()
                        .into_iter()
                        .enumerate()
                    {
                        let (label, sign) = external_columns[column];
                        let coefficient = value as i32 * sign;
                        if label == case.legs.len() {
                            for x in &mut q[case.loops..] {
                                *x -= coefficient;
                            }
                        } else {
                            q[case.loops + label - 1] += coefficient;
                        }
                    }
                    if let Some(external) = &data.external {
                        let mapped = self
                            .externals
                            .iter()
                            .find(|leg| leg.index == external.index)
                            .unwrap();
                        let expected = case
                            .legs
                            .iter()
                            .find(|leg| leg.label == mapped.label)
                            .unwrap();
                        let vertex = if let Some(target) = endpoints.target {
                            target.0
                        } else {
                            q.iter_mut().for_each(|value| *value = -*value);
                            endpoints.source.unwrap().0
                        };
                        legs.push(Leg {
                            vertex,
                            label: mapped.label,
                            field: expected.field,
                            flow: expected.flow,
                            momentum: q,
                        });
                    } else {
                        let (field, particle_flow) = self
                            .fields
                            .iter()
                            .find_map(|(&field, &base)| {
                                if data.particle == base {
                                    Some((field, 1))
                                } else if data.particle
                                    == self.model.particle_by_id(base).unwrap().antiparticle
                                {
                                    Some((field, -1))
                                } else {
                                    None
                                }
                            })
                            .unwrap();
                        q.iter_mut().for_each(|value| *value = -*value);
                        edges.push(Edge {
                            vertices: [endpoints.source.unwrap().0, endpoints.target.unwrap().0],
                            field,
                            flow: if data.directed {
                                particle_flow * self.flow_multiplier
                            } else {
                                0
                            },
                            momentum: q,
                        });
                    }
                }
                let (loop_basis, external_basis) = case.basis();
                Record {
                    rules,
                    legs,
                    edges,
                    aut: diagram.automorphism_order(),
                    multiplicity: diagram.multiplicity(),
                    sign: diagram.sign(),
                    loop_basis,
                    external_basis,
                }
            })
            .collect();
        // Retaining only the complete common records is the benchmark contract.
        // Dropping the richer native geometry is included before completion.
        drop(result);
        records
    }
}
