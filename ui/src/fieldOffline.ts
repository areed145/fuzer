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
  /** The server said no (P5.17f): kept with its reason until the operator resends, moves or discards it. */
  refused?: { reason: string; at: string };
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

const changed = (detail?: FlushResult) => {
  window.dispatchEvent(new CustomEvent(OUTBOX_CHANGED, { detail }));
  void mine().then((all) => note({ waiting: all.length }));
};

/** What the phone should still hold for each account, noted in `localStorage` (P5.17h): iOS can clear an installed
 * app's IndexedDB under storage pressure or after weeks unused, and without the note an emptied store would look like
 * a first launch. When the whole origin is cleared the note goes too, and the next load with signal fetches again. */
const MARKER = "fuzer.field.marker";
type Marker = { kept?: boolean; waiting?: number };
let checked = false;
const readMarker = (): Record<string, Marker> => {
  try { return JSON.parse(localStorage.getItem(MARKER) ?? "{}") as Record<string, Marker>; } catch { return {}; }
};
function note(m: Marker) {
  if (!checked) return;  // not before the store has been checked against the note
  try {
    const all = readMarker();
    localStorage.setItem(MARKER, JSON.stringify({ ...all, [account]: { ...all[account], ...m } }));
  } catch { /* storage unavailable */ }
}

/** The phone cleared what it kept, and how many writes went with it, once noticed; until the operator dismisses it. */
let cleared: { lost: number } | null = null;
export const dismissCleared = () => { cleared = null; changed(); };

async function noticeCleared() {
  try {
    const was = readMarker()[account];
    if (!was || (!was.kept && !was.waiting) || !(await store.durable())) return;
    const kept = (await store.keys("kept")).some((k) => k.startsWith(`jobs:${account}:`));
    const queued = (await store.all<QueuedWrite>("outbox")).some((w) => w.account === account);
    if (!kept && !queued) cleared = { lost: was.waiting ?? 0 };
  } finally {
    checked = true;
  }
}

async function mine(): Promise<QueuedWrite[]> {
  await migrated;
  return (await store.all<QueuedWrite>("outbox")).filter((w) => w.account === account).sort((a, b) => a.at.localeCompare(b.at));
}
/** What waits to send for the signed-in account, oldest first. */
export const outbox = async () => (await mine()).filter((w) => !w.refused);
/** What the server refused, kept on the phone with its reason (P5.17f). */
export const refusedWrites = async () => (await mine()).filter((w) => w.refused);

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
  void migrated.then(async () => {
    if (!checked) await noticeCleared();
    if (cleared) note({ kept: false });
    changed();
    if (live) void flush();
  });
}

const jobsKey = (person: string) => `jobs:${account}:${getProject()}:${person}`;

