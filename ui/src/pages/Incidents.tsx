import "../styles/events.css";
import { percent, when, whenDay, count } from "../format";
import { Button, Disclosure, PageHeader, Card as UICard, Badge as UIBadge, Empty, Textarea, ErrorText, Line, Text, List, Heading } from "../ui";
import { useState } from "react";
import { api, type Brief, type Incident } from "../api";
import { KindBadge, Status, entityHref, href, useAsync } from "../lib";
import { TrendView } from "../TrendView";
import { loadPerson } from "../work";
import { IncidentRepeats, useRepeats } from "./IncidentRepeats";
import { FlareAttribution } from "./FlareAttribution";
import { plural, say } from "../wording";

/** The latest incidents are on the page; earlier ones are one click away. */
const LATEST = 8;

export function IncidentsPage() {
  const list = useAsync(() => api.incidents(), []);
  const repeats = useRepeats();
  // The repeats lead the page and the list follows: each drawn as it answered, the later pushed the other down (#1069).
  const ready = (list.data || list.error) && (repeats.data !== undefined || repeats.error);
  return (
    <section>
      <PageHeader>Incidents</PageHeader>
      {/* Repeats lead: whether problems come back is what a manager reads this page for (#1078). */}
      {ready && <IncidentRepeats data={repeats} />}
      <Status loading={!ready} error={list.error} empty={list.data?.total === 0}>
        No incidents yet.
      </Status>
      {ready && list.data && list.data.total > 0 && (
        <UICard as="section" title="Latest"
          detail={<>{count(list.data.total)} incidents{list.data.total > list.data.items.length && `, showing the latest ${list.data.items.length}`}</>}>
          <IncidentList items={list.data.items.slice(0, LATEST)} all={list.data.items} />
          {list.data.items.length > LATEST && <Disclosure className="more-incidents" label={`Show ${list.data.items.length - LATEST} earlier incidents`}>
            <IncidentList items={list.data.items.slice(LATEST)} all={list.data.items} />
          </Disclosure>}
        </UICard>
      )}
      {/* Under the latest incidents once they are in place: shown sooner, the list arriving above pushed them down (#1069). */}
      {ready && <>
        <FlareAttribution />
        <RatingProgress />
      </>}
    </section>
  );
}

export function IncidentList({ items, all = items }: { items: Incident[]; all?: Incident[] }) {
  // The same event grouped on more than one unit (#402): same start, same description.
  const twins = (i: Incident) => all.filter((o) => o.id !== i.id && o.start === i.start && o.description === i.description);
  return (
    <ul className="timeline">
      {items.map((i) => (
        <li key={i.id}>
          <span className="when">{i.start}</span>
          <div className="what">
            <a href={entityHref(i.id)}>{say(i.description)}</a>
            <Line small muted>
              {i.unit && <span>on {i.unit}</span>}
              {twins(i).length > 0 && <span>also on {twins(i).map((o, n) => <span key={o.id}>{n > 0 && ", "}
                <a href={entityHref(o.id)}>{o.unit || "another unit"}</a></span>)}</span>}
              {i.downtime_records > 0 && <UIBadge tone="danger">downtime</UIBadge>}
              {i.events > 0 && <span>{plural(i.events, "event")}</span>}
              {i.notifications > 0 && <span>{plural(i.notifications, "notification")}</span>}
              {i.lost_oil_bbl > 0 && <span>{count(Math.round(i.lost_oil_bbl))} bbl lost</span>}
              {i.failure_modes?.map((m) => <UIBadge key={m} title={`ISO 14224 ${m}`}>
                {i.failure_mode_words?.[m] ?? m}</UIBadge>)}
            </Line>
          </div>
        </li>
      ))}
    </ul>
  );
}

// Who is rating: the name the Review page remembers.
const rater = () => loadPerson() || "anonymous";

