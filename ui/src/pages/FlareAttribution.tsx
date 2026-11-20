/** What explains the flare? (#351), on Incidents: the daily flare against its own baseline over the last year, each
 * episode above it with the downtime, log entries and detections around it or its cause unknown, and how much of the
 * flare over the baseline those records explain. Where the flare is metered, what is piped into it (the relief,
 * blowdown and control valves, by the installation they relieve) and the flare production accounting allocated back
 * to each source. Evidence for the flaring report: not an emissions inventory or a
 * compliance finding, and no permit limit is loaded. */
import { api, type FlareReview } from "../api";
import { whenDay, count } from "../format";
import { Badge, Empty, Table, Text, List } from "../ui";
import { entityHref, ReadCard, useAsync } from "../lib";
import { kindWord } from "../wording";

/** A cause's kind as words: an entity kind as the ontology labels it; a detection is not an entity. */
const causeKind = (kind: FlareReview["episodes"][number]["causes"][number]["kind"]) =>
  kind === "detection" ? "Detection" : kindWord(kind);

export function FlareAttribution() {
  const data = useAsync(() => api.incidentFlare(), []);
  const d = data.data;
  if (d && !d.days?.length) return null;
  const s = d?.summary;
  return (
    <ReadCard of={data} className="flare-attribution" title="What explains the flare?" note={d?.note}>
      {d && s && <>
        <Text as="div" small>
          {d.since} to {d.until}: {count(s.flare_mscf)} Mscf flared, {s.episodes} episodes above the baseline
          {" "}({s.attributed} with records around them, {s.unknown} cause unknown)
          {s.explained_share_pct != null && <>; the records explain {s.explained_share_pct}% of the
            {" "}{count(s.over_baseline_mscf)} Mscf over the baseline</>}.
        </Text>
        {d.episodes.length ? (
          <List plain small>{d.episodes.map((e) => (
            <li key={`${e.label}|${e.start}`} data-start={e.start.slice(0, 10)}>
              <b>{whenDay(e.start)}</b> about {count(e.volume)} {e.volume_unit ?? ""} over the baseline
              {" "}{e.attributed
                ? <Badge tone="muted">{e.causes.length} records</Badge>
                : <Badge tone="warn">cause unknown</Badge>}
              {e.causes.slice(0, 3).map((c) => (
                <Text as="div" key={c.id} muted>{causeKind(c.kind)} {c.at.replace("T", " ")}:{" "}
                  {c.kind === "detection" ? c.text : <a href={entityHref(c.id)}>{c.text}</a>}</Text>
              ))}
            </li>
          ))}</List>
        ) : <Empty>No day flared above its baseline in the window.</Empty>}
        {d.systems?.map((f) => (
          <Text as="div" key={f.subject} small>
            <a href={entityHref(f.subject)}>{f.label}</a> meters the flare; {f.devices} relief, blowdown and control
            {" "}valves are piped into it: {f.sources.map((g) => `${g.devices.length} on ${g.area_name ?? "no area"}`).join(", ")}.
          </Text>
        ))}
        {d.allocation && d.allocation.sources.length > 0 && (
          <Table density="compact">
            <thead><tr><th>Allocated to</th><th>Mscf</th><th>Share</th><th>Non-routine</th><th>Metered</th></tr></thead>
            <tbody>{d.allocation.sources.map((g) => (
              <tr key={g.source}>
                <td><a href={entityHref(g.source)}>{g.name}</a></td>
                <td>{count(g.mscf)}</td>
                <td>{g.share_pct != null ? `${g.share_pct}%` : "-"}</td>
                <td>{count(Object.entries(g.by_kind).filter(([k]) => k !== "routine")
                  .reduce((t, [, v]) => t + v, 0))}</td>
                <td>{count(g.by_basis.metered ?? 0)}</td>
              </tr>
            ))}</tbody>
          </Table>
        )}
      </>}
    </ReadCard>
  );
}
