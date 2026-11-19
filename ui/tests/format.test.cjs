// One value reads the same on every page (src/format.ts).
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

const exportsObject = {};
const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src/format.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
runInNewContext(code, { exports: exportsObject });
const { money, percent, quantity, count } = exportsObject;

test("money is whole dollars from 100 up and never abbreviated", () => {
  assert.equal(money(5540400), "$5,540,400");
  assert.equal(money(5520150.4), "$5,520,150");
  assert.equal(money(1000), "$1,000");
  assert.equal(money(100), "$100");
  assert.equal(money(0), "$0");
});

test("money shows cents below 100", () => {
  assert.equal(money(12.5), "$12.50");
  assert.equal(money(0.42), "$0.42");
  assert.equal(money(0.004), "<$0.01");
  assert.equal(money(-3), "-$3.00");
});

test("money in another currency names it after the number", () => {
  assert.equal(money(5520150, "NOK"), "5,520,150 NOK");
  assert.equal(money(40, "unknown"), "40.00 (currency unknown)");
});

test("percent, quantity and count", () => {
  assert.equal(percent(0.873), "87%");
  assert.equal(percent(0.873, 1), "87.3%");
  assert.equal(percent(1), "100%");
  assert.equal(quantity(1250, "bbl"), "1,250 bbl");
  assert.equal(quantity(87, "%"), "87%");
  assert.equal(quantity(3.456), "3.46");
  assert.equal(count(12480), "12,480");
});
