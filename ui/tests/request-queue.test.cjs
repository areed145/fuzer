const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

function queue() {
  const exports = {}, timers = new Map();
  let id = 0;
  runInNewContext(ts.transpileModule(readFileSync(resolve(__dirname, "../src/requestQueue.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText, { exports, setTimeout: (fn) => { timers.set(++id, fn); return id; },
    clearTimeout: (key) => timers.delete(key) });
  return { schedule: exports.scheduleRequest, timers };
}
const turn = () => new Promise((resolve) => setImmediate(resolve));

test("request queue bounds reads, coalesces identical in-flight reads and never replays actions", async () => {
  const { schedule } = queue();
  const releases = [];
  let active = 0, peak = 0, calls = 0;
  const run = () => new Promise((resolve) => {
    calls++; active++; peak = Math.max(peak, active);
    releases.push(() => { active--; resolve(calls); });
  });
  const read = schedule(run, "session:/stats");
  assert.equal(schedule(run, "session:/stats"), read);
  const otherSession = schedule(run, "other:/stats");
  const action = schedule(run);
  await turn();
  assert.equal(calls, 2);
  releases.shift()(); await read; await turn();
  assert.equal(calls, 3);
  releases.shift()(); releases.shift()();
  await Promise.all([otherSession, action]);
  assert.equal(peak, 2);
  let failures = 0;
  await assert.rejects(schedule(async () => { failures++; throw new Error("429 busy"); }), /429 busy/);
  assert.equal(failures, 1, "Failed operations must never retry automatically");
  assert.equal(await schedule(async () => "recovered"), "recovered");
});

test("request queue rejects overflow and expired waiting explicitly without executing them", async () => {
  const { schedule, timers } = queue();
  const releases = [];
  const hold = () => new Promise((resolve) => releases.push(resolve));
  const running = [schedule(hold), schedule(hold)];
  await turn();
  let executed = 0;
  const pending = Array.from({ length: 32 }, () => schedule(async () => { executed++; }));
  const expired = pending.map((promise) => assert.rejects(promise, /Timed out waiting/));
  await assert.rejects(schedule(async () => { executed++; }), /Too many pending requests/);
  for (const timeout of [...timers.values()]) timeout();
  await Promise.all(expired);
  releases.forEach((release) => release());
  await Promise.all(running);
  assert.equal(executed, 0);
  assert.equal(await schedule(async () => "available"), "available");
});
