#import "components.typ": el

// Grouping and formatting are presentation only. Every coloured comparison is
// selected from the offline analysis; no median or ratio is recomputed here.
#let matrix-run(item) = json.encode((item.snapshot_id, item.engine, item.at("variant", default: "")), pretty: false)
#let matrix-number(value, unit) = {
  if value == none { "—" } else {
    let scale = 1
    let suffix = unit
    if unit == "ns" {
      if calc.abs(value) >= 1000000000 { scale = 1000000000; suffix = "s" }
      else if calc.abs(value) >= 1000000 { scale = 1000000; suffix = "ms" }
      else if calc.abs(value) >= 1000 { scale = 1000; suffix = "µs" }
    } else if unit == "bytes" {
      if calc.abs(value) >= 1000000000 { scale = 1000000000; suffix = "GB" }
      else if calc.abs(value) >= 1000000 { scale = 1000000; suffix = "MB" }
      else if calc.abs(value) >= 1000 { scale = 1000; suffix = "kB" }
      else { suffix = "B" }
    }
    str(calc.round(value / scale, digits: 2)) + " " + suffix
  }
}
// A failed capture can retain a metric definition without having a value.
// Count it as evidence, while keeping zero and partial measured captures distinct.
#let matrix-has-measurement(item) = item.count > 0 and item.median != none
#let matrix-series(item, multiple: false) = {
  let complete = (item.missing_values == 0 and item.count > 0
    and item.statuses.keys().all(status => status == "completed"))
  el("article", class: "matrix-series", attrs: ("data-series": item.id,
    "data-median": if item.median == none { "" } else { json.encode(item.median, pretty: false) },
    "data-unit": item.unit, "data-direction": item.direction,
    "data-validation": item.validation_status, "data-count": str(item.count),
    "data-missing-values": str(item.missing_values),
    "data-has-measurement": json.encode(matrix-has-measurement(item)),
    "data-complete": json.encode(complete), "data-task": item.task), [
    #if matrix-has-measurement(item) {
      if multiple { el("span", class: "matrix-task-label", item.task) }
      el("strong", class: "matrix-value", matrix-number(item.median, item.unit))
    } else { el("p", [No measured value was captured for this metric.]) }
  ])
}
#let matrix-metric(metric) = (
  full_diagrams_ns: "Full diagram construction time",
  signed_routed_ns: "Signed and routed diagrams time",
  materialized_ns: "Materialized diagrams time",
  elapsed_ns: "Driver completion time",
  completion_ns: "Application completion time",
  process_wall_ns: "Whole process time",
  whole_process_Ir: "Whole process instructions",
  allocated_bytes: "Allocated bytes",
  peak_live_bytes: "Peak live bytes",
  live_blocks_at_peak_bytes: "Live blocks at peak bytes",
).at(metric, default: metric)
#let matrix-option(value, text, attrs: (:), selected: false) = {
  let attrs = attrs + (value: value)
  if selected { attrs.insert("selected", "") }
  el("option", attrs: attrs, text)
}
#let matrix-select(id, title, options, hidden: false) = el("label", attrs: if hidden { (hidden: "") } else { (:) }, (
  el("span", title), el("select", attrs: (id: id), options),
).join())
#let matrix-unique(values) = {
  let found = ()
  for value in values { if not found.contains(value) { found.push(value) } }
  found
}
#let matrix-pair-notes(pair) = {
  let notes = pair.reasons
  if pair.host_differences.len() > 0 {
    notes.push("Host fields differ: " + pair.host_differences.join(", "))
  }
  if pair.historical { notes.push("Contains historical evidence") }
  if notes.len() == 0 { "" } else { notes.join("; ") }
}

