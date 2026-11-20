# αLoop website theme

The styles, wordmarks and self-hosted fonts come from the redesigned GammaLoop
site at `c8b051ef69188803633e4ee2c5a8b6413de217fa`. The asset manifest
records their upstream paths and SHA-256 hashes. Keep the upstream license,
font licenses and font and logo attribution files with the vendored assets.

The global navigation markup in `../components.typ` uses the same classes as
the group homepage and manual. The local `site.js` handles its menu and color
theme for hosted pages and offline report bundles. It shares the site's
saved theme preference. The upstream navigation sources are listed in the
manifest for reference. Benchmark controls and charts use their own scripts.

For a theme update, copy the files from a single upstream revision, follow
stylesheet imports and font URLs, and update the manifest. Benchmark snapshot
engine pins are independent of the website theme.
