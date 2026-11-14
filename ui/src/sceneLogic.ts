// What the 3D view works out in the browser (P5.16b to d): status as a colour, the path up or down from a picked item,
// and a well moved to another header. Kept free of imports so it is tested on its own (tests/scene-logic.test.cjs).

export interface LinkLike { id: number; src: string; dst: string; rel: string; product?: string | null; path: number[][] }
export interface ItemLike { id: string; kind: string; label: string; x: number; y: number; z: number }
export interface ItemStatus { open_work: number; alarms: number; down: { id: string; start: string | null; reason: string | null } | null }

/** The relations a flow path follows (P5.16c). */
export const PATH_RELS = ["flows_to", "lift_gas_to", "pipes_to"];

/** The worst thing an item has to say: down, then an alarm sounding, then open work. */
export type StatusKind = "down" | "alarm" | "work";
export function worst(s: ItemStatus | undefined): StatusKind | null {
  if (!s) return null;
  return s.down ? "down" : s.alarms ? "alarm" : s.open_work ? "work" : null;
}

/** A badge's text: the open work count, with a mark for an alarm or for down. */
export function badge(s: ItemStatus): string {
  return [s.down ? "Down" : "", s.alarms ? `${s.alarms}!` : "", s.open_work ? String(s.open_work) : ""].filter(Boolean).join(" ");
}

/** Everything reached from `from` going downstream (along each link) or upstream (against it), over `rels`. */
export function trace(links: LinkLike[], from: string, dir: "up" | "down", rels: string[] = PATH_RELS) {
  const next = new Map<string, LinkLike[]>();
  for (const l of links) {
    if (!rels.includes(l.rel)) continue;
    const key = dir === "down" ? l.src : l.dst;
    next.set(key, [...(next.get(key) ?? []), l]);
  }
  const items = new Set([from]), lit = new Set<number>(), stack = [from];
  while (stack.length) {
    for (const l of next.get(stack.pop()!) ?? []) {
      lit.add(l.id);
      const to = dir === "down" ? l.dst : l.src;
      if (!items.has(to)) { items.add(to); stack.push(to); }
    }
  }
  return { items, links: lit };
}

/** The headers and manifolds wells flow to, and where each well goes now: what a reroute can choose between. */
export function headers(items: ItemLike[], links: LinkLike[]) {
  const wells = new Set(items.filter((i) => i.kind === "well").map((i) => i.id));
  const now = new Map<string, LinkLike>();
  const into = new Set<string>();
  for (const l of links) {
    if (l.rel !== "flows_to" || !wells.has(l.src)) continue;
    if (!now.has(l.src)) now.set(l.src, l);
    into.add(l.dst);
  }
  return { now, into: [...into] };
}

/** "move" in the link: a well and the header it is moved to, as well~header. */
export function parseMove(v: string | null): { well: string; to: string } | null {
  const [well, to] = (v ?? "").split("~");
  return well && to ? { well, to } : null;
}

export const MOVED = -1; // the id of the drawn new run

/** A well moved to another header, in the browser only: its old run, the new one (drawn over the deck, not routed),
 * and wells per header before and after. Nothing is written and no hydraulics are claimed. */
export function reroute(items: ItemLike[], links: LinkLike[], move: { well: string; to: string }) {
  const { now, into } = headers(items, links);
  const old = now.get(move.well);
  const well = items.find((i) => i.id === move.well), to = items.find((i) => i.id === move.to);
  if (!old || !well || !to || old.dst === move.to || !into.includes(move.to)) return null;
  const up = Math.max(well.y, to.y) + 6;
  const added: LinkLike = { id: MOVED, src: well.id, dst: to.id, rel: "flows_to", product: old.product ?? null,
    path: [[well.x, well.y + 1, well.z], [well.x, up, well.z], [to.x, up, to.z], [to.x, to.y + 1, to.z]] };
  const count = (m: Map<string, string>) => {
    const n = new Map<string, number>();
    for (const h of m.values()) n.set(h, (n.get(h) ?? 0) + 1);
    return n;
  };
  const before = new Map([...now].map(([w, l]) => [w, l.dst]));
  const after = new Map(before).set(move.well, move.to);
  const b = count(before), a = count(after);
  const changed = [old.dst, move.to].map((h) => ({ header: h, before: b.get(h) ?? 0, after: a.get(h) ?? 0 }));
  return { old, added, changed };
}
