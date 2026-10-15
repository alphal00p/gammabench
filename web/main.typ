#import "/src/gammabench/assets/components.typ": *
#import "/src/gammabench/assets/matrix.typ": comparison-matrix
#import "/src/gammabench/assets/history.typ": benchmark-history
#import "explorer.typ": explorer

#let projected = json(sys.inputs.at("catalog-path"))
#let preview-path = sys.inputs.at("preview-context", default: "")
#let preview = if preview-path == "" { none } else { json(preview-path) }
#let proposals = if preview == none { (:) } else {
  let values = (:)
  for id in preview.snapshots { values.insert(id, preview.ci) }
  values
}
#let explorer-catalog = (schema_version: projected.schema_version, fields: projected.fields,
  cases: projected.cases.map(item => if preview == none { item } else { item + (href: item.href.replace("tab=compare", "tab=history")) }))
#let preview-notice(root) = if preview != none {
  el("aside", class: "preview-notice", attrs: ("aria-label": "Proposed PR measurement"))[
    #el("strong", "Proposed PR #" + str(preview.ci.pr) + " · " + preview.ci.candidate.slice(0, 12))
    #el("p", "Gate: " + ("Passed", "Regression or correctness failure", "Inconclusive").at(preview.status))
    #el("p")[Published main history is shown for context. Hollow diamond markers identify this PR's measured candidate; they do not represent a merged main commit.]
    #if preview.snapshots.len() == 0 { el("p")[No candidate measurements are available for this run.] }
    #if preview.at("missing_cases", default: ()).len() > 0 {
      el("p", "PR measurements unavailable for: " + preview.missing_cases.join(", ") + ".")
    }
    #el("p")[
      #link(preview.ci.repository_url + "/pull/" + str(preview.ci.pr))[Pull request] ·
      #link(root + "summary.html")[Run summary] ·
      #link(root + "../../runs.html")[All runs for this PR] ·
      #link("https://alphal00p.github.io/gammabench/ci-history/")[Main history]
    ]
  ]
}

#let registry = json("benchmarks.json")
#assert(registry.schema_version == 1, message: "Unsupported benchmark registry version")
#let benchmark-ids = registry.benchmarks.map(benchmark => benchmark.id)
#let family-ids = registry.families.map(family => family.id)
#assert(benchmark-ids.dedup().len() == benchmark-ids.len(), message: "Duplicate benchmark ID")
#assert(family-ids.dedup().len() == family-ids.len(), message: "Duplicate benchmark family ID")
#assert(benchmark-ids.contains(registry.default_benchmark), message: "Default benchmark is missing")

