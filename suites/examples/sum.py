"""Small portable demonstration of a benchmark result contract."""
import json
import sys
import time
from pathlib import Path

workload = json.loads(Path('workload.json').read_text())
values = list(range(workload['size']))
started = time.perf_counter_ns()
if sys.argv[1] == 'loop':
    total = 0
    for value in values:
        total += value
elif sys.argv[1] == 'builtin':
    total = sum(values)
else:
    raise ValueError('Unknown implementation')
duration = time.perf_counter_ns() - started
print('RESULT ' + json.dumps({'total': total, 'items': len(values), 'compute_ns': duration}))
