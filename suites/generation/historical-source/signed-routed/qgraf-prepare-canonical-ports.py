#!/usr/bin/env python3
"""Choose one native Grassmann operator convention before native generation."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
MODELS=Path('/tmp/feynkit-full-diagram-comparison-evidence/models')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def odd(values):return sum(a>b for i,a in enumerate(values) for b in values[i+1:])%2

def main():
    rows=[]
    for source in sorted((ROOT/'final-inputs').iterdir()):
        case=json.loads((source/'case.json').read_text());modelmap=MODELS/(case['name']+'.map.json');mapping=json.loads(modelmap.read_text())
        fermi={f['id'] for f in case['fields'] if f['fermi']}
        names={(f['id'],-1):f['name'] for f in case['fields'] if f['name']!=f['anti_name']}
        names.update({(f['id'],1):f['anti_name'] for f in case['fields'] if f['name']!=f['anti_name']})
        names.update({(f['id'],0):f['name'] for f in case['fields'] if f['name']==f['anti_name']})
        model=[];changed=[]
        for line in (source/'model.dat').read_text().splitlines():
            if 'benchrule=' in line:
                rule=int(re.search(r"benchrule='([0-9]+)'",line).group(1));stubs=list(case['signatures'][rule]);ports=[i for i,s in enumerate(stubs) if s['field'] in fermi]
                if ports:
                    assert len(ports)==2 and sorted(stubs[i]['flow'] for i in ports)==[-1,1]
                    if stubs[ports[0]]['flow']==-1:
                        stubs[ports[0]],stubs[ports[1]]=stubs[ports[1]],stubs[ports[0]];changed.append(rule)
                line='[ '+', '.join(names[(s['field'],s['flow'])] for s in stubs)+';'+line.split(';',1)[1]
            model.append(line)
        target=ROOT/'canonical-inputs'/case['name'];target.mkdir(parents=True,exist_ok=False)
        for path in source.iterdir():
            if path.is_file():(target/path.name).write_bytes(path.read_bytes())
        (target/'model.dat').write_text('\n'.join(model)+'\n')
        native_fermion_labels=[leg['label'] for leg in case['legs'] if leg['field'] in fermi]
        assert len(native_fermion_labels)%2==0
        physical={e['label']:e['index'] for e in mapping['externals']}
        indices=[physical[label] for label in native_fermion_labels]
        base_flip=int(mapping['flow_multiplier']==-1)
        exponent=(1+base_flip)*(len(native_fermion_labels)//2)+odd(indices)
        coefficient=-1 if exponent%2 else 1
        expected_file=Path('/tmp/feynkit-full-diagram-comparison-evidence/correctness-gates-v2')/('qgraf-'+case['name']+'-full-dump.full-equality.json')
        expected=json.loads(expected_file.read_text())['candidate_count']
        rows.append({'case':case['name'],'directory':str(target),'rewritten_rules':changed,'ordered_native_Grassmann_ports':'anti_name,field.name','native_external_fermion_labels':native_fermion_labels,'physical_external_fermion_indices':indices,'physical_external_order_odd':bool(odd(indices)),'flow_multiplier':mapping['flow_multiplier'],'external_sign_coefficient':coefficient,'expected_count_from_immutable_previous_exact_gate':expected,'previous_exact_gate':str(expected_file),'previous_exact_gate_sha256':sha(expected_file),'model_map':str(modelmap),'model_map_sha256':sha(modelmap),'files':{p.name:sha(p) for p in target.iterdir() if p.is_file()}})
    (ROOT/'canonical-preparation.json').write_text(json.dumps({'schema':1,'prepare_sha256':sha(Path(__file__)),'status':'prepared_requires_fresh_native_sign_qualification','cases':rows},indent=2)+'\n')
    print(json.dumps([{k:r[k] for k in ('case','rewritten_rules','external_sign_coefficient','expected_count_from_immutable_previous_exact_gate')} for r in rows]))
if __name__=='__main__':main()
