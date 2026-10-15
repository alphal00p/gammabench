/* Navigation and plots use frozen report values; no statistics are computed here. */
(() => {
  const start = () => {
    const workspace = document.getElementById('benchmark-workspace');
    if (!workspace) return;
    const byId = id => document.getElementById(id);
    const benchmarks = [...workspace.querySelectorAll('[data-benchmark-id][data-study]')];
    const tabs = [...workspace.querySelectorAll('[data-workspace-tab]')];
    const panels = [...workspace.querySelectorAll('[data-workspace-panel]')];
    const groups = [...workspace.querySelectorAll('.history-series[data-series-id]')];
    const historyRoot = byId('benchmark-history');
    const includeRetired = byId('history-include-retired');
    const localStudies = new Set([workspace.dataset.defaultStudy, historyRoot?.dataset.defaultStudy,
      byId('comparison-matrix')?.dataset.defaultStudy, ...groups.map(group => group.dataset.study)]);
    const controls = Object.fromEntries(['case', 'metric', 'series'].map(key => [key, byId('history-' + key)]));
    const templates = Object.fromEntries(Object.entries(controls).map(([key, control]) =>
      [key, control ? [...control.options].map(option => option.cloneNode(true)) : []]));
    const benchmarkMap = new Map(benchmarks.map(button => [button.dataset.benchmarkId, button]));
    const groupMap = new Map(groups.map(group => [group.dataset.seriesId, group]));
    const defaultBenchmark = workspace.dataset.defaultBenchmark || benchmarks[0]?.dataset.benchmarkId;
    const state = {benchmark: defaultBenchmark, tab: workspace.dataset.defaultTab || 'compare', case: '', metric: '', scope: '', unit: '', direction: '', contract: '', series: ''};
    let restoring = false;

    const selected = () => benchmarkMap.get(state.benchmark);
    const study = () => selected()?.dataset.study;

    function setOptions(control, options, value) {
      if (!control) return;
      control.replaceChildren(...options.map(option => option.cloneNode(true)));
      control.disabled = options.length === 0;
      if (options.some(option => option.value === value)) control.value = value;
    }

    function refreshHistory() {
      const caseOptions = templates.case.filter(option => !option.dataset.study || option.dataset.study === study());
      if (state.case && !caseOptions.some(option => option.value === state.case)) state.case = '';
      const wantedCase = state.case || historyRoot?.dataset.defaultCase || caseOptions[0]?.value;
      setOptions(controls.case, caseOptions, wantedCase);
      if (state.tab === 'history') state.case = controls.case?.value || state.case;
      const metricOptions = templates.metric.filter(option => option.dataset.study === study());
      const wantedMetric = metricOptions.find(option => option.dataset.metric === (state.metric || selected()?.dataset.defaultMetric) &&
        (!state.scope || option.dataset.scope === state.scope) &&
        (!state.unit || option.dataset.unit === state.unit) &&
        (!state.direction || option.dataset.direction === state.direction)) || metricOptions[0];
      setOptions(controls.metric, metricOptions, wantedMetric?.value);
      if (wantedMetric) {
        state.metric = wantedMetric.dataset.metric;
        state.scope = wantedMetric.dataset.scope;
        state.unit = wantedMetric.dataset.unit;
        state.direction = wantedMetric.dataset.direction;
      }
      const visibleCase = state.case || controls.case?.value;
      const visibleMetric = state.metric || wantedMetric?.dataset.metric;
      const choices = templates.series.filter(option => option.dataset.study === study() &&
        option.dataset.case === visibleCase && option.dataset.metric === visibleMetric &&
        ['scope', 'unit', 'direction'].every(key => !state[key] || groupMap.get(option.value)?.dataset[key] === state[key]));
      const wantedSeries = choices.find(option => option.value === state.series &&
        (!state.contract || groupMap.get(option.value)?.dataset.contract === state.contract));
      const all = templates.series.find(option => option.value === '');
      if (all) choices.unshift(all);
      setOptions(controls.series, choices, wantedSeries?.value || '');
      state.series = controls.series?.value || '';
      const candidates = groups.filter(group => group.dataset.study === study() && group.dataset.case === visibleCase &&
        group.dataset.metric === visibleMetric && ['scope', 'unit', 'direction'].every(key => !state[key] || group.dataset[key] === state[key]) &&
        (!state.contract || group.dataset.contract === state.contract) && (!state.series || group.dataset.seriesId === state.series));
      renderWayControls(candidates);
      const active = candidates.filter(group => !hiddenWays.has(wayIdentity(group)) &&
        (includeRetired?.checked !== false || group.dataset.consistentlyRetired !== 'true'));
      if (state.series && active.length === 1) state.contract = active[0].dataset.contract || '';
      for (const group of groups) group.hidden = !active.includes(group);
      if (state.tab === 'history') drawOverview(active);
      for (const row of workspace.querySelectorAll('.history-availability')) {
        row.hidden = row.dataset.study !== study() || (visibleCase && row.dataset.case !== visibleCase);
      }
      const empty = byId('history-empty');
      if (empty) empty.hidden = active.length > 0;
      const summary = byId('history-summary');
      if (summary) {
        const count = active.reduce((total, group) => total + group.querySelectorAll('.history-point').length, 0);
        const retained = active.reduce((total, group) => total + group.querySelectorAll('.history-unplotted').length, 0);
        summary.textContent = active.length
          ? `${active.length} implementation/configuration${active.length === 1 ? '' : 's'} · ${count} qualified commit point${count === 1 ? '' : 's'}.` +
            (retained ? ` ${retained} unplotted capture${retained === 1 ? '' : 's'} retained below.` : '')
          : 'No captured configuration for this selection. Availability and evidence remain visible.';
      }
    }

    function show() {
      const benchmark = selected();
      for (const button of benchmarks) {
        const current = button === benchmark;
        button.setAttribute('aria-current', current ? 'page' : 'false');
        button.classList.toggle('is-active', current);
      }
      for (const tab of tabs) {
        const current = tab.dataset.workspaceTab === state.tab;
        tab.setAttribute('aria-selected', String(current));
        tab.tabIndex = current ? 0 : -1;
      }
      for (const panel of panels) panel.hidden = panel.dataset.workspacePanel !== state.tab;
      for (const content of workspace.querySelectorAll('[data-workspace-evidence][data-study]')) {
        content.hidden = content.dataset.study !== study();
      }
      for (const [id, key] of [['workspace-family', 'family'], ['workspace-title', 'title'], ['workspace-description', 'description']]) {
        if (byId(id) && benchmark?.dataset[key]) byId(id).textContent = benchmark.dataset[key];
      }
      refreshHistory();
    }

    function selectMatrix(extra = {}) {
      const group = groupMap.get(state.series);
      const context = group?.dataset.study === study() && group.dataset.metric === state.metric
        ? {unit: group.dataset.unit, direction: group.dataset.direction, contract: group.dataset.contract} : {};
      window.dispatchEvent(new CustomEvent('gammabench:matrix-select', {detail: {
        study: study(), case: state.case, metric: state.metric || undefined,
        scope: state.scope || undefined, unit: state.unit || undefined, direction: state.direction || undefined,
        contract: state.contract || undefined,
        writeQuery: false, ...context, ...extra,
      }}));
    }

    function writeQuery(replace = false) {
      if (restoring) return;
      const url = new URL(location.href);
      for (const key of ['benchmark', 'tab', 'case', 'metric', 'unit', 'direction', 'contract', 'series']) {
        if (state[key]) url.searchParams.set(key, state[key]);
        else url.searchParams.delete(key);
      }
      if (state.scope) url.searchParams.set('scope', state.scope);
      else url.searchParams.delete('scope');
      if (includeRetired?.checked === false) url.searchParams.set('history_include_retired', '0'); else url.searchParams.delete('history_include_retired');
      if (hiddenWays.size) url.searchParams.set('hidden_ways', JSON.stringify([...hiddenWays].sort()));
      else url.searchParams.delete('hidden_ways');
      const view = byId('matrix-metric')?.value;
      const reference = byId('matrix-reference')?.value;
      const captureSet = byId('matrix-set')?.value;
      if (captureSet !== undefined) url.searchParams.set('set', captureSet);
      url.searchParams.set('study', study());
      if (view) url.searchParams.set('view', view);
      if (reference !== undefined) url.searchParams.set('reference', reference);
      if (url.href !== location.href) history[replace ? 'replaceState' : 'pushState'](null, '', url);
    }

    function navigateBenchmark(button, preserveQuery = false) {
      if (localStudies.has(button.dataset.study) || !button.dataset.href) return false;
      const url = new URL(button.dataset.href, location.href);
      if (url.origin !== location.origin) return false;
      url.search = preserveQuery ? location.search : '';
      url.searchParams.set('benchmark', button.dataset.benchmarkId);
      if (!preserveQuery) {
        url.searchParams.set('tab', state.tab);
        if (state.case) url.searchParams.set('case', state.case);
      }
      if (url.href === location.href) return false;
      location[preserveQuery ? 'replace' : 'assign'](url.href);
      return true;
    }

    function restore() {
      restoring = true;
      const params = new URLSearchParams(location.search);
      hiddenWays.clear();
      if (includeRetired) includeRetired.checked = params.get('history_include_retired') !== '0';
      try {
        const hidden = JSON.parse(params.get('hidden_ways') || '[]');
        if (Array.isArray(hidden)) for (const key of hidden) if (wayLabels.has(key)) hiddenWays.add(key);
      } catch { /* Invalid saved series visibility keeps captured evidence visible. */ }
      let requestedViewStudy;
      try {
        const view = JSON.parse(params.get('view'));
        if (Array.isArray(view) && typeof view[0] === 'string') requestedViewStudy = view[0];
      } catch { /* An invalid legacy view is handled by the matrix notice. */ }
      const legacyStudy = params.get('study') || requestedViewStudy || document.querySelector('.matrix-view:not([hidden])')?.dataset.study;
      const legacy = benchmarks.find(button => button.dataset.study === legacyStudy);
      state.benchmark = benchmarkMap.has(params.get('benchmark')) ? params.get('benchmark') : legacy?.dataset.benchmarkId || defaultBenchmark;
      state.tab = tabs.some(tab => tab.dataset.workspaceTab === params.get('tab')) ? params.get('tab') :
        params.has('study') || params.has('reference') || params.has('baseline') || params.has('view') ? 'compare' : workspace.dataset.defaultTab || 'compare';
      if (navigateBenchmark(selected(), true)) return;
      state.case = params.get('case') || '';
      const active = document.querySelector('.matrix-view:not([hidden])');
      const explicitMeasurement = ['view', 'metric', 'scope', 'unit', 'direction', 'contract', 'reference', 'baseline']
        .some(key => params.has(key));
      const validatedView = explicitMeasurement && active?.dataset.study === study() ? active : null;
      state.metric = validatedView?.dataset.metric || params.get('metric') ||
        (!explicitMeasurement ? selected()?.dataset.defaultMetric : '') ||
        (active?.dataset.study === study() ? active.dataset.metric : '');
      const sameMeasurement = active?.dataset.study === study() && active.dataset.metric === state.metric;
      for (const key of ['scope', 'unit', 'direction', 'contract']) {
        state[key] = validatedView ? validatedView.dataset[key] :
          params.get(key) || (sameMeasurement ? active.dataset[key] : '');
      }
      state.series = params.get('series') || '';
      show();
      // report.js already resolved legacy references and invalid view notices.
      if (params.has('benchmark') || state.tab === 'history' || active?.dataset.study !== study() ||
          active?.dataset.metric !== state.metric) selectMatrix({preserveNotice: true});
      restoring = false;
    }

    function activateTab(tab) {
      state.tab = tab;
      show();
      selectMatrix();
      writeQuery();
    }

    for (const button of benchmarks) button.addEventListener('click', event => {
      if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (button.dataset.benchmarkId === state.benchmark || navigateBenchmark(button)) return;
      state.benchmark = button.dataset.benchmarkId;
      state.case = ''; state.metric = ''; state.scope = ''; state.unit = ''; state.direction = ''; state.contract = ''; state.series = '';
      const preferred = [...document.querySelectorAll('.matrix-view')].filter(view =>
        view.dataset.study === study() && view.dataset.metric === button.dataset.defaultMetric);
      selectMatrix(preferred.length === 1 ? {view: preferred[0].dataset.matrixView} : {});
      const active = document.querySelector('.matrix-view:not([hidden])');
      state.metric = active?.dataset.metric || '';
      state.scope = active?.dataset.scope || '';
      state.unit = active?.dataset.unit || '';
      state.direction = active?.dataset.direction || '';
      state.contract = active?.dataset.contract || '';
      show(); selectMatrix(); writeQuery();
    });
    for (const tab of tabs) {
      tab.addEventListener('click', () => activateTab(tab.dataset.workspaceTab));
      tab.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const index = tabs.indexOf(tab);
        const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs.at(-1) :
          tabs[(index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
        activateTab(next.dataset.workspaceTab); next.focus();
      });
    }
    for (const [key, control] of Object.entries(controls)) if (control) control.addEventListener('input', () => {
      if (key === 'metric') {
        const option = control.selectedOptions[0];
        state.metric = option?.dataset.metric || '';
        state.scope = option?.dataset.scope || '';
        state.unit = option?.dataset.unit || '';
        state.direction = option?.dataset.direction || '';
        state.contract = '';
      } else {
        state[key] = control.value;
        if (key === 'series') state.contract = groupMap.get(control.value)?.dataset.contract || '';
      }
      show(); selectMatrix(); writeQuery();
    });
    window.addEventListener('gammabench:matrix-change', event => {
      if (event.detail.origin !== 'control') return;
      const benchmark = benchmarks.find(button => button.dataset.study === event.detail.study);
      if (benchmark) state.benchmark = benchmark.dataset.benchmarkId;
      state.case = event.detail.case || '';
      state.metric = event.detail.metric || '';
      state.scope = event.detail.scope || '';
      state.unit = event.detail.unit || '';
      state.direction = event.detail.direction || '';
      state.contract = event.detail.contract || '';
      show(); writeQuery(true);
    });
    includeRetired?.addEventListener('input', () => { refreshHistory(); writeQuery(); });
    window.addEventListener('popstate', restore);
    window.addEventListener('resize', () => {
      if (state.tab === 'history') {
        drawOverview(groups.filter(group => !group.hidden));
      }
    });
    const wayIdentity = group => JSON.stringify([group.dataset.tool, group.dataset.way]);
    const wayLabels = new Map(groups.map(group => [wayIdentity(group), `${group.dataset.toolLabel} / ${group.dataset.wayLabel}`]));
    const wayPositions = new Map([...wayLabels.keys()].map((key, index) => [key, index]));
    const hiddenWays = new Set();
    function renderWayControls(candidates) {
      const container = byId('history-visible-series');
      if (!container) return;
      const choices = new Set(candidates.map(wayIdentity));
      container.replaceChildren();
      for (const key of choices) {
        const label = document.createElement('label'), input = document.createElement('input'), sample = document.createElement('span');
        input.type = 'checkbox'; input.checked = !hiddenWays.has(key); input.dataset.historyWay = key;
        input.addEventListener('input', () => {
          if (input.checked) hiddenWays.delete(key); else hiddenWays.add(key);
          refreshHistory(); writeQuery();
          [...container.querySelectorAll('input')].find(control => control.dataset.historyWay === key)?.focus();
        });
        sample.className = 'history-series-sample'; sample.style.setProperty('--history-series-color', `var(--history-series-${wayPositions.get(key) % 6})`);
        label.append(input, sample, document.createTextNode(wayLabels.get(key))); container.append(label);
      }
    }

    function drawOverview(selectedGroups) {
      const chart = byId('history-overview-chart');
      if (!chart || !chart.clientWidth) return;
      const width = chart.clientWidth, height = width < 500 ? 420 : 400;
      const left = width < 500 ? 84 : 110, right = 35, top = 35, bottom = 85;
      const numeric = point => ['median', 'min', 'max'].every(key => point.dataset[key] !== '' && Number.isFinite(Number(point.dataset[key])));
      const capturedTime = point => /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(point.dataset.createdAt || '') ? Date.parse(point.dataset.createdAt) : NaN;
      const points = selectedGroups.flatMap(group => [...group.querySelectorAll('.history-point')].filter(point =>
        /^[a-f0-9]{40}$/i.test(point.dataset.commit || '') && point.dataset.validation === 'passed' && point.dataset.complete === 'true' &&
        Number(point.dataset.count) > 0 && numeric(point) && Number.isFinite(capturedTime(point)) &&
        Number(point.dataset.min) <= Number(point.dataset.median) && Number(point.dataset.median) <= Number(point.dataset.max)));
      chart.replaceChildren();
      if (!points.length) {
        const message = document.createElement('p'); message.className = 'history-sparse';
        message.textContent = selectedGroups.length ? 'No qualified source-commit points with recorded capture dates for this selection. Retained captures remain in the evidence below.' : 'Select an implementation to show its measured history.';
        chart.append(message); return;
      }
      const svgNS = 'http://www.w3.org/2000/svg';
      const node = (tag, attributes, text) => {
        const element = document.createElementNS(svgNS, tag);
        for (const [key, value] of Object.entries(attributes || {})) element.setAttribute(key, value);
        if (text !== undefined) element.textContent = text;
        return element;
      };
      const times = points.map(capturedTime), first = Math.min(...times), last = Math.max(...times);
      const minimum = Math.min(...points.map(point => Number(point.dataset.min))), maximum = Math.max(...points.map(point => Number(point.dataset.max)));
      const padding = Math.max((maximum - minimum) * .15, Math.abs(maximum) * .015, Number.EPSILON), low = minimum - padding, high = maximum + padding;
      const x = point => first === last ? (left + width - right) / 2 : left + (capturedTime(point) - first) * (width - left - right) / (last - first);
      const y = value => top + (high - value) * (height - top - bottom) / (high - low);
      const unit = selectedGroups[0]?.dataset.unit || '';
      const format = value => {
        const steps = unit === 'ns' ? [[1e9, 's'], [1e6, 'ms'], [1e3, 'µs']] : unit === 'bytes' ? [[1e9, 'GB'], [1e6, 'MB'], [1e3, 'kB']] : [];
        const [scale, label] = steps.find(([scale]) => Math.abs(value) >= scale) || [1, unit === 'bytes' ? 'B' : unit];
        return (value / scale).toLocaleString(undefined, {maximumSignificantDigits: 3}) + (label ? ' ' + label : '');
      };
      const svg = node('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', class: 'history-plot',
        'aria-label': `${state.case}: chosen implementation histories by recorded capture date; captured medians and observed ranges`});
      svg.append(node('title', {}, 'Implementation histories by measurement capture date'),
        node('desc', {}, 'Each line segment keeps one implementation and fixed measurement context. Missing, failed, unqualified or undated captures break lines. Dates belong to measurements, not source commits.'));
      for (let tick = 0; tick < 4; tick++) {
        const value = low + tick * (high - low) / 3;
        svg.append(node('line', {x1: left, y1: y(value), x2: width - right, y2: y(value), class: 'history-grid'}),
          node('text', {x: left - 12, y: y(value) + 4, 'text-anchor': 'end', class: 'history-axis-label'}, format(value)));
      }
      const tickCount = first === last ? 1 : width < 600 ? 2 : 4;
      for (let tick = 0; tick < tickCount; tick++) {
        const time = tickCount === 1 ? first : first + tick * (last - first) / (tickCount - 1);
        const center = tickCount === 1 ? (left + width - right) / 2 : left + tick * (width - left - right) / (tickCount - 1);
        svg.append(node('text', {x: center, y: height - bottom + 28, 'text-anchor': tick === 0 && tickCount > 1 ? 'start' : tick === tickCount - 1 && tickCount > 1 ? 'end' : 'middle', class: 'history-axis-label'}, last - first < 3 * 86400000 && tickCount > 2 ? new Date(time).toISOString().slice(5, 10) + ' ' + new Date(time).toISOString().slice(11, 16) + 'Z' : new Date(time).toISOString().slice(0, 10)));
      }
      svg.append(node('text', {x: left, y: 16, class: 'history-axis-label'}, selectedGroups[0]?.dataset.direction === 'higher' ? 'Higher is better' : 'Lower is better'));
      for (const [configuration, group] of selectedGroups.entries()) {
        const color = `var(--history-series-${wayPositions.get(wayIdentity(group)) % 6})`;
        const records = [...group.querySelectorAll('.history-point, .history-unplotted')];
        const captureCounts = new Map();
        for (const record of records) captureCounts.set(record.dataset.captureIndex, (captureCounts.get(record.dataset.captureIndex) || 0) + 1);
        let segment = [], previousCapture, previousTime;
        const flush = () => {
          if (segment.length > 1) svg.append(node('polyline', {points: segment.map(point => `${x(point)},${y(Number(point.dataset.median))}`).join(' '),
            class: 'history-trend', fill: 'none', style: '--history-series-color:' + color, 'stroke-dasharray': configuration % 3 === 0 ? '' : configuration % 3 === 1 ? '7 4' : '2 4', 'data-context': group.dataset.context}));
          segment = [];
        };
        for (const record of records) {
          const capture = Number(record.dataset.captureIndex), time = capturedTime(record);
          if (!points.includes(record)) { flush(); previousCapture = capture; previousTime = time; continue; }
          const ambiguousCapture = captureCounts.get(record.dataset.captureIndex) > 1;
          if (ambiguousCapture || previousCapture !== undefined && (capture > previousCapture + 1 || time <= previousTime)) flush();
          if (!ambiguousCapture) segment.push(record);
          previousCapture = capture; previousTime = time;
          const data = record.dataset, center = x(record), medianY = y(Number(data.median));
          const label = `${group.dataset.label}; ${data.commit}; captured ${data.createdAt}; median ${format(Number(data.median))}, observed range ${format(Number(data.min))} to ${format(Number(data.max))}, ${data.count} samples`;
          const matching = [...document.querySelectorAll('.matrix-view')].filter(view => view.dataset.independent !== 'true' &&
            ['study', 'case', 'metric', 'unit', 'scope', 'direction', 'contract'].every(key => key === 'case' || view.dataset[key] === group.dataset[key]) &&
            [...view.querySelectorAll('thead th[data-snapshot]')].some(header => header.dataset.snapshot === data.snapshot));
          const captureSets = new Set(matching.map(view => view.dataset.set));
          const canCompare = captureSets.size === 1;
          const mark = node('g', {class: 'history-mark', tabindex: '0', role: canCompare ? 'button' : 'img',
            'aria-label': (canCompare ? 'Open recorded comparison: ' : '') + label, 'data-source-series': data.sourceSeries});
          if (canCompare) {
            const open = () => {
              selectMatrix({set: matching[0].dataset.set, metric: group.dataset.metric, case: group.dataset.case,
                unit: group.dataset.unit, scope: group.dataset.scope, direction: group.dataset.direction, contract: group.dataset.contract});
              activateTab('compare');
            };
            mark.addEventListener('click', open);
            mark.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
          }
          mark.append(node('title', {}, label), node('line', {x1: center, x2: center, y1: y(Number(data.min)), y2: y(Number(data.max)), class: 'history-range', style: '--history-series-color:' + color}),
            node('circle', {cx: center, cy: medianY, r: 22, fill: 'transparent', class: 'history-hit'}));
          if (wayPositions.get(wayIdentity(group)) % 3 === 1) mark.append(node('rect', {x: center - 4, y: medianY - 4, width: 8, height: 8, fill: color}));
          else if (wayPositions.get(wayIdentity(group)) % 3 === 2) mark.append(node('path', {d: `M ${center} ${medianY - 5} l 5 9 h -10 Z`, fill: color}));
          else mark.append(node('circle', {cx: center, cy: medianY, r: 4, fill: color}));
          svg.append(mark);
        }
        flush();
      }
      svg.append(node('text', {x: (left + width - right) / 2, y: height - 15, 'text-anchor': 'middle', class: 'history-axis-label'}, 'Measurement capture date'));
      chart.append(svg);
    }
    restore();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
