// Citations in text a person reads: `OK-J09-04 [fz-well-2526585e8224]` should read as a linked name.
// This rewrites them into tokens Markdown renders as links. It's only about display: the text the
// agents wrote, what's stored and what the evals count all keep their ids.

/** `at`: for equipment (labelled by serial number), the tag it is installed at, which is what people know it by. */
export interface EntityName { label: string | null; description: string | null; at?: string }
export type EntityNames = Record<string, EntityName>;

const ID = "fz-[a-z_]+-[0-9a-f]{12}";
const CITATION = new RegExp(`[ \\t]*[\\[(](${ID}(?:\\s*[,;]\\s*${ID})*)[\\])]`, "g");
const OPEN = "", MID = "", CLOSE = "";
export const TOKEN = new RegExp(`^${OPEN}(.*)${MID}(.*)${CLOSE}$`, "s");
export const TOKENS = `${OPEN}[^${CLOSE}]*${CLOSE}`;
export const isId = (text: string) => new RegExp(`^${ID}$`).test(text);

const token = (id: string, text: string) => `${OPEN}${id}${MID}${text}${CLOSE}`;
/** What to call an entity: its label, except equipment (labelled by serial number), which reads as the tag it is
 * installed at and its description ("20-K-2100B 1st Stage Compressor"), as people in the field know it (#495). */
export const display = (id: string, names?: EntityNames) => {
  const n = names?.[id];
  return n?.at ? [n.at, n.description].filter(Boolean).join(" ") : n?.label || id;
};

/** What people call a thing, when its description says more than its tag: "Crude Cooler" for 10-E-3001, "Okan
 * Production Platform" for OKAN-10 (described "OKA_PP Okan Production Platform"). A leading source code is dropped
 * when it has an underscore or the rest still says which one it is ("OKJ-01 Okan Jacket 01" reads "Okan Jacket 01",
 * "OKJ-03 Gas Lift Manifold" stays whole). Null when the description only repeats the tag ("Well OK-J01-05 (GL oil
 * producer)"), is missing, or is too long to sit on a drawing. */
export function commonName(label: string | null | undefined, description: string | null | undefined): string | null {
  let d = (description ?? "").trim().replace(/\s+/g, " ");
  if (!d || (label && d.toLowerCase().includes(label.toLowerCase()))) return null;
  const code = /^([A-Z0-9][A-Z0-9_\-./]*[A-Z0-9])\s+(.+)$/.exec(d);
  if (code && /[\d_]/.test(code[1]) && (code[1].includes("_") || /\d/.test(code[2]))) d = code[2];
  return d.length > 48 ? null : d;
}

/** A node's name on a graph: its tag, with what people call it on the line below when that says more. */
export const nodeLabel = (label: string, description?: string | null) => {
  const name = commonName(label, description);
  return name ? `${label}\n${name}` : label;
};

export function idsIn(text: string): string[] {
  return [...new Set(text.match(new RegExp(ID, "g")) ?? [])];
}

/** Link the name already in front of a citation. Bold and code around it stay out of the match. */
function nameBefore(before: string, id: string, names: EntityNames): string | null {
  const wrapper = before.endsWith("**") ? "**" : before.endsWith("`") ? "`" : "";
  const head = wrapper ? before.slice(0, -wrapper.length) : before;
  const label = names[id]?.label;
  // A record's label can extend the name people use: OKLOG-2025-12-24-01146#1, 10-P-1001A@2025-12-24.
  const candidates = [names[id]?.description, label, label?.split(/[#@]/)[0]].filter((n): n is string => !!n)
    .sort((a, b) => b.length - a.length);
  for (const name of candidates) {
    if (!head.toLowerCase().endsWith(name.toLowerCase())) continue;
    const start = head.length - name.length;
    if (start > 0 && /[\w-]/.test(head[start - 1])) continue;  // the end of a longer word
    const linked = token(id, head.slice(start));
    if (wrapper === "`") return head[start - 1] === "`" ? head.slice(0, start - 1) + linked : before;
    return head.slice(0, start) + linked + wrapper;
  }
  return null;
}

/** `names` is undefined while they load: citations wait, so raw ids never flash on the page. */
export function linkCitations(text: string, names: EntityNames | undefined): string {
  let out = "", last = 0;
  for (const match of text.matchAll(CITATION)) {
    out += text.slice(last, match.index);
    last = match.index + match[0].length;
    if (!names) continue;
    const ids = match[1].split(/\s*[,;]\s*/);
    const named = ids.length === 1 ? nameBefore(out, ids[0], names) : null;
    out = named ?? `${out} (${ids.map((id) => token(id, display(id, names))).join(", ")})`;
  }
  return out + text.slice(last);
}

// Daily report lines also cite things that are not entities: an upset, a work board card, a maintenance plan
// (#392). Their raw keys read as machine text, so they become words; entity ids stay for linkCitations.
const CITE_KINDS: Record<string, string> = {
  upset: "upset", opportunity: "work board", maintenance_plan: "maintenance plan", work_item: "case",
};
const KEY = /^[a-z_]+:\S/;

export function readableCitations(text: string): string {
  return text.replace(/(?<!\[)\[([^[\]]+)\](?!\])/g, (whole, inner: string) => {
    const parts = inner.split(/\s*,\s*/).filter(Boolean);
    const ids = parts.filter(isId), keys = parts.filter((p) => !isId(p));
    if (!keys.length || !keys.every((k) => KEY.test(k))) return whole;
    const words = keys.map((k) => {
      const [kind, ...rest] = k.split(":");
      const name = rest.join(" ").replace(/_/g, " ");
      return `${CITE_KINDS[kind] ?? kind.replace(/_/g, " ")}${/^\d+$/.test(name) ? " " : ": "}${name}`;
    });
    return `${ids.length ? ` [${ids.join(", ")}]` : ""} (${words.join("; ")})`.replace(/^ /, "");
  });
}
