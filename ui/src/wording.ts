/** Words for people (#1072): the browser half of the display layer in `src/fuzer/core/wording.py`. Alarm kinds,
 * unit symbols, queue names and plurals read the same on every page, whatever spelling the record carries. */

const LIMIT: Record<string, string> = { hihi: "high-high", hi: "high", lo: "low", lolo: "low-low" };
const BASIS: Record<string, string> = { alarm: "alarm", iow: "IOW limit", learned: "learned normal range" };
const KEYS: Record<string, string> = {
  stopped: "Stopped", step: "Step change", drift_up: "Drifting up", spe: "Multivariate anomaly",
  dp_rising: "Pressure drop rising",
};

/** A detection key as words: alarm:lo is "Low alarm", iow:hi "High IOW limit". */
export function alarm(key: string | null | undefined): string {
  if (!key) return "Process upset";
  if (KEYS[key]) return KEYS[key];
  const [head, tail = ""] = key.split(":", 2);
  if (BASIS[head] && LIMIT[tail]) {
    if (head === "learned") return `${tail.startsWith("hi") ? "Above" : "Below"} learned normal range`;
    const words = `${LIMIT[tail]} ${BASIS[head]}`;
    return words[0].toUpperCase() + words.slice(1);
  }
  if (head === "instrument") return `Instrument fault (${tail.replace(/_/g, " ")})`;
  const words = key.replace(/[:_]/g, " ");
  return words[0].toUpperCase() + words.slice(1);
}

const lowerFirst = (s: string) => (s.length > 1 && s[1] === s[1].toUpperCase() && /[A-Z]/.test(s[1]) ? s : s[0].toLowerCase() + s.slice(1));

const UNIT = /(?<![\w])(?:k|M)?(?:Sm3|Am3|m3)(?!\w)|(?<![\w])deg[CF](?!\w)|(?<=\d ?)um(?!\w)|^um$/g;

/** Unit spellings in running text as their symbols: degF is °F, degC is °C, Sm3 Sm³, um µm (only after a number). */
export function units(text: string): string {
  return text.replace(UNIT, (s) => (s === "um" ? "µm" : s === "degC" ? "°C" : s === "degF" ? "°F" : s.replace("3", "³")));
}

/** A unit as printed. */
export const unit = (u: string | null | undefined): string => (u ? units(u) : "");

/** A routing role as its queue: "role:data_steward" and "ops_lead" read "data steward queue", "ops lead queue". */
export function queue(role: string | null | undefined): string {
  const name = String(role ?? "").replace(/^role:/, "").replace(/_/g, " ").trim();
  return name ? `${name} queue` : "no queue";
}

/** "1 detection", "7 detections". */
export function plural(n: number, word: string, many?: string): string {
  return `${n.toLocaleString()} ${n === 1 ? word : many ?? `${word}s`}`;
}

const KEY = /(?<![\w:/])((?:alarm|iow|learned|instrument):[a-z_]+)(?!\w)/g;
const ROLE = /\brole:([a-z_]+)( queue)?/g;
const SNAKE_QUEUE = /\b([a-z]+(?:_[a-z]+)+) queue\b/g;
// a source system named in a sentence by its key ("sap has it under"), never inside an id or a path
const SYSTEM = /(?<![\w:/.\-])(sap|jde|sre|dcs)(?![\w:/.\-])/g;
// the shared demo sign-in, as the record keeps it ("user:demo"); people see the account's name
const DEMO_ACTOR = /\buser:demo\b/g;
// a signed-in account as the record keeps it ("user:9abd…"); people see the account's display name
const ACCOUNT_ACTOR = /\buser:[0-9a-f]{32}\b/g;
const NAMES_KEY = "fuzer.people-names";
// the last names this browser saw, so a page opens with names rather than a placeholder while the list reloads
let accountNames: Record<string, string> = (() => {
  try { return JSON.parse(globalThis.localStorage?.getItem(NAMES_KEY) ?? "{}") ?? {}; } catch { return {}; }
})();

/** The accounts' display names by actor (GET /work/names), loaded once by the app shell. */
export function setPeopleNames(names: Record<string, string>) {
  if (!names || Array.isArray(names) || names === accountNames) return;
  accountNames = names;
  try { globalThis.localStorage?.setItem(NAMES_KEY, JSON.stringify(names)); } catch { /* names last for this visit */ }
}
const accountName = (actor: string) => accountNames[actor] ?? "a signed-in user";

/** A state or label as a sentence starts: "open" reads "Open". */
export const cap = (s: string | null | undefined): string => (s ? s[0].toUpperCase() + s.slice(1) : "");

/** Who did something, as a name: the demo sign-in reads "Demo account", the system "Fuzer". */
export function person(who: string | null | undefined): string {
  if (!who) return "";
  if (who === "system") return "Fuzer";
  return who.replace(DEMO_ACTOR, "Demo account").replace(ACCOUNT_ACTOR, accountName);
}

/** Text a record carries, in people's words: alarm keys, queue roles and unit spellings. Text with none comes back
 * unchanged. */
export function say(text: string): string;
export function say(text: string | null | undefined): string | null | undefined;
export function say(text: string | null | undefined) {
  if (!text) return text;
  return units(text
    .replace(KEY, (_, k: string) => lowerFirst(alarm(k)))
    .replace(ROLE, (_, r: string, q?: string) => (q ? queue(r) : queue(r).replace(/ queue$/, "")))
    .replace(SNAKE_QUEUE, (_, r: string) => queue(r))
    .replace(SYSTEM, (k: string) => k.toUpperCase())
    .replace(DEMO_ACTOR, "Demo account")
    .replace(ACCOUNT_ACTOR, accountName));
}

const DOC_TYPES: Record<string, string> = {
  pid: "P&ID drawing", p_id: "P&ID drawing", pfd: "Process flow diagram", oem_manual: "OEM manual", datasheet: "Datasheet",
  procedure: "Procedure", troubleshooting_guide: "Troubleshooting guide", drawing: "Drawing",
};

/** Each kind's name as people read it, filled from the declared ontology (applyOntology in kinds.ts): the one place a
 * kind is labelled. */
export const KIND_LABELS: Record<string, string> = {};

/** What an entity is, in the words of someone who works the plant (#1072): a compressor's page says "Compressor"
 * (its template), not "Tag"; a drawing says "P&ID drawing", not "Document". Other kinds read as the ontology labels them. */
export function kindWord(kind: string, fields?: { template?: unknown; doc_type?: unknown; format?: unknown }): string {
  const template = typeof fields?.template === "string" ? fields.template.trim() : "";
  if (kind === "tag" && template) return template;
  const doc = typeof fields?.doc_type === "string" ? fields.doc_type.toLowerCase() : "";
  if (kind === "document" && doc) return DOC_TYPES[doc] ?? doc.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  // the ontology is the one place a kind is labelled; before it loads, the kind's own name
  const label = KIND_LABELS[kind];
  return label ?? kind.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/** Kinds people operate and ask about as assets; any other record is asked about as itself. */
export const ASSET_KINDS = ["tag", "equipment", "well", "facility", "area"];

/** Plant abbreviations a record lower-cased, as written on the plant (#1072): "lp production separator" reads
 * "LP production separator", "st1 test separator" "ST1 test separator". */
export const acronyms = (text: string): string =>
  text.replace(/\b(lp|hp|mp|ip|esd|psv|st\d|gt\d?)\b/g, (w) => w.toUpperCase());

