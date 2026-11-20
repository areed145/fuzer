import { getCsrf, getProject, switchProject } from "../authState";
import { scheduleRequest } from "../requestQueue";
import { lost, reached } from "../signal";
import { inPack, keepAnswer, keptAnswer } from "../fieldPack";

/** A fetch that tells the signal (P5.17b) whether the server was reached. */
async function reach(input: string, init?: RequestInit, deadline?: number): Promise<Response> {
  const abort = deadline && typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = abort ? setTimeout(() => abort.abort(), deadline) : null;
  try {
    const res = await fetch(input, abort ? { ...init, signal: abort.signal } : init);
    reached();
    return res;
  } catch (e) {
    // No answer in time on a connection that says it is up (P5.17h): treated as never reaching the server.
    const unanswered = abort?.signal.aborted ? new TypeError("No answer from the server in time.") : e;
    if (unanswered instanceof TypeError) lost();
    throw unanswered;
  } finally {
    if (timer) clearTimeout(timer);  // the answer has started: a slow body is not cut off
  }
}

/** How long to wait for an answer to start before treating the server as unreachable (P5.17h). On lie-fi (a
 * connection that says it is up and answers nothing) a request can otherwise wait for minutes, holding one of the
 * queue's four places, so a few silent reads stop everything else. A read the phone keeps then comes from the phone;
 * any other read gives up after longer than the slowest page takes; a write that carries a client id, which the server
 * applies once however often it is sent, is kept in the outbox. Other writes wait as long as the browser does, since
 * whether they arrived can't be known. */
export const KEPT_READ_DEADLINE_MS = 15_000;
export const READ_DEADLINE_MS = 90_000;
export const KEPT_WRITE_DEADLINE_MS = 60_000;
const carriesClientId = (body: unknown) =>
  body !== null && typeof body === "object" && typeof (body as { client_id?: unknown }).client_id === "string";

/** No signal, and the phone kept nothing for this read (P5.17c). Still a TypeError, as an unreached fetch is. */
const notKept = () => new TypeError("Not kept on this phone, and there is no signal.");

/** A read the field pack covers is kept when answered and read from the phone when the server can't be reached. */
async function keptRead<T>(url: string, read: () => Promise<T>, file = false): Promise<T> {
  try {
    const data = await read();
    void keepAnswer(url, data, file);
    return data;
  } catch (e) {
    if (!(e instanceof TypeError)) throw e;
    const kept = await keptAnswer<T>(url);
    if (kept === undefined) throw notKept();
    return kept;
  }
}

/** The live deployment keeps its job state on a disk only one server can hold, so each deploy stops the old server
 * before the new one starts, and for about a minute the host answers 502, 503 or 504 for every request. A read that
 * meets one waits and asks again on these delays (about 90 s in all), so a page opened mid-deploy loads once the new
 * server is up instead of showing a bare status code. Writes are never retried here: whether they arrived can't be
 * known. */
export const RESTART_RETRY_MS = [2_000, 4_000, 8_000, 15_000, 15_000, 15_000, 15_000, 15_000];
export const RESTARTING = "Fuzer is restarting after an update. Try again in a minute.";
const restarting = (status: number) => status === 502 || status === 503 || status === 504;
const pause = (ms: number) => new Promise(done => setTimeout(done, ms));

type Params = Record<string, string | number | undefined | null>;

/** `project` overrides the picked project, for a write kept on the phone that must go to the project it was made in. */
export async function request<T>(method: string, path: string, params?: Params, body?: unknown,
  project: string = getProject()): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const url = qs.size ? `${path}?${qs}` : path;
  const csrf = getCsrf();
  const readOnly = method === "GET" && !params?.claude && !path.endsWith("generate");
  const deadline = readOnly ? (inPack(url) ? KEPT_READ_DEADLINE_MS : READ_DEADLINE_MS)
    : carriesClientId(body) ? KEPT_WRITE_DEADLINE_MS : undefined;
  const run = async (): Promise<T> => {
    const send = () => reach(url, {
      method,
      headers: { ...(body ? { "Content-Type": "application/json" } : {}),
        ...(csrf ? { "X-Fuzer-CSRF": csrf } : {}),
        ...(project ? { "X-Fuzer-Project": project } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    }, deadline);
    let res = await send();
    for (let i = 0; readOnly && restarting(res.status) && i < RESTART_RETRY_MS.length; i++) {
      await pause(RESTART_RETRY_MS[i]);
      res = await send();
    }
    if (readOnly && restarting(res.status)) throw Object.assign(new Error(RESTARTING), { status: res.status });
    if (!res.ok) {
      if (res.status === 401) window.dispatchEvent(new Event("fuzer:unauthorized"));
      const detail = await res.json().catch(() => null);
      // The project picked earlier is gone from this instance: go back to the server's own project.
      if (res.status === 404 && project && String(detail?.detail ?? "").startsWith("No project named")) switchProject("");
      throw Object.assign(new Error(detail?.detail ?? `${res.status} ${res.statusText}`), { body: detail, status: res.status });
    }
    return res.json();
  };
  return readOnly ? scheduleRequest(() => keptRead(url, run), `${csrf}:${project}:${url}`) : scheduleRequest(run);
}

export async function requestText(path: string): Promise<string> {
  return scheduleRequest(async () => {
    const project = getProject();
    const res = await (project ? reach(path, { headers: { "X-Fuzer-Project": project } }) : reach(path));
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.text();
  }, `${getCsrf()}:${getProject()}:text:${path}`);
}

/** A file the API serves (a document's PDF or text), with its media type, through the same queue and project. */
export async function requestFile(path: string): Promise<{ type: string; bytes: ArrayBuffer }> {
  return scheduleRequest(() => keptRead(path, async () => {
    const project = getProject();
    const deadline = inPack(path) ? KEPT_READ_DEADLINE_MS : READ_DEADLINE_MS;
    const res = await reach(path, project ? { headers: { "X-Fuzer-Project": project } } : undefined, deadline);
    if (!res.ok) throw new Error(res.status === 404 ? "No file is loaded for this document." : `${res.status} ${res.statusText}`);
    return { type: res.headers.get("content-type") ?? "", bytes: await res.arrayBuffer() };
  }, true));
}
