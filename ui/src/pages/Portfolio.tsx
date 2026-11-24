/** The portfolio (steps 6 and 9 of the work model): every level work is grouped at, from the business plan down to a
 * package, one tree. A container shows its jobs by stage and its cases; its Plan tab reads the intent lines a planner
 * wrote against the cases that did the work, and lists the work no line covers. */
import { useState } from "react";
import { api, type PlanAgainstActual, type PlanLine, type SearchHit, type WorkContainer } from "../api";
import { money, whenDay } from "../format";
import { Badge, Button, Card, Empty, ErrorText, Facts, Field, Input, Line, PageHeader, Select, Stat, Stats, Table, Tabs, Text } from "../ui";
import { Status, errorMessage, href, useAction, useAsync } from "../lib";
import { usePerson } from "../work";
import { acronyms } from "../wording";

const KINDS: [string, string][] = [["business_plan", "Business plan"], ["program", "Program"], ["project", "Project"],
  ["turnaround", "Turnaround"], ["package", "Package"], ["campaign", "Campaign"]];
const kindWord = (k: string) => KINDS.find((x) => x[0] === k)?.[1] ?? k.replace(/_/g, " ");
const STAGES: [string, string][] = [["planned", "Planned"], ["scheduled", "Scheduled"], ["released", "Released"],
  ["in_progress", "In progress"], ["done", "Done"], ["cancelled", "Cancelled"]];
const hours = (h: number | null | undefined) => h == null ? "" : `${Math.round(h * 10) / 10} h`;
const window_ = (c: { start_at: string | null; finish_by: string | null }) =>
  c.start_at ? `${whenDay(c.start_at)}${c.finish_by ? ` to ${whenDay(c.finish_by)}` : ""}` : c.finish_by ? `by ${whenDay(c.finish_by)}` : "";

export function PortfolioPage({ query }: { query: URLSearchParams }) {
  const id = Number(query.get("container"));
  return id ? <ContainerPage id={id} tab={query.get("tab") ?? ""} /> : <Portfolio />;
}

/** Parents first, each child under it, so the tree reads top down. */
function ordered(rows: WorkContainer[]): { row: WorkContainer; depth: number }[] {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const out: { row: WorkContainer; depth: number }[] = [];
  const seen = new Set<number>();
  const visit = (r: WorkContainer, depth: number) => {
    if (seen.has(r.id)) return;
    seen.add(r.id);
    out.push({ row: r, depth });
    r.children.map((c) => byId.get(c)).filter((c): c is WorkContainer => !!c).forEach((c) => visit(c, depth + 1));
  };
  rows.filter((r) => !r.parent_id || !byId.has(r.parent_id)).forEach((r) => visit(r, 0));
  rows.forEach((r) => visit(r, 0));
  return out;
}

function JobStages({ jobs }: { jobs: Record<string, number> }) {
  const shown = STAGES.filter(([k]) => jobs[k]);
  return shown.length ? <Line wrap small>{shown.map(([k, label]) =>
    <Badge key={k} tone={k === "done" ? "ok" : k === "released" || k === "in_progress" ? "info" : "muted"}>{jobs[k]} {label.toLowerCase()}</Badge>)}</Line>
    : <Text small muted>No jobs</Text>;
}

function Portfolio() {
  const [person] = usePerson();
  const [kind, setKind] = useState("");
  const list = useAsync(() => api.containers(), []);
  const rows = Array.isArray(list.data) ? list.data : [];
  const shown = ordered(rows).filter(({ row }) => !kind || row.kind === kind);
  return (
    <section>
      <PageHeader>Portfolio</PageHeader>
      <Line wrap>
        <Field label="Kind" width="sm"><Select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">Every kind</option>{KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </Select></Field>
      </Line>
      <Status of={list} />
      {Array.isArray(list.data) && (shown.length ? <Table variant="list" className="phone-rows" aria-label="Portfolio">
        <thead><tr><th>Name</th><th>Kind</th><th>Window</th><th>Jobs</th><th>Cases</th></tr></thead>
        <tbody>{shown.map(({ row, depth }) => <tr key={row.id}>
          <td><span className="portfolio-depth" data-depth={Math.min(depth, 4)}>
            <a href={href("/plan", { portfolio: "1", container: String(row.id) })}><strong>{acronyms(row.title)}</strong></a></span></td>
          <td>{kindWord(row.kind)}</td>
          <td>{window_(row)}</td>
          <td><JobStages jobs={row.jobs} /></td>
          <td>{row.cases}</td>
        </tr>)}</tbody>
      </Table> : <Empty>Nothing here yet. Schedules, board projects and turnaround revisions appear on their own; add a business plan or program below.</Empty>)}
      {person ? <AddContainer person={person} rows={rows} onAdded={list.reload} />
        : <Text as="p" small>Choose who you are to add a business plan, program or project.</Text>}
    </section>
  );
}

