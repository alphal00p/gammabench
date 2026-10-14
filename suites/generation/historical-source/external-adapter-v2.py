#!/usr/bin/env python3
"""Prepare direct external-generator invocations; parse complete output after timing."""
import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import subprocess
import sys

HERE = Path(__file__).resolve().parent
NAUTY = Path('/nix/store/pmavpfqfa6s1w44ysg4037yy48ckns5m-nauty-2.9.3/bin')
QGRAF = HERE / 'qgraf-3.6.10/qgraf'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def field_names(case):
    result = {}
    for f in case['fields']:
        assert re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', f['name'])
        assert re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', f['anti_name'])
        if f['name'] == f['anti_name']:
            result[f['name']] = (f['id'], 0)
        else:
            result[f['name']] = (f['id'], -1)
            result[f['anti_name']] = (f['id'], 1)
    assert len(set(result.values())) == len(result)
    return result


def signature_key(stubs):
    return tuple(sorted((s['field'], s['flow']) for s in stubs))


def qgraf_inputs(case, mode):
    if not case['legs']:
        raise ValueError('QGRAF does not generate zero-point/vacuum diagrams')
    if not case['zero_flow_edges']:
        raise ValueError('This adapter does not map zero-flow restrictions')
    if case['max_vertices'] is not None or case['exact_vertices'] is not None:
        raise ValueError('Vertex-count restrictions are outside this QGRAF adapter scope')
    names = field_names(case)
    to_name = {stub: name for name, stub in names.items()}
    assert len({signature_key(s) for s in case['signatures']}) == len(case['signatures'])
    model = ['% Exact benchmark signatures; no numerator algebra.']
    for f in case['fields']:
        model.append(f"[ {f['name']}, {f['anti_name']}, {'-' if f['fermi'] else '+'} ]")
    weights = case['weights']
    for i, signature in enumerate(case['signatures']):
        particles = ', '.join(to_name[(s['field'], s['flow'])] for s in signature)
        weight = '' if weights is None else f'; weight={weights[i]}'
        model.append(f'[ {particles}{weight} ]')
    options = []
    if case['one_pi']:
        options.append('onepi')
    if not case['allow_self_loops']:
        options.append('noselfloop')
    if case['simple']:
        options.append('noparallel')
    legs = case['legs']
    assert [l['label'] for l in legs] == list(range(1, len(legs) + 1))
    incoming = ', '.join(to_name[(l['field'], l['flow'])] + f'[p{i+1}]'
                         for i, l in enumerate(legs))
    control = [f"config = info{', nolist' if mode == 'count' else ''} ;",
               "messages = 'messages.txt' ;", "output = 'graphs.raw' ;",
               "style = 'incidence.sty' ;", "model = 'model.dat' ;",
               f'in = {incoming} ;', 'out = ;', f"loops = {case['loops']} ;",
               'loop_momentum = k ;', 'options = ' + ', '.join(options) + ' ;']
    if weights is not None:
        bound = case['weight_exact']
        assert isinstance(bound, int)
        control.append(f'true = vsum[weight,{bound},{bound}] ;')
    style = '''<prologue>
FORMAT qgraf-neutral-v1
<diagram>
D <diagram_index> <sign> <symmetry_factor>
<in_loop>L <in_index> <vertex_index> <field>
<end><propagator_loop>E <vertex_index> <dual-vertex_index> <field>
<end><vertex_loop><ray_loop>R <vertex_index> <field>
<end><end>
<epilogue>
END <diagram_index>
<exit>
'''
    files = {'model.dat': '\n'.join(model) + '\n', 'qgraf.dat': '\n'.join(control) + '\n',
             'incidence.sty': style}
    for name, content in files.items():
        assert max(map(len, content.splitlines()), default=0) <= 80, name
    return files, [str(QGRAF)], 'graphs.raw'


def nauty_inputs(case, mode):
    if case['legs'] or len(case['fields']) != 1 or case['weights'] is not None or case['one_pi']:
        raise ValueError('Nauty adapter covers the specified uncoloured topology-only families')
    if any(s['flow'] != 0 for sig in case['signatures'] for s in sig):
        raise ValueError('Nauty topology adapter accepts undirected stubs only')
    degrees = [len(s) for s in case['signatures']]
    n = case['exact_vertices']
    if case['simple'] and degrees == [3] and n in (10, 12):
        command = [str(NAUTY / 'geng'), '-c', '-d3', '-D3']
        if mode == 'count': command.append('-u')
        command.append(str(n))
        return {}, command, 'graph6'
    if case['loops'] == 0 and n == 14 and degrees == list(range(1, 14)):
        command = [str(NAUTY / 'gentreeg')]
        if mode == 'count': command.append('-u')
        command.append(str(n))
        return {}, command, 'sparse6'
    if degrees == [3] and case['allow_self_loops'] and case['loops'] in (5, 6):
        n = 2 * (case['loops'] - 1)
        first = [str(NAUTY / 'geng'), '-c', '-d1', '-D3', str(n), f'0:{3*n//2}']
        second = [str(NAUTY / 'multig'), '-l3', '-u' if mode == 'count' else '-G']
        script = '#!/usr/bin/env bash\nset -euo pipefail\n' + shlex.join(first) + ' | ' + shlex.join(second) + '\n'
        return {'pipeline.sh': script}, ['/run/current-system/sw/bin/bash', 'pipeline.sh'], 'multig-G'
    raise ValueError('No exact nauty adapter for this family')


