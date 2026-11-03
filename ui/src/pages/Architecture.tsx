import { api, type ArchComponent, type Architecture } from "../api";
import { Status, useAsync } from "../lib";

const SITE = "https://areed145.github.io/fuzer/";
// Data flows down: each row uses the rows above it (docs/architecture.md draws the same, from the imports).
const LAYERS = [["sources"], ["graph", "workflow"], ["analysis"], ["ops", "plots"], ["agents"], ["api", "evals", "cli"]];
const JOB_LABEL: Record<string, string> = {
  refresh_all: "Load every export", claude_reading: "Claude reads logs and drawings", work_tick: "Work timers",
  sre_scan: "SRE scan", studies_job: "Studies", day_close: "Daily report", opportunities: "Opportunity scan",
};

const n = (x: number | undefined) => (x ?? 0).toLocaleString();
const ago = (t: string | null) => (t ? t.slice(0, 16).replace("T", " ") : "never");

export function ArchitecturePage() {
  const a = useAsync(() => api.architecture(), []);
  if (!a.data) return <Status loading={a.loading} error={a.error} />;
  const d: Architecture = a.data;
  const by = Object.fromEntries(d.components.map((c) => [c.id, c]));
  return (
    <section>
      <h1>Architecture</h1>
      <p className="muted">
        The parts of Fuzer, from the source systems down to the app, with what each holds now. Data flows down the
        page; each part uses the ones above it, and they all use core. The <a href={`${SITE}architecture/`}>architecture
        docs</a> draw the same from the code.
      </p>
      <div className="arch-totals">
        <span><b>{n(d.rows.entity)}</b> entities</span>
        <span><b>{n(d.rows.edge)}</b> links</span>
        {Object.entries(d.sources).map(([s, k]) => <span key={s}><b>{k}</b> sources {s}</span>)}
      </div>
      <div className="arch">
        <div className="arch-row"><div className="arch-ext">Source systems: SAP, JDE, Maximo, PI, ADX, EC, P&amp;IDs, logs, documents</div></div>
        {LAYERS.map((row) => (
          <div className="arch-row" key={row.join()}>
            {row.filter((id) => by[id]).map((id) => <Card key={id} c={by[id]} rows={d.rows} />)}
          </div>
        ))}
        <div className="arch-row"><div className="arch-ext">The app, and Claude through MCP</div></div>
        <div className="arch-row">{by.core && <Card c={by.core} rows={d.rows} />}</div>
      </div>
      <div className="grid">
        <div className="card">
          <h2>Scheduled jobs</h2>
          <table className="list small">
            <thead><tr><th>Job</th><th>Last did something</th></tr></thead>
            <tbody>
              {Object.entries(d.jobs).map(([j, t]) => (
                <tr key={j}><td>{JOB_LABEL[j] ?? j}</td><td className={t ? "" : "muted"}>{ago(t)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h2>Entities by kind</h2>
          <table className="list small">
            <tbody>
              {d.entities.map((e) => <tr key={e.kind}><td>{e.kind}</td><td>{n(e.n)}</td></tr>)}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function Card({ c, rows }: { c: ArchComponent; rows: Record<string, number> }) {
  const uses = c.uses.filter((u) => u !== "core");
  return (
    <div className="card arch-card">
      <h3>{c.title} <span className="muted small">fuzer.{c.id}</span></h3>
      <p className="small">{c.purpose}</p>
      {uses.length > 0 && <p className="small muted">Uses {uses.join(", ")}</p>}
      {c.writes.length > 0 && (
        <p className="small">
          {c.writes.map((t) => (
            <span key={t} className="arch-table"><code>{t}</code> {n(rows[t])}</span>
          ))}
        </p>
      )}
      <p className="small">
        <a href={c.code} target="_blank" rel="noreferrer">Code</a> ({c.modules} modules) ·{" "}
        <a href={`${SITE}${c.page.replace(/\.md$/, "/")}`} target="_blank" rel="noreferrer">Docs</a>
      </p>
    </div>
  );
}
