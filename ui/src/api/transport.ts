import { getCsrf, getProject, switchProject } from "../authState";
import { scheduleRequest } from "../requestQueue";

type Params = Record<string, string | number | undefined | null>;

export async function request<T>(method: string, path: string, params?: Params, body?: unknown): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const url = qs.size ? `${path}?${qs}` : path;
  const csrf = getCsrf();
  const project = getProject();
  const readOnly = method === "GET" && !params?.claude && !path.endsWith("generate");
  return scheduleRequest(async () => {
    const res = await fetch(url, {
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
  }, readOnly ? `${csrf}:${project}:${url}` : undefined);
}

export async function requestText(path: string): Promise<string> {
  return scheduleRequest(async () => {
    const project = getProject();
    const res = await (project ? fetch(path, { headers: { "X-Fuzer-Project": project } }) : fetch(path));
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.text();
  }, `${getCsrf()}:${getProject()}:text:${path}`);
}

/** A file the API serves (a document's PDF or text), with its media type, through the same queue and project. */
export async function requestFile(path: string): Promise<{ type: string; bytes: ArrayBuffer }> {
  return scheduleRequest(async () => {
    const project = getProject();
    const res = await (project ? fetch(path, { headers: { "X-Fuzer-Project": project } }) : fetch(path));
    if (!res.ok) throw new Error(res.status === 404 ? "No file is loaded for this document." : `${res.status} ${res.statusText}`);
    return { type: res.headers.get("content-type") ?? "", bytes: await res.arrayBuffer() };
  });
}
