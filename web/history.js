/* Frozen measurement summaries, shared capture columns and pin-to-compare plots. */
(() => {
  'use strict';
  const time = record => /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(record.createdAt || '') ? Date.parse(record.createdAt) : NaN;
  const proposed = record => record.historyRole === 'proposed';
  const isCommit = value => /^[a-f0-9]{40}$/i.test(value || '');
  const usable = record => record.qualified && isCommit(record.commit) && record.validation === 'passed' &&
    record.complete === 'true' && Number(record.count) > 0 && Number.isFinite(time(record)) &&
    ['median', 'min', 'max'].every(key => record[key] !== undefined && record[key] !== '' && Number.isFinite(Number(record[key]))) &&
    Number(record.min) <= Number(record.median) && Number(record.median) <= Number(record.max);

  function captureKey(record) {
    // Run provenance, not snapshot names or per-case timestamps, joins CI cases.
    const identity = /^\d+$/.test(record.runId || '') && /^[1-9]\d*$/.test(record.runAttempt || '')
      ? ['ci', record.repository, record.runId, record.runAttempt, record.commit]
      : ['capture', record.repository, record.snapshot, record.commit];
    return JSON.stringify([proposed(record) ? 'proposed' : 'main', ...identity]);
  }

  function columns(records) {
    const captures = new Map();
    for (const record of records) {
      const key = captureKey(record), stamp = Number.isFinite(time(record)) ? time(record) : Infinity;
      if (!captures.has(key)) captures.set(key, {...record, key, date: stamp});
      else captures.get(key).date = Math.min(captures.get(key).date, stamp);
    }
    const result = [...captures.values()].sort((a, b) => {
      if (proposed(a) !== proposed(b)) return proposed(a) ? 1 : -1;
      if (a.repository === b.repository && a.runId && b.runId) {
        const run = a.runId.localeCompare(b.runId, 'en', {numeric: true});
        if (run) return run;
        if (a.runAttempt !== b.runAttempt) return Number(a.runAttempt) - Number(b.runAttempt);
      }
      return (a.date === b.date ? 0 : a.date - b.date) || a.key.localeCompare(b.key);
    });
    const mainCounts = new Map(), versions = new Map();
    for (const column of result) {
      column.index = result.indexOf(column);
      if (!proposed(column)) {
        column.mainIndex = mainCounts.get(column.repository) || 0;
        mainCounts.set(column.repository, column.mainIndex + 1);
      }
      const version = JSON.stringify([column.historyRole, column.repository, column.commit]);
      versions.set(version, (versions.get(version) || 0) + 1);
    }
    const seen = new Map();
    for (const column of result) {
      const version = JSON.stringify([column.historyRole, column.repository, column.commit]);
      seen.set(version, (seen.get(version) || 0) + 1);
      column.label = proposed(column) ? 'PR #' + column.pr : isCommit(column.commit) ? column.commit.slice(0, 7) : 'Unpinned';
      if (versions.get(version) > 1) column.label += ' · ' + seen.get(version);
    }
    return result;
  }

  function segments(records, axis) {
    const positions = new Map(axis.map(column => [column.key, column]));
    const recorded = records.filter(record => !proposed(record)).sort((a, b) => positions.get(captureKey(a)).index - positions.get(captureKey(b)).index);
    const counts = new Map();
    recorded.forEach(record => counts.set(captureKey(record), (counts.get(captureKey(record)) || 0) + 1));
    const lines = [], proposals = [];
    let current = [], previous;
    const flush = () => { if (current.length > 1) lines.push(current); current = []; };
    for (const record of recorded) {
      const column = positions.get(captureKey(record));
      if (!usable(record) || counts.get(column.key) !== 1) { flush(); previous = record; continue; }
      if (previous && (column.mainIndex !== positions.get(captureKey(previous)).mainIndex + 1 || time(record) <= time(previous) ||
        (record.captureIndex !== undefined && Number(record.captureIndex) !== Number(previous.captureIndex) + 1))) flush();
      current.push(record); previous = record;
    }
    flush();
    for (const record of records.filter(record => proposed(record) && usable(record))) {
      const bases = recorded.filter(base => usable(base) && base.commit === record.baselineCommit &&
        base.repository === record.repository && counts.get(captureKey(base)) === 1 && time(base) <= time(record));
      const base = bases.sort((a, b) => time(a) - time(b)).at(-1);
      if (base) proposals.push([base, record]);
    }
    return {lines, proposals};
  }

  function latest(records, axis) {
    const repositories = new Set(records.map(record => record.repository));
    const last = axis.filter(column => repositories.has(column.repository)).at(-1);
    if (!last) return {status: 'missing'};
    const retained = records.filter(record => captureKey(record) === last.key);
    if (!retained.length) return {status: 'missing', proposed: proposed(last)};
    if (retained.length !== 1) return {status: 'multiple', count: retained.length};
    const record = retained[0];
    return {status: usable(record) ? 'qualified' : 'unqualified', record};
  }

  function format(value, unit) {
    const steps = unit === 'ns' ? [[1e9, 's'], [1e6, 'ms'], [1e3, 'µs']] : unit === 'bytes' ? [[1e9, 'GB'], [1e6, 'MB'], [1e3, 'kB']] : [];
    const [scale, label] = steps.find(([scale]) => Math.abs(value) >= scale) || [1, unit === 'bytes' ? 'B' : unit];
    return (value / scale).toLocaleString(undefined, {maximumSignificantDigits: 3}) + (label ? ' ' + label : '');
  }

  const recordsOf = group => [...group.querySelectorAll('.history-point, .history-unplotted')].map(element =>
    ({...element.dataset, qualified: element.classList.contains('history-point'), element, group}));
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const svgNode = (tag, attrs = {}, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const xAt = (index, count, width) => count === 1 ? (72 + width - 30) / 2 : 72 + index * (width - 102) / Math.max(1, count - 1);

  function drawAxis(svg, axis, width, height) {
    const budget = Math.max(2, Math.floor((width - 80) / 100)), stride = Math.max(1, Math.ceil((axis.length - 1) / (budget - 1)));
    axis.forEach((column, index) => {
      if (index % stride && index !== axis.length - 1) return;
      if (index !== axis.length - 1 && index > axis.length - 1 - stride) return;
      const x = xAt(index, axis.length, width);
      const label = svgNode('text', {x, y: height - 22, 'text-anchor': 'middle', class: 'history-axis-label', 'data-column': column.key}, column.label);
      label.append(svgNode('title', {}, `${column.repository}; ${column.commit || 'Unpinned source'}; ${column.runId ? 'run ' + column.runId + '/' + column.runAttempt + '; ' : ''}${Number.isFinite(column.date) ? new Date(column.date).toISOString() : 'Capture date unavailable'}`));
      svg.append(label, svgNode('text', {x, y: height - 6, 'text-anchor': 'middle', class: 'history-axis-label'}, proposed(column) ? 'Proposed' : Number.isFinite(column.date) ? new Date(column.date).toISOString().slice(5, 10) : 'Undated'));
    });
  }

  function plot(container, groups, axis, options) {
    const width = container.clientWidth;
    if (!width) return;
    container.replaceChildren();
    const mini = options.mini, height = mini ? 116 : 300, top = 24, bottom = height - (mini ? 18 : 65);
    const points = groups.flatMap(recordsOf).filter(usable);
    const unit = groups[0]?.dataset.unit || '';
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', class: 'history-plot',
      'aria-label': `${options.label}: measured commit/run columns; medians and observed ranges in ${unit}`});
    // A title on the entire plot obscures individual point tooltips in browsers.
    svg.append(svgNode('desc', {}, 'Solid segments retain one measurement context. Hollow diamonds and dashed links identify proposed PR measurements. Crosses retain unsuccessful captures without numeric values.'));
    axis.forEach((column, index) => {
      const center = xAt(index, axis.length, width);
      if (proposed(column)) svg.append(svgNode('rect', {x: center - 13, y: top - 12, width: 26, height: bottom - top + 24, class: 'history-proposed-column'}));
      svg.append(svgNode('line', {x1: center, x2: center, y1: top, y2: bottom, class: 'history-grid'}));
    });
    if (points.length) {
      const minimum = Math.min(...points.map(point => Number(point.min))), maximum = Math.max(...points.map(point => Number(point.max)));
      const padding = Math.max((maximum - minimum) * .15, Math.abs(maximum) * .015, Number.EPSILON);
      const low = minimum - padding, high = maximum + padding;
      const y = value => top + (high - value) * (bottom - top) / (high - low);
      const positions = new Map(axis.map(column => [column.key, xAt(column.index, axis.length, width)]));
      const x = record => positions.get(captureKey(record));
      for (let tick = 0; tick < (mini ? 2 : 4); tick++) {
        const value = mini ? tick ? maximum : minimum : low + tick * (high - low) / 3;
        if (mini && tick && maximum === minimum) continue;
        svg.append(svgNode('line', {x1: 72, x2: width - 30, y1: y(value), y2: y(value), class: 'history-grid'}),
          svgNode('text', {x: 64, y: y(value) + 4, 'text-anchor': 'end', class: 'history-axis-label'}, format(value, unit)));
      }
      for (const group of groups) {
        const records = recordsOf(group), color = options.color(group), style = '--history-series-color:' + color;
        const parts = segments(records, axis);
        for (const line of parts.lines) svg.append(svgNode('polyline', {points: line.map(record => `${x(record)},${y(Number(record.median))}`).join(' '), fill: 'none', class: 'history-trend', style, 'data-context': group.dataset.context}));
        for (const [base, record] of parts.proposals) svg.append(svgNode('line', {x1: x(base), x2: x(record), y1: y(Number(base.median)), y2: y(Number(record.median)), class: 'history-proposed-trend', style, 'data-base-commit': base.commit, 'data-proposed-commit': record.commit}));
        for (const record of records.filter(usable)) {
          const center = x(record), median = y(Number(record.median));
          const label = `${proposed(record) ? 'Proposed PR #' + record.pr + '; ' : ''}${group.dataset.case}; ${group.dataset.label}; ${record.commit}; ${record.runId ? 'run ' + record.runId + '/' + record.runAttempt + '; ' : ''}captured ${record.createdAt}; median ${format(Number(record.median), unit)}, observed range ${format(Number(record.min), unit)} to ${format(Number(record.max), unit)}, ${record.count} samples`;
          const open = options.open(record, group);
          const mark = svgNode('g', {class: 'history-mark', tabindex: '0', role: open ? 'button' : 'img',
            'aria-label': (open ? 'Open recorded comparison: ' : '') + label,
            'data-source-series': record.sourceSeries, 'data-history-role': record.historyRole, 'data-column': captureKey(record)});
          if (open) {
            mark.addEventListener('click', open);
            mark.addEventListener('keydown', event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); open();}});
          }
          mark.append(svgNode('title', {}, label), svgNode('line', {x1: center, x2: center, y1: y(Number(record.min)), y2: y(Number(record.max)), class: 'history-range', style}),
            svgNode('circle', {cx: center, cy: median, r: 18, fill: 'transparent', class: 'history-hit'}));
          if (proposed(record)) mark.append(svgNode('path', {d: `M ${center} ${median - 6} l 6 6 l -6 6 l -6 -6 Z`, class: 'history-proposed-point', style}));
          else mark.append(svgNode('circle', {cx: center, cy: median, r: 3.5, fill: color}));
          svg.append(mark);
        }
      }
    }
    const missing = new Map();
    for (const record of groups.flatMap(recordsOf).filter(record => !usable(record))) missing.set(captureKey(record), record);
    for (const [key, record] of missing) {
      const index = axis.findIndex(column => column.key === key), center = xAt(index, axis.length, width);
      const reason = record.reason || 'No qualified, complete measurement with a capture date';
      const mark = svgNode('g', {class: 'history-unqualified-mark', tabindex: '0', role: 'img', 'aria-label': reason, 'data-column': key, 'data-history-role': record.historyRole});
      mark.append(svgNode('title', {}, `${record.commit}; ${reason}`), svgNode('path', {d: `M ${center - 4} ${bottom - 4} l 8 8 m -8 0 l 8 -8`, class: 'history-missing-mark'}));
      svg.append(mark);
    }
    if (!points.length) svg.append(svgNode('text', {x: width / 2, y: height / 2, 'text-anchor': 'middle', class: 'history-axis-label'}, 'No qualified points'));
    if (!mini) {
      drawAxis(svg, axis, width, height - 18);
      svg.append(svgNode('text', {x: (72 + width - 30) / 2, y: height - 2, 'text-anchor': 'middle', class: 'history-axis-label'}, 'Measured commits / runs'));
    }
    container.append(svg);
  }

  function render({root, groups, allGroups, identity, labels, colors, pins, toggle, open}) {
    const axis = columns(allGroups.flatMap(recordsOf));
    const choices = [...new Set(groups.map(identity))];
    const container = root.querySelector('#history-workloads');
    if (!container) return;
    container.replaceChildren();
    const header = element('div', 'history-run-layout history-run-heading');
    const heading = element('div', '', 'Workload / implementation'), axisBox = element('div', 'history-run-chart'), latestLabel = element('div', 'history-latest-heading', axis.some(proposed) ? 'Proposed PR' : 'Latest run');
    header.append(heading, axisBox, latestLabel); container.append(header);
    if (axisBox.clientWidth) {
      const svg = svgNode('svg', {viewBox: `0 0 ${axisBox.clientWidth} 48`, role: 'img', class: 'history-plot', 'aria-label': 'Shared measured commit and run columns'});
      drawAxis(svg, axis, axisBox.clientWidth, 46); axisBox.append(svg);
    }
    for (const key of choices) {
      const selected = groups.filter(group => identity(group) === key), records = selected.flatMap(recordsOf);
      const row = element('article', 'history-run-layout history-workload'); row.dataset.historyWay = key;
      const title = element('div', 'history-workload-title', labels.get(key));
      const representation = /^(.*) · (orientation|explicit|projected) ([34])D$/.exec(labels.get(key));
      if (representation) {
        title.textContent = representation[1];
        title.append(element('small', 'history-workload-variant', representation[2][0].toUpperCase() + representation[2].slice(1) + ' ' + representation[3] + 'D'));
      }
      if (selected.length > 1) title.append(element('small', 'history-context-count', `${selected.length} measurement contexts`));
      const chart = element('div', 'history-run-chart'), side = element('div', 'history-workload-actions');
      const result = latest(records, axis);
      if (result.status === 'qualified') {
        side.append(element('strong', 'history-latest-value', format(Number(result.record.median), selected[0].dataset.unit)));
        if (result.record.pairedRatio && Number(result.record.pairedRatio) > 0 && Number.isFinite(Number(result.record.pairedRatio))) {
          const ratio = Number(result.record.pairedRatio), delta = element('span', 'history-paired-value', `${ratio.toLocaleString(undefined, {maximumSignificantDigits: 3})}× paired baseline`);
          delta.title = 'Retained candidate / baseline ratio from this run’s paired discovery measurements. This is not the gate outcome or a comparison with an independent history point.';
          side.append(delta);
        }
      } else {
        side.append(element('span', 'history-latest-unavailable', result.status === 'missing' ? (result.proposed ? 'No PR measurement' : 'Not measured') : result.status === 'multiple' ? `${result.count} retained measurements` : 'Unqualified'));
      }
      const button = element('button', 'history-pin', pins.has(key) ? 'Pinned' : 'Pin to compare');
      button.type = 'button'; button.dataset.historyPin = key; button.setAttribute('aria-pressed', String(pins.has(key)));
      button.setAttribute('aria-label', `${pins.has(key) ? 'Unpin' : 'Pin'} ${labels.get(key)}`);
      button.addEventListener('click', () => toggle(key)); side.append(button);
      row.append(title, chart, side); container.append(row);
      plot(chart, selected, axis, {mini: true, color: group => pins.has(key) ? colors(group) : 'var(--link)', label: labels.get(key), open});
    }
    const legend = root.querySelector('#history-visible-series'); legend.replaceChildren();
    const pinned = groups.filter(group => pins.has(identity(group)));
    for (const key of choices.filter(key => pins.has(key))) {
      const button = element('button', 'history-unpin'), swatch = element('span', 'history-series-sample');
      swatch.style.setProperty('--history-series-color', colors(groups.find(group => identity(group) === key)));
      button.type = 'button';button.setAttribute('aria-label', 'Unpin ' + labels.get(key));
      button.append(swatch, document.createTextNode(labels.get(key) + ' ×')); button.addEventListener('click', () => toggle(key));legend.append(button);
    }
    const count = new Set(pinned.map(identity)).size;
    const jump = root.querySelector('#history-pin-jump');
    if (jump) jump.textContent = `Compare pinned (${count}) ↓`;
    root.querySelector('#history-pin-count').textContent = `${count} pinned · shared ${groups[0]?.dataset.unit || ''} scale · ${groups[0]?.dataset.direction === 'higher' ? 'higher' : 'lower'} is better`;
    const comparison = root.querySelector('#history-overview-chart');
    if (pinned.length) plot(comparison, pinned, axis, {color: colors, label: 'Pinned comparison', open});
    else comparison.replaceChildren(element('p', 'history-sparse', 'Pin workloads above to compare their histories on one scale.'));
  }

  const api = {captureKey, columns, segments, latest, usable, format, render};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else window.GammabenchHistory = api;
})();
