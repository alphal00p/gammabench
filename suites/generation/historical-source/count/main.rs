use graphica::{GenerationSettings, Graph, HalfEdge};
use linnet::half_edge::{
    HedgeGraph,
    builder::HedgeGraphBuilder,
    generate::{GraphGenerator, Stage, Stub},
    involution::{Flow, Orientation},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs::File,
    io::{BufRead, BufReader, BufWriter, Write},
    sync::{Arc, Mutex},
    time::Instant,
};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
struct S {
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
struct Leg {
    vertex: usize,
    label: usize,
    field: u8,
    flow: i8,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct External {
    label: usize,
    field: u8,
    flow: i8,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct Edge {
    vertices: [usize; 2],
    field: u8,
    flow: i8,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct Record {
    rules: Vec<usize>,
    legs: Vec<Leg>,
    edges: Vec<Edge>,
    aut: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    sign: Option<i8>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct Case {
    name: String,
    fields: Vec<Field>,
    signatures: Vec<Vec<S>>,
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
impl S {
    fn native(&self) -> Stub<u8> {
        match self.flow {
            -1 => Stub::incoming(self.field),
            0 => Stub::undirected(self.field),
            1 => Stub::outgoing(self.field),
            _ => panic!("invalid flow"),
        }
    }
    fn graphica(&self) -> HalfEdge<u8> {
        match self.flow {
            -1 => HalfEdge::incoming(self.field),
            0 => HalfEdge::undirected(self.field),
            1 => HalfEdge::outgoing(self.field),
            _ => panic!("invalid flow"),
        }
    }
}
fn flow(f: Option<Flow>) -> i8 {
    match f {
        None => 0,
        Some(Flow::Source) => 1,
        Some(Flow::Sink) => -1,
    }
}
impl Case {
    fn max_vertices(&self) -> usize {
        self.max_vertices.unwrap_or_else(|| {
            (2 * self.loops + self.legs.len() - 2)
                / (self.signatures.iter().map(Vec::len).min().unwrap() - 2)
        })
    }
    fn rule(&self, mut stubs: Vec<S>) -> usize {
        stubs.sort();
        self.signatures
            .iter()
            .position(|s| {
                let mut s = s.clone();
                s.sort();
                s == stubs
            })
            .expect("unknown vertex signature")
    }
    fn particle_name(&self, s: &S) -> String {
        let f = self.fields.iter().find(|f| f.id == s.field).unwrap();
        if s.flow > 0 {
            f.anti_name.clone()
        } else {
            f.name.clone()
        }
    }
    fn accepted(&self, r: &Record) -> bool {
        if r.edges.len() + 1 < r.rules.len() || r.edges.len() + 1 - r.rules.len() != self.loops {
            return false;
        }
        if self.exact_vertices.is_some_and(|n| n != r.rules.len()) {
            return false;
        }
        if self.simple && !simple(r.edges.iter().map(|e| e.vertices)) {
            return false;
        }
        if !self.allow_self_loops && r.edges.iter().any(|e| e.vertices[0] == e.vertices[1]) {
            return false;
        }
        if let (Some(w), Some(x)) = (&self.weights, self.weight_exact) {
            if r.rules.iter().map(|&r| w[r]).sum::<i32>() != x {
                return false;
            }
        }
        true
    }
}
fn simple(edges: impl Iterator<Item = [usize; 2]>) -> bool {
    let mut seen = BTreeSet::new();
    for mut e in edges {
        e.sort();
        if e[0] == e[1] || !seen.insert(e) {
            return false;
        }
    }
    true
}
fn native_record(d: &linnet::half_edge::generate::Diagram<u8, usize>) -> Record {
    Record {
        rules: d.rules().iter().map(|&r| r as usize).collect(),
        legs: d
            .legs()
            .map(|(v, l, s)| Leg {
                vertex: v,
                label: *l,
                field: *s.field,
                flow: flow(s.flow),
            })
            .collect(),
        edges: d
            .edges()
            .map(|e| {
                let s = e.stub.unwrap();
                Edge {
                    vertices: e.vertices,
                    field: *s.field,
                    flow: flow(s.flow),
                }
            })
            .collect(),
        aut: d.automorphism_count.map(|x| x.to_string()),
        sign: None,
    }
}
fn graphica_record(c: &Case, g: &Graph<usize, u8>, aut: String) -> Record {
    let mut stubs = vec![Vec::new(); g.nodes().len()];
    for e in g.edges() {
        let (a, b) = e.vertices;
        stubs[a].push(S {
            field: e.data,
            flow: if e.directed { 1 } else { 0 },
        });
        stubs[b].push(S {
            field: e.data,
            flow: if e.directed { -1 } else { 0 },
        });
    }
    let mut rules = Vec::new();
    let mut vertices = vec![None; g.nodes().len()];
    for (i, n) in g.nodes().iter().enumerate() {
        if n.data == 0 {
            vertices[i] = Some(rules.len());
            rules.push(c.rule(stubs[i].clone()));
        }
    }
    let (mut legs, mut edges) = (Vec::new(), Vec::new());
    for e in g.edges() {
        let (a, b) = e.vertices;
        let f = if e.directed { 1 } else { 0 };
        match (vertices[a], vertices[b]) {
            (Some(a), Some(b)) => edges.push(Edge {
                vertices: [a, b],
                field: e.data,
                flow: f,
            }),
            (Some(v), None) => legs.push(Leg {
                vertex: v,
                label: g.nodes()[b].data,
                field: e.data,
                flow: f,
            }),
            (None, Some(v)) => legs.push(Leg {
                vertex: v,
                label: g.nodes()[a].data,
                field: e.data,
                flow: -f,
            }),
            _ => panic!("edge between external nodes"),
        }
    }
    Record {
        rules,
        legs,
        edges,
        aut: Some(aut),
        sign: None,
    }
}
#[derive(Default, Serialize)]
struct Census {
    topology_states: usize,
    complete_topology_states: usize,
    field_states: usize,
    complete_states: usize,
    topologies: usize,
    diagrams: usize,
    zero_decoration_topologies: usize,
    #[serde(skip)]
    start_diagrams: usize,
}
fn native(
    c: &Case,
    census: bool,
    count_only: bool,
    threads: usize,
) -> (Vec<Record>, u128, Option<Value>, usize) {
    let state = Arc::new(Mutex::new(Census::default()));
    let observe = state.clone();
    let cc = c.clone();
    let start = Instant::now();
    let mut generator = GraphGenerator::new(
        c.legs.iter().map(|l| {
            (
                l.label,
                S {
                    field: l.field,
                    flow: l.flow,
                }
                .native(),
            )
        }),
        c.signatures
            .iter()
            .map(|s| s.iter().map(S::native).collect()),
    )
    .loops(c.loops..=c.loops)
    .max_vertices(c.max_vertices())
    .allow_self_loops(c.allow_self_loops)
    .allow_zero_flow_edges(c.zero_flow_edges)
    .parallel(!census && threads > 1);
    if census || c.simple || c.exact_vertices.is_some() {
        generator = generator.filter(move |v| {
            if census {
                let mut a = observe.lock().unwrap();
                match v.stage() {
                    Stage::Topology => {
                        a.topology_states += 1;
                        if v.is_topology_complete() {
                            a.complete_topology_states += 1;
                        }
                    }
                    Stage::Fields => a.field_states += 1,
                    Stage::Complete => a.complete_states += 1,
                }
            }
            (!cc.simple || simple(v.edges().map(|e| e.vertices)))
                && (!v.is_topology_complete()
                    || cc.exact_vertices.is_none_or(|n| v.n_vertices() == n))
        });
    }
    if c.one_pi {
        generator = generator.max_bridges(0);
    }
    if let (Some(w), Some(x)) = (&c.weights, c.weight_exact) {
        generator = generator.weight(w.clone(), x..=x);
    }
    if census {
        let a = state.clone();
        generator = generator.progress(move |p| {
            let mut a = a.lock().unwrap();
            if p.topologies != a.topologies {
                if a.topologies > 0 && p.diagrams == a.start_diagrams {
                    a.zero_decoration_topologies += 1;
                }
                a.start_diagrams = p.diagrams;
                a.topologies = p.topologies;
            }
            a.diagrams = p.diagrams;
            true
        });
    }
    let (ds, count) = if count_only {
        (Vec::new(), generator.iter().unwrap().count())
    } else {
        let ds: Vec<_> = generator.iter().unwrap().collect();
        let count = ds.len();
        (ds, count)
    };
    let ns = start.elapsed().as_nanos();
    let report = if census {
        let mut a = state.lock().unwrap();
        if a.topologies > 0 && a.diagrams == a.start_diagrams {
            a.zero_decoration_topologies += 1;
        }
        Some(serde_json::to_value(&*a).unwrap())
    } else {
        None
    };
    (ds.iter().map(native_record).collect(), ns, report, count)
}
fn graphica_run(c: &Case, count_only: bool) -> (Vec<Record>, u128, usize) {
    let cc = c.clone();
    let low_degree = c.signatures.iter().any(|signature| signature.len() <= 2);
    let start = Instant::now();
    let ext: Vec<_> = c
        .legs
        .iter()
        .map(|l| {
            (
                l.label,
                S {
                    field: l.field,
                    flow: -l.flow,
                }
                .graphica(),
            )
        })
        .collect();
    let sig: Vec<_> = c
        .signatures
        .iter()
        .map(|s| s.iter().map(S::graphica).collect())
        .collect();
    let mut settings = GenerationSettings::new()
        .max_vertices(c.max_vertices() + c.legs.len())
        .allow_self_loops(c.allow_self_loops)
        .allow_zero_flow_edges(c.zero_flow_edges)
        .filter_fn(Box::new(move |g: &Graph<usize, u8>, cur: usize| {
            // Graphica's built-in completion estimate rejects the initial degree-one
            // vacuum vertex. E - V + 1 is a safe cycle lower bound even for a
            // disconnected partial graph; retain the finite vertex bound as well.
            if low_degree && g.edges().len() + 1 > g.nodes().len() + cc.loops {
                return false;
            }
            if cc.simple && !simple(g.edges().iter().map(|e| [e.vertices.0, e.vertices.1])) {
                return false;
            }
            if let (Some(w), Some(x)) = (&cc.weights, cc.weight_exact) {
                let mut total = 0;
                for (v, n) in g.nodes().iter().enumerate().take(cur) {
                    if n.data != 0 {
                        continue;
                    }
                    let mut st = Vec::new();
                    for e in g.edges() {
                        if e.vertices.0 == v {
                            st.push(S {
                                field: e.data,
                                flow: if e.directed { 1 } else { 0 },
                            });
                        }
                        if e.vertices.1 == v {
                            st.push(S {
                                field: e.data,
                                flow: if e.directed { -1 } else { 0 },
                            });
                        }
                    }
                    total += w[cc.rule(st)];
                }
                if total > x || (cur == g.nodes().len() && total != x) {
                    return false;
                }
            }
            cur != g.nodes().len()
                || (g.num_loops() == cc.loops
                    && cc
                        .exact_vertices
                        .is_none_or(|n| g.nodes().len() == n + cc.legs.len()))
        }));
    if !low_degree {
        settings = settings.max_loops(c.loops);
    }
    if c.one_pi {
        settings = settings.max_bridges(0);
    }
    let ds = Graph::generate(&ext, &sig, settings).unwrap();
    let ns = start.elapsed().as_nanos();
    (
        if count_only {
            Vec::new()
        } else {
            ds.iter()
                .map(|(g, a)| graphica_record(c, g, a.to_string()))
                .collect()
        },
        ns,
        ds.len(),
    )
}
fn feyngraph_run(c: &Case, count_only: bool) -> Result<(Vec<Record>, u128, usize), String> {
    if c.signatures.iter().any(|s| s.len() < 3) {
        return Err("FeynGraph public degree-partition API requires degrees >=3; internal degree-one tree vertices are unsupported".into());
    }
    let start = Instant::now();
    let mut model = feyngraph::Model::empty();
    for f in &c.fields {
        model.add_particle(
            f.name.clone(),
            f.anti_name.clone(),
            if f.fermi { 1 } else { 0 },
            1,
            f.id as isize + 1,
            f.name.clone(),
            f.anti_name.clone(),
            feyngraph::model::LineStyle::Straight,
            if f.fermi {
                feyngraph::model::Statistic::Fermi
            } else {
                feyngraph::model::Statistic::Bose
            },
        );
    }
    for (i, s) in c.signatures.iter().enumerate() {
        let mut spin = vec![-1; s.len()];
        let fermions: Vec<_> = s
            .iter()
            .enumerate()
            .filter(|(_, s)| c.fields.iter().find(|f| f.id == s.field).unwrap().fermi)
            .map(|(i, _)| i)
            .collect();
        if let [a, b] = fermions[..] {
            spin[a] = b as isize;
            spin[b] = a as isize;
        }
        let mut weights = rustc_hash::FxHashMap::default();
        if let Some(w) = &c.weights {
            weights.insert("X".to_string(), w[i] as usize);
        }
        model
            .add_vertex(
                format!("r{i}"),
                s.iter().map(|s| c.particle_name(s)).collect(),
                spin,
                weights,
            )
            .unwrap();
    }
    let mut select = feyngraph::DiagramSelector::new();
    if !c.allow_self_loops {
        select.select_self_loops(0);
    }
    if c.one_pi {
        select.select_opi_components(1);
    }
    if let Some(x) = c.weight_exact {
        select.select_coupling_power("X", x as usize);
    }
    if c.simple || c.exact_vertices.is_some() {
        let cc = c.clone();
        select.add_topology_function(Arc::new(move |t| {
            cc.exact_vertices
                .is_none_or(|n| t.n_nodes() == n + cc.legs.len())
                && (!cc.simple || simple(t.edges_iter().map(|e| e.connected_nodes)))
        }));
    }
    let input: Vec<_> = c
        .legs
        .iter()
        .map(|l| {
            c.particle_name(&S {
                field: l.field,
                flow: l.flow,
            })
        })
        .collect();
    let genr = feyngraph::DiagramGenerator::new(
        &input,
        Vec::<String>::new(),
        c.loops,
        model,
        Some(select),
    )
    .map_err(|e| e.to_string())?;
    if count_only {
        let count = genr.count();
        return Ok((Vec::new(), start.elapsed().as_nanos(), count));
    }
    let ds = genr.generate();
    let ns = start.elapsed().as_nanos();
    let records = ds
        .views()
        .map(|d| {
            let rules = d
                .vertices()
                .map(|v| {
                    v.interaction()
                        .name()
                        .strip_prefix('r')
                        .unwrap()
                        .parse()
                        .unwrap()
                })
                .collect();
            let legs = d
                .legs()
                .map(|l| {
                    let e = &c.legs[l.id()];
                    Leg {
                        vertex: l.vertex().id(),
                        label: e.label,
                        field: e.field,
                        flow: e.flow,
                    }
                })
                .collect();
            let edges = d
                .propagators()
                .map(|p| {
                    let part = p.particle();
                    let f = c
                        .fields
                        .iter()
                        .find(|f| &f.name == part.name() || &f.anti_name == part.name())
                        .unwrap();
                    Edge {
                        vertices: [p.vertex(0).id(), p.vertex(1).id()],
                        field: f.id,
                        flow: if part.self_anti() {
                            0
                        } else if part.is_anti() {
                            -1
                        } else {
                            1
                        },
                    }
                })
                .collect();
            Record {
                rules,
                legs,
                edges,
                aut: Some(d.symmetry_factor().to_string()),
                sign: Some(d.sign()),
            }
        })
        .collect();
    Ok((records, ns, ds.len()))
}
fn validate_record(c: &Case, r: &Record) {
    assert!(c.accepted(r), "record outside case");
    let mut incidence = vec![Vec::new(); r.rules.len()];
    for e in &r.edges {
        assert!((-1..=1).contains(&e.flow));
        incidence[e.vertices[0]].push(S {
            field: e.field,
            flow: e.flow,
        });
        incidence[e.vertices[1]].push(S {
            field: e.field,
            flow: -e.flow,
        });
    }
    for l in &r.legs {
        incidence[l.vertex].push(S {
            field: l.field,
            flow: l.flow,
        });
    }
    for (i, mut actual) in incidence.into_iter().enumerate() {
        let mut expected = c.signatures[r.rules[i]].clone();
        actual.sort();
        expected.sort();
        assert_eq!(actual, expected, "vertex signature mismatch");
    }
    let mut actual: Vec<_> = r.legs.iter().map(|l| (l.label, l.field, l.flow)).collect();
    let mut expected: Vec<_> = c.legs.iter().map(|l| (l.label, l.field, l.flow)).collect();
    actual.sort();
    expected.sort();
    assert_eq!(actual, expected, "external contract mismatch");
    let graph = plain_graph(r);
    assert!(graph.is_connected(), "disconnected graph");
    if c.one_pi {
        let mut tree = graph.get_spanning_tree(0);
        tree.chain_decomposition();
        assert_eq!(tree.count_bridges(), 0, "internal bridge forbidden");
    }
}
fn hedge_graph(r: &Record) -> HedgeGraph<u8, usize, Option<usize>> {
    let mut b = HedgeGraphBuilder::new();
    let ns: Vec<_> = r.rules.iter().map(|&x| b.add_node(x)).collect();
    for e in &r.edges {
        let [a, z] = e.vertices;
        let (o, a, z) = match e.flow {
            -1 => (Orientation::Default, z, a),
            0 => (Orientation::Undirected, a, z),
            1 => (Orientation::Default, a, z),
            _ => panic!("flow"),
        };
        b.add_edge(ns[a].add_data(None), ns[z].add_data(None), e.field, o);
    }
    for l in &r.legs {
        b.add_external_edge(
            ns[l.vertex].add_data(Some(l.label)),
            l.field,
            if l.flow == 0 {
                Orientation::Undirected
            } else {
                Orientation::Default
            },
            if l.flow < 0 { Flow::Sink } else { Flow::Source },
        );
    }
    b.build()
}
fn plain_graph(r: &Record) -> Graph<(u8, usize), u8> {
    let mut g = Graph::new();
    for &rule in &r.rules {
        g.add_node((0, rule));
    }
    for l in &r.legs {
        let ext = g.add_node((1, l.label));
        let (a, b) = if l.flow < 0 {
            (ext, l.vertex)
        } else {
            (l.vertex, ext)
        };
        g.add_edge(a, b, l.flow != 0, l.field).unwrap();
    }
    for e in &r.edges {
        let [a, b] = e.vertices;
        let (a, b) = if e.flow < 0 { (b, a) } else { (a, b) };
        g.add_edge(a, b, e.flow != 0, e.field).unwrap();
    }
    g
}
fn canonical(r: &Record) -> Value {
    let hg = hedge_graph(r);
    let n = hg.canonize_by(|x| *x, |x| *x, |x| *x);
    let p = plain_graph(r).canonize();
    let aut = n.automorphism_count().unwrap().to_string();
    assert_eq!(
        aut,
        p.automorphism_group_size.to_string(),
        "independent canonizer aut mismatch"
    );
    if let Some(a) = &r.aut {
        assert_eq!(&aut, a, "reported aut mismatch");
    }
    let f = n.canonical_form();
    json!({"native_key":[f.words(),f.node_keys(),f.edge_keys(),f.hedge_keys()],"graphica_key":{"nodes":p.graph.nodes().iter().map(|n|n.data).collect::<Vec<_>>(),"edges":p.graph.edges().iter().map(|e|(e.vertices,e.directed,e.data)).collect::<Vec<_>>()},"aut":aut})
}
fn gcd(mut a: u128, mut b: u128) -> u128 {
    while b != 0 {
        let c = a % b;
        a = b;
        b = c;
    }
    a
}
fn summary(records: &[Record]) -> Value {
    let (mut n, mut d) = (0u128, 1u128);
    for r in records {
        let a: u128 = r.aut.as_ref().unwrap().parse().unwrap();
        let g = gcd(d, a);
        n = n.checked_mul(a / g).unwrap().checked_add(d / g).unwrap();
        d = d.checked_mul(a / g).unwrap();
        let g = gcd(n, d);
        n /= g;
        d /= g;
    }
    json!({"numerator":n.to_string(),"denominator":d.to_string()})
}
fn dump(path: &str, rows: impl IntoIterator<Item = Value>) {
    let mut w = BufWriter::new(File::create(path).unwrap());
    for r in rows {
        serde_json::to_writer(&mut w, &r).unwrap();
        w.write_all(b"\n").unwrap();
    }
    w.flush().unwrap();
}
fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("list") {
        println!("{}", include_str!("../cases.json"));
        return;
    }
    let cases: Vec<Case> = match std::env::var("BENCH_CASE_FILE") {
        Ok(path) => {
            vec![serde_json::from_reader(BufReader::new(File::open(path).unwrap())).unwrap()]
        }
        Err(_) => serde_json::from_str(include_str!("../cases.json")).unwrap(),
    };
    if args.first().map(String::as_str) == Some("validate") {
        let c = cases.iter().find(|c| c.name == args[1]).unwrap();
        let rs: Vec<Record> = BufReader::new(File::open(&args[2]).unwrap())
            .lines()
            .map(|l| serde_json::from_str(&l.unwrap()).unwrap())
            .collect();
        let mut rows = Vec::new();
        let mut seen = BTreeSet::new();
        for r in &rs {
            validate_record(c, r);
            let row = canonical(r);
            assert!(
                seen.insert(row["native_key"].to_string()),
                "duplicate diagram"
            );
            rows.push(row);
        }
        rows.sort_by_key(|r| r["native_key"].to_string());
        dump(&args[3], rows);
        println!(
            "RESULT {}",
            json!({"validation":"pass","case":c.name,"count":rs.len(),"reported_weights":rs.iter().all(|r|r.aut.is_some())})
        );
        return;
    }
    let engine = &args[0];
    let c = cases
        .iter()
        .find(|c| c.name == args[1])
        .expect("unknown case");
    let mode = args.get(2).map(String::as_str).unwrap_or("enumerate");
    let threads: usize = std::env::var("BENCH_THREADS")
        .unwrap_or_else(|_| "1".to_owned())
        .parse()
        .unwrap();
    assert!(threads > 0);
    if mode == "census" {
        assert_eq!(threads, 1, "census requires sequential callbacks");
    }
    if engine == "feyngraph" || threads > 1 {
        rayon::ThreadPoolBuilder::new()
            .num_threads(threads)
            .build_global()
            .unwrap();
    }
    let setup = Instant::now();
    let count_only = mode == "count" || mode == "census";
    let (records, ns, census, emitted) = match engine.as_str() {
        "linnet" => native(c, mode == "census", count_only, threads),
        "graphica" => {
            let (r, n, count) = graphica_run(c, count_only);
            (r, n, None, count)
        }
        "feyngraph" => match feyngraph_run(c, count_only) {
            Ok((r, n, count)) => (r, n, None, count),
            Err(e) => {
                println!(
                    "RESULT {}",
                    json!({"engine":engine,"case":c.name,"status":"unsupported","reason":e})
                );
                return;
            }
        },
        _ => panic!("unknown engine"),
    };
    let records: Vec<_> = records.into_iter().filter(|r| c.accepted(r)).collect();
    let weight = if count_only {
        Value::Null
    } else {
        summary(&records)
    };
    let accepted = if count_only { emitted } else { records.len() };
    let mut materialization_ns = None;
    if mode == "dump" {
        dump(
            &args[3],
            records.iter().map(|r| serde_json::to_value(r).unwrap()),
        );
    } else if mode == "canonical" {
        let t = Instant::now();
        let rows: Vec<_> = records.iter().map(canonical).collect();
        materialization_ns = Some(t.elapsed().as_nanos());
        if let Some(path) = args.get(3) {
            dump(path, rows);
        }
    }
    println!(
        "RESULT {}",
        json!({"status":"ok","engine":engine,"case":c.name,"mode":mode,"elapsed_ns":ns,"adapter_total_ns":setup.elapsed().as_nanos(),"emitted_count":emitted,"accepted_count":accepted,"sum_inverse_aut":weight,"canonical_validation_ns":materialization_ns,"census":census,"worker_threads":threads,"engine_parallel":engine!="graphica"&&threads>1,"scope":"request compilation plus public count/enumerate; count excludes neutral transport and weights entirely; Graphica rejects lower-order completions before canonization; FeynGraph count includes topology momentum work, owned enumeration also computes signs"})
    );
}
