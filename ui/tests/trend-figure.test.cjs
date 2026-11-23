const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

const exportsObject = {};
const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src/trendFigure.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
runInNewContext(code, { exports: exportsObject, Date, Math, Set, Map, Object, JSON });
const { names, reading, traceName, pointTag } = exportsObject;

const vals = [["2024-07-24T10:00", 1], ["2024-07-24T11:00", 2]];
const trace = (id, axis, extra = {}) => ({ id, point: `P.${id}`, description: null, uom: axis, axis, show: true, values: vals,
  min: 1, max: 2, mean: 1.5, last: 2, role_label: id, on: { id: "u", label: "K-1", relation: "self" }, ...extra });
const view = {
  provider: "synthetic", centre: "2024-07-24T12:00", start: "2024-07-24T00:00", end: "2024-07-25T00:00", step_minutes: 10,
  upsets: ["2024-07-24T13:45"], unit: "u", unit_label: "K-1", classes: ["CO"], template: { key: "CO", title: "Compressor" },
  groups: [
    { key: "pressures", label: "Pressures", show: true, traces: [trace("suction", "barg"), trace("discharge", "barg")] },
    { key: "driver", label: "Driver", show: true, traces: [trace("speed", "rpm", { on: { id: "g", label: "GT-1", relation: "driver" } }), trace("power", "kw")] },
    { key: "lube", label: "Lube oil", show: false, traces: [trace("lube", "barg", { show: false, limits: [{ type: "hi", value: 3, basis: "alarm" }] })] },
  ],
  other: [trace("spare", "degc", { show: false })], missing: [],
};
const plain = (x) => JSON.parse(JSON.stringify(x));

test("a neighbour's trace names the neighbour", () => {
  assert.equal(traceName(view.groups[1].traces[0]), "speed · GT-1");
  assert.equal(traceName(view.groups[0].traces[0]), "suction");
});

test("two traces that would read the same are told apart by what differs in their descriptions", () => {
  const twins = { ...view, groups: [{ key: "v", label: "V", show: true, traces: [
    trace("a", "degc", { role_label: "Bearing temperature", description: "Train B Compressor Thrust Bearing Temperature" }),
    trace("b", "degc", { role_label: "Bearing temperature", description: "Train B Compressor Journal Bearing Temperature" }),
    trace("c", "mm/s", { role_label: "Casing vibration" })] }], other: [] };
  const n = names(twins);
  assert.equal(n.get("a"), "Thrust bearing temperature");
  assert.equal(n.get("b"), "Journal bearing temperature");
  assert.equal(n.get("c"), "Casing vibration");
});

test("a value reads to three figures and a tag without its server", () => {
  assert.equal(reading(1234.5), "1,235");
  assert.equal(reading(0.01234), "0.012");
  assert.equal(pointTag("OK_GGCP:20-FT-2100B.PV"), "20-FT-2100B");
});
