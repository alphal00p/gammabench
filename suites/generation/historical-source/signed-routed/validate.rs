//! Independent post-clock graph and affine routing certificates.
use super::*;
use graphica::Graph;
use numerica::{
    domains::rational::{Q, Rational, RationalField},
    tensors::matrix::Matrix,
};

struct Canonical {
    key: String,
    rows: Vec<Vec<i32>>,
    factor: Rational,
}

fn matrix(rows: &[Vec<i32>], columns: std::ops::Range<usize>) -> Matrix<RationalField> {
    Matrix::from_linear(
        rows.iter()
            .flat_map(|row| columns.clone().map(|i| Rational::from(row[i])))
            .collect(),
        rows.len() as u32,
        columns.len() as u32,
        Q,
    )
    .unwrap()
}

impl Record {
    fn certify(&self, case: &Case) -> Canonical {
        assert!(self.aut > 0 && self.multiplicity > 0 && matches!(self.sign, -1 | 1));
        assert_eq!(self.loop_basis.len(), case.loops);
        assert_eq!(
            self.external_basis,
            (1..case.legs.len())
                .map(|i| format!("p{i}"))
                .collect::<Vec<_>>()
        );
        assert_eq!(
            self.loop_basis.iter().collect::<BTreeSet<_>>().len(),
            case.loops
        );
        let n = self.rules.len();
        assert_eq!(self.edges.len() + 1 - n, case.loops);
        if let Some(exact) = case.exact_vertices {
            assert_eq!(n, exact);
        }
        assert!(n <= case.max_vertices());
        if let (Some(weights), Some(exact)) = (&case.weights, case.weight_exact) {
            assert_eq!(
                self.rules.iter().map(|&rule| weights[rule]).sum::<i32>(),
                exact
            );
        }
        let width = case.loops + case.legs.len().saturating_sub(1);
        let mut conservation = vec![vec![0; width]; n];
        let mut incidence = vec![Vec::new(); n];
        let mut seen_edges = BTreeSet::new();
        let mut graph = Graph::new();
        for &rule in &self.rules {
            assert!(rule < case.signatures.len());
            graph.add_node((0, rule));
        }
        for edge in &self.edges {
            let [a, b] = edge.vertices;
            assert!(a < n && b < n && (-1..=1).contains(&edge.flow));
            assert_eq!(edge.momentum.len(), width);
            if !case.allow_self_loops {
                assert_ne!(a, b);
            }
            if case.simple {
                let mut endpoints = edge.vertices;
                endpoints.sort();
                assert_ne!(a, b);
                assert!(seen_edges.insert(endpoints));
            }
            incidence[a].push(Stub {
                field: edge.field,
                flow: edge.flow,
            });
            incidence[b].push(Stub {
                field: edge.field,
                flow: -edge.flow,
            });
            for (i, &q) in edge.momentum.iter().enumerate() {
                conservation[a][i] += q;
                conservation[b][i] -= q;
            }
            let (source, target) = if edge.flow < 0 { (b, a) } else { (a, b) };
            graph
                .add_edge(source, target, edge.flow != 0, edge.field)
                .unwrap();
        }
        let mut external_labels = BTreeSet::new();
        for leg in &self.legs {
            assert!(leg.vertex < n && external_labels.insert(leg.label));
            let expected = case.legs.iter().find(|l| l.label == leg.label).unwrap();
            assert_eq!((leg.field, leg.flow), (expected.field, expected.flow));
            assert_eq!(
                leg.momentum,
                momentum(&format!("p{}", leg.label), case),
                "external incoming convention"
            );
            incidence[leg.vertex].push(Stub {
                field: leg.field,
                flow: leg.flow,
            });
            for (i, &q) in leg.momentum.iter().enumerate() {
                conservation[leg.vertex][i] += q;
            }
            let ext = graph.add_node((1, leg.label));
            let (source, target) = if leg.flow < 0 {
                (ext, leg.vertex)
            } else {
                (leg.vertex, ext)
            };
            graph
                .add_edge(source, target, leg.flow != 0, leg.field)
                .unwrap();
        }
        assert_eq!(external_labels.len(), case.legs.len());
        assert!(
            conservation.iter().all(|row| row.iter().all(|&q| q == 0)),
            "momentum conservation: {conservation:?}"
        );
        for (vertex, mut stubs) in incidence.into_iter().enumerate() {
            stubs.sort();
            let mut expected = case.signatures[self.rules[vertex]].clone();
            expected.sort();
            assert_eq!(stubs, expected, "rule incidence vertex{vertex}");
        }
        let mut tree = graph.get_spanning_tree(0);
        assert!(tree.is_connected());
        if case.one_pi {
            tree.chain_decomposition();
            assert_eq!(tree.count_bridges(), 0);
        }
        if case.loops > 0 {
            // Restricting a conserved cycle to the chords of any spanning tree
            // identifies the integer cycle lattice. Rank alone cannot rule out
            // a scaled/sublattice loop basis; a unit determinant can.
            let tree_edges: BTreeSet<_> =
                tree.nodes.iter().filter_map(|node| node.edge_id).collect();
            let chord_rows: Vec<_> = self
                .edges
                .iter()
                .enumerate()
                .filter(|(id, _)| !tree_edges.contains(id))
                .map(|(_, edge)| edge.momentum.clone())
                .collect();
            assert_eq!(chord_rows.len(), case.loops);
            let determinant = matrix(&chord_rows, 0..case.loops).det().unwrap();
            assert!(
                determinant == 1 || determinant == -1,
                "native loop basis does not span the primitive cycle lattice"
            );
        }
        let canonical = graph.canonize();
        assert_eq!(
            canonical.automorphism_group_size.to_string(),
            self.aut.to_string(),
            "native automorphism order"
        );
        let mut rules = vec![usize::MAX; n + case.legs.len()];
        for (old, &rule) in self.rules.iter().enumerate() {
            rules[canonical.vertex_map[old]] = rule;
        }
        let mut edges: Vec<_> = self
            .edges
            .iter()
            .enumerate()
            .map(|(index, edge)| {
                let [mut a, mut b] = edge.vertices.map(|v| canonical.vertex_map[v]);
                let mut q = edge.momentum.clone();
                if edge.flow < 0 || (edge.flow == 0 && a > b) {
                    std::mem::swap(&mut a, &mut b);
                    q.iter_mut().for_each(|v| *v = -*v);
                }
                ((a, b, edge.field, i8::from(edge.flow != 0)), index, q)
            })
            .collect();
        // Equal parallel edges can use any deterministic bijection: each is a
        // legal graph isomorphism. Routing equivalence is proved for that map,
        // rather than selecting a map based on the observed momentum vectors.
        edges.sort_by_key(|(key, index, _)| (*key, *index));
        let mut legs: Vec<_> = self
            .legs
            .iter()
            .map(|leg| {
                (
                    (
                        leg.label,
                        canonical.vertex_map[leg.vertex],
                        leg.field,
                        leg.flow,
                    ),
                    leg.momentum.clone(),
                )
            })
            .collect();
        legs.sort_by_key(|(key, _)| *key);
        let key = serde_json::to_string(&(
            rules,
            edges.iter().map(|(key, _, _)| key).collect::<Vec<_>>(),
            legs.iter().map(|(key, _)| key).collect::<Vec<_>>(),
        ))
        .unwrap();
        let rows = edges
            .into_iter()
            .map(|(_, _, q)| q)
            .chain(legs.into_iter().map(|(_, q)| q))
            .collect::<Vec<_>>();
        if case.loops > 0 {
            assert_eq!(
                matrix(&rows, 0..case.loops).rank(),
                case.loops,
                "cycle-space rank"
            );
        }
        Canonical {
            key,
            rows,
            factor: Rational::from(self.sign as i64) * Rational::from(self.multiplicity)
                / Rational::from(self.aut),
        }
    }
}

