/** The field without signal (P5.3c, #643). The operator's jobs, with their instructions and assets, are kept on the
 * phone each time they load, and read from there when the server can't be reached. A report made without signal
 * waits in an outbox with an id and the time it was made, and is sent when the signal returns; the server applies
 * each id once and keeps that time, so a report sent twice arrives once. */
import { api, type FieldJob, type FieldReading, type PlanItem } from "./api";

export interface QueuedReport {
  client_id: string; exception_id: number; by: string; status: string; note?: string; at: string;
  reading?: FieldReading; photos?: string[];
  /** Why it starts without a valid permit (P5.11a). */
  override?: string;
  /** A permit confirmed in hand where none could be checked (#742). */
  permit_in_hand?: boolean;
}
type Jobs = { person: string; jobs: FieldJob[]; coming_up: PlanItem[]; offline?: { since: string } };

const OUTBOX = "fuzer.field.outbox";
const JOBS = "fuzer.field.jobs.";
export const OUTBOX_CHANGED = "fuzer:outbox";

function read<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) ?? "") as T; } catch { return fallback; }
}
function write(key: string, value: unknown): boolean {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
}

/** A fetch that never reached the server (no signal), as opposed to the server saying no. */
export const unreachable = (e: unknown) => e instanceof TypeError || (typeof navigator !== "undefined" && !navigator.onLine);

export const outbox = (): QueuedReport[] => read<QueuedReport[]>(OUTBOX, []);

/** The person's jobs from the server, kept on the phone; without signal, the ones kept last time. */
export async function fieldJobs(person: string): Promise<Jobs> {
  try {
    const fresh = await api.fieldJobs(person);
    write(JOBS + person, { ...fresh, kept_at: new Date().toISOString() });
    return fresh;
  } catch (e) {
    const kept = read<(Jobs & { kept_at?: string }) | null>(JOBS + person, null);
    if (!unreachable(e) || !kept) throw e;
    return { ...kept, offline: { since: kept.kept_at ?? "" } };
  }
}

const newId = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID()
  : `${Date.now()}-${Math.random().toString(36).slice(2)}`);

/** Send a report now; without signal, keep it in the outbox. Returns "sent" or "queued". */
export async function sendReport(r: Omit<QueuedReport, "client_id" | "at">): Promise<"sent" | "queued"> {
  const q: QueuedReport = { ...r, client_id: newId(), at: new Date().toISOString() };
  try {
    await post(q);
    return "sent";
  } catch (e) {
    if (!unreachable(e)) throw e;
    if (!write(OUTBOX, [...outbox(), q])) throw new Error("No signal, and the phone has no room to keep this report: try without photos.");
    window.dispatchEvent(new Event(OUTBOX_CHANGED));
    return "queued";
  }
}

const post = (q: QueuedReport) => api.fieldReport(q.exception_id, q.by, q.status, q.note,
  { reading: q.reading, photos: q.photos, client_id: q.client_id, at: q.at, override: q.override,
    permit_in_hand: q.permit_in_hand });

let flushing = false;
/** Send what waited, oldest first. A report the server refuses is dropped and its reason returned, so the operator
 * hears it; one that still can't reach the server stays for next time. */
export async function flush(): Promise<{ sent: number; refused: string[] }> {
  const out = { sent: 0, refused: [] as string[] };
  if (flushing) return out;
  flushing = true;
  try {
    for (const q of outbox()) {
      try {
        await post(q);
        out.sent++;
      } catch (e) {
        if (unreachable(e)) break;
        out.refused.push((e as Error).message);
      }
      write(OUTBOX, outbox().filter((x) => x.client_id !== q.client_id));
    }
  } finally {
    flushing = false;
    if (out.sent || out.refused.length) window.dispatchEvent(new Event(OUTBOX_CHANGED));
  }
  return out;
}
