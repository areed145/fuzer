import "../../styles/sources.css";
import { Button, ButtonLink, PageHeader, Table, Input , Badge as UIBadge} from "../../ui";
import { usePermission } from "../../Auth";
/** The Sources page (#225): datasets, systems and connections, rules, a dataset's own page, and Add a source. */
import { useState } from "react";
import { api } from "../../api";
import { Status, href, useAsync } from "../../lib";
import { AddWizard } from "./AddWizard";
import { HealthBadge, RebuildBanner, STATE, scheduleText, when } from "./common";
import { DatasetPage, type Tab } from "./DatasetPage";
import { Rules } from "./Rules";
import { Systems } from "./Systems";

export function SourcesPage({ query }: { query: URLSearchParams }) {
  const configure = usePermission("configure");
  if (!configure) return <section><PageHeader>Sources</PageHeader>
    <p>Source configuration requires administrator access. Ask an owner to update your application roles.</p></section>;
  return <SourceConfiguration query={query} />;
}

function SourceConfiguration({ query }: { query: URLSearchParams }) {
  const view = query.get("view") ?? "datasets";
  const name = query.get("name");
  if (view === "add") {
    return (
      <section>
        <p className="small"><a href={href("/sources")}>Sources</a> › Add a source</p>
        <PageHeader>Add a source</PageHeader>
        <p className="muted">
          Five steps: the system the data comes from, where it is, which table, what its rows mean, and how it should load.
          Nothing is loaded until the last step.
        </p>
        <AddWizard system={query.get("system")} connection={query.get("connection")} table={query.get("table")} />
      </section>
    );
  }
  if ((view === "dataset" || view === "datasets") && name) {
    return <><RebuildBanner /><DatasetPage key={name} name={name} tab={(query.get("tab") as Tab) || "overview"} /></>;
  }
  return (
    <section>
      {/* Sources' own pages sit where every other area's secondary navigation does, above the heading (#394). */}
      <nav className="area-navigation" aria-label="Sources pages">
        {[["datasets", "Datasets"], ["systems", "Systems & connections"], ["rules", "Rules"]].map(([k, label]) => (
          <a key={k} href={href("/sources", { view: k })} aria-current={view === k ? "page" : undefined}>{label}</a>
        ))}
      </nav>
      <div className="row spread">
        <PageHeader>Sources</PageHeader>
        <ButtonLink variant="primary" href={href("/sources", { view: "add" })}>+ Add a source</ButtonLink>
      </div>
      <p className="muted">
        Every system of record, how Fuzer reaches it, and each dataset it delivers. Open a dataset for how it's doing and
        to change it. Every change is kept with who made it and why.
      </p>
      <RebuildBanner />
      {view === "datasets" && <Datasets />}
      {view === "systems" && <Systems />}
      {view === "rules" && <Rules />}
    </section>
  );
}

