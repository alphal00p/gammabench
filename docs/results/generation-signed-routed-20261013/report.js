/* Offline display only: all qualified statistics come from analysis.json. */
(() => {
  const ids = ['search', 'case', 'baseline', 'variant', 'metric', 'status'];
  const controls = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
  const tables = [
    ['measurements', 'measurement-count'],
    ['comparisons', 'comparison-count'],
    ['availability', 'availability-count'],
  ].map(([id, counter]) => ({id, counter: document.getElementById(counter),
                            rows: [...document.querySelectorAll('#' + id + ' tbody tr')]}));

  function restoreQuery() {
    const params = new URLSearchParams(window.location.search);
    for (const [id, control] of Object.entries(controls)) {
      const value = params.get(id);
      if (value !== null && (id === 'search' || [...control.options].some(option => option.value === value))) {
        control.value = value;
      }
    }
  }

  function filter(updateQuery = false) {
    const query = controls.search.value.toLowerCase();
    const selected = Object.fromEntries(ids.slice(1).map(id => [id, controls[id].value]));
    for (const table of tables) {
      for (const row of table.rows) {
        const matches = Object.entries(selected).every(([key, value]) => {
          if (!value) return true;
          if (key === 'baseline') {
            if (table.id !== 'comparisons') return true;
            const separator = value.indexOf(':');
            const kind = value.slice(0, separator);
            const label = value.slice(separator + 1);
            return row.dataset[kind === 'engine' ? 'baselineEngine' : 'baselineVariant'] === label;
          }
          // Availability belongs to a task/stage, not a successful metric sample.
          if (key === 'metric' && table.id === 'availability') return true;
          return row.dataset[key] === value;
        });
        row.hidden = !matches || !row.textContent.toLowerCase().includes(query);
      }
      table.counter.textContent = '(' + table.rows.filter(row => !row.hidden).length + ' / ' + table.rows.length + ')';
    }
    if (updateQuery) {
      const url = new URL(window.location.href);
      for (const [id, control] of Object.entries(controls)) {
        if (control.value) url.searchParams.set(id, control.value);
        else url.searchParams.delete(id);
      }
      // Keep an explicitly selected "all" baseline when opening this URL again.
      if (!controls.baseline.value) url.searchParams.set('baseline', '');
      window.history.replaceState(null, '', url);
    }
  }

  restoreQuery();
  for (const control of Object.values(controls)) control.addEventListener('input', () => filter(true));
  filter();
})();
