/* Discovery only: queries never derive timing statistics or measurement equivalence. */
const GammabenchExplorer = (() => {
  const unknown = value => value === undefined || value === null;
  const notApplicable = value => value && typeof value === 'object' && value.state === 'not-applicable';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const unary = op => ['has', 'missing', 'na'].includes(op);
  const identity = item => item.id || JSON.stringify([item.benchmark_id, item.case_id]);
  const read = (item, capture, field) => {
    if (capture?.metadata && Object.hasOwn(capture.metadata, field.id)) return capture.metadata[field.id];
    return field.scope === 'workload' ? item.metadata?.[field.id] : undefined;
  };
  const units = field => field.units || [{id: field.unit, label: field.unit, factor: 1}];
  const quantity = (value, field) => {
    if (!value || !finite(value.value)) return null;
    const unit = units(field).find(unit => unit.id === value.unit);
    return unit ? value.value * unit.factor : null;
  };
  function operators(field) {
    const equal = [['eq', 'is'], ['ne', 'is not']];
    const options = ['number', 'quantity'].includes(field.type) ? [...equal, ['gt', '>'], ['ge', '≥'], ['lt', '<'], ['le', '≤']] :
      field.type === 'date' ? [...equal, ['gt', 'after'], ['ge', 'on or after'], ['lt', 'before'], ['le', 'on or before']] :
      field.type === 'text' ? [['contains', 'contains'], ...equal] : field.type === 'tags' ? [['contains', 'contains tag']] : equal;
    return [...options, ['has', 'has a value'], ['missing', 'is unknown'], ['na', 'is not applicable']];
  }
  function validFilter(filter, fields) {
    if (!filter || typeof filter !== 'object') return false;
    const field = fields.find(field => field.id === filter.field);
    if (!field || !operators(field).some(([op]) => op === filter.op)) return false;
    if (unary(filter.op)) return true;
    const value = filter.value;
    if (field.type === 'number') return finite(value);
    if (field.type === 'quantity') return finite(quantity(value, field));
    if (field.type === 'boolean') return typeof value === 'boolean';
    if (field.type === 'date') return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) &&
      Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
    return typeof value === 'string' && value.length > 0 && value.length <= 500 &&
      (!field.values || field.type === 'text' || field.values.includes(value));
  }
  function matches(value, filter, field) {
    if (filter.op === 'missing') return unknown(value);
    if (filter.op === 'na') return Boolean(notApplicable(value));
    if (filter.op === 'has') return !unknown(value) && !notApplicable(value);
    if (unknown(value) || notApplicable(value)) return false;
    if (filter.op === 'contains') return Array.isArray(value) ? value.some(value => value === filter.value) :
      typeof value === 'string' && value.toLowerCase().includes(filter.value.toLowerCase());
    let left = value, right = filter.value;
    if (field.type === 'quantity') {
      left = quantity(left, field); right = quantity(right, field);
      if (!finite(left) || !finite(right)) return false;
    } else if (field.type === 'number' && !finite(left)) return false;
    if (typeof left === 'string') { left = left.toLowerCase(); right = right.toLowerCase(); }
    if (filter.op === 'eq') return left === right;
    if (filter.op === 'ne') return left !== right;
    return filter.op === 'ge' ? left >= right : filter.op === 'gt' ? left > right : filter.op === 'le' ? left <= right : left < right;
  }
  function searchable(value) {
    if (unknown(value) || notApplicable(value)) return [];
    if (Array.isArray(value)) return value.flatMap(searchable);
    return typeof value === 'object' ? Object.values(value).flatMap(searchable) : [String(value)];
  }
  function matching(catalog, state) {
    if (state.errors?.length) return [];
    const fields = new Map(catalog.fields.map(field => [field.id, field]));
    const terms = state.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return catalog.cases.map(item => ({item, contexts: (item.captures.length ? item.captures : [null]).filter(capture => {
      if (!state.filters.every(filter => matches(read(item, capture, fields.get(filter.field)), filter, fields.get(filter.field)))) return false;
      const text = [item.title, item.description, item.benchmark_title, item.benchmark_id, item.case_id,
        ...catalog.fields.flatMap(field => searchable(read(item, capture, field)))].join(' ').toLowerCase();
      return terms.every(term => text.includes(term));
    })})).filter(row => row.contexts.length);
  }
  function display(value, field) {
    if (unknown(value)) return 'Unknown';
    if (notApplicable(value)) return 'Not applicable';
    if (Array.isArray(value)) return value.length ? value.join(', ') : 'No values';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (field.type === 'quantity') return `${value.value} ${units(field).find(unit => unit.id === value.unit)?.label || value.unit}`;
    return String(value);
  }
  function grouped(rows, field) {
    const groups = new Map();
    for (const row of rows) for (const capture of row.contexts) {
      const raw = read(row.item, capture, field);
      for (const value of Array.isArray(raw) && raw.length ? raw : [raw]) {
        const canonical = field.type === 'quantity' && !unknown(value) && !notApplicable(value) ? quantity(value, field) : value;
        const key = unknown(value) ? 'unknown' : notApplicable(value) ? 'na' : JSON.stringify(canonical);
        if (!groups.has(key)) groups.set(key, {value, canonical, label: display(value, field), rows: new Map()});
        const group = groups.get(key), id = identity(row.item);
        if (!group.rows.has(id)) group.rows.set(id, {item: row.item, contexts: []});
        if (!group.rows.get(id).contexts.includes(capture)) group.rows.get(id).contexts.push(capture);
      }
    }
    return [...groups.values()].sort((a, b) => {
      if (finite(a.canonical) && finite(b.canonical)) return a.canonical - b.canonical;
      return a.label.localeCompare(b.label);
    }).map(group => ({...group, rows: [...group.rows.values()]}));
  }
  function inspectorSections(row, fields) {
    const owns = (metadata, field) => metadata && Object.hasOwn(metadata, field.id);
    const common = fields.filter(field => field.scope === 'workload' && owns(row.item.metadata, field));
    const sections = [{label: 'Common workload metadata', fields: common, capture: null}];
    for (const [index, capture] of row.contexts.filter(Boolean).entries()) {
      const retained = fields.filter(field => owns(capture.metadata, field) && (field.scope === 'capture' ||
        !owns(row.item.metadata, field) || JSON.stringify(capture.metadata[field.id]) !== JSON.stringify(row.item.metadata[field.id])));
      sections.push({label: capture.label || `Matching configuration ${index + 1}`, fields: retained, capture});
    }
    return sections;
  }
  function decode(search, fields) {
    const params = new URLSearchParams(search), errors = [];
    const query = params.get('q') || '', group = params.get('group') || 'none';
    let filters = [];
    if (query.length > 500) errors.push('The keyword query exceeds 500 characters.');
    if (params.has('filters')) {
      try {
        const value = JSON.parse(params.get('filters'));
        if (!Array.isArray(value) || value.length > 20 || !value.every(filter => validFilter(filter, fields))) throw Error();
        filters = value;
      } catch { errors.push('The saved filters are invalid or refer to unavailable fields. Clear all to start a new query.'); }
    }
    if (group !== 'none' && !fields.some(field => field.id === group)) errors.push('The saved grouping field is unavailable.');
    return {query, filters, group, errors};
  }
  function encode(state) {
    const params = new URLSearchParams();
    if (state.query) params.set('q', state.query);
    if (state.filters.length) params.set('filters', JSON.stringify(state.filters));
    if (state.group !== 'none') params.set('group', state.group);
    return params.toString();
  }
  return {unknown, notApplicable, identity, read, units, quantity, operators, validFilter, matches, matching, display, grouped, inspectorSections, decode, encode};
})();

