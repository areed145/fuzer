// The tabs inside pages, one list each. The page that draws a row of tabs reads it from here, and so does app search,
// so a tab found by search is always a tab the page has.
import { href } from "./lib";

export interface PageTab { key: string; label: string }

/** The views All work had before it became one list (#502). Each is only a set of the list's filters now, so its old
 * links and saved views still open the same work. */
export type WorkList = "dispatch" | "field" | "back" | "open" | "progress" | "ranked" | "closed" | "board";
const VIEW_FILTERS: Record<WorkList, Record<string, string>> = {
  dispatch: { stage: "dispatch" }, field: { stage: "field" }, back: { stage: "back" }, open: {}, progress: {},
  ranked: {}, closed: { state: "closed" }, board: {},
};

/** The filters a link can carry, renamed from the old ones (`type` is the kind, `who` the holder). */
export function listFilters(query?: Record<string, string | undefined>): Record<string, string | undefined> {
  const { type, who, list: _list, view: _view, ...rest } = query ?? {};
  return { ...rest, ...(type ? { kind: type } : {}), ...(who ? { holder: who } : {}) };
}

/** What a saved view filters on: the old view's own filters, then the ones it was saved with. */
export const savedFilters = (list: string, filters: Record<string, string | undefined>): Record<string, string> =>
  Object.fromEntries(Object.entries({ ...VIEW_FILTERS[list as WorkList] ?? {}, ...listFilters(filters) })
    .filter(([, v]) => v)) as Record<string, string>;

/** A link to all the work as a List (or the Board), with an old view's filters and any others. */
export const workHref = (list: WorkList, query?: Record<string, string | undefined>) =>
  list === "ranked" ? href("/ranked", { view: query?.view })  // the backlog ranked by value, under Plan
    : href(list === "board" ? "/board" : "/list", { ...VIEW_FILTERS[list] ?? {}, ...listFilters(query) });

/** Presets: sets of filters people found worth a name (what waits on you is My work, its own page). Add one only once
 * it has proved its worth; anyone can save their own filters for a role as a saved view. */
export const WORK_PRESETS: { key: string; label: string; filters: Record<string, string> }[] = [
  { key: "dispatch", label: "To dispatch", filters: { stage: "dispatch" } },
  { key: "back", label: "Back from the field", filters: { stage: "back" } },
  { key: "overdue", label: "Overdue", filters: { overdue: "true" } },
  { key: "data", label: "Data questions", filters: { queue: "data_steward" } },
];

/** Master data's tabs (`?tab=`). The first is the default. */
export const MASTER_DATA_TABS: PageTab[] = [
  { key: "unmatched", label: "Unmatched on open work" }, { key: "questions", label: "Questions" },
  { key: "unresolved", label: "Unresolved values" }, { key: "gaps", label: "Gaps" }, { key: "context", label: "Asset context" },
  { key: "proposals", label: "Claude's proposals" }, { key: "decisions", label: "Decisions" },
];

/** Sources' pages (`?view=`). The first is the default. */
export const SOURCES_VIEWS: PageTab[] = [
  { key: "datasets", label: "Datasets" }, { key: "systems", label: "Systems & connections" }, { key: "rules", label: "Rules" },
  { key: "statuses", label: "Status mapping" },
];

/** Application settings' sections (`?view=`). Readiness is the default and has no key. */
export const SETTINGS_VIEWS: PageTab[] = [
  { key: "", label: "Readiness" }, { key: "configuration", label: "All settings" }, { key: "detection", label: "Detection" },
  { key: "onboarding", label: "Onboarding" }, { key: "standards", label: "Standards" }, { key: "ai-usage", label: "AI usage" },
];
