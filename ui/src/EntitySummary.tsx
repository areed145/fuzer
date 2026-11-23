/** The summary at the top of an entity's overview: what matters about it and everything beneath it, written by the
 * summary specialist from the rolled-up facts and kept for everyone for a day. Someone with AI access who opens the
 * page when there is none, or it is a day old, has a new one written (one short call on a small model); everyone
 * else reads the kept one. */
import { useEffect, useRef, useState } from "react";
import { api, type EntitySummary as Summary } from "./api";
import { ago } from "./format";
import { usePermission } from "./Auth";
import { Card, ErrorText, List, Text } from "./ui";
import { entityHref, errorMessage, href, useAsync } from "./lib";
import { useEntityNames } from "./useEntityNames";
import { display, type EntityNames } from "./entityRefs";

function Cite({ c, names }: { c: string; names?: EntityNames }) {
  const n = /^case:(\d+)$/.exec(c);
  if (n) return <a href={href(`/cases/${n[1]}`)}>case {n[1]}</a>;
  if (c.startsWith("fz-")) return <a href={entityHref(c)}>{display(c, names)}</a>;
  return null;
}

export function EntitySummary({ id }: { id: string }) {
  const ai = usePermission("ai");
  const kept = useAsync(() => api.entitySummary(id).then((k) => (k && "can_write" in k ? k : null), () => null), [id]);
  const [written, setWritten] = useState<Summary | null>(null);
  const [writing, setWriting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const asked = useRef<string | null>(null);
  const summary = written ?? kept.data?.summary ?? null;
  const names = useEntityNames([...new Set((summary?.points ?? []).flatMap((p) => p.cites).filter((c) => c.startsWith("fz-")))]);
  const due = !!kept.data && (!kept.data.summary || kept.data.summary.stale);
  useEffect(() => {
    // Once per entity per visit, never retried: a failed write says so and leaves the kept summary in place.
    if (!ai || !due || !kept.data?.can_write || asked.current === id) return;
    asked.current = id;
    setWriting(true);
    api.writeEntitySummary(id).then((r) => setWritten(r.summary), (e) => setError(errorMessage(e))).finally(() => setWriting(false));
  }, [ai, due, id, kept.data?.can_write]);
  if (!summary && !writing && !error) return null;
  return <Card as="section" className="entity-summary" title="Summary"
    detail={summary ? <>written by Claude {ago(summary.written_at)} from the open work, downtime and plan beneath it · shared</>
      : undefined}>
    {writing && !summary && <Text small muted>Writing the summary…</Text>}
    {summary && <>
      <Text as="p"><strong>{summary.headline}</strong></Text>
      {summary.points.length > 0 && <List>{summary.points.map((p, i) => <li key={i}>{p.text}
        {p.cites.length > 0 && <Text small muted> {p.cites.map((c, j) => <span key={c}>{j > 0 ? " · " : ""}<Cite c={c} names={names} /></span>)}</Text>}
      </li>)}</List>}
      {writing && <Text small muted>Writing a newer one…</Text>}
    </>}
    <ErrorText>{error}</ErrorText>
  </Card>;
}
