#import "components.typ": el, bench-document, hero, kicker, section
#import "matrix.typ": comparison-matrix
#{
// Measurements and gates are computed offline. Typst owns their presentation.
let analysis = json("analysis.json")
let presentation = json("presentation.json")
let series = (:)
for item in analysis.series { series.insert(item.id, item) }
let number(value) = if value == none { "—" } else { str(value) }
let observed(stats) = if stats.median == none { "—" } else {
  number(stats.minimum) + " – " + number(stats.maximum)
}
let label(item) = item.engine + " / " + item.variant + " · " + item.snapshot_id
let unique(values) = {
  let result = ()
  for value in values {
    if value != "" and not result.contains(value) { result.push(value) }
  }
  result.sorted()
}
let rows(cells, attrs: (:)) = el("tr", attrs: attrs,
  cells.map(cell => el("td", str(cell))).join())
let row-attrs(case, variant, metric, status) = (
  "data-case": case, "data-variant": variant,
  "data-metric": metric, "data-status": status,
)
let table(id, headings, body) = el("div", class: "tablewrap", [
  #el("table", attrs: (id: id), [
    #el("thead", el("tr", headings.map(value => el("th", value)).join()))
    #el("tbody", body)
  ])
])
let option(value, text: none, selected: false) = {
  let attrs = (value: value)
  if selected { attrs.insert("selected", "") }
  el("option", attrs: attrs, if text == none { value } else { text })
}
let select(id, title, all, options) = el("label", (
  el("span", title),
  el("select", attrs: (id: id), (option("", text: all), options).join()),
).join())
let variants = unique(analysis.series.map(item => item.variant) +
  analysis.availability.map(item => item.at("variant", default: "")))
let cases = unique(analysis.series.map(item => item.case) +
  analysis.availability.map(item => item.at("case", default: "")))
let metrics = unique(analysis.series.map(item => item.metric))
let statuses = unique(analysis.series.map(item => item.validation_status) +
  analysis.comparisons.map(item => item.status) + analysis.availability.map(item => item.status))
let baselines = analysis.comparisons.map(pair => series.at(pair.baseline))
let selectors = (unique(baselines.map(item => item.engine)).map(value =>
  (value: "engine:" + value, text: "Engine: " + value)) +
  unique(baselines.map(item => item.variant)).map(value =>
    (value: "variant:" + value, text: "Variant: " + value)))
let available = selectors.map(item => item.value)
let default-baseline = if analysis.baseline != none {
  let value = if analysis.baseline.starts-with("engine:") { analysis.baseline }
    else { "variant:" + analysis.baseline }
  if available.contains(value) { value } else { "" }
} else if available.contains("variant:baseline") { "variant:baseline" }
  else if available.contains("engine:linnet") { "engine:linnet" } else { "" }
let evidence-links(links) = links.map(item =>
  el("a", attrs: (href: item.href), item.label)).join([ · ])
bench-document(
  "Benchmark evidence",
  "Verified snapshots, explicit work contracts and paired comparisons",
  interactive: true,
)[
#hero(compact: true)[
  #kicker[Captured measurements · source evidence]

  = Benchmark evidence

  Verified snapshots · explicit work contracts · paired comparisons
]

#section[

#comparison-matrix((analysis,))

#el("details", class: "matrix-evidence", [
#el("summary", [Detailed measurements and evidence])

#el("p", [
  #el("a", attrs: (href: "results.csv"), [Measurement CSV]) ·
  #el("a", attrs: (href: "comparisons.csv"), [Comparison CSV]) ·
  #el("a", attrs: (href: "availability.csv"), [Unsupported / censored CSV]) ·
  #el("a", attrs: (href: "analysis.json"), [Analysis JSON]) ·
  #el("a", attrs: (href: "files.sha256.json"), [Report checksums])
])

#el("div", class: "notice", [
  *How to read these results*

  #el("ul", analysis.warnings.map(value => el("li", value)).join())

  Summary medians use completed measurement samples. Warmups are excluded.
  Timeouts and missing samples remain visible and suppress pair rankings.
  Ratios are candidate / baseline; speedup above 1 favours the candidate
  using the metric's declared direction. Ratios require exact semantic
  qualification, equal contracts and metric scopes, and complete matching rounds.
  Baseline selection filters precomputed comparisons; the browser does not
  calculate statistics. Metric and baseline filters leave unsupported and
  censored work visible.
])

#el("div", class: "controls", (
  el("label", (el("span", [Search cases or text]),
    el("input", attrs: (id: "search", type: "search", placeholder: "Filter all rows"), [])).join()),
  select("case", "Case", "All cases", cases.map(value => option(value)).join()),
  select("baseline", "Comparison baseline", "All baseline directions",
    selectors.map(item => option(item.value, text: item.text,
      selected: item.value == default-baseline)).join()),
  select("variant", "Candidate / measurement variant", "All variants",
    variants.map(value => option(value)).join()),
  select("metric", "Metric", "All metrics", metrics.map(value => option(value)).join()),
  select("status", "Qualification / comparison status", "All statuses",
    statuses.map(value => option(value)).join()),
).join())

