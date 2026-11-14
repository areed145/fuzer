/** The field without signal (P5.3c, #643; P5.17a, #1090). The operator's jobs, with their instructions and assets, are
 * kept on the phone each time they load, and read from there when the server can't be reached. A write made without
 * signal waits in an outbox with an id and the time it was made, and is sent when the signal returns; the server
 * applies each id once and keeps that time, so a write sent twice arrives once.
 *
 * Both live in IndexedDB (`fieldStore.ts`), photos as files. The outbox belongs to the account that made it and the
 * project it was made in, and one sender serves the whole app: it sends on launch, when the signal comes back, when
 * the app returns to the foreground and every 30 s while it is on screen, from whichever page is open. iOS has no
 * background sync, so nothing is sent while the app is closed. Only writes that never reached the server are sent
 * again; one the server refused is not resent (AGENTS.md), and an expired session holds the outbox until sign-in. */
import { useEffect, useState } from "react";
import { api, type FieldJob, type FieldReading, type PlanItem } from "./api";
import { getProject, projectUrl } from "./authState";
import * as store from "./fieldStore";
import { clearKept, loadPack, setPack } from "./fieldPack";
import { requestFile } from "./api/transport";

export interface QueuedReport {
  client_id: string; exception_id: number; by: string; status: string; note?: string; at: string;
  reading?: FieldReading; photos?: string[];
  /** Why it starts without a valid permit (P5.11a). */
  override?: string;
  /** A permit confirmed in hand where none could be checked (#742). */
  permit_in_hand?: boolean;
}

/** One write waiting on the phone. `kind` picks its sender; photos are kept apart from the payload, as files. */
export interface QueuedWrite<P = unknown> {
  client_id: string; kind: string; at: string;
  /** The signed-in account that made it ("" in local mode), and the project it was made in. */
  account: string; project: string;
  payload: P; photos?: store.KeptPhoto[];
}

type Jobs = { person: string; jobs: FieldJob[]; coming_up: PlanItem[]; offline?: { since: string } };
type Sender = (w: QueuedWrite, photos: string[]) => Promise<unknown>;

export const OUTBOX_CHANGED = "fuzer:outbox";
/** The outcome of a send, as the detail of an OUTBOX_CHANGED event. */
export interface FlushResult { sent: number; refused: string[]; held: boolean }

/** A fetch that never reached the server (no signal), as opposed to the server saying no. */
export const unreachable = (e: unknown) => e instanceof TypeError || (typeof navigator !== "undefined" && !navigator.onLine);

/** The server said this session can't send right now (signed out, or its confirmation expired): keep everything and
 * wait for sign-in, rather than treating it as a refusal. */
const heldBySession = (e: unknown) => {
  const { status, message } = e as { status?: number; message?: string };
  return status === 401 || status === 403 && /^Session confirmation/.test(message ?? "");
};

let account = "";
let live = false;
let migrated: Promise<void> | null = null;
let packLoaded: Promise<void> | null = null;

const senders = new Map<string, Sender>();
/** How a kind of queued write is sent. Each sender must be idempotent on `client_id`. */
export function registerSender(kind: string, send: Sender) { senders.set(kind, send); }

const changed = (detail?: FlushResult) => window.dispatchEvent(new CustomEvent(OUTBOX_CHANGED, { detail }));

/** What waits to send for the signed-in account, oldest first. */
export async function outbox(): Promise<QueuedWrite[]> {
  await migrated;
  return (await store.all<QueuedWrite>("outbox")).filter((w) => w.account === account).sort((a, b) => a.at.localeCompare(b.at));
}

const LEGACY_OUTBOX = "fuzer.field.outbox";
const LEGACY_JOBS = "fuzer.field.jobs.";

/** Move what P5.3c kept in `localStorage` into the store, then clear it. */
async function migrate() {
  let legacy: QueuedReport[] = [];
  try { legacy = JSON.parse(localStorage.getItem(LEGACY_OUTBOX) ?? "[]") as QueuedReport[]; } catch { /* nothing kept */ }
  let ok = true;
  for (const r of Array.isArray(legacy) ? legacy : []) {
    const { photos, client_id, at, ...payload } = r;
    const kept = await Promise.all((photos ?? []).map(store.keepPhoto));
    ok = await store.put("outbox", { client_id, kind: "field_report", at, account, project: getProject(), payload,
      photos: kept } satisfies QueuedWrite) && ok;
  }
  try {
    if (ok) localStorage.removeItem(LEGACY_OUTBOX);
    for (const key of Object.keys(localStorage).filter((k) => k.startsWith(LEGACY_JOBS))) {
      const person = key.slice(LEGACY_JOBS.length);
      const jobs = JSON.parse(localStorage.getItem(key) ?? "null");
      if (jobs && await store.put("kept", jobs, jobsKey(person))) localStorage.removeItem(key);
    }
  } catch { /* storage unavailable */ }
}

