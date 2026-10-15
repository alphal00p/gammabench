"""Discovery preserves typed values and requires one matching capture context."""
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def catalog_fixture():
    fields = [
        {"id": "operation", "label": "Operation", "type": "enum", "scope": "workload", "values": ["Construct", "Evaluate", "Algebra"]},
        {"id": "loops", "label": "Loop order", "type": "number", "scope": "workload", "integer": True, "min": 0},
        {"id": "topic", "label": "Topic", "type": "text", "scope": "workload"},
        {"id": "memory", "label": "Memory", "type": "quantity", "scope": "workload", "unit": "B", "units": [
            {"id": "B", "label": "B", "factor": 1}, {"id": "GiB", "label": "GiB", "factor": 1073741824},
            {"id": "MiB", "label": "MiB", "factor": 1048576}]},
        {"id": "revision", "label": "Revision", "type": "enum", "scope": "capture", "values": ["parent", "candidate"]},
        {"id": "threads", "label": "Threads", "type": "number", "scope": "capture", "integer": True},
        {"id": "validated", "label": "Validation passed", "type": "boolean", "scope": "capture"},
        {"id": "note", "label": "Note", "type": "text", "scope": "capture"},
        {"id": "date", "label": "Date", "type": "date", "scope": "capture"},
        {"id": "tags", "label": "Tags", "type": "tags", "scope": "workload", "values": ["kernel", "regression"]},
    ]
    rows = [
        {"case_id": "construct", "metadata": {"operation": "Construct", "loops": 4, "topic": "shared-only", "memory": {"value": 4, "unit": "GiB"}, "tags": ["kernel", "regression"]}, "captures": [
            {"id": "parent", "metadata": {"revision": "parent", "threads": 1, "validated": True, "note": "slow", "date": "2026-10-11"}},
            {"id": "candidate", "metadata": {"revision": "candidate", "threads": 8, "validated": True, "note": "fast", "date": "2026-10-14"}}]},
        {"case_id": "evaluate", "metadata": {"operation": "Evaluate", "loops": 2, "memory": {"value": 4096, "unit": "MiB"}}, "captures": [
            {"id": "eval", "metadata": {"revision": "candidate", "threads": 4, "validated": False}}]},
        {"case_id": "tree", "metadata": {"operation": "Construct", "loops": 0}, "captures": []},
        {"case_id": "algebra", "metadata": {"operation": "Algebra", "loops": {"state": "not-applicable"}}, "captures": [
            {"id": "algebra", "metadata": {"threads": 2, "validated": False}}]},
        {"case_id": "unknown", "metadata": {}, "captures": []},
    ]
    for row in rows:
        row.update(benchmark_id="kernel-suite", benchmark_title="Kernel suite", title=row["case_id"],
                   description="Native fixture", href="benchmarks/kernel-suite/index.html?case=" + row["case_id"] + "&tab=compare")
    return {"schema_version": 1, "fields": fields, "cases": rows}


