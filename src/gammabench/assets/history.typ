#import "components.typ": el
#import "matrix.typ": matrix-number, matrix-metric, matrix-option, matrix-select, matrix-unique, matrix-implementation, matrix-implementation-status, matrix-status-summary

// This view consumes captured summaries. It never combines samples, estimates
// a revision, or derives a statistic in the browser. Context changes split lines.
#let history-canonical(value) = {
  let normalize(value) = {
    if type(value) == dictionary {
      let result = (:)
      for (key, item) in value.pairs().sorted(key: pair => pair.first()) {
        result.insert(key, normalize(item))
      }
      result
    } else if type(value) == array { value.map(normalize) } else { value }
  }
  json.encode(normalize(value), pretty: false)
}
#let history-commit(value) = type(value) == str and value.match(regex("^[0-9a-fA-F]{40}$")) != none
// A CI run is a paired comparison, but its candidate is the new tool version.
// Discovery supplies the history point; confirmation stays in Compare/Evidence.
#let history-selected(snapshot, item: none) = {
  let gate = snapshot.provenance.at("ci_gate", default: none)
  gate == none or (gate.at("phase", default: "") == "discovery"
    and (item == none or item.at("variant", default: "") == "candidate"))
}
#let history-source(item, snapshot, subjects, capture-modes) = {
  let implementation = matrix-implementation(item, snapshot, subjects: subjects)
  if item.at("implementation", default: none) == none {
    implementation += (way_id: "", way_label: "Implementation not declared")
  }
  let retained = item.at("history", default: none)
  if type(retained) == dictionary and ("nix-build", "ci-gate").contains(retained.at("kind", default: "")) {
    return (implementation: implementation, revision: retained.revision, capture: retained.capture,
      binding: retained.evidence.at("source", default: (:)),
      dependencies: retained.at("context").at("fixed_sources", default: (:)),
      reason: retained.reason, repository: retained.repository, task: item.task, capture_task: "")
  }
  let subject = subjects.at(item.engine, default: (:))
  let task = item.task
  let declared-mode = capture-modes.at(snapshot.id, default: (:)).at(item.task, default: none)
  let known-capture = snapshot.provenance.keys().contains("profile_records") or declared-mode != none
  let capture = if declared-mode != none { declared-mode }
    else if known-capture { "timing" } else { "Unspecified capture · " + item.task }
  // Historical profile task IDs are resolved only through their retained
  // provenance. A suffix alone is not evidence of a source or capture mode.
  let profiles = snapshot.provenance.at("profile_records", default: ())
    .filter(record => record.task + "-" + record.tool == item.task)
  if profiles.len() > 0 {
    let choices = matrix-unique(profiles.map(record => (record.task, record.tool)))
    if choices.len() == 1 { task = choices.first().first(); capture = choices.first().last() }
    else { capture = "unknown profile" }
  }
  let bindings = snapshot.tools
  let binding = bindings.at(task, default: none)
  let tool = subject.at("tool", default: none)
  if binding == none and tool != none { binding = bindings.at(tool, default: none) }
  let revision = none
  let dependencies = (:)
  let reason = ""
  if binding == none {
    reason = "No retained source binding for the measured engine."
  } else {
    if binding.at("kind", default: "") == "historical" {
      revision = binding.at("engine_revision", default: none)
      dependencies = binding.at("dependency_sources", default: (:))
      if tool != none and dependencies.keys().contains(tool) { let removed = dependencies.remove(tool) }
    } else {
      revision = binding.at("revision", default: none)
      // Keep fixed tool pins without their local cache paths. The subject pin
      // is the x axis; every other tool/source pin belongs to the context.
      for (name, dependency) in bindings {
        if name != tool {
          let pin = (:)
          for key in ("kind", "url", "revision", "version", "sha256", "source_sha256", "dirty", "dirty_source_sha256") {
            if dependency.keys().contains(key) { pin.insert(key, dependency.at(key)) }
          }
          dependencies.insert(name, pin)
        }
      }
    }
    if not history-commit(revision) { reason = "No complete Git commit for the measured engine; retained archive/version evidence is available below." }
    if binding.at("dirty", default: false) { reason = "Dirty source capture does not identify an immutable measured commit." }
  }
  if capture == "unknown profile" { reason = "Ambiguous profile capture provenance; measurement retained below." }
  let repository = if item.at("implementation", default: none) != none and type(implementation.at("repository", default: none)) == str and implementation.repository.starts-with("https://") {
    implementation.repository
  } else { subject.at("repository", default: "") }
  if type(repository) != str or not repository.starts-with("https://") { repository = "" }
  (implementation: implementation, revision: revision, capture: capture, binding: binding, dependencies: dependencies,
    reason: reason, repository: repository, task: task, capture_task: if known-capture { "" } else { task })
}
#let history-host(snapshot) = {
  let host = (:)
  // Retain unfamiliar fields conservatively; load observations and sampled
  // frequency vary within the same hardware context and do not define a line.
  for (key, value) in snapshot.host {
    if not ("loadavg", "load_average_start", "load_average_end").contains(key) {
      if key == "selected_cpu_info" and type(value) == str {
        value = value.split("\n").filter(line => not line.starts-with("cpu MHz") and not line.starts-with("bogomips")).join("\n")
      }
      host.insert(key, value)
    }
  }
  let known = ("selected_cpu_info", "uname", "hostname", "platform", "machine", "cpu", "cpu_count")
  if not known.any(key => host.keys().contains(key)) { host.insert("unknown_context_snapshot", snapshot.id) }
  host
}
#let history-complete(item, snapshot) = {
  let expected = snapshot.schedule.at("task_repeats", default: (:)).at(item.task,
    default: snapshot.schedule.at("repeats", default: none))
  let rounds = item.samples.map(sample => sample.repeat)
  let valid = (item.count > 0 and item.median != none and item.minimum != none and item.maximum != none
    and item.missing_values == 0 and item.statuses.keys().all(status => status == "completed")
    and rounds.len() == matrix-unique(rounds).len())
  if type(expected) == int { valid = valid and rounds.sorted() == range(expected) }
  valid
}
#let history-context(study, item, snapshot, source) = {
  let retained = item.at("history", default: none)
  if type(retained) == dictionary and ("nix-build", "ci-gate").contains(retained.at("kind", default: "")) {
    let ci-inputs = none
    if retained.kind == "ci-gate" {
      let identity = snapshot.provenance.at("ci_gate").at("identity").at("candidate")
      ci-inputs = (corpus: identity.corpus_sha256, settings: identity.config_sha256)
    }
    return history-canonical((study: study, case: item.case, engine: item.engine,
      metric: item.metric, unit: item.unit, scope: item.scope, direction: item.direction,
      contract: snapshot.contracts.at(item.contract, default: none), workload: item.workload,
      capture: source.capture, way: source.implementation.way_id, repository: source.repository,
      receipt_context: retained.at("context"), ci_inputs: ci-inputs))
  }
  let workload = item.at("workload", default: none)
  let contract = snapshot.contracts.at(item.contract, default: none)
  let controller = snapshot.provenance.at("legacy_controller_sha256", default: none)
  let known-controller = (snapshot.origin == "historical-import" and type(controller) == str
    and controller.match(regex("^[0-9a-fA-F]{64}$")) != none)
  // Normal analysis does not expose task-specific environment/build settings.
  // A declared capture mode alone cannot establish those settings are equal.
  // Keep each such task/capture separate until that context is retained here.
  let unknown = if workload == none or contract == none or not known-controller {
    item.snapshot_id + ":" + item.task
  } else { "" }
  history-canonical((study: study, case: item.case, engine: item.engine, metric: item.metric,
    unit: item.unit, scope: item.scope, direction: item.direction, contract_id: item.contract,
    contract: contract, workload: workload, capture: source.capture, capture_task: source.capture_task,
    way: source.implementation.way_id, repository: source.repository,
    semantic_metadata: item.at("metadata_semantics", default: (:)),
    host: history-host(snapshot), dependencies: source.dependencies,
    harness: snapshot.provenance.at("harness_sources", default: (:)),
    controller: controller,
    environment: snapshot.provenance.at("explicit_environment", default: (:)),
    clock: snapshot.provenance.at("clock", default: none),
    process_clock_scope: snapshot.provenance.at("process_wall_includes", default: none), unknown: unknown))
}
#let history-record-row(record, group, reports, comparisons) = {
  let item = record.item
  let source = record.source
  let attrs = ("data-source-series": item.id, "data-snapshot": item.snapshot_id, "data-task": item.task,
    "data-capture-index": json.encode(record.capture_index), "data-created-at": record.snapshot.created_at,
    "data-version-label": source.implementation.version_label, "data-repository": source.repository,
    "data-commit": if history-commit(source.revision) { source.revision } else { "" },
    "data-reason": record.reason)
  let ci = record.snapshot.provenance.at("ci", default: (:))
  if ci.at("run_id", default: none) != none and ci.at("run_attempt", default: none) != none {
    attrs += ("data-run-id": str(ci.run_id), "data-run-attempt": str(ci.run_attempt),
      "data-baseline-commit": ci.baseline)
  }
  if record.paired != none {
    attrs.insert("data-paired-ratio", json.encode(record.paired.ratio.median))
  }
  attrs.insert("data-history-role", if record.proposal == none { "main" } else { "proposed" })
  if record.proposal != none {
    attrs.insert("data-pr", str(record.proposal.pr))
    attrs.insert("data-baseline-commit", record.proposal.baseline)
  }
  let comparison = comparisons.at(item.snapshot_id, default: none)
  if comparison != none { attrs.insert("data-comparison-href", comparison) }
  let class = "history-unplotted"
  if record.qualified {
    class = "history-point"
    attrs += ("data-series-id": group.id, "data-commit": source.revision,
      "data-median": json.encode(item.median), "data-min": json.encode(item.minimum),
      "data-max": json.encode(item.maximum), "data-count": str(item.count),
      "data-validation": item.validation_status, "data-complete": "true",
      "data-repository": source.repository, "data-order": str(record.order),
      "data-created-at": record.snapshot.created_at, "data-origin": item.origin)
  }
  el("tr", class: class, attrs: attrs, [
    #el("td", [
      #if history-commit(source.revision) {
        if source.repository != "" { el("a", attrs: (href: source.repository + "/commit/" + source.revision), source.revision.slice(0, 10)) }
        else { source.revision.slice(0, 10) }
      } else { source.implementation.version_label }
      #el("span", class: "history-variant", item.variant)
      #if record.proposal != none { el("strong", class: "history-proposal-label", "Proposed PR #" + str(record.proposal.pr)) }
      #let declared-status = matrix-implementation-status(item)
      #if declared-status != "" { el("span", class: "implementation-status", "Declared status: " + declared-status) }
      #el("span", class: "history-capture-date", if record.snapshot.created_at == "" { "Capture date unavailable" } else { record.snapshot.created_at })
    ])
    #el("td", matrix-number(item.median, item.unit))
    #el("td", matrix-number(item.minimum, item.unit) + " – " + matrix-number(item.maximum, item.unit))
    #el("td", str(item.count))
    #el("td", if record.qualified { "Qualified · complete" } else { record.reason })
    #el("td", [
      #let href = reports.at(item.snapshot_id, default: none)
      #if href != none { el("a", attrs: (href: href), item.snapshot_id + " · " + item.task) } else { item.snapshot_id + " · " + item.task; el("span", [ · Report link unavailable]) }
    ])
  ])
}

