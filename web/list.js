/* Frozen measurements only. Histories never infer pairing or recalculate ratios. */
const GammabenchList = (() => {
  'use strict';
  const key = item => JSON.stringify([item.benchmark_id ?? item.benchmark, item.case_id ?? item.case ?? null]);
  const measurement = r => JSON.stringify([r.metric, r.unit, r.scope, r.direction, r.contract]);
  const implementation = r => JSON.stringify([r.tool, r.way, r.capture, r.repository]);
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  function hydrate(model) {
    const number = value => value === null ? null : typeof value === 'number' ? value :
      typeof value === 'string' && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value) && Number.isFinite(Number(value)) ? Number(value) : null;
    for (const r of model.records) for (const field of ['median', 'min', 'max', 'pairedRatio']) r[field] = number(r[field]);
    for (const pair of model.comparisons) for (const field of ['ratio', 'reverse']) pair[field] = number(pair[field]);
    return model;
  }
  const numeric = r => r.qualified && r.count > 0 && [r.median, r.min, r.max].every(finite) && r.min <= r.median && r.median <= r.max;
  const stamp = r => /^\d{4}-\d{2}-\d{2}T/.test(r.createdAt || '') ? Date.parse(r.createdAt) : NaN;
  const ordered = records => [...records].sort((a, b) => Number(a.proposed) - Number(b.proposed) ||
    (Number.isFinite(stamp(a)) && Number.isFinite(stamp(b)) ? stamp(a) - stamp(b) : 0) || a.id.localeCompare(b.id));
  function segments(records, unavailable = [], captures = records) {
    const main = ordered(records.filter(r => !r.proposed)), lines = [], runners = [], proposals = [];
    const valid = r => numeric(r) && r.historyQualified && Number.isFinite(stamp(r));
    const run = r => JSON.stringify([r.repository, r.runId, r.runAttempt]);
    const compareRun = (a, b) => String(a.runId).localeCompare(String(b.runId), 'en', {numeric: true}) || a.runAttempt - b.runAttempt;
    const runs = [...new Map(captures.filter(r => r.history && !r.proposed && r.phase === 'discovery' && r.runId).map(r => [run(r), r])).values()];
    let previous;
    for (const r of main) {
      const interrupted = previous && unavailable.some(a => a.benchmark === r.benchmark && a.case === r.case &&
        a.engine === r.tool && (!a.variant || a.variant === r.variant) && stamp(a) > stamp(previous) && stamp(a) <= stamp(r));
      const missingRun = previous && previous.runId && r.runId && (compareRun(previous, r) >= 0 || runs.some(c => c.repository === r.repository &&
        compareRun(previous, c) < 0 && compareRun(c, r) < 0 && !main.some(point => run(point) === run(c))));
      if (previous && !interrupted && !missingRun && valid(previous) && valid(r) && stamp(r) > stamp(previous)) {
        if (r.context === previous.context) lines.push([previous, r]);
        else if (r.runnerContext && r.runnerContext === previous.runnerContext) runners.push([previous, r]);
      }
      previous = r;
    }
    for (const r of records.filter(r => r.proposed && valid(r))) {
      const base = main.filter(b => valid(b) && b.context === r.context && b.repository === r.repository &&
        b.commit && b.commit === r.baselineCommit && stamp(b) <= stamp(r)).at(-1);
      if (base) proposals.push([base, r]);
    }
    return {lines, runners, proposals};
  }
  function paired(comparisons, reference, candidate) {
    if (!numeric(reference) || !numeric(candidate)) return {status: 'suppressed', reason: 'Unqualified or incomplete measurement'};
    if (reference.median <= 0 || candidate.median <= 0) return {status: 'suppressed', reason: 'Nonpositive measurements have no paired multiplier'};
    if (reference.id === candidate.id) return {status: 'reference', ratio: 1};
    if (reference.set !== candidate.set || key(reference) !== key(candidate) || measurement(reference) !== measurement(candidate))
      return {status: 'independent', reason: 'Independent captures; no paired multiplier'};
    const direct = comparisons.find(p => p.baseline === reference.id && p.candidate === candidate.id);
    const reverse = comparisons.find(p => p.candidate === reference.id && p.baseline === candidate.id);
    const pair = direct || reverse, ratio = direct ? pair.ratio : pair?.reverse;
    if (pair?.status === 'comparable' && finite(ratio) && ratio > 0)
      return {status: 'paired', ratio, count: pair.count};
    return {status: 'suppressed', reason: pair?.reasons?.join('; ') || 'No recorded qualified pairing for this reference'};
  }
  function readiness(records, comparisons) {
    if (records.length < 2) return {ready: false, message: 'Select at least two pinned measurements.'};
    if (records.some(r => !numeric(r))) return {ready: false, message: 'Unqualified and incomplete captures stay in separate panels.'};
    if (records.some(r => key(r) !== key(records[0]) || measurement(r) !== measurement(records[0])))
      return {ready: false, message: 'Different workloads, metrics, units or scopes stay in separate panels.'};
    const references = records.filter(ref => records.every(r => ['reference', 'paired'].includes(paired(comparisons, ref, r).status)));
    if (references.length) return {ready: true, kind: 'paired', references, message: 'Recorded paired comparison · same workload and measurement.'};
    const suppressed = comparisons.some(p => p.status !== 'comparable' && records.some(r => r.id === p.baseline) && records.some(r => r.id === p.candidate));
    if (!suppressed && records.every(r => r.contextKnown && r.context === records[0].context))
      return {ready: true, kind: 'absolute', references: [], message: 'Matching independent capture conditions · absolute values only.'};
    return {ready: false, message: 'No qualified recorded pairing or matching independent conditions. Keep these captures separate.'};
  }
  function matchingRecords(records, row) {
    const contexts = row.contexts.filter(Boolean);
    return records.filter(r => key(r) === key(row.item) && contexts.some(c => c.snapshot_id === r.snapshot &&
      c.task === r.task && (!c.metrics?.length || c.metrics.includes(r.metric))));
  }
  function format(value, unit) {
    if (!finite(value)) return 'Unavailable';
    const base = unit.split('/')[0], rest = unit.slice(base.length);
    const scales = base === 'ns' ? [[1e9, 's'], [1e6, 'ms'], [1e3, 'µs']] : base === 's' ? [[1, 's'], [1e-3, 'ms'], [1e-6, 'µs'], [1e-9, 'ns']] :
      base === 'bytes' ? [[1e9, 'GB'], [1e6, 'MB'], [1e3, 'kB']] : [];
    const [scale, suffix] = scales.find(([scale]) => Math.abs(value) >= scale) || [1, base === 'bytes' ? 'B' : base];
    return (value / scale).toLocaleString(undefined, {maximumSignificantDigits: 3}) + (suffix ? ' ' + suffix + rest : '');
  }
  function decode(search, records) {
    const params = new URLSearchParams(search), ids = new Set(records.map(r => r.id));
    let saved = {}, error = '';
    if (params.has('list')) {
      try {
        if (params.get('list').length > 200000) throw Error();
        saved = JSON.parse(params.get('list'));
        if (!saved || Array.isArray(saved) || typeof saved !== 'object') throw Error();
      } catch { saved = {}; error = 'The saved list selection is invalid. Choose measurements again.'; }
    }
    if (saved.version === 2) {
      const aliases = new Map(records.map(r => [r.shortId || r.id, r]));
      const selected = value => Array.isArray(value) ? value.map(id => aliases.get(id)).filter(Boolean) : [];
      const metrics = {}, choices = {}, references = {}, sets = {};
      for (const r of selected(saved.metrics)) metrics[key(r)] = measurement(r);
      for (const r of selected(saved.choices)) choices[key(r) + measurement(r) + implementation(r)] = r.id;
      for (const r of selected(saved.references)) references[key(r) + measurement(r) + r.set] = r.id;
      for (const choice of Array.isArray(saved.sets) ? saved.sets : []) {
        if (!Array.isArray(choice)) continue;
        const r = aliases.get(choice[0]); if (r) sets[key(r) + measurement(r)] = choice[1] === true ? 'independent' : r.set;
      }
      if (Array.isArray(saved.pins) && saved.pins.some(id => !aliases.has(id))) error = 'Some saved pins are unavailable in this catalog. Their identities were not substituted.';
      saved = {...saved, metrics, choices, references, sets, pins: selected(saved.pins).map(r => r.id),
        selected: Array.isArray(saved.selected) ? selected(saved.selected).map(r => r.id) : undefined,
        evidence: selected(saved.evidence).map(key), pinReference: aliases.get(saved.pinReference)?.id};
    }
    const map = value => value && !Array.isArray(value) && typeof value === 'object'
      ? Object.fromEntries(Object.entries(value).slice(0, 500).filter(([k, v]) => k.length < 5000 && typeof v === 'string' && v.length < 5000)) : {};
    const pins = Array.isArray(saved.pins) ? [...new Set(saved.pins.filter(id => ids.has(id)))].slice(0, 100) : [];
    if (Array.isArray(saved.pins) && saved.pins.some(id => !ids.has(id))) error = 'Some saved pins are unavailable in this catalog. Their identities were not substituted.';
    const benchmark = params.get('benchmark') || records.find(r => r.snapshot === params.get('study'))?.benchmark || '';
    return {view: params.get('tab') === 'history' ? 'history' : params.get('tab') === 'compare' || saved.view === 'compare' ? 'compare' : 'history',
      benchmark, case: params.get('case') || '', tag: params.get('tag') || '',
      pins, selected: pins.filter(id => !Array.isArray(saved.selected) || saved.selected.includes(id)),
      metrics: map(saved.metrics), sets: map(saved.sets), choices: map(saved.choices), references: map(saved.references),
      evidence: Array.isArray(saved.evidence) ? saved.evidence.filter(id => typeof id === 'string' && id.length < 5000).slice(0, 100) : [],
      pinReference: ids.has(saved.pinReference) ? saved.pinReference : '', compare: saved.compare === true, error};
  }
  function encode(state, records, evidence = []) {
    const lookup = new Map(records.map(r => [r.id, r])), token = r => r.shortId || r.id;
    const chosen = values => [...new Set(Object.values(values).map(id => lookup.get(id)).filter(Boolean).map(token))];
    const pins = state.pins.map(id => token(lookup.get(id)));
    return {version: 2, pins,
      ...(state.selected.length === state.pins.length ? {} : {selected: state.selected.map(id => token(lookup.get(id)))}),
      metrics: Object.entries(state.metrics).map(([k, v]) => records.find(r => key(r) === k && measurement(r) === v)).filter(Boolean).map(token),
      choices: chosen(state.choices), references: chosen(state.references),
      sets: Object.entries(state.sets).map(([k, v]) => {
        const r = records.find(r => key(r) + measurement(r) === k && (v === 'independent' || r.set === v));
        return r ? [token(r), v === 'independent'] : null;
      }).filter(Boolean),
      evidence: evidence.map(k => records.find(r => key(r) === k)).filter(Boolean).map(token),
      pinReference: lookup.has(state.pinReference) ? token(lookup.get(state.pinReference)) : '', compare: state.compare};
  }
  function create(root, catalog, refresh) {
    if (!root.dataset.listModel) return null;
    const model = hydrate(JSON.parse(root.dataset.listModel)), records = model.records;
    const lookup = new Map(records.map(r => [r.id, r])), sets = new Map(model.sets.map(s => [s.id, s]));
    const byId = id => root.querySelector('#' + id), e = (tag, cls, text) => {
      const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n;
    };
    const button = (text, field, value, cls = 'explorer-button') => {
      const n = e('button', cls, text); n.type = 'button'; n.dataset[field] = value; n.dataset.listControl = field + ':' + value; return n;
    };
    const select = (title, field, id, options, selected) => {
      const label = e('label', 'list-select'), n = e('select'); n.dataset[field] = id; n.dataset.listControl = field + ':' + id;
      n.setAttribute('aria-label', title); label.append(e('span', '', title), n);
      for (const [value, text] of options) { const option = e('option', '', text); option.value = value; n.append(option); }
      if (options.some(([value]) => value === selected)) n.value = selected;
      return label;
    };
    const path = raw => {
      if (typeof raw !== 'string' || !/^(docs\/results\/|snapshots\/)/.test(raw) || raw.includes('\\') || raw.split('/').includes('..')) return '';
      return new URL((root.dataset.root || '') + raw, location.href).href;
    };
    const link = (text, href) => { const n = e('a', '', text); if (href) n.href = href; return n; };
    const sourceURL = r => /^https:\/\/github\.com\/[A-Za-z0-9-]+\/[A-Za-z0-9_.-]+$/.test(r.repository || '') ? r.repository : '';
    const label = r => `${r.toolLabel} / ${r.wayLabel} @ ${r.version}${r.proposed ? ' · Proposed PR #' + r.pr : r.phase && r.variant === 'baseline' ? ' · paired base' : ''}`;
    const workloadTitle = r => {
      const title = catalog.cases.find(c => key(c) === key(r))?.benchmark_title || r.benchmark;
      return title === r.case ? title : title + ' / ' + r.case;
    };
    const date = r => Number.isFinite(stamp(r)) ? new Date(r.createdAt).toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'}) : 'Capture date unavailable';
    let state = decode(location.search, records), openEvidence = new Set(state.evidence), plots = new Set();
    let evidenceRequested = new URLSearchParams(location.search).get('tab') === 'evidence';
    const board = byId('list-pins'); board.hidden = false;
    function remember(push = true) {
      const url = new URL(location.href);
      const saved = encode(state, records, [...openEvidence]);
      url.searchParams.set('tab', state.view); url.searchParams.set('list', JSON.stringify(saved));
      for (const k of ['benchmark', 'case', 'tag']) { if (state[k]) url.searchParams.set(k, state[k]); else url.searchParams.delete(k); }
      // The old fields initialize an exact selection once; subsequent list state
      // owns it, so changing a metric cannot be undone by a stale legacy field.
      for (const k of ['metric', 'unit', 'scope', 'direction', 'contract', 'study', 'set', 'view', 'reference']) url.searchParams.delete(k);
      if (url.href !== location.href) history[push ? 'pushState' : 'replaceState'](null, '', url);
    }
    const ns = 'http://www.w3.org/2000/svg', svgNode = (tag, attrs = {}, text) => {
      const n = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      if (text !== undefined) n.textContent = text; return n;
    };
    const observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
      for (const entry of entries) { entry.target._visible = entry.isIntersecting; if (entry.isIntersecting) entry.target._draw?.(); }
    }, {rootMargin: '200px'}) : null;
    const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(entries => {
      for (const entry of entries) if (entry.target._visible !== false) entry.target._draw?.();
    }) : null;
    function chart(container, draw) {
      container.className = 'list-chart'; container._draw = () => draw(container); plots.add(container);
      container._visible = !observer || container.getBoundingClientRect().top < innerHeight + 200;
      observer?.observe(container); resize?.observe(container); if (container._visible) container._draw();
    }
    function svg(container, height, title) {
      const width = Math.max(180, container.getBoundingClientRect().width || 400);
      const n = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img', 'aria-label': title});
      n.append(svgNode('title', {}, title)); container.replaceChildren(n); return {n, width};
    }
    function drawHistory(container, values, selected) {
      const rows = ordered(values), valid = rows.filter(r => numeric(r) && r.historyQualified && Number.isFinite(stamp(r)));
      const {n, width} = svg(container, 178, 'Independent capture history; observed min–max ranges; capture order, not commit dates');
      if (!valid.length) { n.append(svgNode('text', {x: 8, y: 58}, 'No qualified history points')); return; }
      const right = width - 14, top = 16, bottom = 116;
      let low = Math.min(...valid.map(r => r.min)), high = Math.max(...valid.map(r => r.max));
      const pad = (high - low || Math.abs(high) || 1) * .18; low = low >= 0 ? Math.max(0, low - pad) : low - pad; high += pad;
      const ticksY = [low, (low + high) / 2, high];
      let labelsY = ticksY.map(v => format(v, rows[0].unit));
      const probe = svgNode('text', {visibility: 'hidden'}); n.append(probe);
      const labelWidth = () => Math.max(...labelsY.map(text => { probe.textContent = text; return probe.getComputedTextLength(); }));
      if (labelWidth() > width - 130) {
        labelsY = ticksY.map(v => v.toLocaleString(undefined, {notation: v !== 0 && (Math.abs(v) < .001 || Math.abs(v) >= 1e15) ? 'scientific' : 'compact', maximumSignificantDigits: 3}));
        n.append(svgNode('text', {x: Math.max(58, labelWidth() + 10) + 6, y: 11}, rows[0].unit));
      }
      const left = Math.max(58, labelWidth() + 10); probe.remove();
      const x = r => rows.length === 1 ? (left + right) / 2 : left + rows.indexOf(r) * (right - left) / (rows.length - 1);
      const y = v => bottom - (v - low) / (high - low) * (bottom - top);
      for (const [i, value] of ticksY.entries()) {
        n.append(svgNode('line', {x1: left, x2: right, y1: y(value), y2: y(value), class: 'list-grid'}));
        n.append(svgNode('text', {x: left - 7, y: y(value) + 4, 'text-anchor': 'end'}, labelsY[i]));
      }
      const paths = segments(rows, model.unavailable, records);
      for (const [kind, lines] of Object.entries(paths)) for (const [a, b] of lines) {
        const line = svgNode('line', {x1: x(a), y1: y(a.median), x2: x(b), y2: y(b.median), class: 'list-trend list-' + kind});
        line.append(svgNode('title', {}, kind === 'runners' ? 'Runner changed; capture order does not establish a software speedup' : kind === 'proposals' ? 'Proposed measurement connected to its recorded base' : 'Matching measurement conditions'));
        n.append(line);
      }
      const ticks = new Set([0, rows.length - 1]);
      if (rows.length > 3 && width > 380) ticks.add(Math.floor((rows.length - 1) / 2));
      rows.forEach((r, i) => {
        const good = valid.includes(r), active = r.id === selected;
        const hit = svgNode('g', {'data-list-record': r.id, class: 'list-mark'});
        hit.append(svgNode('title', {}, `${label(r)} · ${date(r)} UTC · ${good ? format(r.median, r.unit) + ' · observed ' + format(r.min, r.unit) + '–' + format(r.max, r.unit) : r.historyReason || 'Unqualified'}${r.runnerLabel ? ' · ' + r.runnerLabel : ''}`));
        const cy = good ? y(r.median) : bottom, radius = active ? 6 : 4;
        if (good) {
          hit.append(svgNode('line', {x1: x(r), x2: x(r), y1: y(r.min), y2: y(r.max), class: 'list-range'}));
          hit.append(r.proposed ? svgNode('path', {d: `M${x(r)} ${cy - radius}l${radius} ${radius}l-${radius} ${radius}l-${radius} -${radius}Z`, class: 'list-point list-proposed'}) :
            svgNode('circle', {cx: x(r), cy, r: radius, class: 'list-point'}));
        } else hit.append(svgNode('text', {x: x(r), y: bottom - 5, 'text-anchor': 'middle'}, '×'));
        hit.append(svgNode('rect', {x: x(r) - 15, y: cy - 16, width: 30, height: 32, fill: 'transparent'})); n.append(hit);
        if (ticks.has(i)) {
          const anchor = i === 0 && rows.length > 1 ? 'start' : i === rows.length - 1 && rows.length > 1 ? 'end' : 'middle';
          const tick = svgNode('text', {x: x(r), y: 138, 'text-anchor': anchor});
          tick.append(svgNode('tspan', {x: x(r)}, r.proposed ? '◇ PR #' + r.pr : r.commit ? r.commit.slice(0, 7) : r.version));
          tick.append(svgNode('tspan', {x: x(r), dy: 15}, date(r).replace(/ \d{4}$/, ''))); n.append(tick);
        }
      });
      n.append(svgNode('text', {x: (left + right) / 2, y: 174, 'text-anchor': 'middle'}, 'Own versions / captures'));
    }
    function drawComparison(container, values, reference, kind) {
      const ref = lookup.get(reference), height = 56 + values.length * 60;
      const {n, width} = svg(container, height, kind === 'paired' ? 'Recorded candidate/reference multipliers; absolute medians beneath' : 'Absolute captured medians; independent measurements have no paired multiplier');
      const left = Math.min(width > 700 ? 280 : width > 460 ? 210 : 130, width * .44), right = width - 28;
      const results = values.map(r => ({r, pair: kind === 'paired' && ref ? paired(model.comparisons, ref, r) : null}));
      const plotted = results.filter(v => numeric(v.r) && (kind !== 'paired' || finite(v.pair?.ratio)));
      const raw = plotted.map(v => kind === 'paired' ? v.pair.ratio : v.r.median);
      let low = Math.min(...raw, kind === 'paired' ? 1 : Infinity), high = Math.max(...raw, kind === 'paired' ? 1 : -Infinity);
      if (!raw.length) { low = 0; high = 1; }
      const padding = (high - low || Math.abs(high) || 1) * .25; low = low >= 0 ? Math.max(0, low - padding) : low - padding; high += padding;
      const x = v => left + (v - low) / (high - low) * (right - left);
      for (const v of [low, (low + high) / 2, high]) n.append(svgNode('line', {x1: x(v), x2: x(v), y1: 12, y2: height - 36, class: 'list-grid'}));
      if (kind === 'paired') n.append(svgNode('line', {x1: x(1), x2: x(1), y1: 12, y2: height - 36, class: 'list-reference'}));
      results.forEach(({r, pair}, index) => {
        const y = 26 + index * 60, lines = width < 500 ? [r.toolLabel, r.wayLabel, r.version] : [r.toolLabel + ' / ' + r.wayLabel, r.version];
        const name = svgNode('text', {x: 0, y});
        const limit = Math.max(8, Math.floor((left - 12) / 6.7));
        name.append(svgNode('title', {}, label(r)));
        lines.forEach((line, i) => name.append(svgNode('tspan', {x: 0, dy: i ? 14 : 0}, line.length > limit ? line.slice(0, limit - 1) + '…' : line))); n.append(name);
        const value = kind === 'paired' ? pair?.ratio : r.median;
        if (!numeric(r) || !finite(value)) {
          n.append(svgNode('text', {x: left, y: y + 7}, numeric(r) ? 'No paired result' : 'Unqualified'));
          if (finite(r.median)) {
            const captured = svgNode('text', {x: left, y: y + 25}, format(r.median, r.unit)); n.append(captured);
            if (captured.getComputedTextLength() > right - left) {
              captured.textContent = r.median.toLocaleString(undefined, {maximumSignificantDigits: 3, notation: 'scientific'});
              captured.append(svgNode('tspan', {x: left, dy: 13}, r.unit));
            }
          }
          return;
        }
        const anchor = x(value) > right - 70 ? 'end' : 'start', tx = x(value) + (anchor === 'end' ? -9 : 9);
        const better = kind === 'paired' && pair.status !== 'reference' && (r.direction === 'higher' ? value > 1 : value < 1);
        const worse = kind === 'paired' && pair.status !== 'reference' && (r.direction === 'higher' ? value < 1 : value > 1);
        const mark = svgNode('circle', {cx: x(value), cy: y + 3, r: 4, class: 'list-point'});
        mark.append(svgNode('title', {}, `${label(r)} · ${date(r)} UTC · ${format(r.median, r.unit)}${pair?.count ? ' · ' + pair.count + ' matched pairs' : ''}`)); n.append(mark);
        const large = svgNode('text', {x: tx, y: y + 8, 'text-anchor': anchor, class: 'list-multiplier', 'data-rank': better ? 'better' : worse ? 'worse' : 'reference'},
          kind === 'paired' ? value.toLocaleString(undefined, {maximumSignificantDigits: 3, notation: value < .01 || value >= 10000 ? 'scientific' : 'standard'}) + '×' : format(r.median, r.unit));
        n.append(large);
        if (large.getComputedTextLength() > right - left - 5) large.textContent = value.toLocaleString(undefined, {maximumSignificantDigits: 3, notation: 'scientific'}) + (kind === 'paired' ? '×' : '');
        const fit = text => {
          n.append(text); const length = text.getComputedTextLength();
          if (x(value) + 9 + length <= right) { text.setAttribute('text-anchor', 'start'); text.setAttribute('x', x(value) + 9); }
          else { text.setAttribute('text-anchor', 'end'); text.setAttribute('x', Math.max(left + length, x(value) - 9)); }
        };
        fit(large);
        if (kind === 'paired') fit(svgNode('text', {x: tx, y: y + 25, 'text-anchor': anchor}, format(r.median, r.unit)));
      });
      n.append(svgNode('text', {x: (left + right) / 2, y: height - 23, 'text-anchor': 'middle'}, kind === 'paired' ? 'Reference 1×' : values[0]?.unit || ''));
      n.append(svgNode('text', {x: (left + right) / 2, y: height - 8, 'text-anchor': 'middle'}, (values[0]?.direction === 'higher' ? 'Higher' : 'Lower') + ' is better'));
    }
    function identityLinks(r) {
      const n = e('div', 'list-identity'), repository = sourceURL(r);
      if (repository && /^[a-f0-9]{40}$/i.test(r.commit || '')) n.append(link(r.commit.slice(0, 10), repository + '/commit/' + r.commit));
      else n.append(e('span', '', r.version));
      for (const number of r.pulls || []) if (repository && Number.isSafeInteger(number) && number > 0) n.append(link('PR #' + number, repository + '/pull/' + number));
      if (repository && r.pr && !r.pulls?.includes(r.pr)) n.append(link('Capture PR #' + r.pr, repository + '/pull/' + r.pr));
      if (r.runId && repository) n.append(link('Run ' + r.runId + '/' + r.runAttempt, repository + '/actions/runs/' + r.runId + '/attempts/' + r.runAttempt));
      n.append(e('span', '', date(r) + ' UTC' + (r.phase ? ' · ' + r.phase : '')));
      return n;
    }
    function pinButton(r) { return button(state.pins.includes(r.id) ? 'Pinned' : 'Pin capture', 'listPin', r.id, 'explorer-button' + (state.pins.includes(r.id) ? ' list-active' : '')); }
    function evidence(values, row) {
      const details = e('details', 'list-evidence'), caseKey = row ? key(row.item) : 'pins';
      details.dataset.listEvidence = caseKey; details.open = openEvidence.has(caseKey); details.append(e('summary', '', 'Evidence and captured values'));
      if (row && evidenceRequested) { details.open = true; openEvidence.add(caseKey); evidenceRequested = false; }
      const wrap = e('div', 'list-table-wrap'), table = e('table', 'list-evidence-table'), head = e('tr');
      for (const text of ['Measurement / source', 'Median and observed range', 'Qualification / evidence']) head.append(e('th', '', text));
      const thead = e('thead'); thead.append(head); table.append(thead); const body = e('tbody');
      for (const r of ordered(values)) {
        const tr = e('tr'), a = e('td'), b = e('td'), c = e('td');
        a.append(e('div', '', label(r)), identityLinks(r), e('div', 'list-meta', r.variant + ' · ' + r.origin));
        b.append(e('div', '', format(r.median, r.unit)), e('div', 'list-meta', format(r.min, r.unit) + ' – ' + format(r.max, r.unit)), e('div', 'list-meta', r.count + ' samples'));
        c.append(e('div', '', numeric(r) ? 'Qualified · complete' : r.reason || 'Unqualified'), link('Report', path(r.report)));
        c.append(link('Analysis JSON', path(r.report.replace(/index\.html$/, 'analysis.json'))));
        for (const [kind, href] of Object.entries(r.evidence)) c.append(link({snapshot: 'Sealed snapshot', suite: 'Suite', hashes: 'Checksums'}[kind] || kind, path(href)));
        c.append(pinButton(r)); tr.append(a, b, c); body.append(tr);
      }
      table.append(body); wrap.append(table); details.append(wrap);
      if (row) {
        const unavailable = model.unavailable.filter(r => r.benchmark === row.item.benchmark_id && r.case === row.item.case_id);
        for (const r of unavailable) {
          const note = e('p', 'list-meta', `${r.engine || r.task || 'Capture'} · ${r.status}: ${r.reason || 'No successful measurement'} · `);
          note.append(link('Source report', path(r.report))); details.append(note);
        }
        const metadata = e('details', 'list-metadata'); metadata.append(e('summary', '', 'Workload and matching capture metadata'));
        metadata.append(e('pre', '', JSON.stringify({workload: row.item.metadata, captures: row.contexts.filter(Boolean).map(c => ({id: c.id, metadata: c.metadata, implementation: c.implementation}))}, null, 2)));
        details.append(metadata);
      }
      return details;
    }
    function comparePanel(host, values, reference, kind, referenceKey, draws, allowedReferences = null) {
      const ref = lookup.get(reference), refs = allowedReferences || values.filter(r => numeric(r) && (kind !== 'paired' || r.median > 0));
      const effective = ref && refs.some(r => r.id === ref.id) ? ref : refs[0];
      if (kind === 'paired') host.append(select('Reference · 1×', referenceKey === 'pins' ? 'listPinReference' : 'listReference', referenceKey,
        refs.map(r => [r.id, label(r)]), effective?.id));
      host.append(e('p', 'list-meta', kind === 'paired' ? 'Recorded paired multipliers · captured medians underneath' : 'Independent captures · absolute values only; no paired multiplier'));
      const plot = e('div'); host.append(plot); draws.push(() => chart(plot, box => drawComparison(box, values, effective?.id, kind)));
    }
    function historyPanels(host, values, row, metricKey, draws) {
      const histories = new Map();
      for (const r of values.filter(r => r.history)) { const k = implementation(r); if (!histories.has(k)) histories.set(k, []); histories.get(k).push(r); }
      if (!histories.size) { host.append(e('p', 'list-meta', 'No candidate discovery history for this measurement. Baselines and confirmation remain in Compare and Evidence.')); return; }
      const grid = e('div', 'list-histories');
      for (const [impl, captures] of histories) {
        const rows = ordered(captures), selectionKey = key(row.item) + metricKey + impl;
        const selected = rows.find(r => r.id === state.choices[selectionKey]) || rows.at(-1), first = rows[0];
        const panel = e('section', 'list-history'), heading = e('div', 'list-history-heading');
        heading.append(e('h3', '', first.toolLabel + ' / ' + first.wayLabel), e('span', 'list-current-value', 'Selected: ' + format(selected.median, selected.unit)));
        panel.append(heading, e('p', 'list-meta', rows.length + ' own captures · ' + new Set(rows.map(r => r.context)).size + ' measurement context(s)'));
        const plot = e('div'); panel.append(plot);
        const controls = e('div', 'list-capture-controls');
        controls.append(select('Capture to pin', 'listCapture', selectionKey, rows.map(r => [r.id, (r.proposed ? '◇ PR #' + r.pr : r.version) + ' · ' + date(r) + ' · ' + format(r.median, r.unit) + (!r.historyQualified ? ' · ' + (r.historyReason || 'unqualified') : '')]), selected.id), pinButton(selected));
        panel.append(controls, identityLinks(selected));
        if (segments(rows, model.unavailable, records).runners.length) panel.append(e('p', 'list-meta', 'Dotted connector: Runner changed. Paired results assess software changes.'));
        if (model.unavailable.some(a => a.benchmark === first.benchmark && a.case === first.case && a.engine === first.tool && stamp(a) >= stamp(rows.at(-1))))
          panel.append(e('p', 'list-meta', 'A later capture is unavailable. The selected older measurement and failure evidence remain explicit.'));
        if (selected.proposed) panel.append(e('p', 'list-meta list-proposal-label', '◇ Proposed PR #' + selected.pr));
        if (finite(selected.pairedRatio)) panel.append(e('p', 'list-meta', selected.pairedRatio.toLocaleString(undefined, {maximumSignificantDigits: 3}) + '× recorded paired baseline'));
        const compare = button('Show recorded comparison', 'listShowSet', selected.id, 'explorer-quiet'); panel.append(compare);
        grid.append(panel); draws.push(() => chart(plot, box => drawHistory(box, rows, selected.id)));
      }
      host.append(grid);
    }
    function casePanel(row, draws) {
      const item = row.item, caseKey = key(item), values = matchingRecords(records, row), panel = e('article', 'list-case');
      panel.dataset.listCase = caseKey; panel.dataset.benchmark = item.benchmark_id; panel.dataset.case = item.case_id || '';
      const heading = e('div', 'list-case-heading'), title = e('h2', '', item.benchmark_title);
      heading.append(title); const tags = e('div', 'list-tags');
      for (const tag of new Set([...(item.tags || []), ...(Array.isArray(item.metadata?.topic) ? item.metadata.topic : [])])) tags.append(button('#' + tag, 'listTag', tag, 'explorer-quiet'));
      heading.append(tags); panel.append(heading);
      if (item.case_id !== item.benchmark_title) panel.append(e('p', 'list-workload', item.case_id || 'Workload not declared'));
      if (!values.length) { panel.append(e('p', 'list-meta', item.planned ? 'No measurements yet. This benchmark is registered for capture.' : 'No measured values match this configuration.'), evidence([], row)); return panel; }
      const metrics = [...new Map(values.map(r => [measurement(r), r])).entries()];
      const params = new URLSearchParams(location.search);
      const parsed = name => { try { const v = JSON.parse(params.get(name)); return Array.isArray(v) ? v : []; } catch { return []; } };
      const legacyView = parsed('view'), legacyRef = parsed('reference');
      const legacyMetric = params.get('metric') || legacyView[2], legacyScope = params.get('scope') || legacyView[4], legacyUnit = params.get('unit') || legacyView[3];
      const legacyContract = params.get('contract') || legacyView[6], legacyDirection = params.get('direction') || legacyView[5];
      const legacy = values.find(r => r.metric === legacyMetric && (!legacyScope || r.scope === legacyScope) && (!legacyUnit || r.unit === legacyUnit) &&
        (!legacyContract || r.contract === legacyContract) && (!legacyDirection || r.direction === legacyDirection));
      const legacyReferenceMatches = r => r.snapshot === legacyRef[0] && r.tool === legacyRef[1] && r.variant === legacyRef[2] &&
        (!legacyRef[3] || r.way === legacyRef[3]) && (!legacyRef[4] || r.versionId === legacyRef[4]);
      const chosen = metrics.find(([k]) => k === state.metrics[caseKey]) || metrics.find(([, r]) => legacy && measurement(r) === measurement(legacy)) ||
        metrics.find(([, r]) => r.metric === item.default_metric) || metrics[0];
      const metricKey = chosen[0], current = values.filter(r => measurement(r) === metricKey), first = current[0];
      if (legacy) state.metrics[caseKey] = metricKey;
      panel.append(select('Measurement', 'listMetric', caseKey, metrics.map(([k, r]) => [k, r.metricLabel + ' · ' + r.unit + (metrics.some(([other, v]) => other !== k && v.metric === r.metric) ? ' · ' + r.scope + ' · ' + r.contract : '')]), metricKey));
      const details = e('details', 'list-measurement-details'); details.append(e('summary', '', 'Measurement details'), e('p', 'list-meta', first.scope), e('p', 'list-meta', 'Contract: ' + first.contract + ' · ' + (first.direction === 'higher' ? 'higher' : 'lower') + ' is better. Ranges are observed min–max, not confidence intervals.'));
      panel.append(details);
      if (state.view === 'history') historyPanels(panel, current, row, metricKey, draws);
      else {
        const options = [...new Set(current.map(r => r.set))].map(id => sets.get(id));
        const setKey = caseKey + metricKey, requested = params.get('set') || (typeof legacyView[1] === 'string' ? legacyView[1] : '');
        const selected = options.find(s => s.id === state.sets[setKey]) || options.find(s => s.id === requested) ||
          options.find(s => (!requested || s.report === requested) && current.some(r => r.set === s.id && r.snapshot === legacyRef[0])) || options.find(s => s.report === requested) ||
          [...options].reverse().find(s => s.phase !== 'confirmation') || options.at(-1);
        const setId = state.sets[setKey] === 'independent' ? 'independent' : selected.id;
        if (requested && options.some(s => s.id === requested || s.report === requested) && !state.sets[setKey]) state.sets[setKey] = selected.id;
        if (requested && !options.some(s => s.id === requested || s.report === requested)) panel.append(e('p', 'list-meta', 'The requested comparison is unavailable in this catalog. Choose a retained set below.'));
        panel.append(select('Recorded comparison set', 'listSet', setKey, [...options.map(s => [s.id, s.label]), ['independent', 'Latest available · independent captures']], setId));
        let displayed = current.filter(r => r.set === setId);
        if (setId === 'independent') {
          const latest = new Map(); for (const r of ordered(current.filter(r => r.history))) latest.set(implementation(r), r); displayed = [...latest.values()];
        }
        const refKey = setKey + setId;
        const reference = displayed.find(r => r.id === state.references[refKey]) || displayed.find(legacyReferenceMatches) || displayed.find(r => r.variant === 'baseline') || displayed.find(numeric) || displayed[0];
        if (legacyRef.length && reference && legacyReferenceMatches(reference)) state.references[refKey] = reference.id;
        if (legacyRef.length && !displayed.some(legacyReferenceMatches)) panel.append(e('p', 'list-meta', 'The requested reference is unavailable in this selection. Choose a retained reference.'));
        comparePanel(panel, displayed, reference?.id, setId === 'independent' ? 'absolute' : 'paired', refKey, draws);
        for (const r of displayed) {
          const line = e('div', 'list-capture-row'), text = e('div'); text.append(e('div', 'list-record-label', label(r)), identityLinks(r));
          if (reference && setId !== 'independent') {
            const result = paired(model.comparisons, reference, r);
            if (result.status === 'suppressed') text.append(e('p', 'list-meta', result.reason));
          }
          line.append(text, pinButton(r)); panel.append(line);
        }
        for (const missing of model.unavailable.filter(r => r.benchmark === item.benchmark_id && r.case === item.case_id && (setId === 'independent' || r.report === selected.report)))
          panel.append(e('p', 'list-meta', `${missing.engine || missing.task || 'Capture'} · ${missing.status}: ${missing.reason || 'No measured value'}`));
      }
      panel.append(evidence(current, row)); return panel;
    }
    function renderPins(draws) {
      board.replaceChildren(); const selected = state.pins.filter(id => state.selected.includes(id)).map(id => lookup.get(id));
      const ready = readiness(selected, model.comparisons); if (!ready.ready) state.compare = false;
      const heading = e('div', 'list-pin-heading'), actions = e('div', 'list-pin-actions');
      heading.append(e('h2', '', 'Pinned measurements')); actions.append(e('span', 'list-meta', state.pins.length + ' pinned · ' + selected.length + ' selected'));
      const compare = button(state.compare ? 'Back to separate panels' : 'Compare selection', 'listComparePins', 'true', 'explorer-button explorer-primary');
      compare.disabled = !state.compare && !ready.ready; actions.append(compare); heading.append(actions); board.append(heading);
      board.append(e('p', 'list-pin-status', state.error || (state.pins.length ? ready.message : 'Pin an exact capture from any history or comparison.')));
      if (state.compare) {
        const ref = ready.references.find(r => r.id === state.pinReference) || ready.references.find(r => r.variant === 'baseline') || ready.references[0];
        board.append(e('h3', '', workloadTitle(selected[0])));
        comparePanel(board, selected, ref?.id, ready.kind, 'pins', draws, ready.references); board.append(evidence(selected));
      } else {
        const grid = e('div', 'list-pin-grid');
        for (const id of state.pins) {
          const r = lookup.get(id), card = e('article', 'list-pin-card'); card.dataset.pinnedRecord = id;
          const top = e('div', 'list-pin-card-heading'), choose = e('label', 'list-pin-check'), check = e('input');
          check.type = 'checkbox'; check.checked = state.selected.includes(id); check.dataset.listSelected = id; check.dataset.listControl = 'listSelected:' + id;
          check.setAttribute('aria-label', 'Include ' + label(r) + ' in comparison'); choose.append(check, document.createTextNode('Use in comparison'));
          top.append(choose, button('Remove', 'listPin', id, 'explorer-quiet')); card.append(top,
            e('p', 'list-meta', workloadTitle(r)),
            e('h3', '', label(r)), e('p', 'list-pin-value', format(r.median, r.unit)),
            e('p', 'list-meta', r.metricLabel + ' · ' + r.unit + ' · ' + r.contract), identityLinks(r),
            e('p', 'list-meta', r.scope), e('p', 'list-meta', numeric(r) ? 'Qualified · ' + r.count + ' samples' : r.reason || 'Unqualified'), link('Report and evidence', path(r.report)));
          grid.append(card);
        }
        board.append(grid);
      }
      byId('list-pin-jump').textContent = 'Pinned (' + state.pins.length + ') ↑';
    }
    function scope(rows) {
      return rows.filter(row => (!state.benchmark || row.item.benchmark_id === state.benchmark) && (!state.case || row.item.case_id === state.case) &&
        (!state.tag || [...(row.item.tags || []), ...(Array.isArray(row.item.metadata?.topic) ? row.item.metadata.topic : []), ...row.contexts.flatMap(c => Array.isArray(c?.metadata?.topic) ? c.metadata.topic : [])].includes(state.tag)));
    }
    function render(rows, groups) {
      const activeNode = document.activeElement, active = activeNode?.dataset.listControl;
      const activeCase = activeNode?.closest('[data-list-case]')?.dataset.listCase;
      const activeEvidence = activeNode?.closest('[data-list-evidence]')?.dataset.listEvidence;
      const activeBoard = Boolean(activeNode?.closest('#list-pins'));
      for (const plot of plots) { observer?.unobserve(plot); resize?.unobserve(plot); } plots = new Set();
      const draws = []; renderPins(draws);
      root.querySelectorAll('[data-list-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.listView === state.view)));
      const scopeHost = byId('list-scope'); scopeHost.replaceChildren();
      for (const [field, title] of [['benchmark', 'Benchmark'], ['case', 'Workload'], ['tag', 'Tag']]) if (state[field]) scopeHost.append(button(title + ': ' + state[field] + ' ×', 'listClearScope', field));
      const results = byId('explorer-results'); results.replaceChildren();
      const sections = groups || [{rows}];
      for (const group of sections) {
        if (group.label) results.append(e('h2', 'explorer-group-heading', group.label));
        for (const row of group.rows) results.append(casePanel(row, draws));
      }
      if (!rows.length) results.append(e('p', 'explorer-empty', 'No matching benchmark or workload. Clear a filter to broaden the list.'));
      draws.forEach(draw => draw());
      if (active) [...root.querySelectorAll('[data-list-control]')].find(node => node.dataset.listControl === active &&
        node.closest('[data-list-case]')?.dataset.listCase === activeCase &&
        node.closest('[data-list-evidence]')?.dataset.listEvidence === activeEvidence &&
        Boolean(node.closest('#list-pins')) === activeBoard)?.focus({preventScroll: true});
    }
    root.addEventListener('toggle', event => {
      const id = event.target.dataset?.listEvidence; if (id) { if (event.target.open) openEvidence.add(id); else openEvidence.delete(id); }
    }, true);
    root.addEventListener('click', event => {
      const target = event.target.closest('button, [data-list-record]'); if (!target) return;
      const d = target.dataset; let changed = true;
      if (d.listView) state.view = d.listView;
      else if (d.listTag) { state.tag = d.listTag; state.benchmark = state.case = ''; }
      else if (d.listClearScope) { state[d.listClearScope] = ''; if (d.listClearScope === 'benchmark') state.case = ''; }
      else if (d.listPin && lookup.has(d.listPin)) {
        if (state.pins.includes(d.listPin)) { state.pins = state.pins.filter(id => id !== d.listPin); state.selected = state.selected.filter(id => id !== d.listPin); }
        else if (state.pins.length < 100) { state.pins.push(d.listPin); state.selected.push(d.listPin); }
        state.compare = false;
      } else if (d.listComparePins) state.compare = !state.compare;
      else if ((d.listShowSet || d.listRecord) && lookup.has(d.listShowSet || d.listRecord)) {
        const r = lookup.get(d.listShowSet || d.listRecord), caseKey = key(r), mk = measurement(r);
        state.choices[caseKey + mk + implementation(r)] = r.id;
        if (d.listShowSet) { state.view = 'compare'; state.sets[caseKey + mk] = r.set; state.metrics[caseKey] = mk; }
      } else changed = false;
      if (changed) { state.error = ''; remember(); refresh(); }
    });
    root.addEventListener('change', event => {
      const t = event.target, d = t.dataset; let changed = true;
      if (d.listMetric) state.metrics[d.listMetric] = t.value;
      else if (d.listSet) state.sets[d.listSet] = t.value;
      else if (d.listCapture) state.choices[d.listCapture] = t.value;
      else if (d.listReference) state.references[d.listReference] = t.value;
      else if (d.listPinReference) state.pinReference = t.value;
      else if (d.listSelected) { state.selected = t.checked ? [...new Set([...state.selected, d.listSelected])] : state.selected.filter(id => id !== d.listSelected); state.compare = false; }
      else changed = false;
      if (changed) { remember(); refresh(); }
    });
    addEventListener('popstate', () => {
      state = decode(location.search, records); openEvidence = new Set(state.evidence);
      evidenceRequested = new URLSearchParams(location.search).get('tab') === 'evidence'; refresh();
    });
    return {scope, render, clearScope: () => { state.benchmark = state.case = state.tag = ''; remember(false); }};
  }
  return {key, measurement, implementation, hydrate, numeric, ordered, segments, paired, readiness, matchingRecords, format, decode, encode, create};
})();
if (typeof module !== 'undefined' && module.exports) module.exports = GammabenchList;
