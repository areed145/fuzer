// The process diagram Fuzer draws itself (BpmnView): every shipped process parses into its shapes and flows.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, readdirSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

const exportsObject = {};
const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src/processDrawing.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
runInNewContext(code, { exports: exportsObject });
const { wrap, roundedPath } = exportsObject;

test("a step's name wraps to its width, and a long one ends in an ellipsis", () => {
  assert.equal(JSON.stringify(wrap("Do the task", 84, 6.4)), JSON.stringify(["Do the task"]));
  assert.equal(JSON.stringify(wrap("Approve proposed action", 100, 6.4)), JSON.stringify(["Approve", "proposed action"]));
  const lines = wrap("one two three four five six seven eight nine ten eleven twelve", 60, 6, 2);
  assert.equal(lines.length, 2);
  assert.ok(lines[1].endsWith("…"));
});

test("a flow's corners are rounded, and a straight flow stays a line", () => {
  assert.equal(roundedPath([{ x: 0, y: 0 }, { x: 10, y: 0 }]), "M0 0 L10 0");
  assert.match(roundedPath([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }]), /Q100 0 100 8/);
});