@unittest.skipUnless(NODE, "Node is required for the explorer query-engine checks")
class ExplorerQueryTests(unittest.TestCase):
    def check_js(self, script, catalog=None):
        prefix = "const assert=require('node:assert/strict');const E=require(process.argv[1]);const catalog=JSON.parse(require('node:fs').readFileSync(0,'utf8'));const fields=catalog.fields;const state=(filters=[],query='',group='none')=>({filters,query,group,errors:[]});const ids=rows=>rows.map(row=>row.item.case_id);\n"
        completed = subprocess.run([NODE, "-e", prefix + script, str(ROOT / "web/explorer.js")],
                                   input=json.dumps(catalog or catalog_fixture()), text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_filters_and_keywords_cannot_cross_join_captures(self):
        self.check_js("""
          const parent={field:'revision',op:'eq',value:'parent'};
          const eight={field:'threads',op:'ge',value:8};
          assert.deepEqual(E.matching(catalog,state([parent,eight])),[]);
          assert.deepEqual(E.matching(catalog,state([], 'parent fast')),[]);
          const rows=E.matching(catalog,state([eight],'fast'));
          assert.deepEqual(ids(rows),['construct']);
          assert.deepEqual(rows[0].contexts.map(c=>c.id),['candidate']);
        """)

    def test_effective_workload_metadata_overrides_common_values_even_with_null(self):
        self.check_js("""
          const item=catalog.cases[0];
          item.captures[0].metadata.topic=null;
          item.captures[1].metadata.topic='different';
          assert.equal(E.read(item,item.captures[0],fields.find(f=>f.id==='topic')),null);
          assert.deepEqual(E.matching(catalog,state([],'shared-only')),[]);
          item.captures[0].metadata.loops=2;
          const rows=E.matching(catalog,state([{field:'loops',op:'eq',value:4}]));
          assert.deepEqual(rows[0].contexts.map(c=>c.id),['candidate']);
        """)

    def test_zero_false_unknown_and_not_applicable_are_distinct(self):
        self.check_js("""
          assert.deepEqual(ids(E.matching(catalog,state([{field:'loops',op:'eq',value:0}]))),['tree']);
          assert.deepEqual(ids(E.matching(catalog,state([{field:'validated',op:'eq',value:false}]))),['evaluate','algebra']);
          assert.deepEqual(ids(E.matching(catalog,state([{field:'loops',op:'missing'}]))),['unknown']);
          assert.deepEqual(ids(E.matching(catalog,state([{field:'loops',op:'na'}]))),['algebra']);
          assert.deepEqual(ids(E.matching(catalog,state([{field:'loops',op:'ne',value:0}]))),['construct','evaluate']);
        """)

    def test_quantity_filters_use_only_declared_units_without_mutating_inputs(self):
        self.check_js("""
          const before=JSON.stringify(catalog);
          const filter={field:'memory',op:'ge',value:{value:4096,unit:'MiB'}};
          assert.equal(E.validFilter(filter,fields),true);
          assert.deepEqual(ids(E.matching(catalog,state([filter]))),['construct','evaluate']);
          assert.equal(E.validFilter({...filter,value:{value:4,unit:'TB'}},fields),false);
          assert.equal(E.quantity({value:500,unit:'bogus'},fields.find(f=>f.id==='memory')),null);
          assert.equal(JSON.stringify(catalog),before);
        """)

    def test_numeric_query_thresholds_are_not_restricted_by_metadata_value_constraints(self):
        self.check_js("""
          const over={field:'loops',op:'gt',value:2.5};
          const outside={field:'loops',op:'ge',value:-1};
          assert.equal(E.validFilter(over,fields),true);
          assert.equal(E.validFilter(outside,fields),true);
          assert.deepEqual(ids(E.matching(catalog,state([over]))),['construct']);
          assert.deepEqual(ids(E.matching(catalog,state([outside]))),['construct','evaluate','tree']);
          assert.deepEqual(E.matching(catalog,state([{field:'loops',op:'eq',value:2.5}])),[]);
          assert.deepEqual(E.matching(catalog,state([{field:'loops',op:'lt',value:-1}])),[]);
          assert.equal(fields.find(f=>f.id==='loops').integer,true);
          assert.equal(fields.find(f=>f.id==='loops').min,0);
        """)

    def test_grouping_preserves_matching_contexts_and_sorts_numbers(self):
        self.check_js("""
          const rows=E.matching(catalog,state([{field:'threads',op:'has'}]));
          const groups=E.grouped(rows,fields.find(f=>f.id==='threads'));
          assert.deepEqual(groups.map(g=>g.canonical),[1,2,4,8]);
          assert.deepEqual(groups[0].rows[0].contexts.map(c=>c.id),['parent']);
          const quantities=E.grouped(E.matching(catalog,state([{field:'memory',op:'has'}])),fields.find(f=>f.id==='memory'));
          assert.equal(quantities.length,1);
          assert.equal(quantities[0].rows.length,2);
        """)

    def test_grouping_empty_tags_does_not_hide_the_case(self):
        self.check_js("""
          catalog.cases[2].metadata.tags=[];
          const groups=E.grouped(E.matching(catalog,state()),fields.find(f=>f.id==='tags'));
          assert.deepEqual(groups.find(g=>g.label==='No values').rows.map(row=>row.item.case_id),['tree']);
          const visible=new Set(groups.flatMap(g=>g.rows.map(row=>row.item.case_id)));
          assert.equal(visible.size,catalog.cases.length);
        """)

    def test_shareable_queries_preserve_types_and_invalid_saved_filters_do_not_broaden(self):
        self.check_js("""
          const original=state([{field:'loops',op:'eq',value:0},{field:'validated',op:'eq',value:false},
            {field:'memory',op:'le',value:{value:4,unit:'GiB'}}],'point kernel','threads');
          assert.deepEqual(E.decode('?'+E.encode(original),fields),original);
          for(const query of ['?filters=broken','?filters='+encodeURIComponent(JSON.stringify([{field:'absent',op:'has'}])),'?group=absent']){
            const decoded=E.decode(query,fields);
            assert.ok(decoded.errors.length);
            assert.deepEqual(E.matching(catalog,decoded),[]);
          }
        """)

    def test_typed_validation_rejects_nonfinite_values_invalid_dates_and_unknown_enums(self):
        self.check_js("""
          for(const value of [Infinity,NaN,true,'8'])assert.equal(E.validFilter({field:'threads',op:'eq',value},fields),false);
          assert.equal(E.validFilter({field:'loops',op:'eq',value:2.5},fields),true);
          assert.equal(E.validFilter({field:'date',op:'eq',value:'2026-02-30'},fields),false);
          assert.equal(E.validFilter({field:'date',op:'eq',value:'2026-10-14'},fields),true);
          assert.equal(E.validFilter({field:'revision',op:'eq',value:'unregistered'},fields),false);
        """)

    def test_inspector_six_contexts_keep_only_retained_or_differing_fields(self):
        self.check_js("""
          for(let i=0;i<43;i++)fields.push({id:'unused.'+i,label:'Unused',type:'text',scope:'capture'});
          const item=catalog.cases[0];
          item.metadata={loops:4,topic:'kernel',operation:'Construct'};
          item.captures=Array.from({length:6},(_,i)=>({id:'capture-'+i,metadata:{threads:i+1,revision:'candidate',loops:i===5?null:4}}));
          const sections=E.inspectorSections({item,contexts:item.captures},fields);
          assert.equal(fields.length,53);
          assert.equal(sections.length,7);
          assert.equal(sections[0].fields.length,3);
          assert.equal(sections[1].fields.length,2);
          assert.equal(sections[6].fields.length,3);
          assert.ok(sections[6].fields.some(f=>f.id==='loops'));
          assert.equal(sections.flatMap(s=>s.fields).length,16);
        """)

    def test_benchmark_and_case_identity_does_not_collide(self):
        self.check_js("""
          assert.notEqual(E.identity({benchmark_id:'one:two',case_id:'three'}),E.identity({benchmark_id:'one',case_id:'two:three'}));
        """)


class ExplorerMarkup(HTMLParser):
    def __init__(self, value):
        super().__init__()
        self.links = []
        self.catalog = None
        self.scripts = []
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs["href"])
        if attrs.get("id") == "benchmark-explorer":
            self.catalog = json.loads(attrs["data-catalog"])
        if tag == "script":
            self.scripts.append(attrs)


