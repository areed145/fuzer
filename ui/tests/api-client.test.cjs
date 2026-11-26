const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve, dirname } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

function client(stored = {}) {
  const localStorage = { getItem: (k) => stored[k] ?? null, setItem: (k, v) => { stored[k] = v; },
    removeItem: (k) => { delete stored[k]; } };
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
      exports, window, Event, localStorage, console, URLSearchParams, setTimeout, clearTimeout,
      fetch: async (url, options) => { calls.push({ url, options }); return typeof reply === "function" ? reply(url) : reply; },
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

test("Requests carry the picked project, and links the browser opens name it in the query", async () => {
  const c = client({ "fuzer.project": "kestrel" });
  await c.api.entity("asset");
  await c.load(resolve(__dirname, "../src/api/work.ts")).workApi.workDefinitionXml(3);
  assert.equal(c.calls[0].options.headers["X-Fuzer-Project"], "kestrel");
  assert.equal(c.calls[1].options.headers["X-Fuzer-Project"], "kestrel");
  const { projectUrl } = c.load(resolve(__dirname, "../src/authState.ts"));
  assert.equal(projectUrl("/documents/a/file"), "/documents/a/file?fuzer_project=kestrel");
  assert.equal(projectUrl("/x?y=1"), "/x?y=1&fuzer_project=kestrel");
  const plain = client();
  await plain.api.entity("asset");
  assert.equal(plain.calls[0].options.headers["X-Fuzer-Project"], undefined);
});

test("A read that meets a deploy's 502 waits for the new server; a write is not retried", async () => {
  const c = client();
  c.load(resolve(__dirname, "../src/api/transport.ts")).RESTART_RETRY_MS.splice(0, Infinity, 0, 0, 0);
  const down = { ok: false, status: 502, json: async () => { throw new Error("an HTML error page"); } };
  const up = { ok: true, status: 200, json: async () => ({ items: [] }) };
  let answers = [down, down, up];
  c.respond(() => answers.shift() ?? down);
  const sent = (url) => c.calls.filter(call => call.url === url).length;
  assert.deepEqual(await c.api.entity("asset"), { items: [] });
  assert.equal(sent("/entities/asset"), 3);
  await assert.rejects(c.api.entity("other"), /restarting after an update/);
  assert.equal(sent("/entities/other"), 4);
  await assert.rejects(c.api.updateAccount("user/a", { enabled: true, owner: false, roles: [] }), /502/);
  assert.equal(sent("/auth/accounts/user%2Fa"), 1);
  // Fuzer's own 503 (JSON with a detail) is the app answering, not the host restarting: shown at once.
  c.respond({ ok: false, status: 503, headers: new Map([["content-type", "application/json"]]),
    json: async () => ({ detail: "Database restarting" }) });
  await assert.rejects(c.api.entity("db"), /Database restarting/);
  assert.equal(sent("/entities/db"), 1);
});

test("A write goes ahead of the reads waiting their turn (P5.28f: Done took over 2.5 s behind My work's reads)", async () => {
  const c = client();
  const held = [];
  c.respond((url) => url.startsWith("/entities/")
    ? new Promise((done) => held.push(() => done({ ok: true, json: async () => ({}) })))
    : { ok: true, json: async () => ({ ok: true }) });
  const reads = Array.from({ length: 7 }, (_, i) => c.api.entity(`asset-${i}`));
  await new Promise((r) => setTimeout(r, 0));
  const write = c.api.comment(3, "riley", "on my way");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(c.calls.length, 4, "four reads hold the browser's places");
  held.shift()();  // one read answers: the write takes its place, before the three reads waiting
  await write;
  assert.equal(c.calls[4].url, "/work/items/3/comment");
  let finished = false;
  void Promise.all(reads).then(() => { finished = true; });
  while (!finished) { held.splice(0).forEach((done) => done()); await new Promise((r) => setTimeout(r, 0)); }
});
