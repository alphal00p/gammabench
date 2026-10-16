/* Keep old bookmark selections while retiring the dedicated browsing pages. */
(() => {
  const node = document.getElementById('list-redirect');
  if (!node) return;
  const target = new URL(node.dataset.target, location.href);
  if (target.origin !== location.origin) return;
  const defaults = JSON.parse(node.dataset.defaults);
  for (const [key, value] of Object.entries(defaults)) target.searchParams.set(key, value);
  for (const [key, value] of new URLSearchParams(location.search)) target.searchParams.set(key, value);
  target.hash = location.hash;
  location.replace(target.href);
})();
