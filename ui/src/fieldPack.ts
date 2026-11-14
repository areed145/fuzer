/** The day's work kept on the phone (P5.17c, #1092). The field pack is the set of records the operator's jobs point
 * at: each job's assets, and their documents and drawings. Any read the app makes that names one of them (an asset's
 * page and its panels, a drawing, a document's file) is kept when it is answered, and served from the phone, marked
 * with when it was kept, when the server can't be reached. Nothing else is kept, mutations and paid reads never are,
 * and the service worker still keeps only the app itself.
 *
 * What is kept stays under a budget: past it, the oldest kept answers go first; the job lists are not counted and
 * never evicted here. No React here: the transport imports this. */
import { getProject } from "./authState";
import * as store from "./fieldStore";

/** How much kept answers may take on the phone. */
export const BUDGET_BYTES = 150 * 1024 * 1024;

export interface KeptAnswer<T = unknown> { data: T; kept_at: string; bytes: number; file?: boolean }

const prefix = () => `ans:${store.getScope()}:${getProject()}:`;
const packKey = () => `pack:${store.getScope()}:${getProject()}`;

let ids = new Set<string>();
let loaded: Promise<void> | null = null;
/** Read the pack's ids for the signed-in account and project. Called again when the account changes. */
export function loadPack(): Promise<void> {
  loaded = store.get<{ ids: string[] }>("kept", packKey()).then((p) => { ids = new Set(p?.ids ?? []); });
  return loaded;
}

/** The records the field pack covers, replacing the last set. */
export async function setPack(next: Iterable<string>): Promise<void> {
  ids = new Set([...next].filter(Boolean));
  await store.put("kept", { ids: [...ids], built_at: new Date().toISOString() }, packKey());
}
export const packIds = () => ids;

/** Reads kept whoever they name: this shift's rounds and their points (P5.17d), and a task step and its case once
 * opened (P5.17e), so a close-out form can be filled in without signal. */
const ALWAYS = /^\/(rounds(\/\d+)?|work\/tasks\/\d+|work\/items\/\d+)$/;

/** Whether a URL names a record in the pack (as a path segment or a query value), or is one of the reads always kept. */
export function inPack(url: string, pack: Set<string> = ids): boolean {
  const [path, query = ""] = url.replace(/^https?:\/\/[^/]+/, "").split("?", 2);
  if (ALWAYS.test(path)) return true;
  if (!pack.size) return false;
  const u = { searchParams: new URLSearchParams(query) };
  const parts = path.split("/").map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  return parts.some((p) => pack.has(p)) || [...u.searchParams.values()].some((v) => pack.has(v));
}

/** When a value the transport returned was read from the phone, not the server: its kept time. */
const keptTimes = new WeakMap<object, string>();
export const keptAt = (value: unknown): string | null =>
  value !== null && typeof value === "object" ? keptTimes.get(value) ?? null : null;

const size = (data: unknown) => (data instanceof ArrayBuffer ? data.byteLength
  : (data as { bytes?: ArrayBuffer })?.bytes instanceof ArrayBuffer ? (data as { bytes: ArrayBuffer }).bytes.byteLength
  : JSON.stringify(data)?.length ?? 0);

/** Keep an answer the server gave for `url`, if the URL names a record in the pack. */
export async function keepAnswer(url: string, data: unknown, file = false): Promise<void> {
  await loaded;
  if (!inPack(url)) return;
  if (file && !(await store.durable())) return;  // files only where IndexedDB holds them
  const kept: KeptAnswer = { data, kept_at: new Date().toISOString(), bytes: size(data), file };
  if (await store.put("kept", kept, prefix() + url)) scheduleEvict();
}

/** The answer kept for `url`, marked with when it was kept, or undefined. */
export async function keptAnswer<T>(url: string): Promise<T | undefined> {
  await loaded;
  const kept = await store.get<KeptAnswer<T>>("kept", prefix() + url);
  if (!kept) return undefined;
  if (kept.data !== null && typeof kept.data === "object") keptTimes.set(kept.data as object, kept.kept_at);
  return kept.data;
}

/** Which kept answers to drop to get under `budget`: the oldest first. Job lists and the pack itself don't count. */
export function toEvict(all: [string, Partial<KeptAnswer>][], budget = BUDGET_BYTES): string[] {
  const answers = all.filter(([k]) => k.startsWith("ans:")).sort(([, a], [, b]) => (a.kept_at ?? "").localeCompare(b.kept_at ?? ""));
  let total = answers.reduce((n, [, a]) => n + (a.bytes ?? 0), 0);
  const out: string[] = [];
  for (const [k, a] of answers) {
    if (total <= budget) break;
    out.push(k);
    total -= a.bytes ?? 0;
  }
  return out;
}

let evicting: ReturnType<typeof setTimeout> | null = null;
function scheduleEvict() {
  if (evicting) return;
  evicting = setTimeout(async () => {
    evicting = null;
    for (const k of toEvict(await store.entries<KeptAnswer>("kept"))) await store.remove("kept", k);
  }, 2000);
}

/** Forget what was kept for this account (at sign-out). The outbox is left alone: it is the operator's work. */
export async function clearKept(account = store.getScope()): Promise<void> {
  const mine = (await store.entries("kept")).map(([k]) => k)
    .filter((k) => k.startsWith(`ans:${account}:`) || k.startsWith(`pack:${account}:`) || k.startsWith(`jobs:${account}:`));
  for (const k of mine) await store.remove("kept", k);
  ids = new Set();
}