def prepare(args):
    cases_path = Path(args.cases).resolve()
    cases = json.loads(cases_path.read_text())
    case = next(c for c in cases if c['name'] == args.case)
    target = Path(args.directory).resolve()
    target.mkdir(parents=True, exist_ok=False)
    maker = qgraf_inputs if args.engine == 'qgraf' else nauty_inputs
    try:
        files, command, raw_format = maker(case, args.mode)
    except ValueError as error:
        record = {'status': 'unsupported', 'engine': args.engine, 'case': args.case,
                  'reason': str(error), 'case_spec_sha256': sha(cases_path)}
        (target / 'unsupported.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record)); return
    files['case.json'] = json.dumps(case, indent=2) + '\n'
    for name, content in files.items():
        (target / name).write_text(content)
    tools = [QGRAF] if args.engine == 'qgraf' else [NAUTY / n for n in ('geng','gentreeg','multig','copyg')]
    parse_command = [sys.executable, str(Path(__file__).resolve()), 'parse',
                     '--engine', args.engine, '--mode', args.mode,
                     '--case-file', '{workdir}/case.json', '--workdir', '{workdir}',
                     '--stdout', '{stdout}', '--stderr', '{stderr}', '--format', raw_format]
    task = {'id': f'{args.engine}-{args.case}-{args.mode}', 'engine': args.engine, 'case': args.case,
            'mode': args.mode, 'command': command, 'inputs': {n: str(target / n) for n in files},
            'input_hashes': {n: sha(target / n) for n in files}, 'parse_command': parse_command,
            'expected': {'engine': args.engine, 'case': args.case, 'mode': args.mode},
            'correctness_gate': None, 'profile': False,
            'profiler_scope': 'unsupported_pipeline' if raw_format == 'multig-G' else 'single_process',
            'case_spec_sha256': sha(cases_path),
            'binary_hashes': {str(p): sha(p) for p in tools},
            'adapter_sha256': sha(__file__), 'raw_format': raw_format,
            'output_artifacts': ['graphs.jsonl'] if args.mode == 'dump' else [],
            'timed_boundary': 'Direct external process (or explicit geng|multig pipeline); input preparation and output parsing excluded',
            'scope': 'normal incidence stream with formatting' if args.mode == 'dump' else 'native count-only capability; graph output suppressed'}
    (target / 'launch.json').write_text(json.dumps(task, indent=2) + '\n')
    print(json.dumps(task))


def qgraf_records(case, raw):
    names = field_names(case)
    rules = {signature_key(s): i for i, s in enumerate(case['signatures'])}
    current = None
    count = None
    last_index = 0
    def finish():
        vertices = sorted(current.pop('rays'))
        assert vertices == list(range(len(vertices))), vertices
        current['rules'] = [rules[tuple(sorted(rays[v]))] for v in vertices]
        return current
    for line in raw.splitlines():
        parts = line.split()
        if not parts or parts[0] == 'FORMAT': continue
        if parts[0] == 'D':
            if current is not None: yield finish()
            index, sign, symmetry = int(parts[1]), parts[2], Fraction(parts[3])
            assert index == last_index + 1
            last_index = index
            assert symmetry > 0 and symmetry.numerator == 1, symmetry
            assert sign in ('+', '-')
            rays = {}
            current = {'rules': [], 'legs': [], 'edges': [], 'aut': str(symmetry.denominator),
                       'sign': 1 if sign == '+' else -1, 'rays': rays}
        elif parts[0] == 'R':
            v = int(parts[1]) - 1
            rays.setdefault(v, []).append(names[parts[2]])
        elif parts[0] == 'L':
            label, v = int(parts[1]), int(parts[2]) - 1
            field, flow = names[parts[3]]
            expected = case['legs'][label-1]
            assert (field,flow)==(expected['field'],expected['flow'])
            current['legs'].append({'vertex': v,'label': label,'field': field,'flow': flow})
        elif parts[0] == 'E':
            u,v = int(parts[1])-1,int(parts[2])-1
            field,flow = names[parts[3]]
            current['edges'].append({'vertices':[u,v],'field':field,'flow':flow})
        elif parts[0] == 'END':
            count = int(parts[1])
        else: raise ValueError(f'Unknown QGRAF record: {parts}')
    if current is not None: yield finish()
    assert count is not None and count == last_index


def graph6_record(case, line):
    data = line.strip()
    if data.startswith(b'>>graph6<<'): data = data[len(b'>>graph6<<'):]
    vals = [b-63 for b in data]
    assert vals and all(0 <= v < 64 for v in vals)
    n = vals[0]
    assert n < 63, 'This finite suite uses small graph6 orders only'
    bits = [(x >> bit) & 1 for x in vals[1:] for bit in range(5,-1,-1)]
    assert len(bits) >= n*(n-1)//2
    edges=[];degree=[0]*n;at=0
    for v in range(1,n):
        for u in range(v):
            if bits[at]:
                edges.append({'vertices':[u,v],'field':case['fields'][0]['id'],'flow':0})
                degree[u]+=1;degree[v]+=1
            at+=1
    assert not any(bits[at:])
    by_degree={len(s):i for i,s in enumerate(case['signatures'])}
    return {'rules':[by_degree[d] for d in degree],'legs':[],'edges':edges,'aut':None}


def multig_record(case, line):
    p=[int(x) for x in line.split()]
    n,ne,group=p[:3];assert len(p)==3+3*ne
    edges=[];degrees=[0]*n;kernel=1
    for u,v,m in zip(p[3::3],p[4::3],p[5::3]):
        assert 0<=u<n and 0<=v<n and m>0
        degrees[u]+=m;degrees[v]+=m
        kernel*=math.factorial(m)*(2**m if u==v else 1)
        edges.extend({'vertices':[u,v],'field':case['fields'][0]['id'],'flow':0} for _ in range(m))
    assert all(d==3 for d in degrees)
    return {'rules':[0]*n,'legs':[],'edges':edges,'aut':str(group*kernel)}


def parse(args):
    case=json.loads(Path(args.case_file).read_text());work=Path(args.workdir)
    stdout=Path(args.stdout).read_text();stderr=Path(args.stderr).read_text()
    if args.engine=='qgraf':
        matches=re.findall(r'total\s*=\s*(\d+)\s+connected diagrams',stdout)
        assert len(matches)==1, 'QGRAF did not report exactly one successful final count'
        count=int(matches[0]);messages=(work/'messages.txt').read_text() if (work/'messages.txt').exists() else ''
        assert not re.search(r'\b(?:error|fatal)\b',messages,re.I), messages
        records=qgraf_records(case,(work/'graphs.raw').read_text()) if args.mode=='dump' else None
    else:
        matches=re.findall(r'(\d+)\s+(?:graphs|trees|multigraphs)\s+(?:generated|written)',stderr)
        assert matches, 'nauty did not report a completed generator count'
        count=int(matches[-1]);records=None
        if args.mode=='dump':
            if args.format=='multig-G': records=(multig_record(case,l) for l in stdout.splitlines() if l.strip())
            else:
                raw=Path(args.stdout).read_bytes()
                if args.format=='sparse6':
                    converted=subprocess.run([str(NAUTY/'copyg'),'-g','-q'],input=raw,capture_output=True,check=True)
                    raw=converted.stdout
                records=(graph6_record(case,l) for l in raw.splitlines() if l.strip())
    total=0;aut_sum=Fraction(0);has_aut=True
    if records is not None:
        with (work/'graphs.jsonl').open('x') as out:
            for record in records:
                total+=1
                if record['aut'] is None: has_aut=False
                else: aut_sum+=Fraction(1,int(record['aut']))
                out.write(json.dumps(record,separators=(',',':'))+'\n')
        assert total==count,(total,count)
    result={'engine':args.engine,'case':case['name'],'mode':args.mode,'count':count}
    if records is not None:
        result['dump_sha256']=sha(work/'graphs.jsonl')
        result['automorphism_inverse_sum']=str(aut_sum) if has_aut else None
    print('RESULT '+json.dumps(result,separators=(',',':')))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    q=sub.add_parser('prepare');q.add_argument('--cases',required=True);q.add_argument('--case',required=True);q.add_argument('--directory',required=True)
    q.add_argument('--engine',choices=['qgraf','nauty'],required=True);q.add_argument('--mode',choices=['count','dump'],required=True)
    q.set_defaults(run=prepare)
    q=sub.add_parser('parse')
    for name in ['engine','mode','case-file','workdir','stdout','stderr','format']:q.add_argument('--'+name,required=True)
    q.set_defaults(run=parse)
    args=p.parse_args();args.run(args)

if __name__=='__main__':main()