function AddContainer({ person, rows, onAdded }: { person: string; rows: WorkContainer[]; onAdded: () => void }) {
  const { busy, error, run } = useAction();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("business_plan");
  const [title, setTitle] = useState("");
  const [parent, setParent] = useState("");
  const [owner, setOwner] = useState("");
  const [start, setStart] = useState("");
  const [finish, setFinish] = useState("");
  if (!open) return <Button variant="ghost" onClick={() => setOpen(true)}>Add a business plan, program or project</Button>;
  const add = () => run("Adding", () => api.containerCreate({ by: person, kind, title: title.trim(),
    parent: parent ? Number(parent) : null, owner: owner.trim() || null,
    start_at: start ? new Date(start).toISOString() : null, finish_by: finish ? new Date(finish).toISOString() : null }),
  (c) => { setOpen(false); setTitle(""); onAdded(); location.hash = href("/plan", { portfolio: "1", container: String(c.id) }); });
  return (
    <Card title="Add to the portfolio" actions={<Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Close</Button>}>
      <Line wrap>
        <Field label="Kind" width="sm"><Select value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
        <Field label="Name" width="md"><Input value={title} placeholder="2027 business plan" onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field label="Under" width="md"><Select value={parent} onChange={(e) => setParent(e.target.value)}>
          <option value="">Nothing (top level)</option>
          {rows.map((r) => <option key={r.id} value={r.id}>{kindWord(r.kind)}: {r.title}</option>)}</Select></Field>
      </Line>
      <Line wrap>
        <Field label="Owner" width="sm" hint="Holds its governing case"><Input value={owner} placeholder={person} onChange={(e) => setOwner(e.target.value)} /></Field>
        <Field label="Starts" width="sm"><Input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
        <Field label="Finishes by" width="sm"><Input type="date" value={finish} onChange={(e) => setFinish(e.target.value)} /></Field>
      </Line>
      <Button variant="primary" disabled={!!busy || !title.trim()} onClick={add}>{busy ?? "Add"}</Button>
      <ErrorText>{error}</ErrorText>
    </Card>
  );
}

function ContainerPage({ id, tab }: { id: number; tab: string }) {
  const c = useAsync(() => api.container(id), [id]);
  const d = c.data;
  const link = (t: string) => href("/plan", { portfolio: "1", container: String(id), tab: t || undefined });
  return (
    <section>
      <Text as="p" small><a href={href("/plan", { portfolio: "1" })}>Portfolio</a>
        {d?.parents.map((p) => <span key={p.id}> / <a href={href("/plan", { portfolio: "1", container: String(p.id) })}>{acronyms(p.title)}</a></span>)}</Text>
      <Status of={c} />
      {d && <>
        <PageHeader record detail={[kindWord(d.kind), window_(d), d.owner ? `owned by ${d.owner}` : ""].filter(Boolean).join(" · ")}>{acronyms(d.title)}</PageHeader>
        <Tabs label="Container views" items={[
          { key: "", label: "Work", href: link(""), current: tab !== "plan" },
          { key: "plan", label: "Plan against actual", href: link("plan"), current: tab === "plan" },
        ]} />
        {tab === "plan" ? <PlanTab id={id} /> : <>
          {d.item_id && <Text as="p" small>Its gates and decisions are on its <a href={href(`/cases/${d.item_id}`)}>governing case</a>.</Text>}
          {d.children.length > 0 && <Card title="Under it">
            <Table variant="list" className="phone-rows" aria-label="Under it"><thead><tr><th>Name</th><th>Kind</th></tr></thead>
              <tbody>{d.children.map((k) => <tr key={k.id}>
                <td><a href={href("/plan", { portfolio: "1", container: String(k.id) })}>{acronyms(k.title)}</a></td><td>{kindWord(k.kind)}</td>
              </tr>)}</tbody></Table></Card>}
          <Card title="Jobs">
            {d.jobs.length ? <Table variant="list" className="phone-rows" aria-label="Jobs">
              <thead><tr><th>Job</th><th>When</th><th>Crew</th><th>Stage</th></tr></thead>
              <tbody>{d.jobs.map((j) => <tr key={j.id}>
                <td><a href={href("/plan", { item: String(j.id), from: j.start_at.slice(0, 10) })}>{acronyms(j.title)}</a></td>
                <td>{whenDay(j.start_at)}</td><td>{j.crew ?? ""}</td>
                <td><Badge tone={j.stage === "done" ? "ok" : "muted"}>{STAGES.find((s) => s.at(0) === j.stage)?.[1] ?? j.stage}</Badge></td>
              </tr>)}</tbody></Table> : <Empty>No jobs in it yet.</Empty>}
          </Card>
          <Card title="Cases">
            {d.cases.length ? <Table variant="list" className="phone-rows" aria-label="Cases">
              <thead><tr><th>Case</th><th>State</th><th>With</th></tr></thead>
              <tbody>{d.cases.map((k) => <tr key={k.id}>
                <td><a href={href(`/cases/${k.id}`)}>{acronyms(k.title)}</a></td><td>{k.state}</td>
                <td>{k.assignee ?? (k.role ? k.role.replace(/_/g, " ") : "")}</td>
              </tr>)}</tbody></Table> : <Empty>No cases in it yet.</Empty>}
          </Card>
        </>}
      </>}
    </section>
  );
}

