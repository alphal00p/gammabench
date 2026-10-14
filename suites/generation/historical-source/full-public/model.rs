//! Exact model/filter setup for direct public full FeynmanDiagram generation.
use super::*;
use feynkit_generator::{
    GenerationFilter, GenerationOptions, GenerationOutput, GenerationResult, NumeratorGrouping,
    Process, VertexSelector,
};
use feynkit_graph::ExternalState;
use feynkit_model::Model;
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
    options: GenerationOptions,
    pub(super) model_fingerprint: String,
}
impl NativeModel {
    pub(super) fn new(case: &Case) -> Self {
        let specification: Specification = serde_json::from_reader(BufReader::new(
            File::open(std::env::var("BENCH_FULL_MODEL_FILE").unwrap()).unwrap(),
        ))
        .unwrap();
        let model = Arc::new(
            Model::from_json(&std::fs::read_to_string(&specification.model_path).unwrap()).unwrap(),
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
        assert_eq!(
            fields.keys().copied().collect::<BTreeSet<_>>(),
            case.fields
                .iter()
                .map(|field| field.id)
                .collect::<BTreeSet<_>>()
        );
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
            .numerator_grouping(NumeratorGrouping::None)
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
        let model_fingerprint = model.fingerprint().to_string();
        Self {
            model,
            process,
            options,
            model_fingerprint,
        }
    }

    pub(super) fn generate(&self) -> GenerationResult {
        let result = self
            .process
            .generate_diagrams(Arc::clone(&self.model), &self.options)
            .unwrap();
        assert!(result.report.completed);
        assert_eq!(result.report.zero_numerator_count, 0);
        assert_eq!(result.report.retained_count, result.diagrams.len());
        assert_eq!(result.groups.len(), result.diagrams.len());
        result
    }
}
