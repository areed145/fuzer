/** Every Plotly chart's config. No floating toolbar on any chart: it sat over the plot and crowded it. Drag zooms,
 * a double-click resets, and a page that wants its own reset button listens for the zoom (PlotlyChart's onZoom).
 * `tests/plotly-config.test.cjs` fails on a chart drawn without it, or without its full screen button. */
export const PLOTLY_CONFIG = { responsive: true, displaylogo: false, displayModeBar: false } as const;

/** Every chart opens on the whole screen from its own button (FullscreenButton, useFullscreen in the UI kit), in
 * place of the toolbar's. There it takes the screen's height, and on a touch screen a drag zooms, since nothing
 * underneath needs to scroll. `height` is the room the full screen leaves. */
export function fullscreenLayout(layout: Record<string, any>, on: boolean, height: number): Record<string, any> {
  return on ? { ...layout, height: Math.max(height, 240), dragmode: "zoom" } : layout;
}

/** The height inside an element's padding: the room a chart has on the full screen. */
export function roomIn(el: HTMLElement | null): number {
  if (!el) return 0;
  const css = getComputedStyle(el);
  return el.clientHeight - parseFloat(css.paddingTop) - parseFloat(css.paddingBottom);
}
