const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve, dirname } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

function client() {
  const cache = new Map(), calls = [], window = new EventTarget();
  let reply = { ok: true, json: async () => ({ ok: true }), text: async () => "<bpmn />" };
  const load = (path) => {
    if (cache.has(path)) return cache.get(path);
    const exports = {};
    cache.set(path, exports);
    const code = ts.transpileModule(readFileSync(path, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS },
    }).outputText;
    runInNewContext(code, {
      exports, window, Event, URLSearchParams, setTimeout, clearTimeout,
      fetch: async (url, options) => { calls.push({ url, options }); return reply; },
      require: (name) => load(resolve(dirname(path), `${name}.ts`)),
    });
    return exports;
  };
  load(resolve(__dirname, "../src/authState.ts")).setCsrf("test-csrf");
  return { load, calls, window, respond: (value) => { reply = value; },
    api: load(resolve(__dirname, "../src/api.ts")).api };
}

test("API facade preserves URLs, bodies, CSRF, read coalescing and unreplayed paid calls", async () => {
  const c = client();
  const a = c.api.entity("asset/with spaces", "2024-03-01");
  await Promise.all([a, c.api.entity("asset/with spaces", "2024-03-01")]);
  assert.equal(c.calls.length, 1);
  assert.equal(c.calls[0].url, "/entities/asset%2Fwith%20spaces?as_of=2024-03-01");
  assert.equal(c.calls[0].options.headers["X-Fuzer-CSRF"], "test-csrf");
  await c.api.updateAccount("user/a", { enabled: true, owner: false, roles: ["reader"] });
  assert.equal(c.calls[1].url, "/auth/accounts/user%2Fa");
  assert.equal(c.calls[1].options.method, "PUT");
  assert.deepEqual(JSON.parse(c.calls[1].options.body), { enabled: true, owner: false, roles: ["reader"] });
  await Promise.all([c.api.tablePreview(7, "a/b", { claude: true }), c.api.tablePreview(7, "a/b", { claude: true })]);
  assert.equal(c.calls.filter(call => call.url === "/sources/connections/7/tables/a%2Fb?claude=true").length, 2);
  let unauthorized = 0;
  c.window.addEventListener("fuzer:unauthorized", () => unauthorized++);
  c.respond({ ok: false, status: 401, json: async () => ({ detail: "Session ended" }) });
  await assert.rejects(c.api.signOut(), /Session ended/);
  assert.equal(unauthorized, 1);
  assert.equal(c.calls.filter(call => call.url === "/auth/logout").length, 1);
});

test("Domain API exports share the facade's in-flight reads and transport", async () => {
  const c = client();
  const { exploreApi } = c.load(resolve(__dirname, "../src/api/explore.ts"));
  const { workApi } = c.load(resolve(__dirname, "../src/api/work.ts"));
  await Promise.all([exploreApi.entity("asset"), c.api.entity("asset")]);
  await workApi.workDefinitionXml(3);
  assert.equal(c.calls.filter(call => call.url === "/entities/asset").length, 1);
  assert.equal(c.calls.find(call => call.url === "/work/definitions/3/bpmn").options, undefined);
});