if (typeof module !== 'undefined' && module.exports) module.exports = GammabenchExplorer;
if (typeof document !== 'undefined') {
  const start = () => {
    const root = document.getElementById('benchmark-explorer');
    if (!root) return;
    const E = GammabenchExplorer, byId = id => root.querySelector('#explorer-' + id);
    const catalog = JSON.parse(root.dataset.catalog), fields = catalog.fields;
    const fieldMap = new Map(fields.map(field => [field.id, field]));
    const escape = value => String(value).replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
    function href(item) {
      if (!item.href.startsWith('benchmarks/') || item.href.includes('\\') || item.href.split('?')[0].split('/').includes('..')) return '';
      return new URL((root.dataset.root || '') + item.href, location.href).href;
    }
    // Former root links remain exact links to a known benchmark workspace.
    const initial = new URLSearchParams(location.search), requested = initial.get('benchmark'), study = initial.get('study');
    const legacy = catalog.cases.find(item => requested ? item.benchmark_id === requested : study &&
      (item.study === study || item.captures.some(capture => capture.snapshot_id === study)));
    if (legacy && href(legacy)) {
      const destination = new URL(href(legacy)); destination.search = location.search; destination.hash = location.hash;
      if (destination.pathname !== location.pathname) { location.replace(destination.href); return; }
    }
    let state = E.decode(location.search, fields), selected = null, editing = null;
    let editor = {field: fields[0]?.id, op: 'eq', value: undefined};
    const notice = byId('notice'), builder = byId('builder'), fieldSelect = byId('field'), opSelect = byId('operator'), slot = byId('value-slot');
    byId('controls').hidden = false; byId('group-label').hidden = false;
    byId('add').disabled = fields.length === 0;
    function remember(replace = false) {
      if (state.errors.length) return;
      const url = new URL(location.href), query = E.encode(state);
      for (const key of ['q', 'filters', 'group']) url.searchParams.delete(key);
      for (const [key, value] of new URLSearchParams(query)) url.searchParams.set(key, value);
      if (url.href !== location.href) history[replace ? 'replaceState' : 'pushState'](null, '', url);
    }
    function knownValues(field) {
      if (field.values) return field.values;
      const values = catalog.cases.flatMap(item => (item.captures.length ? item.captures : [null]).flatMap(capture => E.read(item, capture, field)));
      return [...new Set(values.filter(value => !E.unknown(value) && !E.notApplicable(value) && typeof value !== 'object'))].sort((a, b) => String(a).localeCompare(String(b)));
    }
    function openEditor(open) {
      builder.hidden = !open; byId('add').setAttribute('aria-expanded', String(open)); byId('error').hidden = true;
    }
    function defaultEditor(field) {
      editor = {field: field.id, op: field.type === 'text' || field.type === 'tags' ? 'contains' : 'eq',
        value: field.type === 'quantity' ? {value: '', unit: E.units(field)[0].id} : field.type === 'boolean' ? true : knownValues(field)[0] ?? ''};
    }
    function renderEditor() {
      const field = fieldMap.get(editor.field); if (!field) return;
      fieldSelect.value = field.id;
      const ops = E.operators(field);
      if (!ops.some(([op]) => op === editor.op)) editor.op = ops[0][0];
      opSelect.innerHTML = ops.map(([value, label]) => `<option value="${value}">${escape(label)}</option>`).join(''); opSelect.value = editor.op;
      const unary = ['has', 'missing', 'na'].includes(editor.op);
      byId('value-label').hidden = unary;
      slot.replaceChildren();
      if (!unary) {
        const input = document.createElement(['enum', 'tags', 'boolean'].includes(field.type) ? 'select' : 'input');
        input.id = 'explorer-value'; input.setAttribute('aria-label', 'Filter value');
        if (input.tagName === 'SELECT') {
          const values = field.type === 'boolean' ? [true, false] : knownValues(field);
          for (const value of values) { const option = document.createElement('option'); option.value = String(value); option.textContent = E.display(value, field); input.append(option); }
          if (values.some(value => String(value) === String(editor.value))) input.value = String(editor.value);
        } else {
          input.type = field.type === 'date' ? 'date' : ['number', 'quantity'].includes(field.type) ? 'number' : 'text';
          if (input.type === 'number') input.step = 'any';
          input.value = field.type === 'quantity' ? editor.value?.value ?? '' : editor.value ?? '';
        }
        slot.append(input);
        if (field.type === 'quantity') {
          const unit = document.createElement('select'); unit.id = 'explorer-unit'; unit.className = 'explorer-unit'; unit.setAttribute('aria-label', 'Value unit');
          for (const entry of E.units(field)) { const option = document.createElement('option'); option.value = entry.id; option.textContent = entry.label; unit.append(option); }
          if (editor.value?.unit) unit.value = editor.value.unit;
          slot.append(unit);
        }
      }
      byId('field-help').textContent = `${field.scope === 'workload' ? 'Workload metadata' : 'Capture metadata · same configuration'} · ${field.type}`;
      byId('apply').textContent = editing === null ? 'Add' : 'Update'; byId('error').hidden = true;
    }
    for (const scope of ['workload', 'capture']) {
      const group = document.createElement('optgroup'); group.label = scope === 'workload' ? 'Workload fields' : 'Capture fields';
      for (const field of fields.filter(field => field.scope === scope)) { const option = document.createElement('option'); option.value = field.id; option.textContent = `${field.label} · ${field.type}`; group.append(option); }
      if (group.children.length) fieldSelect.append(group);
    }
    byId('group').innerHTML = '<option value="none">None</option>' + fields.map(field => `<option value="${escape(field.id)}">${escape(field.label)}</option>`).join('');
    const filterLabel = filter => {
      const field = fieldMap.get(filter.field), op = E.operators(field).find(([op]) => op === filter.op)[1];
      return `${field.label} ${op}${['has', 'missing', 'na'].includes(filter.op) ? '' : ' ' + E.display(filter.value, field)}`;
    };
    function summaryFields(row) {
      const preferred = [...new Set([...state.filters.map(filter => filter.field), ...fields.map(field => field.id)])];
      return preferred.map(id => fieldMap.get(id)).filter(field => row.contexts.some(capture => !E.unknown(E.read(row.item, capture, field))))
        .slice(0, 3).map(field => `<span>${escape(field.label)}: ${escape([...new Set(row.contexts.map(capture => E.display(E.read(row.item, capture, field), field)))].join(' / '))}</span>`).join('');
    }
    function table(rows) {
      return '<div class="explorer-table-wrap"><table class="explorer-table"><thead><tr><th scope="col">Benchmark case</th><th scope="col">Benchmark</th><th scope="col">Fields</th><th scope="col">Captures</th></tr></thead><tbody>' + rows.map(row => {
        const item = row.item, id = E.identity(item), count = row.contexts.filter(Boolean).length;
        return `<tr><td><button type="button" class="explorer-case" data-explorer-case="${escape(id)}">${escape(item.title)}</button><span class="explorer-description">${escape(item.description)}</span></td><td><a href="${escape(href(item))}">${escape(item.benchmark_title)}</a>${item.case_id === null ? '<span class="explorer-description">No cases declared</span>' : ''}</td><td class="explorer-fields">${summaryFields(row)}</td><td class="explorer-captures">${count ? count + ' matching configuration' + (count === 1 ? '' : 's') : 'No captures'}</td></tr>`;
      }).join('') + '</tbody></table></div>';
    }
    function inspect(row) {
      const host = byId('inspector'); host.hidden = false;
      const sections = E.inspectorSections(row, fields);
      const metadataTable = (section, index) => '<div class="explorer-table-wrap"><table class="explorer-table explorer-inspector-table"><thead><tr><th scope="col">Field</th><th scope="col">Value</th></tr></thead><tbody>' + section.fields.map(field => {
        const value = E.read(row.item, section.capture, field), disabled = Array.isArray(value) && value.length === 0;
        return `<tr><td>${escape(field.label)}</td><td><button type="button" class="explorer-value-filter" data-inspect-field="${escape(field.id)}" data-inspect-section="${index}" ${disabled ? 'disabled' : ''}>${escape(E.display(value, field))}</button></td></tr>`;
      }).join('') + '</tbody></table></div>';
      const configuration = (section, index) => {
        const capture = section.capture, metrics = capture.metrics?.length ? ' · ' + capture.metrics.join(', ') : '';
        const report = capture.href && capture.href.startsWith('docs/results/') && !capture.href.split('/').includes('..')
          ? new URL((root.dataset.root || '') + capture.href, location.href).href : '';
        return `<details class="explorer-capture-details" data-explorer-capture="${escape(capture.id)}"><summary>${escape(section.label + metrics)}</summary><p class="explorer-capture-id">Capture identity: <code>${escape(capture.id)}</code></p>${report ? `<p><a href="${escape(report)}">Retained report and source evidence →</a></p>` : ''}${metadataTable(section, index)}${capture.implementation ? `<details class="explorer-source-identity"><summary>Source and executable identity</summary><pre>${escape(JSON.stringify(capture.implementation, null, 2))}</pre></details>` : ''}</details>`;
      };
      host.innerHTML = `<div class="explorer-inspector-heading"><h2>${escape(row.item.title)}</h2><button type="button" class="explorer-quiet" id="explorer-close-inspector">Close</button></div><p class="explorer-inspector-note"><a href="${escape(href(row.item))}">Open benchmark and retained evidence →</a> · Select a metadata value to filter.</p>` +
        (sections[0].fields.length ? `<h3 class="explorer-group-heading">${escape(sections[0].label)}</h3>${metadataTable(sections[0], 0)}` : '<p class="explorer-empty">No common workload metadata is retained.</p>') +
        sections.slice(1).map((section, index) => configuration(section, index + 1)).join('');
      byId('close-inspector').addEventListener('click', () => { const trigger = [...byId('results').querySelectorAll('[data-explorer-case]')].find(button => button.dataset.explorerCase === selected); selected = null; host.hidden = true; trigger?.focus(); });
      for (const button of host.querySelectorAll('[data-inspect-field]')) button.addEventListener('click', () => {
        const field = fieldMap.get(button.dataset.inspectField), section = sections[Number(button.dataset.inspectSection)], value = E.read(row.item, section.capture, field);
        const filter = {field: field.id, op: E.unknown(value) ? 'missing' : E.notApplicable(value) ? 'na' : Array.isArray(value) ? 'contains' : 'eq'};
        if (!['missing', 'na'].includes(filter.op)) filter.value = Array.isArray(value) ? value[0] : value;
        if (state.filters.length < 20 && E.validFilter(filter, fields) && !state.filters.some(existing => JSON.stringify(existing) === JSON.stringify(filter))) state.filters.push(filter);
        state.errors = []; render(); remember();
      });
    }
    function render() {
      byId('keywords').value = state.query; byId('group').value = state.group;
      notice.hidden = !state.errors.length; notice.textContent = state.errors.join(' ');
      byId('add').disabled = fields.length === 0 || state.errors.length > 0; byId('group').disabled = state.errors.length > 0;
      byId('chips').innerHTML = state.filters.map((filter, index) => `<span class="explorer-chip"><button type="button" data-edit-filter="${index}" aria-label="Edit filter: ${escape(filterLabel(filter))}">${escape(filterLabel(filter))}</button><button type="button" class="explorer-chip-remove" data-remove-filter="${index}" aria-label="Remove filter: ${escape(filterLabel(filter))}">×</button></span>`).join('');
      for (const button of byId('chips').querySelectorAll('[data-remove-filter]')) button.addEventListener('click', () => { state.filters.splice(Number(button.dataset.removeFilter), 1); editing = null; renderEditor(); render(); remember(); });
      for (const button of byId('chips').querySelectorAll('[data-edit-filter]')) button.addEventListener('click', () => { editing = Number(button.dataset.editFilter); const filter = state.filters[editing]; editor = {field: filter.field, op: filter.op, value: filter.value}; openEditor(true); renderEditor(); });
      const rows = E.matching(catalog, state), cases = rows.filter(row => row.item.case_id !== null).length, planned = rows.length - cases;
      const count = rows.reduce((sum, row) => sum + row.contexts.filter(Boolean).length, 0);
      byId('count').textContent = `${cases} matching case${cases === 1 ? '' : 's'}${planned ? ` · ${planned} planned benchmark${planned === 1 ? '' : 's'}` : ''} · ${count} retained configuration${count === 1 ? '' : 's'}`;
      const results = byId('results');
      if (!rows.length) results.innerHTML = `<p class="explorer-empty">${state.errors.length ? 'Correct or clear the saved query to show results.' : 'No benchmark case matches this query. Remove a filter or broaden the keywords.'}</p>`;
      else if (state.group === 'none') results.innerHTML = table(rows);
      else {
        const field = fieldMap.get(state.group);
        results.innerHTML = E.grouped(rows, field).map(group => `<h2 class="explorer-group-heading">${escape(field.label)}: ${escape(group.label)} · ${group.rows.length} cases</h2>${table(group.rows)}`).join('');
        if (field.scope === 'capture' || field.type === 'tags') results.insertAdjacentHTML('beforeend', '<p class="explorer-group-note">A case can appear in more than one group. Counts above include each case once.</p>');
      }
      for (const button of results.querySelectorAll('[data-explorer-case]')) button.addEventListener('click', () => { selected = button.dataset.explorerCase; inspect(rows.find(row => E.identity(row.item) === selected)); byId('inspector').scrollIntoView({block: 'start'}); });
      const row = rows.find(row => E.identity(row.item) === selected);
      if (row) inspect(row); else { selected = null; byId('inspector').hidden = true; }
    }
    byId('keywords').addEventListener('input', event => { state.query = event.target.value; render(); remember(true); });
    byId('add').addEventListener('click', () => { editing = null; defaultEditor(fields[0]); openEditor(builder.hidden); renderEditor(); });
    byId('cancel').addEventListener('click', () => { editing = null; openEditor(false); });
    byId('clear').addEventListener('click', () => { state = {query: '', filters: [], group: 'none', errors: []}; selected = editing = null; openEditor(false); render(); remember(); });
    fieldSelect.addEventListener('change', () => { defaultEditor(fieldMap.get(fieldSelect.value)); renderEditor(); });
    opSelect.addEventListener('change', () => { const field = fieldMap.get(editor.field), value = byId('value')?.value; if (value !== undefined) editor.value = field.type === 'quantity' ? {value, unit: byId('unit').value} : value; editor.op = opSelect.value; renderEditor(); });
    builder.addEventListener('submit', event => {
      event.preventDefault(); const field = fieldMap.get(editor.field), filter = {field: field.id, op: editor.op};
      if (!['has', 'missing', 'na'].includes(filter.op)) {
        const value = byId('value')?.value;
        if (value !== undefined && value.trim() !== '') filter.value = field.type === 'number' ? Number(value) : field.type === 'quantity' ? {value: Number(value), unit: byId('unit').value} : field.type === 'boolean' ? value === 'true' : value;
      }
      if (!E.validFilter(filter, fields) || (editing === null && state.filters.length >= 20)) { byId('error').textContent = 'Enter a valid value in the declared field type and units; at most 20 filters are supported.'; byId('error').hidden = false; return; }
      if (editing === null) state.filters.push(filter); else state.filters[editing] = filter;
      state.errors = []; editing = null; openEditor(false); render(); remember();
    });
    byId('group').addEventListener('change', () => { state.group = byId('group').value; render(); remember(); });
    addEventListener('popstate', () => { state = E.decode(location.search, fields); selected = editing = null; openEditor(false); render(); });
    render();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
}
