import { request } from "./transport";
import type { TimelineData, TrendViewData } from "../trendFigure";
import type { TrendSpec } from "../trends/spec";

export type { TimelineData, TimelineEvent, TimelineLane, TrendGroup, TrendTrace, TrendViewData } from "../trendFigure";
export type { TrendMark, TrendPanel, TrendSeries, TrendSpec } from "../trends/spec";

/** Historian views (P5.12, P5.13): an entity's trend grouped by its class's template, and its event lanes. */
export const historianApi = {
  trendView: (id: string, at?: string, hoursBefore?: number, hoursAfter?: number) =>
    request<TrendViewData>("GET", `/entities/${encodeURIComponent(id)}/trend-view`,
                           { at, hours_before: hoursBefore, hours_after: hoursAfter }),
  /** The trend as a spec, the one config every chart over time draws; `focus` opens it on those series, and `caseId` on
   * what the case's event was seen on, with the event marked; `compare` ("7d", "incidents") adds earlier windows. */
  trendSpec: (id: string, at?: string, hoursBefore?: number, hoursAfter?: number, focus?: string[], caseId?: number,
              compare?: string) =>
    request<TrendSpec>("GET", `/entities/${encodeURIComponent(id)}/trend-spec`,
                       { at, hours_before: hoursBefore, hours_after: hoursAfter, focus: focus?.length ? focus.join(",") : undefined,
                         case: caseId, compare }),
  /** The entity's landed readings over their whole history, as a spec: a panel per quantity and location. */
  measurementsSpec: (id: string) => request<TrendSpec>("GET", `/entities/${encodeURIComponent(id)}/measurements/spec`),
  timeline: (id: string, start: string, end: string) =>
    request<TimelineData>("GET", `/entities/${encodeURIComponent(id)}/timeline`, { start, end }),
};
