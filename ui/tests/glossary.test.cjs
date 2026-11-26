// The glossary is binding (docs/glossary.md): what people read uses its words. Two rules are checked here.
//  1. An entity kind is labelled in one place, the ontology (src/fuzer/core/ontology.yaml), read through kindWord.
//  2. Screen text calls a thing in the register an entity, never an "item", and a piece of work a case, never a
//     "work item". Other words the glossary retires go in RETIRED as they are swept.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, readdirSync, statSync } = require("node:fs");
const { join, relative, resolve } = require("node:path");

const SRC = resolve(__dirname, "../src");
const files = (dir) => readdirSync(dir).flatMap((name) => {
  const path = join(dir, name);
  return statSync(path).isDirectory() ? files(path) : /\.tsx?$/.test(name) ? [path] : [];
});
/** Comments are for code readers, not screen text. */
const uncommented = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
const sources = files(SRC).map((path) => ({ path: relative(SRC, path), text: uncommented(readFileSync(path, "utf8")) }));

/** The ontology's kind names: the `kinds:` block's two-space keys. */
function kinds() {
  const yaml = readFileSync(resolve(__dirname, "../../src/fuzer/core/ontology.yaml"), "utf8");
  const block = yaml.slice(yaml.indexOf("\nkinds:"), yaml.indexOf("\nrelations:"));
  return [...block.matchAll(/^ {2}([a-z_]+):\s*$/gm)].map((m) => m[1]);
}

/** Files whose keys share a kind's name but name something else: the trend's lanes. */
const LANES = ["trends/figure.ts"];

test("an entity kind is labelled only by the ontology", () => {
  const names = kinds();
  assert.ok(names.includes("work_order") && names.includes("timeseries"), "the ontology's kinds are read");
  const label = new RegExp(`(?:^|[{,\\s])(${names.join("|")})\\s*:\\s*"[A-Z][^"]*"`, "gm");
  const found = sources.filter(({ path }) => !LANES.includes(path)).flatMap(({ path, text }) => [...text.matchAll(label)].map((m) => `${path}: ${m[0].trim()}`));
  assert.deepEqual(found, [], "label a kind in ontology.yaml and read it with kindWord, not in a map of its own");
});

/** Prose people read: JSX text, and quoted strings with a space in them (labels, titles, messages). */
function prose(text) {
  const jsx = [...text.matchAll(/>([^<>{}]*[A-Za-z][^<>{}]*)</g)].map((m) => m[1]);
  const quoted = [...text.matchAll(/"([^"\n]*\s[^"\n]*)"|`([^`\n]*\s[^`\n]*)`/g)].map((m) => m[1] ?? m[2]);
  return [...jsx, ...quoted];
}

/** Retired words, with the glossary word that replaces each. */
const RETIRED = [
  [/\bwork items?\b/i, "case"],
  [/\b(?:an?|the|this|that|same|another|picked|new|two|other)\s+items?\b/i, "entity"],
  [/\bitems\s+(?:with|in|to)\b/i, "entities"],
  [/\bother fields\b/i, "other sites"],
];

test("screen text uses the glossary's words: entity, not item; case, not work item", () => {
  const found = [];
  for (const { path, text } of sources) {
    if (path.startsWith("api/")) continue;  // types and doc comments for the server's own field names
    for (const line of prose(text)) {
      for (const [word, instead] of RETIRED) {
        const hit = line.match(word);
        if (hit) found.push(`${path}: "${hit[0]}" (say ${instead}) in "${line.trim().slice(0, 80)}"`);
      }
    }
  }
  assert.deepEqual(found, []);
});
