"""Assemble a generation suite around prebuilt immutable executables."""
import json
from pathlib import Path
import shutil
import sys

source, output, config_file = map(Path, sys.argv[1:])
sys.path.insert(0, str(source / "suites/generation"))
import prepare

config = json.loads(config_file.read_text())
kind = config["contract"]
binary = config["binary"]
engines = config["engines"]
shutil.copytree(source / "suites/generation/fixtures", output / "fixtures")
shutil.copyfile(source / "suites/generation/validate.py", output / "validate.py")
tools = {"driver": {"kind": "executable", "path": binary, "version": "gammabench generation adapter v1"}}
for name, executable in config["external"].items():
    tools[name] = {"kind": "executable", "path": executable, "version": "Pinned " + name}
tasks, unsupported = [], []
for case_file in sorted((output / "fixtures/cases").glob("*.json")):
    case_id = case_file.stem
    case = prepare.read(case_file)
    mapping_file = output / "fixtures/models" / (case_id + ".map.json")
    for engine in engines:
        task_id = engine + "-" + case_id
        inputs = {"case.json": str(case_file.relative_to(output))}
        env = {"BENCH_CASE_FILE": "{input:case.json}", "BENCH_THREADS": "1", "RAYON_NUM_THREADS": "1", "RUST_LOG": "off"}
        selected_tools = ["driver"]
        if kind in {"signed-routed", "full-public"}:
            if not mapping_file.is_file():
                unsupported.append({"case": case_id, "engine": engine, "reason": "No immutable rich-model mapping is supplied for this workload"})
                continue
            mapping = prepare.read(mapping_file)
            model_file = output / "fixtures/models" / mapping["model_path"]
            inputs.update({"model-map.json": str(mapping_file.relative_to(output)), mapping["model_path"]: str(model_file.relative_to(output))})
            env["BENCH_ROUTED_MODEL_FILE" if kind == "signed-routed" else "BENCH_FULL_MODEL_FILE"] = "{input:model-map.json}"
        if engine == "feyngraph" and any(len(signature) < 3 for signature in case["signatures"]):
            unsupported.append({"case": case_id, "engine": engine, "reason": "FeynGraph public degree-partition API requires degrees >=3"})
            continue
        workload_inputs = list(inputs)
        inputs["nix-build-receipt.json"] = "nix-build-receipt.json"
        try:
            external = prepare.qgraf_inputs(case, Path(config["external"]["qgraf"]), signed=kind == "signed-routed", mapping=prepare.read(mapping_file) if kind == "signed-routed" else None) if engine == "qgraf" else prepare.nauty_inputs(case, {name: Path(value) for name, value in config["external"].items()}) if engine == "nauty" else {}
        except ValueError as error:
            unsupported.append({"case": case_id, "engine": engine, "reason": str(error)})
            continue
        if external:
            directory = output / "external" / task_id
            directory.mkdir(parents=True)
            for name, content in external.items():
                (directory / name).write_text(content)
                inputs[name] = str((directory / name).relative_to(output))
            env["BENCH_EXTERNAL_CONFIG"] = "{input:external-launch.json}"
            selected_tools += ["qgraf"] if engine == "qgraf" else list(dict.fromkeys(Path(command[0]).name for command in json.loads(external["external-launch.json"])["commands"]))
        tasks.append({"id": task_id, "case": case_id, "engine": engine, "variant": "nix", "contract": prepare.CONTRACTS[kind],
            "argv": ["{tool:driver}", engine, case_id, prepare.MODES[kind]], "tool_ids": selected_tools, "inputs": inputs,
            "workload_inputs": workload_inputs, "environment": env, "expected": {"status": "ok"},
            "validation_command": ["{python}", "{suite_dir}/validate.py", "--contract", kind, "--binary", "{tool:driver}", "--engine", engine, "--case", case_id],
            "validation_equivalence_fields": ["canonical_sha256"]})
metric_field = {"count": "elapsed_ns", "materialized": "materialized_ns", "signed-routed": "signed_routed_ns", "full-public": "full_diagrams_ns"}[kind]
suite = {"schema_version": 1, "id": "generation-" + kind, "description": prepare.DESCRIPTIONS[kind],
    "contracts": {prepare.CONTRACTS[kind]: {"description": prepare.DESCRIPTIONS[kind], "included": [prepare.DESCRIPTIONS[kind]], "excluded": ["Post-clock semantic validation and output serialization"]}},
    "metrics": {"completion_ns": {"unit": "ns", "direction": "lower", "scope": prepare.DESCRIPTIONS[kind], "result_field": metric_field}},
    "tools": tools, "tasks": tasks, "unsupported": unsupported, "nix_build_receipt": "nix-build-receipt.json"}
(output / "suite.json").write_text(json.dumps(suite, indent=2) + "\n")
