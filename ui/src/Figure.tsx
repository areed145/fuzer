// Interactive charts the assistant made (src/fuzer/plots): fetched by id and drawn with plotly.js,
// which loads only when a chart is on the page. The page's theme (light / dark) is applied here, so
// stored figures carry no colours of their own. Clicking a point opens the Fuzer record behind it.

import { useEffect, useRef, useState } from "react";
import { api, type FigureData } from "./api";
import { entityHref, Status, useAsync } from "./lib";
import { Button, Table , Card as UICard, Badge as UIBadge} from "./ui";
import { useTheme } from "./theme";

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** The figure's layout with the page's colours, fonts and chart palette. */
function themed(layout: Record<string, any>): Record<string, any> {
  const text = css("--text");
  const grid = css("--line");
  const axes = Object.fromEntries(
    Object.entries(layout)
      .filter(([k]) => /^[xy]axis\d*$/.test(k))
      .map(([k, v]) => [k, { gridcolor: grid, zerolinecolor: grid, linecolor: grid, automargin: true, ...v }]),
  );
  return {
    ...layout,
    ...axes,
    xaxis: { gridcolor: grid, zerolinecolor: grid, automargin: true, ...(layout.xaxis ?? {}) },
    yaxis: { gridcolor: grid, zerolinecolor: grid, automargin: true, ...(layout.yaxis ?? {}) },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: getComputedStyle(document.body).fontFamily, color: text, size: 12 },
    colorway: [1, 2, 3, 4, 5, 6].map((i) => css(`--chart-${i}`)),
    hoverlabel: { bgcolor: css("--panel"), bordercolor: grid, font: { color: text } },
    title: { ...(layout.title ?? {}), font: { size: 14 }, x: 0, xanchor: "left" },
    autosize: true,
  };
}

export function FigureView({ id }: { id: string }) {
  const f = useAsync(() => api.figure(id), [id]);
  const el = useRef<HTMLDivElement>(null);
  const [table, setTable] = useState(false);
  const theme = useTheme();
  const d = f.data;

  useEffect(() => {
    if (!d || !el.current) return;
    const node = el.current as HTMLDivElement & { on?: (ev: string, fn: (e: any) => void) => void };
    let gone = false;
    import("plotly.js-cartesian-dist-min").then(({ default: Plotly }) => {
      if (gone) return;
      Plotly.react(node, d.spec.data, themed(d.spec.layout), {
        responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"],
        toImageButtonOptions: { filename: `${d.kind}-${d.id}` },
      }).then(() => {
        node.on?.("plotly_click", (e: any) => {
          const p = e.points?.[0];
          const target = p?.customdata ?? p?.data?.meta?.entity;
          if (typeof target === "string" && target.startsWith("fz-")) window.location.href = entityHref(target);
        });
      });
    });
    return () => {
      gone = true;
      import("plotly.js-cartesian-dist-min").then(({ default: Plotly }) => Plotly.purge(node));
    };
  }, [d, theme]);

  return (
    <UICard className="figure" as="figure">
      <Status loading={f.loading && !d} error={f.error} />
      {d && (
        <>
          <div className="figure-plot" ref={el} style={{ height: d.spec.layout.height ?? 420 }}
               role="img" aria-label={d.title} />
          <figcaption className="row small muted">
            {d.by_source === "assistant" && <UIBadge className="badge">assistant-entered data</UIBadge>}
            <span className="spacer" />
            <Button variant="link" onClick={() => setTable((t) => !t)}>{table ? "Hide data" : "Data"}</Button>
            <a href={`/figures/${encodeURIComponent(d.id)}.html`} target="_blank" rel="noreferrer">Open full size</a>
          </figcaption>
          {table && <FigureTable d={d} />}
        </>
      )}
    </UICard>
  );
}

/** The plotted numbers as tables, one per trace (first 200 rows), for reading or copying. */
function FigureTable({ d }: { d: FigureData }) {
  return (
    <div className="figure-data">
      {d.spec.data.map((t: any, i: number) => {
        if (t.type === "heatmap") {
          return (
            <Table key={i} variant="list" density="compact">
              <thead><tr><th />{(t.x ?? []).map((x: string) => <th key={x}>{x}</th>)}</tr></thead>
              <tbody>{(t.z ?? []).map((row: number[], r: number) => (
                <tr key={r}><th>{t.y?.[r]}</th>{row.map((v, c) => <td key={c}>{v}</td>)}</tr>))}</tbody>
            </Table>
          );
        }
        const xs: unknown[] = t.x ?? [];
        const ys: unknown[] = t.y ?? [];
        const n = Math.min(200, Math.max(xs.length, ys.length));
        return (
          <details key={i} open={d.spec.data.length === 1}>
            <summary>{t.name || `series ${i + 1}`} <span className="muted">{Math.max(xs.length, ys.length)} points</span></summary>
            <Table variant="list" density="compact">
              <tbody>
                {Array.from({ length: n }, (_, k) => (
                  <tr key={k}><td className="mono">{String(xs[k] ?? k)}</td><td>{String(ys[k] ?? "")}</td></tr>
                ))}
              </tbody>
            </Table>
          </details>
        );
      })}
    </div>
  );
}
