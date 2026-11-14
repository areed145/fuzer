import { useEffect, useRef, useState } from "react";
import type { FigureData } from "../api";
import { themed } from "../Figure";
import { PLOTLY_CONFIG, fullscreenLayout, roomIn } from "../plotlyConfig";
import { useTheme } from "../theme";
import { FullscreenButton, useFullscreen } from "../ui";

type Spec = FigureData["spec"];

/** Bars with long labels run down the page below the width the figure asks for. */
function forWidth(spec: Spec, width: number): Spec {
  const below = spec.layout.meta?.horizontal_below;
  if (!below || width >= below) return spec;
  const { xaxis, yaxis, ...layout } = spec.layout;
  return {
    data: spec.data.map((trace) => ({ ...trace, orientation: "h", x: trace.y, y: trace.x })),
    layout: {
      ...layout, height: 180 + (spec.data[0]?.x?.length ?? 0) * 44, margin: { t: 8, r: 44, b: 20, l: 8 },
      legend: { ...layout.legend, x: 0, y: -0.14 }, xaxis: yaxis,
      // the names read level down the side: the column chart's slant doesn't carry over
      yaxis: { ...xaxis, title: { text: "" }, autorange: "reversed", tickangle: 0 },
    },
  };
}

/** A server-built figure in the page's theme: colours, fonts and colorway all come from `themed`. */
export function PlotlyChart({ spec, label, onPoint, onZoom, onTime }: {
  spec: Spec; label: string; onPoint?: (customdata: unknown) => void;
  /** Told whether the time axis is zoomed, and to what range, so a page can offer its own reset (no chart has the
   * toolbar) and read finer values for the range (P5.15e). */
  onZoom?: (zoomed: boolean, range?: [string, string]) => void;
  /** Told the time of a click that lands on no record: a page can pin it (P5.15d). */
  onTime?: (x: string) => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const full = useFullscreen();
  const theme = useTheme();
  const [error, setError] = useState("");
  useEffect(() => {
    const node = element.current;
    if (!node) return;
    let gone = false;
    let dispose: (() => void) | undefined;
    let purge: (() => void) | undefined;
    setError("");
    import("plotly.js-cartesian-dist-min").then(async ({ default: Plotly }) => {
      if (gone) return;
      purge = () => Plotly.purge(node);
      const draw = (available: number) => {
        const shaped = forWidth(spec, available);
        // On touch screens a drag scrolls the page; zooming stays a mouse gesture.
        const layout = themed(fullscreenLayout({ ...shaped.layout, ...(matchMedia("(pointer: coarse)").matches ? { dragmode: false } : {}) },
          full.on, roomIn(full.ref.current)));
        return Plotly.react(node, shaped.data, layout, PLOTLY_CONFIG);
      };
      let width = node.parentElement?.clientWidth ?? 0;
      await draw(width);
      if (gone) return;
      const events = node as HTMLDivElement & { on?: (ev: string, fn: (e: any) => void) => void };
      if (onPoint || onTime) {
        events.on?.("plotly_click", (e: any) => {
          const p = e?.points?.find((x: any) => x?.customdata) ?? e?.points?.[0];
          if (p?.customdata && onPoint) onPoint(p.customdata);
          else if (p?.x !== undefined && onTime) onTime(String(p.x));
        });
      }
      if (onZoom) {
        events.on?.("plotly_relayout", (e: any) => {
          const range = e?.["xaxis.range"] ?? (e?.["xaxis.range[0]"] !== undefined ? [e["xaxis.range[0]"], e["xaxis.range[1]"]] : null);
          if (range) onZoom(true, [String(range[0]), String(range[1])]);
          else if (e?.["xaxis.autorange"]) onZoom(false);
        });
      }
      const observer = new ResizeObserver(() => {
        const next = node.parentElement?.clientWidth ?? 0;
        if (!gone && width !== next) {
          width = next;
          draw(width).catch(() => {});
        }
      });
      observer.observe(node.parentElement ?? node);
      dispose = () => observer.disconnect();
    }).catch((failure: Error) => { if (!gone) setError(failure.message); });
    return () => { gone = true; dispose?.(); purge?.(); };
  }, [spec, theme, onPoint, onZoom, onTime, full.on]);
  return <>
    {error && <p className="error" role="alert">Chart unavailable: {error}. Use the data table below.</p>}
    <div ref={full.ref} className={`chart-frame${full.on ? " ui-fullscreen" : ""}`}>
      <div ref={element} style={{ minHeight: spec.layout.height ?? 340 }} role="img" aria-label={label} />
      <FullscreenButton on={full.on} onClick={() => void full.toggle()} className="chart-fullscreen" />
    </div>
  </>;
}
