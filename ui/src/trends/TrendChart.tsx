// The one chart over time: it draws a trend spec and knows nothing of compressors, cases or incidents. Colours come
// from the theme; every readability rule is in figure.ts.
import { useMemo } from "react";
import { PlotlyChart } from "../analytics/PlotlyChart";
import { useTheme } from "../theme";
import { buildFigure, type Palette } from "./figure";
import type { TimelineData, TimelineEvent, TrendSpec } from "./spec";

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export function palette(): Palette {
  const series = [1, 2, 3, 4, 5, 6].map((i) => css(`--chart-${i}`));
  return {
    series, text: css("--text"), muted: css("--muted"), limit: css("--warn"), danger: css("--danger"), shade: css("--warn-soft"),
    lanes: { alarms: css("--danger"), log: css("--muted"), cases: css("--accent"), maintenance: series[1], downtime: css("--warn") },
  };
}

/** Whether the chart is drawn for a phone: shorter panels, one scale a panel, keys as lists. */
export const isCompact = () => typeof matchMedia === "function" && matchMedia("(max-width: 640px)").matches;

export function TrendChart({ spec, timeline, on, pins = [], zoom = null, compact = isCompact(), label, onEvents, onZoom, onTime }: {
  spec: TrendSpec; timeline: TimelineData | null; on: Set<string>; pins?: string[]; zoom?: [string, string] | null;
  compact?: boolean; label: string;
  /** Told the events behind a clicked lane marker. */
  onEvents?: (events: TimelineEvent[]) => void;
  onZoom?: (zoomed: boolean, range?: [string, string]) => void;
  /** Told the time of a click that lands on no event: a page can pin it. */
  onTime?: (x: string) => void;
}) {
  const theme = useTheme();
  const figure = useMemo(() => {
    const f = buildFigure(spec, timeline, on, palette(), compact, pins);
    return zoom ? { ...f, layout: { ...f.layout, xaxis: { ...(f.layout as Record<string, any>).xaxis, range: zoom } } } : f;
  }, [spec, timeline, on, theme, compact, pins, zoom]);
  const point = useMemo(() => (data: unknown) => {
    if (onEvents && Array.isArray(data) && data[0] === "events") onEvents((data.slice(1) as number[]).map((i) => figure.events[i]).filter(Boolean));
  }, [figure, onEvents]);
  return <PlotlyChart spec={figure as never} onPoint={point} onZoom={onZoom} onTime={onTime} label={label} />;
}
