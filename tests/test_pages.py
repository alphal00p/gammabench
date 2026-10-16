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
        self.catalog = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id") == "benchmark-explorer":
            self.catalog = json.loads(attributes["data-catalog"])
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

    def test_list_retires_benchmark_pages_and_preserves_selection_redirects(self):
        from test_inline_list import ListElements
        registry = json.loads((ROOT / 'web/benchmarks.json').read_text())
        self.assertEqual(json.loads((self.site / 'assets/benchmarks.json').read_text()), registry)
        for benchmark in registry['benchmarks']:
            page = self.site / 'benchmarks' / benchmark['id'] / 'index.html'
            html = page.read_text()
            self.assertEqual(ListElements(html).redirect,
                             {'target': '../../index.html', 'defaults': {'benchmark': benchmark['id']}})
            self.assertNotIn('data-workspace-panel', html)
            self.assertNotIn('workspace-sidebar', html)
        for path in ('index.html', 'docs/results/index.html'):
            listing = ListElements((self.site / path).read_text())
            self.assertEqual({case['benchmark_id'] for case in listing.catalog['cases']},
                             {benchmark['id'] for benchmark in registry['benchmarks']})
            self.assertTrue(any(not case['captures'] for case in listing.catalog['cases']))
            self.assertTrue(all(case.get('tags') for case in listing.catalog['cases']))
            self.assertLess(listing.ids.index('list-pins'), listing.ids.index('explorer-controls'))
            self.assertLess(listing.ids.index('list-pins'), listing.ids.index('explorer-results'))
            self.assertTrue(listing.model['records'])
            self.assertEqual(len({r['id'] for r in listing.model['records']}), len(listing.model['records']))
            for record in listing.model['records']:
                for target in (record['report'], *record['evidence'].values()):
                    resolved = (self.site / target).resolve()
                    self.assertTrue(resolved.is_relative_to(self.site.resolve()))
                    self.assertTrue(resolved.is_file(), target)
        for asset in ('explorer.js', 'explorer.css', 'list.js', 'list.css', 'list-redirect.js', 'metadata.json'):
            self.assertTrue((self.site / 'assets' / asset).is_file())

    def test_ci_history_selects_candidates_and_inline_compare_retains_both_sides(self):
        from test_inline_list import ListElements
        registry = json.loads((ROOT / 'web/benchmarks.json').read_text())
        entries = [item for item in registry['benchmarks'] if item['family'] == 'gammaloop-ci' and item['analyses']]
        model = ListElements((self.site / 'index.html').read_text()).model
        for entry in entries:
            retained = [r for r in model['records'] if r['benchmark'] == entry['id']]
            history = [r for r in retained if r['history']]
            self.assertTrue(history)
            self.assertTrue(all(r['variant'] == 'candidate' and r['phase'] == 'discovery' for r in history))
            self.assertTrue({'baseline', 'candidate'} <= {r['variant'] for r in retained})
            originals = {item['id']: item for path in entry['analyses']
                         for item in json.loads((ROOT / path).read_text())['series']}
            for record in retained:
                for frozen, original in (('median', 'median'), ('min', 'minimum'), ('max', 'maximum'), ('count', 'count')):
                    self.assertEqual(record[frozen], originals[record['id']][original])
            self.assertTrue(all(r['runId'] and r['runAttempt'] for r in history))
        redirect = ListElements((self.site / 'ci-history/index.html').read_text()).redirect
        self.assertEqual(redirect['defaults'], {'tag': 'gammaloop-ci', 'tab': 'history'})

    def test_scaffolded_benchmark_has_empty_workspace_without_fabricated_measurements(self):
        from gammabench.scaffold import create
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'checkout'
            root.mkdir()
            for name in ('scripts', 'web', 'docs/results', 'snapshots', 'src', 'suites'):
                shutil.copytree(ROOT / name, root / name)
            (root / 'nix').mkdir()
            (root / 'flake.nix').write_text('{}')
            shutil.copyfile(ROOT / 'artifacts.json', root / 'artifacts.json')
            create(root, 'planned-operation', family='tensor-contraction')
            destination = Path(temporary) / 'site'
            subprocess.run(['bash', str(root / 'scripts/build-pages.sh'), str(destination)],
                           check=True, capture_output=True)
            from test_inline_list import ListElements
            page = (destination / 'benchmarks/planned-operation/index.html').read_text()
            self.assertEqual(ListElements(page).redirect['defaults'], {'benchmark': 'planned-operation'})
            listing = ListElements((destination / 'index.html').read_text())
            self.assertFalse(any(r['benchmark'] == 'planned-operation' for r in listing.model['records']))
            parser = Links()
            parser.feed((destination / 'index.html').read_text())
            planned = [case for case in parser.catalog['cases'] if case['benchmark_id'] == 'planned-operation']
            self.assertEqual(len(planned), 1)
            self.assertEqual(planned[0]['captures'], [])
            self.assertTrue(planned[0]['planned'])
            self.assertTrue(any(case['benchmark_id'] == 'full-diagrams' for case in parser.catalog['cases']))

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
