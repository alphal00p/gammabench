"""The published artifact must retain every relative report/evidence link."""
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import json
import re
import subprocess
import shutil
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('href', 'src') and value:
                self.targets.append(value)


class PagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.site = Path(cls.temporary.name) / 'site'
        subprocess.run(['bash', str(ROOT / 'scripts/build-pages.sh'), str(cls.site)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_all_local_site_links_resolve_within_published_artifact(self):
        checked = 0
        for page in self.site.rglob('*.html'):
            parser = Links()
            parser.feed(page.read_text())
            for target in parser.targets:
                url = urlsplit(target)
                if url.scheme or url.netloc or not url.path:
                    continue
                relative = unquote(url.path)
                path = ((self.site / relative.removeprefix('/gammabench/'))
                        if relative.startswith('/gammabench/') else page.parent / relative).resolve()
                self.assertTrue(path.is_relative_to(self.site.resolve()), (page, target))
                self.assertTrue(path.exists(), (page, target))
                checked += 1
        self.assertGreater(checked, 1000)

    def test_only_published_reports_evidence_and_catalog_are_included(self):
        self.assertTrue((self.site / 'index.html').is_file())
        self.assertTrue((self.site / '.nojekyll').is_file())
        self.assertTrue((self.site / 'artifacts.json').is_file())
        self.assertTrue((self.site / 'snapshots/historical').is_dir())
        for name in ('src', 'suites', '.git', '.venv', '.gammabench', 'tools.lock.json'):
            self.assertFalse((self.site / name).exists(), name)
        self.assertFalse(any(path.is_symlink() for path in self.site.rglob('*')))

    def test_pinned_theme_and_local_css_assets_are_complete(self):
        theme = self.site / 'assets/theme'
        provenance = json.loads((theme / 'provenance.json').read_text())
        self.assertEqual(provenance['revision'], '2e22d46aae9aa987aada970474cec266976cde5b')
        for asset in provenance['assets']:
            if 'vendored_path' not in asset:
                continue
            path = theme / asset['vendored_path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), asset['sha256'])
        for css in self.site.rglob('*.css'):
            for raw in re.findall(r'url\(([^)]+)\)', css.read_text()):
                url = urlsplit(raw.strip('"\' '))
                if url.scheme or url.netloc or not url.path:
                    continue
                path = (css.parent / unquote(url.path)).resolve()
                self.assertTrue(path.is_relative_to(self.site.resolve()), (css, raw))
                self.assertTrue(path.is_file(), (css, raw))
        self.assertTrue((theme / 'upstream-LICENSE').is_file())
        self.assertTrue((theme / 'STIX-Two-OFL.txt').is_file())
        page = (self.site / 'index.html').read_text()
        self.assertIn('assets/theme/site.css', page)
        self.assertIn('data-theme-toggle', page)
        self.assertNotIn('<style>', page)

    def test_registry_builds_separate_benchmark_workspaces(self):
        registry = json.loads((ROOT / 'web/benchmarks.json').read_text())
        self.assertEqual(json.loads((self.site / 'assets/benchmarks.json').read_text()), registry)
        self.assertTrue((self.site / 'assets/workspace.js').is_file())
        for benchmark in registry['benchmarks']:
            page = self.site / 'benchmarks' / benchmark['id'] / 'index.html'
            html = page.read_text()
            self.assertIn(f'data-default-benchmark="{benchmark["id"]}"', html)
            for tab in ('history', 'compare', 'evidence'):
                self.assertIn(f'data-workspace-panel="{tab}"', html)
            own_snapshots = set()
            for path in benchmark['analyses']:
                own_snapshots.update(snapshot['id'] for snapshot in json.loads((ROOT / path).read_text())['snapshots'])
            displayed_studies = set(re.findall(r'class="history-series"[^>]*data-study="([^"]+)"', html))
            if benchmark['analyses']:
                self.assertTrue(displayed_studies)
                self.assertTrue(displayed_studies <= own_snapshots)
                self.assertTrue('id="comparison-matrix"' in html, benchmark['id'])
            else:
                self.assertFalse(displayed_studies)
                self.assertIn('No measurements yet', html)
                self.assertNotIn('class="matrix-cell"', html)
            for other in registry['benchmarks']:
                self.assertIn(f'data-href="../../benchmarks/{other["id"]}/index.html"', html)
        default = registry['default_benchmark']
        for path in ('index.html', 'docs/results/index.html'):
            self.assertIn(f'data-default-benchmark="{default}"', (self.site / path).read_text())
        self.assertIn('Planned', (self.site / 'index.html').read_text())

    def test_scaffolded_benchmark_has_empty_workspace_without_fabricated_measurements(self):
        from gammabench.scaffold import create
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'checkout'
            root.mkdir()
            for name in ('scripts', 'web', 'docs/results', 'snapshots', 'src/gammabench/assets'):
                shutil.copytree(ROOT / name, root / name)
            (root / 'nix').mkdir()
            (root / 'flake.nix').write_text('{}')
            shutil.copyfile(ROOT / 'artifacts.json', root / 'artifacts.json')
            create(root, 'planned-operation', family='tensor-contraction')
            destination = Path(temporary) / 'site'
            subprocess.run(['bash', str(root / 'scripts/build-pages.sh'), str(destination)],
                           check=True, capture_output=True)
            page = (destination / 'benchmarks/planned-operation/index.html').read_text()
            self.assertIn('No measurements yet', page)
            self.assertIn('data-default-study="planned:planned-operation"', page)
            self.assertNotIn('class="history-point"', page)
            self.assertNotIn('class="matrix-cell"', page)
            for tab in ('history', 'compare', 'evidence'):
                self.assertIn(f'data-workspace-panel="{tab}"', page)
            self.assertTrue('Planned operation · planned' in (destination / 'index.html').read_text(), 'planned benchmark appears in the sidebar')
            self.assertIn('data-default-benchmark="full-diagrams"', (destination / 'index.html').read_text())

    def test_existing_destination_and_source_destinations_are_protected(self):
        old = (self.site / 'index.html').read_bytes()
        result = subprocess.run(['bash', str(ROOT / 'scripts/build-pages.sh'), str(self.site)],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.site / 'index.html').read_bytes(), old)
        forbidden = ROOT / 'snapshots/historical/new-pages-site'
        result = subprocess.run(['bash', str(ROOT / 'scripts/build-pages.sh'), str(forbidden)],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(forbidden.exists())
