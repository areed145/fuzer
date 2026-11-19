// Keeps the clean style (docs/ui-style.md, "Clean style") from drifting back. Each rule counts a pattern
// across ui/src. A count may go down but never up: when you remove clutter, lower its number in
// style-baseline.json; when a rule fails, follow the hint instead of raising the number.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const src = path.join(__dirname, "..", "src");
const baseline = JSON.parse(fs.readFileSync(path.join(__dirname, "style-baseline.json"), "utf8"));

function files(dir, ext) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? files(full, ext) : ext.test(entry.name) ? [full] : [];
  });
}
const read = (ext) => files(src, ext).map((file) => ({ file: path.relative(src, file), text: fs.readFileSync(file, "utf8") }));
const sources = read(/\.tsx$/);
const styles = read(/\.css$/);

const RULES = {
  "help-disclosure": {
    pattern: /<(?:details|summary)\b/g,
    hint: "No carets. Guidance goes in the docs (PageHeader help=); long data goes in Disclosure; everything else is shown.",
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
  "status-pill": {
    css: true,
    pattern: /\.(?:ui-)?badge[^{]*\{[^}]*(?:background:(?!\s*none)[^;]+|border-radius:(?!\s*0)[^;]+)/g,
    hint: "Status is a word in the state's colour (Badge tone). Don't give badges a fill or a pill shape.",
  },
  "summary-marker": {
    css: true,
    pattern: /(?<![-\w.])summary\b[^{]*\{/g,
    hint: "There are no <summary> elements to style. Use Disclosure for long data.",
  },
  "local-number-format": {
    pattern: /\$\$\{|>\$\{[^}]*(?:usd|cost|price|value)|\* 100\)\}?%|const (?:money|usd|dollars) = /g,
    hint: "Format money, percentages, quantities and counts with ui/src/format.ts so one value reads the same on every page.",
  },
  "local-action-state": {
    pattern: /setBusy\(|instanceof Error \? \w+\.message|as Error\)\.message/g,
    hint: "Run a button's async action with useAction (or useReasonedAction) from ui/src/lib.tsx and read failures with errorMessage; don't keep busy and error state by hand.",
  },
  "kit-override": {
    css: true,
    skip: /^styles\/(?:ui-kit|ios|field-mode)\.css$/,
    pattern: /\.ui-[a-z0-9-]+/g,
    hint: "A page stylesheet must not restyle a kit component (.ui-*). Add a variant or prop to the kit in ui/src/ui so every page gets it.",
  },
  "computed-total": {
    pattern: /\.reduce\(/g,
    hint: "A page doesn't add up a list itself. Show a total the API returned, or define the figure once in ui/src/figures.ts.",
  },
  "raw-fetch": {
    pattern: /(?<![\w.])fetch\(/g,
    hint: "Load data through the api facade (ui/src/api) with useAsync, so every page shares one transport, queue and sign-in handling.",
  },
  "raw-control": {
    pattern: /<(?:button|table|textarea|dialog)\b|(?<![\w.])(?:window\.)?(?:confirm|prompt|alert)\(/g,
    hint: "Use Button, Table and Textarea from ui/src/ui, and confirmDialog or promptDialog in place of browser dialogs, so controls look and behave the same everywhere.",
  },
};

for (const [rule, { pattern, hint, css, skip }] of Object.entries(RULES)) {
  test(`style: ${rule}`, () => {
    const hits = (css ? styles : sources).filter(({ file }) => !skip?.test(file.replace(/\\/g, "/"))).map(({ file, text }) => ({ file, n: (text.match(pattern) ?? []).length })).filter(({ n }) => n);
    const total = hits.reduce((sum, { n }) => sum + n, 0);
    const allowed = baseline[rule];
    assert.equal(typeof allowed, "number", `style-baseline.json needs a number for "${rule}"`);
    if (total < allowed) console.log(`style: ${rule} is down to ${total} (baseline ${allowed}); lower it in tests/style-baseline.json.`);
    assert.ok(total <= allowed, `${rule}: ${total} found, at most ${allowed} allowed.\n${hint}\n` +
      hits.sort((a, b) => b.n - a.n).map(({ file, n }) => `  ${file}: ${n}`).join("\n"));
  });
}
