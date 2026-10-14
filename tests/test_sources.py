import hashlib
import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from gammabench.sources import resolve_tools, verify_tools


class SourcesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def git_source(self):
        source = self.root / 'repo'
        source.mkdir()
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        (source / 'input.txt').write_text('original\n')
        subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=lcnbr',
                        '-c', 'user.email=im@lcnbr.ch', 'commit', '-qm', 'fixture'], check=True)
        revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        return source, {'kind': 'git', 'url': source.as_uri(), 'revision': revision}

    def test_exact_pin_and_changed_content_guard(self):
        source, pin = self.git_source()
        bindings = resolve_tools({'engine': pin}, ['engine'], cache_dir=self.root / 'cache',
                                 overrides={'engine': str(source)})
        verify_tools(bindings)
        (source / 'input.txt').write_text('changed\n')
        with self.assertRaisesRegex(ValueError, 'cleanliness changed'):
            verify_tools(bindings)
        with self.assertRaisesRegex(ValueError, 'Dirty source'):
            resolve_tools({'engine': pin}, ['engine'], cache_dir=self.root / 'cache',
                          overrides={'engine': str(source)})

    def test_dirty_override_captures_actual_content(self):
        source, pin = self.git_source()
        (source / 'input.txt').write_text('local experiment\n')
        bindings = resolve_tools({'engine': pin}, ['engine'], cache_dir=self.root / 'cache',
                                 overrides={'engine': str(source)}, allow_dirty=True)
        binding = bindings['engine']
        self.assertTrue(binding['dirty'])
        with tarfile.open(binding['source_archive']) as archive:
            self.assertEqual(archive.extractfile('input.txt').read(), b'local experiment\n')
        verify_tools(bindings)
        (source / 'input.txt').write_text('another edit\n')
        with self.assertRaisesRegex(ValueError, 'content changed'):
            verify_tools(bindings)

    def test_unselected_engine_does_not_need_installation(self):
        pins = {'python': {'kind': 'executable', 'path': sys.executable, 'version': 'Python'},
                'absent': {'kind': 'executable', 'path': '/missing/tool', 'version': '1'}}
        bindings = resolve_tools(pins, ['python'], cache_dir=self.root / 'cache')
        self.assertEqual(set(bindings), {'python'})
        verify_tools(bindings)

    def test_wrong_executable_hash_is_rejected(self):
        pin = {'kind': 'executable', 'path': sys.executable, 'version': 'Python', 'sha256': '0' * 64}
        with self.assertRaisesRegex(ValueError, 'hash does not match'):
            resolve_tools({'python': pin}, ['python'], cache_dir=self.root / 'cache')

    def test_archive_escape_is_rejected(self):
        archive = self.root / 'bad.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            member = tarfile.TarInfo('../escape')
            member.size = 4
            output.addfile(member, io.BytesIO(b'oops'))
        pin = {'kind': 'archive', 'url': archive.as_uri(), 'version': '1',
               'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
        with self.assertRaisesRegex(ValueError, 'escaping path'):
            resolve_tools({'bad': pin}, ['bad'], cache_dir=self.root / 'cache')
        self.assertFalse((self.root / 'escape').exists())

    def test_local_git_fetch_and_pin_mismatch(self):
        source, pin = self.git_source()
        result = resolve_tools({'engine': pin}, ['engine'], cache_dir=self.root / 'cache')
        self.assertNotEqual(result['engine']['source_path'], str(source))
        verify_tools(result)
        wrong = {**pin, 'revision': '0' * 40}
        with self.assertRaisesRegex(ValueError, 'revision mismatch'):
            resolve_tools({'engine': wrong}, ['engine'], cache_dir=self.root / 'cache',
                          overrides={'engine': str(source)})

    def test_cached_extraction_must_still_match_pinned_archive(self):
        archive = self.root / 'good.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            member = tarfile.TarInfo('source/data.txt')
            member.size = 4
            member.mode = 0o644
            output.addfile(member, io.BytesIO(b'good'))
        pin = {'kind': 'archive', 'url': archive.as_uri(), 'version': '1', 'subdir': 'source',
               'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
        bindings = resolve_tools({'engine': pin}, ['engine'], cache_dir=self.root / 'cache')
        verify_tools(bindings)
        (Path(bindings['engine']['source_path']) / 'data.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'no longer matches'):
            resolve_tools({'engine': pin}, ['engine'], cache_dir=self.root / 'cache')

    def test_cached_dirty_archive_must_match_actual_experiment(self):
        source, pin = self.git_source()
        (source / 'input.txt').write_text('actual experiment')
        options = dict(cache_dir=self.root / 'cache', overrides={'engine': str(source)}, allow_dirty=True)
        bindings = resolve_tools({'engine': pin}, ['engine'], **options)
        archive = Path(bindings['engine']['source_archive'])
        with tarfile.open(archive, 'w:gz') as output:
            member = tarfile.TarInfo('input.txt')
            member.size = 5
            member.mode = 0o644
            output.addfile(member, io.BytesIO(b'wrong'))
        with self.assertRaisesRegex(ValueError, 'does not match source content'):
            resolve_tools({'engine': pin}, ['engine'], **options)

    def test_dirty_snapshot_retains_internal_symlink(self):
        source, pin = self.git_source()
        (source / 'link.txt').symlink_to('input.txt')
        options = dict(cache_dir=self.root / 'cache', overrides={'engine': str(source)}, allow_dirty=True)
        bindings = resolve_tools({'engine': pin}, ['engine'], **options)
        with tarfile.open(bindings['engine']['source_archive']) as archive:
            self.assertTrue(archive.getmember('link.txt').issym())
        verify_tools(bindings)
        resolve_tools({'engine': pin}, ['engine'], **options)
