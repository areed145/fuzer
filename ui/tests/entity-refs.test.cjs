const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

const exportsObject = {};
const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src/entityRefs.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
runInNewContext(code, { exports: exportsObject });
const { linkCitations, idsIn, display, commonName, TOKEN } = exportsObject;

const WELL = "fz-well-2526585e8224", TERM = "fz-allocation_node-b708d0477249", DAY = "fz-daily_report-82c067e8e550", SEP = "fz-tag-94741a20e99d";
const names = {
  [WELL]: { label: "OK-J09-04", description: "Gas-lifted oil well" },
  [TERM]: { label: "ESCRAVOS-TERM", description: "Escravos crude terminal" },
  [DAY]: { label: "Daily report 2025-12-30", description: null },
  [SEP]: { label: "10-V-2001", description: "HP Production Separator" },
};
// Tokens as [text](id), to read the results.
const read = (text) => text.replace(/([^]*)([^]*)/g, "[$2]($1)");
const link = (text, known = names) => read(linkCitations(text, known));

test("a name in front of its citation becomes the link and the bracket goes", () => {
  assert.equal(link(`I picked OK-J09-04 [${WELL}], a producer.`), `I picked [OK-J09-04](${WELL}), a producer.`);
  assert.equal(link(`the HP separator 10-V-2001 [${SEP}].`), `the HP separator [10-V-2001](${SEP}).`);
});

test("bold and code around a name stay around or leave cleanly", () => {
  assert.equal(link(`**OK-J09-04** [${WELL}]`), `**[OK-J09-04](${WELL})**`);
  assert.equal(link(`\`10-V-2001\` [${SEP}]`), `[10-V-2001](${SEP})`);
});

test("a name made of several words matches through the description, keeping the author's wording", () => {
  assert.equal(link(`to the **Escravos crude terminal** [${TERM}]`), `to the **[Escravos crude terminal](${TERM})**`);
  assert.equal(link(`the hp production separator [${SEP}]`), `the [hp production separator](${SEP})`);
});

test("the longer of label and description wins when both fit", () => {
  const known = { [WELL]: { label: "well", description: "oil well" } };
  assert.equal(link(`an oil well [${WELL}]`, known), `an [oil well](${WELL})`);
});

test("a label's record suffix doesn't stop the name matching", () => {
  const log = "fz-event-0123456789ab", incident = "fz-incident-0123456789ab";
  const known = { [log]: { label: "OKLOG-2025-12-24-01146#1", description: null }, [incident]: { label: "10-P-1001A@2025-12-24", description: null } };
  assert.equal(link(`log OKLOG-2025-12-24-01146 [${log}]`, known), `log [OKLOG-2025-12-24-01146](${log})`);
  assert.equal(link(`on 10-P-1001A [${incident}] ran on`, known), `on [10-P-1001A](${incident}) ran on`);
});

test("a name must be whole words", () => {
  assert.equal(link(`XOK-J09-04 [${WELL}]`), `XOK-J09-04 ([OK-J09-04](${WELL}))`);
});

test("with no name in front, a citation becomes a short labeled link", () => {
  assert.equal(link(`7,619 Sm3 [${DAY}]`), `7,619 Sm3 ([Daily report 2025-12-30](${DAY}))`);
});

test("several ids in one bracket each keep a link", () => {
  assert.equal(link(`down 46 Sm3 [${DAY}, ${WELL}]`), `down 46 Sm3 ([Daily report 2025-12-30](${DAY}), [OK-J09-04](${WELL}))`);
});

test("a citation of nothing we know stays visible", () => {
  const missing = "fz-tag-000000000000";
  assert.equal(link(`Claimed fact [${missing}]`), `Claimed fact ([${missing}](${missing}))`);
});

test("citations wait for names rather than flashing raw ids", () => {
  assert.equal(linkCitations(`OK-J09-04 [${WELL}], then more.`, undefined), "OK-J09-04, then more.");
});

test("other brackets and text are left alone", () => {
  assert.equal(link("recovered [upset:10] and [see note]"), "recovered [upset:10] and [see note]");
  assert.equal(link("no citations here"), "no citations here");
});

test("every id is found once, and a name falls back to its id", () => {
  assert.deepEqual([...idsIn(`${WELL} [${WELL}, ${DAY}]`)], [WELL, DAY]);
  assert.equal(display(WELL, names), "OK-J09-04");
  assert.equal(display(WELL, {}), WELL);
  assert.equal(display(WELL, undefined), WELL);
  assert.ok(TOKEN.test(linkCitations(`OK-J09-04 [${WELL}]`, names)));
});

test("report citations that are not entities read as words, entity ids stay for linking (#392)", () => {
  const { readableCitations } = exportsObject;
  assert.equal(readableCitations("- On the board: X [opportunity:gas_lift:OK-J07-05→OK-J12-09]"),
    "- On the board: X (work board: gas lift OK-J07-05→OK-J12-09)");
  assert.equal(readableCitations("- Due [maintenance_plan:INSP-30000038]"), "- Due (maintenance plan: INSP-30000038)");
  assert.equal(readableCitations(`- Upset [upset:12, ${WELL}]`), `- Upset [${WELL}] (upset 12)`);
  assert.equal(readableCitations(`- Oil [${DAY}]`), `- Oil [${DAY}]`);
  assert.equal(readableCitations("[[figure:f_0a1b]] and [not a cite]"), "[[figure:f_0a1b]] and [not a cite]");
});

test("a common name is the description, without a source code in front, unless it only repeats the tag", () => {
  assert.equal(commonName("10-E-3001", "Crude Cooler"), "Crude Cooler");
  assert.equal(commonName("OKAN-10", "OKA_PP Okan Production Platform"), "Okan Production Platform");
  assert.equal(commonName("OKAN-61", "OKJ-01 Okan Jacket 01"), "Okan Jacket 01");
  assert.equal(commonName("OKJ03-GLM", "OKJ-03 Gas Lift Manifold"), "OKJ-03 Gas Lift Manifold");
  assert.equal(commonName("OK-J01-05", "Well OK-J01-05 (GL oil producer)"), null);
  assert.equal(commonName("X", null), null);
  assert.equal(commonName("X", "a".repeat(60)), null);
});
