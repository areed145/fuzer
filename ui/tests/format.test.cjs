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
runInNewContext(code, { exports: exportsObject, Date, Number });
const { money, percent, quantity, count, when, whenDay, ago } = exportsObject;

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

const year = new Date().getFullYear();

test("a plant-clock time is shown as written and marked, never moved to the viewer's clock", () => {
  assert.equal(when(`${year}-11-17 18:00`), "Nov 17, 6:00 PM plant time");
  assert.equal(when(`${year}-11-17T18:00:30`), "Nov 17, 6:00 PM plant time");
  assert.equal(when("2019-11-17T18:00"), "Nov 17, 2019, 6:00 PM plant time");
});

test("a day alone stays that day in every timezone", () => {
  assert.equal(when(`${year}-11-17`), "Nov 17");
  assert.equal(whenDay(`${year}-11-17`), "Nov 17");
  assert.equal(whenDay(`${year}-11-17T23:30`), "Nov 17");
  assert.equal(whenDay("2019-01-02"), "Jan 2, 2019");
});

test("an instant reads in the viewer's clock, in the same shape", () => {
  const at = `${year}-11-14T14:11:00Z`;
  assert.equal(when(at), new Date(at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }));
  assert.ok(!when(at).includes("plant time"));
});

test("no value and unreadable values say so instead of a wrong date", () => {
  assert.equal(when(null), "");
  assert.equal(when(undefined, "never"), "never");
  assert.equal(when("soon"), "soon");
  assert.equal(whenDay("", "unknown"), "unknown");
});

test("ago is relative within a week and falls back to the one format", () => {
  assert.equal(ago(new Date(Date.now() - 3 * 60_000).toISOString()), "3 min ago");
  assert.equal(ago(new Date(Date.now() + 2 * 3_600_000).toISOString()), "in 2 h");
  assert.equal(ago(`${year}-11-17 18:00`), "Nov 17, 6:00 PM plant time");
  assert.equal(ago(null, "Time unknown"), "Time unknown");
});
