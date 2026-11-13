/** The app's own history, for the installed app (P5.14a, #985). Added to a phone's home screen, Fuzer runs without
 * the browser's back and forward buttons (iOS has no back gesture there either), so the shell draws its own Back.
 *
 * Each history entry carries its depth in the app: 0 for the first page this launch, one more for each page opened
 * from inside the app. Back walks history while there is a page behind; with none (opened from a link, or a cold
 * launch) it goes up to the page's owner instead, so it never leaves the app.
 *
 * A redirect (location.replace) doesn't add an entry, and neither does a page rewriting its own query. A new entry
 * is told from a replaced one by history growing; when that can't be told (a new page opened right after one step
 * back), the depth is kept, which can only send Back up a level, never out of the app. */
import { useEffect, useState } from "react";

const KEY = "fzDepth";
const ENTRY = "fzKey";
const SCROLLS = "fuzer.scrolls";
const LAST = "fuzer.lastRoute";
/** A cold launch older than this starts at Home, not on yesterday's page. */
const RESUME_MS = 12 * 60 * 60 * 1000;

let depth = 0;
let length = 0;
let entry = "";
/** Where each history entry was scrolled to, so Back lands where the operator left the list (P5.14c). */
const scrolls = new Map<string, number>(readScrolls());

function readScrolls(): [string, number][] {
  try { return JSON.parse(sessionStorage.getItem(SCROLLS) ?? "[]"); } catch { return []; }
}
let saving = 0;
function writeScrolls() {
  window.clearTimeout(saving);
  try { sessionStorage.setItem(SCROLLS, JSON.stringify([...scrolls].slice(-50))); } catch { /* private mode */ }
}
function saveScrolls() {
  window.clearTimeout(saving);
  saving = window.setTimeout(writeScrolls, 300);
}
const newKey = () => Math.random().toString(36).slice(2, 10);
const stateKey = (): string | undefined => {
  const value = (history.state as Record<string, unknown> | null)?.[ENTRY];
  return typeof value === "string" ? value : undefined;
};

let restoring = 0;
/** Scroll back to where an entry was left. The page draws its data a moment later, so keep at it until it holds, for up
 * to two seconds, and stop as soon as the operator touches or scrolls the page themselves. */
function restore(y: number) {
  window.cancelAnimationFrame(restoring);
  const until = performance.now() + 2000;
  let stop = false;
  const cancel = () => { stop = true; };
  window.addEventListener("touchstart", cancel, { once: true, passive: true });
  window.addEventListener("wheel", cancel, { once: true, passive: true });
  window.addEventListener("keydown", cancel, { once: true });
  // Held until it has stayed there for half a second: the page's data arriving above the fold can shift it again.
  let settled = 0;
  const step = () => {
    if (stop) return;
    if (Math.abs(window.scrollY - y) > 2) { window.scrollTo(0, y); settled = 0; }
    else if (!settled) settled = performance.now();
    const now = performance.now();
    if (now < until && (!settled || now - settled < 500)) restoring = window.requestAnimationFrame(step);
  };
  step();
}
const listeners = new Set<() => void>();

const stateDepth = (): number | undefined => {
  const value = (history.state as Record<string, unknown> | null)?.[KEY];
  return typeof value === "number" ? value : undefined;
};

function record(next: number, key: string) {
  depth = next;
  length = history.length;
  entry = key;
  history.replaceState({ ...(history.state as object | null), [KEY]: next, [ENTRY]: key }, "");
}

/** Installed on the home screen and opened from there (standalone display), not in a browser tab. */
export function installed(): boolean {
  try {
    return matchMedia("(display-mode: standalone)").matches || matchMedia("(display-mode: fullscreen)").matches
      || (navigator as Navigator & { standalone?: boolean }).standalone === true;
  } catch {
    return false;
  }
}

function remember() {
  try { localStorage.setItem(LAST, JSON.stringify({ hash: location.hash, at: Date.now() })); } catch { /* private mode */ }
}

/** Where a cold launch of the installed app should land: the last page from the last 12 hours, when it was opened at
 * Home with no route of its own. */
export function resumeRoute(now = Date.now()): string | undefined {
  if (!installed() || (location.hash && location.hash !== "#/" && location.hash !== "#")) return undefined;
  // With no signal, open to My work, which reads the jobs kept on the phone (P5.14d); other pages need the server.
  if (typeof navigator !== "undefined" && navigator.onLine === false) return "#/inbox";
  try {
    const last = JSON.parse(localStorage.getItem(LAST) ?? "null") as { hash?: string; at?: number } | null;
    if (!last?.hash || !last.hash.startsWith("#/") || last.hash === "#/" || !last.at || now - last.at > RESUME_MS) return undefined;
    return last.hash;
  } catch {
    return undefined;
  }
}

function onChange() {
  const known = stateDepth();
  // The page being left is still drawn: note where it was, before its scroll event (which may be a frame behind) is
  // credited to the next entry.
  const leaving = entry;
  const left = window.scrollY;
  if (known !== undefined) {
    // Back, forward, or a page that kept the state it had: return to where that entry was left.
    depth = known;
    length = history.length;
    const key = stateKey() ?? newKey();
    if (key !== entry) {
      scrolls.set(leaving, left);
      entry = key;
      restore(scrolls.get(key) ?? 0);
    }
  } else if (history.length > length) {
    // A new page opens at its top, not at the scroll the last page was left at.
    scrolls.set(leaving, left);
    record(depth + 1, newKey());
    window.cancelAnimationFrame(restoring);
    window.scrollTo(0, 0);
  } else {
    record(depth, entry || newKey());  // a redirect or a filter rewriting the link: same entry, same place
  }
  remember();
  listeners.forEach((listener) => listener());
}

let started = false;
/** Call once before the app renders. Returns true when it redirected to resume the last page. */
export function startAppHistory(): boolean {
  if (started) return false;
  started = true;
  try { history.scrollRestoration = "manual"; } catch { /* older browsers */ }
  record(stateDepth() ?? 0, stateKey() ?? newKey());
  window.addEventListener("scroll", () => { scrolls.set(entry, window.scrollY); saveScrolls(); }, { passive: true });
  // Leaving or reloading, or the phone putting the app in the background: keep the place now, not in 300 ms.
  window.addEventListener("pagehide", writeScrolls);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") writeScrolls(); });
  window.addEventListener("hashchange", onChange);
  window.addEventListener("popstate", onChange);
  document.documentElement.classList.toggle("installed", installed());
  const resume = resumeRoute();
  if (resume) {
    location.replace(resume);
    return true;
  }
  remember();
  // A reload keeps the entry: put the page back where it was.
  const y = scrolls.get(entry);
  if (y) restore(y);
  return false;
}

/** How many pages back the app's own history goes from here (0: this is the first page). */
export function useAppDepth(): number {
  const [value, setValue] = useState(depth);
  useEffect(() => {
    const update = () => setValue(depth);
    listeners.add(update);
    update();
    return () => { listeners.delete(update); };
  }, []);
  return value;
}

/** Back: a step through the app's history when there is one, otherwise up to `up` (replacing this entry). */
export function goBack(up: string) {
  if (depth > 0) history.back();
  else location.replace(up);
}
