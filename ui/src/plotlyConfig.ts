/** Every Plotly chart's config. No floating toolbar on any chart: it sat over the plot and crowded it. Drag zooms,
 * a double-click resets, and a page that wants its own reset button listens for the zoom (PlotlyChart's onZoom).
 * `tests/plotly-config.test.cjs` fails on a chart drawn without it. */
export const PLOTLY_CONFIG = { responsive: true, displaylogo: false, displayModeBar: false } as const;
