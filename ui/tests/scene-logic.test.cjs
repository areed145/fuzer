// P5.16b to d: status as a colour, a flow path up or down, and a well rerouted in the browser.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

const exportsObject = {};
const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src/sceneLogic.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
runInNewContext(code, { exports: exportsObject, Map, Set, Math, String });
const { MOVED, badge, headers, parseMove, reroute, trace, worst } = exportsObject;

const item = (id, kind = "tag", x = 0) => ({ id, kind, label: id, x, y: 0, z: 0 });
const link = (id, src, dst, rel = "flows_to") => ({ id, src, dst, rel, product: "multiphase", path: [] });
// W1, W2 -> M1 -> SEP -> K -> EXPORT; W3 -> M2 -> SEP; lift gas K -> W1; a control link that no path follows.
const items = [item("W1", "well"), item("W2", "well"), item("W3", "well"), item("M1"), item("M2"), item("SEP"), item("K"), item("EXPORT")];
const links = [link(1, "W1", "M1"), link(2, "W2", "M1"), link(3, "W3", "M2"), link(4, "M1", "SEP"), link(5, "M2", "SEP"),
  link(6, "SEP", "K", "pipes_to"), link(7, "K", "EXPORT"), link(8, "K", "W1", "lift_gas_to"), link(9, "K", "SEP", "controls")];

test("status: down outranks an alarm, which outranks open work, and the badge says each", () => {
  assert.equal(worst({ open_work: 2, alarms: 1, down: { id: "d" } }), "down");
  assert.equal(worst({ open_work: 2, alarms: 1, down: null }), "alarm");
  assert.equal(worst({ open_work: 2, alarms: 0, down: null }), "work");
  assert.equal(worst(undefined), null);
  assert.equal(badge({ open_work: 2, alarms: 1, down: null }), "1! 2");
  assert.equal(badge({ open_work: 0, alarms: 0, down: { id: "d" } }), "Down");
});

test("a path downstream follows each link to the export, over flow, piping and lift gas but not control", () => {
  const down = trace(links, "SEP", "down");
  assert.deepEqual([...down.items].sort(), ["EXPORT", "K", "M1", "SEP", "W1"].sort());
  assert.deepEqual([...down.links].sort((a, b) => a - b), [1, 4, 6, 7, 8]); // lift gas goes to W1, which flows back on to M1
  assert.ok(!down.links.has(9));
});

test("a path upstream finds the wells and manifolds behind a compressor", () => {
  const up = trace(links, "K", "up", ["flows_to", "pipes_to"]);
  assert.deepEqual([...up.items].sort(), ["K", "M1", "M2", "SEP", "W1", "W2", "W3"]);
  assert.ok(!up.items.has("EXPORT"));
});

test("a well moved to another header: old and new runs, and wells per header before and after", () => {
  const h = headers(items, links);
  assert.deepEqual([...h.into].sort(), ["M1", "M2"]);
  assert.equal(h.now.get("W2").dst, "M1");
  assert.deepEqual({ ...parseMove("W2~M2") }, { well: "W2", to: "M2" });
  assert.equal(parseMove("W2"), null);
  const r = reroute(items, links, { well: "W2", to: "M2" });
  assert.equal(r.old.id, 2);
  assert.equal(r.added.id, MOVED);
  assert.equal(r.added.dst, "M2");
  assert.equal(r.added.path.length, 4);
  assert.deepEqual(JSON.parse(JSON.stringify(r.changed)), [{ header: "M1", before: 2, after: 1 }, { header: "M2", before: 1, after: 2 }]);
  assert.equal(reroute(items, links, { well: "W2", to: "M1" }), null, "to where it already goes is no reroute");
  assert.equal(reroute(items, links, { well: "W2", to: "K" }), null, "only to a header wells flow to");
});
