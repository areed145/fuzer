// #1074: hotspots whose markers would overlap on screen are gathered, the most urgent leading each group.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

const exportsObject = {};
const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src/map/engine.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
runInNewContext(code, { exports: exportsObject, Math, Number });
const { clusterOnScreen } = exportsObject;

test("markers that would touch are one group, led by the first (most urgent); apart ones stay alone", () => {
  const spots = [{ id: "a", x: 100, y: 100 }, { id: "b", x: 120, y: 100 }, { id: "c", x: 300, y: 100 }, { id: "d", x: 95, y: 130 }];
  const groups = clusterOnScreen(spots, (s) => s, () => 30);
  assert.equal(JSON.stringify(groups.map((g) => g.map((s) => s.id))), JSON.stringify([["a", "b", "d"], ["c"]]));
});

test("a gap between markers keeps them apart", () => {
  const groups = clusterOnScreen([{ x: 0, y: 0 }, { x: 40, y: 0 }], (s) => s, () => 30, 4);
  assert.equal(groups.length, 2);
});
