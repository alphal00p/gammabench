#import "components.typ": el
#import "matrix.typ": matrix-number, matrix-metric, matrix-option, matrix-select, matrix-unique

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
#let history-source(item, snapshot, subjects, capture-modes) = {
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
  let repository = subject.at("repository", default: "")
  if type(repository) != str or not repository.starts-with("https://") { repository = "" }
  (revision: revision, capture: capture, binding: binding, dependencies: dependencies,
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
    host: history-host(snapshot), dependencies: source.dependencies,
    harness: snapshot.provenance.at("harness_sources", default: (:)),
    controller: controller,
    environment: snapshot.provenance.at("explicit_environment", default: (:)),
    clock: snapshot.provenance.at("clock", default: none),
    process_clock_scope: snapshot.provenance.at("process_wall_includes", default: none), unknown: unknown))
}
#let history-record-row(record, group, reports) = {
  let item = record.item
  let source = record.source
  let attrs = ("data-source-series": item.id, "data-snapshot": item.snapshot_id, "data-task": item.task)
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
      } else { [No Git commit] }
      #el("span", class: "history-variant", item.variant)
    ])
    #el("td", matrix-number(item.median, item.unit))
    #el("td", matrix-number(item.minimum, item.unit) + " – " + matrix-number(item.maximum, item.unit))
    #el("td", str(item.count))
    #el("td", if record.qualified { "Qualified · complete" } else { record.reason })
    #el("td", [
      #let href = reports.at(item.snapshot_id, default: none)
      #if href != none { el("a", attrs: (href: href), item.snapshot_id + " · " + item.task) }
      else { item.snapshot_id + " · " + item.task; el("span", [ · Report link unavailable]) }
    ])
  ])
}

#let benchmark-history(analyses, subjects: (:), reports: (:), capture-modes: (:), initial-study: none, initial-case: none, initial-metric: none) = {
  let groups = (:)
  let availability = ()
  let cases = ()
  let measurements = (:)
  let studies = ()
  let order = 0
  for analysis in analyses {
    let study = analysis.snapshots.first().id
    studies.push(study)
    for item in analysis.series {
      let snapshot = analysis.snapshots.find(snapshot => snapshot.id == item.snapshot_id)
      let source = history-source(item, snapshot, subjects, capture-modes)
      let key = history-context(study, item, snapshot, source)
      let group = groups.at(key, default: (id: "history-series-" + str(groups.len()), study: study,
        case: item.case, metric: item.metric, unit: item.unit, scope: item.scope, direction: item.direction,
        engine: item.engine, capture: source.capture, contract: item.contract, snapshot: snapshot,
        dependencies: source.dependencies, workload: item.workload, records: ()))
      let complete = history-complete(item, snapshot)
      let qualified = complete and item.validation_status == "passed" and source.reason == ""
      let reason = source.reason
      if not complete { reason = "Incomplete or unavailable capture; observed values are retained without a plotted point." }
      if item.validation_status != "passed" {
        reason = "Qualification " + item.validation_status + ". " + item.validation_reason
      }
      group.records.push((item: item, snapshot: snapshot, source: source, qualified: qualified, reason: reason, order: order))
      groups.insert(key, group)
      order += 1
      if not cases.contains((study, item.case)) { cases.push((study, item.case)) }
      let metric-key = history-canonical((study, item.metric, item.unit, item.scope, item.direction))
      measurements.insert(metric-key, (study: study, metric: item.metric, unit: item.unit, scope: item.scope, direction: item.direction))
    }
    for item in analysis.availability {
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
    group.scope, group.unit, group.engine, group.capture))
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
    let label = group.engine + " · " + group.capture
    if counts.at(key) > 1 { label += " · configuration " + str(position) }
    group.insert("label", label)
    all-groups.push(group)
  }
  el("div", attrs: (id: "benchmark-history", "data-default-study": first-study,
    "data-default-case": first-case, "data-default-metric": first-metric), [
    #el("div", class: "history-controls", (
      matrix-select("history-case", "Workload", cases.map(case => matrix-option(case.last(), case.last(),
        attrs: ("data-study": case.first()), selected: case.first() == first-study and case.last() == first-case)).join()),
      matrix-select("history-metric", "Measurement", measurements.values().map(metric => matrix-option(history-canonical((metric.metric, metric.scope, metric.unit, metric.direction)),
        matrix-metric(metric.metric) + " · " + metric.unit
          + if measurements.values().filter(other => other.study == metric.study and other.metric == metric.metric).len() > 1 { " · " + metric.scope } else { "" },
        attrs: ("data-study": metric.study, "data-metric": metric.metric, "data-unit": metric.unit, "data-scope": metric.scope, "data-direction": metric.direction),
        selected: metric.study == first-study and metric.metric == first-metric)).join()),
      matrix-select("history-series", "Engine / configuration", (matrix-option("", "All recorded configurations"),
        all-groups.map(group => matrix-option(group.id, group.label,
          attrs: ("data-study": group.study, "data-case": group.case, "data-metric": group.metric))).join()).join()),
    ).join())
    #el("p", class: "history-reading-guide", [
      Points identify measured tool commits. Medians and observed min–max ranges come from retained captures;
      ranges are not confidence intervals. Lines join only the same workload, metric, contract, hardware,
      capture mode and fixed dependencies. Captures with incomplete configuration metadata stay separate.
      Only captured revisions are shown, in capture order.
    ])
    #el("p", attrs: (id: "history-summary", "aria-live": "polite"), [])
    #for (index, group) in all-groups.enumerate() {
      let attrs = ("data-study": group.study, "data-case": group.case, "data-metric": group.metric,
        "data-unit": group.unit, "data-scope": group.scope, "data-direction": group.direction, "data-contract": group.contract,
        "data-engine": group.engine, "data-capture": group.capture, "data-series-id": group.id,
        "data-context": group.id, "data-label": group.label)
      if group.study != first-study or group.case != first-case or group.metric != first-metric { attrs.insert("hidden", "") }
      el("article", class: "history-series", attrs: attrs, [
        #el("h3", group.label)
        #el("p", class: "history-scope", group.scope + " · " + group.contract)
        #el("div", class: "history-chart", attrs: ("data-series-id": group.id), [])
        #if group.records.filter(record => record.qualified).len() == 1 {
          el("p", class: "history-sparse", [One qualified measured revision. No trend is available yet.])
        } else if group.records.filter(record => record.qualified).len() == 0 {
          el("p", class: "history-sparse", [No qualified commit point in this configuration. The captured evidence remains below.])
        }
        #el("details", [
          #el("summary", [Captured values and provenance])
          #el("div", class: "history-tablewrap", el("table", class: "history-table", [
            #el("thead", el("tr", ("Measured commit", "Median", "Observed range", "Samples", "Qualification", "Evidence").map(title => el("th", attrs: (scope: "col"), title)).join()))
            #el("tbody", group.records.map(record => history-record-row(record, group, reports)).join())
          ]))
          #el("p", [*Contract definition:*])
          #el("pre", json.encode(group.snapshot.contracts.at(group.contract, default: (:)), pretty: true))
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
          #if href != none { el("a", attrs: (href: href + "#availability"), [Retained record]) }
          else { [ · Report link unavailable] }
        ])
      }
    ])
  ])
}
