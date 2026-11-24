import { request } from "./transport";
import type { NoteResult, SummaryResult, WindowKey } from "../reports/spec";

/** One point of a report's AI note, with what it rests on: measure keys, entity ids or "case:<n>". */
export interface ReportNotePoint { text: string; cites: string[] }

/** The checks the daily report runs, run on the AI note (agents/report_summary.check). */
export interface ReportNoteChecks { uncited_points: string[]; unknown_citations: string[]; unknown_numbers: string[]; ok: boolean }

/** A report's AI note for one scope, window and day, kept for everyone. */
export interface ReportSummary {
  report: string; scope: string | null; window: WindowKey; at: string;
  headline: string; points: ReportNotePoint[]; checks: ReportNoteChecks;
  written_at: string; by: string; model: string | null; cost_usd: number | null;
}

/** The `summary` panel's result. `due`: none kept, the default window, ending today, so a visit with AI access writes it. */
export interface SummaryPanelResult extends SummaryResult { summary: ReportSummary | null; can_write: boolean; due: boolean }

/** A live value a note names (`{production.oil}`), resolved by the server for the note's scope and period. */
export interface LiveValue {
  key: string; scope: string | null; value: number | null; unit?: string | null; label?: string | null;
  as_of?: string | null; note?: string | null; error: string | null;
}

/** A person's note, pinned to one report, scope and period. `own`: pinned to the period shown, not a shorter one inside. */
export interface PersonNote {
  id: number; report: string; scope: string | null; window: WindowKey; at: string; start: string; body: string;
  by: string; written_at: string; edited_at: string | null; own?: boolean; values?: Record<string, LiveValue>;
}

/** The `note` panel's result: fixed text from the definition with its values, or the people's notes. */
export interface NotePanelResult extends NoteResult { text: string | null; values: Record<string, LiveValue>; notes: PersonNote[] }

export type CommentaryResult = SummaryPanelResult | NotePanelResult;

interface Period { scope: string | null; window: WindowKey; at: string | null }

export const commentaryApi = {
  writeReportSummary: (key: string, p: Period, rewrite = false) =>
    request<{ summary: ReportSummary; written: boolean }>("POST", `/reports/${encodeURIComponent(key)}/summary/generate`,
      { ...p, rewrite: rewrite ? "true" : undefined }),
  addReportNote: (key: string, p: Period, body: string) =>
    request<PersonNote>("POST", `/reports/${encodeURIComponent(key)}/notes`, undefined, { ...p, body }),
  editReportNote: (key: string, id: number, body: string) =>
    request<PersonNote>("PATCH", `/reports/${encodeURIComponent(key)}/notes/${id}`, undefined, { body }),
  removeReportNote: (key: string, id: number) =>
    request<{ ok: boolean }>("DELETE", `/reports/${encodeURIComponent(key)}/notes/${id}`),
};