#let comparison-matrix(analyses, titles: (:), initial-study: none, show-study: true) = {
  let studies = ()
  let views = ()
  for analysis in analyses {
    let study = analysis.snapshots.first().id
    let title = titles.at(study, default: study)
    let lookup = (:)
    let runs = (:)
    let cases = ()
    for item in analysis.series {
      lookup.insert(item.id, item)
      let run = matrix-run(item)
      runs.insert(run, (key: run, snapshot_id: item.snapshot_id,
        engine: item.engine, variant: item.variant))
      if not cases.contains(item.case) { cases.push(item.case) }
    }
    // Unsupported tasks often omit a variant. Reuse the sole matching engine
    // column; otherwise retain a separate unmeasured column rather than guess.
    for item in analysis.availability {
      let case = item.at("case", default: "")
      if not cases.contains(case) { cases.push(case) }
      let existing = runs.values().filter(run => run.snapshot_id == item.snapshot_id
        and run.engine == item.at("engine", default: "")
        and (item.at("variant", default: "") == "" or run.variant == item.variant))
      if existing.len() != 1 {
        let run = matrix-run(item)
        runs.insert(run, (key: run, snapshot_id: item.snapshot_id,
          engine: item.at("engine", default: ""), variant: item.at("variant", default: "")))
      }
    }
    let columns = runs.values().sorted(key: run => (run.engine, run.variant, run.snapshot_id))
    let labels = (:)
    for run in columns {
      let same-engine = columns.filter(other => other.engine == run.engine)
      let label = run.engine
      if same-engine.len() > 1 { label += " / " + if run.variant == "" { "unspecified" } else { run.variant } }
      if same-engine.filter(other => other.variant == run.variant).len() > 1 {
        label += " · " + run.snapshot_id
      }
      labels.insert(run.key, label)
    }
    let groups = (:)
    for item in analysis.series {
      let key = json.encode((study, item.metric, item.unit, item.scope, item.direction, item.contract), pretty: false)
      let items = groups.at(key, default: ())
      items.push(item)
      groups.insert(key, items)
    }
    let study-views = ()
    for (key, items) in groups {
      let first = items.first()
      let pairs = analysis.comparisons.filter(pair => items.any(item => item.id == pair.candidate)
        and items.any(item => item.id == pair.baseline))
      let references = matrix-unique(if analysis.baseline == none {
        items.map(matrix-run)
      } else { pairs.map(pair => matrix-run(lookup.at(pair.baseline))) })
      let default-reference = if references.len() > 0 { references.first() } else { "" }
      if analysis.baseline == none {
        let preferred = references.filter(key => runs.at(key).variant == "baseline"
          and columns.filter(run => run.engine == runs.at(key).engine).len() > 1)
        if preferred.len() == 0 { preferred = references.filter(key => runs.at(key).engine == "qgraf") }
        if preferred.len() == 0 { preferred = references.filter(key => runs.at(key).engine == "linnet") }
        if preferred.len() > 0 { default-reference = preferred.first() }
      }
      let view = (key: key, study: study, title: title, analysis: analysis, lookup: lookup,
        items: items, pairs: pairs, columns: columns, labels: labels, cases: cases,
        metric: first.metric, unit: first.unit, scope: first.scope, direction: first.direction,
        contract: first.contract, references: references, default-reference: default-reference)
      views.push(view)
      study-views.push(view)
    }
    let default-view = if study-views.len() > 0 { study-views.first().key } else { "" }
    for metric in ("full_diagrams_ns", "signed_routed_ns", "materialized_ns", "elapsed_ns", "completion_ns") {
      let preferred = study-views.filter(view => view.metric == metric)
      if preferred.len() > 0 { default-view = preferred.first().key; break }
    }
    studies.push((id: study, title: title, cases: cases, analysis: analysis, default-view: default-view))
  }
  let first-study = if initial-study != none and studies.any(study => study.id == initial-study) {
    studies.find(study => study.id == initial-study)
  } else { studies.first() }
  let first-view = first-study.default-view
  el("div", attrs: (id: "comparison-matrix", "data-default-study": first-study.id,
    "data-default-view": first-view), [
    #el("div", class: "matrix-controls", (
      if studies.len() > 1 {
        matrix-select("matrix-study", "Work being compared", studies.map(study => matrix-option(
          study.id, study.title, attrs: ("data-default-view": study.default-view),
          selected: study.id == first-study.id)).join(), hidden: not show-study)
      },
      matrix-select("matrix-metric", "Measurement", views.map(view => {
        let same-metric = views.filter(other => other.study == view.study and other.metric == view.metric)
        let label = matrix-metric(view.metric)
        if same-metric.len() > 1 { label += " · " + view.contract + " · " + view.scope }
        matrix-option(view.key, label, attrs: ("data-study": view.study), selected: view.key == first-view)
      }).join()),
      matrix-select("matrix-reference", "Reference", (matrix-option("", "Select a reference"),
        views.map(view => view.references.map(run => matrix-option(run, view.labels.at(run),
          attrs: ("data-matrix-view": view.key),
          selected: view.key == first-view and run == view.default-reference)).join()).join()).join()),
      matrix-select("matrix-case", "Workload", (matrix-option("", "All workloads"),
        matrix-unique(studies.map(study => study.cases).flatten()).map(case => matrix-option(case, case)).join()).join()),
      el("label", (el("span", "Search workloads"),
        el("input", attrs: (id: "matrix-search", type: "search", placeholder: "Filter workloads"), [])).join()),
    ).join())
    #el("p", class: "matrix-reading-guide", [
      Cells show captured medians; observed ranges and qualification are available underneath.
      Colours compare each workload with its selected reference, using qualified matched sample pairs.
      Different contracts and timing boundaries have separate matrices.
    ])
    #el("div", class: "matrix-legend", attrs: ("aria-label": "Relative performance colour scale"), [
      #el("span", class: "matrix-legend-slower", [2× worse])
      #el("span", class: "matrix-legend-reference", [Reference · 1×])
      #el("span", class: "matrix-legend-faster", [2× better])
      #el("span", class: "matrix-legend-neutral", [Unranked / unavailable])
    ])
    #el("p", attrs: (id: "matrix-summary", "aria-live": "polite"), [])
    #for view in views {
      let attrs = ("data-matrix-view": view.key, "data-study": view.study,
        "data-metric": view.metric, "data-unit": view.unit, "data-scope": view.scope,
        "data-direction": view.direction, "data-contract": view.contract,
        "data-default-reference": view.default-reference)
      if view.key != first-view { attrs.insert("hidden", "") }
      el("section", class: "matrix-view", attrs: attrs, [
        #el("h3", class: "matrix-heading", view.title + " · " + matrix-metric(view.metric))
        #el("p", class: "matrix-scope", [
          #view.scope · #view.contract · #if view.direction == "lower" { [Lower is better] } else { [Higher is better] }
        ])
        #el("details", class: "matrix-contract", [
          #el("summary", [What this measurement includes])
          #for snapshot in view.analysis.snapshots {
            if snapshot.contracts.keys().contains(view.contract) {
              let contract = snapshot.contracts.at(view.contract)
              el("p", contract.at("description", default: ""))
              el("p", [*Included:* #contract.at("included", default: ()).join("; ")])
              el("p", [*Excluded:* #contract.at("excluded", default: ()).join("; ")])
            }
          }
        ])
        #el("div", class: "matrix-tablewrap", [
          #el("table", class: "matrix-table", [
            #el("thead", el("tr", [
              #el("th", attrs: (scope: "col"), [Workload])
              #for run in view.columns {
                el("th", attrs: (scope: "col", "data-run": run.key,
                  title: run.engine + " / " + run.variant + " · " + run.snapshot_id), view.labels.at(run.key))
              }
            ]))
            #el("tbody", [
              #for case in view.cases {
                el("tr", attrs: ("data-matrix-case": case), [
                  #el("th", class: "matrix-case", attrs: (scope: "row"), case)
                  #for run in view.columns {
                    let items = view.items.filter(item => item.case == case and matrix-run(item) == run.key)
                    let measured = items.filter(matrix-has-measurement)
                    el("td", class: "matrix-cell", attrs: ("data-run": run.key,
                      "data-label": view.labels.at(run.key)), [
                      #if measured.len() == 0 {
                        el("strong", class: "matrix-value", [—])
                        el("span", class: "matrix-unavailable", [No measurement for this scope])
                      }
                      #for item in measured { matrix-series(item, multiple: measured.len() > 1) }
                      #el("span", class: "matrix-relative", [Select a reference])
                      #for pair in view.pairs.filter(pair => items.any(item => item.id == pair.candidate)) {
                        let left = view.lookup.at(pair.baseline)
                        let notes = matrix-pair-notes(pair)
                        el("span", class: "matrix-comparison", attrs: (
                          hidden: "", "data-reference-run": matrix-run(left),
                          "data-reference-series": pair.baseline, "data-candidate-series": pair.candidate,
                          "data-status": pair.status,
                          "data-ratio": if pair.ratio.median == none { "" } else { json.encode(pair.ratio.median, pretty: false) },
                          "data-speedup": if pair.speedup.median == none { "" } else { json.encode(pair.speedup.median, pretty: false) },
                          "data-pairs": str(pair.ratio.count), "data-reason": notes,
                          "data-minimum-ratio": if pair.ratio.minimum == none { "" } else { json.encode(pair.ratio.minimum, pretty: false) },
                          "data-maximum-ratio": if pair.ratio.maximum == none { "" } else { json.encode(pair.ratio.maximum, pretty: false) },
                        ), notes)
                      }
                      #if items.len() > 0 {
                        el("details", class: "matrix-cell-details", [
                          #el("summary", [Details])
                          #for item in items {
                            el("p", [*#item.task* · #item.origin · #item.snapshot_id])
                            if not matrix-has-measurement(item) { matrix-series(item) }
                            el("p", [Observed range: #matrix-number(item.minimum, item.unit) – #matrix-number(item.maximum, item.unit)])
                            el("p", [#str(item.count) completed samples · #str(item.warmups) warmups excluded])
                            el("p", [Qualification: #item.validation_status #item.validation_reason])
                            el("p", [Measurement statuses: #json.encode(item.statuses) · #str(item.missing_values) missing values])
                          }
                          #for pair in view.pairs.filter(pair => items.any(item => item.id == pair.candidate)) {
                            let left = view.lookup.at(pair.baseline)
                            el("p", view.labels.at(matrix-run(left)) + ": " + pair.status + "; " +
                              str(pair.ratio.count) + " matched pairs; candidate/reference median ratio " +
                              if pair.ratio.median == none { "unavailable" } else { json.encode(pair.ratio.median, pretty: false) })
                            el("p", matrix-pair-notes(pair))
                          }
                        ])
                      }
                    ])
                  }
                ])
              }
            ])
          ])
        ])
        #el("p", class: "matrix-empty", attrs: (hidden: ""), [No workloads match these filters.])
      ])
    }
    #el("section", attrs: (id: "matrix-availability"), [
      #el("h3", [Unsupported, failed and censored work])
      #el("p", [These records stay visible when the measurement or reference changes. They are not zero timings. Qualification timeouts do not bound a different measured stage.])
      #el("p", attrs: (id: "matrix-availability-count", "aria-live": "polite"), [])
      #el("ul", [
        #for study in studies {
          for item in study.analysis.availability {
            let reason = item.at("reason", default: "")
            let interpretation = item.at("timing_interpretation", default: "")
            el("li", class: "matrix-availability-item", attrs: (
              "data-study": study.id, "data-matrix-case": item.at("case", default: ""),
              "data-status": item.status, "data-engine": item.at("engine", default: "")), [
              #el("strong", item.at("case", default: "") + " · " + item.at("engine", default: ""))
              #item.status (#item.stage): #reason
              #if interpretation != "" and interpretation != reason { [#interpretation] }
              #if item.at("timeout_seconds", default: none) != none { [Recorded cap: #str(item.timeout_seconds) s.] }
              #el("span", class: "subtle", item.snapshot_id + " · " + item.origin)
            ])
          }
        }
      ])
    ])
  ])
}
