/** What each read last answered, so opening the app, a reload or a return to a page shows it at once instead of a
 * blank page, while the read asks the server again (stale-while-revalidate). Kept in `localStorage` per account and
 * project, for twelve hours at most, under a size budget, so a cold open of Tower shows the last-known map and lists
 * in the first second; the shell says it is showing kept answers until the fresh ones are in (`useKept`). Only plain
 * JSON answers are kept, never a file or an answer read from the phone's field pack. Signing out, or the session
 * ending, forgets it.
 *
 * A wave holds fresh answers so that the reads shown from here change together, never one figure new and the one next
 * to it old: a page that opens with kept answers (after a reload, or going back to it) holds the fresh answers of every
 * read it starts until all are in, and so does Refresh. No React here: `useAsync` uses it. */

/** Answers outlive a new version of the app, which deploys often: a page that can't draw an answer kept by the version
 * before is caught by the app's error boundary, which forgets every kept answer and draws again (`recalledAny`).
 * `VERSION` changes only when kept answers must all go. */
const VERSION = "4";
const ROOT = "fz:snap:";
const PREFIX = `${ROOT}${VERSION}|`;
export const MAX_AGE_MS = 12 * 3_600_000;
// Characters, as the browser counts its ~5 million-character localStorage quota, shared with the app's other settings.
// A page's main list (open exceptions, My work's fields) runs to a few hundred thousand: kept, or the page opens on
// Loading after all.
const ENTRY_LIMIT = 1_200_000;
const BUDGET = 3_000_000;
/** The longest a wave holds answers that have already come back while others are still out. */
export const WAVE_LIMIT_MS = 10_000;

interface Entry { at: number; json: string }
const entries = new Map<string, Entry>();
const dirty = new Set<string>();
let used = 0;

function storage(): Storage | null {
  try { return typeof localStorage === "undefined" ? null : localStorage; } catch { return null; }
}

/** The tab-only copy this replaced: cleared once so it doesn't hold the session quota. */
try {
  if (typeof sessionStorage !== "undefined") {
    for (let i = sessionStorage.length - 1; i >= 0; i--) { const k = sessionStorage.key(i); if (k?.startsWith(ROOT)) sessionStorage.removeItem(k); }
  }
} catch { /* ignore */ }

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

/** The key without the read's place in the page (the fourth part): a cold open can mount the page's parts in another
 * order, which moves the place. */
const loose = (key: string) => { const p = key.split("|"); return [...p.slice(0, 3), ...p.slice(4)].join("|"); };
/** The answer kept for the same read with the same inputs on the same page at another place, when exactly one is:
 * two kept at different places are reads that only look alike (a shared wrapper), so neither is taken. */
function elsewhere(key: string): Entry | undefined {
  const want = loose(key);
  let found: Entry | undefined;
  for (const [k, e] of entries) {
    if (loose(k) !== want) continue;
    if (found) return undefined;
    found = e;
  }
  return found;
}

let recalled = false;
/** Whether any kept answer has been drawn since the page loaded (the error boundary's cue to forget them and retry). */
export const recalledAny = () => recalled;

/** The answer this read last gave, if it is recent enough. Notes how old it is, for the shell's "kept" notice. */
export function recall<T>(key: string | null): { value: T; at: number } | undefined {
  const entry = key ? entries.get(key) ?? elsewhere(key) : undefined;
  if (!entry || Date.now() - entry.at >= MAX_AGE_MS) return undefined;
  try {
    const value = JSON.parse(entry.json) as T;
    recalled = true;
    return { value, at: entry.at };
  } catch { return undefined; }
}

/** When the oldest kept answer on screen was given, while the fresh answers are still coming in; null otherwise. */
let oldestShown: number | null = null;
export const keptSince = () => (wave ? oldestShown : null);
/** A kept answer given at `at` is on screen in the open wave. */
export function showingKept(at: number): void {
  if (!wave) return;
  // Called while a page draws: tell the shell after it, not during it.
  if (oldestShown === null || at < oldestShown) { oldestShown = at; queueMicrotask(() => window.dispatchEvent(new Event(WAVE))); }
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

/** Fires when data changes (e.g. after a review decision) so views can refresh. A change ends a wave that is holding
 * answers (P5.28d): opened by kept answers on the way in, it held the reads made after an action with the rest, up to
 * ten seconds, so a case just dispatched or answered still showed its old owner, or Open, after the confirmation. */
export const CHANGED = "fuzer:changed";
export const notifyChanged = () => {
  flush();
  window.dispatchEvent(new Event(CHANGED));
};

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
  oldestShown = null;
  held.forEach((apply) => apply());  // one task: React renders every change together
  window.dispatchEvent(new Event(WAVE));
}

/** Start holding answers until every read started from now on has come back (or `WAVE_LIMIT_MS` has passed). */
export function openWave(): void {
  if (wave) return;
  oldestShown = null;
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
  window.dispatchEvent(new Event(CHANGED));  // not notifyChanged, which would end the wave just opened
  const loaded = [...document.querySelectorAll<HTMLScriptElement>("script[type=module][src]")].map((s) => new URL(s.src).pathname);
  try {
    const page = await (await fetch("/", { cache: "no-store" })).text();
    if (loaded.length && !loaded.every((src) => page.includes(src))) location.reload();
  } catch { /* no signal: the reads themselves say so */ }
}
