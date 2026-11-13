/** How the work is running (P7.2b, #661): one compact view, reached from All work. Per queue, whether it is keeping
 * up this week against the last four, and the measures behind that; every number opens the cases behind it. */
import { useState } from "react";
import { api, type DetectorEarning, type FindingOrigin, type PerfCell, type PerfRow } from "../api";
import { Badge, Button, Card, Empty, PageHeader, Table, Tabs } from "../ui";
import { Status, href, useAsync } from "../lib";
import { when } from "../work";

const VERDICT: Record<PerfRow["keeping_up"], { text: string; tone: string }> = {
  keeping_up: { text: "keeping up", tone: "ok" },
  holding: { text: "holding", tone: "" },
  falling_behind: { text: "falling behind", tone: "late" },
};
const COUNTS: [keyof PerfCell, string][] = [["sla_misses", "SLA misses"], ["escalations", "Escalations"],
  ["hand_backs", "Hand-backs"], ["repeats", "Repeats"]];

export function PerformancePage({ query }: { query: URLSearchParams }) {
  const data = useAsync(() => api.workPerformance(), []);
  const by = query.get("by") === "type" ? "type" : query.get("by") === "detector" ? "detector" : "queue";
  const [open, setOpen] = useState<{ label: string; ids: number[] } | null>(null);
  const d = data.data;
  const rows: PerfRow[] = d ? (by === "type" ? d.types : d.queues) : [];
  const n = (label: string, cell: { n: number; ids: number[] }) => cell.n
    ? <Button variant="link" className="perf-n" onClick={() => setOpen({ label, ids: cell.ids })}>{cell.n}</Button>
    : <span className="muted">0</span>;
  return (
    <section>
      <PageHeader subtitle="Is each queue keeping up this week, against the last four?" actions={<a href={href("/exceptions")}>All work</a>}>
        Work performance</PageHeader>
      <Tabs label="Measure by" items={[
        { key: "queue", label: "By queue", href: href("/performance"), current: by === "queue" },
        { key: "type", label: "By work type", href: href("/performance", { by: "type" }), current: by === "type" },
        { key: "detector", label: "By detector", href: href("/performance", { by: "detector" }), current: by === "detector" },
      ]} />
      {by === "detector" ? <Detectors /> : <>
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && (rows.length ? (
        <Table density="compact" className="perf-table phone-rows">
          <thead><tr>
            <th>{by === "type" ? "Work type" : "Queue"}</th><th>This week</th><th>Arrived</th><th>Closed</th><th>Open</th>
            <th>Hours to triage, dispatch, close</th>{COUNTS.map(([, l]) => <th key={l}>{l}</th>)}
          </tr></thead>
          <tbody>{rows.map((r) => {
            const name = (r.queue ?? r.type ?? "").replace(/_/g, " ");
            const w = r.weeks[r.weeks.length - 1];
            return (
              <tr key={name}>
                <td><strong>{name}</strong></td>
                <td><Badge className={`badge ${VERDICT[r.keeping_up].tone}`}>{VERDICT[r.keeping_up].text}</Badge></td>
                <td>{n(`${name}: arrived this week`, w.arrivals)} <span className="small muted">avg {r.arrivals_avg4}</span></td>
                <td>{n(`${name}: closed this week`, w.closed)} <span className="small muted">avg {r.closed_avg4}</span></td>
                <td>{n(`${name}: open now`, w.backlog)} <span className="small muted">avg {r.backlog_avg4}</span>
                  <div className="small muted" title="open at the end of each of the last five weeks, oldest first">
                    {r.weeks.map((c) => c.backlog.n).join(" · ")}</div></td>
                <td className="small">{[w.triage_h, w.dispatch_h, w.close_h].map((h) => (h == null ? "–" : h)).join(" · ")}</td>
                {COUNTS.map(([k, l]) => <td key={k} data-label={l}>{n(`${name}: ${l.toLowerCase()} this week`, w[k] as { n: number; ids: number[] })}</td>)}
              </tr>
            );
          })}</tbody>
        </Table>
      ) : <Empty>No work in the last five weeks.</Empty>)}
      {open && <Card className="perf-cases" title={open.label}
        actions={<Button variant="link" onClick={() => setOpen(null)}>Close</Button>}>
        <ul className="plain small">{open.ids.map((id) => <li key={id}><a href={href(`/cases/${id}`)}>case {id}</a></li>)}</ul>
      </Card>}
      {d?.compliance && <Card title="Schedule compliance">
        <ul className="plain small">{Object.entries(d.compliance.summary).map(([kind, s]) => (
          <li key={kind}><strong>{kind}</strong> {s.on_time_pct}% on time · {s.overdue} of {s.plans} plans overdue</li>
        ))}</ul>
        <p className="small muted">Maintenance and inspection plans as of {d.compliance.as_of}.</p>
      </Card>}
      {d?.rounds && d.rounds.length > 0 && <Card title="Operator rounds per shift">
        <Table density="compact" className="phone-rows">
          <thead><tr><th>Shift</th><th>Done</th><th>Partly done</th><th>Missed</th><th>Checks not taken</th><th>Found something</th></tr></thead>
          <tbody>{d.rounds.map((r) => <tr key={r.shift_start}>
            <td>{when(r.shift_start)}{r.open ? <span className="small muted"> · {r.open} under way</span> : null}</td>
            <td data-label="Done">{r.done} of {r.rounds}</td>
            <td data-label="Partly done">{r.partly_done}</td>
            <td data-label="Missed">{r.missed ? <Badge tone="warn">{r.missed}</Badge> : 0}</td>
            <td data-label="Checks not taken">{r.not_taken}</td>
            <td data-label="Found something">{r.found}</td>
          </tr>)}</tbody>
        </Table>
      </Card>}
      </>}
    </section>
  );
}