== Measurements #el("span", attrs: (id: "measurement-count"), class: "subtle", [])

#table("measurements", (
  "Case", "Engine / variant · snapshot", "Contract", "Metric", "Measured scope",
  "Median", "Observed range", "Unit", "Samples", "Qualification", "Origin",
), [
  #for item in analysis.series {
    let counts = item.statuses.pairs().sorted(key: pair => pair.first())
      .map(pair => str(pair.last()) + " " + pair.first()).join(", ")
    if counts == "" { counts = "no measurements" }
    if item.missing_values > 0 { counts += "; " + str(item.missing_values) + " missing metric" }
    let qualification = (item.validation_status +
      if item.validation_reason == "" { "" } else { ": " + item.validation_reason })
    rows((item.case, label(item), item.contract, item.metric, item.scope,
      number(item.median), observed(item), item.unit,
      str(item.count) + " (" + counts + "); " + str(item.warmups) + " warmups excluded",
      qualification, item.origin), attrs: row-attrs(item.case, item.variant, item.metric, item.validation_status))
  }
])

== Paired comparisons #el("span", attrs: (id: "comparison-count"), class: "subtle", [])

#table("comparisons", (
  "Case", "Baseline", "Candidate", "Metric", "Status", "Median ratio",
  "Observed ratio range", "Median speedup", "Pairs", "Notes",
), [
  #for pair in analysis.comparisons {
    let left = series.at(pair.baseline)
    let right = series.at(pair.candidate)
    let notes = pair.reasons
    if pair.host_differences.len() > 0 { notes.push("Host fields differ: " + pair.host_differences.join(", ")) }
    if pair.historical { notes.push("Contains historical evidence") }
    let attrs = row-attrs(pair.case, right.variant, pair.metric, pair.status)
    attrs.insert("data-baseline-engine", left.engine)
    attrs.insert("data-baseline-variant", left.variant)
    rows((pair.case, label(left), label(right), pair.metric, pair.status,
      number(pair.ratio.median), observed(pair.ratio), number(pair.speedup.median),
      str(pair.ratio.count), if notes.len() == 0 { "Same recorded host" } else { notes.join("; ") }), attrs: attrs)
  }
])

== Unsupported, failed and censored work #el("span", attrs: (id: "availability-count"), class: "subtle", [])

These entries are not zero timings. Qualification timeouts do not establish a
lower bound on a different measured stage.

#table("availability", (
  "Case", "Engine", "Variant", "Snapshot", "Stage", "Status",
  "Reason / timing interpretation", "Cap (seconds)", "Observed process duration (ns)", "Origin",
), [
  #for item in analysis.availability {
    rows((item.at("case", default: ""), item.at("engine", default: ""), item.at("variant", default: ""),
      item.snapshot_id, item.stage, item.status,
      item.at("reason", default: item.at("timing_interpretation", default: "")),
      item.at("timeout_seconds", default: ""), item.at("duration_ns", default: ""), item.origin),
      attrs: row-attrs(item.at("case", default: ""), item.at("variant", default: ""), "", item.status))
  }
])

== Contracts, versions and source evidence

#for snapshot in analysis.snapshots {
  let links = presentation.snapshots.at(snapshot.id)
  el("details", [
    #el("summary", snapshot.id + " · " + snapshot.origin + " · " + str(snapshot.created_at))
    #el("p", evidence-links(links.artifacts))
    #el("div", class: "contracts", [
      #for (name, contract) in snapshot.contracts {
        el("article", [
          === #name

          #contract.at("description", default: "")

          *Included*
          #el("ul", contract.at("included", default: ()).map(value => el("li", value)).join())
          *Excluded*
          #el("ul", contract.at("excluded", default: ()).map(value => el("li", value)).join())
        ])
      }
    ])

    === Pinned tools

    #el("pre", json.encode(snapshot.tools, pretty: true))

    === Recorded host

    #el("pre", json.encode(snapshot.host, pretty: true))

    === Schedule and provenance

    #el("pre", json.encode((schedule: snapshot.schedule, provenance: snapshot.provenance), pretty: true))

    #el("details", [
      #el("summary", "Raw records and logs (" + str(snapshot.raw_artifacts.len()) + ")")
      #el("ul", [
        #for (index, artifact) in snapshot.raw_artifacts.enumerate() {
          el("li", [
            #artifact.task · #artifact.phase #artifact.repeat · #artifact.status:
            #evidence-links(links.raw_artifacts.at(index))
          ])
        }
      ])
    ])
  ])
}

#el("footer", [Generated offline by gammabench. Exported HTML, CSV and JSON share
  the same analysis values. Source snapshots are never changed. The retained
  #el("a", attrs: (href: "report.typ"), [report Typst source]),
  #el("a", attrs: (href: "matrix.typ"), [matrix Typst source]) and
  #el("a", attrs: (href: "presentation.json"), [presentation links]) allow direct rebuilding with the
  #el("a", attrs: (href: "rendering.json"), [pinned compiler and command]).])

])
]
]

}