fn equivalent(a: &Canonical, b: &Canonical, case: &Case) -> Value {
    assert_eq!(a.key, b.key);
    assert_eq!(a.factor, b.factor, "exact signed weight");
    assert_eq!(a.rows.len(), b.rows.len());
    let loops = case.loops;
    if loops == 0 {
        assert_eq!(a.rows, b.rows);
        return json!({"loop_determinant":"1","affine_columns":0});
    }
    let source = matrix(&a.rows, 0..loops);
    let width = loops + case.legs.len().saturating_sub(1);
    let mut columns = Vec::new();
    for column in 0..width {
        let rhs = Matrix::from_linear(
            a.rows
                .iter()
                .zip(&b.rows)
                .map(|(a, b)| {
                    Rational::from(b[column] - if column < loops { 0 } else { a[column] })
                })
                .collect(),
            a.rows.len() as u32,
            1,
            Q,
        )
        .unwrap();
        let image = source.solve(&rhs).expect("all-edge affine routing map");
        assert!(
            image.iter().all(Rational::is_integer),
            "nonintegral loop basis/shift"
        );
        assert_eq!(&source * &image, rhs, "verify all mapped edges/externals");
        columns.push(image.into_vec());
    }
    let transform = Matrix::from_linear(
        (0..loops)
            .flat_map(|row| {
                columns
                    .iter()
                    .take(loops)
                    .map(move |column| column[row].clone())
            })
            .collect(),
        loops as u32,
        loops as u32,
        Q,
    )
    .unwrap();
    let determinant = transform.det().unwrap();
    assert!(
        determinant == 1 || determinant == -1,
        "non-unimodular loop transform"
    );
    json!({"loop_determinant":determinant.to_string(),"affine_columns":width})
}

