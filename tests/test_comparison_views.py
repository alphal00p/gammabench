"""Version columns and capture selection retain the stored analysis boundaries."""
import copy
from html.parser import HTMLParser
from importlib import resources
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from gammabench.metadata import project_implementation
from gammabench.report import analyze
from gammabench.snapshot import load
import test_report as report_fixtures
from test_history_context import nix_capture


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class ComparisonViewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = report_fixtures.ReportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.compilations = 0
        assets = resources.files('gammabench').joinpath('assets')
        for name in ('matrix.typ', 'history.typ', 'components.typ', 'report.css', 'report.js'):
            (self.root / name).write_bytes(assets.joinpath(name).read_bytes())
        shutil.copytree(assets.joinpath('theme'), self.root / 'theme')
        (self.root / 'workspace.js').write_bytes((Path(__file__).resolve().parents[1] / 'web/workspace.js').read_bytes())

    def capture(self, date='2026-10-14T12:00:00Z'):
        def recorded_date(snapshot, suite):
            snapshot['created_at'] = date
        path = nix_capture(self.fixture, paired=True, change=recorded_date)
        snapshot = load(path)
        analysis = analyze(path)
        for item in analysis['series']:
            item['implementation'] = project_implementation(snapshot, item, values={'implementation.way': 'evaluate'})
        return analysis

    def compile(self, captures):
        self.compilations += 1
        data = self.root / f'captures-{self.compilations}.json'
        before = json.dumps(captures, sort_keys=True)
        data.write_text(before)
        study = captures[0]['snapshots'][0]['id']
        template = self.root / f'workspace-{self.compilations}.typ'
        template.write_text('''#import "components.typ": el, bench-document
#import "matrix.typ": comparison-matrix
#import "history.typ": benchmark-history
#let captures = json("''' + data.name + '''")
#let combined = captures.first() + (
  snapshots: captures.map(capture => capture.snapshots).flatten(),
  series: captures.map(capture => capture.series).flatten(),
  comparisons: captures.map(capture => capture.comparisons).flatten(),
  availability: captures.map(capture => capture.availability).flatten(),
)
#let sets = captures.enumerate().map(((index, capture)) => (id: "set-" + str(index), label: "Recorded capture " + str(index), analysis: capture))
#bench-document("Version fixture", "Test measurements only", home: "index.html", interactive: true, scripts: ("workspace.js",))[
#el("div", attrs: (id: "benchmark-workspace", "data-default-benchmark": "fixture", "data-default-study": "''' + study + '''", "data-default-tab": "compare"))[
#el("a", attrs: ("data-benchmark-id": "fixture", "data-study": "''' + study + '''", "data-title": "Version fixture", "data-default-metric": "completion_ns"), [Fixture])
#el("nav", ("history", "compare", "evidence").map(tab => el("button", attrs: (type: "button", "data-workspace-tab": tab), tab)).join())
#el("section", attrs: ("data-workspace-panel": "compare"))[#comparison-matrix((combined,), comparison-sets: sets)]
#el("section", attrs: ("data-workspace-panel": "history"))[#benchmark-history((combined,))]
#el("section", attrs: ("data-workspace-panel": "evidence"), [Captured evidence])
]]
''')
        output = self.root / f'workspace-{self.compilations}.html'
        compiled = subprocess.run(['typst', 'compile', '--root', str(self.root), '--features', 'html',
                                   '--format', 'html', str(template), str(output)], capture_output=True, text=True)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        self.assertEqual(data.read_text(), before)
        self.assertEqual(json.dumps(captures, sort_keys=True), before)
        return output, Elements(output.read_text())

    def test_same_tool_and_way_keep_distinct_source_versions(self):
        capture = self.capture()
        _, document = self.compile([capture])
        versions = [attrs for tag, attrs in document.elements if tag == 'th' and 'data-version' in attrs]
        self.assertEqual({item['data-way'] for item in versions}, {'evaluate'})
        self.assertEqual({item['data-version-label'] for item in versions}, {'a' * 10, 'b' * 10})
        self.assertEqual(len({item['data-version'] for item in versions}), 2)
        self.assertEqual(len({item['data-run'] for item in versions}), 2)
        self.assertTrue(all(item['data-created-at'] == '2026-10-14T12:00:00Z' for item in versions))
        self.assertTrue(any('data-tool-group' in attrs for _, attrs in document.elements))
        self.assertTrue(any('data-way-group' in attrs for _, attrs in document.elements))

    def test_independent_latest_view_contains_no_precomputed_pair_markers(self):
        first = self.capture()
        second = self.capture('2026-10-15T12:00:00Z')
        _, document = self.compile([first, second])
        views = [attrs for _, attrs in document.elements if attrs.get('class') == 'matrix-view']
        self.assertEqual({item['data-set'] for item in views}, {'set-0', 'set-1', 'latest:' + first['snapshots'][0]['id']})
        self.assertTrue(any(item['data-independent'] == 'true' for item in views))
        markers = [attrs for _, attrs in document.elements if attrs.get('class') == 'matrix-comparison']
        self.assertEqual(len(markers), sum(len(capture['comparisons']) for capture in (first, second)))
        retained = {(pair['baseline'], pair['candidate']): pair for capture in (first, second) for pair in capture['comparisons']}
        for marker in markers:
            pair = retained[(marker['data-reference-series'], marker['data-candidate-series'])]
            self.assertEqual(float(marker['data-ratio']), pair['ratio']['median'])
            self.assertEqual(int(marker['data-pairs']), pair['ratio']['count'])

    def test_task_ways_and_tool_labels_are_literal_and_never_merge_columns(self):
        capture = self.capture()
        for item in capture['series']:
            item['variant'] = 'same-source-role'
            item['implementation']['way_id'] = item['task']
            item['implementation']['way_label'] = 'Map <script>unsafe</script>' if item['task'].startswith('candidate.') else 'Iterative'
            item['implementation']['tool_label'] = 'GammaLoop <b>literal</b>'
        output, document = self.compile([capture])
        columns = [attrs for tag, attrs in document.elements if tag == 'th' and 'data-version' in attrs]
        self.assertEqual(len({item['data-run'] for item in columns}), 2)
        self.assertEqual(len({item['data-way'] for item in columns}), 2)
        self.assertFalse(any(tag == 'script' and 'src' not in attrs for tag, attrs in document.elements))
        self.assertFalse(any(tag == 'b' for tag, _ in document.elements))
        self.assertIn('&lt;script>unsafe&lt;/script>', output.read_text())

    def test_archive_and_unknown_source_columns_render_without_invented_commit_attributes(self):
        capture = self.capture()
        for item in capture['series']:
            implementation = item['implementation']
            implementation.update(revision=None, repository='', source_kind='archive' if item['variant'] == 'parent' else 'unknown',
                                  version_label='Archive 1.2' if item['variant'] == 'parent' else 'Source not identified')
        output, document = self.compile([capture])
        columns = [attrs for tag, attrs in document.elements if tag == 'th' and 'data-version' in attrs]
        self.assertTrue(columns)
        self.assertTrue(all(item['data-revision'] in ('', None) and item['data-repository'] in ('', None) for item in columns))
        self.assertIn('Archive 1.2', output.read_text())
        self.assertIn('Source not identified', output.read_text())

    def test_preserved_generation_capture_renders_unknown_archive_engine_sources(self):
        repository = Path(__file__).resolve().parents[1]
        source = repository / 'docs/results/generation-count-20261011/analysis.json'
        original = source.read_bytes()
        capture = json.loads(original)
        snapshots = {snapshot['id']: snapshot for snapshot in capture['snapshots']}
        for item in capture['series']:
            item['implementation'] = project_implementation(snapshots[item['snapshot_id']], item,
                                                            values={'implementation.way': 'default'})
        self.assertTrue(any(item['engine'] in ('qgraf', 'nauty') and item['implementation']['revision'] is None
                            for item in capture['series']))
        _, document = self.compile([capture])
        columns = [attrs for tag, attrs in document.elements if tag == 'th' and attrs.get('data-tool') in ('qgraf', 'nauty')]
        self.assertTrue(columns)
        self.assertTrue(all(column['data-revision'] in ('', None) for column in columns))
        self.assertEqual(source.read_bytes(), original)

    def test_profiles_from_other_metrics_do_not_create_empty_timing_columns(self):
        capture = self.capture()
        profile = copy.deepcopy(next(item for item in capture['series'] if item['metric'] == 'process_wall_ns'))
        profile['id'] += '-profile'
        profile['task'] += '-profile'
        profile['implementation']['way_id'] = 'profile-only'
        profile['implementation']['way_label'] = 'Profile only'
        capture['series'].append(profile)
        output, _ = self.compile([capture])
        class ScopedColumns(HTMLParser):
            def __init__(self):
                super().__init__(); self.metric = None; self.columns = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get('class') == 'matrix-view': self.metric = attrs['data-metric']
                if tag == 'th' and 'data-way' in attrs: self.columns.append((self.metric, attrs['data-way']))
        document = ScopedColumns(); document.feed(output.read_text())
        self.assertNotIn(('completion_ns', 'profile-only'), document.columns)
        self.assertIn(('process_wall_ns', 'profile-only'), document.columns)

    def test_explicit_status_labels_and_retired_controls_leave_undeclared_data_unlabelled(self):
        capture = self.capture()
        output, _ = self.compile([capture])
        self.assertNotIn('id="matrix-include-retired"', output.read_text())
        self.assertNotIn('id="history-include-retired"', output.read_text())
        for item in capture['series']:
            retired = item['variant'] == 'parent'
            item['metadata'] = {'implementation.status': 'retired' if retired else 'experimental'}
            item['implementation'].update(way_id='retired-way' if retired else 'experimental-way')
        output, document = self.compile([capture])
        columns = [attrs for tag, attrs in document.elements if tag == 'th' and 'data-status-label' in attrs]
        self.assertEqual({column['data-status-label'] for column in columns}, {'Retired', 'Experimental'})
        self.assertTrue(all(column['data-consistently-retired'] == ('true' if column['data-status-label'] == 'Retired' else 'false') for column in columns))
        self.assertIn('id="matrix-include-retired"', output.read_text())
        self.assertIn('id="history-include-retired"', output.read_text())
        self.assertIn('Declared status: retired', output.read_text())

    def test_conflicting_case_statuses_cannot_retire_an_active_version_column(self):
        capture = self.capture()
        for item in capture['series']:
            if item['variant'] == 'parent': item['metadata'] = {'implementation.status': 'retired'}
        for item in list(capture['series']):
            if item['variant'] != 'parent': continue
            active = copy.deepcopy(item)
            active.update(id=item['id'] + '-active-case', case='another-case', metadata={'implementation.status': 'active'})
            capture['series'].append(active)
        _, document = self.compile([capture])
        columns = [attrs for tag, attrs in document.elements if tag == 'th' and attrs.get('data-revision') == 'a' * 40]
        self.assertTrue(columns)
        self.assertTrue(all(column['data-consistently-retired'] == 'false' for column in columns))
        self.assertTrue(all('active' in column['data-status-label'] and 'retired' in column['data-status-label'] for column in columns))

    def test_unavailable_case_is_retained_inside_its_exact_tool_column(self):
        capture = self.capture()
        item = capture['series'][0]
        capture['availability'].append({'snapshot_id': item['snapshot_id'], 'case': item['case'], 'engine': item['engine'],
                                        'variant': item['variant'], 'task': item['task'], 'status': 'censored',
                                        'stage': 'qualification', 'reason': 'Retained exact-case timeout', 'origin': 'measured'})
        output, document = self.compile([capture])
        cells = [attrs for _, attrs in document.elements if attrs.get('class') == 'matrix-cell-availability']
        self.assertTrue(cells)
        self.assertIn('Retained exact-case timeout', output.read_text())


if __name__ == '__main__':
    unittest.main()
