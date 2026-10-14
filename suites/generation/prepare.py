#!/usr/bin/env python3
"""Build portable adapters from resolved pins and emit a runnable suite.

The prepared output is disposable and contains build products. Engine checkouts
are read-only inputs; preparation never patches public engine implementations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from external import nauty_inputs, qgraf_inputs

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
from gammabench.sources import verify_tools

BUILD_COMMANDS = []
CONTRACTS = {
    "count": "graph-count-v1",
    "materialized": "materialized-incidence-and-symmetry-v1",
    "signed-routed": "signed-routed-diagrams-v1",
    "full-public": "full-native-diagrams-local-fragments-v1",
}
MODES = {"count": "count", "materialized": "materialize", "signed-routed": "signed-routed", "full-public": "full-native"}
DESCRIPTIONS = {
    "count": "Complete native generation with output suppressed; returns accepted count. No owned diagram materialization.",
    "materialized": "Retained owned interaction/field/flow incidence and exact native symmetry factors. Numerators and routing excluded.",
    "signed-routed": "Retained owned typed incidence, exact unsigned symmetry factor, Grassmann sign and integer momentum routing. Numerators/projectors excluded.",
    "full-public": "Direct public Process.generate_diagrams retaining complete GenerationResult/FeynmanDiagram objects. Local numerators, projectors, routing, factors and groups included; aggregate multiplication excluded.",
}


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source(bindings, name):
    value = bindings[name]
    return Path(value.get("source_path", value.get("tool_path", ""))).resolve()


def cargo_manifest(kind, bindings):
    gamma, symbolica = source(bindings, "gammaloop"), source(bindings, "symbolica")
    dependencies = {
        "serde": '{ version = "1", features = ["derive"] }',
        "serde_json": '"1"',
        "linnet": '{ path = ' + json.dumps(str(gamma / "crates/linnet")) + ', default-features = false, features = ["rayon"] }',
        "graphica": '{ path = ' + json.dumps(str(symbolica / "lib/graphica")) + ', default-features = false }',
        "numerica": '{ path = ' + json.dumps(str(symbolica / "lib/numerica")) + ', default-features = false, features = ["integer-gmp", "float-mpfr"] }',
    }
    if kind in {"count", "materialized"}:
        dependencies.update(feyngraph='{ path = ' + json.dumps(str(source(bindings, "feyngraph"))) + ' }', rayon='"1"', **{"rustc-hash": '"2"'})
    else:
        for name in ("feynkit-generator", "feynkit-graph", "feynkit-model"):
            dependencies[name] = '{ path = ' + json.dumps(str(gamma / "crates" / name)) + ' }'
        dependencies["symbolica"] = '{ path = ' + json.dumps(str(symbolica)) + ', default-features = false }'
    patches = {"numerica": symbolica / "lib/numerica", "graphica": symbolica / "lib/graphica", "symbolica": symbolica}
    if kind in {"signed-routed", "full-public"}:
        patches.update({name: gamma / "vendor" / name for name in ("typst-library", "typst-svg", "clarabel")})
    text = '[package]\nname = "gammabench-generation-' + kind + '"\nversion = "0.1.0"\nedition = "2024"\n\n[dependencies]\n'
    text += "\n".join(name + " = " + value for name, value in dependencies.items())
    text += "\n\n[patch.crates-io]\n" + "\n".join(name + " = { path = " + json.dumps(str(path)) + " }" for name, path in patches.items())
    if kind in {"signed-routed", "full-public"}:
        text += '\nsymbolica-typst-plugin = { git = "https://github.com/symbolica-dev/symbolica-typst-plugin", rev = "57b7455b37c30f4f87353c0073e49f5225e937ab" }'
    return text + '\n\n[profile.release]\nopt-level = 3\ndebug = 1\nlto = "thin"\ncodegen-units = 1\n'


def run(argv, cwd, log):
    BUILD_COMMANDS.append({"argv": argv, "cwd": str(cwd), "log": str(log)})
    with log.open("w") as output:
        completed = subprocess.run(argv, cwd=cwd, stdout=output, stderr=subprocess.STDOUT)
    if completed.returncode:
        raise RuntimeError(f"Build failed ({completed.returncode}); see {log}")


def build_nauty(bindings, target, jobs):
    upstream = source(bindings, "nauty")
    if not (upstream / "configure").is_file():
        raise ValueError("Resolved nauty source must contain configure")
    work = target / "nauty-build"
    shutil.copytree(upstream, work, ignore=shutil.ignore_patterns("*.o", "*.a"))
    run(["./configure"], work, target / "nauty-configure.log")
    run(["make", f"-j{jobs}", "geng", "gentreeg", "multig", "nautyW.a"], work, target / "nauty-make.log")
    callback = HERE / "native/nauty_callback.c"
    for program, function in (("geng", "materialized_graph"), ("gentreeg", "materialized_tree")):
        flags = ["cc", "-O3", "-g", "-DWORDSIZE=32", "-DMAXN=32", f"-DOUTPROC={function}"]
        if program == "geng":
            flags.append("-DMATERIALIZED_GENG")
        # OUTPROC needs its declaration before upstream's translation unit.
        header = work / (program + "-callback.h")
        signature = "graph *" if program == "geng" else "int *"
        header.write_text('#include "nauty.h"\nvoid ' + function + '(FILE *, ' + signature + ', int);\n')
        flags += ["-I", str(work), "-include", str(header), str(work / (program + ".c")), str(callback), str(work / "nautyW.a"), "-lm", "-o", str(work / (program + "-materialized"))]
        run(flags, work, target / (program + "-callback-build.log"))
    return {name: work / name for name in ("geng", "multig", "geng-materialized", "gentreeg-materialized")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolved-tools", required=True, type=Path)
    parser.add_argument("--tools-lock", type=Path, default=HERE.parents[1] / "tools.lock.json")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--contract", choices=CONTRACTS, default="materialized")
    parser.add_argument("--engines", nargs="+", default=["linnet", "graphica", "feyngraph", "qgraf", "nauty"])
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--target-cache", type=Path, help="Copy an existing Cargo target as an independent build cache; Cargo still validates all source fingerprints")
    parser.add_argument("--build", action="store_true", help="Run builds; without it, emit source/build plan only")
    parser.add_argument("--qgraf-binary", type=Path, help="Optional prebuilt upstream executable instead of compiling the supplied source archive")
    args = parser.parse_args()
    kind, engines = args.contract, args.engines
    allowed = {"count": {"linnet", "graphica", "feyngraph"}, "materialized": {"linnet", "graphica", "feyngraph", "qgraf", "nauty"},
               "signed-routed": {"linnet", "qgraf"}, "full-public": {"linnet"}}[kind]
    if set(engines) - allowed:
        parser.error(f"{kind} supports {sorted(allowed)}; external native count controls are preserved in historical-source")
    bindings = read(args.resolved_tools)
    bindings = bindings.get("tools", bindings)
    lock = read(args.tools_lock)["tools"]
    required = ["gammaloop", "symbolica"] + (["feyngraph"] if kind in {"count", "materialized"} else [])
    required += [name for name in ("qgraf", "nauty") if name in engines]
    for name in required:
        binding, declared = bindings[name], lock[name]
        fields = ("kind", "url", "revision") if declared["kind"] == "git" else ("kind", "url", "sha256", "version")
        if any(binding.get(field) != declared.get(field) for field in fields):
            raise ValueError(f"Resolved {name} source does not match tools-lock declaration")
        if declared["kind"] == "archive" and binding.get("subdir", ".") != declared.get("subdir", "."):
            raise ValueError(f"Resolved {name} archive selects a different source subdirectory")
        if declared["kind"] == "git" and binding.get("resolved_revision") != declared["revision"]:
            raise ValueError(f"Actual {name} Git revision differs from its declared source pin")
        if declared["kind"] == "archive" and binding.get("source_archive_sha256") != declared["sha256"]:
            raise ValueError(f"Actual {name} archive digest differs from its declared source pin")
        if binding.get("dirty"):
            raise ValueError("Generation preparation requires clean immutable tool sources")
    used_bindings = {name: bindings[name] for name in required}
    verify_tools(used_bindings)
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    driver = output / "driver"
    shutil.copytree(HERE / "drivers" / kind, driver)
    (driver / "Cargo.toml").write_text(cargo_manifest(kind, bindings))
    shutil.copytree(HERE / "fixtures", output / "fixtures")
    shutil.copyfile(HERE / "validate.py", output / "validate.py")
    plan = {"schema_version": 1, "contract": CONTRACTS[kind], "adapter_sha256": {str(path.relative_to(HERE)): sha(path) for path in (HERE / "drivers" / kind).rglob("*") if path.is_file()},
            "resolved_tools": bindings, "cargo_manifest_sha256": sha(driver / "Cargo.toml"), "built": args.build}
    helper_files = [HERE / name for name in ("prepare.py", "external.py", "validate.py", "native/nauty_callback.c")]
    plan["helper_sha256"] = {str(path): sha(path) for path in helper_files}
    (output / "preparation.json").write_text(json.dumps(plan, indent=2) + "\n")
    if not args.build:
        print(json.dumps({"status": "prepared-build-plan", "output": str(output), "next": "Rerun with --build into a new output directory"}))
        return
    if args.target_cache:
        subprocess.run(["cp", "-a", "--reflink=auto", str(args.target_cache.resolve()), str(driver / "target")], check=True)
        plan["copied_build_cache"] = str(args.target_cache.resolve())
    run(["cargo", "build", "--release", f"-j{args.jobs}"], driver, output / "cargo-build.log")
    binary = driver / "target/release" / ("gammabench-generation-" + kind)
    bins = build_nauty(bindings, output, args.jobs) if "nauty" in engines else {}
    qgraf = args.qgraf_binary.absolute() if args.qgraf_binary else None
    if "qgraf" in engines and qgraf is None:
        upstream = source(bindings, "qgraf")
        candidates = list(upstream.rglob("qgraf-3.6.10.f08"))
        if len(candidates) != 1:
            raise ValueError("Exactly one supplied upstream qgraf-3.6.10.f08 source is required")
        qgraf = output / "qgraf"
        run(["gfortran", "-O3", str(candidates[0]), "-o", str(qgraf)], output, output / "qgraf-build.log")
    verify_tools(used_bindings)
    if any(sha(path) != plan["helper_sha256"][str(path)] for path in helper_files):
        raise ValueError("Generation preparation helpers changed during the build")
    metadata_path = output / "cargo-metadata.json"
    with metadata_path.open("w") as stream:
        subprocess.run(["cargo", "metadata", "--locked", "--format-version", "1"], cwd=driver, stdout=stream, check=True)
    toolchain = {}
    for executable in ("rustc", "cargo", "cc", "gfortran"):
        if shutil.which(executable):
            completed = subprocess.run([executable, "--version"], capture_output=True, text=True, check=True)
            toolchain[executable] = completed.stdout.splitlines()[0]
    plan.update(build_commands=BUILD_COMMANDS, toolchain=toolchain,
                cargo_lock_sha256=sha(driver / "Cargo.lock"), cargo_metadata_sha256=sha(metadata_path),
                actual_source_bindings=used_bindings, binary_sha256=sha(binary))
    (output / "build-receipt.json").write_text(json.dumps(plan, indent=2) + "\n")
    (output / "preparation.json").write_text(json.dumps(plan, indent=2) + "\n")
    source_ids = ["gammaloop", "symbolica"] + (["feyngraph"] if kind in {"count", "materialized"} else [])
    tools = {name: lock[name] for name in source_ids}
    tools["driver"] = {"kind": "executable", "path": str(binary), "version": "gammabench generation adapter v1", "sha256": sha(binary)}
    if qgraf is not None:
        tools["qgraf"] = {"kind": "executable", "path": str(qgraf), "version": "QGRAF 3.6.10", "sha256": sha(qgraf)}
        tools["qgraf-source"] = lock["qgraf"]
    if bins:
        tools["nauty-source"] = lock["nauty"]
    for name, executable in bins.items():
        tools[name] = {"kind": "executable", "path": str(executable), "version": "nauty 2.9.3 public OUTPROC adapter", "sha256": sha(executable)}
    selected = args.cases or [path.stem for path in (output / "fixtures/cases").glob("*.json")]
    tasks, unsupported = [], []
    for case_id in sorted(selected):
        case_file = output / "fixtures/cases" / (case_id + ".json")
        case = read(case_file)
        mapping_file = output / "fixtures/models" / (case_id + ".map.json")
        for engine in engines:
            task_id = engine + "-" + case_id
            inputs = {"case.json": str(case_file.relative_to(output))}
            env = {"BENCH_CASE_FILE": "{input:case.json}", "BENCH_THREADS": "1", "RAYON_NUM_THREADS": "1", "RUST_LOG": "off"}
            selected_tools = source_ids + ["driver"]
            if kind in {"signed-routed", "full-public"}:
                mapping = read(mapping_file)
                model_file = output / "fixtures/models" / mapping["model_path"]
                inputs.update({"model-map.json": str(mapping_file.relative_to(output)), mapping["model_path"]: str(model_file.relative_to(output))})
                env["BENCH_ROUTED_MODEL_FILE" if kind == "signed-routed" else "BENCH_FULL_MODEL_FILE"] = "{input:model-map.json}"
            if engine == "feyngraph" and any(len(signature) < 3 for signature in case["signatures"]):
                unsupported.append({"case": case_id, "engine": engine, "reason": "FeynGraph public degree-partition API requires degrees >=3"})
                continue
            workload_inputs = list(inputs)
            inputs.update({"adapter.Cargo.lock": "driver/Cargo.lock", "adapter-build-receipt.json": "build-receipt.json", "adapter-cargo-metadata.json": "cargo-metadata.json"})
            try:
                external = qgraf_inputs(case, qgraf, signed=kind == "signed-routed", mapping=read(mapping_file) if kind == "signed-routed" else None) if engine == "qgraf" else nauty_inputs(case, bins) if engine == "nauty" else {}
            except ValueError as error:
                unsupported.append({"case": case_id, "engine": engine, "reason": str(error)})
                continue
            if external:
                directory = output / "external" / task_id
                directory.mkdir(parents=True)
                for name, text in external.items():
                    (directory / name).write_text(text)
                    inputs[name] = str((directory / name).relative_to(output))
                env["BENCH_EXTERNAL_CONFIG"] = "{input:external-launch.json}"
                if engine == "qgraf":
                    selected_tools += ["qgraf", "qgraf-source"]
                else:
                    commands = json.loads(external["external-launch.json"])["commands"]
                    selected_tools += ["nauty-source"] + [Path(command[0]).name for command in commands]
            tasks.append({"id": task_id, "case": case_id, "engine": engine, "variant": "pinned", "contract": CONTRACTS[kind],
                          "argv": ["{tool:driver}", engine, case_id, MODES[kind]], "tool_ids": selected_tools, "inputs": inputs,
                          "workload_inputs": workload_inputs,
                          "environment": env, "expected": {"status": "ok"},
                          "validation_command": ["{python}", "{suite_dir}/validate.py", "--contract", kind, "--binary", "{tool:driver}", "--engine", engine, "--case", case_id],
                          "validation_equivalence_fields": ["canonical_sha256"]})
    metric_field = {"count": "elapsed_ns", "materialized": "materialized_ns", "signed-routed": "signed_routed_ns", "full-public": "full_diagrams_ns"}[kind]
    suite = {"schema_version": 1, "id": "generation-" + kind, "description": DESCRIPTIONS[kind],
             "contracts": {CONTRACTS[kind]: {"description": DESCRIPTIONS[kind], "included": [DESCRIPTIONS[kind]], "excluded": ["Post-clock semantic validation and output serialization"]}},
             "metrics": {"completion_ns": {"unit": "ns", "direction": "lower", "scope": DESCRIPTIONS[kind], "result_field": metric_field}},
             "tools": tools, "tasks": tasks, "unsupported": unsupported, "preparation": plan}
    (output / "suite.json").write_text(json.dumps(suite, indent=2) + "\n")
    print(json.dumps({"status": "built", "suite": str(output / "suite.json"), "tasks": len(tasks), "unsupported": unsupported}))


if __name__ == "__main__":
    main()
