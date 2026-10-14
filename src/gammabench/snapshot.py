"""Portable, atomically sealed result bundles with complete SHA-256 inventories."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from .schema import SchemaError, validate_snapshot, validate_suite

INVENTORY = "files.sha256.json"


class IntegrityError(ValueError):
    """A sealed bundle is incomplete, changed, or contains unsafe paths."""


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def staging_directory(destination: str | Path) -> Path:
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"snapshot destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{destination.name}.unsealed-", dir=destination.parent))


def _files(directory: Path) -> dict[str, Path]:
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise IntegrityError(f"snapshots cannot contain symlinks: {path.relative_to(directory)}")
        if path.is_file() and path != directory / INVENTORY:
            files[path.relative_to(directory).as_posix()] = path
        elif not path.is_dir() and not path.is_file():
            raise IntegrityError(f"unsupported snapshot entry: {path.relative_to(directory)}")
    return files


def _verify(path: str | Path) -> dict:
    """Check the entire inventory, including added files, after any relocation."""
    directory = Path(path)
    inventory_path = directory / INVENTORY
    if inventory_path.is_symlink() or not inventory_path.is_file():
        raise IntegrityError("snapshot is unsealed: files.sha256.json is missing")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict) or not inventory:
        raise IntegrityError("invalid SHA-256 inventory")
    files = _files(directory)
    if set(files) != set(inventory):
        missing = sorted(set(inventory) - set(files))
        added = sorted(set(files) - set(inventory))
        raise IntegrityError(f"inventory mismatch: missing={missing}, added={added}")
    for name, file in files.items():
        if not isinstance(inventory[name], str) or sha256(file) != inventory[name]:
            raise IntegrityError(f"SHA-256 mismatch: {name}")
    if not {"snapshot.json", "suite.json"} <= files.keys():
        raise IntegrityError("snapshot metadata is missing")
    return inventory


def verify(path: str | Path) -> dict:
    return _verify(path)


def load(path: str | Path, verify: bool = True) -> dict:
    directory = Path(path)
    if verify:
        _verify(directory)
    snapshot = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
    validate_snapshot(snapshot)
    suite = validate_suite(json.loads((directory / "suite.json").read_text(encoding="utf-8")))
    if suite["id"] != snapshot["suite_id"]:
        raise SchemaError("snapshot suite ID does not match suite.json")
    if suite["contracts"] != snapshot["contracts"] or suite["metrics"] != snapshot["metrics"]:
        raise SchemaError("snapshot contracts or metrics do not match suite.json")
    return snapshot


def seal(staging: str | Path, destination: str | Path, snapshot: dict, suite: dict) -> Path:
    """Finalize a new bundle. A failure leaves explicitly unsealed staging files."""
    staging, destination = Path(staging), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"snapshot destination already exists: {destination}")
    if (staging / INVENTORY).exists():
        raise IntegrityError("staging directory is already sealed")
    suite = validate_suite(suite)
    validate_snapshot(snapshot)
    if snapshot["suite_id"] != suite["id"]:
        raise SchemaError("snapshot suite ID does not match suite")
    if snapshot["contracts"] != suite["contracts"] or snapshot["metrics"] != suite["metrics"]:
        raise SchemaError("snapshot must retain the suite contracts and metric scopes")
    write_json(staging / "snapshot.json", snapshot)
    write_json(staging / "suite.json", suite)
    inventory = {name: sha256(path) for name, path in _files(staging).items()}
    write_json(staging / INVENTORY, inventory)
    _verify(staging)
    for file in staging.rglob("*"):
        if file.is_file():
            file.chmod(0o444)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"snapshot destination already exists: {destination}")
    os.rename(staging, destination)
    descriptor = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return destination
