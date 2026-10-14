"""Acquire pinned sources and bind the exact files used by a benchmark."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import urlopen


def file_sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'Git source operation failed ({args[0]}): {result.stderr.strip()}')
    return result.stdout.strip()


def _source_files(path: Path, git: bool) -> list[Path]:
    if git:
        result = subprocess.run(['git', '-C', str(path), 'ls-files', '-z', '--cached',
                                 '--others', '--exclude-standard'], capture_output=True, check=True)
        return sorted({path / os.fsdecode(name) for name in result.stdout.split(b'\0') if name})
    return sorted(p for p in path.rglob('*') if p.is_file() or p.is_symlink())


def _source_digest(path: Path, git: bool = False) -> str:
    digest = hashlib.sha256()
    for item in _source_files(path, git):
        relative = item.relative_to(path).as_posix()
        if item.is_symlink():
            if not item.resolve().is_relative_to(path.resolve()):
                raise ValueError(f'Source symlink escapes captured source: {relative}')
            value, kind = os.readlink(item), 'symlink'
        elif item.is_file():
            value, kind = file_sha256(item), 'executable' if item.stat().st_mode & 0o111 else 'file'
        else:
            raise ValueError(f'Unsupported source entry: {relative}')
        digest.update(json.dumps([relative, kind, value], ensure_ascii=True).encode() + b'\n')
    return digest.hexdigest()


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.username or parsed.password:
        raise ValueError('Source URLs must not embed credentials')
    if parsed.scheme not in ('https', 'http', 'file'):
        raise ValueError('Source URL must use https, http, or file')


def _git_source(tool: dict, cache: Path, override: Path | None, allow_dirty: bool) -> dict:
    revision = tool['revision']
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
        raise ValueError('Git source revisions must be complete immutable commit IDs')
    _validate_url(tool['url'])
    if override:
        source = override.resolve()
        acquisition = 'override'
    else:
        key = hashlib.sha256((tool['url'] + '\0' + revision).encode()).hexdigest()[:20]
        source = cache / ('git-' + key)
        acquisition = 'cache'
        if not source.exists():
            acquisition = 'fetch'
            with tempfile.TemporaryDirectory(prefix='fetch-', dir=cache) as temporary:
                staged = Path(temporary) / 'source'
                staged.mkdir()
                _git(staged, 'init', '--quiet')
                _git(staged, 'remote', 'add', 'origin', tool['url'])
                _git(staged, 'fetch', '--quiet', '--depth=1', 'origin', revision)
                _git(staged, 'checkout', '--quiet', '--detach', 'FETCH_HEAD')
                staged.rename(source)
    actual = _git(source, 'rev-parse', 'HEAD')
    if actual != revision:
        raise ValueError(f'Source revision mismatch: expected {revision}, found {actual}')
    dirty = bool(_git(source, 'status', '--porcelain', '--untracked-files=all'))
    if dirty and not allow_dirty:
        raise ValueError(f'Dirty source rejected: {source}; use --allow-dirty to capture its content')
    content = _source_digest(source, git=True)
    result = {**tool, 'source_path': str(source), 'resolved_revision': actual,
              'source_sha256': content, 'dirty': dirty, 'acquisition': acquisition}
    if dirty:
        archive = cache / f'dirty-{content}.tar.gz'
        if not archive.exists():
            with tarfile.open(archive, 'w:gz') as output:
                for item in _source_files(source, True):
                    output.add(item, arcname=item.relative_to(source).as_posix(), recursive=False)
        if _archive_digest(archive, '.', allow_symlinks=True) != content:
            raise ValueError('Cached dirty source archive does not match source content')
        result.update(source_archive=str(archive), source_archive_sha256=file_sha256(archive))
    return result


def _extract(archive: Path, destination: Path) -> None:
    """Reject links and path escapes before extracting a source archive."""
    with tarfile.open(archive, 'r:*') as source:
        for member in source.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()) or member.name.startswith('/'):
                raise ValueError('Archive contains an escaping path')
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                body = source.extractfile(member)
                if body is None:
                    raise ValueError('Archive file has no content')
                with body, target.open('xb') as output:
                    shutil.copyfileobj(body, output)
                target.chmod(member.mode & 0o777)
            else:
                raise ValueError('Source archives must contain only files and directories')


def _archive_digest(archive: Path, subdir: str, *, allow_symlinks: bool = False) -> str:
    entries = {}
    prefix = PurePosixPath(subdir)
    with tarfile.open(archive, 'r:*') as source:
        for member in source.getmembers():
            if not member.isfile() and not (allow_symlinks and member.issym()):
                continue
            try:
                name = PurePosixPath(member.name).relative_to(prefix).as_posix()
            except ValueError:
                continue
            if name in entries:
                raise ValueError('Source archive contains duplicate files')
            if member.issym():
                entries[name] = ['symlink', member.linkname]
            else:
                body = source.extractfile(member)
                if body is None:
                    raise ValueError('Source archive file has no content')
                with body:
                    value = hashlib.file_digest(body, 'sha256').hexdigest()
                entries[name] = ['executable' if member.mode & 0o111 else 'file', value]
    digest = hashlib.sha256()
    for name, (kind, value) in sorted(entries.items()):
        digest.update(json.dumps([name, kind, value], ensure_ascii=True).encode() + b'\n')
    return digest.hexdigest()


def _archive_source(tool: dict, cache: Path, override: Path | None) -> dict:
    expected = tool['sha256']
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('Archives require an exact SHA-256 digest')
    _validate_url(tool['url'])
    archive = override.resolve() if override else cache / (expected + '.archive')
    if not archive.exists():
        temporary = archive.with_suffix('.partial')
        try:
            with urlopen(tool['url'], timeout=60) as source, temporary.open('xb') as output:
                shutil.copyfileobj(source, output)
            if file_sha256(temporary) != expected:
                raise ValueError('Downloaded archive digest does not match its pin')
            temporary.rename(archive)
        finally:
            temporary.unlink(missing_ok=True)
    if not archive.is_file() or file_sha256(archive) != expected:
        raise ValueError('Provided source archive digest does not match its pin')
    extracted = cache / ('archive-' + expected[:20])
    if not extracted.exists():
        with tempfile.TemporaryDirectory(prefix='extract-', dir=cache) as temporary:
            staged = Path(temporary) / 'source'
            staged.mkdir()
            _extract(archive, staged)
            staged.rename(extracted)
    source = (extracted / tool.get('subdir', '.')).resolve()
    if not source.is_relative_to(extracted.resolve()) or not source.is_dir():
        raise ValueError('Source archive subdirectory is invalid')
    content = _source_digest(source)
    if content != _archive_digest(archive, tool.get('subdir', '.')):
        raise ValueError('Cached source content no longer matches its pinned archive')
    return {**tool, 'source_path': str(source), 'source_archive': str(archive),
            'source_archive_sha256': expected, 'source_sha256': content,
            'declared_version': tool['version'], 'dirty': False,
            'acquisition': 'override' if override else 'fetch-or-cache'}


def _expand_path(value: str, bindings: dict, base: Path, repo: Path) -> str:
    value = value.replace('{python}', sys.executable).replace(
        '{repo}', str(repo))
    for name, tool in bindings.items():
        if 'source_path' in tool:
            value = value.replace('{source:' + name + '}', tool['source_path'])
        if 'tool_path' in tool:
            value = value.replace('{tool:' + name + '}', tool['tool_path'])
    if re.search(r'\{(?:source|tool):[^}]+\}', value):
        raise ValueError(f'Unresolved source/tool placeholder: {value}')
    return str((base / value).resolve()) if not Path(value).is_absolute() else value


def _executable(tool: dict, bindings: dict, base: Path, override: Path | None, repo: Path) -> dict:
    value = str(override) if override else tool['path']
    raw = _expand_path(value, bindings, base, repo) if Path(value).is_absolute() or '/' in value or '{' in value else value
    resolved = shutil.which(raw) if not Path(raw).is_absolute() else raw
    if not resolved or not Path(resolved).is_file():
        raise ValueError(f'Executable tool unavailable: {raw}')
    path = Path(resolved).resolve()
    digest = file_sha256(path)
    if tool.get('sha256') and digest != tool['sha256']:
        raise ValueError('Executable hash does not match its pin')
    result = {**tool, 'tool_path': str(path), 'sha256': digest,
              'declared_version': tool['version'], 'dirty': False}
    if tool.get('version_command'):
        argv = [part.replace('{tool}', str(path)) for part in tool['version_command']]
        child = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        observed = (child.stdout + child.stderr).strip()
        if child.returncode or tool['version'] not in observed:
            raise ValueError('Executable version command does not match its declared version')
        result.update(version_command=argv, observed_version=observed)
    return result


def resolve_tools(tools: dict, selected_ids, *, cache_dir: str | Path,
                  overrides: dict | None = None, allow_dirty: bool = False,
                  base_dir: str | Path | None = None,
                  repo_dir: str | Path | None = None) -> dict:
    """Resolve only selected tools; unrelated backend installations are unnecessary."""
    cache, base = Path(cache_dir).resolve(), Path(base_dir or '.').resolve()
    repo = Path(repo_dir).resolve() if repo_dir else Path(__file__).resolve().parents[2]
    cache.mkdir(parents=True, exist_ok=True)
    overrides = overrides or {}
    ids = list(dict.fromkeys(selected_ids))
    if any(name not in tools for name in ids):
        raise ValueError('Selected task references an unknown tool')
    bindings = {}
    for kind in ('git', 'archive', 'executable'):
        for name in ids:
            tool = tools[name]
            if tool['kind'] != kind:
                continue
            override = Path(overrides[name]) if name in overrides else None
            if kind == 'git':
                binding = _git_source(tool, cache, override, allow_dirty)
            elif kind == 'archive':
                binding = _archive_source(tool, cache, override)
            else:
                binding = _executable(tool, bindings, base, override, repo)
            bindings[name] = binding
    if set(bindings) != set(ids):
        raise ValueError('Unsupported tool kind')
    return bindings


def verify_tools(bindings: dict) -> None:
    """Detect source/binary changes before sealing a measured run."""
    for name, tool in bindings.items():
        if tool.get('source_archive') and file_sha256(Path(tool['source_archive'])) != tool['source_archive_sha256']:
            raise ValueError(f'Source archive changed during run: {name}')
        if tool['kind'] == 'executable':
            if file_sha256(Path(tool['tool_path'])) != tool['sha256']:
                raise ValueError(f'Executable changed during run: {name}')
        else:
            path = Path(tool['source_path'])
            if tool['kind'] == 'git':
                if _git(path, 'rev-parse', 'HEAD') != tool['resolved_revision']:
                    raise ValueError(f'Source revision changed during run: {name}')
                dirty = bool(_git(path, 'status', '--porcelain', '--untracked-files=all'))
                if dirty != tool['dirty']:
                    raise ValueError(f'Source cleanliness changed during run: {name}')
            if _source_digest(path, git=tool['kind'] == 'git') != tool['source_sha256']:
                raise ValueError(f'Source content changed during run: {name}')