/** How far engineers are through rating briefs on past incidents, and the next ones to rate. */
function RatingProgress() {
  const r = useAsync(() => api.briefRatings(), []);
  if (!r.data || r.data.incidents === 0) return null;
  const next = r.data.items.filter((i) => i.ratings === 0).slice(0, 5);
  return (
    <UICard as="section" title="Rate the troubleshooting briefs"
      detail={<>{r.data.rated} of {r.data.incidents} incidents rated {r.data.useful_share !== null && ` · ${percent(r.data.useful_share)} of ratings useful`}</>}>
      {next.length > 0 && (
        <List plain>
          {next.map((i) => (
            <li key={i.id} className="small">
              <Text muted mono>{i.start}</Text> <a href={entityHref(i.id)}>{i.description}</a>
            </li>
          ))}
        </List>
      )}
    </UICard>
  );
}

export function IncidentDetail({ id }: { id: string }) {
  return (
    <>
      <BriefCard id={id} />
      <IncidentPanels id={id} />
      {/* The unit's trend around the incident, the 12 h leading into it shaded on the chart. */}
      <TrendView id={id} />
    </>
  );
}

function IncidentPanels({ id }: { id: string }) {
  const timeline = useAsync(() => api.incidentTimeline(id), [id]);
  const similar = useAsync(() => api.similarIncidents(id), [id]);
  const reading = useAsync(() => api.incidentReading(id), [id]);
  return (
    <div className="grid">
      <UICard as="section" title="Timeline">
        <Status of={timeline} />
        <ul className="timeline">
          {timeline.data?.map((t) => (
            <li key={t.id}>
              <span className="when">{when(t.at)}</span>
              <div className="what">
                <Line>
                  <KindBadge kind={t.kind} />
                  {t.detail && <UIBadge tone="danger">{t.detail.replace("_", " ")}</UIBadge>}
                </Line>
                <a href={entityHref(t.id)}>{t.text}</a>
              </div>
            </li>
          ))}
        </ul>
      </UICard>
      <UICard as="section">
        {reading.data && reading.data.length > 0 && (
          <>
            <Heading>Suggested reading</Heading>
            <List plain>
              {reading.data.map((r) => (
                <li key={r.id} className="small">
                  <a href={entityHref(r.id)}>{r.document_label} · {r.heading}</a>
                  {r.page && <Text muted> p. {r.page}</Text>}{" "}
                  {r.failure_modes.map((m) => <UIBadge key={m}>{m}</UIBadge>)}
                </li>
              ))}
            </List>
          </>
        )}
        <Heading>Similar past incidents</Heading>
        <Status loading={similar.loading && !similar.data} empty={similar.data?.length === 0}>
          None found.
        </Status>
        <List plain>
          {similar.data?.map((s) => (
            <li key={s.id} className="small">
              <Text muted mono>{s.start}</Text> <a href={entityHref(s.id)}>{s.description}</a>
              {s.same_unit && <UIBadge>same unit</UIBadge>}
            </li>
          ))}
        </List>
      </UICard>
    </div>
  );
}

