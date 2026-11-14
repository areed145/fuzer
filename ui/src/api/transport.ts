import { getCsrf, getProject, switchProject } from "../authState";
import { scheduleRequest } from "../requestQueue";
import { lost, reached } from "../signal";
import { keepAnswer, keptAnswer } from "../fieldPack";

/** A fetch that tells the signal (P5.17b) whether the server was reached. */
async function reach(input: string, init?: RequestInit): Promise<Response> {
  try {
    const res = await fetch(input, init);
    reached();
    return res;
  } catch (e) {
    if (e instanceof TypeError) lost();
    throw e;
  }
}

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
  const run = async (): Promise<T> => {
    const res = await reach(url, {
      method,
      headers: { ...(body ? { "Content-Type": "application/json" } : {}),
        ...(csrf ? { "X-Fuzer-CSRF": csrf } : {}),
        ...(project ? { "X-Fuzer-Project": project } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
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
    const res = await (project ? reach(path, { headers: { "X-Fuzer-Project": project } }) : reach(path));
    if (!res.ok) throw new Error(res.status === 404 ? "No file is loaded for this document." : `${res.status} ${res.statusText}`);
    return { type: res.headers.get("content-type") ?? "", bytes: await res.arrayBuffer() };
  }, true));
}
