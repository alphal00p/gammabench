#import "components.typ": el

// Grouping and formatting are presentation only. Every coloured comparison is
// selected from the offline analysis; no median or ratio is recomputed here.
#let matrix-run(item) = {
  let implementation = item.at("implementation", default: none)
  let identity = (item.snapshot_id, item.engine, item.at("variant", default: ""))
  if type(implementation) == dictionary {
    identity += (implementation.way_id, implementation.version_id)
  }
  json.encode(identity, pretty: false)
}
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

#let matrix-implementation(item, snapshot, subjects: (:)) = {
  let captured = item.at("implementation", default: none)
  if type(captured) == dictionary { return captured }
  let subject = subjects.at(item.at("engine", default: ""), default: (:))
  let binding = snapshot.tools.at(item.at("task", default: ""), default: none)
  if binding == none { binding = snapshot.tools.at(subject.at("tool", default: item.at("engine", default: "")), default: (:)) }
  let retained = item.at("history", default: none)
  let source = if type(retained) == dictionary { retained.at("evidence", default: (:)).at("source", default: binding) } else { binding }
  let historical = binding.at("kind", default: "") == "historical"
  let revision = if historical { binding.at("engine_revision", default: none) } else { source.at("revision", default: none) }
  let git = type(revision) == str and revision.match(regex("^[a-fA-F0-9]{40}$")) != none
  let kind = if git { "git" } else if historical { "unknown" } else { source.at("kind", default: "unknown") }
  let version = if git { revision.slice(0, 10) } else if kind == "archive" {
    source.at("version", default: "Archive " + source.at("archive_sha256", default: source.at("sha256", default: "unrecorded")).slice(0, 10))
  } else if kind == "executable" { source.at("version", default: "Executable " + source.at("sha256", default: "unrecorded").slice(0, 10)) }
  else { "Source not identified" }
  let repository = if type(retained) == dictionary { retained.at("repository", default: "") } else { subject.at("repository", default: "") }
  let way = item.at("variant", default: "")
  if way == "" { way = "Unspecified implementation" }
  (tool_id: item.at("engine", default: ""), tool_label: item.at("engine", default: ""),
    way_id: way, way_label: way, revision: revision, repository: repository, source_kind: kind,
    version_id: json.encode((kind, revision, source, if kind == "unknown" { snapshot.id } else { "" }), pretty: false),
    version_label: version)
}
#let matrix-column(item, snapshot, subjects: (:)) = {
  let implementation = matrix-implementation(item, snapshot, subjects: subjects)
  (key: matrix-run(item), snapshot_id: item.snapshot_id, engine: item.at("engine", default: ""),
    variant: item.at("variant", default: ""), task: item.at("task", default: ""), implementation: implementation,
    created_at: if type(snapshot.at("created_at", default: none)) == str { snapshot.created_at } else { "" })
}
#let matrix-column-label(run) = run.implementation.tool_label + " / " + run.implementation.way_label + " · " + run.implementation.version_label
#let matrix-availability-run(item, columns) = {
  let matches = columns.filter(run => run.snapshot_id == item.snapshot_id and run.engine == item.at("engine", default: "")
    and (item.at("variant", default: "") == "" or run.variant == item.variant))
  if matches.len() == 1 { matches.first().key } else { matrix-run(item) }
}

#let matrix-implementation-status(item) = {
  let status = item.at("metadata", default: (:)).at("implementation.status", default: "")
  if ("active", "experimental", "retired").contains(status) { status } else { "" }
}
#let matrix-status-summary(items) = {
  let statuses = items.map(matrix-implementation-status)
  let declared = matrix-unique(statuses.filter(status => status != ""))
  let label = ""
  if declared.len() > 0 {
    label = if statuses.all(status => status == declared.first()) {
      (active: "Active", experimental: "Experimental", retired: "Retired").at(declared.first())
    } else { "Mixed status: " + declared.join(", ") + if statuses.contains("") { ", undeclared" } else { "" } }
  }
  (label: label, retired: statuses.len() > 0 and statuses.all(status => status == "retired"), declared: declared)
}

