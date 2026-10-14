mod model;
mod observe;

use feynkit_generator::GenerationResult;
use feynkit_graph::FeynmanDiagram;
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::{BufReader, BufWriter, Write},
    time::Instant,
};
use symbolica::atom::AtomCore;

const CONTRACT: &str = "full-native-diagrams-local-fragments-v1";

#[derive(Clone, Debug, Deserialize)]
struct Stub {
    field: u8,
    flow: i8,
}
#[derive(Clone, Debug, Deserialize)]
struct Field {
    id: u8,
}
#[derive(Clone, Debug, Deserialize)]
struct External {
    label: usize,
    field: u8,
    flow: i8,
}
#[derive(Clone, Debug, Deserialize)]
struct Case {
    name: String,
    fields: Vec<Field>,
    signatures: Vec<Vec<Stub>>,
    legs: Vec<External>,
    loops: usize,
    max_vertices: Option<usize>,
    exact_vertices: Option<usize>,
    simple: bool,
    allow_self_loops: bool,
    zero_flow_edges: bool,
    one_pi: bool,
    weights: Option<Vec<i32>>,
    weight_exact: Option<i32>,
}
impl Case {
    fn max_vertices(&self) -> usize {
        self.max_vertices.unwrap_or_else(|| {
            (2 * self.loops + self.legs.len() - 2)
                / (self.signatures.iter().map(Vec::len).min().unwrap() - 2)
        })
    }
}

fn metadata(result: &GenerationResult) -> Value {
    let groups: Vec<_> = result
        .groups
        .iter()
        .map(|group| {
            let members: Vec<_> = group
                .members
                .iter()
                .map(|member| {
                    json!({
                        "source_diagram":member.source_diagram,
                        "source_identity":member.source_identity.as_ref().map(|identity| {
                            json!({"id":identity.id,"name":identity.name})
                        }),
                        "diagram":member.diagram,
                        "ratio":member.ratio.to_canonical_string(),
                        "overall_factor":member.overall_factor.to_canonical_string(),
                    })
                })
                .collect();
            json!({"master":group.master,"members":members})
        })
        .collect();
    json!({"report":result.report,"groups":groups})
}

fn dump(path: &str, diagrams: &[FeynmanDiagram]) {
    let mut output = BufWriter::new(File::create(path).unwrap());
    for diagram in diagrams {
        // The official 14-field serializer demands the aggregate here only.
        // This branch never runs in performance/profile mode.
        serde_json::to_writer(&mut output, diagram).unwrap();
        writeln!(output).unwrap();
    }
    output.flush().unwrap();
}

fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    assert_eq!(args[0], "linnet");
    assert!(matches!(
        args[2].as_str(),
        "full-native" | "full-native-dump"
    ));
    let started = Instant::now();
    let case: Case = serde_json::from_reader(BufReader::new(
        File::open(std::env::var("BENCH_CASE_FILE").unwrap()).unwrap(),
    ))
    .unwrap();
    assert_eq!(case.name, args[1]);
    let threads: usize = std::env::var("BENCH_THREADS")
        .unwrap_or_else(|_| "1".into())
        .parse()
        .unwrap();
    assert_eq!(threads, 1);
    let native = model::NativeModel::new(&case);
    let model_fingerprint = native.model_fingerprint.clone();
    let setup_ns = started.elapsed().as_nanos();
    let result = native.generate();
    let full_diagrams_ns = started.elapsed().as_nanos();
    // Keep the actual public result and its FeynmanDiagram objects alive.
    let count = result.diagrams.len();
    let group_count = result.groups.len();
    let report = result.report.clone();
    let structure = observe::observe_structure(&result.diagrams);
    let local_atoms = observe::observe_process_local(&result.diagrams);
    let observed_ns = started.elapsed().as_nanos();
    if args[2] == "full-native-dump" {
        dump(&args[3], &result.diagrams);
        std::fs::write(
            &args[4],
            serde_json::to_vec_pretty(&metadata(&result)).unwrap(),
        )
        .unwrap();
    }
    std::hint::black_box(&result);
    drop(result);
    drop(native);
    let destroyed_ns = started.elapsed().as_nanos();
    println!(
        "RESULT {}",
        json!({
            "status":"ok","engine":"linnet","case":case.name,"mode":args[2],
            "output_contract":CONTRACT,"setup_ns":setup_ns,"full_diagrams_ns":full_diagrams_ns,
            "observed_ns":observed_ns,"destroyed_ns":destroyed_ns,"elapsed_ns":started.elapsed().as_nanos(),
            "accepted_count":count,"emitted_count":count,"full_diagram_count":count,"group_count":group_count,
            "full_diagram_observation":structure,"process_local_atom_observation":local_atoms,
            "generation_report":report,"full_model_fingerprint":model_fingerprint,"worker_threads":threads,
            "driver_pid":std::process::id(),"process_manifest":[],
            "scope":"fresh direct Process::generate_diagrams through retained public GenerationResult with local numerators, automatic projectors, signs/routing/validation/groups; aggregate lazy. No native record conversion or common canonical input normalization. Observer/drop outside completion; official 14-field aggregate serialization only in qualification."
        })
    );
}
