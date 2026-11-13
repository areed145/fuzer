import { request } from "./transport";
import type { TimelineData, TrendViewData } from "../trendFigure";

export type { TimelineData, TimelineEvent, TimelineLane, TrendGroup, TrendTrace, TrendViewData } from "../trendFigure";

/** Historian views (P5.12, P5.13): an entity's trend grouped by its class's template, and its event lanes. */
export const historianApi = {
  trendView: (id: string, at?: string, hoursBefore?: number, hoursAfter?: number) =>
    request<TrendViewData>("GET", `/entities/${encodeURIComponent(id)}/trend-view`,
                           { at, hours_before: hoursBefore, hours_after: hoursAfter }),
  timeline: (id: string, start: string, end: string) =>
    request<TimelineData>("GET", `/entities/${encodeURIComponent(id)}/timeline`, { start, end }),
};
