// The approved stack (stack.json at the repository root, docs/stack.md). A dependency in ui/package.json that the
// approved list doesn't name fails here, and so does a listed one that nothing uses. Don't add the name to
// stack.json to get past this: a new dependency needs Adam's go-ahead first (AGENTS.md, "Stack and shared modules").
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const json = (...parts) => JSON.parse(fs.readFileSync(path.join(__dirname, "..", ...parts), "utf8"));
const approved = json("..", "stack.json").ui;
const manifest = json("package.json");

for (const group of ["dependencies", "devDependencies"]) {
  test(`stack: ui ${group} are the approved ones`, () => {
    const used = Object.keys(manifest[group] ?? {}).sort();
    const listed = Object.keys(approved[group]).sort();
    const unapproved = used.filter((name) => !listed.includes(name));
    const unused = listed.filter((name) => !used.includes(name));
    assert.deepEqual(unapproved, [], `Not in the approved stack: ${unapproved.join(", ")}.\n` +
      "Use what the stack already has, or ask Adam before adding a dependency; once agreed, add it to stack.json with what it is for.");
    assert.deepEqual(unused, [], `stack.json lists ${unused.join(", ")}, which ui/package.json no longer uses. Remove the entry.`);
  });
}

test("stack: every approved UI dependency says what it is for", () => {
  for (const group of ["dependencies", "devDependencies"]) for (const [name, purpose] of Object.entries(approved[group])) {
    assert.ok(typeof purpose === "string" && purpose.length > 3, `${name} needs a purpose in stack.json`);
  }
});
