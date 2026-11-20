import "../../styles/sources.css";
import { count as countText, when } from "../../format";
import { Button, ButtonLink, PageHeader, Table, Input, Badge as UIBadge, Line, Text, Checkbox } from "../../ui";
import { usePermission } from "../../Auth";
/** The Sources page (#225): datasets, systems and connections, rules, a dataset's own page, and Add a source. */
import { useState } from "react";
import { api } from "../../api";
import { Status, href, useAsync } from "../../lib";
import { AddWizard } from "./AddWizard";
import { HealthBadge, RebuildBanner, STATE, scheduleText } from "./common";
import { DatasetPage, type Tab } from "./DatasetPage";
import { Rules } from "./Rules";
import { Systems } from "./Systems";
import { SOURCES_VIEWS } from "../../pageTabs";

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
        <Text as="p" small><a href={href("/sources")}>Sources</a> › Add a source</Text>
        <PageHeader>Add a source</PageHeader>
        <AddWizard system={query.get("system")} connection={query.get("connection")} table={query.get("table")} />
      </section>
    );
  }
  if ((view === "dataset" || view === "datasets") && name) {
    return <><RebuildBanner /><DatasetPage key={name} name={name} tab={(query.get("tab") as Tab) || "overview"} /></>;
  }
  return (
    <section>
      <Line spread>
        <PageHeader>Sources</PageHeader>
        <ButtonLink variant="primary" href={href("/sources", { view: "add" })}>+ Add a source</ButtonLink>
      </Line>
      {/* Sources' own pages are section tabs under the title, as on Settings. */}
      <nav className="area-navigation" aria-label="Sources pages">
        {SOURCES_VIEWS.map(({ key: k, label }) => (
          <a key={k} href={href("/sources", { view: k })} aria-current={view === k ? "page" : undefined}>{label}</a>
        ))}
      </nav>
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
      <Button disabled={cat.loading} onClick={cat.reload}>Refresh source readiness</Button>
      {cat.data && <p>{connections.length} connections · {rows.filter(({ d }) => d.loaded_at).length} datasets
        with a recorded load.</p>}
      <Status of={cat} empty={!!cat.data && rows.length === 0}>
        No datasets yet: <a href={href("/sources", { view: "add" })}>add a source</a>.
      </Status>
      {rows.length > 0 && (
        <>
          <Line wrap>
            <Text small>
              {here.length} datasets:{" "}
              {["ok", "stale", "held", "failed", "checks failing", "profile drift", "not loaded"].map((st) =>
                count(st) ? <UIBadge key={st} tone={STATE[st]}>{count(st)} {st}</UIBadge> : null)}
            </Text>
            <Input placeholder="filter by name, system or owner" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <Checkbox checked={problems} onChange={setProblems}>problems only</Checkbox>
            {rows.length > here.length && (
              <label className="small" title="datasets whose data isn't in this project's folders">
                <Input type="checkbox" checked={absent} onChange={(e) => setAbsent(e.target.checked)} /> show {rows.length - here.length} with no data here</label>
            )}
          </Line>
          <Table variant="results">
            <thead>
              <tr><th>Dataset</th><th>System</th><th>Health</th><th>Last run</th><th>Owner</th><th>Schedule</th><th>Records</th><th>Last ingested (not event date)</th></tr>
            </thead>
            <tbody>
              {shown.map(({ s, d }) => (
                <tr key={d.name} className={d.present ? "static" : "static muted"}>
                  <td><a href={href("/sources", { view: "dataset", name: d.name })}><code>{d.name}</code></a> <Text small muted>{d.record_type ?? d.reader}</Text>
                    {d.status !== "active" && <UIBadge> {d.status}</UIBadge>}</td>
                  <td><code>{s.name}</code></td>
                  <td>{d.present ? <HealthBadge h={d.health} /> : <Text small muted>no data here</Text>}</td>
                  <td>{d.last_run ? <UIBadge tone={d.last_run === "failed" ? "danger" : d.last_run === "held" ? "warn" : d.last_run === "done" ? "ok" : "muted"}>{d.last_run}</UIBadge> : "No recorded run"}</td>
                  <td className="small">{d.owner ?? <Text muted>–</Text>}</td>
                  <td className="muted small">{d.schedule ? scheduleText(d.schedule) : "daily"}</td>
                  <td>{d.rows != null ? countText(d.rows) : "Unknown"}</td>
                  <td className="muted small" title={d.loaded_at ?? undefined}>{when(d.loaded_at, "Time unknown")}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
    </>
  );
}