/** The troubleshooting brief's own suggestions (checks, what moved, lessons, spares) and a rating. */
function BriefCard({ id }: { id: string }) {
  const brief = useAsync(() => api.brief(id), [id]);
  const b = brief.data;
  return (
    <UICard as="section" title="Troubleshooting brief">
      <Status of={brief} />
      {b && (
        <div className="grid">
          <div>
            <Heading level={3}>Suggested checks</Heading>
            {b.suggested_checks.length === 0 ? (
              <Empty className="small">No approved lessons or reading for this unit yet.</Empty>
            ) : (
              <List as="ol" small>
                {b.suggested_checks.map((c, n) => (
                  <li key={n}>
                    {c.step}{" "}
                    <Text muted>
                      ({c.from.startsWith("fz-") ? <a href={entityHref(c.from)}>source</a> : c.from})
                    </Text>
                  </li>
                ))}
              </List>
            )}
            <Moved pv={b.process_values} />
          </div>
          <div>
            {b.measurements.length > 0 && (
              <>
                <Heading level={3}>Measurements</Heading>
                <List plain small>
                  {b.measurements.slice(0, 6).map((m) => (
                    <li key={`${m.quantity}|${m.location}|${m.of.id}`}>
                      {m.quantity.replaceAll("_", " ")}{m.location && <Text muted> {m.location}</Text>}:{" "}
                      <Text mono>{m.latest.qualifier ?? ""}{m.latest.value} {m.unit}</Text>{" "}
                      <Text muted>
                        {whenDay(m.latest.at)}
                        {m.per_year !== null && ` · ${m.per_year > 0 ? "+" : ""}${m.per_year}/year`}
                      </Text>
                    </li>
                  ))}
                </List>
              </>
            )}
            {b.lessons.approved.length > 0 && (
              <>
                <Heading level={3}>Lessons</Heading>
                <List plain small>
                  {b.lessons.approved.map((l) => <li key={l.id}><a href={href("/lessons")}>{l.title}</a></li>)}
                </List>
              </>
            )}
            {b.spares.length > 0 && (
              <>
                <Heading level={3}>Spares</Heading>
                <List plain small>
                  {b.spares.slice(0, 6).map((p) => (
                    <li key={p.fuzer_id}>
                      <a href={entityHref(p.fuzer_id)}>{p.description || p.label}</a>{" "}
                      <span className={p.stock > 0 ? "muted" : "error"}>{p.stock > 0 ? `${p.stock} in ${p.bin}` : "none in stock"}</span>
                    </li>
                  ))}
                </List>
              </>
            )}
            <Rate target={`brief:${id}`} />
          </div>
        </div>
      )}
    </UICard>
  );
}

/** Points that moved most in the lead-in, against the hours before it. */
function Moved({ pv }: { pv: Brief["process_values"] }) {
  const moved = pv.series
    .filter((s) => s.lead_in?.change_pct != null && Math.abs(s.lead_in.change_pct) >= 10)
    .sort((a, b) => Math.abs(b.lead_in!.change_pct!) - Math.abs(a.lead_in!.change_pct!));
  return (
    <>
      <Heading level={3} detail={`12 h before ${pv.centre.replace("T", " ")} vs. before that`}>What moved</Heading>
      {moved.length === 0 ? (
        <Empty className="small">No point moved more than 10%.{pv.errors && ` ${pv.errors.length} points couldn't be read.`}</Empty>
      ) : (
        <List plain small>
          {moved.map((s) => (
            <li key={s.id}>
              <a href={entityHref(s.id)}>{s.description || s.point}</a>{" "}
              <Text mono>
                {s.lead_in!.change_pct! > 0 ? "+" : ""}{s.lead_in!.change_pct}%
              </Text>{" "}
              <Text muted>
                ({s.lead_in!.baseline_mean} → {s.lead_in!.extreme} {s.uom})
              </Text>
            </li>
          ))}
        </List>
      )}
      {pv.note && <Text as="p" small>{pv.note}</Text>}
    </>
  );
}

function Rate({ target }: { target: string }) {
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const send = (useful: boolean) =>
    api.feedback(target, useful, rater(), comment || undefined).then(() => setSent(useful), (e) => setError(String(e)));
  return (
    <div className="rate">
      <Heading level={3}>Would this brief have helped?</Heading>
      {sent !== null ? (
        <Text as="p" small>Thanks: rated {sent ? "useful" : "not useful"}.</Text>
      ) : (
        <>
          <Textarea className="lesson-text" rows={2} placeholder="What was missing or wrong? (optional)"
                    value={comment} onChange={(e) => setComment(e.target.value)} />
          <Line>
            <Button variant="primary" onClick={() => send(true)}>Useful</Button>
            <Button onClick={() => send(false)}>Not useful</Button>
          </Line>
        </>
      )}
      <ErrorText>{error}</ErrorText>
    </div>
  );
}
