// Shared αLoop theme and global navigation for hosted pages and offline reports.
(() => {
  const root = document.documentElement;
  const themeButton = document.querySelector("[data-theme-toggle]");
  const themeColor = document.querySelector('meta[name="theme-color"]');
  const preferred = matchMedia("(prefers-color-scheme: dark)");
  const storageKey = "alphal00p-docs-theme";
  let choice;
  try {
    const stored = localStorage.getItem(storageKey);
    if (stored === "light" || stored === "dark") choice = stored;
  } catch { /* Storage can be disabled on local files. */ }

  function applyTheme() {
    const theme = choice || (preferred.matches ? "dark" : "light");
    root.dataset.theme = theme;
    const dark = theme === "dark";
    themeButton?.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    themeButton?.setAttribute("aria-pressed", String(dark));
    themeColor?.setAttribute("content", dark ? "#211a23" : "#f9f6f0");
  }

  applyTheme();
  preferred.addEventListener("change", applyTheme);
  themeButton?.addEventListener("click", () => {
    choice = root.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem(storageKey, choice); } catch { /* Use the selection for this page. */ }
    applyTheme();
  });

  const menuButton = document.querySelector("[data-portal-menu-toggle]");
  const nav = document.getElementById("bench-navigation");
  const main = document.getElementById("main-content");
  if (!menuButton || !nav || !main) return;
  root.classList.add("js");
  const narrow = matchMedia("(max-width: 52rem)");
  const footer = document.querySelector(".portal-footer");
  const backdrop = document.createElement("button");
  backdrop.type = "button";
  backdrop.className = "bench-drawer-backdrop";
  backdrop.tabIndex = -1;
  backdrop.setAttribute("aria-label", "Close navigation");
  document.body.append(backdrop);

  function setOpen(requested) {
    const open = requested && narrow.matches;
    document.body.classList.toggle("bench-drawer-open", open);
    backdrop.hidden = !open;
    nav.hidden = narrow.matches && !open;
    main.inert = open;
    if (footer) footer.inert = open;
    menuButton.setAttribute("aria-expanded", String(open));
    menuButton.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
    menuButton.querySelector("span").textContent = open ? "" : "☰";
  }

  function navItems() {
    const links = nav.querySelectorAll(":scope > a[href], summary, details[open] a[href]");
    return [...links].filter(item => item.getClientRects().length);
  }

  function focusSelection() {
    const selected = nav.querySelector(':scope > [aria-current="page"]');
    (selected || navItems()[0])?.focus({ preventScroll: true });
  }

  function dismiss() {
    setOpen(false);
    menuButton.focus({ preventScroll: true });
  }

  setOpen(false);
  narrow.addEventListener("change", () => {
    const focused = document.activeElement;
    setOpen(false);
    if (narrow.matches && nav.contains(focused)) menuButton.focus({ preventScroll: true });
    else if (!narrow.matches && (focused === menuButton || (nav.contains(focused) && !focused.getClientRects().length))) focusSelection();
  });
  menuButton.addEventListener("click", () => {
    if (!backdrop.hidden) return dismiss();
    nav.querySelector(".bench-site-links").open = false;
    setOpen(true);
    nav.scrollTop = 0;
    focusSelection();
  });
  backdrop.addEventListener("click", dismiss);
  document.addEventListener("click", event => {
    if (backdrop.hidden || menuButton.contains(event.target)) return;
    if (nav.contains(event.target)) {
      if (event.target.closest("a[href]")) dismiss();
    } else setOpen(false);
  });
  document.addEventListener("keydown", event => {
    if (backdrop.hidden) return;
    if (event.key === "Escape") {
      dismiss();
      event.preventDefault();
    } else if (event.key === "Tab") {
      const last = navItems().at(-1);
      if (last && event.shiftKey && document.activeElement === menuButton) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        menuButton.focus();
      }
    }
  });
})();
