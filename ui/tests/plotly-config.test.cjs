// No Plotly chart shows the floating toolbar: every draw passes the shared config, and the config turns it off.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, readdirSync, statSync } = require("node:fs");
const { join, resolve } = require("node:path");
const ts = require("typescript");

const SRC = resolve(__dirname, "../src");
const files = (dir) => readdirSync(dir).flatMap((f) => {
  const p = join(dir, f);
  return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(f) ? [p] : [];
});

test("the shared config turns the toolbar off", () => {
  const js = ts.transpileModule(readFileSync(join(SRC, "plotlyConfig.ts"), "utf8"),
                                { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const m = { exports: {} };
  new Function("module", "exports", js)(m, m.exports);
  assert.equal(m.exports.PLOTLY_CONFIG.displayModeBar, false);
});

test("every Plotly chart is drawn with the shared config", () => {
  const draws = [];
  for (const f of files(SRC)) {
    const text = readFileSync(f, "utf8");
    for (const m of text.matchAll(/Plotly\.(react|newPlot|plot)\(/g)) {
      const call = text.slice(m.index, text.indexOf(";", m.index));
      draws.push(f);
      assert.ok(call.includes("PLOTLY_CONFIG"), `${f}: a Plotly chart drawn without PLOTLY_CONFIG shows the toolbar`);
      assert.ok(text.includes("<FullscreenButton") && text.includes("fullscreenLayout("),
        `${f}: a Plotly chart without its full screen button (FullscreenButton and fullscreenLayout)`);
    }
  }
  assert.ok(draws.length >= 2, "found the charts");
});
