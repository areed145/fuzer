import "../../styles/sources.css";
import { ButtonLink, PageHeader, Table, Input , Badge as UIBadge} from "../../ui";
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
      <div className="row spread">
        <PageHeader>Sources</PageHeader>
        <ButtonLink variant="primary" href={href("/sources", { view: "add" })}>+ Add a source</ButtonLink>
      </div>
      <p className="muted">
        Every system of record, how Fuzer reaches it, and each dataset it delivers. Open a dataset for how it's doing and
        to change it. Every change is kept with who made it and why.
      </p>
      <RebuildBanner />
      <div className="tabs">
        {[["datasets", "Datasets"], ["systems", "Systems & connections"], ["rules", "Rules"]].map(([k, label]) => (
          <a key={k} className={view === k ? "on" : ""} href={href("/sources", { view: k })}>{label}</a>
        ))}
      </div>
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
  const shown = (absent ? rows : here).filter(({ s, d }) =>
    (!filter || `${s.name} ${d.name} ${d.record_type ?? ""} ${d.owner ?? ""}`.toLowerCase().includes(filter.toLowerCase())) &&
    (!problems || (d.health?.problems.length ?? 0) > 0));
  const open = (n: string) => (location.hash = href("/sources", { view: "dataset", name: n }));
  return (
    <>
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
              <tr><th>Dataset</th><th>System</th><th>Health</th><th>Last run</th><th>Owner</th><th>Schedule</th><th>Records</th><th>Loaded</th></tr>
            </thead>
            <tbody>
              {shown.map(({ s, d }) => (
                <tr key={d.name} onClick={() => open(d.name)} className={d.present ? "" : "muted"}>
                  <td><code>{d.name}</code> <span className="muted small">{d.record_type ?? d.reader}</span>
                    {d.status !== "active" && <UIBadge className="badge"> {d.status}</UIBadge>}</td>
                  <td><code>{s.name}</code></td>
                  <td>{d.present ? <HealthBadge h={d.health} /> : <span className="muted small">no data here</span>}</td>
                  <td>{d.last_run && <UIBadge className={`badge ${STATE[d.last_run] ?? ""}`}>{d.last_run}</UIBadge>}</td>
                  <td className="small">{d.owner ?? <span className="muted">–</span>}</td>
                  <td className="muted small">{d.schedule ? scheduleText(d.schedule) : "daily"}</td>
                  <td>{d.rows?.toLocaleString() ?? ""}</td>
                  <td className="muted small">{when(d.loaded_at)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
    </>
  );
}
