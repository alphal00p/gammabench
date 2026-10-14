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
    const localStudies = new Set([historyRoot?.dataset.defaultStudy,
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
        (!state.contract || groupMap.get(option.value)?.dataset.contract === state.contract)) ||
        choices.find(option => !state.contract || groupMap.get(option.value)?.dataset.contract === state.contract);
      if (!wantedSeries && state.contract) {
        const missing = document.createElement('option');
        missing.value = ''; missing.textContent = 'No captured configuration for this output';
        choices.unshift(missing);
      }
      setOptions(controls.series, choices, wantedSeries?.value || '');
      state.series = controls.series?.value || '';
      const active = groupMap.get(state.series);
      if (active) state.contract = active.dataset.contract || '';
      for (const group of groups) {
        group.hidden = group !== active;
        if (group === active && state.tab === 'history') drawHistory(group);
      }
      for (const row of workspace.querySelectorAll('.history-availability')) {
        row.hidden = row.dataset.study !== study() || (visibleCase && row.dataset.case !== visibleCase);
      }
      const empty = byId('history-empty');
      if (empty) empty.hidden = Boolean(active);
      const summary = byId('history-summary');
      if (summary) {
        const count = active?.querySelectorAll('.history-point').length || 0;
        const retained = active?.querySelectorAll('.history-unplotted').length || 0;
        summary.textContent = active
          ? `${active.dataset.label}: ${count} qualified commit point${count === 1 ? '' : 's'}.` +
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
      const view = byId('matrix-metric')?.value;
      const reference = byId('matrix-reference')?.value;
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
    window.addEventListener('popstate', restore);
    window.addEventListener('resize', () => {
      if (state.tab === 'history') {
        const active = groupMap.get(state.series);
        if (active) drawHistory(active);
      }
    });
    restore();

    function drawHistory(group) {
      const chart = group.querySelector('.history-chart');
      if (!chart || !chart.clientWidth) return;
      const width = chart.clientWidth;
      if (chart.firstElementChild && chart.dataset.plotWidth === String(width)) return;
      const points = [...group.querySelectorAll('.history-point')].filter(point => {
        const data = point.dataset;
        return /^[a-f0-9]{40}$/i.test(data.commit || '') && data.validation === 'passed' && data.complete === 'true' &&
          Number(data.count) > 0 && ['median', 'min', 'max'].every(key => data[key] !== '' && Number.isFinite(Number(data[key]))) &&
          Number(data.min) <= Number(data.median) && Number(data.median) <= Number(data.max);
      });
      if (!points.length) return;
      const svgNS = 'http://www.w3.org/2000/svg';
      const node = (tag, attributes, text) => {
        const element = document.createElementNS(svgNS, tag);
        for (const [key, value] of Object.entries(attributes || {})) element.setAttribute(key, value);
        if (text !== undefined) element.textContent = text;
        return element;
      };
      const minimum = Math.min(...points.map(point => Number(point.dataset.min)));
      const maximum = Math.max(...points.map(point => Number(point.dataset.max)));
      const padding = Math.max((maximum - minimum) * .15, Math.abs(maximum) * .015, Number.EPSILON);
      const low = minimum - padding, high = maximum + padding;
      const height = 300, left = 95, right = 35, top = 30, bottom = 70;
      const x = index => points.length === 1 ? (left + width - right) / 2 : left + index * (width - left - right) / (points.length - 1);
      const y = value => top + (high - value) * (height - top - bottom) / (high - low);
      const format = value => {
        const unit = group.dataset.unit;
        const steps = unit === 'ns' ? [[1e9, 's'], [1e6, 'ms'], [1e3, 'µs']] :
          unit === 'bytes' ? [[1e9, 'GB'], [1e6, 'MB'], [1e3, 'kB']] : [];
        const [scale, label] = steps.find(([scale]) => Math.abs(value) >= scale) || [1, unit === 'bytes' ? 'B' : unit];
        return (value / scale).toLocaleString(undefined, {maximumSignificantDigits: 3}) + (label ? ' ' + label : '');
      };
      const svg = node('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', class: 'history-plot',
        'aria-label': `${group.dataset.case}: ${group.dataset.engine}, ${group.dataset.metric}; captured source commits and observed ranges`});
      svg.append(node('title', {}, 'Median and observed minimum/maximum across captured source commits'),
        node('desc', {}, `${points.length} qualified captured commit points. Ranges are observed minimum/maximum, not confidence intervals. Commit positions follow captured order; they are not calendar intervals.`));
      for (let tick = 0; tick < 4; tick++) {
        const value = low + tick * (high - low) / 3;
        svg.append(node('line', {x1: left, y1: y(value), x2: width - right, y2: y(value), class: 'history-grid'}),
          node('text', {x: left - 12, y: y(value) + 4, 'text-anchor': 'end', class: 'history-axis-label'}, format(value)));
      }
      svg.append(node('text', {x: left, y: 15, class: 'history-axis-label'}, group.dataset.direction === 'higher' ? 'Higher is better' : 'Lower is better'));
      if (points.length > 1 && !group.querySelector('.history-unplotted')) {
        svg.append(node('polyline', {points: points.map((point, index) => `${x(index)},${y(Number(point.dataset.median))}`).join(' '), class: 'history-trend', fill: 'none'}));
      }
      for (const [index, point] of points.entries()) {
        const data = point.dataset, center = x(index), medianY = y(Number(data.median));
        const mark = node('g', {class: 'history-mark', tabindex: '0', role: 'img',
          'aria-label': `${data.commit}: median ${format(Number(data.median))}, observed range ${format(Number(data.min))} to ${format(Number(data.max))}, ${data.count} samples`});
        mark.append(node('title', {}, mark.getAttribute('aria-label')),
          node('line', {x1: center, x2: center, y1: y(Number(data.min)), y2: y(Number(data.max)), class: 'history-range'}),
          node('circle', {cx: center, cy: medianY, r: 22, fill: 'transparent', class: 'history-hit'}),
          node('circle', {cx: center, cy: medianY, r: 4, class: 'history-dot'}));
        svg.append(mark);
        if (points.length <= 8 || index === 0 || index === points.length - 1) {
          svg.append(node('text', {x: center, y: height - bottom + 25, 'text-anchor': 'middle', class: 'history-axis-label'}, data.commit.slice(0, 7)));
        }
      }
      svg.append(node('text', {x: (left + width - right) / 2, y: height - 15, 'text-anchor': 'middle', class: 'history-axis-label'},
        points.length === 1
          ? width < 500 ? 'One captured commit' : 'One captured source commit; no trend yet'
          : width < 500 ? 'Measured commits' : 'Captured source commits · observed ranges'));
      chart.replaceChildren(svg);
      chart.dataset.plotWidth = String(width);
    }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
