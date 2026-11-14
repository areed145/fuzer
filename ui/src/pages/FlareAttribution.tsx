/** What explains the flare? (#351), on Incidents: the daily flare against its own baseline over the last year, each
 * episode above it with the downtime, log entries and detections around it or its cause unknown, and how much of the
 * flare over the baseline those records explain. Where the flare is metered, what is piped into it (the relief,
 * blowdown and control valves, by the installation they relieve) and the flare production accounting allocated back
 * to each source. Evidence for the flaring report: not an emissions inventory or a
 * compliance finding, and no permit limit is loaded. */
import { api, type FlareReview } from "../api";
import { Badge, Card, Empty, Table } from "../ui";
import { Status, entityHref, useAsync } from "../lib";

const KIND: Record<FlareReview["episodes"][number]["causes"][number]["kind"], string> = {
  downtime: "Downtime", log_entry: "Log", detection: "Detection",
};

export function FlareAttribution() {
  const data = useAsync(() => api.incidentFlare(), []);
  const d = data.data;
  if (d && !d.days?.length) return null;  // no daily flare volume (or a list that isn't this shape)
  const s = d?.summary;
  return (
    <Card className="flare-attribution" title="What explains the flare?">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && s && <>
        <div className="small flare-summary">
          {d.since} to {d.until}: {s.flare_ksm3.toLocaleString()} kSm3 flared, {s.episodes} episodes above the baseline
          {" "}({s.attributed} with records around them, {s.unknown} cause unknown)
          {s.explained_share_pct != null && <>; the records explain {s.explained_share_pct}% of the
            {" "}{s.over_baseline_ksm3.toLocaleString()} kSm3 over the baseline</>}.
        </div>
        {d.episodes.length ? (
          <ul className="plain small flare-episodes">{d.episodes.map((e) => (
            <li key={`${e.label}|${e.start}`} data-start={e.start.slice(0, 10)}>
              <b>{e.start.slice(0, 10)}</b> about {e.volume.toLocaleString()} {e.volume_unit ?? ""} over the baseline
              {" "}{e.attributed
                ? <Badge tone="muted" className="flare-cause">{e.causes.length} records</Badge>
                : <Badge tone="warn" className="flare-cause">cause unknown</Badge>}
              {e.causes.slice(0, 3).map((c) => (
                <div key={c.id} className="muted">{KIND[c.kind]} {c.at.replace("T", " ")}:{" "}
                  {c.kind === "detection" ? c.text : <a href={entityHref(c.id)}>{c.text}</a>}</div>
              ))}
            </li>
          ))}</ul>
        ) : <Empty>No day flared above its baseline in the window.</Empty>}
        {d.systems?.map((f) => (
          <div key={f.subject} className="small flare-system">
            <a href={entityHref(f.subject)}>{f.label}</a> meters the flare; {f.devices} relief, blowdown and control
            {" "}valves are piped into it: {f.sources.map((g) => `${g.devices.length} on ${g.area_name ?? "no area"}`).join(", ")}.
          </div>
        ))}
        {d.allocation && d.allocation.sources.length > 0 && (
          <Table density="compact" className="flare-allocation phone-rows">
            <thead><tr><th>Allocated to</th><th>kSm3</th><th>Share</th><th>Non-routine</th><th>Metered</th></tr></thead>
            <tbody>{d.allocation.sources.map((g) => (
              <tr key={g.source}>
                <td><a href={entityHref(g.source)}>{g.name}</a></td>
                <td>{g.ksm3.toLocaleString()}</td>
                <td>{g.share_pct != null ? `${g.share_pct}%` : "-"}</td>
                <td>{Object.entries(g.by_kind).filter(([k]) => k !== "routine")
                  .reduce((t, [, v]) => t + v, 0).toLocaleString()}</td>
                <td>{(g.by_basis.metered ?? 0).toLocaleString()}</td>
              </tr>
            ))}</tbody>
          </Table>
        )}
        <div className="small muted">{d.note}</div>
      </>}
    </Card>
  );
}
