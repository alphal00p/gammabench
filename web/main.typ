#import "/src/gammabench/assets/components.typ": *
#import "explorer.typ": explorer
#import "list.typ": list-model

#let projected = json(sys.inputs.at("catalog-path"))
#let registry = json("benchmarks.json")
#let source-links = json("source-links.json").repositories
#let preview-path = sys.inputs.at("preview-context", default: "")
#let preview = if preview-path == "" { none } else { json(preview-path) }
#let proposals = if preview == none { (:) } else {
  let values = (:)
  for id in preview.snapshots { values.insert(id, preview.ci) }
  values
}
#let model = list-model(projected, registry, proposals: proposals, source-links: source-links)
#let catalog = (schema_version: projected.schema_version, fields: projected.fields,
  cases: projected.cases.map(item => if preview == none { item } else { item + (href: item.href.replace("index.html?", "explore.html?")) }))
#let home = if preview == none { "index.html" } else { "explore.html" }
#let previews = if preview == none { "previews/index.html" } else { "../../../index.html" }

#let preview-notice(root) = if preview != none {
  el("aside", class: "preview-notice", attrs: ("aria-label": "Proposed PR measurement",
    "data-proposed-candidate": preview.ci.candidate, "data-proposed-repository": preview.ci.repository_url,
    "data-captured-at": preview.at("captured_at", default: "")))[
    #el("div", class: "preview-heading")[
      #el("strong")[#link(preview.ci.repository_url + "/pull/" + str(preview.ci.pr), "Proposed PR #" + str(preview.ci.pr))]
      #link(preview.ci.repository_url + "/commit/" + preview.ci.candidate, preview.ci.candidate.slice(0, 12))
      #el("span", "Gate: " + preview.at("headline", default: ("Passed", "Regression or correctness failure", "Inconclusive").at(preview.status)))
      #if preview.at("captured_at", default: "") != "" {
        el("span", "Captured " + preview.captured_at.split("T").first() + " UTC")
      }
    ]
    #if preview.snapshots.len() == 0 { el("p")[No candidate measurements are available for this run.] }
    #if preview.at("missing_cases", default: ()).len() > 0 {
      el("p", "PR measurements unavailable for: " + preview.missing_cases.join(", ") + ".")
    }
    #el("p")[
      #link(root + "summary.html")[Run summary] ·
      #link(root + "../../runs.html")[All runs for this PR] ·
      #link(root + "../../../../ci-history/index.html?view=history")[Main history]
    ]
  ]
}

#let list-page(root: "", assets: "assets/") = bench-document(
  "Benchmark catalog", "Independent tool histories, recorded comparisons and exact measurement pins.",
  assets: assets, home: root + home, current: "Benchmark catalog", search: "#explorer-keywords",
  performance: root + if preview == none { "ci-history/index.html" } else { "../../../../ci-history/index.html" },
  local-performance: if preview != none { root + "index.html" },
  previews: root + previews,
  run-id: if preview != none { str(preview.ci.run_id) + "-" + str(preview.ci.run_attempt) },
  scripts: ("list.js?ui=compact-v2", "explorer.js?ui=list-v1"),
  styles: ("explorer.css?ui=list-v1", "list.css?ui=compact-v2"),
)[
  #preview-notice(root)
  #explorer(catalog, root: root, model: model)
]

// Former browsing URLs preserve their query and fragment in the list. Complete
// immutable reports under docs/results/CAPTURE remain separate evidence URLs.
#let redirect-page(target, defaults, title, root) = bench-document(
  title, "Open this selection in the benchmark list.", assets: root + "assets/", home: target,
  previews: root + previews,
  local-performance: root + if preview == none { "ci-history/index.html" } else { "index.html" },
  run-id: if preview != none { str(preview.ci.run_id) + "-" + str(preview.ci.run_attempt) },
  scripts: ("list-redirect.js",),
)[
  #hero(compact: true)[
    = #title
    #el("p", attrs: (id: "list-redirect", "data-target": target, "data-defaults": json.encode(defaults, pretty: false)))[
      #link(target + "?" + defaults.pairs().map(((key, value)) => key + "=" + value).join("&"))[Open in the benchmark list →]
    ]
  ]
]

#document("index.html")[#list-page()]
#document("docs/results/index.html")[#list-page(root: "../../", assets: "../../assets/")]
#if preview != none {
  document("ci-history/index.html")[
    #redirect-page("../" + home, (tag: "gammaloop-ci", tab: "history"), "GammaLoop CI history", "../")
  ]
}
#for benchmark in registry.benchmarks {
  assert(benchmark.id.match(regex("^[a-z0-9][a-z0-9-]*$")) != none, message: "Unsafe benchmark URL")
  document("benchmarks/" + benchmark.id + "/index.html")[
    #redirect-page("../../" + home, (benchmark: benchmark.id), benchmark.title, "../../")
  ]
}
#document("404.html")[
  #bench-document("Page not found", "Return to the benchmark list.", assets: "/gammabench/assets/",
    home: "/gammabench/", previews: "/gammabench/previews/")[
    #hero(compact: true)[
      = Page not found
      Return to #link("/gammabench/")[the benchmark list].
    ]
  ]
]
