const assert = require('node:assert/strict');
const L = require('../web/list.js');
const record = (id, extra = {}) => ({id, benchmark: 'generation', case: 'three-loop', metric: 'seconds', unit: 's', scope: 'generate',
  direction: 'lower', contract: 'diagrams', tool: 'gamma', way: 'native', capture: 'timing', repository: 'https://github.com/owner/gamma',
  set: 'set-a', qualified: true, historyQualified: true, contextKnown: true, count: 5, median: 12, min: 11, max: 13,
  context: 'fixed', createdAt: '2026-10-01T12:00:00Z', proposed: false, ...extra});
const a = record('a'), b = record('b', {tool: 'qgraf', median: 18, min: 17, max: 19, context: 'another-tool'});
const pairs = [{baseline: 'a', candidate: 'b', status: 'comparable', ratio: 1.51, reverse: .663, count: 5, reasons: []}];
assert.equal(L.paired(pairs, a, b).ratio, 1.51);
assert.equal(L.paired(pairs, b, a).ratio, .663); // Retained reverse, never 1 / median.
assert.equal(L.paired(pairs, a, a).ratio, 1);
assert.equal(L.paired(pairs,{...a,median:0,min:0,max:0},{...a,median:0,min:0,max:0}).status,'suppressed');
assert.equal(L.readiness([a, b], pairs).kind, 'paired');
const third = record('third', {tool:'third',context:'third-context'});
assert.deepEqual(L.readiness([a,b,third],[...pairs,{...pairs[0],candidate:'third'}]).references.map(r=>r.id),['a']);
for (const changed of [{case: 'four-loop'}, {benchmark: 'other'}, {metric: 'build'}, {unit: 'ms'}, {scope: 'warm'}, {contract: 'count'}, {direction: 'higher'}])
  assert.equal(L.readiness([a, record('b', {...b, ...changed})], pairs).ready, false);
const independent = record('later', {set: 'set-b', createdAt: '2026-10-03T12:00:00Z'});
assert.equal(L.readiness([a, independent], pairs).kind, 'absolute');
assert.equal(L.paired(pairs, a, independent).status, 'independent');
assert.equal(L.readiness([a, {...independent, context: 'changed-cpu'}], pairs).ready, false);
assert.equal(L.readiness([a, {...independent, contextKnown: false}], pairs).ready, false);
assert.equal(L.readiness([a], pairs).ready, false);
assert.equal(L.readiness([a, {...b, qualified: false}], pairs).ready, false);
assert.equal(L.readiness([a, {...b, count: 0}], pairs).ready, false);
const suppressed = [{...pairs[0], status: 'suppressed', ratio: null, reverse: null, reasons: ['Different outputs']}];
assert.equal(L.readiness([a, {...b, context: 'fixed'}], suppressed).ready, false);
assert.equal(L.paired(suppressed, a, b).status, 'suppressed');
assert.match(L.paired(suppressed, a, b).reason, /Different outputs/);
const next = record('next', {createdAt: '2026-10-03T12:00:00Z', commit: 'b'.repeat(40)});
a.commit = 'a'.repeat(40);
assert.deepEqual(L.segments([a, next]).lines, [[a, next]]);
const failed = record('failed', {createdAt: '2026-10-02T12:00:00Z', qualified: false, median: null, min: null, max: null});
assert.equal(L.segments([a, failed, next]).lines.length, 0);
assert.equal(L.segments([a, {...next, context: 'different-input'}]).lines.length, 0);
assert.equal(L.segments([a, next], [{benchmark: 'generation', case: 'three-loop', engine: 'gamma', createdAt: failed.createdAt}]).lines.length, 0);
const runA={...a,history:true,phase:'discovery',runId:10,runAttempt:1};
const runC={...next,history:true,phase:'discovery',runId:12,runAttempt:1};
const missingWorkload={...runA,case:'other',id:'other-case',runId:11};
assert.equal(L.segments([runA,runC],[],[runA,missingWorkload,runC]).lines.length,0);
assert.equal(L.segments([runA,runC],[],[runA,runC]).lines.length,1);
assert.equal(L.segments([a, {...next, createdAt: ''}]).lines.length, 0);
const runnerA = {...a, runnerContext: 'runner-only'}, runnerB = {...next, context: 'cpu-change', runnerContext: 'runner-only'};
assert.deepEqual(L.segments([runnerA, runnerB]).runners, [[runnerA, runnerB]]);
const proposed = record('pr', {createdAt: '2026-10-04T12:00:00Z', proposed: true, pr: 119, baselineCommit: next.commit});
assert.deepEqual(L.segments([a, next, proposed]).proposals, [[next, proposed]]);
assert.equal(L.segments([a, next, {...proposed, context: 'other'}]).proposals.length, 0);
assert.equal(L.segments([a, next, {...proposed, baselineCommit: 'c'.repeat(40)}]).proposals.length, 0);
assert.notEqual(L.implementation(a), L.implementation(b));
assert.equal(L.ordered([proposed, next, a])[0].id, 'a');
const row = {item: {benchmark_id: 'generation', case_id: 'three-loop'}, contexts: [{snapshot_id: 's1', task: 'native', metrics: ['seconds']}]};
assert.deepEqual(L.matchingRecords([record('one', {snapshot: 's1', task: 'native'}), record('two', {snapshot: 's2', task: 'native'}), record('three', {snapshot: 's1', task: 'other'})], row).map(r => r.id), ['one']);
const state = L.decode('?tab=compare&benchmark=generation&case=three-loop&list=' + encodeURIComponent(JSON.stringify({pins: ['a', 'a', 'b'], selected: ['b'], choices: {'capture': 'a'}})), [a, b]);
assert.deepEqual(state.pins, ['a', 'b']);
assert.deepEqual(state.selected, ['b']);
assert.equal(state.view, 'compare');
assert.equal(state.benchmark, 'generation');
assert.equal(state.choices.capture, 'a');
assert.match(L.decode('?list=%7Bbroken', [a]).error, /invalid/);
const unavailable = L.decode('?list=' + encodeURIComponent(JSON.stringify({pins: ['missing']})), [a]);
assert.deepEqual(unavailable.pins, []);
assert.match(unavailable.error, /not substituted/);
assert.equal(L.decode('?tab=history&list=' + encodeURIComponent(JSON.stringify({view: 'compare'})), [a]).view, 'history');
assert.equal(L.format(null, 's'), 'Unavailable');
assert.equal(L.format(0, 's'), '0 s');
const precise = L.hydrate({records: [{median: '0.00017994794660636633', min: null, max: '1e-9', pairedRatio: '1.0000000000000002'}],
  comparisons: [{ratio: '1.0000000000000002', reverse: '0.9999999999999998'}]});
