// The tabs inside pages, one list each. The page that draws a row of tabs reads it from here, and so does app search,
// so a tab found by search is always a tab the page has.
import type { WorkList } from "./pages/AllWork";
import { href } from "./lib";

export interface PageTab { key: string; label: string }

/** All work's views (#502). The first is the default and carries no `list` in its link. */
export const WORK_LISTS: { key: WorkList; label: string }[] = [
  { key: "dispatch", label: "To dispatch" },
  { key: "field", label: "With the field" },
  { key: "back", label: "Back from the field" },
  { key: "open", label: "All open" },
  { key: "progress", label: "In progress" },
  { key: "ranked", label: "Ranked by value" },
  { key: "closed", label: "Closed" },
  { key: "board", label: "Board" },
];

/** A link to one view of All work, with that view's own filters. */
export const workHref = (list: WorkList, query?: Record<string, string | undefined>) =>
  href("/exceptions", { list: list === "dispatch" ? undefined : list, ...query });

/** Master data's tabs (`?tab=`). The first is the default. */
export const MASTER_DATA_TABS: PageTab[] = [
  { key: "unmatched", label: "Unmatched on open work" }, { key: "questions", label: "Questions" },
  { key: "unresolved", label: "Unresolved values" }, { key: "gaps", label: "Gaps" }, { key: "context", label: "Asset context" },
  { key: "proposals", label: "Claude's proposals" }, { key: "decisions", label: "Decisions" },
];

/** Sources' pages (`?view=`). The first is the default. */
export const SOURCES_VIEWS: PageTab[] = [
  { key: "datasets", label: "Datasets" }, { key: "systems", label: "Systems & connections" }, { key: "rules", label: "Rules" },
];

/** Application settings' sections (`?view=`). Readiness is the default and has no key. */
export const SETTINGS_VIEWS: PageTab[] = [
  { key: "", label: "Readiness" }, { key: "configuration", label: "All settings" }, { key: "detection", label: "Detection" },
  { key: "onboarding", label: "Onboarding" }, { key: "standards", label: "Standards" }, { key: "ai-usage", label: "AI usage" },
];
