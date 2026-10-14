"""Input-only adapters for native QGRAF and nauty, outside all measured clocks."""
from __future__ import annotations

import json
from pathlib import Path
import re


QGRAF_STYLE = """<prologue>
FORMAT qgraf-neutral-v2
<diagram>
D <diagram_index> <sign> <symmetry_factor>
<in_loop>L <in_index> <vertex_index> <field>
<end><propagator_loop>E <vertex_index> <dual-vertex_index> <field>
<end><vertex_loop>V <vertex_index> [benchrule]
<end>
<epilogue>
END <diagram_index>
<exit>
"""
SIGNED_STYLE = """<prologue>
FORMAT qgraf-signed-routed-v1
<diagram>
D <diagram_index> <sign> <symmetry_number>
B <loops> <legs> <legs_in> <legs_out>
<in_loop>L <in_index> <vertex_index> <field> <momentum>
<end><out_loop>O <out_index> <vertex_index> <field> <dual-momentum>
<end>
<propagator_loop>
E <propagator_index> <vertex_index> <dual-vertex_index> <field>
M <momentum> <dual-momentum>
<end>
<vertex_loop>V <vertex_index> [benchrule]
<end>
<epilogue>
END <diagram_index>
<exit>
"""


def qgraf_inputs(case, binary, *, signed=False, mapping=None):
    if not case["legs"]:
        raise ValueError("QGRAF 3.6.10 rejects zero external particles")
    if not case["zero_flow_edges"] or case["max_vertices"] is not None or case["exact_vertices"] is not None:
        raise ValueError("QGRAF adapter does not implement these vertex/zero-flow restrictions")
    names = {}
    fermions = {field["id"] for field in case["fields"] if field["fermi"]}
    model = ["% Exact gammabench signatures; no numerator algebra."]
    for field in case["fields"]:
        for name in (field["name"], field["anti_name"]):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name):
                raise ValueError("Invalid native particle name")
        if field["name"] == field["anti_name"]:
            names[field["id"], 0] = field["name"]
        else:
            names[field["id"], -1] = field["name"]
            names[field["id"], 1] = field["anti_name"]
        model.append(f"[ {field['name']}, {field['anti_name']}, {'-' if field['fermi'] else '+'} ]")
    for index, signature in enumerate(case["signatures"]):
        ports = list(signature)
        if signed:
            odd = [i for i, stub in enumerate(ports) if stub["field"] in fermions]
            if odd:
                if len(odd) != 2 or sorted(ports[i]["flow"] for i in odd) != [-1, 1]:
                    raise ValueError("Signed adapter requires exactly two opposite Grassmann ports per rule")
                if ports[odd[0]]["flow"] == -1:
                    ports[odd[0]], ports[odd[1]] = ports[odd[1]], ports[odd[0]]
        particles = ", ".join(names[stub["field"], stub["flow"]] for stub in ports)
        weight = "" if case["weights"] is None else f"weight={case['weights'][index]}, "
        model.append(f"[ {particles}; {weight}benchrule='{index}' ]")
    if [leg["label"] for leg in case["legs"]] != list(range(1, len(case["legs"]) + 1)):
        raise ValueError("Native external labels must be contiguous")
    incoming = ", ".join(names[leg["field"], leg["flow"]] + f"[p{leg['label']}]" for leg in case["legs"])
    options = [option for flag, option in ((case["one_pi"], "onepi"), (not case["allow_self_loops"], "noselfloop"), (case["simple"], "noparallel")) if flag]
    style_name = "signed-routing.sty" if signed else "incidence.sty"
    control = ["config = info, lf ;", "messages = 'messages.txt' ;", "output = 'graphs.raw' ;",
               f"style = '{style_name}' ;", "model = 'model.dat' ;", f"in = {incoming} ;", "out = ;",
               f"loops = {case['loops']} ;", "loop_momentum = k ;", "options = " + ", ".join(options) + " ;"]
    if case["weights"] is not None:
        exact = case["weight_exact"]
        control.append(f"true = vsum[weight,{exact},{exact}] ;")
    files = {"model.dat": "\n".join(model) + "\n", "qgraf.dat": "\n".join(control) + "\n",
             style_name: SIGNED_STYLE if signed else QGRAF_STYLE}
    if any(max(map(len, text.splitlines()), default=0) > 80 for text in files.values()):
        raise ValueError("QGRAF's 80-column input limit exceeded")
    launch = {"commands": [[str(binary)]], "output_file": "graphs.raw",
              "raw_format": "qgraf-signed-routed-v1" if signed else "qgraf-neutral-v2"}
    if signed:
        if mapping is None:
            raise ValueError("Signed external convention requires model mapping")
        labels = [leg["label"] for leg in case["legs"] if leg["field"] in fermions]
        physical = {external["label"]: external["index"] for external in mapping["externals"]}
        indices = [physical[label] for label in labels]
        inversions = sum(a > b for i, a in enumerate(indices) for b in indices[i + 1:])
        exponent = (1 + int(mapping["flow_multiplier"] == -1)) * (len(labels) // 2) + inversions
        launch["sign_coefficient"] = -1 if exponent % 2 else 1
    files["external-launch.json"] = json.dumps(launch, indent=2) + "\n"
    return files


def nauty_inputs(case, binaries):
    if case["legs"] or len(case["fields"]) != 1 or case["weights"] is not None or case["one_pi"]:
        raise ValueError("Nauty adapter supports uncoloured undirected topology families")
    if any(stub["flow"] for signature in case["signatures"] for stub in signature):
        raise ValueError("Nauty adapter requires undirected fields")
    degrees, count = [len(signature) for signature in case["signatures"]], case["exact_vertices"]
    if case["simple"] and degrees == [3] and count in (10, 12):
        commands = [[str(binaries["geng-materialized"]), "-c", "-d3", "-D3", "-l", str(count)]]
    elif case["loops"] == 0 and count == 14 and degrees == list(range(1, 14)):
        commands = [[str(binaries["gentreeg-materialized"]), str(count)]]
    elif degrees == [3] and case["allow_self_loops"] and case["loops"] in (5, 6):
        count = 2 * (case["loops"] - 1)
        commands = [[str(binaries["geng"]), "-c", "-d1", "-D3", str(count), f"0:{3*count//2}"],
                    [str(binaries["multig"]), "-l3", "-G"]]
    else:
        raise ValueError("No exact nauty adapter for this family")
    return {"external-launch.json": json.dumps({"commands": commands, "output_file": None, "raw_format": "multig-G"}, indent=2) + "\n"}
