#!/usr/bin/env python3
"""Copy immutable accepted QGRAF LF inputs; expose native sign and routing."""
import hashlib, json
from pathlib import Path
HERE = Path(__file__).resolve().parent
OLD = Path('/tmp/feynkit-materialized-comparison-evidence/external/prepared-lf')
QGRAF = Path('/tmp/feynkit-generator-comparison-evidence/external/qgraf-3.6.10/qgraf')
STYLE = '''<prologue>
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
'''
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    assert max(map(len,STYLE.splitlines())) <= 80
    rows = []
    for old in sorted(OLD.iterdir()):
        if not (old/'qgraf.dat').exists(): continue
        target = HERE/'final-inputs'/old.name
        target.mkdir(parents=True, exist_ok=False)
        for name in ('model.dat', 'qgraf.dat', 'case.json'):
            (target/name).write_bytes((old/name).read_bytes())
        (target/'qgraf.dat').write_text((target/'qgraf.dat').read_text().replace("style = 'incidence.sty'","style = 'signed-routing.sty'"))
        (target/'signed-routing.sty').write_text(STYLE)
        launch = {'commands': [[str(QGRAF)]], 'output_file': 'graphs.raw', 'raw_format': 'qgraf-signed-routed-v1'}
        (target/'external-launch.json').write_text(json.dumps(launch, indent=2)+'\n')
        files = {name: str(target/name) for name in ('model.dat','qgraf.dat','case.json','signed-routing.sty','external-launch.json')}
        rows.append({'case':old.name,'inputs':files,'input_hashes':{name:sha(Path(p)) for name,p in files.items()},'copied_inputs':{name:{'source':str(old/name),'sha256':sha(old/name)} for name in ('model.dat','qgraf.dat','case.json')}})
    (HERE/'preparation.json').write_text(json.dumps({'schema':1,'status':'prepared_not_qualified','binary':str(QGRAF),'binary_sha256':sha(QGRAF),'source':str(QGRAF.parent/'qgraf-3.6.10.f08'),'source_sha256':sha(QGRAF.parent/'qgraf-3.6.10.f08'),'prepare_sha256':sha(Path(__file__)),'routing_convention':{'loops':'k1..kL','external':'p1..pN, all incoming','external_relation':'sum(p1..pN)=0','momentum':'incoming to first emitted endpoint','dual_momentum':'incoming to second emitted endpoint, redundant qualification witness'},'cases':rows},indent=2)+'\n')
    print(json.dumps({'prepared':len(rows),'manifest':str(HERE/'preparation.json')}))
if __name__ == '__main__':main()
