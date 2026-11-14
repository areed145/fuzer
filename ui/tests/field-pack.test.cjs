// P5.17c (#1092): what the field pack keeps stays under its budget, the oldest kept answers going first, and only reads
// that name a record in the pack are kept.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

function load() {
  const exports = {};
  const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src/fieldPack.ts"), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  runInNewContext(code, { exports, URL, JSON, setTimeout,
    require: (name) => (name.endsWith("authState") ? { getProject: () => "" } : { getScope: () => "", put: async () => true }) });
  return exports;
}

test("over budget the oldest kept answers go, and job lists and the pack are never counted or evicted", () => {
  const { toEvict } = load();
  const all = [
    ["jobs::okan:fred", { kept_at: "2026-11-14T01:00:00Z" }],
    ["pack::okan", { ids: ["a"] }],
    ["ans::okan:/entities/a", { kept_at: "2026-11-14T03:00:00Z", bytes: 40 }],
    ["ans::okan:/documents/d/file", { kept_at: "2026-11-14T02:00:00Z", bytes: 50 }],
    ["ans::okan:/entities/a/status", { kept_at: "2026-11-14T04:00:00Z", bytes: 30 }],
  ];
  assert.deepEqual([...toEvict(all, 200)], []);
  assert.deepEqual([...toEvict(all, 80)], ["ans::okan:/documents/d/file"]);
  assert.deepEqual([...toEvict(all, 30)], ["ans::okan:/documents/d/file", "ans::okan:/entities/a"]);
});

test("a read is in the pack when its path or a query value names a record in it", () => {
  const { inPack } = load();
  const pack = new Set(["fz-equipment-p101", "fz-doc 1"]);
  assert.equal(inPack("/entities/fz-equipment-p101/status", pack), true);
  assert.equal(inPack("/exceptions?state=open&subject=fz-equipment-p101", pack), true);
  assert.equal(inPack("/documents/fz-doc%201/image?project=okan", pack), true);
  assert.equal(inPack("/entities/fz-equipment-p102", pack), false);
  assert.equal(inPack("/stats", pack), false);
});
