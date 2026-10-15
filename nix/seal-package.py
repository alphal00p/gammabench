"""Bind executable contents in an immutable Nix benchmark package."""
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
suite = json.loads((root / "suite.json").read_text())
receipt = json.loads((root / "nix-build-receipt.json").read_text())
executables = {}
for name, tool in suite["tools"].items():
    if tool["kind"] != "executable":
        raise ValueError("Packaged suites must use realized executable tools")
    path = Path(tool["path"])
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    tool["sha256"] = digest
    executables[name] = {"path": str(path), "sha256": digest}
receipt["build"]["executables"] = executables
for filename, data in (("suite.json", suite), ("nix-build-receipt.json", receipt)):
    (root / filename).write_text(json.dumps(data, indent=2) + "\n")
