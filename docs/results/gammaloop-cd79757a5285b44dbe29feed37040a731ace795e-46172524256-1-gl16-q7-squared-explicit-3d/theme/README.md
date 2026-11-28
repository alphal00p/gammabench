# GammaLoop Pages theme

These assets are unchanged copies of GammaLoop's public Pages theme at
`2e22d46aae9aa987aada970474cec266976cde5b`. `provenance.json` binds each file
to its upstream path, complete revision and SHA-256. The same revision's
`docs/portal/components.typ` is recorded as the reference for gammabench's
semantic Typst components; its unused components are not copied.

Keep `upstream-LICENSE` and `STIX-Two-OFL.txt` alongside the assets. Theme pins
are independent of measured engine versions in benchmark snapshots.

`site.css` and `site.js` own the palette, typography, responsive portal layout
and theme switch. Benchmark-specific layout overrides live in `../report.css`.
The upstream stylesheet imports Google Fonts for Inter, Newsreader and IBM Plex
Mono; offline viewing uses its system-font fallbacks. The local STIX math font
and all local CSS asset references are bundled.

To update the theme, choose one complete public upstream revision, copy the
listed files unchanged, update their provenance and verify the resulting pages.