#let comparison-matrix(analyses, titles: (:), initial-study: none, show-study: true, comparison-sets: (), subjects: (:), reports: (:)) = {
  let studies = ()
  let views = ()
  for combined in analyses {
    let study = combined.snapshots.first().id
    let title = titles.at(study, default: study)
    let sets = if comparison-sets.len() > 0 { comparison-sets.filter(capture => capture.analysis.snapshots.any(snapshot => combined.snapshots.any(other => other.id == snapshot.id))) }
      else { ((id: "capture:" + study, label: "Recorded comparison · " + study, analysis: combined),) }
    sets = sets.map(capture => capture + (independent: false))
    if comparison-sets.len() > 0 { sets.push((id: "latest:" + study, label: "Latest available · independent captures", analysis: combined, independent: true)) }
    let study-views = ()
    for capture-set in sets {
    let analysis = capture-set.analysis
    let lookup = (:)
    let runs = (:)
    let cases = ()
    for item in analysis.series {
      lookup.insert(item.id, item)
      let run = matrix-run(item)
      let snapshot = analysis.snapshots.find(snapshot => snapshot.id == item.snapshot_id)
      runs.insert(run, matrix-column(item, snapshot, subjects: subjects))
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
        let snapshot = analysis.snapshots.find(snapshot => snapshot.id == item.snapshot_id)
        runs.insert(run, matrix-column(item, snapshot, subjects: subjects))
      }
    }
    let columns = runs.values().sorted(key: run => (run.implementation.tool_label, run.implementation.tool_id, run.implementation.way_label, run.implementation.way_id, run.created_at, run.implementation.version_label, run.snapshot_id))
    let labels = (:)
    for run in columns {
      let label = matrix-column-label(run)
      if columns.filter(other => matrix-column-label(other) == label).len() > 1 { label += " · " + run.snapshot_id }
      labels.insert(run.key, label)
    }
    let groups = (:)
    for item in analysis.series {
      let key = json.encode((study, capture-set.id, item.metric, item.unit, item.scope, item.direction, item.contract), pretty: false)
      let items = groups.at(key, default: ())
      items.push(item)
      groups.insert(key, items)
    }
    for (key, items) in groups {
      let first = items.first()
      let pairs = if capture-set.independent { () } else { analysis.comparisons.filter(pair => items.any(item => item.id == pair.candidate)
        and items.any(item => item.id == pair.baseline)) }
      let references = matrix-unique(if capture-set.independent { () } else if analysis.baseline == none {
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
      let view-columns = columns.filter(run => items.any(item => matrix-run(item) == run.key)
        or analysis.availability.any(item => matrix-availability-run(item, columns) == run.key))
      let view = (key: key, study: study, capture_id: capture-set.id, independent: capture-set.independent, title: title, analysis: analysis, lookup: lookup,
        items: items, pairs: pairs, columns: view-columns, labels: labels, cases: cases,
        metric: first.metric, unit: first.unit, scope: first.scope, direction: first.direction,
        contract: first.contract, references: references, default-reference: default-reference)
      views.push(view)
      study-views.push(view)
    }
    }
    let default-views = study-views.filter(view => view.capture_id == sets.first().id)
    let default-view = if default-views.len() > 0 { default-views.first().key } else { "" }
    for metric in ("full_diagrams_ns", "signed_routed_ns", "materialized_ns", "elapsed_ns", "completion_ns") {
      let preferred = default-views.filter(view => view.metric == metric)
      if preferred.len() > 0 { default-view = preferred.first().key; break }
    }
    studies.push((id: study, title: title, cases: matrix-unique(combined.series.map(item => item.case) + combined.availability.map(item => item.at("case", default: ""))), analysis: combined, sets: sets, default-set: sets.first().id, default-view: default-view))
  }
  let first-study = if initial-study != none and studies.any(study => study.id == initial-study) {
    studies.find(study => study.id == initial-study)
  } else { studies.first() }
  let first-view = first-study.default-view
  el("div", attrs: (id: "comparison-matrix", "data-default-study": first-study.id,
    "data-default-view": first-view, "data-default-set": first-study.default-set), [
    #el("div", class: "matrix-controls", (
      if studies.len() > 1 {
        matrix-select("matrix-study", "Work being compared", studies.map(study => matrix-option(
          study.id, study.title, attrs: ("data-default-view": study.default-view),
          selected: study.id == first-study.id)).join(), hidden: not show-study)
      },
      matrix-select("matrix-set", "Comparison set", studies.map(study => study.sets.map(capture => matrix-option(capture.id, capture.label,
        attrs: ("data-study": study.id, "data-independent": json.encode(capture.independent)), selected: capture.id == first-study.default-set)).join()).join()),
      matrix-select("matrix-metric", "Measurement", views.map(view => {
        let same-metric = views.filter(other => other.study == view.study and other.capture_id == view.capture_id and other.metric == view.metric)
        let label = matrix-metric(view.metric)
        if same-metric.len() > 1 { label += " · " + view.contract + " · " + view.scope }
        matrix-option(view.key, label, attrs: ("data-study": view.study, "data-set": view.capture_id), selected: view.key == first-view)
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
    #if analyses.any(analysis => (analysis.series + analysis.availability).any(item => matrix-implementation-status(item) == "retired")) {
      el("label", class: "matrix-status-control", [
        #el("input", attrs: (id: "matrix-include-retired", type: "checkbox", checked: ""), [])
        Include retired versions (only consistently declared retired columns are hidden)
      ])
    }
    #el("details", class: "matrix-columns", attrs: (id: "matrix-columns"), [
      #el("summary", [Columns #el("span", attrs: (id: "matrix-column-count"), [])])
      #el("div", attrs: (id: "matrix-column-options"), [])
      #el("button", attrs: (id: "matrix-show-columns", type: "button"), [Show all columns])
    ])
    #el("p", class: "matrix-reading-guide", [
      Cells show captured medians; observed ranges and qualification are available underneath.
      Colours compare each workload with its selected reference, using qualified matched sample pairs.
      Select an original recorded comparison set for stored paired ratios. Latest available captures show absolute values only.
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
      let attrs = ("data-matrix-view": view.key, "data-study": view.study, "data-set": view.capture_id,
        "data-independent": json.encode(view.independent),
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
            #el("thead", [
              #el("tr", class: "matrix-tool-heading", [
                #el("th", attrs: (scope: "col", rowspan: "3"), [Workload])
                #for tool in matrix-unique(view.columns.map(run => run.implementation.tool_id)) {
                  let columns = view.columns.filter(run => run.implementation.tool_id == tool)
                  el("th", attrs: (scope: "colgroup", colspan: str(columns.len()), "data-tool-group": tool), columns.first().implementation.tool_label)
                }
              ])
              #el("tr", class: "matrix-way-heading", [
                #for group in matrix-unique(view.columns.map(run => (run.implementation.tool_id, run.implementation.way_id))) {
                  let columns = view.columns.filter(run => (run.implementation.tool_id, run.implementation.way_id) == group)
                  el("th", attrs: (scope: "colgroup", colspan: str(columns.len()), "data-way-group": json.encode(group, pretty: false)), columns.first().implementation.way_label)
                }
              ])
              #el("tr", class: "matrix-version-heading", [
                #for run in view.columns {
                  let implementation = run.implementation
                  let status = matrix-status-summary(view.items.filter(item => matrix-run(item) == run.key)
                    + view.analysis.availability.filter(item => matrix-availability-run(item, view.columns) == run.key))
                  el("th", attrs: (scope: "col", "data-run": run.key,
                    "data-status-label": status.label, "data-consistently-retired": json.encode(status.retired),
                    "data-tool": implementation.tool_id, "data-tool-label": implementation.tool_label,
                    "data-way": implementation.way_id, "data-way-label": implementation.way_label,
                    "data-version": implementation.version_id, "data-version-label": implementation.version_label,
                    "data-revision": if type(implementation.at("revision", default: none)) == str { implementation.revision } else { "" },
                    "data-repository": if type(implementation.at("repository", default: none)) == str { implementation.repository } else { "" },
                    "data-snapshot": run.snapshot_id, "data-created-at": run.created_at,
                    title: view.labels.at(run.key) + " · " + run.snapshot_id), [
                    #el("div", class: "matrix-version-top", [
                      #if (type(implementation.at("revision", default: none)) == str and implementation.revision.match(regex("^[a-f0-9A-F]{40}$")) != none
                        and implementation.at("repository", default: "").starts-with("https://")) {
                        el("a", attrs: (href: implementation.repository + "/commit/" + implementation.revision, title: implementation.revision), implementation.version_label)
                      } else { el("span", implementation.version_label) }
                      #el("button", class: "matrix-hide-column", attrs: (type: "button", "data-hide-column": run.key,
                        "aria-label": "Hide " + view.labels.at(run.key) + " column"), [×])
                    ])
                    #el("span", class: "matrix-capture-date", if run.created_at == "" { "Capture date unavailable" } else { run.created_at.slice(0, calc.min(10, run.created_at.len())) })
                    #if run.variant != "" { el("span", class: "matrix-capture-date", "Capture role: " + run.variant) }
                    #if status.label != "" { el("span", class: "implementation-status", status.label) }
                  ])
                }
              ])
            ])
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
                      #for unavailable in view.analysis.availability.filter(item => item.snapshot_id == run.snapshot_id
                        and item.at("case", default: "") == case and item.at("engine", default: "") == run.engine
                        and (item.at("variant", default: "") == "" or item.variant == run.variant)
                        and (item.at("task", default: "") == "" or items.any(measured => measured.task == item.task)
                          or (items.len() == 0 and run.task == item.task))) {
                        el("span", class: "matrix-cell-availability", unavailable.status + ": " + unavailable.at("reason", default: "Retained unavailable capture"))
                      }
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
                            let status = matrix-implementation-status(item)
                            if status != "" { el("p", "Declared implementation status: " + status) }
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
          for capture-set in study.sets.filter(capture => not capture.independent) { for item in capture-set.analysis.availability {
            let reason = item.at("reason", default: "")
            let interpretation = item.at("timing_interpretation", default: "")
            el("li", class: "matrix-availability-item", attrs: (
              "data-study": study.id, "data-set": capture-set.id, "data-snapshot": item.snapshot_id, "data-matrix-case": item.at("case", default: ""),
              "data-status": item.status, "data-engine": item.at("engine", default: "")), [
              #el("strong", item.at("case", default: "") + " · " + item.at("engine", default: ""))
              #item.status (#item.stage): #reason
              #if interpretation != "" and interpretation != reason { [#interpretation] }
              #if item.at("timeout_seconds", default: none) != none { [Recorded cap: #str(item.timeout_seconds) s.] }
              #el("span", class: "subtle", item.snapshot_id + " · " + item.origin)
            ])
          } }
        }
      ])
    ])
  ])
}
