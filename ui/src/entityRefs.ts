// Citations in text a person reads: `OK-J09-04 [fz-well-2526585e8224]` should read as a linked name.
// This rewrites them into tokens Markdown renders as links. It's only about display: the text the
// agents wrote, what's stored and what the evals count all keep their ids.

export interface EntityName { label: string | null; description: string | null }
export type EntityNames = Record<string, EntityName>;

const ID = "fz-[a-z_]+-[0-9a-f]{12}";
const CITATION = new RegExp(`[ \\t]*[\\[(](${ID}(?:\\s*[,;]\\s*${ID})*)[\\])]`, "g");
const OPEN = "", MID = "", CLOSE = "";
export const TOKEN = new RegExp(`^${OPEN}(.*)${MID}(.*)${CLOSE}$`, "s");
export const TOKENS = `${OPEN}[^${CLOSE}]*${CLOSE}`;
export const isId = (text: string) => new RegExp(`^${ID}$`).test(text);

const token = (id: string, text: string) => `${OPEN}${id}${MID}${text}${CLOSE}`;
export const display = (id: string, names?: EntityNames) => names?.[id]?.label || id;

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
