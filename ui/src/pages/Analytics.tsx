import { useEffect, useRef, useState } from "react";
import { api, type ProductionEfficiency } from "../api";
import { themed } from "../Figure";
import { Status, useAsync } from "../lib";
import { useTheme } from "../theme";
import { Button, Card, PageHeader, Table } from "../ui";
import {
  buildDashboard, constraintFor, dailyPoints, defaultSpec, loadSpec, parseSpec, queryVariables,
  STORAGE_KEY, type DashboardSpec,
} from "../analytics/dashboard";
import "../analytics/analytics.css";

const dataSources = { "production-efficiency": api.productionEfficiency };
function initialDashboard() {
  try { return loadSpec(window.localStorage); }
  catch { return { spec: defaultSpec(), error: "Browser storage is unavailable; dashboard changes will only apply in this tab." }; }
}
const number = (value: number | null) => value === null ? "Unknown" : value.toLocaleString(undefined, { maximumFractionDigits: 1 });

export function AnalyticsPage({ query }: { query: URLSearchParams }) {
  const [initial] = useState(initialDashboard);
  const [spec, setSpec] = useState(initial.spec);
  const [json, setJson] = useState(() => JSON.stringify(initial.spec, null, 2));
  const [notice, setNotice] = useState(initial.error ?? "");
  const [editorError, setEditorError] = useState("");
  const [controls, setControls] = useState({ end: query.get("end") ?? "", facility: query.get("ref") ?? "" });
  const [variables, setVariables] = useState(controls);
  const [controlError, setControlError] = useState("");
  const [table, setTable] = useState(false);
  useEffect(() => {
    const next = { end: query.get("end") ?? "", facility: query.get("ref") ?? "" };
    setControls(next); setVariables(next); setControlError("");
  }, [query.get("end"), query.get("ref")]);
  const result = useAsync(async () => {
    const { end, ref } = queryVariables(spec, variables);
    return dataSources[spec.dataSource](end, ref);
  }, [variables.end, variables.facility, spec.dataSource]);
  const d = result.data;
  const production = spec.panels.find((p) => p.visualization === "time-series");
  const apply = (save: boolean) => {
    try {
      const next = parseSpec(json);
      if (save) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      setSpec(next); setJson(JSON.stringify(next, null, 2)); setEditorError("");
      setNotice(save ? "Dashboard saved in this browser." : "Dashboard applied for this tab.");
    } catch (error) { setEditorError((error as Error).message); }
  };
  const reset = () => {
    const next = defaultSpec();
    setSpec(next); setJson(JSON.stringify(next, null, 2)); setEditorError("");
    try { window.localStorage.removeItem(STORAGE_KEY); setNotice("Default dashboard restored."); }
    catch { setNotice("Defaults restored for this tab; browser storage is unavailable."); }
  };
  return (
    <section className="analytics-page">
      <PageHeader>Analytics</PageHeader>
      <div className="row wrap"><h2>{spec.title}</h2><span className="badge">Daily · last 14 days</span></div>
      <p className="muted">Allocated oil against the daily dynamic maximum, with the binding constraints behind capacity. Unknown values are gaps, never zero.</p>
      <form className="analytics-controls row wrap" onSubmit={(event) => {
        event.preventDefault();
        try { queryVariables(spec, controls); setVariables({ ...controls }); setControlError(""); }
        catch (error) { setControlError((error as Error).message); }
      }}>
        <label>End date (inclusive)<input type="date" value={controls.end} onChange={(event) => setControls({ ...controls, end: event.target.value })} /></label>
        <label>Facility ref (optional)<input type="text" placeholder="Default facility" value={controls.facility} onChange={(event) => setControls({ ...controls, facility: event.target.value })} /></label>
        <Button type="submit">Update</Button>
        <Button type="button" variant="secondary" onClick={() => {
          const next = { end: "", facility: controls.facility };
          setControls(next); setVariables(next); setControlError("");
        }}>Latest report</Button>
      </form>
      <p className="small muted">Leave the end date blank to use this facility's latest production report.</p>
      {controlError && <p role="alert" className="error">{controlError}</p>}
      <Status loading={result.loading && !d} error={result.error} />
      {result.error && <Button onClick={result.reload}>Retry</Button>}
      {d && <>
        <p className="small muted">{d.facility.label} · {dailyPoints(d)[0].date} – {d.end} inclusive · Source: {d.source}{result.loading ? " · Updating…" : ""}</p>
        <div className="analytics-summary">
          <Card><span className="small muted">Weighted efficiency</span><strong>{number(d.summary.efficiency_pct)}{d.summary.efficiency_pct === null ? "" : "%"}</strong><span className="small muted">Allocated / maximum over paired days</span></Card>
          <Card><span className="small muted">Allocated · paired days</span><strong>{number(d.summary.allocated_sm3)} <small>Sm3</small></strong></Card>
          <Card><span className="small muted">Dynamic maximum · paired days</span><strong>{number(d.summary.dynamic_max_sm3)} <small>Sm3</small></strong><span className="small muted">{d.summary.paired_days} / 14 paired days</span></Card>
        </div>
        {d.warnings.length > 0 && <div className="analytics-warnings" role="status"><strong>Data notes</strong><ul>{d.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul></div>}
        {d.summary.paired_days === 0 && <p role="status">No paired production and capacity values in this window. Available values and unknown days are shown below.</p>}
        <Card className="analytics-chart">
          <DashboardPlot spec={spec} response={d} />
          <p className="small muted">Shared date axis: hover for daily details; drag to zoom both panels; double-click to reset. Click legend items to toggle series. Ties show every binding constraint.</p>
          <p className="small muted">Reported potential is a capacity proxy: the physical binding constraint remains unknown. Efficiency above 100% is shown as reported, not clipped.</p>
          {production?.visualization === "time-series" && production.thresholds && <p className="small muted">
            Allocation marker efficiency: <span style={{ color: `var(--${production.thresholds.colors.good})` }}>≥ {production.thresholds.good}%</span> · <span style={{ color: `var(--${production.thresholds.colors.warning})` }}>≥ {production.thresholds.warning}% and &lt; {production.thresholds.good}%</span> · <span style={{ color: `var(--${production.thresholds.colors.low})` }}>&lt; {production.thresholds.warning}%</span> · <span style={{ color: `var(--${production.thresholds.colors.unknown})` }}>unknown</span>.
          </p>}
          <Button variant="link" onClick={() => setTable(!table)}>{table ? "Hide daily data" : "Show daily data"}</Button>
          {table && <div className="analytics-table"><Table variant="list" density="compact">
            <thead><tr><th>Day</th><th>Maximum (Sm3/d)</th><th>Allocated (Sm3/d)</th><th>Gap (Sm3/d)</th><th>Efficiency (%)</th><th>Binding constraints</th><th>Capacity basis / flags</th></tr></thead>
            <tbody>{dailyPoints(d).map((p) => <tr key={p.date}>
              <td>{p.date}</td><td>{number(p.dynamic_max_sm3d)}</td><td>{number(p.allocated_sm3d)}</td><td>{number(p.gap_sm3d)}</td><td>{number(p.efficiency_pct)}</td>
              <td>{constraintFor(p, d).label}</td><td>{p.capacity_basis.replaceAll("_", " ")}{p.flags.length ? ` · ${p.flags.join(", ")}` : ""}</td>
            </tr>)}</tbody>
          </Table></div>}
        </Card>
      </>}
      <details className="analytics-editor">
        <summary>Dashboard JSON · customize & save</summary>
        <p className="small muted">Versioned spec with two registered renderers and one whitelisted data source. Use $end and $facility query variables. Apply previews changes; Save persists them in this browser. Paste JSON or import a file.</p>
        <label>Import dashboard JSON<input type="file" accept=".json,application/json" onChange={async (event) => {
          const file = event.target.files?.[0];
          if (!file) return;
          try {
            if (file.size > 32_768) throw Error("Dashboard JSON must be at most 32 KB");
            const next = parseSpec(await file.text());
            setJson(JSON.stringify(next, null, 2)); setEditorError(""); setNotice("Imported JSON is ready to apply.");
          } catch (error) { setEditorError((error as Error).message); }
          event.target.value = "";
        }} /></label>
        <label>Dashboard specification<textarea spellCheck={false} rows={18} value={json} onChange={(event) => setJson(event.target.value)} /></label>
        {editorError && <p className="error" role="alert">{editorError}</p>}
        <div className="row wrap">
          <Button onClick={() => apply(false)}>Apply JSON</Button>
          <Button onClick={() => apply(true)}>Save dashboard</Button>
          <Button variant="secondary" onClick={reset}>Reset dashboard</Button>
        </div>
      </details>
      {notice && <p className="small muted" role="status">{notice}</p>}
    </section>
  );
}

function DashboardPlot({ spec, response }: { spec: DashboardSpec; response: ProductionEfficiency }) {
  const element = useRef<HTMLDivElement>(null);
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
      const chart = buildDashboard(spec, response, (token) => getComputedStyle(document.documentElement).getPropertyValue(`--${token}`).trim());
      const config = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] };
      await Plotly.react(node, chart.data, themed(chart.layout), config);
      if (gone) return;
      let width = node.parentElement?.clientWidth;
      const observer = new ResizeObserver(() => {
        const next = node.parentElement?.clientWidth;
        if (!gone && width !== next) {
          width = next;
          Plotly.react(node, chart.data, themed(chart.layout), config).catch(() => {});
        }
      });
      observer.observe(node.parentElement ?? node);
      dispose = () => observer.disconnect();
    }).catch((error: Error) => { if (!gone) setError(error.message); });
    return () => { gone = true; dispose?.(); purge?.(); };
  }, [spec, response, theme]);
  return <>
    {error && <p className="error" role="alert">Chart unavailable: {error}. Use the daily data table below.</p>}
    <div ref={element} style={{ minHeight: spec.layout.height }} role="img" aria-label={`${spec.title}: shared production and constraint daily charts`} />
  </>;
}
