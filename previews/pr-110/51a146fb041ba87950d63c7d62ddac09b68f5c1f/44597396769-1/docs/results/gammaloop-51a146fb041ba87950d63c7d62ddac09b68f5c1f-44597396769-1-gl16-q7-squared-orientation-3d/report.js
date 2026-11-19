/* Offline display only: all qualified statistics come from analysis.json. */
(() => {
  const byId = id => document.getElementById(id);
  const hasOption = (control, value) => control && [...control.options].some(option => option.value === value);
  const positive = value => value !== '' && Number.isFinite(Number(value)) && Number(value) > 0;
  const evidenceIds = ['search', 'case', 'baseline', 'variant', 'metric', 'status'];
  const evidence = Object.fromEntries(evidenceIds.map(id => [id, byId(id)]).filter(([, control]) => control));
  const evidenceDefaults = Object.fromEntries(Object.entries(evidence).map(([id, control]) => [id, control.value]));
  const tables = [['measurements', 'measurement-count'], ['comparisons', 'comparison-count'],
                  ['availability', 'availability-count']].map(([id, counter]) => ({
    id, counter: byId(counter), rows: [...document.querySelectorAll('#' + id + ' tbody tr')],
  }));
  const matrix = byId('comparison-matrix');
  const includeRetired = byId('matrix-include-retired');
  const controls = Object.fromEntries(['study', 'set', 'metric', 'reference', 'case', 'search']
    .map(id => [id, byId('matrix-' + id)]));
  const views = [...document.querySelectorAll('[data-matrix-view]')]
    .filter(element => element.classList.contains('matrix-view'));
  const viewMap = new Map(views.map(view => [view.dataset.matrixView, view]));
  const metricOptions = controls.metric ? [...controls.metric.options].map(option => option.cloneNode(true)) : [];
  const setOptions = controls.set ? [...controls.set.options].map(option => option.cloneNode(true)) : [];
  const referenceOptions = controls.reference ? [...controls.reference.options].map(option => option.cloneNode(true)) : [];
  const studies = new Set([matrix?.dataset.defaultStudy, ...views.map(view => view.dataset.study),
    ...(controls.study ? [...controls.study.options].map(option => option.value) : [])]);
  let notice = '';
  const hiddenColumns = new Map();
  const hiddenFor = captureSet => {
    if (!hiddenColumns.has(captureSet)) hiddenColumns.set(captureSet, new Set());
    return hiddenColumns.get(captureSet);
  };
  const columnHeaders = view => [...(view?.querySelectorAll('thead th[data-run]') || [])];
  function eligibleColumns(view) {
    const headers = columnHeaders(view);
    if (view?.dataset.independent !== 'true') return headers;
    const groups = new Map();
    for (const header of headers) {
      const key = JSON.stringify([header.dataset.tool, header.dataset.way]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(header);
    }
    return [...groups.values()].flatMap(choices => {
      const dated = choices.filter(header => Number.isFinite(Date.parse(header.dataset.createdAt)));
      if (!dated.length) return choices;
      const latest = Math.max(...dated.map(header => Date.parse(header.dataset.createdAt)));
      // Equal or absent capture dates never justify silently discarding evidence.
      return choices.filter(header => !Number.isFinite(Date.parse(header.dataset.createdAt)) || Date.parse(header.dataset.createdAt) === latest);
    });
  }
  function restoreHidden(params) {
    hiddenColumns.clear();
    try {
      const values = JSON.parse(params.get('hidden_columns') || '[]');
      if (!Array.isArray(values)) return;
      for (const value of values) {
        if (!Array.isArray(value) || value.length !== 2 || typeof value[0] !== 'string' || !Array.isArray(value[1])) continue;
        const valid = new Set(views.filter(view => view.dataset.set === value[0]).flatMap(columnHeaders).map(header => header.dataset.run));
        hiddenColumns.set(value[0], new Set(value[1].filter(run => typeof run === 'string' && valid.has(run))));
      }
    } catch { /* Invalid saved visibility does not hide captured evidence. */ }
  }
  function updateColumns(view) {
    const eligible = eligibleColumns(view), eligibleRuns = new Set(eligible.map(header => header.dataset.run));
    const hidden = hiddenFor(view.dataset.set);
    const visible = eligible.filter(header => !hidden.has(header.dataset.run) &&
      (includeRetired?.checked !== false || header.dataset.consistentlyRetired !== 'true'));
    const visibleRuns = new Set(visible.map(header => header.dataset.run));
    for (const column of view.querySelectorAll('[data-run]')) column.hidden = !visibleRuns.has(column.dataset.run);
    for (const heading of view.querySelectorAll('[data-tool-group], [data-way-group]')) {
      const columns = visible.filter(header => heading.dataset.toolGroup !== undefined ? header.dataset.tool === heading.dataset.toolGroup :
        JSON.stringify([header.dataset.tool, header.dataset.way]) === heading.dataset.wayGroup);
      heading.hidden = !columns.length;
      heading.colSpan = Math.max(1, columns.length);
    }
    const picker = byId('matrix-column-options');
    if (picker) {
      picker.replaceChildren();
      const groups = new Map();
      for (const header of eligible) {
        const key = JSON.stringify([header.dataset.tool, header.dataset.way]);
        if (!groups.has(key)) {
          const fieldset = document.createElement('fieldset'), legend = document.createElement('legend');
          legend.textContent = header.dataset.toolLabel + ' / ' + header.dataset.wayLabel;
          fieldset.append(legend); picker.append(fieldset); groups.set(key, fieldset);
        }
        const label = document.createElement('label'), input = document.createElement('input');
        input.type = 'checkbox'; input.checked = visibleRuns.has(header.dataset.run);
        input.disabled = includeRetired?.checked === false && header.dataset.consistentlyRetired === 'true';
        input.dataset.visibleColumn = header.dataset.run;
        input.addEventListener('input', () => changeColumn(header.dataset.run, input.checked));
        label.append(input, document.createTextNode(header.dataset.versionLabel + (header.dataset.statusLabel ? ' · ' + header.dataset.statusLabel : '') + ' · ' + (header.dataset.createdAt || 'date unavailable')));
        groups.get(key).append(label);
      }
    }
    const counter = byId('matrix-column-count');
    if (counter) counter.textContent = `${visible.length} visible / ${eligible.length - visible.length} hidden`;
    const previous = controls.reference.value;
    options(controls.reference, referenceOptions, option => !option.value ||
      option.dataset.matrixView === view.dataset.matrixView && visibleRuns.has(option.value), previous);
    if (previous && !visibleRuns.has(previous)) {
      notice = 'Reference hidden or unavailable. Choose a visible reference; cells remain unranked.';
      controls.reference.value = '';
    }
    controls.reference.disabled = view.dataset.independent === 'true' || ![...controls.reference.options].some(option => option.value);
    if (view.dataset.independent === 'true') controls.reference.value = '';
    view.querySelector('.matrix-tablewrap').hidden = !visible.length;
    return {eligibleRuns, visibleRuns, snapshots: new Set(eligible.map(header => header.dataset.snapshot))};
  }
  function changeColumn(run, visible) {
    const active = viewMap.get(controls.metric.value);
    if (!active) return;
    const hidden = hiddenFor(active.dataset.set);
    if (visible) hidden.delete(run); else hidden.add(run);
    if (!visible && controls.reference.value === run) {
      controls.reference.value = '';
      notice = 'Reference hidden. Choose a visible reference; cells remain unranked.';
    }
    filter(); writeQuery(); publishMatrix('control');
  }


  function options(control, templates, predicate, value) {
    const selected = templates.filter(predicate).map(option => option.cloneNode(true));
    control.replaceChildren(...selected);
    control.value = selected.some(option => option.value === value) ? value : '';
  }

  function chooseView(study, requested, captureSet = controls.set?.value || matrix?.dataset.defaultSet) {
    const choices = views.filter(view => view.dataset.study === study && (!captureSet || view.dataset.set === captureSet));
    if (choices.some(view => view.dataset.matrixView === requested)) return requested;
    const previous = viewMap.get(requested);
    const equivalent = previous && choices.find(view => ['metric', 'scope', 'unit', 'direction', 'contract'].every(key => view.dataset[key] === previous.dataset[key]));
    if (equivalent) return equivalent.dataset.matrixView;
    const defaultView = controls.study ? [...controls.study.options].find(option => option.value === study)?.dataset.defaultView : matrix.dataset.defaultView;
    if (choices.some(view => view.dataset.matrixView === defaultView)) return defaultView;
    return choices[0]?.dataset.matrixView || '';
  }

  function placeholder(control, label) {
    const option = document.createElement('option');
    option.value = ''; option.textContent = label;
    control.replaceChildren(option); control.disabled = true;
  }

  function selectedStudy() {
    return controls.study?.value || viewMap.get(controls.metric?.value)?.dataset.study || matrix?.dataset.defaultStudy;
  }

  function setView(study, viewKey, reference, captureSet = viewMap.get(viewKey)?.dataset.set || controls.set?.value || matrix.dataset.defaultSet) {
    if (controls.study) controls.study.value = study;
    if (controls.set) options(controls.set, setOptions, option => option.dataset.study === study, captureSet);
    options(controls.metric, metricOptions, option => option.dataset.study === study && (!captureSet || option.dataset.set === captureSet), viewKey);
    controls.metric.disabled = !viewKey;
    if (!viewKey) placeholder(controls.metric, 'No captured measurements');
    options(controls.reference, referenceOptions,
      option => !option.value || option.dataset.matrixView === viewKey, reference);
    controls.reference.disabled = !viewKey || ![...controls.reference.options].some(option => option.value);
    if (controls.reference.disabled) placeholder(controls.reference, 'No reference measurements');
  }

  function legacyReference(selector, viewKey) {
    const separator = selector.indexOf(':');
    const kind = selector.slice(0, separator), label = selector.slice(separator + 1);
    if (!['engine', 'variant'].includes(kind)) return [];
    return [...new Set(referenceOptions.filter(option => option.dataset.matrixView === viewKey && option.value)
      .filter(option => {
        try { const run = JSON.parse(option.value); return run[kind === 'engine' ? 1 : 2] === label; }
        catch { return false; }
      }).map(option => option.value))];
  }

  function restore() {
    const params = new URLSearchParams(location.search);
    for (const [id, control] of Object.entries(evidence)) {
      control.value = evidenceDefaults[id];
      const value = params.get(id);
      if (value !== null && (id === 'search' || hasOption(control, value))) control.value = value;
    }
    if (!matrix || !controls.metric || !controls.reference) return;
    notice = '';
    restoreHidden(params);
    if (includeRetired) includeRetired.checked = params.get('include_retired') !== '0';
    let study = params.get('study') || matrix.dataset.defaultStudy;
    let viewKey = params.get('view');
    if (viewKey && viewMap.has(viewKey) && !params.has('study')) study = viewMap.get(viewKey).dataset.study;
    if (!studies.has(study)) {
      notice = 'Requested study is unavailable. Showing the default study.';
      study = matrix.dataset.defaultStudy;
    }
    let captureSet = params.get('set') || viewMap.get(viewKey)?.dataset.set || setOptions.find(option => option.dataset.study === study)?.value || matrix.dataset.defaultSet;
    if (!setOptions.some(option => option.dataset.study === study && option.value === captureSet)) {
      notice = 'Requested comparison set is unavailable. Showing the first recorded set.';
      captureSet = setOptions.find(option => option.dataset.study === study)?.value || '';
    }
    if (viewKey && !viewMap.has(viewKey)) {
      try {
        const legacy = JSON.parse(viewKey);
        if (Array.isArray(legacy) && legacy.length === 6) viewKey = views.find(view => view.dataset.study === legacy[0] && view.dataset.set === captureSet &&
          ['metric', 'unit', 'scope', 'direction', 'contract'].every((key, index) => view.dataset[key] === legacy[index + 1]))?.dataset.matrixView || viewKey;
      } catch { /* Invalid views are disclosed below. */ }
    }
    if (viewKey && (!viewMap.has(viewKey) || viewMap.get(viewKey).dataset.study !== study || viewMap.get(viewKey).dataset.set !== captureSet)) {
      notice = 'Requested metric or scope is unavailable. Showing the default view.';
      viewKey = '';
    }
    if (!viewKey && params.get('metric')) {
      const matches = views.filter(view => view.dataset.study === study && view.dataset.set === captureSet && view.dataset.metric === params.get('metric'));
      if (matches.length === 1) viewKey = matches[0].dataset.matrixView;
      else notice = 'The legacy metric does not identify one measured scope. Choose a metric below.';
    }
    viewKey = chooseView(study, viewKey, captureSet);
    const view = viewMap.get(viewKey);
    let reference = view?.dataset.defaultReference || '';
    if (view && params.has('reference')) {
      reference = params.get('reference');
      if (!referenceOptions.some(option => option.dataset.matrixView === viewKey && option.value === reference)) {
        if (reference) notice = 'Requested reference is unavailable in this view. Choose a reference; cells remain unranked.';
        reference = '';
      }
    } else if (view && params.has('baseline')) {
      const matches = legacyReference(params.get('baseline'), viewKey);
      reference = matches.length === 1 ? matches[0] : '';
      if (params.get('baseline') && matches.length !== 1) {
        notice = 'The legacy baseline does not identify one run. Choose an exact reference; cells remain unranked.';
      }
    }
    setView(study, viewKey, reference, captureSet);
    controls.search.value = params.get('search') || '';
    controls.case.value = hasOption(controls.case, params.get('case')) ? params.get('case') : '';
  }

  function filterEvidence() {
    const query = (evidence.search?.value || '').toLowerCase();
    for (const table of tables) {
      for (const row of table.rows) {
        const matches = Object.entries(evidence).every(([key, control]) => {
          const value = control.value;
          if (!value || key === 'search') return true;
          if (key === 'baseline') {
            if (table.id !== 'comparisons') return true;
            const separator = value.indexOf(':');
            return row.dataset[value.slice(0, separator) === 'engine' ? 'baselineEngine' : 'baselineVariant'] === value.slice(separator + 1);
          }
          // Availability belongs to a task/stage, not a successful metric sample.
          if (key === 'metric' && table.id === 'availability') return true;
          return row.dataset[key] === value;
        });
        row.hidden = !matches || !row.textContent.toLowerCase().includes(query);
      }
      if (table.counter) table.counter.textContent = '(' + table.rows.filter(row => !row.hidden).length + ' / ' + table.rows.length + ')';
    }
  }

  function unrank(cell, reason) {
    cell.dataset.tone = 'unranked';
    cell.style.removeProperty('--matrix-hue');
    const relative = cell.querySelector('.matrix-relative');
    if (relative) { relative.textContent = reason; relative.removeAttribute('title'); }
  }

  function rank(cell, logPerformance, multiplier, description) {
    cell.dataset.tone = 'qualified';
    cell.style.setProperty('--matrix-hue', String(60 + 60 * Math.max(-1, Math.min(1, logPerformance))));
    const relative = cell.querySelector('.matrix-relative');
    const value = document.createElement('strong');
    value.className = 'matrix-multiplier';
    value.textContent = multiplier;
    const label = document.createElement('span');
    label.className = 'matrix-comparison-label';
    label.textContent = description;
    relative.replaceChildren(value, ' ', label);
    relative.removeAttribute('title');
  }

  function measuredSeries(cell) {
    return cell ? [...cell.querySelectorAll('.matrix-series')].filter(series =>
      positive(series.dataset.count) && series.dataset.median !== '' && Number.isFinite(Number(series.dataset.median))) : [];
  }

  function recolor(row, reference, independent = false) {
    const cells = [...row.querySelectorAll('.matrix-cell')];
    const referenceCell = cells.find(cell => cell.dataset.run === reference);
    const referenceSeries = measuredSeries(referenceCell);
    for (const cell of cells) {
      cell.querySelectorAll('.matrix-comparison').forEach(marker => { marker.hidden = true; delete marker.dataset.active; });
      if (independent) { unrank(cell, 'Independent capture · absolute value only'); continue; }
      if (!reference) { unrank(cell, 'Choose a reference to compare'); continue; }
      if (!referenceCell || !referenceSeries.length) { unrank(cell, 'Reference has no measurement for this workload'); continue; }
      const series = measuredSeries(cell);
      if (referenceSeries.length !== 1 || series.length > 1) {
        unrank(cell, 'Multiple captures: inspect evidence; no single comparison'); continue;
      }
      if (!series.length) { unrank(cell, 'No measurement: inspect availability below'); continue; }
      const candidate = series[0], baseline = referenceSeries[0];
      if (cell === referenceCell) {
        if (candidate.dataset.validation !== 'passed' || candidate.dataset.complete !== 'true' ||
            !positive(candidate.dataset.median) || !positive(candidate.dataset.count)) {
          unrank(cell, 'Reference is incomplete or unqualified; no ranking'); continue;
        }
        rank(cell, 0, '1×', 'Reference');
        cell.dataset.tone = 'reference';
        continue;
      }
      const markers = [...cell.querySelectorAll('.matrix-comparison')].filter(marker =>
        marker.dataset.referenceRun === reference && marker.dataset.referenceSeries === baseline.dataset.series &&
        marker.dataset.candidateSeries === candidate.dataset.series);
      if (markers.length !== 1) { unrank(cell, 'No unambiguous precomputed comparison'); continue; }
      const marker = markers[0];
      marker.dataset.active = 'true';
      if (marker.dataset.status !== 'comparable') {
        unrank(cell, marker.dataset.reason || 'Comparison is suppressed; inspect evidence'); continue;
      }
      if (baseline.dataset.validation !== 'passed' || candidate.dataset.validation !== 'passed' ||
          baseline.dataset.complete !== 'true' || candidate.dataset.complete !== 'true' ||
          !positive(marker.dataset.speedup) ||
          !positive(marker.dataset.ratio) || !positive(marker.dataset.pairs) ||
          !positive(baseline.dataset.median) || !positive(candidate.dataset.median)) {
        unrank(cell, 'Incomplete, unqualified or non-positive values; no ranking'); continue;
      }
      const pairedRatio = Number(marker.dataset.ratio);
      const logPerformance = (candidate.dataset.direction === 'lower' ? -1 : 1) * Math.log2(pairedRatio);
      const ratio = pairedRatio.toLocaleString(undefined, {maximumSignificantDigits: 3});
      const measure = candidate.dataset.unit === 'ns' ? 'time' : 'value';
      rank(cell, logPerformance, ratio + '×', 'reference ' + measure + ' · ' + marker.dataset.pairs + ' pairs');
      cell.querySelector('.matrix-relative').title = marker.dataset.reason || 'Qualified matched sample pairs';
    }
  }

  function filterMatrix() {
    if (!matrix || !controls.metric || !controls.reference) return;
    const active = viewMap.get(controls.metric.value);
    const study = active?.dataset.study || selectedStudy();
    const query = controls.search.value.toLowerCase(), selectedCase = controls.case.value;
    const matches = element => (!selectedCase || element.dataset.matrixCase === selectedCase) &&
      element.textContent.toLowerCase().includes(query);
    let columnState;
    for (const view of views) {
      view.hidden = view !== active;
      if (view !== active) continue;
      columnState = updateColumns(view);
      const rows = [...view.querySelectorAll('tr[data-matrix-case]')];
      for (const row of rows) {
        recolor(row, controls.reference.value, view.dataset.independent === 'true');
        row.hidden = !matches(row);
      }
      const empty = view.querySelector('.matrix-empty');
      if (empty) empty.hidden = rows.some(row => !row.hidden);
      const summary = byId('matrix-summary');
      if (summary) summary.textContent = (notice ? notice + ' ' : '') +
        rows.filter(row => !row.hidden).length + ' / ' + rows.length + ' workloads. ' +
        (!columnState.visibleRuns.size ? 'No columns selected. Open Columns to restore them.' : view.dataset.independent === 'true' ?
          'Latest captured date per tool and implementation; equal or unknown dates remain distinct. Absolute values only, with no paired ranking.' :
          controls.reference.value ? 'Colours use stored qualified paired comparisons from this set.' : 'No reference selected; cells are unranked.');
    }
    if (!active && byId('matrix-summary')) byId('matrix-summary').textContent = (notice ? notice + ' ' : '') +
      'No measurements captured; unavailable or failed records are shown below.';
    const availability = [...document.querySelectorAll('.matrix-availability-item')];
    const independent = active?.dataset.independent === 'true' || controls.set?.selectedOptions[0]?.dataset.independent === 'true';
    for (const item of availability) item.hidden = item.dataset.study !== study ||
      (independent ? columnState && !columnState.snapshots.has(item.dataset.snapshot) : item.dataset.set !== controls.set?.value) || !matches(item);
    const count = byId('matrix-availability-count');
    if (count) count.textContent = availability.filter(item => !item.hidden).length + ' unavailable / failed / censored entries';
  }

  function writeQuery(replace = false) {
    const url = new URL(location.href);
    for (const [id, control] of Object.entries(evidence)) {
      if (control.value) url.searchParams.set(id, control.value);
      else url.searchParams.delete(id);
    }
    if (evidence.baseline && !evidence.baseline.value) url.searchParams.set('baseline', '');
    if (matrix && controls.metric && controls.reference) {
      const active = viewMap.get(controls.metric.value);
      url.searchParams.set('study', active?.dataset.study || selectedStudy());
      url.searchParams.set('view', controls.metric.value);
      if (controls.set) url.searchParams.set('set', controls.set.value);
      if (includeRetired?.checked === false) url.searchParams.set('include_retired', '0'); else url.searchParams.delete('include_retired');
      const hidden = [...hiddenColumns.entries()].filter(([, runs]) => runs.size).map(([key, runs]) => [key, [...runs].sort()]);
      if (hidden.length) url.searchParams.set('hidden_columns', JSON.stringify(hidden)); else url.searchParams.delete('hidden_columns');
      url.searchParams.set('reference', controls.reference.value);
      for (const id of ['case', 'search']) {
        if (controls[id].value) url.searchParams.set(id, controls[id].value);
        else url.searchParams.delete(id);
      }
    }
    if (url.href !== location.href) history[replace ? 'replaceState' : 'pushState'](null, '', url);
  }

  function filter() { filterEvidence(); filterMatrix(); }

  function publishMatrix(origin) {
    if (!matrix) return;
    const active = viewMap.get(controls.metric?.value);
    window.dispatchEvent(new CustomEvent('gammabench:matrix-change', {detail: {
      origin, study: active?.dataset.study || selectedStudy(), view: controls.metric?.value, set: controls.set?.value,
      metric: active?.dataset.metric, scope: active?.dataset.scope,
      unit: active?.dataset.unit, direction: active?.dataset.direction, contract: active?.dataset.contract,
      reference: controls.reference?.value, case: controls.case?.value, search: controls.search?.value,
    }}));
  }

  // The workspace requests an existing view; all ranking stays in this matrix.
  window.addEventListener('gammabench:matrix-select', event => {
    if (!matrix || !event.detail) return;
    const request = event.detail;
    const study = request.study || selectedStudy();
    if (!studies.has(study)) return;
    let viewKey = request.view;
    const captureSet = request.set || viewMap.get(viewKey)?.dataset.set || controls.set?.value || matrix.dataset.defaultSet;
    if (viewKey && viewMap.get(viewKey)?.dataset.study !== study) return;
    if (!viewKey && request.metric) {
      const matches = views.filter(view => view.dataset.study === study && view.dataset.set === captureSet &&
        view.dataset.metric === request.metric && ['scope', 'unit', 'direction', 'contract'].every(key =>
          !request[key] || view.dataset[key] === request[key]));
      if (matches.length !== 1) return;
      viewKey = matches[0].dataset.matrixView;
    }
    viewKey = chooseView(study, viewKey || controls.metric.value, captureSet);
    const previous = controls.reference.value;
    const candidates = referenceOptions.filter(option => option.dataset.matrixView === viewKey);
    let reference = Object.hasOwn(request, 'reference') ? request.reference : previous;
    if (!candidates.some(option => option.value === reference)) {
      reference = '';
    }
    if (!request.preserveNotice) notice = '';
    setView(study, viewKey, reference, captureSet);
    if (Object.hasOwn(request, 'case')) controls.case.value = hasOption(controls.case, request.case) ? request.case : '';
    if (typeof request.search === 'string') controls.search.value = request.search;
    for (const id of ['case', 'search']) if (evidence[id]) evidence[id].value = controls[id].value;
    filter();
    if (request.writeQuery !== false) writeQuery();
    publishMatrix('workspace');
  });
  for (const [id, control] of Object.entries(evidence)) control.addEventListener('input', () => {
    if (matrix && ['case', 'search'].includes(id)) controls[id].value = control.value;
    filter(); writeQuery(id === 'search'); publishMatrix('control');
  });
  for (const [id, control] of Object.entries(controls)) if (control) control.addEventListener('input', () => {
    notice = '';
    if (id === 'study' || id === 'set' || id === 'metric') {
      const active = viewMap.get(controls.metric.value);
      const study = id === 'study' ? controls.study.value : active?.dataset.study || selectedStudy();
      const captureSet = id === 'study' ? setOptions.find(option => option.dataset.study === study)?.value : controls.set?.value;
      const viewKey = chooseView(study, controls.metric.value, captureSet);
      const candidates = referenceOptions.filter(option => option.dataset.matrixView === viewKey);
      const previous = controls.reference.value;
      let reference = previous;
      if (previous && !candidates.some(option => option.value === previous)) {
        reference = '';
        notice = 'Previous reference has no measurements in this view. Choose another reference; cells remain unranked.';
      }
      setView(study, viewKey, reference, captureSet);
    }
    if (['case', 'search'].includes(id) && evidence[id]) evidence[id].value = control.value;
    filter(); writeQuery(id === 'search'); publishMatrix('control');
  });
  matrix?.addEventListener('click', event => {
    const button = event.target.closest('[data-hide-column]');
    if (button) changeColumn(button.dataset.hideColumn, false);
  });
  byId('matrix-show-columns')?.addEventListener('click', () => {
    hiddenFor(controls.set?.value).clear(); if (includeRetired) includeRetired.checked = true;
    filter(); writeQuery(); publishMatrix('control');
  });
  includeRetired?.addEventListener('input', () => { filter(); writeQuery(); publishMatrix('control'); });
  window.addEventListener('popstate', () => { restore(); filter(); publishMatrix('restore'); });
  restore(); filter(); publishMatrix('initial');
})();