/** Who is signed in, and whether the server confirmed it just now (`live`) or the app runs on the session the phone
 * kept. Called by the sign-in gate; the outbox sends only on a live session. */
export function outboxSession(actor: string, isLive: boolean) {
  if (actor !== account || !packLoaded) { store.setScope(actor); packLoaded = loadPack(); }
  account = actor;
  live = isLive;
  migrated ??= migrate().catch(() => undefined);
  void migrated.then(() => { changed(); if (live) void flush(); });
}

const jobsKey = (person: string) => `jobs:${account}:${getProject()}:${person}`;

/** The person's jobs from the server, kept on the phone; without signal, the ones kept last time. */
export async function fieldJobs(person: string): Promise<Jobs> {
  try {
    const fresh = await api.fieldJobs(person);
    void store.put("kept", { ...fresh, kept_at: new Date().toISOString() }, jobsKey(person));
    void warmPack(fresh.jobs);
    return fresh;
  } catch (e) {
    if (!unreachable(e)) throw e;
    await migrated;
    const kept = await store.get<Jobs & { kept_at?: string }>("kept", jobsKey(person));
    if (!kept) throw e;
    return { ...kept, offline: { since: kept.kept_at ?? "" } };
  }
}

/** How often the field pack is read again while the operator's jobs stay the same. */
const WARM_EVERY_MS = 15 * 60_000;
let warming = false;

/** Keep the day's work on the phone (P5.17c): the pack is each job's assets and their documents and drawings, and
 * the reads an asset's page opens with are made once now, one at a time through the bounded queue, so they are kept
 * before the operator walks out of signal. Anything else a pack page reads is kept when it is opened. Read again
 * when the jobs change, or every 15 minutes. */
export async function warmPack(jobs: FieldJob[], force = false): Promise<void> {
  if (warming) return;
  await packLoaded;
  const list = Array.isArray(jobs) ? jobs : [];
  const assets = [...new Set(list.flatMap((j) => (j.assets ?? []).map((a) => a.fuzer_id)))].sort();
  const key = `warm:${account}:${getProject()}`;
  const last = await store.get<{ at: string; assets: string }>("kept", key);
  if (!force && last && last.assets === assets.join(",") && Date.now() - Date.parse(last.at) < WARM_EVERY_MS) return;
  warming = true;
  try {
    const ids = new Set(assets);
    await setPack(ids);
    const drawings = new Set<string>(), documents = new Set<string>();
    for (const id of assets) {
      const [d, docs] = await Promise.all([api.entityDrawings(id).catch(() => null), api.documentsFor(id).catch(() => [])]);
      (Array.isArray(d?.drawings) ? d.drawings : []).forEach((x) => drawings.add(x.id));
      (Array.isArray(docs) ? docs : []).forEach((x) => documents.add(x.id));
    }
    await setPack([...ids, ...drawings, ...documents]);
    for (const id of assets) {
      await Promise.allSettled([api.entity(id), api.entityStatus(id), api.entityProfile(id),
        api.exceptions({ state: "open", subject: id }), api.plan({ subject: id }), api.trendView(id, undefined, 48, 24)]);
      await Promise.allSettled([api.incidents(id), api.related({ subject: id }), api.events(id), api.assetThreads(id),
        api.entityMap(id)]);
    }
    // This shift's rounds and their points (kept whoever they name), so a round can be walked without signal.
    const runs = await api.rounds().catch(() => []);
    for (const r of Array.isArray(runs) ? runs : []) await api.round(r.id).catch(() => null);
    for (const id of drawings) {
      const g = await api.drawing(id).catch(() => null);
      if (g?.image) await requestFile(projectUrl(g.image)).catch(() => null);
    }
    for (const id of documents) {
      if (drawings.has(id)) continue;
      await Promise.allSettled([api.entity(id), requestFile(projectUrl(`/documents/${encodeURIComponent(id)}/file`))]);
    }
    await store.put("kept", { at: new Date().toISOString(), assets: assets.join(",") }, key);
  } catch {
    // An unexpected answer: what was kept so far stays, and the next load tries again.
  } finally {
    warming = false;
  }
}

/** Forget the pages kept for the signed-in account, at sign-out. Its outbox stays: that is the operator's work. */
export async function forgetKept(): Promise<void> {
  await clearKept(account);
  await store.remove("kept", `warm:${account}:${getProject()}`);
}

const newId = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID()
  : `${Date.now()}-${Math.random().toString(36).slice(2)}`);

