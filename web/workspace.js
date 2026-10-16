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
    const multiWorkload = workspace.dataset.multiWorkload === 'true';
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
      if (multiWorkload) state.case = '';
      else if (state.tab === 'history') state.case = controls.case?.value || state.case;
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
      const visibleCase = multiWorkload ? '' : state.case || controls.case?.value;
      const visibleMetric = state.metric || wantedMetric?.dataset.metric;
      const choices = templates.series.filter(option => option.dataset.study === study() &&
        (!visibleCase || option.dataset.case === visibleCase) && option.dataset.metric === visibleMetric &&
        ['scope', 'unit', 'direction'].every(key => !state[key] || groupMap.get(option.value)?.dataset[key] === state[key]));
      const wantedSeries = choices.find(option => option.value === state.series &&
        (!state.contract || groupMap.get(option.value)?.dataset.contract === state.contract));
      const all = templates.series.find(option => option.value === '');
      if (all) choices.unshift(all);
      setOptions(controls.series, choices, wantedSeries?.value || '');
      state.series = controls.series?.value || '';
      const candidates = groups.filter(group => group.dataset.study === study() && (!visibleCase || group.dataset.case === visibleCase) &&
        group.dataset.metric === visibleMetric && ['scope', 'unit', 'direction'].every(key => !state[key] || group.dataset[key] === state[key]) &&
        (!state.contract || group.dataset.contract === state.contract) && (!state.series || group.dataset.seriesId === state.series));
      const active = candidates.filter(group => includeRetired?.checked !== false || group.dataset.consistentlyRetired !== 'true');
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
        const proposed = active.reduce((total, group) => total + group.querySelectorAll('.history-point[data-history-role="proposed"]').length, 0);
        const selectedCount = new Set(active.map(wayIdentity)).size;
        summary.textContent = active.length
          ? `${selectedCount} ${multiWorkload ? 'workload' : 'implementation'}${selectedCount === 1 ? '' : 's'} · ${count} qualified commit point${count === 1 ? '' : 's'}.` +
            (proposed ? ` ${proposed} proposed PR point${proposed === 1 ? '' : 's'}.` : '') +
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
      if (pinnedWays !== null) url.searchParams.set('pinned', JSON.stringify([...pinnedWays].sort()));
      else url.searchParams.delete('pinned');
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
      pinnedWays = null;
      if (includeRetired) includeRetired.checked = params.get('history_include_retired') !== '0';
      try {
        const hidden = JSON.parse(params.get('hidden_ways') || '[]');
        if (Array.isArray(hidden)) for (const key of hidden) if (wayLabels.has(key)) hiddenWays.add(key);
      } catch { /* Invalid saved series visibility keeps captured evidence visible. */ }
      try {
        const pinned = JSON.parse(params.get('pinned'));
        if (Array.isArray(pinned)) pinnedWays = new Set(pinned.filter(key => wayLabels.has(key)));
      } catch { /* An invalid selection uses the first two available histories. */ }
      let requestedViewStudy;
      try {
        const view = JSON.parse(params.get('view'));
        if (Array.isArray(view) && typeof view[0] === 'string') requestedViewStudy = view[0];
      } catch { /* An invalid legacy view is handled by the matrix notice. */ }
      const legacyStudy = params.get('study') || requestedViewStudy || document.querySelector('.matrix-view:not([hidden])')?.dataset.study;
      const legacy = benchmarks.find(button => button.dataset.study === legacyStudy);
      state.benchmark = benchmarkMap.has(params.get('benchmark')) ? params.get('benchmark') : legacy?.dataset.benchmarkId || defaultBenchmark;
      state.tab = multiWorkload ? 'history' : tabs.some(tab => tab.dataset.workspaceTab === params.get('tab')) ? params.get('tab') :
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
    const wayIdentity = group => JSON.stringify([group.dataset.tool, group.dataset.way, ...(multiWorkload ? [group.dataset.case] : [])]);
    const singleImplementation = new Set(groups.map(group => JSON.stringify([group.dataset.tool, group.dataset.way]))).size === 1;
    const workloadLabel = group => group.dataset.case.replace(/^gl(\d+)(?:-|$)/, 'GL$1 · ')
      .replace(/-(orientation|explicit|projected)-([34])d$/, ' · $1 $2D').replaceAll('-', ' ').replace(/ · $/, '');
    const wayLabels = new Map(groups.map(group => [wayIdentity(group), multiWorkload && singleImplementation ? workloadLabel(group) : `${multiWorkload ? workloadLabel(group) + ' · ' : ''}${group.dataset.toolLabel} / ${group.dataset.wayLabel}`]));
    const wayPositions = new Map([...wayLabels.keys()].map((key, index) => [key, index]));
    const hiddenWays = new Set();
    let pinnedWays = null;
    function drawOverview(selectedGroups) {
      if (!historyRoot || !window.GammabenchHistory) return;
      const choices = [...new Set(selectedGroups.map(wayIdentity))];
      const pins = pinnedWays === null ? new Set(hiddenWays.size ? choices.filter(key => !hiddenWays.has(key)) : choices.slice(0, 2)) : pinnedWays;
      window.GammabenchHistory.render({
        root: historyRoot, groups: selectedGroups, allGroups: groups.filter(group => group.dataset.study === study()),
        identity: wayIdentity, labels: wayLabels, colors: group => `var(--history-series-${wayPositions.get(wayIdentity(group)) % 12})`, pins,
        toggle: key => {
          pinnedWays = new Set(pins);
          if (pinnedWays.has(key)) pinnedWays.delete(key); else pinnedWays.add(key);
          hiddenWays.clear();
          refreshHistory(); writeQuery();
          [...historyRoot.querySelectorAll('[data-history-pin]')].find(button => button.dataset.historyPin === key)?.focus({preventScroll: true});
        },
        open: (record, group) => {
          const matching = [...document.querySelectorAll('.matrix-view')].filter(view => view.dataset.independent !== 'true' &&
            ['study', 'metric', 'unit', 'scope', 'direction', 'contract'].every(key => view.dataset[key] === group.dataset[key]) &&
            [...view.querySelectorAll('thead th[data-snapshot]')].some(header => header.dataset.snapshot === record.snapshot));
          if (new Set(matching.map(view => view.dataset.set)).size === 1) return () => {
            selectMatrix({set: matching[0].dataset.set, metric: group.dataset.metric, case: group.dataset.case,
              unit: group.dataset.unit, scope: group.dataset.scope, direction: group.dataset.direction, contract: group.dataset.contract});
            activateTab('compare');
          };
          const url = record.comparisonHref ? new URL(record.comparisonHref, location.href) : null;
          if (url?.origin === location.origin) return () => {
            for (const key of ['case', 'metric', 'unit', 'scope', 'direction', 'contract']) url.searchParams.set(key, group.dataset[key]);
            location.assign(url.href);
          };
          return null;
        },
      });
    }
    restore();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
