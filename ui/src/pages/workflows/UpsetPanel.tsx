import { useState } from "react";
import { ActionStatus, Badge, Card as UICard, ConfirmAction, Table, Disclosure } from "../../ui";
import { api, type RelatedUpset, type UpsetView } from "../../api";
import { Status, useAsync } from "../../lib";
import { PlotlyChart } from "../../analytics/PlotlyChart";
import { when } from "../../work";
import { caseHref, useAct } from "./shared";

const ROLE: Record<RelatedUpset["role"], { label: string; tone: "ok" | "warn" | "danger" | "muted" }> = {
  likely_cause: { label: "Likely cause", tone: "danger" },
  likely_knock_on: { label: "Likely knock-on", tone: "warn" },
  same_time: { label: "Same time, connected", tone: "warn" },
  seen_together: { label: "Seen together before", tone: "muted" },
  coincident: { label: "Coincident", tone: "muted" },
};

const lag = (m: number) => m === 0 ? "same time" : `${Math.abs(m) >= 120 ? `${(Math.abs(m) / 60).toFixed(1)} h` : `${Math.abs(m)} min`} ${m < 0 ? "earlier" : "later"}`;

/** An upset case's triage evidence: how it spread across the plant, its detections in time order, and the other
 * upsets around it (a cascade the SRE split into several cases), with a merge so the IOC triages and dispatches once. */
export function UpsetPanel({ upsetId, itemId, person, canMerge, onMerged }: {
  upsetId: number; itemId: number; person: string; canMerge: boolean; onMerged: () => void;
}) {
  const view = useAsync(() => api.upsetView(upsetId), [upsetId]);
  const v = view.data;
  return (
    <UICard className="upset-panel" title="How it spread">
      <Status loading={view.loading && !v} error={view.error} onRetry={view.reload} />
      {v && <Spread v={v} />}
      {v && <Detections v={v} />}
      {v && <Related v={v} upsetId={upsetId} itemId={itemId} person={person} canMerge={canMerge}
        onMerged={() => { view.reload(); onMerged(); }} />}
    </UICard>
  );
}

function Spread({ v }: { v: UpsetView }) {
  const s = v.spread;
  if (!s.spec || !s.summary) return <p className="small muted">{s.note ?? "It reached no unit in the plant model."}</p>;
  const order = s.summary.order;
  return <>
    <p className="small">Started with <strong>{v.signature}</strong> at {when(v.upset.start)} and reached {s.summary.units}{" "}
      {s.summary.units === 1 ? "unit" : "units"}{order.length > 1 && <>: {order.slice(1, 6).map((o) => `${o.unit} (+${o.minutes_after_first} min)`).join(", ")}
      {order.length > 6 && `, and ${order.length - 6} more`}</>}.</p>
    {order.length > 1 && <PlotlyChart spec={s.spec} label={`${s.title}. First cause ${s.summary.first_cause}.`} />}
  </>;
}

function Detections({ v }: { v: UpsetView }) {
  const shown = v.timeline.slice(0, 25);
  return (
    <Disclosure className="evidence-item" open={v.timeline.length <= 8} label={<><strong>Detections in time order</strong> <span className="muted small">· {v.upset.detections} in all,
        the first is the likely first cause</span></>}>
      <Table density="compact">
        <thead><tr><th>Started</th><th>Unit</th><th>Point</th><th>Finding</th></tr></thead>
        <tbody>{shown.map((d, i) => (
          <tr key={d.id}><td>{when(d.start)}</td><td>{d.unit ?? "—"}</td><td>{d.point ?? "—"}</td>
            <td>{d.finding}{i === 0 && <> <Badge tone="danger">first</Badge></>}{d.end === null && <> <Badge tone="warn">still on</Badge></>}</td></tr>
        ))}</tbody>
      </Table>
      {v.timeline.length > shown.length && <p className="small muted">The first {shown.length} of {v.detections_shown} shown.</p>}
    </Disclosure>
  );
}

function Related({ v, upsetId, itemId, person, canMerge, onMerged }: {
  v: UpsetView; upsetId: number; itemId: number; person: string; canMerge: boolean; onMerged: () => void;
}) {
  const a = useAct();
  const [done, setDone] = useState<{ text: string; moved?: number } | null>(null);
  const rows = v.related.related.filter((r) => r.role !== "coincident" || r.work_item_id);
  return (
    <section>
      <h3>Around the same time</h3>
      <p className="small muted">Other upsets within {v.related.window_hours} h, most likely related first. {v.related.basis}</p>
      {v.merged.length > 0 && <p className="small">Merged into this case: {v.merged.map((m) => m.signature).join("; ")}.</p>}
      {rows.length === 0 && <p className="small muted">Nothing connected in the plant model or seen with it before.</p>}
      <ul className="related-upsets">
        {rows.map((r) => {
          const role = ROLE[r.role];
          const other = r.work_item_id && !r.same_case && r.case_state !== "done" && r.case_state !== "cancelled";
          // A likely cause hosts the event: this case moves under it. Anything else comes into this case.
          const up = r.role === "likely_cause";
          return (
            <li key={r.id} className="evidence-item">
              <div><Badge tone={role.tone}>{role.label}</Badge> <strong>{r.signature}</strong>{" "}
                <span className="muted small">· {lag(r.lag_minutes)} · {r.hops === null ? "not linked in the plant model" :
                  r.hops === 0 ? "shares a unit" : `${r.hops} ${r.hops === 1 ? "link" : "links"} away`}
                  {r.seen_together > 0 && ` · seen together ${r.seen_together}× in 90 days`}</span></div>
              <div className="small">
                {r.same_case ? "Already on this case." : r.work_item_id
                  ? <>Case <a href={caseHref(r.work_item_id)}>{r.case_title ?? `#${r.work_item_id}`}</a>{r.case_state && r.case_state !== "open" && ` (${r.case_state})`}</>
                  : <span className="muted">No case: too small to hand to the IOC on its own.</span>}
              </div>
              {canMerge && other && (
                <ConfirmAction busy={!!a.busy} label={up ? "Move this case under it" : "Bring into this case"}
                  consequence={up
                    ? `This case (#${itemId}) joins case #${r.work_item_id} and is cancelled, with the reason on both histories. Triage and dispatch it once, from ${r.signature}. Split it back out on Exceptions if it was a different event.`
                    : `Case #${r.work_item_id} joins this one and is cancelled, with the reason on both histories. Triage and dispatch it once from here. Split it back out on Exceptions if it was a different event.`}
                  onConfirm={() => a.run("merge", () => up
                    ? api.mergeUpset(upsetId, r.id, person, `${ROLE[r.role].label.toLowerCase()}: ${r.signature}, ${lag(r.lag_minutes)}`)
                    : api.mergeUpset(r.id, upsetId, person, `${ROLE[r.role].label.toLowerCase()} of ${v.signature}, ${lag(r.lag_minutes)}`),
                  (res) => { setDone(up ? { text: `Merged: this case now runs as case #${res.item_id}.`, moved: res.item_id }
                    : { text: `Merged: case #${res.cancelled_case} is now part of this one.` }); onMerged(); })} />
              )}
            </li>
          );
        })}
      </ul>
      <ActionStatus busy={a.busy} error={a.error}>
        {done && <p className="notice small">{done.text} {done.moved && <a href={caseHref(done.moved)}>Open that case</a>}</p>}
      </ActionStatus>
    </section>
  );
}
