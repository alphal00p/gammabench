#import "/src/gammabench/assets/history.typ": history-source, history-context, history-complete, history-selected, history-canonical
#import "/src/gammabench/assets/matrix.typ": matrix-metric

// Freeze display identities and qualification with the same helpers as reports.
// No samples or newly calculated statistics are sent to the list controller.
#let list-model(projected, registry, proposals: (:), source-links: (:)) = {
  let records = ()
  let sets = (:)
  let contexts = (:)
  let runner-contexts = (:)
  let pairs = ()
  let unavailable = ()
  for workspace in projected.workspaces {
    let benchmark = registry.benchmarks.find(item => item.id == workspace.id)
    let pair-values(pair) = workspace.display_values.comparisons.at(pair.baseline).at(pair.candidate)
    for (index, analysis) in workspace.analyses.enumerate() {
      let report = benchmark.reports.at(index)
      for item in analysis.series {
        let snapshot = analysis.snapshots.find(snapshot => snapshot.id == item.snapshot_id)
        let modes = (:)
        modes.insert(snapshot.id, benchmark.at("capture_modes", default: (:)))
        let source = history-source(item, snapshot, registry.subjects, modes)
        let context-key = history-context(benchmark.id, item, snapshot, source)
        let context-id = contexts.at(context-key, default: "context-" + str(contexts.len()))
        contexts.insert(context-key, context-id)
        let gate = snapshot.provenance.at("ci_gate", default: none)
        let ci = snapshot.provenance.at("ci", default: (:))
        let phase = if gate == none { "" } else { gate.phase }
        let set-id = report + if phase == "" { "" } else { "#" + phase }
        if not sets.keys().contains(set-id) {
          sets.insert(set-id, (id: set-id, benchmark: benchmark.id, report: report, phase: phase,
            label: snapshot.created_at.slice(0, 10) + " · " + if ci.at("run_id", default: none) != none {
              "run " + str(ci.run_id) + "/" + str(ci.run_attempt) + " · " + phase
            } else { snapshot.id }, createdAt: snapshot.created_at))
        }
        let runner = ""
        let runner-label = ""
        if gate != none {
          let receipt = item.at("history", default: (:)).at("context", default: (:))
          let host = receipt.at("host", default: (:))
          runner-label = host.at("cpu_model", default: host.at("cpu", default: ""))
          if runner-label != "" {
            let key = history-context(benchmark.id, item, snapshot, source, without-runner: true)
            runner = runner-contexts.at(key, default: "runner-context-" + str(runner-contexts.len()))
            runner-contexts.insert(key, runner)
            runner-label += " · " + host.at("runner_image", default: "")
          }
        }
        let complete = history-complete(item, snapshot)
        let qualified = complete and item.validation_status == "passed"
        let reason = if not complete { "Incomplete or unavailable measurement" }
          else if item.validation_status != "passed" { item.validation_status + ": " + item.validation_reason }
          else { "" }
        let implementation = source.implementation
        let values = workspace.display_values.series.at(item.id)
        let source-identity = implementation.at("source_identity", default: (:))
        let archive-hash = source-identity.at("archive_sha256", default: source-identity.at("sha256", default: ""))
        let immutable-version = (implementation.source_kind == "archive" and not source-identity.at("dirty", default: false)
          and type(archive-hash) == str and archive-hash.match(regex("^[a-fA-F0-9]{64}$")) != none)
        let history-qualified = qualified and (source.reason == "" or immutable-version)
        let proposal = proposals.at(snapshot.id, default: none)
        let pr = if proposal != none { proposal.pr } else { ci.at("pr", default: none) }
        let pulls = if type(pr) == int and pr > 0 and source.revision == ci.at("candidate", default: none) { (pr,) } else if type(source.revision) == str {
          source-links.at(source.repository, default: (:)).at(source.revision, default: ())
        } else { () }
        let baseline = if gate != none { gate.identity.baseline.commit } else { "" }
        let paired = analysis.comparisons.filter(pair => pair.candidate == item.id and pair.status == "comparable"
          and analysis.series.any(other => other.id == pair.baseline and other.snapshot_id == item.snapshot_id and other.variant == "baseline"))
        records.push((id: item.id, shortId: values.identity, benchmark: benchmark.id, case: item.case, task: item.task,
          snapshot: snapshot.id, "set": set-id, report: report, evidence: workspace.evidence.at(snapshot.id, default: (:)),
          tool: implementation.tool_id, toolLabel: implementation.tool_label,
          way: implementation.way_id, wayLabel: implementation.way_label,
          version: implementation.version_label, versionId: implementation.version_id,
          commit: source.revision, repository: source.repository, capture: source.capture,
          metric: item.metric, metricLabel: matrix-metric(item.metric), unit: item.unit,
          scope: item.scope, direction: item.direction, contract: item.contract,
          median: values.median, min: values.minimum, max: values.maximum, count: item.count,
          qualified: qualified, historyQualified: history-qualified,
          history: history-selected(snapshot, item: item), reason: reason,
          historyReason: if reason != "" { reason } else if not history-qualified { source.reason } else { "" },
          "context": context-id, contextKnown: source.reason == "", runnerContext: runner, runnerLabel: runner-label,
          createdAt: snapshot.created_at, origin: item.origin, phase: phase, variant: item.variant,
          proposed: proposal != none and item.variant == "candidate", pr: pr, pulls: pulls, baselineCommit: baseline,
          runId: ci.at("run_id", default: none), runAttempt: ci.at("run_attempt", default: none),
          pairedRatio: if paired.len() == 1 { pair-values(paired.first()).ratio } else { none }))
      }
      for pair in analysis.comparisons {
        pairs.push((baseline: pair.baseline, candidate: pair.candidate, status: pair.status,
          ratio: pair-values(pair).ratio, reverse: pair-values(pair).speedup,
          count: pair.ratio.count, reasons: pair.reasons))
      }
      for item in analysis.availability {
        let snapshot = analysis.snapshots.find(snapshot => snapshot.id == item.snapshot_id)
        unavailable.push(item + (benchmark: benchmark.id, report: report, createdAt: snapshot.created_at))
      }
    }
  }
  assert(records.map(record => record.id).dedup().len() == records.len(), message: "Duplicate measurement identity in list")
  assert(records.map(record => record.shortId).dedup().len() == records.len(), message: "Colliding saved measurement identities")
  (schema_version: 1, records: records, sets: sets.values(), comparisons: pairs, unavailable: unavailable)
}
