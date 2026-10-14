#!/usr/bin/env python3
"""Certify actual output outside the benchmark completion clock."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def invoke(argv):
    completed = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode:
        raise RuntimeError(f"Validation child failed ({completed.returncode}): {completed.stderr[-4000:]}")
    results = [json.loads(line[7:]) for line in completed.stdout.splitlines() if line.startswith("RESULT ")]
    if len(results) != 1:
        raise ValueError("Exactly one RESULT is required from the adapter")
    return results[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, choices=["count", "materialized", "signed-routed", "full-public"])
    parser.add_argument("--binary", required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--case", required=True)
    args = parser.parse_args()
    records, canonical = Path("qualification.jsonl"), Path("canonical.jsonl")
    if args.contract in {"count", "materialized"}:
        result = invoke([args.binary, args.engine, args.case, "dump", str(records)])
        invoke([args.binary, "validate", args.case, str(records), str(canonical)])
        fingerprint = canonical.read_bytes()
    elif args.contract == "signed-routed":
        result = invoke([args.binary, args.engine, args.case, "signed-routed-dump", str(records)])
        invoke([args.binary, "compare", args.case, str(records), "-", "certificate.json"])
        # Primitive integer cycle lattice + conservation/full rank are checked
        # by the independent certifier. This file has canonical typed graph
        # keys and physical signed factors, independent of legal basis choice.
        fingerprint = Path("certificate.records.json").read_bytes()
    else:
        result = invoke([args.binary, args.engine, args.case, "full-native-dump", str(records), "generation-metadata.json"])
        # Exact official fourteen fields, ordered payloads, groups and report.
        fingerprint = records.read_bytes() + Path("generation-metadata.json").read_bytes()
    result["canonical_sha256"] = hashlib.sha256(fingerprint).hexdigest()
    print("RESULT " + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