function Datasets() {
  const cat = useAsync(() => api.catalog(), []);
  const [filter, setFilter] = useState("");
  const [problems, setProblems] = useState(false);
  const [absent, setAbsent] = useState(false);
  const rows = (cat.data?.systems ?? []).flatMap((s) => s.datasets.map((d) => ({ s, d })));
  const here = rows.filter(({ d }) => d.present);
  const count = (st: string) => here.filter(({ d }) => d.health?.state === st).length;
  const connections = (cat.data?.systems ?? []).flatMap((s) => s.connections.map((c) => ({
    ...c, system: s.name, systemEnabled: s.enabled,
  })));
  const shown = (absent ? rows : here).filter(({ s, d }) =>
    (!filter || `${s.name} ${d.name} ${d.record_type ?? ""} ${d.owner ?? ""}`.toLowerCase().includes(filter.toLowerCase())) &&
    (!problems || (d.health?.problems.length ?? 0) > 0));
  return (
    <>
      <h2>Source readiness</h2>
      <p>Connect, test, ingest, monitor and recover here. Viewing Sources reads recorded state only;
        it does not test services, load data or call AI.</p>
      <Button disabled={cat.loading} onClick={cat.reload}>Refresh source readiness</Button>
      {cat.data && <p>{connections.length} connections · {rows.filter(({ d }) => d.loaded_at).length} datasets
        with a recorded load. A passed connection test is not a load or a freshness guarantee.</p>}
      <ol>
        <li><a href={href("/sources", { view: "systems" })}>Connect &amp; test</a>: manage endpoints and
          deployment-secret references. Deliberate tests contact the service, may read sample rows and save results.
          <details><summary>Recorded connection results</summary>
            {!connections.length && <p>No connections yet. Add a source to begin.</p>}
            <ul>{connections.map((c) => <li key={c.id}><strong>{c.system} / {c.name}</strong>: {
              !c.enabled || !c.systemEnabled ? "disabled" : Object.values(c.secrets).some((s) => !s.resolves)
                ? "missing secret reference" : !c.last_test ? "not tested" : c.last_test.ok ? "last test passed" : "last test failed"}
              {c.last_tested_at && <> · recorded {when(c.last_tested_at)}</>}</li>)}</ul>
          </details>
        </li>
        <li><a href={href("/sources", { view: "add" })}>Add and ingest a source</a>: follow connection,
          mapping and load steps. No data loads until the final explicit action.</li>
        <li>Monitor freshness and recover below: open a dataset for problems, checks, run history and retry/load actions.
          Source edits affect the next test/load; mappings require the indicated load/rebuild.</li>
        <li><a href={href("/jobs")}>Inspect scheduled loads &amp; recovery</a> if a load did not run.
          Jobs owns scheduler diagnostics and run failures; a deployment operator repairs missing infrastructure.</li>
      </ol>
      <p className="muted small">Application-wide defaults, AI limits and restart requirements belong in{" "}
        <a href={href("/settings")}>Settings</a>. Secret values stay in hosting.
        {" "}Human decisions on held data are in <a href={href("/review")}>Data review</a>; Sources remains the place to retry loads.</p>
      <Status loading={cat.loading && !cat.data} error={cat.error} empty={!!cat.data && rows.length === 0}>
        No datasets yet: <a href={href("/sources", { view: "add" })}>add a source</a>.
      </Status>
      {rows.length > 0 && (
        <>
          <div className="row wrap">
            <span className="small">
              {here.length} datasets:{" "}
              {["ok", "stale", "held", "failed", "checks failing", "profile drift", "not loaded"].map((st) =>
                count(st) ? <UIBadge key={st} className={`badge ${STATE[st] ?? ""}`}>{count(st)} {st}</UIBadge> : null)}
            </span>
            <Input placeholder="filter by name, system or owner" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <label className="small"><Input type="checkbox" checked={problems} onChange={(e) => setProblems(e.target.checked)} /> problems only</label>
            {rows.length > here.length && (
              <label className="small" title="datasets whose data isn't in this project's folders">
                <Input type="checkbox" checked={absent} onChange={(e) => setAbsent(e.target.checked)} /> show {rows.length - here.length} with no data here</label>
            )}
          </div>
          <Table variant="results">
            <thead>
              <tr><th>Dataset</th><th>System</th><th>Health</th><th>Last run</th><th>Owner</th><th>Schedule</th><th>Records</th><th>Last ingested (not event date)</th></tr>
            </thead>
            <tbody>
              {shown.map(({ s, d }) => (
                <tr key={d.name} className={d.present ? "static" : "static muted"}>
                  <td><a href={href("/sources", { view: "dataset", name: d.name })}><code>{d.name}</code></a> <span className="muted small">{d.record_type ?? d.reader}</span>
                    {d.status !== "active" && <UIBadge className="badge"> {d.status}</UIBadge>}</td>
                  <td><code>{s.name}</code></td>
                  <td>{d.present ? <HealthBadge h={d.health} /> : <span className="muted small">no data here</span>}</td>
                  <td>{d.last_run ? <UIBadge tone={d.last_run === "failed" ? "danger" : d.last_run === "held" ? "warn" : d.last_run === "done" ? "ok" : "muted"}>{d.last_run}</UIBadge> : "No recorded run"}</td>
                  <td className="small">{d.owner ?? <span className="muted">–</span>}</td>
                  <td className="muted small">{d.schedule ? scheduleText(d.schedule) : "daily"}</td>
                  <td>{d.rows?.toLocaleString() ?? "Unknown"}</td>
                  <td className="muted small" title={d.loaded_at ?? undefined}>{when(d.loaded_at)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
    </>
  );
}