class ExplorerPresentationTests(unittest.TestCase):
    def compile(self, catalog):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "src/gammabench/assets"
            assets.mkdir(parents=True)
            for name in ("components.typ", "matrix.typ"):
                shutil.copyfile(ROOT / "src/gammabench/assets" / name, assets / name)
            shutil.copyfile(ROOT / "web/explorer.typ", root / "explorer.typ")
            data = root / "catalog.json"
            before = json.dumps(catalog)
            data.write_text(before)
            (root / "main.typ").write_text('#import "explorer.typ": explorer\n#explorer(json("catalog.json"), root: "../../")\n')
            completed = subprocess.run(["typst", "compile", "--root", str(root), "--features", "html", "--format", "html",
                                        str(root / "main.typ"), str(root / "index.html")], text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(data.read_text(), before)
            result = (root / "index.html").read_text()
            return result, ExplorerMarkup(result)

    def test_static_links_and_inert_catalog_escape_untrusted_metadata(self):
        catalog = catalog_fixture()
        catalog["cases"][0]["title"] = '</a><script>alert("injected")</script>'
        catalog["fields"][0]["label"] = '<img src="bad" onerror="alert(1)">'
        rendered, parsed = self.compile(catalog)
        self.assertEqual(parsed.catalog, catalog)
        self.assertEqual(parsed.scripts, [])
        self.assertIn('../../benchmarks/kernel-suite/index.html?case=construct&tab=compare', parsed.links)
        self.assertIn('No captures', rendered)
        self.assertIn('Search and filters need JavaScript', rendered)
        self.assertNotIn('else {', rendered)
        self.assertNotIn("<ol>", rendered)
        self.assertNotIn('<img src="bad"', rendered)

    def test_empty_catalog_has_explicit_empty_state(self):
        rendered, parsed = self.compile({"schema_version": 1, "fields": [], "cases": []})
        self.assertIn('No benchmark cases are registered yet.', rendered)
        self.assertEqual(parsed.links, [])
        self.assertEqual(parsed.catalog["cases"], [])