/** Send a write now; without signal, keep it in the outbox. Returns "sent" or "queued". Resolves "queued" only once
 * the phone has committed it. */
export async function sendOrQueue<P>(kind: string, payload: P, photos: string[] = []): Promise<"sent" | "queued"> {
  const w: QueuedWrite<P> = { client_id: newId(), kind, at: new Date().toISOString(), account, project: getProject(), payload };
  try {
    await sender(kind)(w, photos);
    return "sent";
  } catch (e) {
    if (!unreachable(e)) throw e;
    await migrated;
    const kept = await Promise.all(photos.map(store.keepPhoto));
    if (!(await store.put("outbox", { ...w, photos: kept }))) {
      throw new Error("No signal, and the phone has no room to keep this report: try without photos.");
    }
    void store.askToPersist();
    changed();
    return "queued";
  }
}

function sender(kind: string): Sender {
  const s = senders.get(kind);
  if (!s) throw new Error(`Nothing sends ${kind}`);
  return s;
}

registerSender("field_report", (w, photos) => {
  const q = w.payload as Omit<QueuedReport, "client_id" | "at" | "photos">;
  return api.fieldReport(q.exception_id, q.by, q.status, q.note, { reading: q.reading, photos, client_id: w.client_id,
    at: w.at, override: q.override, permit_in_hand: q.permit_in_hand, project: w.project });
});

/** A reading or check on a round point (P5.17d). */
export interface QueuedTake { run: number; point: string; by: string; value?: number; passed?: boolean; note?: string }
registerSender("round_take", (w) => {
  const { run, ...t } = w.payload as QueuedTake;
  return api.takeRound(run, { ...t, client_id: w.client_id, at: w.at }, w.project);
});
/** Take a round point now; without signal, keep it in the outbox. */
export const sendTake = (t: QueuedTake) => sendOrQueue("round_take", t);

/** Send a job's report now; without signal, keep it in the outbox. */
export const sendReport = ({ photos, ...r }: Omit<QueuedReport, "client_id" | "at">) =>
  sendOrQueue("field_report", r, photos ?? []);

let flushing = false;
/** True while the sender is sending, for the shell's status (P5.17b). */
export const sending = () => flushing;
let lastRefused: string[] = [];
/** The reasons the last send was refused, so a page opened after it can still say so. */
export const refusedReasons = () => lastRefused;
export const clearRefused = () => { lastRefused = []; changed(); };

/** Send what waited, oldest first. A write the server refuses is dropped and its reason returned, so the operator
 * hears it; one that still can't reach the server stays for next time, and so does everything while the session
 * needs signing in again. One send at a time, across tabs where the browser offers locks. */
export async function flush(): Promise<FlushResult> {
  const out: FlushResult = { sent: 0, refused: [], held: false };
  if (flushing || !live || !(await outbox()).length || flushing) return out;
  flushing = true;
  changed();
  const run = async () => {
    for (const w of await outbox()) {
      try {
        await sender(w.kind)(w, await Promise.all((w.photos ?? []).map(store.photoUrl)));
        out.sent++;
      } catch (e) {
        if (unreachable(e)) break;
        if (heldBySession(e)) { out.held = true; break; }
        out.refused.push((e as Error).message);
      }
      await store.remove("outbox", w.client_id);
    }
  };
  try {
    const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
    if (locks) await locks.request("fuzer-outbox", { ifAvailable: true }, async (lock) => { if (lock) await run(); });
    else await run();
  } finally {
    flushing = false;
    if (out.refused.length) lastRefused = out.refused;
    changed(out);
  }
  return out;
}

let started = false;
/** Start the one sender for the app. */
export function startOutbox() {
  if (started || typeof window === "undefined") return;
  started = true;
  const visible = () => document.visibilityState === "visible";
  window.addEventListener("online", () => void flush());
  document.addEventListener("visibilitychange", () => { if (visible()) void flush(); });
  window.setInterval(() => { if (visible()) void flush(); }, 30_000);
}

export interface OutboxState { waiting: QueuedWrite[]; refused: string[]; sending: boolean }
/** What waits on the phone, what the server refused and whether it is sending now, kept current. */
export function useOutbox(): OutboxState {
  const [state, setState] = useState<OutboxState>({ waiting: [], refused: lastRefused, sending: flushing });
  useEffect(() => {
    let active = true;
    const read = () => void outbox().then((waiting) => {
      if (active) setState({ waiting, refused: lastRefused, sending: flushing });
    });
    window.addEventListener(OUTBOX_CHANGED, read);
    read();
    return () => { active = false; window.removeEventListener(OUTBOX_CHANGED, read); };
  }, []);
  return state;
}