// Combine original arrays for display, without calculating new comparisons or
// changing source measurements. Appending a capture preserves the study anchor.
#let combine-captures(captures) = {
  let snapshots = captures.map(capture => capture.snapshots).flatten()
  assert(snapshots.map(snapshot => snapshot.id).dedup().len() == snapshots.len(), message: "Capture registered more than once")
  let series = captures.map(capture => capture.series).flatten()
  assert(series.map(item => item.id).dedup().len() == series.len(), message: "Duplicate measurement identity")
  let baseline = captures.first().baseline
  (schema_version: 1,
    baseline: if captures.all(capture => capture.baseline == baseline) { baseline } else { none },
    snapshots: snapshots, series: series,
    comparisons: captures.map(capture => capture.comparisons).flatten(),
    availability: captures.map(capture => capture.availability).flatten(),
    warnings: captures.map(capture => capture.warnings).flatten().dedup())
}
#let entries = registry.benchmarks.map(benchmark => {
  assert(benchmark.id.match(regex("^[a-z0-9][a-z0-9-]*$")) != none, message: "Benchmark ID must be a safe URL segment")
  assert(family-ids.contains(benchmark.family), message: "Unknown benchmark family")
  assert(benchmark.analyses.len() == benchmark.reports.len(), message: "Every analysis needs its report")
  let captures = benchmark.analyses.map(path => {
    assert(path.starts-with("docs/results/") and path.ends-with("/analysis.json")
      and not path.split("/").contains(".."), message: "Analysis must be a retained report path")
    projected.workspaces.find(item => item.id == benchmark.id).analyses.at(benchmark.analyses.position(other => other == path))
  })
  for (index, path) in benchmark.reports.enumerate() {
    assert(path == benchmark.analyses.at(index).replace("/analysis.json", "/index.html"),
      message: "Evidence report must accompany its registered analysis")
    assert(path.starts-with("docs/results/") and path.ends-with("/index.html")
      and not path.split("/").contains(".."), message: "Report must be a retained report path")
  }
  if captures.len() == 0 {
    return (benchmark: benchmark, captures: (), study: "planned:" + benchmark.id, analysis: none)
  }
  let analysis = combine-captures(captures)
  (benchmark: benchmark, captures: captures, study: analysis.snapshots.first().id, analysis: analysis)
})
#assert(entries.map(entry => entry.study).dedup().len() == entries.len(), message: "A capture cannot anchor two benchmarks")
#let default-entry = entries.find(entry => entry.benchmark.id == registry.default_benchmark)
#let planned-message(entry) = el("div", class: "notice", attrs: ("data-benchmark-planned": entry.benchmark.id))[
  *No measurements yet.* This benchmark is registered and ready for implementation and capture.

  Run #el("code", "nix run .#" + entry.benchmark.id + " -- snapshots/" + entry.benchmark.id),
  then register its report with #el("code", "gammabench report SNAPSHOT --output docs/results/CAPTURE --register " + entry.benchmark.id).
]
#let workspace(entry: default-entry, root: "", assets: "assets/") = {
  let analyses = if entry.analysis == none { () } else { (entry.analysis,) }
  let snapshots = if entry.analysis == none { () } else { entry.analysis.snapshots }
  let titles = (:)
  titles.insert(entry.study, entry.benchmark.title)
  let reports = (:)
  let capture-modes = (:)
  for snapshot in snapshots {
    capture-modes.insert(snapshot.id, entry.benchmark.at("capture_modes", default: (:)))
  }
  for (index, capture) in entry.captures.enumerate() {
    for snapshot in capture.snapshots { reports.insert(snapshot.id, root + entry.benchmark.reports.at(index)) }
  }
  el("div", class: "workspace", attrs: (
    id: "benchmark-workspace", "data-default-benchmark": entry.benchmark.id,
    "data-default-study": entry.study,
    "data-default-tab": "history",
  ))[
    #el("aside", class: "workspace-sidebar", attrs: ("aria-label": "Benchmark navigation"))[
      #el("p", class: "workspace-registry")[#link(root + if preview == none { "index.html" } else { "explore.html" })[← Explore all benchmarks]]
      == Benchmarks

      #for family in registry.families {
        let benchmarks = entries.filter(entry => entry.benchmark.family == family.id)
        if benchmarks.len() > 0 {
          el("details", attrs: (open: ""))[
            #el("summary", family.title)
            #el("nav", attrs: ("aria-label": family.title), benchmarks.map(entry => el(
              "a", class: "workspace-benchmark", attrs: (
                href: root + "benchmarks/" + entry.benchmark.id + "/index.html",
                "data-benchmark-id": entry.benchmark.id,
                "data-study": entry.study, "data-title": entry.benchmark.title,
                "data-description": entry.benchmark.description, "data-family": family.title,
                "data-definition-version": entry.benchmark.definition_version,
                "data-default-metric": entry.benchmark.default_metric,
                "data-href": root + "benchmarks/" + entry.benchmark.id + "/index.html",
              ), entry.benchmark.title + if entry.analysis == none { " · planned" } else { "" },
            )).join())
          ]
        }
      }
      #el("p", class: "workspace-registry")[#link(assets + "benchmarks.json")[Benchmark registry]]
    ]
    #el("section", class: "workspace-main", attrs: ("aria-label": "Selected benchmark"))[
      #preview-notice(root)
      #el("header", class: "workspace-heading")[
        #el("p", class: "workspace-breadcrumb", attrs: (id: "workspace-family"))[
          #registry.families.find(family => family.id == entry.benchmark.family).title
        ]
        #el("h1", attrs: (id: "workspace-title"), entry.benchmark.title)
        #el("p", attrs: (id: "workspace-description"), entry.benchmark.description)
      ]
      #el("nav", class: "workspace-tabs", attrs: (role: "tablist", "aria-label": "Benchmark views"),
        ("history", "compare", "evidence").map(tab => el("button", attrs: (
          type: "button", role: "tab", id: "workspace-tab-" + tab,
          "data-workspace-tab": tab, "aria-controls": "workspace-" + tab,
          "aria-selected": if tab == "history" { "true" } else { "false" },
        ), (history: "History", compare: "Compare", evidence: "Evidence").at(tab))).join(),
      )
      #el("section", class: "workspace-panel", attrs: (
        id: "workspace-history", "data-workspace-panel": "history", role: "tabpanel",
        "aria-labelledby": "workspace-tab-history",
      ))[
        #if entry.benchmark.family == "gammaloop-ci" {
          el("p", link(root + "ci-history/index.html", [Plot several CI workloads together →]))
        }
        #if entry.analysis == none {
          planned-message(entry)
        } else {
          benchmark-history(analyses, subjects: registry.subjects, reports: reports, proposals: proposals,
            capture-modes: capture-modes, initial-study: entry.study,
            initial-case: entry.benchmark.at("default_case", default: none),
            initial-metric: entry.benchmark.default_metric)
        }
      ]
      #el("section", class: "workspace-panel", attrs: (
        id: "workspace-compare", "data-workspace-panel": "compare", role: "tabpanel", hidden: "",
        "aria-labelledby": "workspace-tab-compare",
      ))[
        #if entry.analysis == none { planned-message(entry) } else {
          comparison-matrix(analyses, titles: titles, initial-study: entry.study, show-study: false,
            subjects: registry.subjects, reports: reports, comparison-sets: entry.captures.enumerate().map(((index, capture)) => (
              id: entry.benchmark.reports.at(index),
              label: capture.snapshots.first().created_at + " · " + capture.snapshots.first().id,
              analysis: capture,
            )))
        }
      ]
      #el("section", class: "workspace-panel", attrs: (
        id: "workspace-evidence", "data-workspace-panel": "evidence", role: "tabpanel", hidden: "",
        "aria-labelledby": "workspace-tab-evidence",
      ))[
        #el("section", class: "workspace-evidence", attrs: (
          "data-workspace-evidence": "", "data-study": entry.study,
        ))[
          == #entry.benchmark.title

          Definition version: #entry.benchmark.definition_version.

          #if entry.analysis == none {
            planned-message(entry)
            for (field, title) in (("suite", "Suite definition"), ("nix_recipe", "Nix package")) {
              if entry.benchmark.keys().contains(field) {
                el("p", link("https://github.com/alphal00p/gammabench/blob/main/" + entry.benchmark.at(field), title))
              }
            }
          } else { [Captures retain their original versions, qualification and measurement boundaries.] }

          #el("ul", entry.benchmark.reports.map(path => el("li", link(root + path)[Complete report and raw evidence])).join())
          #el("ul", entry.benchmark.analyses.map(path => el("li", [
            #link(root + path)[Analysis JSON] ·
            #link(root + path.replace("analysis.json", "results.csv"))[Measurements CSV] ·
            #link(root + path.replace("analysis.json", "comparisons.csv"))[Comparisons CSV] ·
            #link(root + path.replace("analysis.json", "availability.csv"))[Unavailable / failed CSV]
          ])).join())
          #for snapshot in snapshots {
            el("details")[
              #el("summary", snapshot.id + " · " + snapshot.origin)
              #el("p")[#link(reports.at(snapshot.id))[Source report with sealed snapshot and checksums]]
              #el("pre", json.encode((contracts: snapshot.contracts, metrics: snapshot.metrics,
                host: snapshot.host), pretty: true))
            ]
          }
        ]
        #el("p")[#link(root + "artifacts.json")[Large artifact index] · #link("https://github.com/alphal00p/gammabench/releases/tag/v0.1.0")[Raw evidence archives]]
      ]
    ]
  ]
}

