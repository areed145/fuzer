import { getCsrf } from "../authState";
import { scheduleRequest } from "../requestQueue";

type Params = Record<string, string | number | undefined | null>;

export async function request<T>(method: string, path: string, params?: Params, body?: unknown): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const url = qs.size ? `${path}?${qs}` : path;
  const csrf = getCsrf();
  const readOnly = method === "GET" && !params?.claude && !path.endsWith("generate");
  return scheduleRequest(async () => {
    const res = await fetch(url, {
      method,
      headers: { ...(body ? { "Content-Type": "application/json" } : {}),
        ...(csrf ? { "X-Fuzer-CSRF": csrf } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      if (res.status === 401) window.dispatchEvent(new Event("fuzer:unauthorized"));
      const detail = await res.json().catch(() => null);
      throw Object.assign(new Error(detail?.detail ?? `${res.status} ${res.statusText}`), { body: detail });
    }
    return res.json();
  }, readOnly ? `${csrf}:${url}` : undefined);
}

export async function requestText(path: string): Promise<string> {
  return scheduleRequest(async () => {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.text();
  }, `${getCsrf()}:text:${path}`);
}