assert.equal(precise.records[0].median, 0.00017994794660636633);
assert.equal(precise.records[0].min, null);
assert.equal(precise.records[0].pairedRatio, 1.0000000000000002);
assert.equal(precise.comparisons[0].reverse, 0.9999999999999998);
const aliases = [{...a,shortId:'a'.repeat(24)}, {...b,shortId:'b'.repeat(24)}];
const preferences = {...state,metrics:{[L.key(a)]:L.measurement(a)},sets:{[L.key(a)+L.measurement(a)]:'set-a'},
  choices:{[L.key(a)+L.measurement(a)+L.implementation(a)]:'a'},references:{[L.key(b)+L.measurement(b)+b.set]:'b'},pinReference:'b',compare:true};
const compact = L.encode(preferences,aliases,[L.key(a)]);
assert(JSON.stringify(compact).length<700);
const restored = L.decode('?tab=compare&list='+encodeURIComponent(JSON.stringify(compact)),[independent,...aliases]);
assert.deepEqual(restored.pins,['a','b']);
assert.deepEqual(restored.selected,['b']);
assert.deepEqual(restored.metrics,preferences.metrics);
assert.deepEqual(restored.choices,preferences.choices);
assert.deepEqual(restored.references,preferences.references);
assert.deepEqual(restored.sets,preferences.sets);
assert.equal(restored.pinReference,'b');
assert.equal(restored.compare,true);
assert.deepEqual(restored.evidence,[L.key(a)]);
assert.match(L.decode('?list='+encodeURIComponent(JSON.stringify({...compact,pins:['missing']})),aliases).error,/not substituted/);

// Sharing display context never merges snapshot/phase identity or implies pairing.
const firstEvidence = {...a,snapshot:'snapshot-a',evidence:{snapshot:'snapshots/a/snapshot.json',suite:'snapshots/a/suite.json'}};
const secondEvidence = {...b,snapshot:'snapshot-a',evidence:{suite:'snapshots/a/suite.json',snapshot:'snapshots/a/snapshot.json'}};
assert.deepEqual(L.evidenceGroups([firstEvidence,secondEvidence]).map(g=>g.length),[2]);
for(const change of [{snapshot:'snapshot-b'},{set:'confirmation'},{scope:'different clock'},{report:'other-report'},
 {evidence:{snapshot:'snapshots/another/snapshot.json'}}])
 assert.equal(L.evidenceGroups([firstEvidence,{...secondEvidence,...change}]).length,2);
assert.deepEqual(L.sharedFields([{count:0,origin:'import',scope:null},{count:0,origin:'import'}],['count','origin','scope']),{count:0,origin:'import'});
assert.equal(L.comparisonKey([a,b],a.id,'paired'),L.comparisonKey([b,a],a.id,'paired'));
assert.notEqual(L.comparisonKey([a,b],a.id,'paired'),L.comparisonKey([a,b],b.id,'paired'));
assert.notEqual(L.comparisonKey([a,b],a.id,'paired'),L.comparisonKey([a],a.id,'paired'));
assert.notEqual(L.comparisonKey([a,b],a.id,'paired'),L.comparisonKey([a,b],a.id,'absolute'));
assert.equal(L.comparisonKey([a,b],a.id,'absolute'),L.comparisonKey([a,b],b.id,'absolute'));
assert.equal(L.observedRange({...a,min:12,max:12}), '');
assert.equal(L.observedRange({...a,qualified:false}), '');
assert.equal(L.observedRange({...a,min:11,max:13}), '11–13 s');
assert.equal(L.metricTitle({metricLabel:'Whole process instructions',unit:'instructions'}),'Whole process instructions');
assert.equal(L.metricTitle({metricLabel:'Generation time',unit:'s'}),'Generation time · s');

console.log('Independent histories, stored ratios, exact pins, evidence groups and comparison identities passed');
