mod model;
mod validate;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::{BufRead, BufReader, BufWriter, Write},
    process::{Command, Stdio},
    time::Instant,
};

const CONTRACT: &str = "signed-routed-diagrams-v1";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
struct Stub {
    field: u8,
    flow: i8,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct Field {
    id: u8,
    name: String,
    anti_name: String,
    fermi: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct External {
    label: usize,
    field: u8,
    flow: i8,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct Leg {
    vertex: usize,
    label: usize,
    field: u8,
    flow: i8,
    /// Momentum entering the attached vertex, in the declared basis.
    momentum: Vec<i32>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct Edge {
    vertices: [usize; 2],
    field: u8,
    flow: i8,
    /// Momentum entering vertices[0]; its dual enters vertices[1].
    momentum: Vec<i32>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct Record {
    rules: Vec<usize>,
    legs: Vec<Leg>,
    edges: Vec<Edge>,
    aut: u64,
    multiplicity: u64,
    /// Physical sign after the declared fixed request convention conversion.
    sign: i8,
    loop_basis: Vec<String>,
    /// All momenta enter vertices; the last external obeys pN=-sum(p1..pN-1).
    external_basis: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
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
    fn basis(&self) -> (Vec<String>, Vec<String>) {
        (
            (1..=self.loops).map(|i| format!("k{i}")).collect(),
            (1..self.legs.len()).map(|i| format!("p{i}")).collect(),
        )
    }
}

/// Strict native QGRAF momentum grammar, with only the declared pN relation applied.
fn momentum(text: &str, case: &Case) -> Vec<i32> {
    let mut result = vec![0; case.loops + case.legs.len().saturating_sub(1)];
    if text == "0" {
        return result;
    }
    let bytes = text.as_bytes();
    let mut offset = 0;
    while offset < bytes.len() {
        let sign = match bytes[offset] {
            b'-' => {
                offset += 1;
                -1
            }
            b'+' => {
                offset += 1;
                1
            }
            _ => {
                assert_eq!(offset, 0, "missing momentum separator");
                1
            }
        };
        let kind = bytes[offset];
        assert!(matches!(kind, b'k' | b'p'));
        offset += 1;
        let start = offset;
        assert!(matches!(bytes[offset], b'1'..=b'9'));
        while offset < bytes.len() && bytes[offset].is_ascii_digit() {
            offset += 1;
        }
        let index = text[start..offset].parse::<usize>().unwrap() - 1;
        if kind == b'k' {
            assert!(index < case.loops);
            result[index] += sign;
        } else {
            assert!(index < case.legs.len());
            if index + 1 == case.legs.len() {
                for coefficient in &mut result[case.loops..] {
                    *coefficient -= sign;
                }
            } else {
                result[case.loops + index] += sign;
            }
        }
    }
    result
}

fn qgraf_records(case: &Case, reader: impl BufRead, sign_coefficient: i8) -> Vec<Record> {
    assert!(matches!(sign_coefficient, -1 | 1));
    let mut names = BTreeMap::new();
    for field in &case.fields {
        names.insert(
            field.name.as_str(),
            (field.id, if field.name == field.anti_name { 0 } else { -1 }),
        );
        if field.name != field.anti_name {
            names.insert(field.anti_name.as_str(), (field.id, 1));
        }
    }
    let mut records = Vec::new();
    let mut current: Option<Record> = None;
    let mut incoming = 0;
    let mut final_count = None;
    let mut format = false;
    for line in reader.lines() {
        let line = line.unwrap();
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.is_empty() {
            continue;
        }
        match fields[0] {
            "FORMAT" => {
                assert_eq!(fields, ["FORMAT", "qgraf-signed-routed-v1"]);
                assert!(!format);
                format = true;
            }
            "D" => {
                assert_eq!(fields.len(), 4);
                assert!(format && final_count.is_none());
                if let Some(record) = current.take() {
                    records.push(record);
                }
                assert_eq!(fields[1].parse::<usize>().unwrap(), records.len() + 1);
                let sign = sign_coefficient
                    * match fields[2] {
                        "+" => 1,
                        "-" => -1,
                        _ => panic!("invalid native sign"),
                    };
                let aut = fields[3].parse::<u64>().unwrap();
                assert!(aut > 0);
                let (loop_basis, external_basis) = case.basis();
                current = Some(Record {
                    rules: Vec::new(),
                    legs: Vec::new(),
                    edges: Vec::new(),
                    aut,
                    multiplicity: 1,
                    sign,
                    loop_basis,
                    external_basis,
                });
            }
            "B" => {
                assert_eq!(fields.len(), 5);
                assert_eq!(fields[1].parse::<usize>().unwrap(), case.loops);
                assert_eq!(fields[2].parse::<usize>().unwrap(), case.legs.len());
                incoming = fields[3].parse::<usize>().unwrap();
                assert_eq!(
                    incoming + fields[4].parse::<usize>().unwrap(),
                    case.legs.len()
                );
            }
            "L" | "O" => {
                assert_eq!(fields.len(), 5);
                let label = fields[1].parse::<usize>().unwrap()
                    + if fields[0] == "O" { incoming } else { 0 };
                let (field, flow) = names[fields[3]];
                let expected = case.legs.iter().find(|leg| leg.label == label).unwrap();
                assert_eq!((field, flow), (expected.field, expected.flow));
                current.as_mut().unwrap().legs.push(Leg {
                    vertex: fields[2].parse::<usize>().unwrap() - 1,
                    label,
                    field,
                    flow,
                    momentum: momentum(fields[4], case),
                });
            }
            "E" => {
                assert_eq!(fields.len(), 5);
                let record = current.as_mut().unwrap();
                assert_eq!(fields[1].parse::<usize>().unwrap(), record.edges.len() + 1);
                let (field, flow) = names[fields[4]];
                record.edges.push(Edge {
                    vertices: [
                        fields[2].parse::<usize>().unwrap() - 1,
                        fields[3].parse::<usize>().unwrap() - 1,
                    ],
                    field,
                    flow,
                    momentum: Vec::new(),
                });
            }
            "M" => {
                assert_eq!(fields.len(), 3);
                let q = momentum(fields[1], case);
                assert_eq!(
                    momentum(fields[2], case),
                    q.iter().map(|v| -v).collect::<Vec<_>>()
                );
                let edge = current.as_mut().unwrap().edges.last_mut().unwrap();
                assert!(edge.momentum.is_empty());
                edge.momentum = q;
            }
            "V" => {
                assert_eq!(fields.len(), 3);
                let record = current.as_mut().unwrap();
                assert_eq!(fields[1].parse::<usize>().unwrap(), record.rules.len() + 1);
                let rule = fields[2].parse::<usize>().unwrap();
                assert!(rule < case.signatures.len());
                record.rules.push(rule);
            }
            "END" => {
                assert_eq!(fields.len(), 2);
                assert!(final_count.is_none());
                final_count = Some(fields[1].parse::<usize>().unwrap());
            }
            _ => panic!("unexpected native token: {}", fields[0]),
        }
    }
    if let Some(record) = current {
        records.push(record);
    }
    assert_eq!(final_count, Some(records.len()));
    records
}

#[derive(Deserialize)]
struct ExternalConfig {
    commands: Vec<Vec<String>>,
    output_file: String,
    raw_format: String,
    sign_coefficient: i8,
}
#[derive(Serialize)]
struct ChildProcess {
    pid: u32,
    parent_pid: u32,
    command: Vec<String>,
    exit_code: Option<i32>,
    success: bool,
}

fn qgraf(case: &Case) -> (Vec<Record>, Vec<ChildProcess>) {
    let config: ExternalConfig = serde_json::from_reader(BufReader::new(
        File::open(std::env::var("BENCH_EXTERNAL_CONFIG").unwrap()).unwrap(),
    ))
    .unwrap();
    assert_eq!(config.raw_format, "qgraf-signed-routed-v1");
    assert_eq!(config.commands.len(), 1);
    let command = &config.commands[0];
    let mut child = Command::new(&command[0])
        .args(&command[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap();
    let pid = child.id();
    let status = child.wait().unwrap();
    let manifest = vec![ChildProcess {
        pid,
        parent_pid: std::process::id(),
        command: command.clone(),
        exit_code: status.code(),
        success: status.success(),
    }];
    assert!(status.success());
    let records = qgraf_records(
        case,
        BufReader::new(File::open(config.output_file).unwrap()),
        config.sign_coefficient,
    );
    (records, manifest)
}

fn observe(records: &[Record]) -> Value {
    let (mut sum, mut xor) = (0u64, 0u64);
    let (mut vertices, mut edges, mut legs, mut vectors, mut positive, mut negative) =
        (0, 0, 0, 0, 0, 0);
    for record in std::hint::black_box(records) {
        let mut hash = 0xcbf29ce484222325u64;
        let mut word = |v: u64| {
            for b in v.to_le_bytes() {
                hash = (hash ^ u64::from(b)).wrapping_mul(0x100000001b3);
            }
        };
        vertices += record.rules.len();
        edges += record.edges.len();
        legs += record.legs.len();
        vectors += record.edges.len() + record.legs.len();
        positive += usize::from(record.sign == 1);
        negative += usize::from(record.sign == -1);
        word(record.aut);
        word(record.multiplicity);
        word(record.sign as u64);
        word(record.rules.len() as u64);
        for &r in &record.rules {
            word(r as u64);
        }
        word(record.legs.len() as u64);
        for l in &record.legs {
            for v in [
                l.vertex as u64,
                l.label as u64,
                u64::from(l.field),
                l.flow as u64,
            ] {
                word(v);
            }
            word(l.momentum.len() as u64);
            for &v in &l.momentum {
                word(v as u64);
            }
        }
        word(record.edges.len() as u64);
        for e in &record.edges {
            for v in [
                e.vertices[0] as u64,
                e.vertices[1] as u64,
                u64::from(e.field),
                e.flow as u64,
            ] {
                word(v);
            }
            word(e.momentum.len() as u64);
            for &v in &e.momentum {
                word(v as u64);
            }
        }
        for basis in [&record.loop_basis, &record.external_basis] {
            word(basis.len() as u64);
            for name in basis {
                word(name.len() as u64);
                for b in name.bytes() {
                    word(u64::from(b));
                }
            }
        }
        sum = sum.wrapping_add(hash);
        xor ^= hash;
    }
    json!({"schema":"signed-routed-owned-records-v1","vertices":vertices,"edges":edges,"legs":legs,"momentum_vectors":vectors,"positive_signs":positive,"negative_signs":negative,"checksum_fnv64_sum":format!("{:016x}",std::hint::black_box(sum)),"checksum_fnv64_xor":format!("{:016x}",std::hint::black_box(xor))})
}

fn dump(path: &str, records: &[Record]) {
    let mut output = BufWriter::new(File::create(path).unwrap());
    for record in records {
        serde_json::to_writer(&mut output, record).unwrap();
        writeln!(output).unwrap();
    }
    output.flush().unwrap();
}

fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("compare") {
        validate::run(&args);
        return;
    }
    assert!(matches!(args[0].as_str(), "linnet" | "qgraf"));
    assert!(matches!(
        args[2].as_str(),
        "signed-routed" | "signed-routed-dump"
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
    // Each backend parses/compiles its own model. QGRAF model/input files are
    // prepared before the process; their native parsing remains timed in QGRAF.
    let native = (args[0] == "linnet").then(|| model::NativeModel::new(&case));
    let setup_ns = started.elapsed().as_nanos();
    let (records, process_manifest) = if let Some(native) = native {
        (native.generate(&case), Vec::new())
    } else {
        qgraf(&case)
    };
    let signed_routed_ns = started.elapsed().as_nanos();
    let count = records.len();
    let observation = observe(&records);
    let observed_ns = started.elapsed().as_nanos();
    if args[2] == "signed-routed-dump" {
        dump(&args[3], &records);
    }
    std::hint::black_box(&records);
    drop(records);
    let destroyed_ns = started.elapsed().as_nanos();
    println!(
        "RESULT {}",
        json!({"status":"ok","engine":args[0],"case":case.name,"mode":args[2],"output_contract":CONTRACT,"setup_ns":setup_ns,"signed_routed_ns":signed_routed_ns,"observed_ns":observed_ns,"destroyed_ns":destroyed_ns,"elapsed_ns":started.elapsed().as_nanos(),"accepted_count":count,"emitted_count":count,"signed_routed_diagram_count":count,"signed_routed_observation":observation,"worker_threads":threads,"driver_pid":std::process::id(),"process_manifest":process_manifest,"scope":"fresh request parse/setup through native generation/signs/routing and fully owned common records; no projector/local rule fragments/aggregate; completion before observation/serialization/drop; whole process includes all stages"})
    );
}