pub(super) fn run(args: &[String]) {
    let case: Case = serde_json::from_reader(BufReader::new(
        File::open(std::env::var("BENCH_CASE_FILE").unwrap()).unwrap(),
    ))
    .unwrap();
    assert_eq!(case.name, args[1]);
    let read = |path: &str| {
        BufReader::new(File::open(path).unwrap())
            .lines()
            .map(|line| serde_json::from_str::<Record>(&line.unwrap()).unwrap())
            .collect::<Vec<_>>()
    };
    let a = read(&args[2]);
    let b = if args[3] == "-" {
        None
    } else {
        Some(read(&args[3]))
    };
    let mut left = BTreeMap::new();
    for record in &a {
        let canonical = record.certify(&case);
        assert!(
            left.insert(canonical.key.clone(), canonical).is_none(),
            "duplicate left diagram"
        );
    }
    let mut maps = Vec::new();
    if let Some(b) = &b {
        assert_eq!(a.len(), b.len());
        let mut seen = BTreeSet::new();
        for record in b {
            let canonical = record.certify(&case);
            assert!(seen.insert(canonical.key.clone()));
            let original = left
                .get(&canonical.key)
                .expect("typed graph multiset mismatch");
            maps.push(equivalent(original, &canonical, &case));
        }
        assert_eq!(seen.len(), left.len());
    }
    let result = json!({"status":"passed","output_contract":CONTRACT,"case":case.name,"diagrams":a.len(),"paired":b.is_some(),"left_observation":observe(&a),"right_observation":b.as_ref().map(|b|observe(b)),"certificate":"independent Graphica typed graph/Aut; exact integer conservation/full rank; native signed factors; complete all-edge affine unimodular conversion; arbitrary legal parallel/self-loop map","mapping_count":maps.len(),"determinants":maps.iter().map(|map|map["loop_determinant"].as_str().unwrap()).collect::<BTreeSet<_>>()});
    std::fs::write(&args[4], serde_json::to_vec_pretty(&result).unwrap()).unwrap();
    println!("RESULT {result}");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bubble() -> (Case, Record) {
        let case = Case {
            name: "bubble".into(),
            fields: vec![Field {
                id: 0,
                name: "phi".into(),
                anti_name: "phi".into(),
                fermi: false,
            }],
            signatures: vec![vec![Stub { field: 0, flow: 0 }; 3]],
            legs: vec![
                External {
                    label: 1,
                    field: 0,
                    flow: 0,
                },
                External {
                    label: 2,
                    field: 0,
                    flow: 0,
                },
            ],
            loops: 1,
            max_vertices: Some(2),
            exact_vertices: Some(2),
            simple: false,
            allow_self_loops: false,
            zero_flow_edges: true,
            one_pi: false,
            weights: None,
            weight_exact: None,
        };
        let record = Record {
            rules: vec![0, 0],
            legs: vec![
                Leg {
                    vertex: 0,
                    label: 1,
                    field: 0,
                    flow: 0,
                    momentum: vec![0, 1],
                },
                Leg {
                    vertex: 1,
                    label: 2,
                    field: 0,
                    flow: 0,
                    momentum: vec![0, -1],
                },
            ],
            edges: vec![
                Edge {
                    vertices: [0, 1],
                    field: 0,
                    flow: 0,
                    momentum: vec![1, 0],
                },
                Edge {
                    vertices: [0, 1],
                    field: 0,
                    flow: 0,
                    momentum: vec![-1, -1],
                },
            ],
            aut: 2,
            multiplicity: 1,
            sign: 1,
            loop_basis: vec!["k1".into()],
            external_basis: vec!["p1".into()],
        };
        (case, record)
    }

    #[test]
    fn parallel_edges_vertex_permutation_and_affine_shift_are_equivalent() {
        let (case, a) = bubble();
        let mut b = a.clone();
        for leg in &mut b.legs {
            leg.vertex = 1 - leg.vertex;
        }
        for edge in &mut b.edges {
            edge.vertices = [1, 0];
        }
        b.edges[0].momentum = vec![1, 2];
        b.edges[1].momentum = vec![-1, -3];
        b.edges.reverse();
        let left = a.certify(&case);
        let right = b.certify(&case);
        assert_eq!(equivalent(&left, &right, &case)["affine_columns"], 2);
    }

    #[test]
    #[should_panic(expected = "native loop basis does not span the primitive cycle lattice")]
    fn conservation_and_rank_do_not_allow_scaled_loop_basis() {
        let (case, mut record) = bubble();
        for edge in &mut record.edges {
            edge.momentum[0] *= 2;
        }
        record.certify(&case);
    }

    #[test]
    #[should_panic(expected = "exact signed weight")]
    fn equal_graph_and_unsigned_weight_do_not_allow_wrong_sign() {
        let (case, a) = bubble();
        let mut b = a.clone();
        b.sign = -1;
        equivalent(&a.certify(&case), &b.certify(&case), &case);
    }

    #[test]
    #[should_panic(expected = "momentum conservation")]
    fn all_edge_conservation_rejects_missing_external_shift() {
        let (case, mut record) = bubble();
        record.edges[1].momentum[1] = 0;
        record.certify(&case);
    }
}
