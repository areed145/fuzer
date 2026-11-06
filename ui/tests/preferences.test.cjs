const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

function load(file, { stored = {}, dark = false, blocked = false } = {}) {
  const window = new EventTarget();
  const scheme = new EventTarget();
  scheme.matches = dark;
  window.matchMedia = () => scheme;
  const document = { documentElement: { dataset: {} } };
  const localStorage = {
    getItem: (key) => { if (blocked) throw Error("blocked"); return stored[key] ?? null; },
    setItem: (key, value) => { if (blocked) throw Error("blocked"); stored[key] = value; },
  };
  const exports = {};
  const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src", file), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const modules = {
    react: { useSyncExternalStore: (_, snapshot) => snapshot() },
    "./lib": { notifyChanged: () => window.dispatchEvent(new Event("fuzer:changed")) },
    "./ui": {}, "react/jsx-runtime": {},
  };
  runInNewContext(code, {
    exports, window, document, localStorage, Event, console: { warn() {} },
    require: (name) => { assert.ok(name in modules, `Unexpected import ${name}`); return modules[name]; },
  });
  const storageEvent = (key, newValue) => {
    const event = new Event("storage");
    Object.assign(event, { key, newValue });
    window.dispatchEvent(event);
  };
  return { exports, window, scheme, document, stored, storageEvent };
}

test("theme starts from saved preference, persists changes and ignores OS under an explicit choice", () => {
  const state = load("theme.tsx", { stored: { "fuzer.theme": "dark" } });
  assert.equal(state.document.documentElement.dataset.theme, "dark");
  state.exports.setTheme("light");
  state.scheme.matches = true;
  state.scheme.dispatchEvent(new Event("change"));
  assert.equal(state.document.documentElement.dataset.theme, "light");
  assert.equal(state.stored["fuzer.theme"], "light");
});

test("system theme follows OS changes and syncs changes from other tabs", () => {
  const state = load("theme.tsx", { stored: { "fuzer.theme": "invalid" } });
  assert.equal(state.document.documentElement.dataset.theme, "light");
  state.scheme.matches = true;
  state.scheme.dispatchEvent(new Event("change"));
  assert.equal(state.document.documentElement.dataset.theme, "dark");
  state.storageEvent("fuzer.theme", "light");
  assert.equal(state.document.documentElement.dataset.theme, "light");
});

test("theme changes still work when browser storage is blocked", () => {
  const state = load("theme.tsx", { blocked: true });
  state.exports.setTheme("dark");
  assert.equal(state.document.documentElement.dataset.theme, "dark");
});

test("identity is shared immediately, persisted and synced across tabs", () => {
  const state = load("identityStore.ts", { stored: { "fuzer.reviewer": "ana" } });
  assert.equal(state.exports.loadPerson(), "ana");
  state.exports.usePerson()[1]("ben");
  assert.equal(state.exports.usePerson()[0], "ben");
  assert.equal(state.stored["fuzer.reviewer"], "ben");
  state.storageEvent("fuzer.reviewer", "chris");
  assert.equal(state.exports.loadPerson(), "chris");
});

test("identity still updates in memory when browser storage is blocked", () => {
  const state = load("identityStore.ts", { blocked: true });
  state.exports.usePerson()[1]("ana");
  assert.equal(state.exports.loadPerson(), "ana");
});

test("authenticated identity ignores attribution edits and cross-tab localStorage spoofing", () => {
  const state = load("identityStore.ts", { stored: { "fuzer.reviewer": "system" } });
  state.exports.setTrustedPerson("user:trusted-account");
  state.exports.usePerson()[1]("agent:sre");
  state.storageEvent("fuzer.reviewer", "another-user");
  assert.equal(state.exports.loadPerson(), "user:trusted-account");
  assert.equal(state.stored["fuzer.reviewer"], "system", "Microsoft identity is not saved as editable attribution");
});
