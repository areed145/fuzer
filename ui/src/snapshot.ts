/** What each read last answered in this tab, so a refresh or a return to a page shows it at once instead of a blank
 * page, while the read asks the server again. Kept in `sessionStorage`: it survives a reload of the tab and is gone
 * when the tab closes, so opening the app fresh never shows another day's answers. Kept per account and project, for
 * half an hour at most, under a size budget; only plain JSON answers are kept, never a file or an answer read from the
 * phone's field pack. Signing out forgets it.
 *
 * A wave holds fresh answers so that the reads shown from here change together, never one figure new and the one next
 * to it old: a page that opens with kept answers (after a reload, or going back to it) holds the fresh answers of every
 * read it starts until all are in, and so does Refresh. No React here: `useAsync` uses it. */

/** Answers are kept per build of the app: a new version may read an answer's fields differently. */
const BUILD = typeof document === "undefined" ? ""
  : new URL(document.querySelector<HTMLScriptElement>("script[type=module][src]")?.src ?? "x:", "x:/").pathname;
const ROOT = "fz:snap:";
const PREFIX = `${ROOT}${BUILD}|`;
export const MAX_AGE_MS = 30 * 60_000;
const ENTRY_LIMIT = 256 * 1024;
const BUDGET = 3 * 1024 * 1024;
/** The longest a wave holds answers that have already come back while others are still out. */
export const WAVE_LIMIT_MS = 10_000;

interface Entry { at: number; json: string }
const entries = new Map<string, Entry>();
const dirty = new Set<string>();
let used = 0;

function storage(): Storage | null {
  try { return typeof sessionStorage === "undefined" ? null : sessionStorage; } catch { return null; }
}

(function load() {
  const s = storage();
  if (!s) return;
  const now = Date.now();
  try {
    for (let i = s.length - 1; i >= 0; i--) {
      const name = s.key(i);
      if (!name?.startsWith(ROOT)) continue;
      if (!name.startsWith(PREFIX)) { s.removeItem(name); continue; }
      const raw = s.getItem(name) ?? "";
      const cut = raw.indexOf("|");
      const at = Number(raw.slice(0, cut));
      if (cut < 0 || !(now - at < MAX_AGE_MS)) { s.removeItem(name); continue; }
      entries.set(name.slice(PREFIX.length), { at, json: raw.slice(cut + 1) });
      used += raw.length;
    }
  } catch { /* storage refused: start empty */ }
})();

/** Only primitives, arrays and plain objects: a Map, a Date or a file would not come back as it went in. */
function plain(value: unknown, depth = 0): boolean {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (depth > 12 || typeof value !== "object") return false;
  if (Array.isArray(value)) return value.every((v) => plain(v, depth + 1));
  const proto = Object.getPrototypeOf(value);
  return (proto === Object.prototype || proto === null) && Object.values(value).every((v) => v === undefined || plain(v, depth + 1));
}

/** The key of a read, or null when it can't be told apart safely from another (its inputs aren't plain values). */
export function snapshotKey(scope: string, project: string, place: string, read: string, deps: unknown[]): string | null {
  if (!deps.every((d) => d === undefined || d === null || ["string", "number", "boolean"].includes(typeof d))) return null;
  const route = typeof location === "undefined" ? "" : location.hash.split("?")[0];
  return `${scope}|${project}|${route}|${place}|${read}|${JSON.stringify(deps)}`;
}