const EARNS: Record<DetectorEarning["verdict"], { text: string; tone?: "ok" | "warn" | "muted" }> = {
  earning: { text: "Earning its keep", tone: "ok" },
  mixed: { text: "Mixed" },
  needs_tuning: { text: "Needs tuning", tone: "warn" },
  too_few: { text: "Too few close-outs", tone: "muted" },
};
const ORIGIN: Record<FindingOrigin, string> = {
  deterministic: "Rule", agentic: "Agent", external: "Another system", unknown: "Not recorded",
};
const pct = (v: number | null) => (v === null ? "–" : `${Math.round(v * 100)}%`);
const usd = (v: number | null) => (v === null ? "–" : v.toLocaleString(undefined, { maximumFractionDigits: 0 }));

/** Per detector (P7.4a, #955): what it raised, how that closed out, how fast it became a case, the value it confirmed
 * and how the confirmed cases were found; each one opens its cases, or the detector's tuning on Settings. */
function Detectors() {
  const data = useAsync(() => api.detectorPerformance(), []);
  const [open, setOpen] = useState<{ label: string; ids: number[] } | null>(null);
  const d = data.data;
  return <>
    <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
    {d && (d.detectors.length ? <>
      <p className="small">Confirmed cases found by {(Object.keys(ORIGIN) as FindingOrigin[])
        .filter((o) => d.by_origin[o].cases).map((o) => `${ORIGIN[o].toLowerCase()} ${pct(d.by_origin[o].share)}`)
        .join(", ") || "none yet"} ({d.confirmed} confirmed)</p>
      <Table density="compact" className="perf-table phone-rows">
        <thead><tr>
          <th>Detector</th><th>Verdict</th><th>Cases</th><th>Confirmed</th><th>False alarm</th><th>Hours to case</th>
          <th>Value confirmed (USD)</th><th>Found by</th><th />
        </tr></thead>
        <tbody>{d.detectors.map((r) => <tr key={r.detector}>
          <td><strong>{r.title}</strong><div className="small muted">{r.kind_label}</div></td>
          <td data-label="Verdict"><Badge tone={EARNS[r.verdict].tone}>{EARNS[r.verdict].text}</Badge></td>
          <td data-label="Cases"><Button variant="link" className="perf-n"
            onClick={() => setOpen({ label: `${r.title}: cases`, ids: r.item_ids })}>{r.cases}</Button>
            <span className="small muted"> {r.closed} closed out</span></td>
          <td data-label="Confirmed">{pct(r.confirmed_rate)}</td>
          <td data-label="False alarm">{pct(r.false_alarm_rate)}
            {r.duplicate ? <span className="small muted"> +{r.duplicate} duplicate</span> : null}</td>
          <td data-label="Hours to case">{r.hours_to_case ?? "–"}</td>
          <td data-label="Value confirmed (USD)">{usd(r.value_confirmed_usd)}
            {r.real > r.valued && r.valued ? <span className="small muted"> on {r.valued} of {r.real}</span> : null}</td>
          <td data-label="Found by" className="small">{(Object.keys(ORIGIN) as FindingOrigin[])
            .filter((o) => r.by_origin[o]).map((o) => `${ORIGIN[o]} ${r.by_origin[o]}`).join(" · ") || "–"}</td>
          <td><a href={href("/settings", { view: "detection", detector: r.detector })}>Tune</a></td>
        </tr>)}</tbody>
      </Table>
    </> : <Empty>No case has been put down to a detector yet.</Empty>)}
    {open && <Card className="perf-cases" title={open.label}
      actions={<Button variant="link" onClick={() => setOpen(null)}>Close</Button>}>
      <ul className="plain small">{open.ids.map((id) => <li key={id}><a href={href(`/cases/${id}`)}>case {id}</a></li>)}</ul>
    </Card>}
  </>;
}

