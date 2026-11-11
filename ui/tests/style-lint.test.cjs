// Keeps the clean style (docs/ui-style.md, "Clean style") from drifting back. Each rule counts a pattern
// across ui/src. A count may go down but never up: when you remove clutter, lower its number in
// style-baseline.json; when a rule fails, follow the hint instead of raising the number.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const src = path.join(__dirname, "..", "src");
const baseline = JSON.parse(fs.readFileSync(path.join(__dirname, "style-baseline.json"), "utf8"));

function files(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? files(full) : /\.tsx$/.test(entry.name) ? [full] : [];
  });
}
const sources = files(src).map((file) => ({ file: path.relative(src, file), text: fs.readFileSync(file, "utf8") }));

const RULES = {
  "help-disclosure": {
    pattern: /<details\b/g,
    hint: "Don't hide guidance behind a caret. Link the docs with PageHeader help=, or show the content. For a long list use a 'Show all (n)' button.",
  },
  "muted-paragraph": {
    pattern: /<p className="muted[^"]*"/g,
    hint: "A grey paragraph is usually an explanation the page doesn't need. Cut it, or move it to the docs and link with PageHeader help=.",
  },
  "long-subtitle": {
    pattern: /subtitle=(?:"[^"]{91,}"|\{"[^"]{91,}"\})/g,
    hint: "A page subtitle is one line of at most 90 characters with no links. Put the rest in the docs.",
  },
  "disclaimer": {
    pattern: /\b(?:not a sign-in|does not (?:mean|grant|change|verify|prove)|not (?:a )?live (?:plant|operating)|not an? (?:snapshot|guarantee)|never (?:generates|calls))\b/gi,
    hint: "State a limit only where the user acts on it (a confirm step, a disabled button, an empty state), not as a standing disclaimer.",
  },
  "literal-color": {
    pattern: /["'`]#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?["'`]/g,
    hint: "Use theme tokens (var(--accent), var(--warn) …) instead of literal colours in TSX.",
  },
  "inline-style": {
    pattern: /style=\{\{/g,
    hint: "Use a class from the UI kit or the page's stylesheet. Inline styles are only for computed sizes and positions.",
  },
  "raw-control": {
    pattern: /<(?:button|table)\b/g,
    hint: "Use Button and Table from ui/src/ui so controls and tables look the same everywhere.",
  },
};

for (const [rule, { pattern, hint }] of Object.entries(RULES)) {
  test(`style: ${rule}`, () => {
    const hits = sources.map(({ file, text }) => ({ file, n: (text.match(pattern) ?? []).length })).filter(({ n }) => n);
    const total = hits.reduce((sum, { n }) => sum + n, 0);
    const allowed = baseline[rule];
    assert.equal(typeof allowed, "number", `style-baseline.json needs a number for "${rule}"`);
    if (total < allowed) console.log(`style: ${rule} is down to ${total} (baseline ${allowed}); lower it in tests/style-baseline.json.`);
    assert.ok(total <= allowed, `${rule}: ${total} found, at most ${allowed} allowed.\n${hint}\n` +
      hits.sort((a, b) => b.n - a.n).map(({ file, n }) => `  ${file}: ${n}`).join("\n"));
  });
}