/** The answer this read last gave in this tab, if it is recent enough. */
export function recall<T>(key: string | null): { value: T } | undefined {
  const entry = key ? entries.get(key) : undefined;
  if (!entry || Date.now() - entry.at >= MAX_AGE_MS) return undefined;
  try { return { value: JSON.parse(entry.json) as T }; } catch { return undefined; }
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;
function save() {
  saveTimer = null;
  const s = storage();
  if (!s) return;
  for (const key of dirty) {
    const entry = entries.get(key);
    try {
      if (entry) s.setItem(PREFIX + key, `${entry.at}|${entry.json}`); else s.removeItem(PREFIX + key);
    } catch {
      // Over the browser's quota: drop the oldest half and keep going without this one.
      [...entries.entries()].sort((a, b) => a[1].at - b[1].at).slice(0, Math.ceil(entries.size / 2))
        .forEach(([k]) => { entries.delete(k); try { s.removeItem(PREFIX + k); } catch { /* ignore */ } });
      break;
    }
  }
  dirty.clear();
}

/** Keep the answer a read gave. `skip` answers (from the field pack) are not kept. */
export function remember(key: string | null, value: unknown, skip = false): void {
  if (!key) return;
  if (skip || !plain(value)) { forgetKey(key); return; }
  let json: string;
  try { json = JSON.stringify(value); } catch { return; }
  if (json.length > ENTRY_LIMIT) { forgetKey(key); return; }
  const old = entries.get(key);
  used += json.length - (old?.json.length ?? 0);
  entries.set(key, { at: Date.now(), json });
  dirty.add(key);
  // Past the budget, the oldest answers go first.
  if (used > BUDGET) {
    for (const [k, e] of [...entries.entries()].sort((a, b) => a[1].at - b[1].at)) {
      if (used <= BUDGET * 0.8) break;
      entries.delete(k); dirty.add(k); used -= e.json.length;
    }
  }
  if (!saveTimer) saveTimer = setTimeout(save, 500);
}

function forgetKey(key: string) {
  const old = entries.get(key);
  if (!old) return;
  used -= old.json.length;
  entries.delete(key);
  dirty.add(key);
  if (!saveTimer) saveTimer = setTimeout(save, 500);
}

/** Forget every kept answer (signing out, or the session ended). */
export function forgetSnapshot(): void {
  entries.clear(); dirty.clear(); used = 0;
  const s = storage();
  if (!s) return;
  try {
    for (let i = s.length - 1; i >= 0; i--) { const k = s.key(i); if (k?.startsWith(ROOT)) s.removeItem(k); }
  } catch { /* ignore */ }
}

if (typeof window !== "undefined") {
  // iOS can end an installed app without notice: what is waiting to be saved is saved when the page is hidden.
  window.addEventListener("pagehide", () => { if (saveTimer) { clearTimeout(saveTimer); save(); } });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && saveTimer) { clearTimeout(saveTimer); save(); }
  });
  window.addEventListener("fuzer:unauthorized", forgetSnapshot);
}

/** Fires when data changes (e.g. after a review decision) so views can refresh. */
export const CHANGED = "fuzer:changed";
export const notifyChanged = () => window.dispatchEvent(new Event(CHANGED));

// --- Waves -----------------------------------------------------------------------------------------------------------

const WAVE = "fuzer:wave";
let wave: { pending: number; held: (() => void)[]; timer: ReturnType<typeof setTimeout> } | null = null;

/** Whether reads are being held to change together; the Refresh button shows it. */
export const waveOpen = () => wave !== null;
export const subscribeWave = (on: () => void) => {
  window.addEventListener(WAVE, on);
  return () => window.removeEventListener(WAVE, on);
};

function flush() {
  if (!wave) return;
  const { held, timer } = wave;
  clearTimeout(timer);
  wave = null;
  held.forEach((apply) => apply());  // one task: React renders every change together
  window.dispatchEvent(new Event(WAVE));
}

/** Start holding answers until every read started from now on has come back (or `WAVE_LIMIT_MS` has passed). */
export function openWave(): void {
  if (wave) return;
  wave = { pending: 0, held: [], timer: setTimeout(flush, WAVE_LIMIT_MS) };
  window.dispatchEvent(new Event(WAVE));
  // Nothing joined (no page reads anything): close at once rather than spin.
  setTimeout(() => { if (wave && wave.pending === 0) flush(); }, 50);
}

/** A read starting now joins the open wave, if there is one. Returns how to hand its answer back: `settle(apply)`
 * applies at once outside a wave, or holds it until the wave ends; `drop()` is for a read that was abandoned. */
export function joinWave(): { settle: (apply: () => void) => void; drop: () => void } {
  const mine = wave;
  if (!mine) return { settle: (apply) => apply(), drop: () => {} };
  mine.pending++;
  let done = false;
  const leave = () => {
    if (done) return false;
    done = true;
    mine.pending--;
    return true;
  };
  return {
    settle: (apply) => {
      if (!leave() || wave !== mine) { apply(); return; }
      mine.held.push(apply);
      // A read's answer often starts the reads below it (a list, then its rows): wait a moment for them to join.
      if (mine.pending === 0) setTimeout(() => { if (wave === mine && mine.pending === 0) flush(); }, 30);
    },
    drop: () => { if (leave() && wave === mine && mine.pending === 0) setTimeout(() => { if (wave === mine && mine.pending === 0) flush(); }, 30); },
  };
}

/** Refresh (the top bar's button, and pull to refresh in the installed app): every view reads again in place, keeping
 * what it shows until the new answers are all in, and they change together. A new version of the app on the server
 * loads the page afresh instead, so Refresh still picks up an update. */
export async function refreshAll(): Promise<void> {
  openWave();
  notifyChanged();
  const loaded = [...document.querySelectorAll<HTMLScriptElement>("script[type=module][src]")].map((s) => new URL(s.src).pathname);
  try {
    const page = await (await fetch("/", { cache: "no-store" })).text();
    if (loaded.length && !loaded.every((src) => page.includes(src))) location.reload();
  } catch { /* no signal: the reads themselves say so */ }
}
