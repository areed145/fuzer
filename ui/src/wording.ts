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

const UNIT = /(?<![\w])(?:k|M)?(?:Sm3|Am3|m3)(?!\w)|(?<![\w])degC(?!\w)|(?<=\d ?)um(?!\w)|^um$/g;

/** Unit spellings in running text as their symbols: degC is °C, Sm3 Sm³, um µm (only after a number). */
export function units(text: string): string {
  return text.replace(UNIT, (s) => (s === "um" ? "µm" : s === "degC" ? "°C" : s.replace("3", "³")));
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

/** A state or label as a sentence starts: "open" reads "Open". */
export const cap = (s: string | null | undefined): string => (s ? s[0].toUpperCase() + s.slice(1) : "");

/** Who did something, as a name: the demo sign-in reads "Demo account", the system "Fuzer". */
export function person(who: string | null | undefined): string {
  if (!who) return "";
  if (who === "system") return "Fuzer";
  return who.replace(DEMO_ACTOR, "Demo account");
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
    .replace(DEMO_ACTOR, "Demo account"));
}

const DOC_TYPES: Record<string, string> = {
  pid: "P&ID drawing", p_id: "P&ID drawing", pfd: "Process flow diagram", oem_manual: "OEM manual", datasheet: "Datasheet",
  procedure: "Procedure", troubleshooting_guide: "Troubleshooting guide", drawing: "Drawing",
};
const KIND_WORDS: Record<string, string> = {
  tag: "Asset", equipment: "Equipment", equipment_model: "Equipment model", equipment_class: "Equipment class",
  timeseries: "Historian point", document: "Document", notification: "SAP notification", work_order: "Work order",
  event: "Log entry", downtime: "Downtime", incident: "Incident", well: "Well", facility: "Facility", area: "Area",
  permit: "Permit to work", isolation: "Isolation",
};

/** What a record is, in the words of someone who works the plant (#1072): a compressor's page says "Compressor"
 * (its template), not "tag"; a drawing says "P&ID drawing", not "document". Unknown kinds read as the kind, spaced. */
export function kindWord(kind: string, fields?: { template?: unknown; doc_type?: unknown; format?: unknown }): string {
  const template = typeof fields?.template === "string" ? fields.template.trim() : "";
  if (kind === "tag" && template) return template;
  const doc = typeof fields?.doc_type === "string" ? fields.doc_type.toLowerCase() : "";
  if (kind === "document" && doc) return DOC_TYPES[doc] ?? doc.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  return KIND_WORDS[kind] ?? kind.replace(/_/g, " ");
}

/** Kinds people operate and ask about as assets; any other record is asked about as itself. */
export const ASSET_KINDS = ["tag", "equipment", "well", "facility", "area"];

/** Plant abbreviations a record lower-cased, as written on the plant (#1072): "lp production separator" reads
 * "LP production separator", "st1 test separator" "ST1 test separator". */
export const acronyms = (text: string): string =>
  text.replace(/\b(lp|hp|mp|ip|esd|psv|st\d|gt\d?)\b/g, (w) => w.toUpperCase());

/** The app's one date format (#1131): "Nov 14, 2:11 PM", with the year only when it is not this year
 * ("Dec 30, 2025, 9:20 PM"). `utc` reads the clock in UTC for values recorded that way. */
export function stamp(at: string | number | Date, utc = false): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return String(at);
  const zone = utc ? { timeZone: "UTC" } : {};
  const year = utc ? d.getUTCFullYear() : d.getFullYear();
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    ...(year === new Date().getFullYear() ? {} : { year: "numeric" }), ...zone });
}

/** The same format for a day alone: "Nov 14", or "Dec 30, 2025" in another year. */
export function dayStamp(at: string | number | Date, utc = false): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return String(at);
  const year = utc ? d.getUTCFullYear() : d.getFullYear();
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric",
    ...(year === new Date().getFullYear() ? {} : { year: "numeric" }), ...(utc ? { timeZone: "UTC" } : {}) });
}
