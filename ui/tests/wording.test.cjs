// The browser half of the wording layer (#1072): what people read carries no machine text.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

const exportsObject = {};
const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src/wording.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
runInNewContext(code, { exports: exportsObject });
const { KIND_LABELS, acronyms, alarm, detectorName, kindWord, person, plural, queue, say, setPeopleNames, unit } = exportsObject;

const MACHINE = /\b(?:alarm|iow|role):[a-z_]|\bdegC\b|\bSm3\b|\b[a-z]+_[a-z_]+ queue\b/;

test("record text reads in people's words, and words that only contain a unit stay", () => {
  for (const [raw, want] of [
    ["OK_J08_03.WHP alarm:lo: 8 Sm3 oil at risk", "OK_J08_03.WHP low alarm: 8 Sm³ oil at risk"],
    ["with the ops_lead queue, sent to role:data_steward", "with the ops lead queue, sent to data steward"],
    ["48.06 degC, 3,548 Am3/h, 12 kSm3/d, 4.2 um", "48.06 °C, 3,548 Am³/h, 12 kSm³/d, 4.2 µm"],
    ["summer um and a forum", "summer um and a forum"],
  ]) {
    assert.equal(say(raw), want);
    assert.equal(say(want), want);
    assert.doesNotMatch(want, MACHINE);
  }
});

test("alarm kinds, units, queues, plurals, kinds and acronyms", () => {
  assert.equal(alarm("alarm:hihi"), "High-high alarm");
  assert.equal(alarm("iow:lo"), "Low IOW limit");
  assert.deepEqual([unit("degC"), unit("Sm3/d"), unit("um"), unit("barg"), unit(null)], ["°C", "Sm³/d", "µm", "barg", ""]);
  assert.equal(queue("role:data_steward"), "data steward queue");
  assert.equal(queue("ops_lead"), "ops lead queue");
  assert.deepEqual([plural(1, "event"), plural(3, "event"), plural(1, "place")], ["1 event", "3 events", "1 place"]);
  assert.equal(kindWord("tag", { template: "Compressor" }), "Compressor");
  assert.equal(kindWord("work_order", {}), "Work order");  // before the ontology loads: the kind's own name
  KIND_LABELS.timeseries = "Historian point";  // then the ontology's label, the one place a kind is named
  assert.equal(kindWord("timeseries"), "Historian point");
  assert.equal(kindWord("document", { doc_type: "oem_manual" }), "OEM manual");
  assert.equal(kindWord("document", {}), "Document");
  assert.equal(acronyms("365-day preventive maintenance, lp production separator"), "365-day preventive maintenance, LP production separator");
  assert.equal(acronyms("st1 test separator, help"), "ST1 test separator, help");
});

test("a signed-in account reads as its name, never its actor id", () => {
  const id = "user:9abd0c5686d748c987c6101bfc432705";
  assert.equal(person(id), "a signed-in user");  // before the names load: no id on screen
  setPeopleNames({ [id]: "Adam Reeder" });
  assert.equal(person(id), "Adam Reeder");
  assert.equal(person("user:demo"), "Demo account");
  assert.equal(person("system"), "Fuzer");
  assert.equal(say(`handed over by ${id}`), "handed over by Adam Reeder");
});

test("#16 what raised an event reads as words, not its code", () => {
  assert.equal(detectorName("alarm_performance"), "Alarm performance");
  assert.equal(detectorName("rotating_screen"), "Rotating screen");
  assert.equal(detectorName("sre"), "SRE");
  assert.equal(detectorName("agent:opportunity_scan"), "Opportunity scan agent");
  assert.equal(detectorName("SAP PM"), "SAP PM");
  // a row's source is a system or the person who raised it, read here, never as an actor (tour D8:
  // "Machine health · rotating_screen")
  assert.equal(detectorName("user:demo"), "Demo account");
  assert.equal(detectorName("sample crew"), "Demo crew");
});
