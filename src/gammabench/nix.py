"""Retain Nix build identities without treating store trees as Git checkouts."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

from .schema import SchemaError
from .snapshot import sha256


def load_receipt(suite: dict, suite_path: Path) -> dict | None:
    """Read the declared build receipt before starting a measurement."""
    name = suite.get("nix_build_receipt")
    if name is None:
        return None
    base = suite_path.resolve().parent
    path = (base / name).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise SchemaError("Nix receipt must be a file inside the suite directory")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise SchemaError("unsupported Nix build receipt schema")
    for section in ("sources", "build", "comparison_context", "subjects"):
        if not isinstance(data.get(section), dict):
            raise SchemaError(f"Nix build receipt requires {section}")
    for name, source in data["sources"].items():
        if not isinstance(source, dict):
            raise SchemaError(f"invalid Nix source {name}")
        url = source.get("url", "")
        if not isinstance(url, str) or not url:
            raise SchemaError(f"Nix source {name} requires its URL")
        parsed = urlsplit(url)
        if parsed.username or parsed.password:
            raise SchemaError("Nix source URLs must not contain credentials")
        if source.get("kind") == "git":
            if not re.fullmatch(r"[0-9a-f]{40}", str(source.get("revision", ""))):
                raise SchemaError(f"Nix source {name} needs a full Git revision")
        elif source.get("kind") == "archive":
            if not re.fullmatch(r"[0-9a-f]{64}", str(source.get("archive_sha256", ""))):
                raise SchemaError(f"Nix archive {name} needs its SHA-256")
        else:
            raise SchemaError(f"unsupported Nix source identity: {name}")
    if any(not isinstance(engine, str) or not isinstance(subject, str) or subject not in data["sources"]
           for engine, subject in data["subjects"].items()):
        raise SchemaError("Nix subjects must name declared source identities")
    # The source path is runtime-only. The archived receipt itself remains portable.
    return {"path": path, "sha256": sha256(path), "data": data}


def capture_receipt(receipt: dict | None, staging: Path) -> dict:
    if receipt is None:
        return {}
    relative = Path("build/nix-build-receipt.json")
    target = staging / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(receipt["path"], target)
    if sha256(target) != receipt["sha256"]:
        raise SchemaError("Nix build receipt changed during capture")
    return {"nix_build": receipt["data"], "nix_build_receipt": relative.as_posix(),
            "nix_build_receipt_sha256": receipt["sha256"]}


def verify_receipt(receipt: dict | None) -> None:
    if receipt is not None and sha256(receipt["path"]) != receipt["sha256"]:
        raise SchemaError("Nix build receipt changed during capture")
