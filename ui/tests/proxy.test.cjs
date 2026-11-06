const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");

test("the development proxy covers every API resource used by the UI", () => {
  const api = readFileSync(resolve(__dirname, "../src/api.ts"), "utf8");
  const config = readFileSync(resolve(__dirname, "../vite.config.ts"), "utf8");
  const paths = new Set(Array.from(config.matchAll(/"\/([\w-]+)"/g), (match) => match[1]));
  for (const match of api.matchAll(/["`]\/([\w-]+)/g)) {
    assert.ok(paths.has(match[1]), `Missing proxy for /${match[1]}`);
  }
});
