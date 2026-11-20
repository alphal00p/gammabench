// All displayed estimates and verdicts come from retained CI evidence. Discovery
// drives the history chart; independent confirmation stays in the run details.
(() => {
  'use strict';
  const root = document.getElementById('performance-tracker');
  if (!root) return;
  const page = JSON.parse(root.dataset.model);
  const metricLabels = JSON.parse(root.dataset.metricLabels);
  const element = id => document.getElementById('tracker-' + id);
  const params = new URLSearchParams(location.search);
  // Existing history links may encode an entire pinned explorer configuration.
  const oldFields = ['benchmark', 'case', 'study', 'tab', 'tag', 'list', 'metric', 'unit', 'scope', 'direction',
    'contract', 'set', 'reference', 'filters', 'q', 'group'];
  if (oldFields.some(k => params.has(k)) || params.get('view')?.startsWith('[')) {
    const url = new URL(page.explorer, location.href);
    for (const [key, value] of params) url.searchParams.set(key, value);
    url.hash = location.hash;
    if (url.pathname !== location.pathname) {
      location.replace(url.href);
      return;
    }
  }
  const runs = page.runs;
  if (!runs.length) return;
  const statuses = {
    correctness: 'Correctness failure', regression: 'Regression', failed: 'Failed',
    inconclusive: 'Inconclusive', passed: 'Passed',
  };
  const severity = status => Object.keys(statuses).indexOf(status);
  const short = sha => sha.slice(0, 8);
  const timestamp = run => (run.captured_at || '').slice(0, 16).replace('T', ' ') || 'Unavailable';
  const runLabel = run => short(run.sha) + ' · ' + timestamp(run) + ' · ' + run.id;
  const humanCase = id => id.split('-').map(part =>
    /^(gl\d+|[234]d|q\d+|cff|ltd)$/i.test(part) ? part.toUpperCase() : part).join(' ');
  const format = value => Number(value.toPrecision(3)).toLocaleString('en', {maximumSignificantDigits: 3});
  const seconds = v => Number.isFinite(v) ? format(v) + ' s' : '—';
  const percent = v => Number.isFinite(v) ? (v < 0 ? '−' : v > 0 ? '+' : '') + Math.abs(v * 100).toFixed(2) + '%' : '—';
  const decision = c => c?.confirmation || c?.discovery;
  const caseAt = (run, key) => run.cases.find(c => c.id === key);
  const workloads = Array.from(new Set(runs.flatMap(r => r.cases.map(c => c.id)))).sort();
  const compareCases = (a, b) => severity(a.status) - severity(b.status) || a.id.localeCompare(b.id);
  function node(tag, text, className) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  }
  function statusMark(status) {
    const label = node('span', statuses[status] || 'Unavailable', 'tracker-status');
    label.dataset.status = status;
    return label;
  }
  function button(text, key, value) {
    const el = node('button', text, 'tracker-link');
    el.type = 'button';
    el.dataset[key] = value;
    return el;
  }
  function cell(row, value, className) {
    const td = row.insertCell();
    if (className) td.className = className;
    td.append(value);
  }
  function evidenceLink(text, href) {
    const link = node('a', text);
    link.href = href;
    return link;
  }
  const state = {};
  function restore() {
    const query = new URLSearchParams(location.search);
    const selected = [query.get('run'), page.selected_run].map(id => runs.find(r => r.id === id)).find(Boolean) || runs.at(-1);
    state.run = selected.id;
    const requested = query.get('workload');
    // Open the most relevant workload in the selected run, or a known workload
    // if that run failed before producing case measurements.
    state.workload = workloads.includes(requested) ? requested :
      selected.cases.slice().sort(compareCases)[0]?.id || workloads[0];
    const view = query.get('view') || (requested || query.has('display') ? 'history' : page.initial_view);
    state.view = view === 'history' && workloads.length ? 'history' : 'suite';
    state.mode = query.get('display') === 'delta' ? 'delta' : 'seconds';
  }
  function viewUrl(view, workload) {
    const url = new URL(location.href);
    url.searchParams.set('view', view);
    url.searchParams.set('run', state.run);
    url.searchParams.delete('workload');
    url.searchParams.delete('display');
    if (view === 'history') {
      url.searchParams.set('workload', workload);
      if (state.mode === 'delta') url.searchParams.set('display', state.mode);
    }
    return url;
  }
  function counts(run) {
    const labels = document.createDocumentFragment();
    for (const key of Object.keys(statuses)) {
      const n = run.cases.filter(c => c.status === key).length;
      if (!n) continue;
      const label = statuses[key].toLowerCase();
      const text = n + ' ' + label + (n > 1 && ['correctness','regression'].includes(key) ? 's' : '');
      if (labels.hasChildNodes()) labels.append(' · ');
      labels.append(key === 'passed' ? text : node('strong', text));
    }
    return labels;
  }
  function flaggedMetrics(c) {
    return Object.entries(c.flagged_metrics || c.nonpassing_metrics || {}).map(([name, metric]) => {
      const label = (metricLabels[name] || name).replaceAll('_', ' ');
      if (typeof metric === 'string') return label + ' (' + (statuses[metric] || metric).toLowerCase() + ')';
      const delta = Number.isFinite(metric.delta) ? ' ' + percent(metric.delta) : '';
      const phase = metric.phase ? ' in ' + metric.phase : '';
      return label + delta + phase + ' (' + (statuses[metric.status] || metric.status).toLowerCase() + ')';
    });
  }
  function drawHistory(rows, current) {
    const box = element('chart');
    const axis = element('axis');
    axis.textContent = 'Runs in capture order (UTC). Dotted connections indicate a changed or unknown runner.';
    const width = box.clientWidth, height = box.clientHeight;
    if (!width || !height) return;
    const change = state.mode === 'delta';
    const series = change ? [{key: 'delta', name: 'Paired Δ', primary: true}] : [
      {key: 'baseline', name: current?.baseline_label || 'Baseline', primary: false},
      {key: 'candidate', name: current?.candidate_label || 'Candidate', primary: true},
    ];
    const values = rows.flatMap(({c}) => series.map(s => c?.discovery?.[s.key])).filter(Number.isFinite);
    box.replaceChildren();
    if (!values.length) {
      box.append(node('p', 'No qualified discovery measurements for this workload.', 'tracker-note'));
      return;
    }
    const svgNode = (tag, attrs, text) => {
      const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [key, val] of Object.entries(attrs)) el.setAttribute(key, val);
      if (text !== undefined) el.textContent = text;
      return el;
    };
    const svg = svgNode('svg', {role: 'img', viewBox: `0 0 ${width} ${height}`,
      'aria-label': humanCase(state.workload) + ', generation ' + (change ? 'change in percent' : 'time in seconds') + ' by run in capture order'});
    box.append(svg);
    const label = change ? percent : seconds;
    const min = Math.min(...values), max = Math.max(...values);
    const pad = (max - min || Math.abs(max) || 0.01) * .15;
    const low = change ? min - pad : Math.max(0, min - pad), high = max + pad;
    const top = 17, bottom = height - 32;
    const ticks = [low, (high + low) / 2, high];
    const probe = svgNode('text', {visibility: 'hidden'});
    svg.append(probe);
    const measure = text => { probe.textContent = text; return probe.getComputedTextLength(); };
    const left = Math.max(40, ...ticks.map(v => measure(label(v)) + 8));
    const ends = series.map(s => {
      const last = rows.at(-1)?.c?.discovery?.[s.key];
      return Number.isFinite(last) ? s.name + ' ' + label(last) : '';
    });
    // Wrap end labels below the plot when unusually long mode names leave
    // too little room for the actual history on a narrow screen.
    const labelWidth = Math.max(0, ...ends.map(measure)) + 20;
    const separateLabels = width - left - labelWidth < 110;
    const right = width - (separateLabels ? 14 : Math.max(38, labelWidth));
    const x = i => rows.length < 2 ? (left + right) / 2 : left + i * (right - left) / (rows.length - 1);
    const y = v => bottom - (v - low) / (high - low) * (bottom - top);
    function line(x1, y1, x2, y2, attrs) {
      svg.append(svgNode('line', {x1, y1, x2, y2, ...attrs}));
    }
    for (const v of ticks) {
      line(left, y(v), right, y(v), {class: 'grid'});
      svg.append(svgNode('text', {x: left - 5, y: y(v) + 4, 'text-anchor': 'end'}, label(v)));
    }
    const tickIndices = new Set([0, rows.length - 1]);
    if (rows.length > 2 && right - left > 200) tickIndices.add(Math.floor((rows.length - 1) / 2));
    const sameDay = rows.every(({run}) => run.captured_at?.slice(0, 10) === rows[0].run.captured_at?.slice(0, 10));
    for (const i of tickIndices) {
      const run = rows[i].run;
      const stamp = run.captured_at;
      const text = stamp ? (sameDay ? stamp.slice(11, 16) : stamp.slice(0, 10)) : short(run.sha);
      const halfLabel = measure(text) / 2 + 2;
      const tickX = Math.max(halfLabel, Math.min(width - halfLabel, x(i)));
      svg.append(svgNode('text', {x: tickX, y: height - 7, 'text-anchor': 'middle'}, text));
    }
    probe.remove();
    const selected = rows.findIndex(({run}) => run.id === state.run);
    if (selected >= 0 && selected < rows.length - 1)
      line(x(selected), top, x(selected), bottom, {stroke: 'var(--joint)', 'stroke-dasharray': '2 4'});
    const anchors = series.map(s => y(rows.at(-1).c?.discovery?.[s.key]));
    if (anchors.length === 2 && Math.abs(anchors[1] - anchors[0]) < 22) {
      const lower = anchors[0] <= anchors[1] ? 1 : 0;
      const mid = Math.max(top + 11, Math.min(bottom - 11, (anchors[0] + anchors[1]) / 2));
      anchors[lower] = mid + 11;
      anchors[1 - lower] = mid - 11;
    }
    series.forEach((s, n) => {
      const color = s.primary ? 'var(--link)' : 'var(--ink-muted)';
      rows.forEach(({run, c}, i) => {
        const value = c?.discovery?.[s.key];
        if (!Number.isFinite(value)) return;
        const cx = x(i), cy = y(value), prev = rows[i - 1]?.c;
        const prior = prev?.discovery?.[s.key];
        if (Number.isFinite(prior) && c.series_key && c.series_key === prev.series_key) {
          const runnerChanged = !c.runner_key || c.runner_key !== prev.runner_key;
          line(x(i - 1), y(prior), cx, cy, {stroke: color, 'stroke-width': s.primary ? 2.3 : 1.5,
            'stroke-dasharray': runnerChanged ? '2 4' : s.primary ? 'none' : '6 4'});
        }
        const point = svgNode('g', {'data-run-id': run.id});
        point.append(svgNode('title', {}, runLabel(run) + ' · ' + s.name + ' ' + label(value)));
        const latest = i === rows.length - 1;
        point.append(latest && s.primary ?
          svgNode('path', {d: `M ${cx} ${cy-6} l 6 6 -6 6 -6 -6 Z`, stroke: color, 'stroke-width': 2, fill: 'var(--canvas)'}) :
          svgNode('circle', {cx, cy, r: run.id === state.run ? 4.5 : 3.4, stroke: color, fill: s.primary ? color : 'var(--canvas)'}));
        point.append(svgNode('circle', {cx, cy, r: 8, fill: 'transparent'}));
        svg.append(point);
        if (latest && !separateLabels) {
          line(cx + 7, cy, cx + 15, anchors[n], {stroke: color});
          svg.append(svgNode('text', {x: cx + 18, y: anchors[n] + 4, class: s.primary ? 'endpoint' : ''}, ends[n]));
        }
      });
    });
    if (separateLabels && ends.some(Boolean)) axis.prepend('Latest · ' + ends.filter(Boolean).join(' · ') + '. ');
  }
  function renderHistory(run, c) {
    const rows = runs.map(run => ({run, c: caseAt(run, state.workload)}));
    const example = c || rows.findLast(row => row.c)?.c;
    element('title').textContent = humanCase(state.workload);
    const qualified = rows.filter(row => Number.isFinite(row.c?.discovery?.candidate)).length;
    element('context').textContent = qualified + ' measured of ' + rows.length + ' runs · ' +
      (example?.comparison === 'within-candidate' ? 'within-revision comparison' : 'revision comparison');
    element('measure').textContent = (state.mode === 'delta' ? 'Median paired change' : 'Discovery medians') +
      (state.mode === 'delta' ? ' during discovery' : '') + ' · lower is better';
    const selectedStatus = element('selected-status');
    selectedStatus.replaceChildren(statusMark(c?.status));
    const delta = decision(c)?.delta;
    if (Number.isFinite(delta)) selectedStatus.append(' · generation ' + percent(delta));
    drawHistory(rows, example);
    const body = element('history-rows');
    body.replaceChildren();
    for (const row of rows.slice().reverse()) {
      const tr = body.insertRow(), active = row.run.id === run.id;
      tr.classList.toggle('selected', active);
      const pick = button(short(row.run.sha), 'runId', row.run.id);
      pick.setAttribute('aria-pressed', String(active));
      pick.setAttribute('aria-label', runLabel(row.run));
      const identity = tr.insertCell();
      identity.append(pick, node('small', timestamp(row.run), 'tracker-subtext'));
      cell(tr, percent(decision(row.c)?.delta), 'tracker-number');
      cell(tr, statusMark(row.c?.status));
    }
    const detail = element('detail');
    detail.replaceChildren(node('strong', short(run.sha) + ' · '));
    if (!c) { detail.append(run.reason || 'This run has no recorded measurement for this workload.'); return; }
    const s = decision(c);
    if (s) {
      detail.append((c.confirmation ? 'Confirmation' : 'Discovery') + ' · ' +
        c.baseline_label + ' ' + seconds(s.baseline) + ' → ' + c.candidate_label + ' ' + seconds(s.candidate));
      if (Number.isInteger(s.pairs)) detail.append(' · ' + s.pairs + ' pairs');
    } else { detail.append('Generation measurements unavailable'); }
    if (Number.isInteger(c.mismatches)) detail.append(' · ' + c.mismatches + ' correctness mismatches');
    const issues = flaggedMetrics(c);
    if (issues.length) detail.append(' · ' + issues.join(', '));
    if (c.reason) detail.append(' · ' + c.reason);
    if (c.evidence) detail.append(' · ', evidenceLink('Case evidence', c.evidence));
  }
  function renderSuite(run) {
    element('title').textContent = 'Run results';
    element('total').textContent = run.cases.length + (run.cases.length === 1 ? ' workload' : ' workloads');
    const outcomes = counts(run);
    element('counts').replaceChildren(outcomes.hasChildNodes() ? outcomes : run.reason || statuses[run.status] || 'No measurements');
    const body = element('suite-rows');
    body.replaceChildren();
    for (const c of run.cases.slice().sort(compareCases)) {
      const row = body.insertRow();
      const subject = node('div');
      const link = node('a', humanCase(c.id), 'tracker-link');
      link.href = viewUrl('history', c.id);
      link.dataset.workload = c.id;
      subject.append(link);
      if (c.comparison === 'within-candidate') subject.append(node('small',
        c.baseline_label + ' → ' + c.candidate_label + ' · within revision', 'tracker-subtext'));
      cell(row, subject);
      cell(row, percent(decision(c)?.delta), 'tracker-number');
      cell(row, statusMark(c.status));
      const issues = flaggedMetrics(c);
      if (issues.length) {
        row.classList.add('has-issues');
        const detail = body.insertRow().insertCell();
        detail.colSpan = 3;
        detail.className = 'tracker-case-detail';
        detail.id = 'case-detail-' + c.id;
        row.setAttribute('aria-describedby', detail.id);
        for (const issue of issues) detail.append(node('span', issue, 'tracker-subtext'));
      }
    }
  }
  function render() {
    const run = runs.find(r => r.id === state.run);
    const isHistory = state.view === 'history';
    element('workload').value = state.workload || '';
    element('run').value = state.run;
    element('workload').parentElement.hidden = !isHistory;
    element('run').parentElement.hidden = isHistory;
    element('selected-status').hidden = !isHistory;
    element('context').hidden = !isHistory;
    element('results').hidden = isHistory;
    element('history').hidden = !isHistory;
    const suiteUrl = viewUrl('suite');
    element('back').hidden = !isHistory;
    element('back').href = suiteUrl;
    const trackerLink = document.querySelector('#bench-navigation > a[aria-current="page"]');
    if (trackerLink) trackerLink.href = suiteUrl;
    for (const b of root.querySelectorAll('[data-mode]')) b.setAttribute('aria-pressed', String(state.mode === b.dataset.mode));
    if (isHistory) renderHistory(run, caseAt(run, state.workload));
    else renderSuite(run);
    element('origin').textContent = 'Run ' + run.id + ' · ' + short(run.sha) + ' · source base ' + short(run.baseline);
    element('evidence').href = run.url;
    element('actions').href = run.actions_url;
    element('actions').hidden = !run.actions_url;
  }
  function update(patch) {
    Object.assign(state, patch);
    const url = viewUrl(state.view, state.workload);
    if (url.href !== location.href) history.pushState(null, '', url);
    render();
  }
  element('workload').replaceChildren(...workloads.map(id => new Option(humanCase(id), id)));
  const selectorLabel = r => short(r.sha) + ' · ' + timestamp(r);
  const seenLabels = new Set();
  const duplicateLabels = new Set();
  for (const run of runs) {
    const label = selectorLabel(run);
    if (seenLabels.has(label)) duplicateLabels.add(label);
    seenLabels.add(label);
  }
  element('run').replaceChildren(...runs.slice().reverse().map(run => {
    const label = selectorLabel(run);
    return new Option(duplicateLabels.has(label) ? run.id + ' · ' + timestamp(run) : label, run.id);
  }));
  element('workload').addEventListener('change', e => update({workload: e.target.value}));
  element('run').addEventListener('change', e => update({run: e.target.value}));
  root.addEventListener('click', e => {
    const target = e.target.closest('[data-mode], [data-run-id]');
    if (!target) return;
    const d = target.dataset;
    if (d.mode) update({mode: d.mode});
    else if (d.runId) {
      update({run: d.runId});
      if (target.tagName === 'BUTTON') element('history-rows').querySelector('[aria-pressed="true"]').focus({preventScroll: true});
    }
  });
  restore();
  for (const id of ['options', 'evidence']) element(id).hidden = false;
  render();
  element('fallback').hidden = true;
  window.addEventListener('popstate', () => { restore(); render(); });
  // Measure again after a font or container change, retaining the selected run.
  function redrawHistory() {
    if (state.view === 'history') {
      const run = runs.find(r => r.id === state.run);
      const rows = runs.map(run => ({run, c: caseAt(run, state.workload)}));
      drawHistory(rows, caseAt(run, state.workload) || rows.findLast(row => row.c)?.c);
    }
  }
  new ResizeObserver(redrawHistory).observe(element('chart'));
  document.fonts.ready.then(redrawHistory);
})();