#document("index.html")[
  #bench-document("Explore benchmarks", "Search benchmark cases and retained configurations across GammaLoop and related tools.",
    assets: "assets/", home: if preview == none { "index.html" } else { "explore.html" }, scripts: ("explorer.js",), styles: ("explorer.css",),
  )[
    #preview-notice("")
    #if registry.families.any(family => family.id == "gammaloop-ci") {
      el("p", link("ci-history/index.html", [GammaLoop CI history · compare workloads →]))
    }
    #explorer(explorer-catalog)
  ]
]
#if registry.families.any(family => family.id == "gammaloop-ci") {
  document("ci-history/index.html")[
    #bench-document("GammaLoop CI history", "Compare the evolution of several workloads using one metric and shared units.",
      assets: "../assets/", home: if preview == none { "../index.html" } else { "../explore.html" }, scripts: ("workspace.js",),
    )[
      #hero(compact: true)[
        = GammaLoop CI history
        Select a measurement, then choose the workloads to plot together.
      ]
      #preview-notice("../")
      #let ci-entries = entries.filter(entry => entry.benchmark.family == "gammaloop-ci" and entry.analysis != none)
      #if ci-entries.len() == 0 {
        el("p", [No CI measurements have been published yet.])
      } else {
        let analysis = combine-captures(ci-entries.map(entry => entry.captures).flatten())
        let study = analysis.snapshots.first().id
        let reports = (:)
        let comparisons = (:)
        for entry in ci-entries {
          for (index, capture) in entry.captures.enumerate() {
            for snapshot in capture.snapshots {
              reports.insert(snapshot.id, "../" + entry.benchmark.reports.at(index))
              comparisons.insert(snapshot.id, "../benchmarks/" + entry.benchmark.id + "/index.html?tab=compare&set=" + entry.benchmark.reports.at(index))
            }
          }
        }
        el("div", class: "bench-section", attrs: (id: "benchmark-workspace", "data-default-benchmark": "gammaloop-ci-history",
          "data-default-study": study, "data-default-tab": "history", "data-multi-workload": "true"))[
          #el("a", attrs: (href: "index.html", "data-benchmark-id": "gammaloop-ci-history",
            "data-study": study, "data-default-metric": "generation_seconds"), [All CI workloads])
          #benchmark-history((analysis,), subjects: registry.subjects, reports: reports, comparisons: comparisons, proposals: proposals,
            initial-study: study, initial-metric: "generation_seconds", multi-workload: true)
        ]
      }
    ]
  ]
}
#document("docs/results/index.html")[
  #bench-document("Explore benchmarks", "Search benchmark cases, captures and source evidence.",
    assets: "../../assets/", home: if preview == none { "../../index.html" } else { "../../explore.html" }, scripts: ("explorer.js",), styles: ("explorer.css",),
  )[#preview-notice("../../") #explorer(explorer-catalog, root: "../../")]
]
#for entry in entries {
  document("benchmarks/" + entry.benchmark.id + "/index.html")[
    #bench-document(entry.benchmark.title, entry.benchmark.description,
      assets: "../../assets/", home: if preview == none { "../../index.html" } else { "../../explore.html" }, interactive: true, scripts: ("workspace.js",),
    )[#workspace(entry: entry, root: "../../", assets: "../../assets/")]
  ]
}
#document("404.html")[
  #bench-document("Page not found", "Return to the benchmark workspace.", assets: "/gammabench/assets/", home: "/gammabench/")[
    #hero(compact: true)[
      = Page not found

      Return to #link("/gammabench/")[the benchmark workspace].
    ]
  ]
]
