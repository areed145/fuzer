export interface SignedInUser {
  id: string;
  actor: string;
  display_name: string;
  enabled: boolean;
  owner: boolean;
  roles: string[];
  permissions: string[];
  csrf: string;
  projects?: string[];
}

export interface AuthSession {
  mode: "local" | "microsoft";
  configured: boolean;
  user: SignedInUser | null;
  needs_owner?: boolean;
  /** The project this session's requests run for, and (local mode) the projects this instance holds. */
  project?: string | null;
  projects?: string[];
}

let csrf = "";
export const getCsrf = () => csrf;
export const setCsrf = (value: string) => { csrf = value; };

/** The project the person picked; requests carry it as X-Fuzer-Project and the server checks access to it.
 * Empty: the server's own project. Kept per browser so a reload stays in the same project. */
const PROJECT_KEY = "fuzer.project";
let project = "";
try {
  project = localStorage.getItem(PROJECT_KEY) ?? "";
} catch {
  console.warn("Fuzer project choice storage is unavailable; it lasts for this visit only.");
}
export const getProject = () => project;
/** A same-origin link the browser opens itself (no headers): it names the project in the query instead. */
export const projectUrl = (path: string) =>
  project ? `${path}${path.includes("?") ? "&" : "?"}fuzer_project=${encodeURIComponent(project)}` : path;

/** Switch project: every view, cache and session check starts again in the new one. */
export function switchProject(next: string) {
  project = next;
  try {
    if (next) localStorage.setItem(PROJECT_KEY, next);
    else localStorage.removeItem(PROJECT_KEY);
  } catch {
    console.warn("Fuzer project choice could not be saved in browser storage.");
  }
  window.location.hash = "#/";
  window.location.reload();
}