/** The person's jobs from the server, kept on the phone; without signal, the ones kept last time. */
export async function fieldJobs(person: string): Promise<Jobs> {
  try {
    const fresh = await api.fieldJobs(person);
    void store.put("kept", { ...fresh, kept_at: new Date().toISOString() }, jobsKey(person))
      .then((ok) => { if (ok) note({ kept: true }); });
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
    // The task steps the jobs are on, so their forms can be filled in and closed out without signal.
    for (const t of new Set(list.map((j) => j.task_id).filter((x): x is number => typeof x === "number"))) {
      await api.workTask(t).catch(() => null);
    }
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
  note({ kept: false });
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

/** The rest of what the field does at the asset (P5.17e): a task step's close-out, accepting a handover, and saying a
 * planned job has started or is done. Each is idempotent on the server (a client id, or setting a state). */
export interface QueuedCompletion { task: number; by: string; data: Record<string, unknown>; item?: number }
registerSender("task_complete", (w) => {
  const c = w.payload as QueuedCompletion;
  return api.complete(c.task, c.by, c.data, { client_id: w.client_id, at: w.at, project: w.project });
});
export const sendCompletion = (c: QueuedCompletion) => sendOrQueue("task_complete", c);
registerSender("handover_accept", (w) => {
  const h = w.payload as { exception_id: number; by: string };
  return api.acceptHandover(h.exception_id, h.by, w.client_id, w.project);
});
export const sendHandoverAccept = (exception_id: number, by: string) => sendOrQueue("handover_accept", { exception_id, by });
registerSender("plan_change", (w) => {
  const c = w.payload as { item: number; by: string; status: string };
  return api.planChange(c.item, c.by, { status: c.status }, w.project);
});
export const sendPlanChange = (item: number, by: string, status: string) => sendOrQueue("plan_change", { item, by, status });

/** Send a job's report now; without signal, keep it in the outbox. */
export const sendReport = ({ photos, ...r }: Omit<QueuedReport, "client_id" | "at">) =>
  sendOrQueue("field_report", r, photos ?? []);

let flushing = false;
/** True while the sender is sending, for the shell's status (P5.17b). */
export const sending = () => flushing;
/** Send what waited, oldest first. A write the server refuses is never dropped and never sent again by itself
 * (P5.17f): it stays on the phone with the reason, for the operator to resend as a note, move or discard. One that
 * still can't reach the server stays for next time, and so does everything while the session needs signing in again.
 * One send at a time, across tabs where the browser offers locks. */
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
        const reason = (e as Error).message;
        out.refused.push(reason);
        await store.put("outbox", { ...w, refused: { reason, at: new Date().toISOString() } });
        continue;
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

export interface OutboxState { waiting: QueuedWrite[]; refused: QueuedWrite[]; sending: boolean;
  /** The phone cleared what it kept (P5.17h), with how many writes waiting to send went with it. */
  cleared: { lost: number } | null }
/** What waits on the phone, what the server refused and whether it is sending now, kept current. */
export function useOutbox(): OutboxState {
  const [state, setState] = useState<OutboxState>({ waiting: [], refused: [], sending: flushing, cleared });
  useEffect(() => {
    let active = true;
    const read = () => void mine().then((all) => {
      if (active) setState({ waiting: all.filter((w) => !w.refused), refused: all.filter((w) => w.refused), sending: flushing,
        cleared });
    });
    window.addEventListener(OUTBOX_CHANGED, read);
    read();
    return () => { active = false; window.removeEventListener(OUTBOX_CHANGED, read); };
  }, []);
  return state;
}

/** Throw away a write the server refused (P5.17f), once the operator has confirmed it. */
export async function discard(w: QueuedWrite): Promise<void> {
  await store.remove("outbox", w.client_id);
  changed();
}

/** What a refused write said, in a sentence a person can pass on. */
export function describe(w: QueuedWrite): string {
  const p = w.payload as Record<string, unknown>;
  const made = new Date(w.at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  const reading = (p.reading as FieldReading | undefined);
  const said: Record<string, string> = { on_site: "On site", done: "Done", could_not_do: "Could not do", note: "Note" };
  switch (w.kind) {
    case "field_report": return `${said[String(p.status)] ?? "Report"}${p.note ? `: ${p.note}` : ""}${reading ? ` (${reading.what ?
      `${reading.what} ` : ""}${reading.value} ${reading.unit})` : ""}${w.photos?.length ? `, ${w.photos.length} photo${w.photos.length > 1 ? "s" : ""}` : ""}, made ${made}`;
    case "round_take": return `Round point ${p.point}: ${p.value ?? (p.passed ? "passed" : `found something: ${p.note ?? ""}`)}, taken ${made}`;
    case "task_complete": return `Step closed out ${made}${Object.keys((p.data as object) ?? {}).length ? `: ${Object.values(p.data as object).join("; ")}` : ""}`;
    case "handover_accept": return `Handover accepted ${made}`;
    case "plan_change": return `Planned job marked ${p.status === "done" ? "done" : "started"} ${made}`;
    default: return `${w.kind}, made ${made}`;
  }
}

/** A refused write can go on as a note (P5.17f): a job report as a Note on the same job, keeping its photos; a step's
 * close-out as a note on its case. Either way the IOC still gets what was seen. Null when it can't. */
export function asNote(w: QueuedWrite): (() => Promise<void>) | null {
  const p = w.payload as Record<string, unknown>;
  const why = `Made on the phone ${new Date(w.at).toLocaleString()}, not accepted: ${w.refused?.reason ?? ""}`;
  if (w.kind === "field_report") return async () => {
    const photos = await Promise.all((w.photos ?? []).map(store.photoUrl));
    await sendOrQueue("field_report", { exception_id: p.exception_id, by: p.by, status: "note", reading: p.reading,
      note: `${describe({ ...w, photos: [] })}. ${why}` }, photos);
    await discard(w);
  };
  // A comment isn't idempotent, so it is sent now (with signal) rather than queued: without signal it fails and the
  // refused close-out stays on the phone.
  if (w.kind === "task_complete" && typeof p.item === "number") return async () => {
    await api.comment(p.item as number, String(p.by), `${describe(w)}. ${why}`, w.project);
    await discard(w);
  };
  return null;
}

/** Send a refused job report to another of the operator's jobs instead, as it was (P5.17f). */
export async function moveReport(w: QueuedWrite, exceptionId: number): Promise<void> {
  const photos = await Promise.all((w.photos ?? []).map(store.photoUrl));
  await sendOrQueue("field_report", { ...(w.payload as object), exception_id: exceptionId }, photos);
  await discard(w);
}
