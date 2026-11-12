import { api } from "../../api";
import { entityHref, useAsync } from "../../lib";
import { Notice } from "../../ui";
import { when } from "../../work";

/** Safeguards out on this case's system (P3.9b): the bypasses in place on what the case is about, or the rest of its
 * train, and the open change records on it. It leads the evidence, so nobody reads a trend or acts on a trip without
 * knowing a safeguard is out. Nothing shows when there are none. */
export function CaseSafeguards({ id }: { id: number }) {
  const got = useAsync(() => api.workItemSafeguards(id), [id]);
  const d = { bypasses: got.data?.bypasses ?? [], changes: got.data?.changes ?? [] };  // an older server sends neither
  if (!d.bypasses.length && !d.changes.length) return null;
  const late = d.bypasses.some((b) => b.overdue);
  return (
    <Notice tone={d.bypasses.length ? (late ? "danger" : "warn") : "info"} className="case-safeguards"
      role="region" aria-label="Safeguards and changes">
      {d.bypasses.length > 0 && <>
        <strong>{d.bypasses.length === 1 ? "A safeguard is bypassed" : `${d.bypasses.length} safeguards are bypassed`} on this system</strong>
        <ul className="plain">{d.bypasses.map((b) => (
          <li key={b.id}>
            <a href={entityHref(b.id)}>{b.label}</a>: {b.function ?? b.safeguard}
            {b.protects.length > 0 && <> on {b.protects.map((p, i) => <span key={p}>{i ? ", " : ""}<a href={entityHref(p)}>{b.protects_labels[i]}</a></span>)}</>}
            {b.expires && <>, {b.overdue ? <strong>overdue since {when(b.expires)}</strong> : <>until {when(b.expires)}</>}</>}
            {b.moc ? <> under {b.moc}</> : <>, no change record</>}
            {b.reason && <div className="small">{b.reason}{b.authorised_by ? ` (authorised by ${b.authorised_by})` : ""}</div>}
          </li>))}
        </ul>
      </>}
      {d.changes.length > 0 && <>
        <strong>{d.changes.length === 1 ? "An open change" : `${d.changes.length} open changes`} on this system</strong>
        <ul className="plain">{d.changes.map((c) => (
          <li key={c.id}>
            <a href={entityHref(c.id)}>{c.label}</a>{c.change_type ? ` (${c.change_type}, ${c.state ?? "open"})` : ""}: {c.title ?? "Change"}
            {c.closes && <span className="small"> · closes {when(c.closes)}</span>}
          </li>))}
        </ul>
      </>}
    </Notice>
  );
}