const STATE_TONE: Record<string, "ok" | "info" | "warn" | "muted"> = { done: "ok", "in progress": "info", "not started": "warn", dropped: "muted" };

function PlanTab({ id }: { id: number }) {
  const [person] = usePerson();
  const plan = useAsync(() => api.containerPlan(id), [id]);
  const { busy, error, run } = useAction();
  const p: PlanAgainstActual | undefined = plan.data;
  return (
    <>
      <Status of={plan} />
      {p && <>
        <Stats>
          <Stat label="Lines" value={p.totals.lines} detail={`${p.totals.done} done, ${p.totals.not_started} not started`} />
          <Stat label="Hours planned" value={hours(p.totals.estimate_hours) || "0 h"} />
          <Stat label="Hours done and booked" value={hours(p.totals.hours_done + p.totals.hours_booked) || "0 h"}
            detail={`${hours(p.totals.hours_done) || "0 h"} done`} />
          <Stat label="Unplanned cases" value={p.totals.unplanned_cases} tone={p.totals.unplanned_cases ? "danger" : undefined} />
        </Stats>
        {person && <Line wrap>
          <Button disabled={!!busy} onClick={() => run("Matching", () => api.containerPlanMatch(id, person), plan.reload)}>
            {busy === "Matching" ? "Matching" : "Match cases to lines"}</Button>
          <Text small muted>Cases whose jobs are on a line's assets, in its period, join that line.</Text>
        </Line>}
        <ErrorText>{error}</ErrorText>
        {p.lines.length ? <div className="plan-lines">{p.lines.map((l) =>
          <PlanLineCard key={l.id} line={l} person={person} unplanned={p.unplanned} onChange={plan.reload} />)}</div>
          : <Empty>No lines yet. Write what the plan means to do, where, when and for how much.</Empty>}
        {person ? <AddLine id={id} person={person} onAdded={plan.reload} /> : <Text as="p" small>Choose who you are to write the plan.</Text>}
        <Card title="Unplanned work">
          {p.unplanned.length ? <Table variant="list" className="phone-rows" aria-label="Unplanned work">
            <thead><tr><th>Case</th><th>State</th></tr></thead>
            <tbody>{p.unplanned.map((u) => <tr key={u.id}><td><a href={href(`/cases/${u.id}`)}>{acronyms(u.title)}</a></td><td>{u.state}</td></tr>)}</tbody>
          </Table> : <Empty>Every case here is on a line.</Empty>}
        </Card>
      </>}
    </>
  );
}

function PlanLineCard({ line, person, unplanned, onChange }: { line: PlanLine; person: string | null;
  unplanned: PlanAgainstActual["unplanned"]; onChange: () => void }) {
  const { busy, error, run } = useAction();
  const [pick, setPick] = useState("");
  const a = line.actual;
  const period = line.period_start || line.period_end
    ? `${line.period_start ? whenDay(line.period_start) : ""}${line.period_end ? ` to ${whenDay(line.period_end)}` : ""}` : "";
  const variance = line.hours_variance;
  return (
    <Card title={acronyms(line.title)} detail={[line.kind?.replace(/_/g, " "), period, line.owner].filter(Boolean).join(" · ")}
      actions={<Badge tone={STATE_TONE[line.state ?? "not started"]}>{line.state}</Badge>}>
      <Facts grid items={[
        ["Estimate", [hours(line.estimate_hours), line.estimate_cost != null ? money(line.estimate_cost) : ""].filter(Boolean).join(", ") || "None"],
        a && ["Done", `${hours(a.hours_done) || "0 h"} on ${a.jobs_done} of ${a.jobs} jobs`],
        a && a.hours_booked > 0 && ["Still booked", hours(a.hours_booked)],
        a && a.cost != null && ["Cost in SAP", money(a.cost)],
        variance != null && ["Against the estimate", variance > 0 ? `${hours(variance)} over` : variance < 0 ? `${hours(-variance)} under` : "On it"],
        ["Cases", a ? `${a.cases_closed} of ${a.cases} closed` : "None"],
      ]} />
      {line.case_ids && line.case_ids.length > 0 && <Line wrap small>{line.case_ids.map((c) => <span key={c}>
        <a href={href(`/cases/${c}`)}>Case {c}</a>
        {person && line.status === "open" && <Button size="sm" variant="link" disabled={!!busy} aria-label={`Take case ${c} off this line`}
          onClick={() => run("Taking off", () => api.planLineUnmatch(c, person), onChange)}>Take off</Button>}
      </span>)}</Line>}
      {person && line.status === "open" && <Line wrap>
        {unplanned.length > 0 && <>
          <Field label="Add a case" width="md"><Select value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">Pick an unplanned case</option>
            {unplanned.map((u) => <option key={u.id} value={u.id}>{u.title}</option>)}</Select></Field>
          <Button size="sm" disabled={!!busy || !pick} onClick={() => run("Adding", () => api.planLineMatch(line.id, person, Number(pick)), () => { setPick(""); onChange(); })}>Add</Button>
        </>}
        <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => run("Dropping", () => api.planLineUpdate(line.id, person, { status: "dropped" }), onChange)}>Drop this line</Button>
      </Line>}
      {person && line.status === "dropped" && <Button size="sm" variant="ghost" disabled={!!busy}
        onClick={() => run("Restoring", () => api.planLineUpdate(line.id, person, { status: "open" }), onChange)}>Put back on the plan</Button>}
      <ErrorText>{error}</ErrorText>
    </Card>
  );
}

