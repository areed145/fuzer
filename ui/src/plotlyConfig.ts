/** Every Plotly chart's config. No floating toolbar on any chart: it sat over the plot and crowded it. Drag zooms,
 * a double-click resets, and a page that wants its own reset button listens for the zoom (PlotlyChart's onZoom).
 * `tests/plotly-config.test.cjs` fails on a chart drawn without it, or without its full screen button. */
export const PLOTLY_CONFIG = { responsive: true, displaylogo: false, displayModeBar: false } as const;

/** Every chart opens on the whole screen from its own button (FullscreenButton, useFullscreen in the UI kit), in
 * place of the toolbar's. There it takes the screen's height, and on a touch screen a drag zooms, since nothing
 * underneath needs to scroll. `height` is the room the full screen leaves. A chart taller than that (a timeline of
 * stacked bands on a phone) keeps its own height and the full screen scrolls: squeezed into one screen its band
 * titles sat on the traces and its axis numbers overlapped (#1076). A drag then scrolls, as on the page. */
export function fullscreenLayout(layout: Record<string, any>, on: boolean, height: number): Record<string, any> {
  if (!on) return layout;
  const room = Math.max(height, 240);
  return typeof layout.height === "number" && layout.height > room ? layout : { ...layout, height: room, dragmode: "zoom" };
}

/** The height inside an element's padding: the room a chart has on the full screen. */
export function roomIn(el: HTMLElement | null): number {
  if (!el) return 0;
  const css = getComputedStyle(el);
  return el.clientHeight - parseFloat(css.paddingTop) - parseFloat(css.paddingBottom);
}
