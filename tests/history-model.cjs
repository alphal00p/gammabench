// Test capture identity and continuity independently of browser rendering.
const assert = require('node:assert/strict');
const history = require('../web/history.js');
const record = (run, commit, extra = {}) => ({
  repository: 'https://github.com/alphal00p/gammaloop', runId: String(run), runAttempt: '1',
  snapshot: 'capture-' + run, commit: commit.repeat(40), historyRole: 'main',
  createdAt: `2026-10-${String(run).padStart(2, '0')}T12:00:00Z`, qualified: true,
  validation: 'passed', complete: 'true', count: '5', median: '2', min: '1.9', max: '2.1', ...extra,
});

const a = record(10, 'a'), b = record(11, 'b'), c = record(12, 'c');
const proposal = record(11, 'd', {historyRole: 'proposed', baselineCommit: a.commit, pr: '140', runId: '15'});
const otherWorkload = {...a, snapshot: 'another-workload', createdAt: '2026-10-10T12:45:00Z'};
assert.equal(history.captureKey(a), history.captureKey(otherWorkload));
const rerun = {...a, runAttempt: '2'};
assert.notEqual(history.captureKey(a), history.captureKey(rerun));
assert.notEqual(history.captureKey(a), history.captureKey({...a, repository: 'https://github.com/other/tool'}));
assert.notEqual(history.captureKey(a), history.captureKey({...a, historyRole: 'proposed'}));

const axis = history.columns([c, proposal, otherWorkload, b, a]);
assert.deepEqual(axis.map(column => column.commit), [a.commit, b.commit, c.commit, proposal.commit]);
assert.deepEqual(axis.map(column => column.mainIndex), [0, 1, 2, undefined]);
assert.equal(axis[0].date, Date.parse(a.createdAt));
assert.equal(axis.at(-1).label, 'PR #140');
assert.deepEqual(history.columns([rerun, a]).map(column => column.runAttempt), ['1', '2']);
assert.deepEqual(history.columns([rerun, a]).map(column => column.label), ['aaaaaaa · 1', 'aaaaaaa · 2']);

let parts = history.segments([c, proposal, a, b], axis);
assert.deepEqual(parts.lines, [[a, b, c]]);
assert.deepEqual(parts.proposals, [[a, proposal]]);
// The unmeasured middle run remains a gap, even without a failed record.
assert.deepEqual(history.segments([a, c], axis).lines, []);
const failed = {...b, qualified: false, reason: 'Numerical mismatch'};
assert.deepEqual(history.segments([a, failed, c], axis).lines, []);
// Unqualified proposals never break main's line or use a stale numeric median.
const failedPR = {...proposal, validation: 'failed'};
parts = history.segments([a, b, failedPR, c], axis);
assert.deepEqual(parts.lines, [[a, b, c]]);
assert.deepEqual(parts.proposals, []);
assert.equal(history.latest([a, b, c, failedPR], axis).status, 'unqualified');
assert.equal(history.latest([a, b, c], axis).status, 'missing');
assert.equal(history.latest([a, b, c, proposal], axis).status, 'qualified');
assert.equal(history.latest([proposal, {...proposal}], axis).status, 'multiple');
// Context grouping is supplied by retained evidence; no base in this group means no connector.
assert.deepEqual(history.segments([proposal], axis).proposals, []);
assert.deepEqual(history.segments([a, {...proposal, createdAt: '2026-10-09T12:00:00Z'}], axis).proposals, []);
assert.deepEqual(history.segments([a, {...a}, b, c], axis).lines, [[b, c]]);
// No source, date, qualification or finite metric: preserve evidence, suppress numeric plot.
for (const extra of [{commit: ''}, {createdAt: ''}, {median: 'NaN'}, {min: '3'}, {max: 'Infinity'}, {count: '0'}, {complete: 'false'}]) {
  assert.equal(history.usable({...a, ...extra}), false);
}
const noCI = {...a, runId: '', runAttempt: ''};
assert.notEqual(history.captureKey(noCI), history.captureKey({...noCI, snapshot: 'separate'}));
assert.notEqual(history.captureKey(noCI), history.captureKey({...noCI, commit: b.commit}));
assert.equal(history.columns([{...a, createdAt: ''}, c]).length, 2);
const otherTool = {...c, repository: 'https://github.com/other/tool'};
assert.equal(history.latest([a], history.columns([a, otherTool])).record, a);
const settingsGap = {...b, captureIndex: '2'};
assert.deepEqual(history.segments([{...a, captureIndex: '0'}, settingsGap], axis).lines, []);
// Hardware changes have an explicit dotted capture-order connector, separate
// from the solid trend. Only the host may differ in a runner-context family.
const amd = {...a, context: 'AMD context', runnerContext: 'same inputs and settings', runnerLabel: 'AMD EPYC'};
const intel = {...b, context: 'Intel context', runnerContext: amd.runnerContext, runnerLabel: 'Intel Xeon'};
assert.deepEqual(history.runnerTransitions([intel, amd], axis), [[amd, intel]]);
assert.deepEqual(history.runnerTransitions([amd, {...intel, context: amd.context}], axis), []);
assert.deepEqual(history.runnerTransitions([amd, {...intel, runnerContext: 'different inputs'}], axis), []);
assert.deepEqual(history.runnerTransitions([amd, {...intel, qualified: false}], axis), []);
assert.deepEqual(history.runnerTransitions([amd, {...intel, historyRole: 'proposed'}], axis), []);
assert.deepEqual(history.runnerTransitions([amd, {...c, context: intel.context, runnerContext: amd.runnerContext}], axis), []);
assert.deepEqual(history.runnerTransitions([amd, {...amd}, intel], axis), []);
assert.deepEqual(history.runnerTransitions([amd, {...intel, createdAt: amd.createdAt}], axis), []);
assert.deepEqual(history.pullLinks({...a, pullRequests: '[141,141,140]'}).map(pull => pull.number), [141, 140]);
assert.equal(history.pullLinks({...proposal, pullRequests: '[140]'})[0].href, 'https://github.com/alphal00p/gammaloop/pull/140');
for (const extra of [{repository:'javascript:alert(1)',pullRequests:'[140]'}, {repository:'https://github.com/a/..',pullRequests:'[140]'},
  {pullRequests:'["140",0,-1,null]'}, {pullRequests:'broken'}, {pullRequests:'{}'}]) {
  assert.deepEqual(history.pullLinks({...a, ...extra}), []);
}
console.log('History identity, ordering, gaps, reruns, proposals and qualification passed');
