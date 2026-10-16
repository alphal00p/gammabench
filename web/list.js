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
  function sharedFields(values, fields) {
    if (!values.length) return {};
    return Object.fromEntries(fields.filter(field => values.every(r => Object.hasOwn(r, field) && r[field] === values[0][field]))
      .map(field => [field, values[0][field]]));
  }
  function evidenceGroups(values) {
    const groups = new Map();
    for (const r of ordered(values)) {
      const source = JSON.stringify([r.snapshot, r.set, measurement(r), r.report,
        Object.entries(r.evidence || {}).sort(([a], [b]) => a.localeCompare(b))]);
      if (!groups.has(source)) groups.set(source, []); groups.get(source).push(r);
    }
    return [...groups.values()];
  }
  const comparisonKey = (values, reference, kind) => JSON.stringify([kind, kind === 'paired' ? reference || '' : '', values.map(r => r.id).sort()]);
  function observedRange(r) {
    if (!numeric(r) || r.min === r.max) return '';
    const low = format(r.min, r.unit), high = format(r.max, r.unit), suffix = low.slice(low.indexOf(' ') + 1);
    return low.includes(' ') && high.endsWith(' ' + suffix) ? low.slice(0, low.indexOf(' ')) + '–' + high : low + '–' + high;
  }
  const metricTitle = r => r.metricLabel.replaceAll('_', ' ').toLowerCase().endsWith(' ' + r.unit.toLowerCase()) || r.metricLabel.toLowerCase() === r.unit.toLowerCase()
    ? r.metricLabel : r.metricLabel + (r.unit ? ' · ' + r.unit : '');
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
    const openOptions = new Set(), pinEditors = new Map(), expandedComparisons = new Set();
    const proposalContext = document.querySelector('.preview-notice')?.dataset;
    const proposal = proposalContext?.proposedCandidate, proposalDate = date({createdAt: proposalContext?.capturedAt});
    const coveredProposal = r => r.proposed && r.commit === proposal && r.repository === proposalContext?.proposedRepository;
    let pinnedComparison = '', viewFeatures = {};
    let evidenceRequested = new URLSearchParams(location.search).get('tab') === 'evidence';
    const board = byId('list-pins');
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
      container.classList.add('list-chart'); container._draw = () => draw(container); plots.add(container);
      container._visible = !observer || container.getBoundingClientRect().top < innerHeight + 200;
      observer?.observe(container); resize?.observe(container); if (container._visible) container._draw();
    }
    function svg(container, height, title) {
      const width = Math.max(180, container.getBoundingClientRect().width || 400);
      const n = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img', 'aria-label': title});
      n.append(svgNode('title', {}, title)); container.replaceChildren(n); return {n, width};
    }
    function drawHistory(container, values, selected, compact = false) {
      const rows = ordered(values), valid = rows.filter(r => numeric(r) && r.historyQualified && Number.isFinite(stamp(r)));
      const {n, width} = svg(container, compact ? 92 : 178, 'Independent capture history; observed min–max ranges; capture order, not commit dates');
      if (!valid.length) { n.append(svgNode('text', {x: 8, y: 58}, 'No qualified history points')); return; }
      const right = width - 14, top = compact ? 9 : 16, bottom = compact ? 60 : 116;
      let low = Math.min(...valid.map(r => r.min)), high = Math.max(...valid.map(r => r.max));
      const pad = (high - low || Math.abs(high) || 1) * .18; low = low >= 0 ? Math.max(0, low - pad) : low - pad; high += pad;
      const ticksY = compact ? [low, high] : [low, (low + high) / 2, high];
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
      if (!compact && rows.length > 3 && width > 380) ticks.add(Math.floor((rows.length - 1) / 2));
      rows.forEach((r, i) => {
        const good = valid.includes(r), active = r.id === selected;
        const description = `${label(r)} · ${date(r)} UTC · ${good ? format(r.median, r.unit) + ' · observed ' + format(r.min, r.unit) + '–' + format(r.max, r.unit) : r.historyReason || 'Unqualified'}${r.runnerLabel ? ' · ' + r.runnerLabel : ''}`;
        const hit = svgNode('g', {'data-list-record': r.id, class: 'list-mark'});
        hit.append(svgNode('title', {}, description));
        const cy = good ? y(r.median) : bottom, radius = active ? 6 : 4;
        if (good) {
          hit.append(svgNode('line', {x1: x(r), x2: x(r), y1: y(r.min), y2: y(r.max), class: 'list-range'}));
          hit.append(r.proposed ? svgNode('path', {d: `M${x(r)} ${cy - radius}l${radius} ${radius}l-${radius} ${radius}l-${radius} -${radius}Z`, class: 'list-point list-proposed'}) :
            svgNode('circle', {cx: x(r), cy, r: radius, class: 'list-point'}));
        } else hit.append(svgNode('text', {x: x(r), y: bottom - 5, 'text-anchor': 'middle'}, '×'));
        hit.append(svgNode('rect', {x: x(r) - 15, y: cy - 16, width: 30, height: 32, fill: 'transparent'})); n.append(hit);
        const control = button('', 'listRecord', r.id, 'list-history-hit');
        control.setAttribute('aria-label', description); control.setAttribute('aria-pressed', String(active));
        // Native controls make the plotted captures available by keyboard and touch.
        control.style.left = x(r) / width * 100 + '%'; control.style.top = cy + 'px'; container.append(control);
        if (ticks.has(i)) {
          const anchor = i === 0 && rows.length > 1 ? 'start' : i === rows.length - 1 && rows.length > 1 ? 'end' : 'middle';
          const tick = svgNode('text', {x: x(r), y: compact ? 85 : 138, 'text-anchor': anchor});
          tick.append(svgNode('tspan', {x: x(r)}, r.proposed ? '◇ PR #' + r.pr : r.commit ? r.commit.slice(0, 7) : r.version));
          if (!compact) tick.append(svgNode('tspan', {x: x(r), dy: 15}, date(r).replace(/ \d{4}$/, '')));
          if (r.proposed && sourceURL(r) && Number.isSafeInteger(r.pr) && r.pr > 0) {
            const source = svgNode('a', {href: sourceURL(r) + '/pull/' + r.pr, 'aria-label': 'Proposed PR #' + r.pr});
            source.append(tick); n.append(source);
          } else n.append(tick);
        }
      });
      if (!compact) n.append(svgNode('text', {x: (left + right) / 2, y: 174, 'text-anchor': 'middle'}, 'Own versions / captures'));
    }
    function drawInlineComparison(container, value, domain, kind) {
      const width = Math.max(1, container.getBoundingClientRect().width), height = 32;
      const n = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img',
        'aria-label': finite(value) ? (kind === 'paired' ? multiplier(value) + ' of reference' : 'Captured value ' + value) : 'No qualified plotted value'});
      container.replaceChildren(n);
      const log = kind === 'paired', transform = v => log ? Math.log(v) : v;
      const raw = domain.filter(v => finite(v) && (!log || v > 0)).map(transform);
      if (!raw.length) return;
      let low = Math.min(...raw), high = Math.max(...raw);
      const pad = (high - low || Math.abs(high) || 1) * .16; low -= pad; high += pad;
      const x = v => 6 + (transform(v) - low) / (high - low) * (width - 12);
      n.append(svgNode('line', {x1: 6, x2: width - 6, y1: 16, y2: 16, class: 'list-grid'}));
      if (log) n.append(svgNode('line', {x1: x(1), x2: x(1), y1: 5, y2: 27, class: 'list-reference'}));
      if (finite(value) && (!log || value > 0)) n.append(svgNode('circle', {cx: x(value), cy: 16, r: 4, class: 'list-point'}));
    }
    const multiplier = value => value.toLocaleString(undefined, {maximumSignificantDigits: 3,
      notation: value < .01 || value >= 10000 ? 'scientific' : 'standard'}) + '×';
    function compactIdentity(r, proposalLink = true) {
      const n = e('div', 'list-identity list-compact-identity'), repository = sourceURL(r);
      if (repository && /^[a-f0-9]{40}$/i.test(r.commit || '')) n.append(link(r.commit.slice(0, 7), repository + '/commit/' + r.commit));
      else n.append(e('span', '', r.version));
      for (const number of r.pulls || []) if (repository && Number.isSafeInteger(number) && number > 0 && (proposalLink || !r.proposed || number !== r.pr))
        n.append(link((r.proposed && number === r.pr ? '◇ ' : '') + 'PR #' + number, repository + '/pull/' + number));
      if (proposalLink && r.proposed && repository && r.pr && !r.pulls?.includes(r.pr)) n.append(link('◇ PR #' + r.pr, repository + '/pull/' + r.pr));
      return n;
    }
    function pinButton(r) {
      const pinned = state.pins.includes(r.id), n = button(pinned ? 'Pinned' : 'Pin', 'listPin', r.id, 'explorer-quiet list-pin-button');
      n.setAttribute('aria-label', (pinned ? 'Unpin ' : 'Pin ') + label(r)); n.setAttribute('aria-pressed', String(pinned)); return n;
    }
    function evidence(values, row) {
      const details = e('details', 'list-evidence'), caseKey = row ? key(row.item) : 'pins';
      const unavailable = row ? model.unavailable.filter(r => r.benchmark === row.item.benchmark_id && r.case === row.item.case_id) : [];
      const invalid = values.filter(r => !numeric(r));
      details.dataset.listEvidence = caseKey; details.open = openEvidence.has(caseKey);
      details.append(e('summary', '', 'Evidence' + (invalid.length ? ' · ' + invalid.length + ' unqualified' : '') + (unavailable.length ? ' · ' + unavailable.length + ' unavailable' : '')));
      if (row && evidenceRequested) { details.open = true; openEvidence.add(caseKey); evidenceRequested = false; }
      const reports = new Map(); for (const r of [...values, ...unavailable]) if (path(r.report) && !reports.has(r.report)) reports.set(r.report, r);
      const reportLinks = e('div', 'list-evidence-reports');
      const failureNote = r => e('p', 'list-meta', `${r.engine || r.task || 'Capture'} · ${r.status}: ${r.reason || 'No successful measurement'}`);
      for (const r of reports.values()) {
        const source = e('section', 'list-evidence-report'), links = e('div', 'list-identity'); source.dataset.evidenceReport = r.report;
        if (reports.size > 1) links.append(e('span', '', r.runId ? 'Run ' + r.runId + '/' + r.runAttempt : r.snapshot || r.snapshot_id));
        links.append(link('Report', path(r.report)), link('Analysis JSON', path(r.report.replace(/index\.html$/, 'analysis.json'))));
        if (r.runId && sourceURL(r)) links.append(link('Actions run', sourceURL(r) + '/actions/runs/' + r.runId + '/attempts/' + r.runAttempt));
        source.append(links);
        for (const missing of unavailable.filter(missing => missing.report === r.report)) source.append(failureNote(missing));
        reportLinks.append(source);
      }
      details.append(reportLinks);
      for (const rows of evidenceGroups(values)) {
        const first = rows[0], common = sharedFields(rows, ['wayLabel', 'createdAt', 'origin', 'phase', 'variant', 'count']);
        const qualified = rows.every(numeric), group = e('section', 'list-evidence-group');
        group.dataset.evidenceSnapshot = first.snapshot; group.dataset.evidenceSet = first.set;
        group.append(e('h3', '', first.snapshot));
        const context = e('div', 'list-case-context');
        for (const text of [common.createdAt ? date(first) + ' UTC' : '', common.wayLabel, common.origin, common.phase,
          common.variant && common.variant.toLowerCase() !== common.wayLabel?.toLowerCase() ? 'Variant: ' + common.variant : '',
          Object.hasOwn(common, 'count') ? common.count + ' samples per task' : '', qualified ? 'All captures qualified' : ''])
          if (text) context.append(e('span', '', text));
        group.append(context);
        const links = e('div', 'list-identity list-evidence-links');
        for (const [kind, href] of Object.entries(first.evidence)) links.append(link({snapshot: 'Sealed snapshot', suite: 'Suite', hashes: 'Checksums'}[kind] || kind, path(href)));
        group.append(links);
        const wrap = e('div', 'list-table-wrap'), table = e('table', 'list-evidence-table'), head = e('tr');
        table.dataset.allQualified = String(qualified);
        for (const text of ['Tool / source', rows.some(r => observedRange(r)) ? 'Median / observed range' : 'Median', qualified ? 'Pin' : 'Qualification / pin']) head.append(e('th', '', text));
        const thead = e('thead'); thead.append(head); table.append(thead); const body = e('tbody');
        for (const r of rows) {
          const tr = e('tr'), a = e('td'), b = e('td'), c = e('td'); tr.dataset.evidenceRecord = r.id;
          a.append(e('div', '', r.toolLabel + (common.wayLabel ? '' : ' / ' + r.wayLabel)), compactIdentity(r));
          const notes = [];
          for (const field of ['phase', 'variant', 'origin']) if (!Object.hasOwn(common, field) && r[field]) notes.push(r[field]);
          if (!Object.hasOwn(common, 'createdAt')) notes.push(date(r));
          if (notes.length) a.append(e('div', 'list-row-note', notes.join(' · ')));
          b.append(e('div', '', format(r.median, r.unit)));
          if (observedRange(r)) b.append(e('div', 'list-row-note', observedRange(r)));
          if (!Object.hasOwn(common, 'count')) b.append(e('div', 'list-row-note', r.count + ' samples'));
          if (!qualified) c.append(e('div', 'list-meta', numeric(r) ? 'Qualified' : r.reason || 'Unqualified'));
          if (!r.historyQualified && r.historyReason) c.append(e('div', 'list-row-note', r.historyReason));
          c.append(pinButton(r)); tr.append(a, b, c); body.append(tr);
        }
        table.append(body); wrap.append(table); group.append(wrap); details.append(group);
      }
      if (row) {
        for (const r of unavailable.filter(r => !reports.has(r.report))) details.append(failureNote(r));
        const metadata = e('details', 'list-metadata'); metadata.append(e('summary', '', 'Workload and matching capture metadata'));
        metadata.append(e('pre', '', JSON.stringify({workload: row.item.metadata, captures: row.contexts.filter(Boolean).map(c => ({id: c.id, metadata: c.metadata, implementation: c.implementation}))}, null, 2)));
        details.append(metadata);
      }
      return details;
    }
    function comparePanel(host, values, reference, kind, referenceKey, draws, allowedReferences = null) {
      viewFeatures[kind] = true;
      const ref = lookup.get(reference), refs = allowedReferences || values.filter(r => numeric(r) && (kind !== 'paired' || r.median > 0));
      const effective = ref && refs.some(r => r.id === ref.id) ? ref : refs[0];
      const results = values.map(r => ({r, pair: kind === 'paired' && effective ? paired(model.comparisons, effective, r) : null}));
      const domain = results.filter(({r}) => numeric(r)).map(({r, pair}) => kind === 'paired' ? pair?.ratio : r.median).filter(finite);
      if (kind === 'paired') domain.push(1);
      const list = e('div', 'list-comparison');
      for (const {r, pair} of results) {
        const row = e('div', 'list-comparison-row'); row.dataset.displayedRecord = r.id;
        const active = pair?.status === 'reference', canReference = kind === 'paired' && refs.some(ref => ref.id === r.id);
        if (canReference) { row.dataset.listReferenceRow = r.id; row.dataset.referenceKey = referenceKey; }
        if (active) row.classList.add('list-is-reference');
        const tool = r.toolLabel + (values.every(a => a.wayLabel === values[0].wayLabel) ? '' : ' / ' + r.wayLabel);
        const name = e('div', 'list-row-name'), title = canReference ? button(tool, 'listUseReference', r.id, 'explorer-quiet list-reference-choice') : e('span', 'list-record-label', tool);
        if (canReference) {
          title.dataset.referenceKey = referenceKey; title.setAttribute('aria-pressed', String(active));
          title.setAttribute('aria-label', 'Use ' + label(r) + ' as 1× reference');
        }
        name.append(title, compactIdentity(r));
        if (!values.every(a => date(a) === date(values[0]))) name.append(e('span', 'list-row-note', date(r)));
        if (active) name.append(e('span', 'list-row-note', 'Reference'));
        else if (r.phase && r.variant) name.append(e('span', 'list-row-note', r.variant === 'baseline' ? 'paired base' : r.variant));
        const value = kind === 'paired' ? pair?.ratio : r.median, plot = e('div', 'list-inline-plot');
        const ratio = e('div', 'list-row-ratio', kind === 'paired' && finite(value) ? multiplier(value) : '');
        if (kind === 'paired' && finite(value) && !active) ratio.dataset.rank = (r.direction === 'higher' ? value > 1 : value < 1) ? 'better' : value === 1 ? 'same' : 'worse';
        const median = e('div', 'list-row-median', format(r.median, r.unit));
        if (kind !== 'paired') median.classList.add('list-absolute-value');
        row.append(name, plot, ratio, median, pinButton(r));
        if (kind === 'paired' && !finite(value)) row.append(e('p', 'list-row-warning', pair?.reason || 'No recorded qualified pairing'));
        else if (!numeric(r)) row.append(e('p', 'list-row-warning', r.reason || 'Unqualified measurement'));
        list.append(row); draws.push(() => chart(plot, box => drawInlineComparison(box, numeric(r) ? value : null, domain, kind)));
      }
      host.append(list);
      if (kind === 'absolute') host.append(e('p', 'list-chart-caption', 'Independent captures · no paired multiplier'));
    }
    function historyPanels(host, values, row, metricKey, draws) {
      viewFeatures.history = true;
      const histories = new Map();
      for (const r of values.filter(r => r.history)) { const k = implementation(r); if (!histories.has(k)) histories.set(k, []); histories.get(k).push(r); }
      if (!histories.size) { host.append(e('p', 'list-meta', 'No candidate discovery history for this measurement. Baselines and confirmation remain in Compare and Evidence.')); return; }
      const grid = e('div', 'list-histories');
      for (const [impl, captures] of histories) {
        const rows = ordered(captures), selectionKey = key(row.item) + metricKey + impl;
        const selected = rows.find(r => r.id === state.choices[selectionKey]) || rows.at(-1), first = rows[0];
        const panel = e('section', 'list-history'), heading = e('div', 'list-history-name');
        panel.dataset.selectedRecord = selected.id;
        const title = first.toolLabel + (values.every(r => r.wayLabel === first.wayLabel) ? '' : ' / ' + first.wayLabel) +
          (values.some(r => r.tool === first.tool && r.way === first.way && r.capture !== first.capture) ? ' · ' + first.capture : '');
        heading.append(e('h3', '', title));
        if (!coveredProposal(selected)) heading.append(compactIdentity(selected, false));
        if (captures.some(r => r.proposed)) viewFeatures.proposal = true;
        if (segments(rows, model.unavailable, records).runners.length) viewFeatures.runners = true;
        const plot = e('div', 'list-history-plot');
        const value = e('div', 'list-history-value');
        value.append(e('div', 'list-current-value', format(selected.median, selected.unit)));
        if (!coveredProposal(selected) || date(selected) !== proposalDate) value.append(e('span', 'list-row-note', date(selected)));
        if (!selected.historyQualified) value.append(e('span', 'list-row-warning', selected.historyReason || 'Unqualified history point'));
        panel.append(heading, plot, value, pinButton(selected));
        if (model.unavailable.some(a => a.benchmark === first.benchmark && a.case === first.case && a.engine === first.tool && stamp(a) >= stamp(rows.at(-1))))
          panel.append(e('p', 'list-row-warning', 'Later capture unavailable · showing an earlier measurement'));
        grid.append(panel); draws.push(() => chart(plot, box => drawHistory(box, rows, selected.id, true)));
      }
      host.append(grid);
    }
    function casePanel(row, draws) {
      const item = row.item, caseKey = key(item), values = matchingRecords(records, row), panel = e('article', 'list-case');
      panel.dataset.listCase = caseKey; panel.dataset.benchmark = item.benchmark_id; panel.dataset.case = item.case_id || '';
      const heading = e('div', 'list-case-heading'), title = e('h2', '', item.benchmark_title);
      heading.append(title); const tags = e('div', 'list-tags');
      for (const tag of new Set([...(item.tags || []), ...(Array.isArray(item.metadata?.topic) ? item.metadata.topic : [])])) tags.append(button('#' + tag, 'listTag', tag, 'explorer-quiet'));
      if (item.case_id !== item.benchmark_title) title.append(e('span', 'list-workload', item.case_id || 'Workload not declared'));
      panel.append(heading);
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
      const context = e('div', 'list-case-context');
      context.append(e('span', '', metricTitle(first)), e('span', '', (first.direction === 'higher' ? 'Higher' : 'Lower') + ' is better'));
      if (current.every(r => r.wayLabel === first.wayLabel)) context.append(e('span', '', first.wayLabel));
      panel.append(context);
      const optionsPanel = e('details', 'list-options'); optionsPanel.dataset.listOptions = caseKey; optionsPanel.open = openOptions.has(caseKey);
      optionsPanel.append(e('summary', '', 'Options'));
      if (metrics.length > 1) optionsPanel.append(select('Measurement', 'listMetric', caseKey, metrics.map(([k, r]) => [k, r.metricLabel + ' · ' + r.unit + (metrics.some(([other, v]) => other !== k && v.metric === r.metric) ? ' · ' + r.scope + ' · ' + r.contract : '')]), metricKey));
      optionsPanel.append(e('p', 'list-meta', first.scope), e('p', 'list-meta', 'Contract: ' + first.contract), tags);
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
        optionsPanel.append(select('Recorded comparison set', 'listSet', setKey, [...options.map(s => [s.id, s.label]), ['independent', 'Latest available · independent captures']], setId));
        const capture = current.find(r => r.set === setId);
        context.append(e('span', '', setId === 'independent' ? 'Latest available' : date(capture) + (selected.phase ? ' · ' + selected.phase : '')));
        let displayed = current.filter(r => r.set === setId);
        if (setId === 'independent') {
          const latest = new Map(); for (const r of ordered(current.filter(r => r.history))) latest.set(implementation(r), r); displayed = [...latest.values()];
        }
        const refKey = setKey + setId;
        const reference = displayed.find(r => r.id === state.references[refKey]) || displayed.find(legacyReferenceMatches) || displayed.find(r => r.variant === 'baseline') || displayed.find(numeric) || displayed[0];
        if (legacyRef.length && reference && legacyReferenceMatches(reference)) state.references[refKey] = reference.id;
        if (legacyRef.length && !displayed.some(legacyReferenceMatches)) panel.append(e('p', 'list-meta', 'The requested reference is unavailable in this selection. Choose a retained reference.'));
        const kind = setId === 'independent' ? 'absolute' : 'paired';
        const eligible = displayed.filter(r => numeric(r) && (kind !== 'paired' || r.median > 0));
        const effective = eligible.find(r => r.id === reference?.id) || eligible[0];
        const identity = comparisonKey(displayed, effective?.id, kind), duplicate = identity === pinnedComparison;
        if (duplicate) {
          const notice = e('div', 'list-duplicate'); notice.append(link('Shown in pinned comparison ↑', '#list-pins'),
            button(expandedComparisons.has(identity) ? 'Hide here' : 'Show here', 'listExpandComparison', identity, 'explorer-quiet'));
          if (expandedComparisons.has(identity)) panel.append(notice);
          else { panel.classList.add('list-case-pinned'); context.remove(); heading.append(notice); }
        }
        if (!duplicate || expandedComparisons.has(identity)) comparePanel(panel, displayed, effective?.id, kind, refKey, draws);
      }
      const footer = e('div', 'list-case-footer'); footer.append(optionsPanel, evidence(current, row)); panel.append(footer); return panel;
    }
    function pinDetails(r, draws, shared) {
      const options = e('div', 'list-pin-details'); options.hidden = !openOptions.has('pin:' + r.id);
      options.id = 'pin-details-' + (r.shortId || state.pins.indexOf(r.id));
      const captured = [];
      if (!shared.createdAt) captured.push('Captured ' + (Number.isFinite(stamp(r)) ? r.createdAt : 'date unavailable'));
      if (!shared.origin && r.origin) captured.push(r.origin);
      if (captured.length) options.append(e('p', 'list-meta', captured.join(' · ')));
      if (!shared.qualified || !shared.count) options.append(e('p', 'list-meta', (numeric(r) ? 'Qualified' : r.reason || 'Unqualified') +
        (shared.count ? '' : ' · ' + r.count + ' samples')));
      if (!r.historyQualified && r.historyReason) options.append(e('p', 'list-meta', r.historyReason));
      const repository = sourceURL(r), links = e('div', 'list-identity');
      if (repository && r.pr && !r.pulls?.includes(r.pr)) links.append(link('Capture PR #' + r.pr, repository + '/pull/' + r.pr));
      if (repository && r.runId) links.append(link('Run ' + r.runId + '/' + r.runAttempt, repository + '/actions/runs/' + r.runId + '/attempts/' + r.runAttempt));
      if (!shared.report) links.append(link('Report and evidence', path(r.report)));
      if (links.childNodes.length) options.append(links);
      const available = records.filter(a => key(a) === key(r) && a.tool === r.tool && a.way === r.way && a.repository === r.repository);
      const metrics = [...new Map(available.map(a => [measurement(a), a])).entries()];
      const draft = pinEditors.get(r.id) || {metric: measurement(r), capture: r.id};
      const captures = ordered(available.filter(a => measurement(a) === draft.metric));
      const selected = captures.find(a => a.id === draft.capture) || captures.at(-1);
      if (metrics.length > 1) options.append(select('Measurement to add', 'listPinMetric', r.id, metrics.map(([mk, a]) => [mk, a.metricLabel + ' · ' + a.unit + ' · ' + a.scope + ' · ' + a.contract]), draft.metric));
      if (captures.length > 1) options.append(select('Capture to add', 'listPinCapture', r.id, captures.map(a => [a.id,
        a.version + ' · ' + date(a) + (a.proposed ? ' · Proposed PR #' + a.pr : '') + (a.phase ? ' · ' + a.phase + ' / ' + a.variant : '') + ' · ' + format(a.median, a.unit)]), selected.id));
      if (selected && !state.pins.includes(selected.id)) {
        if (captures.length === 1) {
          const preview = [];
          if (selected.version !== r.version) preview.push(selected.version);
          if (date(selected) !== date(r)) preview.push(date(selected));
          preview.push(format(selected.median, selected.unit)); options.append(e('p', 'list-meta list-capture-preview', preview.join(' · ')));
        }
        options.append(pinButton(selected));
      }
      const history = captures.filter(a => a.history && implementation(a) === implementation(selected || r));
      if (history.length > 1) {
        const plot = e('div', 'list-detail-history'); options.append(plot);
        if (!options.hidden) {
          viewFeatures.history = true;
          if (history.some(r => r.proposed)) viewFeatures.proposal = true;
          if (segments(history, model.unavailable, records).runners.length) viewFeatures.runners = true;
        }
        draws.push(() => chart(plot, box => drawHistory(box, history, selected?.id)));
      }
      options.append(button('Show recorded comparison', 'listShowSet', r.id, 'explorer-quiet'));
      return options;
    }
    function pinGroup(values, draws) {
      const first = values[0], group = e('section', 'list-pin-group');
      const same = field => values.every(r => r[field] === first[field]);
      const shared = Object.fromEntries(['wayLabel', 'count', 'report', 'createdAt', 'origin', 'phase', 'variant'].map(field => [field, same(field)]));
      shared.date = values.every(r => date(r) === date(first)); shared.qualified = values.every(numeric);
      group.dataset.pinMeasurement = measurement(first);
      const context = e('div', 'list-case-context');
      for (const text of [metricTitle(first), shared.wayLabel ? first.wayLabel : '', shared.date ? date(first) : '',
        shared.phase && first.phase ? first.phase + (shared.variant && first.variant ? ' / ' + first.variant : '') : ''])
        if (text) context.append(e('span', '', text));
      const headings = e('div', 'list-pin-columns'); headings.append(context, e('span', 'list-row-note',
        values.some(r => numeric(r) && r.min !== r.max) ? 'Median / observed range' : 'Median')); group.append(headings);
      for (const r of values) {
        const row = e('article', 'list-pin-row'); row.dataset.pinnedRecord = r.id;
        const choose = e('label', 'list-pin-check'), check = e('input');
        check.type = 'checkbox'; check.checked = state.selected.includes(r.id); check.dataset.listSelected = r.id; check.dataset.listControl = 'listSelected:' + r.id;
        check.setAttribute('aria-label', 'Include ' + label(r) + ' in comparison'); choose.append(check);
        const identity = e('div', 'list-pin-row-identity');
        identity.append(e('span', 'list-record-label', r.toolLabel + (shared.wayLabel ? '' : ' / ' + r.wayLabel)), compactIdentity(r));
        const notes = [];
        if (!shared.date) notes.push(date(r));
        if (!shared.phase && r.phase) notes.push(r.phase);
        if (!shared.variant && r.variant) notes.push(r.variant === 'baseline' ? 'paired base' : r.variant);
        if (values.some(a => a.tool === r.tool && a.way === r.way && a.capture !== r.capture)) notes.push(r.capture);
        if (notes.length) identity.append(e('span', 'list-row-note', notes.join(' · ')));
        const value = e('div', 'list-pin-row-value'); value.append(e('div', 'list-current-value', format(r.median, r.unit)));
        if (observedRange(r)) value.append(e('span', 'list-row-note', observedRange(r)));
        else if (!numeric(r)) identity.append(e('span', 'list-row-warning', r.reason || 'Unqualified or incomplete measurement'));
        const details = pinDetails(r, draws, shared), toggle = button('Details', 'listPinDetails', r.id, 'explorer-quiet list-pin-detail-toggle');
        toggle.setAttribute('aria-expanded', String(!details.hidden)); toggle.setAttribute('aria-controls', details.id);
        const remove = button('×', 'listPin', r.id, 'explorer-quiet list-unpin'); remove.setAttribute('aria-label', 'Unpin ' + label(r));
        row.append(choose, identity, value, toggle, remove, details); group.append(row);
      }
      const details = e('details', 'list-options'); details.dataset.listOptions = 'group:' + key(first) + measurement(first);
      details.open = openOptions.has(details.dataset.listOptions); details.append(e('summary', '', 'Measurement and evidence'));
      details.append(e('p', 'list-meta', first.scope), e('p', 'list-meta', 'Contract: ' + first.contract + ' · ' +
        (first.direction === 'higher' ? 'Higher' : 'Lower') + ' is better' + (shared.qualified ? ' · Qualified' : '') + (shared.count ? ' · ' + first.count + ' samples per capture' : '') +
        '.'));
      const captured = [];
      if (shared.createdAt && first.createdAt) captured.push('Captured ' + first.createdAt);
      if (shared.origin && first.origin) captured.push(first.origin);
      if (captured.length) details.append(e('p', 'list-meta', captured.join(' · ')));
      if (shared.report) details.append(link('Report and evidence', path(first.report)));
      group.append(details); return group;
    }
    function renderPins(draws) {
      board.hidden = !state.pins.length && !state.error;
      const jump = byId('list-pin-jump'); jump.hidden = board.hidden;
      jump.textContent = state.pins.length ? 'Pinned (' + state.pins.length + ') ↑' : 'Pin issue ↑';
      if (board.hidden) { board.replaceChildren(); return; }
      board.replaceChildren(); const selected = state.pins.filter(id => state.selected.includes(id)).map(id => lookup.get(id));
      const ready = readiness(selected, model.comparisons); if (!ready.ready) state.compare = false;
      const heading = e('div', 'list-pin-heading'), actions = e('div', 'list-pin-actions');
      heading.append(e('h2', '', state.pins.length ? 'Pinned measurements (' + state.pins.length + ')' : 'Pinned measurements'));
      if (state.pins.length > 1 && selected.length !== state.pins.length) actions.append(e('span', 'list-meta', selected.length + ' of ' + state.pins.length + ' selected'));
      const compare = button(state.compare ? 'Back to separate panels' : 'Compare selection', 'listComparePins', 'true', 'explorer-button explorer-primary');
      compare.disabled = !state.compare && !ready.ready; if (state.pins.length > 1) actions.append(compare); heading.append(actions); board.append(heading);
      if (state.error || state.pins.length > 1 && !ready.ready) board.append(e('p', 'list-pin-status', state.error || ready.message));
      if (state.compare) {
        const ref = ready.references.find(r => r.id === state.pinReference) || ready.references.find(r => r.variant === 'baseline') || ready.references[0];
        pinnedComparison = comparisonKey(selected, ref?.id, ready.kind);
        board.append(e('h3', '', workloadTitle(selected[0])));
        const common = sharedFields(selected, ['wayLabel']), context = e('div', 'list-case-context');
        for (const text of [metricTitle(selected[0]), (selected[0].direction === 'higher' ? 'Higher' : 'Lower') + ' is better', common.wayLabel,
          selected.every(r => date(r) === date(selected[0])) ? date(selected[0]) : '']) if (text) context.append(e('span', '', text));
        board.append(context);
        comparePanel(board, selected, ref?.id, ready.kind, 'pins', draws, ready.references); board.append(evidence(selected));
      } else {
        const groups = new Map();
        for (const id of state.pins) {
          const r = lookup.get(id), workload = key(r), metric = measurement(r);
          if (!groups.has(workload)) groups.set(workload, new Map());
          const metrics = groups.get(workload); if (!metrics.has(metric)) metrics.set(metric, []); metrics.get(metric).push(r);
        }
        for (const metrics of groups.values()) {
          const workload = e('section', 'list-pin-workload'); workload.append(e('h3', '', workloadTitle(metrics.values().next().value[0])));
          for (const values of metrics.values()) workload.append(pinGroup(values, draws)); board.append(workload);
        }
      }
    }
    function renderHelp() {
      const help = byId('list-help'), content = byId('list-help-content'); content.replaceChildren();
      help.hidden = !Object.keys(viewFeatures).length;
      if (viewFeatures.history) content.append(e('p', 'list-meta', 'Each tool has its own capture order and scale. Select a point to choose its capture.'));
      if (viewFeatures.paired) content.append(e('p', 'list-meta', 'Paired charts show retained multipliers on a logarithmic scale. Select a tool or point to set the 1× reference.'));
      if (viewFeatures.absolute) content.append(e('p', 'list-meta', 'Independent captures show absolute values and have no paired multiplier.'));
      const legend = [];
      if (viewFeatures.proposal) legend.push('◇ Proposed PR');
      if (viewFeatures.runners) legend.push('Dotted connector: runner changed');
      if (legend.length) content.append(e('p', 'list-meta', legend.join(' · ')));
      content.append(e('p', 'list-meta', 'Ranges show observed min–max, not confidence intervals. Capture dates are not commit dates.'));
    }
    function scope(rows) {
      return rows.filter(row => (!state.benchmark || row.item.benchmark_id === state.benchmark) && (!state.case || row.item.case_id === state.case) &&
        (!state.tag || [...(row.item.tags || []), ...(Array.isArray(row.item.metadata?.topic) ? row.item.metadata.topic : []), ...row.contexts.flatMap(c => Array.isArray(c?.metadata?.topic) ? c.metadata.topic : [])].includes(state.tag)));
    }
    function render(rows, groups) {
      // Toggle events are queued by the browser. Read the current disclosure state
      // before a fast select/change can rebuild it ahead of the queued event.
      for (const n of root.querySelectorAll('[data-list-options]')) {
        if (n.open) openOptions.add(n.dataset.listOptions); else openOptions.delete(n.dataset.listOptions);
      }
      const activeNode = document.activeElement, active = activeNode?.dataset.listControl;
      const activeCase = activeNode?.closest('[data-list-case]')?.dataset.listCase;
      const activeEvidence = activeNode?.closest('[data-list-evidence]')?.dataset.listEvidence;
      const activeBoard = Boolean(activeNode?.closest('#list-pins'));
      for (const plot of plots) { observer?.unobserve(plot); resize?.unobserve(plot); } plots = new Set();
      const draws = []; pinnedComparison = ''; viewFeatures = {}; renderPins(draws);
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
      renderHelp(); draws.forEach(draw => draw());
      if (active) {
        const replacement = [...root.querySelectorAll('[data-list-control]')].find(node => node.dataset.listControl === active &&
          node.closest('[data-list-case]')?.dataset.listCase === activeCase &&
          node.closest('[data-list-evidence]')?.dataset.listEvidence === activeEvidence &&
          Boolean(node.closest('#list-pins')) === activeBoard);
        const collapsed = activeCase && [...root.querySelectorAll('.list-case-pinned')].find(node => node.dataset.listCase === activeCase)?.querySelector('.list-duplicate a');
        (replacement || collapsed)?.focus({preventScroll: true});
      }
    }
    root.addEventListener('toggle', event => {
      const id = event.target.dataset?.listEvidence; if (id) { if (event.target.open) openEvidence.add(id); else openEvidence.delete(id); }
      const options = event.target.dataset?.listOptions; if (options) { if (event.target.open) openOptions.add(options); else openOptions.delete(options); }
    }, true);
    root.addEventListener('click', event => {
      const target = event.target.closest('button, a, [data-list-record], [data-list-reference-row]'); if (!target || target.localName === 'a') return;
      const d = target.dataset; let changed = true;
      if (d.listView) state.view = d.listView;
      else if (d.listTag) { state.tag = d.listTag; state.benchmark = state.case = ''; }
      else if (d.listClearScope) { state[d.listClearScope] = ''; if (d.listClearScope === 'benchmark') state.case = ''; }
      else if (d.listPin && lookup.has(d.listPin)) {
        if (state.pins.includes(d.listPin)) { state.pins = state.pins.filter(id => id !== d.listPin); state.selected = state.selected.filter(id => id !== d.listPin); }
        else if (state.pins.length < 100) { state.pins.push(d.listPin); state.selected.push(d.listPin); }
        state.compare = false;
      } else if (d.listComparePins) state.compare = !state.compare;
      else if (d.listExpandComparison) {
        if (expandedComparisons.has(d.listExpandComparison)) expandedComparisons.delete(d.listExpandComparison); else expandedComparisons.add(d.listExpandComparison);
      }
      else if (d.listPinDetails) {
        const id = 'pin:' + d.listPinDetails; if (openOptions.has(id)) openOptions.delete(id); else openOptions.add(id);
      }
      else if (d.listUseReference || d.listReferenceRow) {
        const id = d.listUseReference || d.listReferenceRow, referenceKey = d.referenceKey;
        if (referenceKey === 'pins') state.pinReference = id;
        else state.references[referenceKey] = id;
      }
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
      else if (d.listPinMetric || d.listPinCapture) {
        const id = d.listPinMetric || d.listPinCapture, r = lookup.get(id);
        const draft = pinEditors.get(id) || {metric: measurement(r), capture: id};
        if (d.listPinMetric) { draft.metric = t.value; draft.capture = ''; } else draft.capture = t.value;
        pinEditors.set(id, draft);
      }
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
  return {key, measurement, implementation, hydrate, numeric, ordered, segments, paired, readiness, matchingRecords, format,
    sharedFields, evidenceGroups, comparisonKey, observedRange, metricTitle, decode, encode, create};
})();
if (typeof module !== 'undefined' && module.exports) module.exports = GammabenchList;