#let benchmark-history(analyses, subjects: (:), reports: (:), comparisons: (:), proposals: (:), capture-modes: (:), initial-study: none, initial-case: none, initial-metric: none, multi-workload: false) = {
  let groups = (:)
  let availability = ()
  let cases = ()
  let measurements = (:)
  let studies = ()
  let order = 0
  let capture-indices = (:)
  let capture-suites = (:)
  let has-ci = analyses.any(analysis => analysis.snapshots.any(snapshot => snapshot.provenance.at("ci_gate", default: none) != none))
  for analysis in analyses {
    let study = analysis.snapshots.first().id
    studies.push(study)
    let snapshots = analysis.snapshots.filter(snapshot => history-selected(snapshot))
    if snapshots.len() > 0 and snapshots.all(snapshot => snapshot.provenance.at("ci_gate", default: none) != none) {
      snapshots = snapshots.sorted(key: snapshot => snapshot.created_at)
    }
    for snapshot in snapshots {
      if not capture-indices.keys().contains(snapshot.id) {
        if proposals.keys().contains(snapshot.id) {
          capture-indices.insert(snapshot.id, -1)
        } else {
          let key = history-canonical((study, snapshot.suite_id))
          let index = capture-suites.at(key, default: 0)
          capture-indices.insert(snapshot.id, index)
          capture-suites.insert(key, index + 1)
        }
      }
    }
    let series = snapshots.map(snapshot => analysis.series.filter(item => item.snapshot_id == snapshot.id
      and history-selected(snapshot, item: item))).flatten()
    for item in series {
      let snapshot = analysis.snapshots.find(snapshot => snapshot.id == item.snapshot_id)
      let source = history-source(item, snapshot, subjects, capture-modes)
      let key = history-context(study, item, snapshot, source)
      let group = groups.at(key, default: (id: "history-series-" + str(groups.len()), study: study,
        case: item.case, metric: item.metric, unit: item.unit, scope: item.scope, direction: item.direction,
        engine: item.engine, implementation: source.implementation, repository: source.repository,
        capture: source.capture, contract: item.contract, snapshot: snapshot,
        dependencies: source.dependencies, workload: item.workload, records: ()))
      let complete = history-complete(item, snapshot)
      let qualified = complete and item.validation_status == "passed" and source.reason == ""
      let reason = source.reason
      if not complete { reason = "Incomplete or unavailable capture; observed values are retained without a plotted point." }
      if item.validation_status != "passed" {
        reason = "Qualification " + item.validation_status + ". " + item.validation_reason
      }
      let paired = analysis.comparisons.filter(comparison => comparison.candidate == item.id
        and comparison.status == "comparable" and analysis.series.any(baseline => baseline.id == comparison.baseline
          and baseline.snapshot_id == item.snapshot_id and baseline.variant == "baseline"))
      group.records.push((item: item, snapshot: snapshot, source: source, qualified: qualified, reason: reason,
        paired: if paired.len() == 1 { paired.first() } else { none },
        proposal: proposals.at(snapshot.id, default: none), order: order, capture_index: capture-indices.at(item.snapshot_id)))
      groups.insert(key, group)
      order += 1
      if not cases.contains((study, item.case)) { cases.push((study, item.case)) }
      let metric-key = history-canonical((study, item.metric, item.unit, item.scope, item.direction))
      measurements.insert(metric-key, (study: study, metric: item.metric, unit: item.unit, scope: item.scope, direction: item.direction))
    }
    for item in analysis.availability {
      let snapshot = analysis.snapshots.find(snapshot => snapshot.id == item.snapshot_id)
      if snapshot != none and not history-selected(snapshot, item: item) { continue }
      availability.push((study: study, item: item))
      let case = item.at("case", default: "")
      if not cases.contains((study, case)) { cases.push((study, case)) }
    }
  }
  let first-study = if initial-study != none and studies.contains(initial-study) { initial-study } else { studies.first() }
  let first-cases = cases.filter(case => case.first() == first-study)
  let first-case = if first-cases.any(case => case.last() == initial-case) { initial-case } else if first-cases.len() > 0 { first-cases.first().last() } else { "" }
  let first-metrics = measurements.values().filter(metric => metric.study == first-study)
  let first-metric = if first-metrics.len() > 0 { first-metrics.first().metric } else { "" }
  if first-metrics.any(metric => metric.metric == initial-metric) { first-metric = initial-metric }
  let label-key(group) = history-canonical((group.study, group.case, group.metric,
    group.scope, group.unit, group.engine, group.implementation.way_id, group.capture))
  let counts = (:)
  for group in groups.values() {
    let key = label-key(group)
    counts.insert(key, counts.at(key, default: 0) + 1)
  }
  let positions = (:)
  let all-groups = ()
  for group in groups.values() {
    let key = label-key(group)
    let position = positions.at(key, default: 0) + 1
    positions.insert(key, position)
    let label = group.implementation.tool_label + " / " + group.implementation.way_label + " · " + group.capture
    if counts.at(key) > 1 { label += " · configuration " + str(position) }
    group.insert("label", label)
    group.insert("status", matrix-status-summary(group.records.map(record => record.item)))
    all-groups.push(group)
  }
  el("div", attrs: (id: "benchmark-history", "data-default-study": first-study,
    "data-default-case": first-case, "data-default-metric": first-metric), [
    #el("div", class: "history-controls", (
      if multi-workload { [] } else { matrix-select("history-case", "Workload", cases.map(case => matrix-option(case.last(), case.last(),
        attrs: ("data-study": case.first()), selected: case.first() == first-study and case.last() == first-case)).join())
      },
      matrix-select("history-metric", "Measurement", measurements.values().map(metric => matrix-option(history-canonical((metric.metric, metric.scope, metric.unit, metric.direction)),
        matrix-metric(metric.metric) + " · " + metric.unit
          + if measurements.values().filter(other => other.study == metric.study and other.metric == metric.metric).len() > 1 { " · " + metric.scope } else { "" },
        attrs: ("data-study": metric.study, "data-metric": metric.metric, "data-unit": metric.unit, "data-scope": metric.scope, "data-direction": metric.direction),
        selected: metric.study == first-study and metric.metric == first-metric)).join()),
      if multi-workload { [] } else { matrix-select("history-series", "Implementation / configuration", (matrix-option("", "All recorded configurations"),
        all-groups.map(group => matrix-option(group.id, group.label,
          attrs: ("data-study": group.study, "data-case": group.case, "data-metric": group.metric))).join()).join())
      },
    ).join())
    #el("details", class: "history-methodology", [
    #el("summary", [Measurement details])
    #if has-ci {
      el("p", class: "history-reading-guide")[CI history follows each run's candidate discovery measurement. Paired baselines and confirmation measurements remain in Compare and Evidence.]
    }
    #if proposals.len() > 0 {
      el("p", class: "history-reading-guide")[Hollow diamonds marked PR are proposed measurements in a separate final column. Dashed connectors lead from the recorded base commit when its measurement context matches. Main history keeps its own line. Exact capture dates remain in each point and the evidence.]
    }
    #if multi-workload {
      el("p", class: "history-reading-guide")[Pin workloads to compare them on one shared scale. The selected metric, units and measurement scope are shared; each workload keeps its own line and captured range.]
    }
    #el("p", class: "history-reading-guide", [
      Points identify measured tool commits. Medians and observed min–max ranges come from retained captures;
      ranges are not confidence intervals. Lines join only the same workload, metric, contract, hardware,
      capture mode and fixed dependencies. Captures with incomplete configuration metadata stay separate.
      All charts share measured commit/run columns in capture order. Workloads from one CI run and attempt share a column.
      Reruns remain separate columns, and source commits from different repositories are never treated as one commit sequence.
      Capture dates describe measurements, not commit dates. Missing or failed captures break lines.
      Mini charts have individual vertical scales; the pinned comparison uses one shared scale.
      Baseline ratios are retained paired discovery comparisons, not ratios between independent history points or gate outcomes.
    ])
    ])
    #if all-groups.any(group => group.status.declared.contains("retired")) {
      el("label", class: "matrix-status-control", [
        #el("input", attrs: (id: "history-include-retired", type: "checkbox", checked: ""), [])
        Include retired configurations (mixed and undeclared statuses remain visible)
      ])
    }
    #el("p", attrs: (id: "history-summary", "aria-live": "polite"), [])
    #el("section", class: "history-overview", attrs: ("aria-label": "Workload histories and pinned comparison"), [
      #el("p", class: "history-pin-navigation", el("a", attrs: (id: "history-pin-jump", href: "#history-pinned"), [Compare pinned histories ↓]))
      #el("div", attrs: (id: "history-workloads"), [])
      #el("section", attrs: (id: "history-pinned", "aria-label": "Pinned comparison"), [
        #el("h3", [Pinned comparison])
        #el("p", attrs: (id: "history-pin-count", "aria-live": "polite"), [])
        #el("div", class: "history-run-layout", [
          #el("div", attrs: (id: "history-visible-series", "aria-label": "Pinned workloads"), [])
          #el("div", class: "history-run-chart", attrs: (id: "history-overview-chart"), [])
          #el("div", [])
        ])
      ])
      #el("p", class: "history-reading-guide", [Mini charts use individual vertical scales. Pinned histories share one scale. Activate a point to open its recorded comparison; missing and unqualified captures remain in the evidence.])
    ])
    #el("details", class: "history-evidence", [
    #el("summary", [Captured values and provenance])
    #for (index, group) in all-groups.enumerate() {
      let attrs = ("data-study": group.study, "data-case": group.case, "data-metric": group.metric,
        "data-unit": group.unit, "data-scope": group.scope, "data-direction": group.direction, "data-contract": group.contract,
        "data-engine": group.engine, "data-tool": group.implementation.tool_id, "data-way": group.implementation.way_id,
        "data-tool-label": group.implementation.tool_label, "data-way-label": group.implementation.way_label,
        "data-repository": group.repository, "data-capture": group.capture, "data-series-id": group.id,
        "data-context": group.id, "data-label": group.label, "data-status-label": group.status.label,
        "data-consistently-retired": json.encode(group.status.retired))
      if group.study != first-study or group.case != first-case or group.metric != first-metric { attrs.insert("hidden", "") }
      el("article", class: "history-series", attrs: attrs, [
        #el("h3", if multi-workload { group.case + " · " + group.label } else { group.label })
        #if group.status.label != "" { el("p", class: "implementation-status", group.status.label) }
        #el("p", class: "history-scope", group.scope + " · " + group.contract)
        #el("div", class: "history-chart", attrs: ("data-series-id": group.id, hidden: ""), [])
        #if group.records.filter(record => record.qualified).len() == 1 {
          el("p", class: "history-sparse", [One qualified measured revision. No trend is available yet.])
        } else if group.records.filter(record => record.qualified).len() == 0 {
          el("p", class: "history-sparse", [No qualified commit point in this configuration. The captured evidence remains below.])
        }
        #el("details", [
          #el("summary", [Captured values and provenance])
          #el("div", class: "history-tablewrap", el("table", class: "history-table", [
            #el("thead", el("tr", ("Measured commit", "Median", "Observed range", "Samples", "Qualification", "Evidence").map(title => el("th", attrs: (scope: "col"), title)).join()))
            #el("tbody", group.records.map(record => history-record-row(record, group, reports, comparisons)).join())
          ]))
          #el("p", [*Contract definition:*])
          #el("pre", json.encode(group.snapshot.contracts.at(group.contract, default: (:)), pretty: true))
          #if group.records.any(record => record.item.at("history", default: none) != none) {
            el("p", [*Nix build and measured runtime evidence:*])
            for record in group.records {
              let retained = record.item.at("history", default: none)
              if retained != none { el("pre", json.encode(retained, pretty: true)) }
            }
          }
          #el("p", [*Workload inputs and fixed dependencies:*])
          #el("pre", json.encode((workload: group.workload, dependencies: group.dependencies), pretty: true))
          #let href = reports.at(group.snapshot.id, default: none)
          #if href != none {
            let analysis-href = href.split("/").slice(0, -1).join("/") + "/analysis.json"
            el("a", attrs: (href: analysis-href), [Complete captured host, source bindings and qualification])
          } else { el("p", [Full provenance report link unavailable for this capture.]) }
        ])
      ])
    }
    #el("div", class: "history-availability-list", [
      #for record in availability {
        let item = record.item
        let attrs = ("data-study": record.study, "data-case": item.at("case", default: ""),
          "data-status": item.status, "data-engine": item.at("engine", default: ""))
        if record.study != first-study or item.at("case", default: "") != first-case { attrs.insert("hidden", "") }
        el("p", class: "history-availability", attrs: attrs, [
          #item.at("engine", default: "") · #item.at("case", default: "") · #item.status:
          #item.at("reason", default: "No successful captured result")
          #let href = reports.at(item.snapshot_id, default: none)
          #if href != none { el("a", attrs: (href: href + "#availability"), [Retained record]) } else { [ · Report link unavailable] }
        ])
      }
    ])
    ])
  ])
}