function AddLine({ id, person, onAdded }: { id: number; person: string; onAdded: () => void }) {
  const { busy, error, run } = useAction();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState("");
  const [assets, setAssets] = useState<{ id: string; label: string }[]>([]);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [est, setEst] = useState("");
  const [cost, setCost] = useState("");
  if (!open) return <Button variant="ghost" onClick={() => setOpen(true)}>Add a line</Button>;
  const add = () => run("Adding", () => api.planLineAdd(id, { by: person, title: title.trim(), kind: kind || null,
    subjects: assets.map((x) => x.id), period_start: start || null, period_end: end || null,
    estimate_hours: est === "" ? null : Number(est), estimate_cost: cost === "" ? null : Number(cost) }),
  () => { setTitle(""); setAssets([]); setEst(""); setCost(""); setOpen(false); onAdded(); });
  return (
    <Card title="Add a line" actions={<Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Close</Button>}>
      <Line wrap>
        <Field label="What the plan means to do" width="md"><Input value={title} placeholder="Overhaul the export compressor" onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field label="Kind of job" width="sm"><Select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">Any</option>
          {["maintenance", "inspection", "well_intervention", "turnaround", "shutdown", "other"].map((k) => <option key={k} value={k}>{k.replace(/_/g, " ")}</option>)}
        </Select></Field>
      </Line>
      <AssetPick picked={assets} onChange={setAssets} />
      <Line wrap>
        <Field label="From" width="sm"><Input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
        <Field label="To" width="sm"><Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
        <Field label="Hours" width="xs"><Input type="number" min="0" step="any" value={est} onChange={(e) => setEst(e.target.value)} /></Field>
        <Field label="Cost" width="xs"><Input type="number" min="0" step="any" value={cost} onChange={(e) => setCost(e.target.value)} /></Field>
      </Line>
      <Button variant="primary" disabled={!!busy || !title.trim()} onClick={add}>{busy ?? "Add the line"}</Button>
      <ErrorText>{error}</ErrorText>
    </Card>
  );
}

/** The assets a line is about: search the register, add each one, take any off. */
function AssetPick({ picked, onChange }: { picked: { id: string; label: string }[]; onChange: (v: { id: string; label: string }[]) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const search = async () => {
    setError(null);
    try { setHits((await api.search(q.trim(), undefined, 8)).slice(0, 8)); } catch (e) { setError(errorMessage(e)); }
  };
  return (
    <div className="asset-pick">
      <Line wrap>
        <Field label="Assets it is about" width="md"><Input value={q} placeholder="A tag or name" onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void search(); } }} /></Field>
        <Button size="sm" disabled={!q.trim()} onClick={() => void search()}>Search</Button>
      </Line>
      {hits && (hits.length ? <Line wrap small>{hits.filter((h) => !picked.some((p) => p.id === h.fuzer_id)).map((h) =>
        <Button key={h.fuzer_id} size="sm" variant="ghost" onClick={() => onChange([...picked, { id: h.fuzer_id, label: h.label ?? h.fuzer_id }])}>
          + {h.label ?? h.fuzer_id}</Button>)}</Line> : <Text as="p" small>Nothing in the register matches that.</Text>)}
      {picked.length > 0 && <Line wrap small>{picked.map((p) => <Badge key={p.id} tone="info">{p.label}{" "}
        <Button size="sm" variant="link" aria-label={`Take ${p.label} off`} onClick={() => onChange(picked.filter((x) => x.id !== p.id))}>×</Button></Badge>)}</Line>}
      <ErrorText>{error}</ErrorText>
    </div>
  );
}
