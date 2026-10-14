use graphica::{GenerationSettings, Graph, HalfEdge};
use linnet::half_edge::{
    HedgeGraph,
    builder::HedgeGraphBuilder,
    generate::{GraphGenerator, Stub},
    involution::{Flow, Orientation},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::{BufRead, BufReader, BufWriter, Write},
    process::{Command, Stdio},
    sync::Arc,
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
    aut: String,
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
    #[serde(skip)]
    rule_ids: BTreeMap<Vec<S>, usize>,
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
    fn compile_rules(&mut self) {
        self.rule_ids.clear();
        for (id, signature) in self.signatures.iter().enumerate() {
            let mut key = signature.clone();
            key.sort();
            assert!(
                self.rule_ids.insert(key, id).is_none(),
                "duplicate signature"
            );
        }
    }
    fn rule(&self, mut stubs: Vec<S>) -> usize {
        stubs.sort();
        *self.rule_ids.get(&stubs).expect("unknown vertex signature")
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
        aut: d
            .automorphism_count
            .expect("native automorphism count")
            .to_string(),
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
        aut,
    }
}
fn native(c: &Case, threads: usize) -> Vec<Record> {
    let cc = c.clone();
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
    .parallel(threads > 1);
    if c.simple || c.exact_vertices.is_some() {
        generator = generator.filter(move |v| {
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
    generator
        .iter()
        .unwrap()
        .map(|d| native_record(&d))
        .collect()
}
fn graphica_run(c: &Case) -> Vec<Record> {
    let cc = c.clone();
    let low_degree = c.signatures.iter().any(|signature| signature.len() <= 2);
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
    ds.iter()
        .map(|(g, a)| graphica_record(c, g, a.to_string()))
        .collect()
}
fn feyngraph_run(c: &Case) -> Result<Vec<Record>, String> {
    if c.signatures.iter().any(|s| s.len() < 3) {
        return Err("FeynGraph public degree-partition API requires degrees >=3; internal degree-one tree vertices are unsupported".into());
    }
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
    let ds = genr.generate();
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
                aut: d.symmetry_factor().to_string(),
            }
        })
        .collect();
    Ok(records)
}
// The scalar topology route has one field assignment per accepted topology.
// This is a separate public API comparison; the original diagram route is unchanged.
fn feyngraph_topology_run(c: &Case) -> Result<Vec<Record>, String> {
    let [field] = c.fields.as_slice() else {
        return Err("topology-only route requires exactly one field".into());
    };
    if field.name != field.anti_name || field.fermi {
        return Err("topology-only route covers one self-conjugate bosonic field".into());
    }
    let mut degrees = BTreeSet::new();
    if c.signatures.is_empty()
        || c.signatures.iter().any(|signature| {
            signature.len() < 3
                || !degrees.insert(signature.len())
                || signature.iter().any(|s| s.field != field.id || s.flow != 0)
        })
    {
        return Err("topology-only route requires unique degrees >=3 and undirected uniform-field signatures".into());
    }
    if c.legs.iter().any(|l| l.field != field.id || l.flow != 0)
        || c.legs
            .iter()
            .map(|l| l.label)
            .collect::<BTreeSet<_>>()
            .len()
            != c.legs.len()
    {
        return Err(
            "topology-only route requires distinct external labels with the same undirected field"
                .into(),
        );
    }
    if !c.zero_flow_edges || c.weights.is_some() || c.weight_exact.is_some() {
        return Err(
            "topology-only route does not translate zero-flow vetoes or coupling budgets".into(),
        );
    }
    let excess = c
        .loops
        .checked_mul(2)
        .and_then(|n| n.checked_add(c.legs.len()))
        .and_then(|n| n.checked_sub(2));
    if excess.is_none_or(|n| n == 0) {
        return Err(
            "topology-only route requires a nonempty degree>=3 interaction topology".into(),
        );
    }
    let mut selector = feyngraph::topology::TopologySelector::new();
    if !c.allow_self_loops {
        selector.select_self_loops(0);
    }
    if c.one_pi {
        selector.select_opi_components(1);
    }
    if c.simple || c.exact_vertices.is_some() || c.max_vertices.is_some() {
        let n_external = c.legs.len();
        let exact = c.exact_vertices;
        let maximum = c.max_vertices;
        let is_simple = c.simple;
        selector.add_custom_function(Arc::new(move |topology| {
            let internal = topology.n_nodes() - n_external;
            exact.is_none_or(|n| internal == n)
                && maximum.is_none_or(|n| internal <= n)
                && (!is_simple || simple(topology.edges_iter().map(|e| e.connected_nodes)))
        }));
    }
    let generator = feyngraph::topology::TopologyGenerator::new(
        c.legs.len(),
        c.loops,
        feyngraph::model::TopologyModel::from(degrees.into_iter().collect::<Vec<_>>()),
        Some(selector),
    );
    let topologies = generator.generate();
    let n_external = c.legs.len();
    let mut records = Vec::with_capacity(topologies.len());
    for index in 0..topologies.len() {
        let topology = topologies.get(index);
        let rules = topology
            .nodes_iter()
            .skip(n_external)
            .map(|node| {
                c.signatures
                    .iter()
                    .position(|s| s.len() == node.degree)
                    .expect("known degree")
            })
            .collect();
        let mut legs = Vec::with_capacity(n_external);
        let mut edges = Vec::new();
        for edge in topology.edges_iter() {
            let [a, b] = edge.connected_nodes;
            match (a < n_external, b < n_external) {
                (false, false) => edges.push(Edge {
                    vertices: [a - n_external, b - n_external],
                    field: field.id,
                    flow: 0,
                }),
                (true, false) | (false, true) => {
                    let (external, internal) = if a < n_external { (a, b) } else { (b, a) };
                    legs.push(Leg {
                        vertex: internal - n_external,
                        label: c.legs[external].label,
                        field: field.id,
                        flow: 0,
                    });
                }
                (true, true) => {
                    return Err(
                        "unexpected external-only edge in a connected interaction topology".into(),
                    );
                }
            }
        }
        // This pinned public API exposes its native exact symmetry denominator
        // only through Display. Formatting and parsing are included in timing;
        // no independent canonizer is used to manufacture a missing weight.
        let formatted = topology.to_string();
        let aut = formatted
            .lines()
            .find_map(|line| line.trim().strip_prefix("SymmetryFactor: 1/"))
            .expect("public topology symmetry denominator")
            .to_owned();
        assert!(aut.parse::<u128>().unwrap() > 0);
        records.push(Record {
            rules,
            legs,
            edges,
            aut,
        });
    }
    Ok(records)
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
    assert_eq!(aut, r.aut, "reported aut mismatch");
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
        let a: u128 = r.aut.parse().unwrap();
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
#[derive(Deserialize)]
struct ExternalConfig {
    commands: Vec<Vec<String>>,
    #[serde(alias = "output_path")]
    output_file: Option<String>,
    raw_format: String,
}
#[derive(Serialize)]
struct ChildProcess {
    pid: u32,
    parent_pid: u32,
    command: Vec<String>,
    exit_code: Option<i32>,
    success: bool,
}
fn qgraf_records(c: &Case, reader: impl BufRead) -> Vec<Record> {
    let mut names = BTreeMap::new();
    for f in &c.fields {
        if f.name == f.anti_name {
            assert!(
                names
                    .insert(
                        f.name.as_str(),
                        S {
                            field: f.id,
                            flow: 0
                        }
                    )
                    .is_none()
            );
        } else {
            assert!(
                names
                    .insert(
                        f.name.as_str(),
                        S {
                            field: f.id,
                            flow: -1
                        }
                    )
                    .is_none()
            );
            assert!(
                names
                    .insert(
                        f.anti_name.as_str(),
                        S {
                            field: f.id,
                            flow: 1
                        }
                    )
                    .is_none()
            );
        }
    }
    let mut records = Vec::new();
    let mut current: Option<Record> = None;
    let mut last_index = 0;
    let mut final_count = None;
    let finish = |r: Record| {
        assert!(
            r.rules.iter().all(|&rule| rule < c.signatures.len()),
            "missing or invalid native vertex rule"
        );
        r
    };
    for line in reader.lines() {
        let line = line.unwrap();
        let mut parts = line.split_whitespace();
        let Some(tag) = parts.next() else { continue };
        match tag {
            "FORMAT" => assert_eq!(parts.next(), Some("qgraf-neutral-v2")),
            "D" => {
                if let Some(r) = current.take() {
                    records.push(finish(r));
                }
                let index: usize = parts.next().unwrap().parse().unwrap();
                assert_eq!(index, last_index + 1);
                last_index = index;
                assert!(matches!(parts.next(), Some("+" | "-")));
                let weight = parts.next().unwrap();
                let aut = if weight == "1" {
                    "1"
                } else {
                    weight
                        .strip_prefix("1/")
                        .expect("inverse integer symmetry factor")
                };
                assert!(aut.parse::<u128>().unwrap() > 0);
                current = Some(Record {
                    rules: Vec::new(),
                    legs: Vec::new(),
                    edges: Vec::new(),
                    aut: aut.to_owned(),
                });
            }
            "V" => {
                let vertex = parts
                    .next()
                    .unwrap()
                    .parse::<usize>()
                    .unwrap()
                    .checked_sub(1)
                    .unwrap();
                let rule: usize = parts.next().unwrap().parse().unwrap();
                assert!(rule < c.signatures.len());
                let rules = &mut current.as_mut().unwrap().rules;
                if rules.len() <= vertex {
                    rules.resize(vertex + 1, usize::MAX);
                }
                assert_eq!(rules[vertex], usize::MAX, "duplicate native vertex rule");
                rules[vertex] = rule;
            }
            "L" => {
                let label = parts.next().unwrap().parse().unwrap();
                let vertex = parts
                    .next()
                    .unwrap()
                    .parse::<usize>()
                    .unwrap()
                    .checked_sub(1)
                    .unwrap();
                let stub = &names[parts.next().unwrap()];
                current.as_mut().unwrap().legs.push(Leg {
                    vertex,
                    label,
                    field: stub.field,
                    flow: stub.flow,
                });
            }
            "E" => {
                let a = parts
                    .next()
                    .unwrap()
                    .parse::<usize>()
                    .unwrap()
                    .checked_sub(1)
                    .unwrap();
                let b = parts
                    .next()
                    .unwrap()
                    .parse::<usize>()
                    .unwrap()
                    .checked_sub(1)
                    .unwrap();
                let stub = &names[parts.next().unwrap()];
                current.as_mut().unwrap().edges.push(Edge {
                    vertices: [a, b],
                    field: stub.field,
                    flow: stub.flow,
                });
            }
            "END" => final_count = Some(parts.next().unwrap().parse::<usize>().unwrap()),
            _ => panic!("unknown QGRAF wire record: {tag}"),
        }
        assert!(parts.next().is_none(), "unexpected trailing wire fields");
    }
    if let Some(r) = current {
        records.push(finish(r));
    }
    assert_eq!(
        final_count,
        Some(last_index),
        "missing or invalid QGRAF end record"
    );
    assert_eq!(records.len(), last_index);
    records
}
fn nauty_records(c: &Case, reader: impl BufRead) -> Vec<Record> {
    assert!(c.legs.is_empty());
    let [field] = c.fields.as_slice() else {
        panic!("one uncoloured field required")
    };
    assert!(
        c.signatures
            .iter()
            .flatten()
            .all(|s| s.field == field.id && s.flow == 0)
    );
    let degree_rules: BTreeMap<_, _> = c
        .signatures
        .iter()
        .enumerate()
        .map(|(i, s)| (s.len(), i))
        .collect();
    assert_eq!(degree_rules.len(), c.signatures.len());
    let mut records = Vec::new();
    for line in reader.lines() {
        let line = line.unwrap();
        let mut parts = line.split_whitespace();
        let Some(n) = parts.next() else { continue };
        let n: usize = n.parse().unwrap();
        let bundles: usize = parts.next().unwrap().parse().unwrap();
        let mut aut: u128 = parts.next().unwrap().parse().unwrap();
        assert!(aut > 0);
        let mut degree = vec![0; n];
        let mut edges = Vec::new();
        for _ in 0..bundles {
            let a: usize = parts.next().unwrap().parse().unwrap();
            let b: usize = parts.next().unwrap().parse().unwrap();
            let multiplicity: usize = parts.next().unwrap().parse().unwrap();
            assert!(multiplicity > 0);
            degree[a] += multiplicity;
            degree[b] += multiplicity;
            // multig/callback supplies the vertex stabilizer. Lift it to the
            // edge incidences: permute equal edges and flip undirected loops.
            for k in 1..=multiplicity {
                aut = aut.checked_mul(k as u128).unwrap();
                if a == b {
                    aut = aut.checked_mul(2).unwrap();
                }
                edges.push(Edge {
                    vertices: [a, b],
                    field: field.id,
                    flow: 0,
                });
            }
        }
        assert!(parts.next().is_none(), "unexpected trailing multig fields");
        records.push(Record {
            rules: degree.iter().map(|d| degree_rules[d]).collect(),
            legs: Vec::new(),
            edges,
            aut: aut.to_string(),
        });
    }
    records
}
fn external(c: &Case) -> (Vec<Record>, Vec<ChildProcess>) {
    let config: ExternalConfig = serde_json::from_reader(BufReader::new(
        File::open(std::env::var("BENCH_EXTERNAL_CONFIG").expect("external launch config"))
            .unwrap(),
    ))
    .unwrap();
    assert!(!config.commands.is_empty());
    let mut children = Vec::new();
    let mut previous = None;
    for (index, argv) in config.commands.iter().enumerate() {
        assert!(!argv.is_empty());
        let mut command = Command::new(&argv[0]);
        command.args(&argv[1..]).stderr(Stdio::inherit());
        if let Some(pipe) = previous.take() {
            command.stdin(Stdio::from(pipe));
        } else {
            command.stdin(Stdio::null());
        }
        let last = index + 1 == config.commands.len();
        if !last || config.output_file.is_none() {
            command.stdout(Stdio::piped());
        } else {
            command.stdout(Stdio::inherit());
        }
        let mut child = command.spawn().expect("launch external engine");
        previous = child.stdout.take();
        children.push((argv.clone(), child));
    }
    let records = if config.output_file.is_none() {
        assert_eq!(config.raw_format, "multig-G");
        nauty_records(c, BufReader::new(previous.take().unwrap()))
    } else {
        Vec::new()
    };
    let mut manifest = Vec::new();
    for (command, mut child) in children {
        let pid = child.id();
        let status = child.wait().expect("wait external engine");
        manifest.push(ChildProcess {
            pid,
            parent_pid: std::process::id(),
            command,
            exit_code: status.code(),
            success: status.success(),
        });
        assert!(status.success(), "external engine failed: {status}");
    }
    let records = if let Some(path) = config.output_file {
        assert_eq!(config.raw_format, "qgraf-neutral-v2");
        qgraf_records(c, BufReader::new(File::open(path).unwrap()))
    } else {
        records
    };
    (records, manifest)
}
fn observe(records: &[Record]) -> Value {
    // Every owned field is read. Record hashes preserve local numbering, but
    // their multiset accumulator permits arbitrary native output ordering.
    // Cross-engine equality is established by separate canonical validation.
    let (mut sum_hash, mut xor_hash) = (0u64, 0u64);
    let (mut vertices, mut legs, mut edges) = (0usize, 0usize, 0usize);
    for r in std::hint::black_box(records) {
        let mut hash = 0xcbf29ce484222325u64;
        let mut word = |x: u64| {
            for b in x.to_le_bytes() {
                hash = (hash ^ u64::from(b)).wrapping_mul(0x100000001b3);
            }
        };
        vertices += r.rules.len();
        legs += r.legs.len();
        edges += r.edges.len();
        word(r.rules.len() as u64);
        for &rule in &r.rules {
            word(rule as u64);
        }
        word(r.legs.len() as u64);
        for l in &r.legs {
            word(l.vertex as u64);
            word(l.label as u64);
            word(l.field as u64);
            word((l.flow + 1) as u64);
        }
        word(r.edges.len() as u64);
        for e in &r.edges {
            word(e.vertices[0] as u64);
            word(e.vertices[1] as u64);
            word(e.field as u64);
            word((e.flow + 1) as u64);
        }
        word(r.aut.len() as u64);
        for b in r.aut.bytes() {
            word(u64::from(b));
        }
        sum_hash = sum_hash.wrapping_add(hash);
        xor_hash ^= hash;
    }
    json!({"checksum_fnv64_sum":format!("{:016x}",std::hint::black_box(sum_hash)),
        "checksum_fnv64_xor":format!("{:016x}",std::hint::black_box(xor_hash)),
        "vertices":vertices,"legs":legs,"edges":edges})
}
fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("list") {
        println!("{}", include_str!("../cases.json"));
        return;
    }
    let mut cases: Vec<Case> = match std::env::var("BENCH_CASE_FILE") {
        Ok(path) => {
            vec![serde_json::from_reader(BufReader::new(File::open(path).unwrap())).unwrap()]
        }
        Err(_) => serde_json::from_str(include_str!("../cases.json")).unwrap(),
    };
    let engine = &args[0];
    let c = cases
        .iter_mut()
        .find(|c| c.name == args[1])
        .expect("known case");
    if engine == "validate" {
        c.compile_rules();
        let records: Vec<Record> = BufReader::new(File::open(&args[2]).unwrap())
            .lines()
            .map(|line| serde_json::from_str(&line.unwrap()).unwrap())
            .collect();
        let mut rows = Vec::new();
        let mut seen = BTreeSet::new();
        for r in &records {
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
            json!({"engine":"validator","validation":"pass","case":c.name,"count":records.len(),
            "reported_weights":true,"sum_inverse_aut":summary(&records),"observation":observe(&records),
            "driver_pid":std::process::id(),"process_manifest":[]})
        );
        return;
    }
    let mode = args.get(2).map(String::as_str).unwrap_or("materialize");
    assert!(matches!(mode, "materialize" | "dump"));
    let threads: usize = std::env::var("BENCH_THREADS")
        .unwrap_or_else(|_| "1".into())
        .parse()
        .unwrap();
    assert!(threads > 0);
    let started = Instant::now();
    c.compile_rules();
    if matches!(engine.as_str(), "feyngraph" | "feyngraph-topology") || threads > 1 {
        rayon::ThreadPoolBuilder::new()
            .num_threads(threads)
            .build_global()
            .unwrap();
    }
    let mut process_manifest = Vec::new();
    let records = match engine.as_str() {
        "linnet" => Ok(native(c, threads)),
        "graphica" => Ok(graphica_run(c)),
        "feyngraph" => feyngraph_run(c),
        "feyngraph-topology" => feyngraph_topology_run(c),
        "qgraf" | "nauty" => {
            let (records, manifest) = external(c);
            process_manifest = manifest;
            Ok(records)
        }
        _ => panic!("unknown engine"),
    };
    let records = match records {
        Ok(r) => r,
        Err(reason) => {
            println!(
                "RESULT {}",
                json!({"status":"unsupported","engine":engine,"case":c.name,"reason":reason,"driver_pid":std::process::id(),"process_manifest":[]})
            );
            return;
        }
    };
    let materialized_ns = started.elapsed().as_nanos();
    let observation = observe(&records);
    let weight = summary(&records);
    let elapsed_ns = started.elapsed().as_nanos();
    if mode == "dump" {
        dump(
            &args[3],
            records.iter().map(|r| serde_json::to_value(r).unwrap()),
        );
    }
    println!(
        "RESULT {}",
        json!({"status":"ok","engine":engine,"case":c.name,"mode":mode,
        "elapsed_ns":elapsed_ns,"materialized_ns":materialized_ns,"emitted_count":records.len(),
        "accepted_count":records.len(),"sum_inverse_aut":weight,"observation":observation,
        "worker_threads":threads,"engine_parallel":matches!(engine.as_str(),"linnet"|"feyngraph"|"feyngraph-topology")&&threads>1,
        "driver_pid":std::process::id(),"process_manifest":process_manifest,
        "scope":"operation timer excludes selected-case JSON parsing and final record destruction (both included in whole-process metrics); includes request compilation, complete native generation and symmetry factors, owned neutral incidence/rule/leg materialization, all-field observation, exact sum of inverse automorphism orders; retained records drop before process exit; no timed canonicalization or JSONL serialization"})
    );
    std::hint::black_box(&records);
}
